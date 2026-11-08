# Security Advisor — review

**TL;DR.** KERP's security *design instincts* are unusually good for a spec at this stage — append-only hash-chained ledgers, `REVOKE UPDATE/DELETE`, two-layer tenant isolation, eligibility snapshots on sign-off, and an "AI proposes, human commits" rule are all already written into the spec. The gaps are not in ambition but in **cryptographic completeness and evidentiary rigour**: the hash chains and the `signing` abstraction, as specced, are **tamper-evident but not court-defensible** — they lack trusted timestamps (RFC 3161), key management, and WORM anchoring. The single highest-value decision is to **upgrade `signing` to produce PAdES-B-LTA signatures with RFC 3161 timestamps and periodically anchor the ledger head**, because the entire commercial value of KERP ("the client held us up, not the other way round" — `inspection_requests`) rests on records a tribunal will actually accept. Second: **close the identity gaps** — the `field_diary` magic-link/PIN flow and the portal magic-link are the softest entry points and are under-specified. Third: **keep `tenant_id` + RLS (TD-007)** but write down the four bypass modes (ARQ worker, cross-module service calls, raw SQL, superuser role) as hard test gates, because the as-built Inspection-ERP already proves the pattern and the retrofit cost is brutal. Resolve TD-003 toward **local JWT behind the `AuthProvider` seam now, Entra ID SSO for staff later** — but fix the symmetric-JWT and session-revocation weaknesses regardless of which identity source wins.

This review is read-only; it proposes, it does not change the tracker or code.

---

## 1. Threat model (KERP-specific)

### 1.1 Assets worth protecting (in priority order)
1. **Evidentiary records** — the hash-chained sign-off and event ledgers (`inspections._signoff`, `inspection_requests._event`, `qms._audit_log`), signed MDR/pack PDFs, delay-attribution records. These are the platform's *reason to exist*: they decide EOT/variation entitlement worth far more than the software. Integrity and non-repudiation matter more than confidentiality here.
2. **Tenant/project data isolation** — asset hierarchy, inspections, welds, claims, documents. Cross-tenant or cross-project leakage is both a commercial and a contractual breach.
3. **Identity & authorisation state** — the three-tier RBAC (`users`), per-user project/site scope (`teams._visibility`), field-worker grants (`field_diary.oe_field_module_grant`), client portal grants (`portal._access_grant`).
4. **Heavy files & the object store** — CAD/BIM, 200-page MDRs, dockets, certs. Presigned-upload boundary is a direct browser→bucket trust edge.
5. **AI action surface** — `erp_chat._action`, `ai_agents`, `compliance_ai`, `voice`, `closeout` AI slot suggestions, `forms` import-assist. Any path that can mutate state is an attack/erroneous-write surface.
6. **Integration credentials** — P6 files, ERP PO-line mirrors, corporate safety KPI sync, outbound webhook secrets (`integrations._webhook.secret`, `notifications._webhook_target.secret`).

### 1.2 Actors / trust boundaries
| Actor | Trust | Entry point | Notes |
|---|---|---|---|
| Staff (13 personas) | Authenticated, DB-rehydrated claims | `users` JWT | Strongest control; the model is sound. |
| **Field worker** | **Low-assurance** | `field_diary` **magic-link + PIN** | Shared/rugged devices, offline, per-module grants. Softest internal surface. |
| **External client** | **Untrusted-but-invited** | `portal` **scoped magic-link** | Can co-sign (v2) and raise/close NCRs (v3) — i.e. *write* paths. |
| Third-party / authority | Untrusted-but-invited | `inspection_requests.external_token` | Slot responses via magic-link. |
| ARQ worker | Trusted service | Redis queue | **Runs outside the request — the main RLS-bypass risk (§4).** |
| Integration feeds (P6/ERP) | Semi-trusted data | `integrations` ingestion | Untrusted file content parsed by heavy libs. |
| AI assistant | **Untrusted output** | `erp_chat`/`ai_agents` tools | Proposes mutations; prompt-injection vector via OCR'd/ingested docs. |

### 1.3 Top threats (STRIDE-ish, tailored)
- **T1 Repudiation / record forgery.** A party disputes a sign-off or delay record; without trusted timestamps and key management the chain proves *internal consistency* but not *when* or *by whom in a legally recognised sense* (§3). **Highest business impact.**
- **T2 Magic-link/PIN compromise.** Link forwarding, PIN brute-force, token replay, non-expiry → unauthorised field capture or client co-sign (§2.2).
- **T3 Cross-tenant / cross-project leakage** via an RLS bypass path — ARQ job runs without tenant context; a cross-module `service.py` call that forgets scope; raw SQL in a heavy query (§4).
- **T4 AI-driven unsafe mutation / prompt injection.** A malicious instruction embedded in an OCR'd docket or ingested P6 note steers `erp_chat` to propose a plausible-looking but wrong change that a tired human confirms (§6).
- **T5 Object-store exposure.** Over-broad presigned URL, bucket CORS misconfig, unscanned malware served to clients, orphaned blobs, external share tokens that never expire (§5).
- **T6 Supply-chain / secrets.** Heavy lazily-imported libs (`fitz`, `trimesh`, `cv2`, P6 parser) are a large untrusted-input attack surface; webhook/integration secrets and object-store keys must never land in the repo (hard rule 10).

---

## 2. Identity & access

### 2.1 Verdict on TD-003 (local JWT vs SSO)
**Adopt the applied default — local JWT behind the `AuthProvider` seam (`users` F5, task T-0007) now; add Entra ID (OIDC) for staff and keep email/password for invited clients later — but treat three things as mandatory regardless of source:**

1. **Drop symmetric HS256.** The as-built Inspection-ERP uses `PyJWT HS256` (`architecture-suite-reference/01` §auth). A single shared secret means any service that can *verify* a token can also *mint* one — bad once the ARQ worker, and later an SSO broker, hold it. Specify **asymmetric RS256/EdDSA** with a published JWKS so verifiers never hold signing keys. This is also a precondition for clean Entra ID federation.
2. **Short-lived access + server-side refresh/revocation.** `users` lists `[F5] session/JWT` with no refresh or revocation model. Self-contained JWTs are not revocable by default (OWASP Proactive Controls C6). Specify access TTL ≤ 15 min, rotating refresh tokens with a server-side session record, and a revocation list keyed to `jti` so a terminated user / rotated grant dies immediately. The spec's strength — **claims rehydrated from the DB per request** (principle 5) — already blunts stale-authz; make the token itself equally revocable.
3. **Keep scrypt** (`hashlib.scrypt`) for local passwords — acceptable; set explicit cost parameters and a pepper from env, and require MFA for `admin`/`manager` tiers.

SSO-for-staff is worth doing in an industrial-construction single-company deployment (the client's IT already runs Entra ID / Windows), but it is **not** worth blocking Phase 0 — the `AuthProvider` seam is exactly right. Record the asymmetric-key and refresh/revocation requirements on **T-0007** so they are not lost when SSO is deferred.

### 2.2 `field_diary` magic-link + PIN — the softest internal surface
`field_diary` (`_magic_link(phone, token/pin hash)`, `oe_field_module_grant`, `oe_field_sync_ledger`) is the only auth path specced in any detail, and it is **thin** for what it guards (field capture that feeds progress, diaries, labour publish). Required hardening, to write into the `field_diary` spec and a new engine rule `field_auth.issue`:
- **PIN is a second factor, never the primary secret.** Magic-link token must be single-use, high-entropy (≥128-bit), short-TTL, and bound to the issued device/phone; the PIN only unlocks an already-authenticated session on a shared device.
- **Brute-force + enumeration defences:** rate-limit and lock PIN attempts (the ledger makes replay detection easy — reuse `oe_field_sync_ledger.client_op_id`); never reveal whether a phone is enrolled.
- **Grant scoping:** `oe_field_module_grant` must be *deny-by-default* and time-boxed; a field session must carry the same `tenant_id`/project scope as a staff session so RLS applies identically (see §4).
- **Offline token lifetime:** offline-first means a device holds a usable credential for a while — specify max offline validity and a server-side "revoke device" that invalidates on next sync.

### 2.3 Portal magic-link scoping (`portal._access_grant`)
The portal is a **write** surface from v2 (co-sign) and v3 (client NCR). `_access_grant(scope, asset_subtree_root_id, expires_at, magic_link_token)` plus `portal.access` ("no cross-tenant leakage → 404") is the right shape. Tighten:
- **Per-resource, least-privilege tokens** with mandatory `expires_at` (no non-expiring links) and single-purpose scope — a co-sign link should not also read the whole register.
- **Bind co-sign identity:** `report.cosign`/`itp.cosign` already require "signer must be the granted client contact" — enforce that the signing identity (§3) is cryptographically the same principal the magic-link was issued to, not just the same row.
- **Revoke on project exit / grant change**, and log every portal action through `audit` (the spec says it does — verify in tests).
- `inspection_requests.external_token` (third-party/authority slot responses) inherits all of the above.

### 2.4 Three-tier RBAC + DB-rehydrated claims
This is the strongest part of the identity design. Capability tier × business-role preset × per-user project/site/module scope, **rehydrated from the DB each request** (principle 5; `users` F2–F4; `teams._visibility` + `verify_project_access`). Keep it. Two cautions: (a) the rehydration query is on every request's hot path — cache per-request only, never across requests; (b) write **contract tests** that a lower tier cannot reach a higher-tier route and that `teams._visibility` actually gates every list endpoint, not just the asset tree.

---

## 3. Legal / evidentiary records — strongest lens, biggest gap

KERP stakes its commercial case on records that survive a dispute. The spec builds **tamper-evidence**; a tribunal needs **tamper-evidence + provenance + trusted time + key custody**. Assessment:

### 3.1 What's already strong (keep, don't weaken)
- **Append-only hash chains** on `inspection_requests._event`, `inspections._signoff`, `inspections._response`, with `prev_hash → content_hash` and **`REVOKE UPDATE/DELETE`** at the DB grant level. This is genuinely good and rare to see specced.
- **Eligibility snapshot + auth_strength captured at sign-off** (`_signoff.auth_strength[password|mfa|pin|pades]`, `eligibility_result jsonb`) — captures *why the signer was entitled at that instant*. Excellent evidentiary instinct.
- **`forms` revision immutability** (`schema_hash` set at publish, UPDATE-blocking trigger, in-flight records pin `template_revision_id`) — a record can't be retro-altered by editing its template.
- **Record-contract hashing** via `BaseRecordMixin` (hard rule 2) on every table.
- **Verifier ≠ resolver** separation (`inspection.signoff`: "verifier ≠ resolver where required") — segregation of duties in the gate.

### 3.2 What's missing for court-defensible records (gaps → recommendations)
| Gap | Why it matters | Fix |
|---|---|---|
| **No trusted timestamps.** All times are app-generated `signed_at`/`occurred_at`/`content_hash`. | A chain proves *order and internal consistency*, not that an entry existed at a wall-clock time. The whole delay-attribution feature ("client no-show at T") is weakened if the counterparty disputes the clock. | Add **RFC 3161 timestamp tokens** from a trusted TSA on each sign-off and on periodic ledger-head checkpoints. ETSI PAdES **B-T** is exactly this for the PDF side. |
| **`signing` is an abstraction with no assurance floor.** `_session(content_hash, provider/capability)`, `_signature(cert_subject/valid_until, content_hash)` — shape is right but no level named. | A hash + a name is weaker than a cryptographic signature a court recognises. | Specify **PAdES-B-LTA** for MDR/pack/notice PDFs that become legal records: embeds the cert chain + CRL/OCSP (B-LT) **and** a document timestamp (B-LTA) so the signature validates years later from the file alone. B-LTA must be **re-stamped before the archival TSA cert expires** — missing the renewal cannot be repaired. (ETSI EN 319 142; PAdES levels B-B/B-T/B-LT/B-LTA.) |
| **No key management named.** Who holds the ledger/signing keys; rotation; HSM/KMS. | If the signing key leaks or can't be produced, every record it backed is contestable. | Add a `docs/decisions/` ADR: signing/timestamp keys in a KMS/HSM (cloud KMS or on-prem softHSM), documented rotation, and a **public-verification bundle** so a third party can verify a KERP record without KERP. |
| **`REVOKE UPDATE/DELETE` ≠ WORM.** A DBA/superuser can still `GRANT` back, `TRUNCATE`, or restore a doctored backup. | "Append-only" is an app-role control, not storage immutability. | For the legal subset, write the signed PDFs + a periodic **ledger-head hash anchor** to **WORM / object-lock storage** (S3 Object Lock / MinIO retention). Anchoring the chain head externally (even a daily hash emailed/committed) makes silent rewrites detectable. |
| **No cross-ledger anchoring.** Three separate chains (`_event`, `_signoff`, `qms._audit_log`) with no common root. | A whole chain could be rebuilt offline and swapped. | Periodically hash each chain head into a single tamper-evident anchor with an RFC 3161 token (reuses the TSA above). |
| **`qms._audit_log` / `admin` audit / `audit`** are referenced but **not fully specced** as a single system of record. | Evidentiary consistency needs one audit contract, not three half-specified ones. | Define one `audit` contract (append-only, hash-chained, RFC3161-anchored) that `qms`, `admin`, `portal`, `erp_chat`, and the ledgers all write through. Raise as a **spec-gap TODO**. |

### 3.3 `as_built_record.valid_for_legal_record`
`construction_control._asbuilt_record(capture_method, instrument_calibration_ref, accuracy, deviation, valid_for_legal_record, signed)` already models legal-record admissibility with calibration provenance — good. Make `valid_for_legal_record=true` **require** the B-LTA signing + eligibility snapshot + calibration-in-date (via `eligibility` instrument check) as an engine rule, so the flag can't be set without the evidence.

---

## 4. Tenant isolation (TD-007)

**Verdict: keep `tenant_id` + RLS, one tenant per deployment.** The as-built Inspection-ERP (`architecture-suite-reference/01`) already runs exactly this and proves it's cheap: a `NOSUPERUSER NOBYPASSRLS` app role (`ie_app`), **prod refuses to start if the role can bypass RLS** (`rls_enforcement_problems`, H-03), tenant bound *after* auth via `set_session_tenant`, re-applied per transaction, and the connection **resets `app.tenant_id` on return to the pool**. Retrofitting multi-tenancy later is a schema-wide rewrite; keeping it is a mixin + a policy generator. The hard rules already mandate it (rule 2/principle 4), so TD-007 should resolve **keep**.

**But RLS is not free of bypass modes — write each as a Phase-0 test gate:**
1. **ARQ worker context (highest risk).** Jobs run outside the request lifecycle, so `set_session_tenant` never fires unless the job explicitly sets it. Every enqueued job must carry `tenant_id` + project scope in its payload and open its DB session *through the same tenant-binding path*. **Test:** an ARQ job with no tenant set must read zero rows, not all rows.
2. **Cross-module `service.py` calls.** Principle 2 forbids cross-module table access, but a service method that runs a scoped query for caller A can leak to caller B if it trusts a passed-in id without re-checking scope. **Test:** service-to-service calls re-assert tenant/project scope; cross-tenant id → 404.
3. **Raw SQL / heavy analytics.** BI rollups, `dashboards` parquet snapshots, materialised views, and P6/CAD parsers that touch the DB directly can sidestep ORM-level guards. **Test:** materialised views and rollup jobs are tenant-scoped; no rollup crosses tenants even though single-company means one tenant today.
4. **Superuser / migration role.** Alembic and backup/restore run privileged. Keep the app role `NOBYPASSRLS`; never serve requests on the migration role; cover with the as-built's start-up refusal check.

Single-company today does **not** make this busywork: `project`/`site` scope reuses the same machinery (`teams._visibility`, per-user project scope), and RLS is the substrate that makes the portal's "cross-tenant → 404" and field grants enforceable rather than advisory.

---

## 5. File security (ADR-001 §3, `documents` VFS, presigned uploads)

ADR-001 already names the right boundary controls; make them non-optional and testable.
- **Presigned boundary.** `POST /uploads/presign → PUT to bucket → POST /uploads/complete`. Presigned URLs must be **least-privilege** (single object key, `PUT` only, short TTL, content-length/-type bound) and the **`sha256` verified server-side on `/complete`** before the file is registered (ADR-001 already requires this — enforce it as `upload.complete` rule).
- **Scan/quarantine before `available`.** The on-complete ARQ task must mark `clean`/`quarantined` and a file must **never** be downloadable (especially by portal clients) while `quarantined` or unscanned. Serve downloads only for `available` + `clean`.
- **Orphan reaping.** The `documents` orphan engine + `resumable_uploads._session` sweep older than presign TTL — keep; a presigned blob with no DB row is both cost and a leak if the key is guessable (use **random UUID keys**, already specced "flat UUID object-store keys").
- **Browser→bucket CORS/TLS.** ADR-001's "terminate `/<bucket>/` on the same Nginx" is the safe pattern — one hostname/cert, tight CORS allowlist (not `*`), TLS only. Write the CORS policy down; a wildcard here exposes every tenant's bucket to any origin.
- **Document sharing / external tokens.** `documents._share(external_token, password_hash, expires_at)` — require `expires_at`, rate-limit token guesses, and gate share creation through `document.share` (already a rule set) so classification (`security_classification`) is checked before an external link is minted. A `restricted` doc should not be shareable by external token at all.
- **`security_classification`** exists on `_document` — make it *enforced* in the routing resolver and portal filters, not just a label.

---

## 6. AI data flows

The governing rule is sound and already written: **AI mutations are proposals carrying `source="ai"`, human-confirmed, revertible, audited** (principle 10; `erp_chat._action` with `before_state`/`decided_by`/`reverted_by`; ADR-001 §5). Keep it as an inviolable invariant and test it. Additions a mining/IRAP-type client security review will demand:

- **AI data-flow register (`ai_gov`).** Produce the optional `ai_gov` artefact: for each AI touchpoint (`erp_chat`, `ai_agents`, `compliance_ai`, `voice`, `closeout` slot suggestions, `forms`/`item_types` import-assist, `inspection_requests` notice drafting, `reconciliation` candidate scoring) record: what data leaves the platform, to which model/endpoint, whether it includes client-confidential or personal data, retention by the provider, and the human-confirm gate. This is the single document an external reviewer asks for first.
- **Prompt-injection boundary.** `erp_chat`/`search`/`retrieval` read OCR'd dockets, ingested P6 notes, and document metadata — all **untrusted content**. Treat retrieved/OCR'd text as data, never instructions; the human-confirm gate is the backstop, but also constrain tools to read-only-by-default and require explicit confirmation for any `_action` that writes.
- **Tool authZ = user authZ.** An AI tool call must run under the **requesting user's** scope and RLS context, not a service identity — otherwise the assistant becomes a confused-deputy that reads across projects. `ai_agents._run.trust` must map to the user's tier, not exceed it.
- **No AI into the legal chain unattended.** AI-drafted notices (`inspection_requests`), closeout slot bindings (`closeout._binding.suggested_by_ai`), and TQ drafts must pass a human sign-off before they acquire a B-LTA signature or enter an MDR.
- **Data residency / egress.** Name where embeddings and chat payloads go (on-prem vs hosted model) — ties to TD-004 hosting.

---

## 7. Compliance evidence, secrets, backup, supply chain

- **Compliance artefacts to produce (gaps):** (1) the hash-chained **audit trail** as one contract (§3.2); (2) **access logs** — auth events, portal/field magic-link issue+use, grant changes, admin actions — append-only; (3) the **AI data-flow register** (§6); (4) a **retention & backup policy** doc (how long records live, WORM for legal subset, restore testing); (5) a **data-flow / DPIA-style map** for personal data (field-worker phones/PINs, client contacts, OSHA injured-person details in `safety._incident` — that is sensitive personal data and needs access restriction + retention rules). None of these exist yet; most are small docs, not code.
- **Secrets (hard rule 10).** No secrets in repo; synthetic fixtures only. Add: JWT signing keys, TSA/KMS creds, object-store keys, webhook secrets (`integrations._webhook.secret`, `notifications._webhook_target.secret`) all from env/secret-store; a CI secret-scan gate; and verify `safety._incident.injured_person_details` never appears in a fixture.
- **Backup/restore.** `backup` module is "no tables, export/import user-data backups" — under-specified for an evidentiary system. Specify: encrypted backups, **restore drills**, and that a restore cannot silently rewrite an append-only ledger (anchor the head so a doctored restore is detectable — §3).
- **Supply chain.** Heavy lazily-imported libs (`fitz`/PyMuPDF, `trimesh`, `cv2`, `pyarrow`, the P6/`.xer` parser) process **untrusted files** — pin versions, run `pip-audit`/Dependabot in CI (prebuilt GHCR images make this easy), and run CAD/PDF/P6 parsing in the already-isolated ARQ worker (which ADR-001 does — good; add resource limits so a malicious file OOMs only the worker).

---

## Recommendations

| # | Recommendation | Rationale | Effort | Priority | Affects |
|---|---|---|---|---|---|
| R1 | `signing` produces **PAdES-B-LTA** signatures (cert chain + RFC 3161 doc timestamp) for MDR/pack/notice PDFs that are legal records; schedule B-LTA re-stamping | Hash+name is not court-defensible; B-LTA validates years later from the file alone | M | P1 | `signing`, `workpacks`, `inspection_requests`, `closeout`; Ph1/5/6b |
| R2 | Add **RFC 3161 trusted timestamps** on every sign-off and on a periodic **ledger-head anchor** across the three chains | Proves *when*, not just *order*; underpins delay-attribution entitlement | M | P1 | `inspections`, `inspection_requests`, `qms`, `audit`; Ph1 |
| R3 | ADR + implementation for **key management** (KMS/HSM, rotation, public-verification bundle) for signing/timestamp/ledger keys | A lost/leaked key voids every record it backed | M | P1 | `signing`, `docs/decisions/`; Ph0/1 |
| R4 | Write the **legal subset to WORM / object-lock** storage and anchor the ledger head externally | `REVOKE UPDATE/DELETE` is an app-role control, not storage immutability | M | P1 | `documents`, `backup`, ADR-001; Ph5 |
| R5 | Define **one `audit` contract** (append-only, hash-chained, RFC3161-anchored) that `qms`/`admin`/`portal`/`erp_chat`/ledgers all write through | Three half-specified audit stores ≠ one system of record | M | P1 | `qms`, `admin`, `audit`; Ph0/1; new spec-gap TD |
| R6 | JWT: **asymmetric RS256/EdDSA + JWKS**, drop shared HS256 secret | Shared secret lets any verifier mint tokens; precondition for clean SSO | S | P1 | `users`, T-0007, TD-003; Ph0 |
| R7 | **Short-lived access + rotating refresh + server-side revocation (`jti` list)** | Self-contained JWTs aren't revocable; terminated users/grants must die now | M | P1 | `users`, T-0007; Ph0 |
| R8 | Harden **`field_diary` magic-link/PIN**: single-use high-entropy token, PIN as 2nd factor, rate-limit/lockout, device-bound, revocable, bounded offline TTL; add `field_auth.issue` rule | Softest internal surface; guards progress/labour writes | M | P1 | `field_diary`, `validation`; Ph0/4 |
| R9 | **Portal/external tokens**: mandatory `expires_at`, least-privilege per-resource scope, bind co-sign identity to issued principal, revoke on grant change | v2/v3 portal are *write* paths for untrusted clients | S | P1 | `portal`, `inspection_requests`; Ph7 |
| R10 | **Resolve TD-007 = keep `tenant_id`+RLS**; add 4 bypass tests (ARQ, cross-module service, raw SQL/rollups, superuser) + prod start-up RLS-enforcement check | Cheap now, brutal retrofit; bypass modes are the real risk | S | P1 | all modules, ARQ, CI; Ph0, TD-007 |
| R11 | Make **upload scan/quarantine + sha256 verify** blocking before a file is `available`/downloadable; enforce `security_classification` in routing + portal filters | Presigned boundary can serve malware or leak restricted files | S | P2 | `uploads`, `documents`, ADR-001; Ph0/5 |
| R12 | Tighten **bucket CORS/TLS** (explicit origin allowlist, no `*`), random UUID keys, least-privilege presigned URLs | Browser→bucket is a direct trust edge | S | P2 | ADR-001, `resumable_uploads`; Ph0 |
| R13 | Produce the **AI data-flow register (`ai_gov`)** + constrain AI tools to the requesting user's RLS scope; treat OCR'd/ingested text as data not instructions | First thing an IRAP/mining security review asks for; prompt-injection backstop | M | P2 | `erp_chat`, `ai_agents`, `search`, `retrieval`, `compliance_ai`; Ph5 |
| R14 | Make `valid_for_legal_record=true` **require** B-LTA signature + eligibility snapshot + in-date calibration (engine rule) | Legal-record flag must be evidence-gated, not free-text | S | P2 | `construction_control`, `eligibility`, `signing`; Ph1 |
| R15 | **Retention/backup policy** doc + encrypted backups + restore drills + personal-data map (field phones/PINs, `safety` injured-person details) | Compliance evidence gap; sensitive personal data unmanaged | S | P2 | `backup`, `safety`, `field_diary`; Ph0 |
| R16 | **Resolve TD-003 = local JWT behind `AuthProvider` now, Entra ID (OIDC) staff SSO later**, record R6/R7 on T-0007 | Don't block Ph0; SSO worth it in an Entra-ID shop; fix crypto regardless | S | P2 | `users`, T-0007, TD-003; Ph0 |
| R17 | **Supply-chain gate**: pin versions, `pip-audit`/Dependabot in CI, resource-limit the ARQ worker parsing untrusted CAD/PDF/P6 | Heavy parsers process untrusted files | S | P3 | CI, ARQ, ADR-001; Ph0 |
| R18 | **CI secret-scan** gate + verify no secrets/real client data in fixtures (hard rule 10) | Enforces an existing hard rule | S | P3 | CI, all fixtures; Ph0 |

---

## Conflicts & risks

- **`REVOKE UPDATE/DELETE` is described as the integrity guarantee, but it is an app-role control.** Not a conflict with the hard rules, but the spec risks *over-claiming* "tamper-proof" (`inspection_requests`, `inspections`, `forms` all use the phrase). A superuser, a `GRANT` reversal, or a doctored backup defeats it. The spec must distinguish **tamper-evident** (hash chain) from **tamper-resistant** (WORM + external anchor). Fix via R2/R4/R5.
- **Symmetric HS256 in the as-built reference (TD-001 "port & adapt")** conflicts with the ARQ-worker and future-SSO topology — porting it verbatim spreads a signing secret to the worker. R6 overrides this specific pattern (flag it as a deliberate deviation from the ported baseline).
- **ARQ worker vs RLS (principle 4).** The modular-monolith/ARQ split (ADR-001) creates an execution context where "tenant comes from the JWT" has no JWT. Unresolved, this is a latent cross-tenant bypass. R10 closes it; must be designed in Phase 0, not retrofitted (the plan's own §9 risk note agrees).
- **AI confirm-gate vs claims integrity.** Principle 10 is correct, but an AI-drafted `inspection_requests` notice or `closeout` binding that a human rubber-stamps can still enter the legal chain. R13/R14 keep AI output out of signed records until explicitly attested.
- **Single-company doctrine vs RLS effort (TD-007).** The only real tension; resolved by noting project/site scope reuses the same machinery, so RLS is not wasted even with one tenant. Keep the doctrine; keep RLS.
- **Top risks:** (1) shipping Phase 1 ledgers *without* timestamps/anchoring, then discovering in a dispute the records don't hold — hard to retrofit onto already-signed history; (2) an under-specified magic-link flow shipped to the field before hardening; (3) an ARQ job written without tenant binding becoming the cross-tenant leak.

---

## Open questions for the owner

1. **(TD-003)** Confirm: local JWT behind `AuthProvider` now, **Entra ID OIDC for staff** later? And accept R6/R7 (asymmetric keys, refresh/revocation) as mandatory on T-0007 regardless?
2. **(TD-007)** Confirm **keep `tenant_id` + RLS, one tenant per deploy** — with the four bypass tests as Phase-0 gates?
3. **(new TD — propose "evidentiary-records assurance level")** What legal weight must KERP records carry — internal tamper-evidence, or **tribunal-grade (RFC 3161 + PAdES-B-LTA + WORM)**? This decides R1–R5 scope and should be set *before* Phase 1 freezes the ledger contract. Likely a `decision` TODO.
4. **(ties TD-004)** Where do signing/timestamp keys and AI/embedding payloads live — on-prem (MinIO/softHSM, self-hosted TSA or a commercial QTSP) or cloud (KMS, hosted model)? Drives R3, R13 and the `ai_gov` register.
5. Is a **qualified/third-party TSA** acceptable, or must timestamps be fully self-hosted (air-gapped sites)? Affects B-T/B-LTA feasibility.
6. Do any deployments carry **personal data beyond contacts** (field-worker IDs, OSHA injured-person details) that trigger a formal DPIA/retention regime? Drives R15.

## Feeds-the-plan

Concrete items for the merged plan (coordinator can lift directly):
1. **Phase 0 security spine:** asymmetric JWT + JWKS, refresh/revocation (`jti`), scrypt params + MFA for admin/manager; `AuthProvider` seam carries these (T-0007). *(R6, R7, R16)*
2. **Phase 0 isolation gates:** tenant-binding path for ARQ jobs; prod start-up RLS-enforcement refusal (port from as-built H-03); four bypass contract tests (ARQ / cross-module service / raw-SQL rollups / superuser). Resolve TD-007 = keep. *(R10)*
3. **New `audit` contract** (one append-only, hash-chained, RFC3161-anchored log) specced in Phase 0/1, written through by `qms`/`admin`/`portal`/`erp_chat`/ledgers. Raise as a spec-gap TODO. *(R2, R5)*
4. **Evidentiary upgrade for Phase 1 ledgers *before* the contract freezes:** RFC 3161 timestamps on sign-offs + periodic cross-chain head anchor; `valid_for_legal_record` gated on B-LTA + eligibility + calibration. *(R2, R14)*
5. **`signing` → PAdES-B-LTA** with a KMS/HSM key-management ADR and re-stamping schedule, consumed by `workpacks`/`inspection_requests`/`closeout` MDR compile. *(R1, R3)*
6. **File-boundary rules** (`upload.complete` scan+sha256 blocking, bucket CORS allowlist, least-privilege presigned URLs, enforced `security_classification`, mandatory `expires_at` on shares/portal tokens) into ADR-001 and the `documents`/`uploads` specs. *(R11, R12, R9)*
7. **`field_diary` magic-link/PIN hardening** + `field_auth.issue` rule set, designed in Phase 0 alongside the offline sync ledger (plan §9 already says sync must be designed in early). *(R8)*
8. **AI governance:** the `ai_gov` data-flow register, AI-tool-runs-as-user RLS scoping, OCR/ingested-text-as-data, and "no unattended AI into the legal chain." *(R13)*
9. **Compliance evidence pack:** retention/backup policy, restore drills, encrypted backups with ledger-head anchoring, personal-data map; CI secret-scan + supply-chain (`pip-audit`, pinned versions, ARQ resource limits). *(R4, R15, R17, R18)*

---

*Standards cited:* ETSI EN 319 142-1/-2 (PAdES baseline levels B-B/B-T/B-LT/B-LTA); RFC 3161 (trusted timestamp tokens); OWASP ASVS v5.0 (V6 Authentication, V7 Session Management, V9 Self-contained Tokens) and OWASP Proactive Controls C6 (JWT revocation needs server-side state); NIST SP 800-63B (one-time verifiers) for the magic-link/PIN flow.
