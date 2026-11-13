# PR suggestions & surfaced notes

> **Generated — do not hand-edit.** Rebuild with `python scripts/pr_suggestions.py rebuild`.
> A running digest of everything agents surfaced on merged PRs — review notes, must-fix items, suggestions, nits, follow-ups and ideas — so nothing is lost after a squash-merge. Formal follow-ups also live as `TD-xxx` in `tracker/todo/` and on [TODO.md](TODO.md); this file is the readable skim. Each item links back to its PR or review for full context.

_Repo `kingdonk-ops/Kerp-`, base `dev` · 32 merged PRs · 32 with surfaced notes._

---

## PR #34 — plan: TD-034 ADR 0007 port/rewrite verdicts for remaining Inspection-ERP core packages
task `T-0015` · merged 2026-11-11 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/34)

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=71a452197d4b33e7ef22b4f6550fe95d1bc79258** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/34#pullrequestreview-5851781675)

- _Must fix_
  1. `tracker/tasks/T-0015.md:10` and `:17-20`. ADR 0007 says its "Rules for the jobs worker" **bind T-0015**, but T-0015 does not carry them in a form its reviewer will enforce:
     - `spec:` does not list `docs/decisions/0007-port-remaining-inspection-erp-core.md`.
     - The acceptance criteria are still only "Both queues process; priority honoured / Worker container runs".
     - The rules exist only in a free-text Note.
     Several of these rules are new scope, not restatements of the goal: RLS on the jobs table with tenant and project in the SAQ kwargs, enqueue-after-commit plus a re-enqueue sweep, and storing only a sanitised error. Because the T-0015 review checks acceptance criteria and spec refs, these rules will not be gated.
     Fixed looks like this:
     - ADR 0007 is added to T-0015 `spec:`.
     - T-0015 gets testable acceptance criteria for each of the 5 rules. For example:
       - "jobs table is BaseRecordMixin + RLS, no RLS exemption; worker binds tenant/project from job kwargs before reading the row (cross-tenant poll -> 404)"
       - "job enqueued only after commit; rolled-back request leaves no SAQ job; sweep re-enqueues rows stuck in queued"
       - "kind declares queue"
       - "async handlers, heavy imports lazy"
       - "stored error sanitised + bounded; traceback logged with request id"
       - "`GET /jobs/{id}` poll + `update(progress, message)`"
     - If this takes T-0015 past size M, it is split instead.

- _Suggestions (non-blocking)_
  - Every new note has a doubled date prefix, `- 2026-11-11: 2026-11-11: ...`, at T-0015:30, T-0016:29, T-0108:37, T-0126:47, T-0133:40 and TD-034:17. Drop the inner date.
  - `tracker/tasks/T-0108.md:13` adds a stray blank line after the frontmatter. It is harmless.
  - Add ADR 0007 to `spec:` for T-0016, T-0126 and T-0133, like T-0108 already does for ADR 0003/0004, so the verdicts are gated there too. T-0110 is in the ADR's "Affects" list but got no note. Add a one-liner about the `response_type` vocabulary and `_validate_sections` as lint codes.
  - The ADR assigns the transactional outbox to E-05-07, whose title is "outbound event bus + webhooks". Add a line to E-05-07 so the planner sees it when breaking the epic down.
  - The `mapping/` fixture "looks client-derived". Consider a `risk` TODO so nobody copies it later. This is optional, because the ADR already says not to copy it.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=71a452197d4b33e7ef22b4f6550fe95d1bc79258

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=71a452197d4b33e7ef22b4f6550fe95d1bc79258** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/34#pullrequestreview-5851828074)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - The two notes added in this commit bring back the duplicate-date prefix that round 1 removed elsewhere: `tracker/tasks/E-05-07.md:23` and `tracker/tasks/T-0110.md:34` both start with `- 2026-11-11: 2026-11-11:`. This looks like `tracker.py` adding a date to a note that already has one. Strip the date from the note text, or make the script deduplicate it.
  - `tracker/tasks/T-0015.md`: the poll/update criterion is the only one without "(test)". Consider adding a contract test for it.
  KERP-REVIEW: APPROVED patch-id=71a452197d4b33e7ef22b4f6550fe95d1bc79258

---

## PR #33 — T-0003: Dev infra: docker-compose (PG16+PostGIS+ltree, Redis7, MinIO), Makefile, .env.example
task `T-0003` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/33)

**TODOs raised**

- TD-131 (missed): root `.env.example` (a permission rule on `.env*` paths blocked writing it), plus a drift test against CI
- TD-132 (risk): run `make reset && make test-pg` once on a machine with Docker; check the MinIO image tag still pulls
- TD-133 (later): a dev database separate from `kerp_test`, the MinIO bucket and backend storage settings
- TD-052: marked resolved in this branch, pointing to TD-132 for the end-to-end check (reviewer to judge)

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=6c614b73beae2eb9cbfe939784e96b9e67a4fbe4** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/33#pullrequestreview-5839453583)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - **Line endings.** Add a `.gitattributes` with at least `Makefile text eol=lf`, `*.sql text eol=lf`, `*.yml text eol=lf` and `*.sh text eol=lf`. With `core.autocrlf=true` (true on this Windows checkout) the Makefile, SQL and compose files check out as CRLF. psql and compose tolerate that. A Makefile with CRLF under MSYS/Git-Bash make is a known source of `\r` errors in recipes and `?=` values, and Windows + Git Bash is the documented platform. A good fit for TD-132's first real run.
  - **PyYAML.** `backend/tests/test_dev_infra.py:10` imports `yaml`, which only arrives through cel-python. It is pinned in `requirements.lock:757`, so installs are deterministic and a missing package would fail loudly, not silently. Still, list `pyyaml` explicitly in the dev/test dependencies so a future cel-python change cannot remove it.
  - **Guarded test list.** `backend/tests/test_dev_infra.py:218` `PG_TESTS` leaves out `tests/test_pg_extensions.py`, but the Makefile's `PG_TESTS` includes it. Add it to the guarded tuple so it cannot be dropped from `make test-pg` without a test noticing.
  - **TD-132 check list.** When TD-132 is worked, also confirm `docker compose up --wait` is supported by the installed Compose version (v2.17+) and note that in the README.
  KERP-REVIEW: APPROVED patch-id=6c614b73beae2eb9cbfe939784e96b9e67a4fbe4

---

## PR #32 — T-0011: Module loader + manifest contract + per-project enable/disable
task `T-0011` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/32)

**TODOs raised**

- TD-129 (deferred): admin capability guard on the toggle API once RBAC lands
- TD-126 (later): CI check that `src/api` matches the backend schema
- TD-127 (later): module settings UI and hiding disabled modules in navigation
- TD-128 (later): race between two simultaneous first toggles of the same module on the unique constraint
- Resolved: TD-036, TD-018

Note: rebased on T-0008 (#31); this PR's migration was renumbered to `0004` (down_revision `0003`).

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=2331949739cc0c67de5465fb8116547e7947b485** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/32#pullrequestreview-5838936629)

- _Must fix_
  1. **`backend/app/core/module_loader/router.py:40` and `:49`: the toggle endpoints do not require an authenticated principal.** The lack of a capability tier can wait for TD-129. The lack of authentication cannot: `CurrentPrincipal` is available. Today an anonymous request and a request with an invalid token both get 404, not 401. `get_tenancy_context` also never checks `is_active`. So once TD-122 binds a project, a deactivated user with an unexpired token, or any caller whose token carries a project, can call a mutating endpoint without the 401/active-user check every other authenticated endpoint has. Fixed means:
     - Both handlers take `principal: CurrentPrincipal` (`app.core.auth.deps`).
     - `set_module` passes `actor_id=principal.user_id`.
     - New HTTP tests use real login tokens, as in `tests/test_auth_api.py`, not the override: no token gives 401 with `WWW-Authenticate: Bearer`; an invalid token gives 401; an inactive user gives 401; a valid tenant-level token gives 404.
     - The existing seam tests still pass. Where a test needs a project bound, override `get_current_user` or `require_claims` as well.
     - TD-129 keeps only the admin capability tier.
  2. **Tracker does not say the gate depends on TD-122, and some text is stale.**
     - `tracker/tasks/T-0011.md:36` says "T-0008 only has to fill `get_tenancy_context`". That is no longer true: T-0008 filled it without a project, so the gate and the toggle API stay inert until TD-122.
     - `T-0011.md:36` and `:38` still say migration `0003` and head `0003`; they are now `0004`.
     - `tracker/todo/TD-129.md:12` says "The

- _Suggestions (non-blocking)_
  - Requests without a project are not gated (`gate.py:196`). That is safe only while tenant-level sessions cannot see project rows. Put that invariant in TD-122, so whoever binds the project knows that a caller can switch it off by leaving the project out. That must never expose project data from a disabled module.
  - Anonymous requests reach module routes, because the gate is not authentication. Once real modules land, consider a `later` TODO to have `mount_modules` add `CurrentPrincipal` as a default router dependency, so a module cannot ship an unauthenticated route by accident.
  - `uq_project_module_setting_module` ignores `deleted_at`. That is fine today because `set_enabled` never soft-deletes. Keep it in mind if a reset-to-default feature deletes rows.

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=ac3ae7d55c84abe7f627e02f750b8f45867af3ac** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/32#pullrequestreview-5839081894)

- _Suggestions (non-blocking)_
  - The PR body still says "migration `0003`". Update it to `0004` before merge.

---

## PR #31 — T-0008: Auth: users, RS256/EdDSA JWT + JWKS endpoint, 15-min access + rotating refresh, jti revocation in Redis
task `T-0008` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/31)

**TODOs raised**

- TD-120 (spec-gap): how a user picks a tenant at login
- TD-121 (spec-gap): refresh-token lifetime, absolute session limit, and SPA transport
- TD-122 (deferred): binding requests to a project (T-0010/T-0012)
- TD-123 (later): login throttling and lockout (T-0009)
- TD-124 (later): check production auth settings at startup

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=50f49d5cb683900d2bd4c0cd7a8167bceb0d015c** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/31#pullrequestreview-5838603168)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `backend/app/core/auth/service.py:83-84`: both `iat` and `password_changed_at` are truncated to whole seconds, so a token issued earlier in the same second as a password change still passes. The window is under 1 s. To close it, store `password_changed_at` rounded up to the next second, or compare against `iat` with sub-second precision.
  - Migration numbering: T-0011's branch also adds `0003_project_module_settings.py` with `down_revision="0002"`. Whichever PR merges second must renumber to `0004`, or alembic will have two heads.
  - TD-123: login is unthrottled and every attempt costs a scrypt round, which makes it an easy way to burn CPU. Make sure T-0009 picks this up before any shared or non-dev deployment.
  - `router.py:89`: logout goes through `CurrentPrincipal`, so a user who has just been deactivated gets a 401 from logout instead of a 204. Their tokens are already dead, so this is harmless. `require_claims` would be enough here if you want logout to always succeed.
  KERP-REVIEW: APPROVED patch-id=50f49d5cb683900d2bd4c0cd7a8167bceb0d015c

---

## PR #30 — plan: TD-113 at most one work anchor per path + CLAUDE.md section 6 wording
task `T-0101` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/30)

**TODOs raised**

- none

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=b94d2cfc0d119f3ee61bca4aa8d322bb0ae8dbd3** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/30#pullrequestreview-5838467534)

- _Must fix_
  1. `tracker/tasks/T-0101.md:24` — "booking work on it → 422 until a work-anchor node exists on the path" is not testable inside T-0101: T-0101 owns the tree (level defs, nodes, move), not any booking endpoint (progress, time lines, NCR completion live in other modules/tasks), so the implementer has nothing to send a request to and the reviewer cannot verify it. Fixed looks like: name the seam T-0101 delivers and tests, e.g. "`asset_management.service.work_anchor_for(node_id)` returns the work-anchor node on the node's root-to-leaf path (ancestor-or-self, or the single descendant on a leaf path), and raises a `no_work_anchor` domain error that routers map to 422 when the path has none (tested on a path with and without a work anchor, both packs)"; and either add a line to the booking tasks (progress / time lines / NCR completion) that they call it and return 422, or raise a `missed` TODO via `new-todo` listing those tasks so the 422 is enforced where booking actually happens.

- _Suggestions (non-blocking)_
  - `docs/decisions/0003-configurable-breakdown-and-terminology.md:137-138` — the prescribed CLAUDE.md wording says "inspections to any `inspection_anchor` level; no BOQ anchor anywhere" while CLAUDE.md L67 now says "... `inspection_anchor` level or an Item. No BOQ anchor anywhere." CLAUDE.md is the more correct one (ADR §6, quality-and-inspection.md); align the ADR quote to it so the two are identical.
  - `docs/spec/modules/asset-and-tagging.md:58` — "a finding (TD-106) may create it when it raises follow-up work" goes slightly beyond the TD-113 resolution text ("the user picks or creates it first"); consider "the user raising follow-up work from a finding (TD-106) picks or creates it" so no automatic node creation is implied.
  - `tracker/tasks/T-0101.md:13` — stray blank line inserted after the front matter; harmless.
  - CLAUDE.md change: TD-119 itself says this is a human edit. The approval is recorded as user-given in chat; I cannot verify that from the repo, so the human merging should confirm it.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=b94d2cfc0d119f3ee61bca4aa8d322bb0ae8dbd3

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=a762868376dce80bbce7c7c803646b749c88a09f** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/30#pullrequestreview-5838486985)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `tracker/tasks/T-0116.md:19` — an NCR may anchor to an Item, but `work_anchor_for` takes a `node_id`. Say how the Item case resolves (its `work_anchor_id` from T-0103, else its `asset_id` node path) so the T-0116 implementer does not have to guess.
  - `tracker/tasks/T-0101.md:25` — with ancestor-or-self only, a node *above* the work-anchor level (e.g. an Asset with several RSWs below) raises `NoWorkAnchor`. That is a reasonable reading of "never guessed", but state it explicitly in asset-and-tagging.md §Anchor invariants so NCR/progress callers anchored at the Asset level get an expected 422, not a surprise.
  - `tracker/todo/TD-125.md` `source: reviewer` — it was raised by the implementer from a review comment; either is fine.
  KERP-REVIEW: APPROVED patch-id=a762868376dce80bbce7c7c803646b749c88a09f

---

## PR #29 — plan: TD-112 apply the 2026-11-10 triage to spec and tasks
task `T-0003` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/29)

**TODOs raised**

- TD-113 — spec-gap: "exactly one" vs "at most one" work anchor per path (default: at most one)
- TD-114 — spec-gap: P6 rule-set names and how links resolve across levels (default in §5.5)
- TD-115 — later: P6 Excel exports and direct P6 API
- TD-116 — decision: approval routes don't exist until Phase 6 (interim default: a second Quality Director or Operations Management user approves an unlock)
- TD-117 — spec-gap: findings register detail (TD-106)
- TD-118 — decision: who edits the tenant-wide qualification catalogue ("super admin")
- TD-119 — later: apply the CLAUDE.md §6 anchor wording

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=9ae3d9d5aeecba2835639d0f435c67191fb403ab** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/29#pullrequestreview-5838305598)

- _Must fix_
  1. **ADR 0006 acceptance (TD-080) is not applied to the planner spec.** The acceptance says interrupt scope granularity is **a subtree of the breakdown, not the whole project and not resource groups**. `docs/spec/modules/planner-and-work-plans.md:88` still allows `project`, `nodes`, `resource_groups` or both, and `:110-111` still describes `interrupt_scope = resource_groups`. `tracker/tasks/E-04-12.md:26` also still says programme-layer/commit tasks carry `blocked_by: [TD-080]` "until ADR 0006 is accepted", but TD-080 is resolved. **Fixed:** `interrupt_scope` is limited to node subtrees (drop `project`/`resource_groups`, cite ADR 0006 Acceptance), the resource-group interrupt paragraph is removed or reworded, and the E-04-12 text drops the TD-080 block and records the subtree decision.
  2. **Inspection lifecycle names disagree between spec and task.** `tracker/tasks/T-0111.md:18,21` requires `kind IN (inspection, itp)` and `PENDING → INSPECTION_REQUESTED → …`, with no `rfi` kind and no `RFI_ISSUED`. The spec section that T-0111 cites still defines `kind IN (inspection, itp, rfi)` (`docs/spec/modules/quality-and-inspection.md:18`) and `PENDING → RFI_ISSUED → …` (`:26`). `docs/spec/KERP-MASTER-SPEC.md:574,642,657` (§4.2) still uses `RFI_ISSUED`. An implementer gets two answers. **Fixed:** follow the pattern you used for TD-113/TD-114. Write the planner-default names into quality-and-inspection.md (`kind IN (inspection, itp)`, `INSPECTION_REQUESTED`, marked "default pending TD-045 spec"). Add a one-line amendment pointer at master spec §4.2 (as you did for §1.3/§4.3), so the sp

- _Suggestions (non-blocking)_
  - TD-113: ADR 0003 Amendment 1 says "exactly one" work anchor per path. The spec and T-0101 enforce "at most one" by default. That is defensible: incremental tree building, and Amendment 5 can be read either way. It is recorded as a spec-gap, so it is not blocking. Get the user's answer before T-0101 merges, because the default weakens an accepted amendment.
  - `tracker/tasks/E-04-11.md:22` still lists TD-072/TD-073 as open decisions. Both are resolved; point to the handover.md first-shift text and TD-083 instead. The handover.md fallback ("first project sign-in until the roster exists") is a planner addition to the TD-073 decision; consider noting it on TD-083.
  - T-0035 and T-0131 name `oe_validation_override` and its columns, but quality-and-inspection.md §Per-record override describes it in prose only. Consider adding the table name and columns to the spec, since T-0131 freezes its shape.
  - T-0018/T-0034: ADR 0004 puts the `system` layer in `BaseRecordMixin` tables with tenant RLS. Make sure the implementer knows whether system rows are per-tenant copies or need a tenant-less policy (T-0007 has no tenant-less rows). Consider a one-line note on T-0034.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=9ae3d9d5aeecba2835639d0f435c67191fb403ab

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=39cef806b20867be85d85c1ac77606ad3525b380** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/29#pullrequestreview-5838405147)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - The TD-045 names, TD-116 and TD-118 are now user-confirmed, but some text still calls them a "default":
    - `quality-and-inspection.md:21,28` and the master spec §4.2 pointer ("planner default pending the TD-045 spec")
    - `T-0111.md:18`
    - `T-0118` ("edited per the TD-118 default")
    - `T-0034` ("interim approver per TD-116")
    Reword to "decided" in a later pass.
  - `T-0035.md:19` lists `decision` as `pending/approved/rejected`, but the new spec table adds `void` and more columns (`finding_id`, `rule_set_key`, `approver_persona`, `audit_chain_key`, `decision_comment`). Point the criterion at the spec table, or add `void`, so the frozen shape (T-0131) has one source.
  - TD-113 ("at most one" vs "exactly one" work anchor per path) still needs the user's answer before T-0101 merges.
  KERP-REVIEW: APPROVED patch-id=39cef806b20867be85d85c1ac77606ad3525b380

---

## PR #28 — plan: TODO triage 2026-11-10 — record 56 decisions, accept ADRs 0003-0006
merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/28)

**TODOs raised**

- TD-112 — planner pass to apply the accepted ADRs and resolved decisions to the spec, CLAUDE.md §6 and task acceptance criteria, and to cut the CI/dev tasks (TD-047/048/051, TD-052, TD-060)

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=d09d5759edb88beac7c48af938cf73c15c99119f** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/28#pullrequestreview-5837989251)

- _Must fix_
  1. `docs/decisions/0003-configurable-breakdown-and-terminology.md:169`: the precedence sentence says amendments win only over "the Decision above". The **Consequences** section still states the per-project rule that Amendment 1 replaces:
     - line 136-137: CLAUDE.md §6 wording "work anchors to the project's **work-anchor level**"
     - line 139-140: T-0101 "the one-work-anchor-level invariant"
     TD-112 tells the planner to apply "spec edits listed in each ADR's Consequences", so as written it points the planner at the superseded invariant. **Fixed looks like:** either widen the sentence (e.g. "Where they conflict with anything above, Decision or Consequences, they win") or amend those two bullets in place to say "one work anchor per root-to-leaf path, never nested". I suggest the same wording change in ADR 0004 line 115 for consistency, but ADR 0004's Consequences do not currently conflict, so that part is optional.

- _Suggestions (non-blocking)_
  - Resolving TD-075 unblocks T-0126 (now ready) and T-0133. T-0018 was already ready. TD-112 lists both T-0018 and T-0126 for acceptance-criteria updates, but TD-112 has `tasks: []` and `kind: later`. Consider linking TD-112 to the affected tasks, or having the coordinator hold T-0018, T-0101 and T-0126 until the TD-112 planner pass lands. Otherwise an implementer may build T-0018 without ADR 0004 amendments A-E.
  - ADR 0004 Amendment E says the new kinds replace "the separate P6 mapping-profile rules". The TD-079 decision note only says "add". This does follow from the TD-079 options, but adding "(replaces the mapping-profile regex/CEL)" to TD-079's resolution would make the two match exactly.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=d09d5759edb88beac7c48af938cf73c15c99119f

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=8eb10ac414e23330b500bf57dd7f2cb647c64c38** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/28#pullrequestreview-5838005150)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - T-0018 is upstream of many Phase 1 tasks, so blocking it on TD-112 holds most of Phase 1 back. Schedule the TD-112 planner pass next.
  KERP-REVIEW: APPROVED patch-id=8eb10ac414e23330b500bf57dd7f2cb647c64c38

---

## PR #27 — plan: planner work plans, client plan review, scaffolding, change of scope, register views + ADR 0006 (TD-080..TD-111)
task `T-0020` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/27)

**TODOs raised**

- TD-080..TD-085: planner/portal/scaffold decisions and gaps. These cover ADR 0006, external/proxy approval, the scaffold interval, the resource pool vs the HR cull, email transport and the distribution matrix.
- TD-086..TD-096: QA documents. Topics: per-party ITP codes, master vs instance ITP, CDRL register, document metadata, weld report, heat vs MDR, TQ concessions, deferred results, area handover sheet, bulk R/S signing, coating ITR row.
- TD-097..TD-110: registers, competency, CUI-system parity and time. Topics: welder qualification range, NDT sampling, credential types, role/competency matrix, LMS system of record, the M1 eligibility risk, inspection profiles, field palette, template applicability, client-asset findings, overlay inheritance, field_time, equipment availability, recurring due dates.
- TD-111: risk that all input files contain real data (extends TD-069).

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=4af0cbe02d4d8932945eb10f29ab92708f3ec1c4** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/27#pullrequestreview-5837282337)

- _Must fix_
  1. `docs/research/coastal-inputs-qa-documents.md:31-33`: the record-number examples contain the real site token. Together with the client description on line 3 it identifies the client and site. **Fixed:** use a placeholder site token (`<SITE>`).
  2. `docs/research/coastal-inputs-qa-documents.md:34, 39, 363, 367`: these lines contain real client functional-location / RSW IDs and asset file keys. **Fixed:** shapes only (`<FLOC>_<seq>`, `<asset>-<rsw>-<disc>-<seq>`).
  3. `docs/research/coastal-inputs-qa-documents.md:80, 250, 289, 363, 408`: these section headings are real source file names, and one of them contains a client contract number. **Fixed:** describe each file generically (e.g. "Signed ITP (coating)", "Completed weld QA report").
  4. `docs/research/coastal-inputs-qa-documents.md`: real controlled-document numbers, client spec numbers and a real TQ number remain at these lines:
     - `:23` and `:321`: the contractor/client document-number prefix.
     - `:65`: the client spec number and the TQ number.
     - `:261, 383, 400, 589, 603`: client spec numbers.
     - `:626, 649`: corporate form numbers.
     **Fixed:** shapes or placeholders (`<client spec>`, `TQ-<3d>`, `<form>-<6d>_<rev>`).
  5. `docs/research/coastal-inputs-registers-and-cui.md:335-340, 364, 369, 380-382, 417, 449, 512, 558`: real client company names and abbreviations appear in the CUI-system category, template and issue-type names. **Fixed:** neutral labels (client A / B / C).
  6. `docs/research/coastal-inputs-registers-and-cui.md:331`: this line contains a path on the user's local drive. **Fixed:** remove it.
  Af

- _Suggestions (non-blocking)_
  - `docs/spec/modules/planner-and-work-plans.md:132`: links to `scaffold-management.md` §6 for `needs_access = scaffold`. The planner integration is in §5 (§6 is photos and files).
  - E-04-12: the consolidation diff (precedence 1) culls HR. When the planner breaks the epic down, the resource-pool tasks should carry `blocked_by: [TD-083]`, and every commit or programme-layer task should carry `blocked_by: [TD-080]` until ADR 0006 is accepted.
  - `docs/spec/modules/scaffold-management.md:83`: state explicitly that `oe_scaffold_tag` is written from an `inspections` completion event or service call, not by reading `oe_inspections_*` tables.
  - `docs/spec/modules/portal-plan-review.md:161`: the client project lead can override absent approvers by default, including contractor approvers. Consider limiting overrides to the approver's own organisation, or add this to TD-081.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=4af0cbe02d4d8932945eb10f29ab92708f3ec1c4

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=169a586f31b5bdbed4275f36e57595c4458c7e15** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/27#pullrequestreview-5837334606)

- _Must fix_
  1. `docs/research/coastal-inputs-qa-documents.md:400` (G-RSW-4): this line was listed in round-1 must-fix 4. Only the first client spec number was replaced. The two spec numbers after `<client spec C1>/` are still the real client spec numbers, with the prefix removed. **Fixed:** `(<client spec C1>/<client spec C2>/<client spec P>)`, the same placeholders used at :383-384.
  2. Real RSW numbers and RFI serial numbers copied from the source documents are still present:
     - `docs/research/coastal-inputs-qa-documents.md:40-41`: short-form RSW examples, the grouped pair, and the multi-RSW cell with its asset code.
     - `:192-193`: RFI file serials.
     - `:252`: the RSW number in the header example.
     - `:419`: the RSW quoted in the client response.
     - `docs/spec/modules/planner-and-work-plans.md:123`: the two RSW ids in the multi-anchor example.
     Alone these values are weakly identifying. They are still real client data, though, and with the site and client description they point back to the source set. **Fixed:** synthetic values or shapes, e.g. `100.1`, `100.2`, `<rsw>.<sub>`, `…-CV-RFI-0001`, `RSW 100.1 only`, `01-100.1-2` / `02-101.1-2`.

- _Suggestions (non-blocking)_
  - `docs/research/coastal-inputs-registers-and-cui.md:30, 210` and `coastal-inputs-qa-documents.md:255`: WPS suffixes and weld numbers are taken from real registers. They do not identify anything, but synthetic values (`XXX-001`, `BW-01`) would be cleaner.
  - `docs/research/coastal-inputs-qa-documents.md:366`: the fixed numeric token in the third-party report number shape could be `<area>` for consistency.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=169a586f31b5bdbed4275f36e57595c4458c7e15

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=ab9510455e769bee7f2ee8ea2da537398f270b76** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/27#pullrequestreview-5837346901)

- _Must fix_
  1. `docs/research/coastal-inputs-qa-documents.md:208`: the "RSW No. (may hold several: …)" example in the RFI structure still quotes the real grouped RSW pair. Round 2 replaced this exact value with `100 & 101` at :40-41. **Fixed:** use `100 & 101` (or `<rsw> & <rsw>`), the same as :41.

- _Suggestions (non-blocking)_
  - `docs/spec/modules/planner-and-work-plans.md:21, 51, 122`: the P6 activity-name example, the shut-window calendar names and the scope-area heading look copied from the source programme or plan. If any of them is a real asset code, calendar name or plant-area name, swap in shapes (`<ASSET> / CP / <rsw>`, `… <SHUT> Preps`, "Scope Area 1 – <area>").
  - `docs/research/coastal-inputs-qa-documents.md:218`: the coating-batch example (batch number plus grid reference) could become `Batch #<n> [<grid ref>]`.
  - `docs/research/coastal-inputs-registers-and-cui.md:590`: if the delay code number and label come from a client delay-code list, use a synthetic code.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=ab9510455e769bee7f2ee8ea2da537398f270b76

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=d102a00f0e9fc371aa0f5b0a48f0386e20417889** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/27#pullrequestreview-5837377280)

- _Must fix_
  1. `docs/research/coastal-inputs-qa-documents.md:102`: the label of the cover-sheet review box still quotes the client company's abbreviation (the 2-letter token before "OFFICE USE ONLY"). This is the same class as round-1 must-fix 5 (client company names and abbreviations). Next to "mining client" (:3) it names the client directly. **Fixed:** `*Document Review Status* **CLIENT OFFICE USE ONLY** (red-bordered box)` or `<client> OFFICE USE ONLY`.
  2. `docs/research/coastal-inputs-registers-and-cui.md:382`: the business-key prefixes of the client-A and client-C issue-type copies are the real prefixes from the client-variant configuration. Lines 334-382 were neutralised to client A/B/C in round 1, but these prefixes were left in place. Prefixes like these are often derived from the client name. **Fixed:** synthetic prefixes, e.g. `(CA, CC)` or `(<prefix A>, <prefix C>)`.

- _Suggestions (non-blocking; none of these identifies the client on its own)_
  - `coastal-inputs-qa-documents.md:90`: "client logo (left, red block)" describes the client's branding. Together with must-fix 1 it narrows the client down. Consider "client logo (left)".
  - `coastal-inputs-qa-documents.md:318, 438`: these two section headings are still source file names. Round 1 changed the other headings to generic descriptions. For consistency: "Coating inspection report (completed sample)" and "Contractor Data Requirements List".
  - `coastal-inputs-qa-documents.md:603`: `<client spec C1> Rev 8` keeps a real revision number. :65 uses `Rev <n>`.
  - `coastal-inputs-qa-documents.md:374`: the secondary-location example looks copied from a report. A synthetic `component – zone – level – side` example would be safer.
  - `coastal-inputs-qa-documents.md:25`: the fixed letter segments in the weld-report template-control shape could become `<form code>`.
  - `docs/spec/modules/planner-and-work-plans.md:24`: if the first milestone acronym in "(… start, junction start, loop start)" is a plant or area code, replace it with a generic term ("isolation start").
  - `coastal-inputs-registers-and-cui.md:566-569`: the full delay-code list (numbers and labels) is still there, although the :590 example was made synthetic. Fine if it is KAEFER's own TM catalogue. If it comes from a client list, show the groups only.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=d102a00f0e9fc371aa0f5b0a48f0386e20417889

---

## PR #26 — plan: P6 import spec + ADR 0005 field-checkout merge (TD-075..TD-078)
task `T-0133` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/26)

**TODOs raised**

- TD-075 — decision: accept ADR 0005 (before T-0126 / M1 freeze)
- TD-076 — spec-gap: meaning of RSW code suffix (`-16`, `-21`) and Online/Shut variants
- TD-077 — decision: P6 supply formats (XER / XML / Excel / API)
- TD-078 — decision: RSW PDF processing location (self-hosted / hybrid / cloud)

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=74becdc7ed62d42a4d60a4a9cfdfe47eb5c55387** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/26#pullrequestreview-5836788049)

- _Must fix_
  1. `tracker/tasks/T-0126.md:1-10` (front matter) and `tracker/tasks/T-0133.md:1-11`: TD-075 is not on either task as a `blocked_by` reference. `docs/TASKS.md:83` shows T-0126 as **▶ ready to start**, and its goal and title still say "LWW with conflict log". An implementer could pick it up today and build record-level LWW, which ADR 0005 replaces. TD-075 itself says "Decide before T-0126". T-0133's goal says "TD-075 must be accepted first", but the pipeline only reads `blocked_by`. **Fixed =** `blocked_by: [TD-075]` on T-0126 and T-0133 (as T-0115 does with TD-045), `tasks: [T-0126, T-0133]` on TD-075, and boards re-rendered so T-0126 no longer shows as ready.

- _Suggestions (non-blocking)_
  - **ADR 0004 alignment.** ADR 0004 puts every rule set in one store by `kind`, and only the `autolink` kind may use `regex`. Spec §5.1 (name-grammar regex) and §5.3 (eligibility CEL) keep their rules in a separate "mapping profile". State whether these are new rule-store kinds or profile config, or raise a `decision` TODO linked to TD-072.
  - **`run_checks`.** The spec defines verifiable transitions (`oe_p6_import` previewed→committed, `oe_p6_anchor_link` proposed→confirmed→approved) but never says they call `run_checks(ctx, rule_set)`. Name the rule sets (T-0125 `schedule.p6_import`, a link-approval set) in §3/§4.
  - **`schedule_link` vs `work_anchor`.** The header says activities link to the level with the `schedule_link` role. §5.1/§5.4 create the link on the work-anchor node. Say what happens when the two roles sit on different levels (e.g. remediation-fep, where Activity sits under RSW).
  - **Envelope shape.** ADR 0005 should also say it supersedes the §1.3 envelope (`client_mutation_id` + whole `payload`) with `client_op_id` + `changes[]`/`collection_ops[]`.
  - **TD-035.** Add a dated note pointing to TD-075/ADR 0005 so its "base_revision check" decision is not read as record-level reject.
  - **Real-data hygiene.** §2 and TD-076 quote literal RSW codes (`918.1-16`, `920.1-21`, …) taken from the client XER. They are low-sensitivity, but replacing them with synthetic look-alikes (e.g. `101`, `101.1-16`, `102 Online`) would keep the shape and remove all XER content.
  - **TD-078** is about RSW PDF processing but is linked to T-0104. Linking it to E-05-09 would be more accurate,

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=3dc3a2ab05efd313be452e6897e3a3e11b96674a** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/26#pullrequestreview-5836801861)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `tracker/tasks/T-0104.md` notes still say "Open: TD-076, TD-078". TD-078 now belongs to E-05-09, and the new TD-079 (linked to T-0104) is not mentioned. Update the note on the next touch.
  - Consider `blocked_by: [TD-079]` on T-0104 (or T-0125) if the rule-store decision has to land before the mapping profile is built. Leaving it open is acceptable while ADR 0004 is still proposed.
  KERP-REVIEW: APPROVED patch-id=3dc3a2ab05efd313be452e6897e3a3e11b96674a

---

## PR #25 — plan: handover module (E-04-11) + ADR 0004 project rule-set defaults
task `T-0019` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/25)

**TODOs raised**

- TD-072 — decision: accept ADR 0004 (before T-0018 starts)
- TD-073 — decision: what counts as the recipient's "first shift"
- TD-074 — deferred: offline handover drafting via Dexie outbox

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=7e1340b08657b93447660d72b3d95ac59d2f1bd7** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/25#pullrequestreview-5836694296)

- _Must fix_
  1. **`docs/spec/modules/handover.md:16`**: the module is placed in a **"Field reports"** nav group. That group does not exist. The binding IA in `KERP-CONSOLIDATION-AND-DIFF.md` §6 (precedence 1) has exactly 8 groups: Logistics, Execution, Defense & Claims, Controls & Insights, Backbone, Overview, Documents, Admin. "Field reports" only appears in the archived legacy `fieldreports` module. **Fixed:** place handover in an existing group (Execution, where the Daily Workface Log / `field_execution` lives). Alternatively, if a new group is really wanted, raise a `spec-gap` TODO. Do not invent one in a precedence-3 doc.
  2. **`docs/decisions/0004-project-rule-sets-with-defaults.md:43-44` and `:71-72`**: the ADR gives authority to a **"tenant admin"**. That role edits tenant defaults and is the only actor who can unlock a locked mandatory gate, such as the calibration hard gate or the Cat A punch / NCR completion block. No tenant-admin persona exists in the 15-persona matrix (`KERP-EDGE-SCREENS-AND-SPEC-UPDATE.md` §5). PER-09 is Single Project scope and SoD-blocked from QA approval. This is the authz rule for bypassing mandatory gates, so it cannot stay undefined. **Fixed:** either name the existing persona(s) for the tenant layer in the ADR, for example PER-13 Quality Director for `check` rules and PER-01 for other kinds, with SoD stated; or keep it open but record it explicitly. To record it, add it as an open question in TD-072's body, or raise a `spec-gap` TODO via `tracker.py new-todo` and reference it from the ADR §5/§7.

- _Suggestions (non-blocking)_
  - `handover.md:130`: `(?<code>...)` is JS named-group syntax; Python `re` needs `(?P<code>...)`. Also, the pattern `\d+(?:\.\d+)?` does not match `RSW 16-900` as the text says: it would link `RSW 16`. Fix the example or the claim.
  - ADR 0004 §6: Python `re` has no timeout. Project admins write regexes that run on the server, so consider specifying RE2 (`google-re2`) or a linear-time engine to rule out ReDoS, rather than relying on a "timeout limit".
  - ADR 0004 §1: `project_id` and `deleted_at` are already provided by `BaseRecordMixin` (`backend/app/core/db.py:136,143`). Note that rather than listing them as rule-store columns. Also say whether the `oe_rules_*` prefix in module `validation` is intended.
  - `handover.md` §8: PER-09 (Users / Settings / Config Packs) reading **all** handover content is a broad grant for person-to-person notes. Consider limiting PER-09 to register metadata.
  - E-04-11 `depends_on` could also list T-0006, T-0015 and T-0025, which are named in spec §12. They are likely transitive, so this is optional.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=7e1340b08657b93447660d72b3d95ac59d2f1bd7

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=b123f5f0f77d5eb0f32f8847bb71d64c53af6c8e** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/25#pullrequestreview-5836706620)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - ADR 0004 §6: the client side runs JS regex. JS does not accept `(?P<name>)` or inline `(?i)`, so the "RE2-compatible subset checked on save" needs a translation step. Simpler options are server-side matching via the debounced resolver call, or storing the case-insensitive flag separately. The breakdown task can settle this.
  - `handover.md:16-17`: the reflowed line is longer than the rest of the file (cosmetic).
  KERP-REVIEW: APPROVED patch-id=b123f5f0f77d5eb0f32f8847bb71d64c53af6c8e

---

## PR #21 — plan: TODOs TD-061..TD-070 from legacy inspection template review
task `T-0110` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/21)

**TODOs raised**

- TD-061 — decision: legacy "items" vs KERP anchors; where personnel/material/equipment registers live (T-0110, T-0111)
- TD-062 — spec-gap: template check layouts (list / form / grid) and check-level settings (T-0110)
- TD-063 — spec-gap: hold / witness / review points as first-class signed ITP steps (T-0113)
- TD-064 — spec-gap: lookup fields bind to typed item + attribute with snapshot at inspection time (T-0110)
- TD-065 — spec-gap: CEL rule severity (block vs warn) and parameterised acceptance limits tied to spec/TQ (T-0110)
- TD-066 — spec-gap: issue types (defect / observation) as versioned configuration with close-out form (T-0111)
- TD-067 — spec-gap: inspection report layout configuration (T-0113)
- TD-068 — later: importer for legacy exports, with known data clean-ups (T-0113)
- TD-069 — risk: legacy exports contain real client data; synthetic fixtures only (T-0113)
- TD-070 — decision: segregation of duties — inspector self-approval/deletion (T-0111)

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=ac693a954f11be8164618d732ba34cd7bb392aeb** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/21#pullrequestreview-5836217366)

- _Must fix_
  1. `tracker/todo/TD-069.md:11`: the text names the real client ("name the client (RTIO)"). CLAUDE.md §6 forbids real client data in the repo, and the TODO does not need the name to state the risk. What fixed looks like: replace it with a neutral phrase such as "name the client". The commit is already pushed to a public PR branch, so amend the commit and force-push (or rewrite the branch) instead of adding a follow-up commit. A squash merge alone would keep it off `dev`, but the name would stay visible in the PR's commit history. Then re-render the boards (title unchanged, so no board diff is expected) and re-request review.

- _Suggestions (non-blocking)_
  - TD-063: the spec already covers hold/witness/review points evaluated via `run_checks` (`docs/spec/modules/quality-and-inspection.md` §construction_control). The real gap is the point type taxonomy (H/W/R/M), signer role(s) per point, and sign-off records. Consider adding T-0114 (construction_control) and T-0119 (signing) to `tasks:`, since those are the tasks that will implement it.
  - TD-061: T-0118 (credentials register + expiry) is the obvious home for the personnel VOC/qualifications question. Consider linking it.
  - TD-062/063/066/068: legacy template codes (CICPR, CIPWR, FBTQ) and business-key prefixes (BI, BQ, CIND, CINO) do not name the client, but they add nothing to the requirement. Consider generalising them ("concrete pour ITR", "bolt-tensioning template") to keep the public tracker free of client-specific identifiers.
  - TD-065: say whether the literal limits (slump 55-105 mm, etc.) come from a client specification. If they do, describe them generically.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=ac693a954f11be8164618d732ba34cd7bb392aeb

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=ff85df5879c8a746a5d366485fc7a526a41ea25a** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/21#pullrequestreview-5836327548)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - The old commit 819cf31 is no longer on any branch. GitHub may still serve it by SHA (PR timeline / force-push event) until it is garbage-collected. If the client name must be fully purged from a public repo, ask GitHub Support to remove cached views / unreferenced objects for 819cf31.
  KERP-REVIEW: APPROVED patch-id=ff85df5879c8a746a5d366485fc7a526a41ea25a

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=bf9623d4b9c93f1f048e6cd2769858835c67691c** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/21#pullrequestreview-5836380977)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - The round-2 suggestion still applies: if 819cf31 must be fully purged from the public repo, raise it with GitHub Support.
  KERP-REVIEW: APPROVED patch-id=bf9623d4b9c93f1f048e6cd2769858835c67691c

---

## PR #23 — plan: ADR 0003 — configurable breakdown, anchor roles and client terminology (TD-071)
task `T-0108` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/23)

**TODOs raised**

- TD-071: accept ADR 0003 before T-0101 starts and before the M1 freeze (T-0131).

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=8dab0c54231306eae1c1d93f369bfa6fea1fc32f** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/23#pullrequestreview-5836358476)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  None.
  KERP-REVIEW: APPROVED patch-id=8dab0c54231306eae1c1d93f369bfa6fea1fc32f

---

## PR #20 — T-0006: Hash-chain audit ledger: append-only (seq, chain_key, prev_hash, content_hash, payload) + REVOKE UPDATE/DELETE
task `T-0006` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/20)

**TODOs raised**

- TD-057 — spec-gap: chain_key semantics / hash / serialisation / verification are implementer defaults (documented in README)
- TD-058 — later: anchor chain heads outside the DB (truncation of newest entries / superuser rewrite)
- TD-059 — later: production runtime role is not the table owner and gets only SELECT+INSERT; startup guard check
- TD-060 — later: freeze migration DDL instead of importing live helpers (project-wide decision)

Size note: ~1185 added lines (~490 code+migration, ~550 tests, ~130 docs) — over the M guide.

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=d6dc33b7a386fd561e56c10131749b8d456cb0ca** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/20#pullrequestreview-5835896679)

- _Must fix_
  1. `backend/app/core/audit/models.py:29` (`UniqueConstraint("tenant_id", "chain_key", "seq")`) together with `service.py:88-92`. **Problem:** a chain key is unique per tenant, but visibility is per project. Any project session can claim a chain key that belongs to another project or to the tenant level. The other scope's RLS hides that row, so its own appends then fail with `IntegrityError` on seq 1. Because the ledger is append-only, nobody can ever clean this up, so the other scope's audit chain is blocked for good. Well-known keys such as the README's `library:checklists` are the obvious target. It is also an existence oracle across projects, which goes against T-0007's rule that cross-scope access looks like "not found". `test_ledger_is_project_scoped_by_rls` asserts this behaviour as intended, and no TODO tracks the risk. **Fixed looks like** either:
     - (a) Make the chain scope part of the identity. For example, make uniqueness `(tenant_id, coalesce(project_id, <nil uuid>), chain_key, seq)`, or `UNIQUE NULLS NOT DISTINCT` on PG16 with an equivalent on SQLite, and include `project_id` in the lock key. Then two scopes get two independent chains, and the test asserts that project B's append to the same key starts its own chain at seq 1. Or
     - (b) Keep the design but raise a `risk` TODO with `tracker.py new-todo --kind risk --task T-0006`, covering the permanent squatting of a chain and the existence oracle. Also document it in `app/core/audit/README.md` §Decisions, including a rule for callers that chain keys must embed a scope-unique id, and re-render the boards.

- _Suggestions (non-blocking)_
  - **Owner bypass (TD-058, TD-059):** TD-058 says a "superuser" can truncate or rewrite. Today the table *owner* is enough: `kerp_app` in CI, and whatever role runs the migrations in prod. It can `ALTER TABLE ... DISABLE TRIGGER` plus `GRANT UPDATE`, `DROP TABLE`, or `NO FORCE ROW LEVEL SECURITY`. Suggest changing the wording to "table owner or superuser". TD-059 should also require that the runtime role is **not** the owner, not just that it has SELECT and INSERT.
  - **Frozen migration DDL:** `migrations/versions/0002_audit_ledger.py:17-19` imports live app code (`ledger_guard_sql`, `rls_policy_sql`), so a later edit to those helpers silently changes what a historical migration emits. 0001 imports nothing from `app`. Consider inlining the DDL strings in 0002, keeping `guard.py` only for `ledger_guard_problems`, with a test that the two match.
  - **Database CHECKs for unhashed columns:** add `CHECK (deleted_at IS NULL)` and possibly `CHECK (revision = 1)`. Then a raw-SQL INSERT with INSERT-only rights cannot create an entry that is born deleted or carries unhashed state. `custom_fields` is not hashed either, so consider `CHECK (custom_fields = '{}')`, or document that it never carries audit content.
  - **Concurrency test:** it would be stronger if it also showed the backstop. For example, append without the lock (monkeypatch `_lock_chain` to a no-op) and assert that the losers get `IntegrityError` and the chain does not fork.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=d6dc33b7a386fd561e56c10131749b8d456cb0ca

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=fd6da54cc025b8d7a23808c8e3471ac6e1ea390b** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/20#pullrequestreview-5836002526)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - The PR body is stale. It still says `UNIQUE (tenant_id, chain_key, seq)`, shows acceptance criteria 1 and 3 unticked, and does not list TD-060. Refresh it before merging so the squash message is accurate.
  - Optional: add an index on `(tenant_id, scope_id, chain_key)`. The unique constraint already serves the lookups, so this is only worth doing if a query plan ever needs it.
  KERP-REVIEW: APPROVED patch-id=fd6da54cc025b8d7a23808c8e3471ac6e1ea390b

---

## PR #19 — T-0007: Project-scoped RLS: project_id context, policy helper, cross-project→404, 4 RLS-bypass contract tests
task `T-0007` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/19)

**TODOs raised**

- TD-052 — local Postgres for PG-only tests (later)
- TD-053 — tenant-wide cross-project reads / tenant-level row visibility (decision)
- TD-054 — spec policy `TO kerp_app_role` vs all roles (spec-gap)
- TD-055 — data migrations under FORCE RLS see no rows (later)
- TD-033 resolved; TD-032 left open → T-0014

Size note: ~1190 changed lines (~430 app, ~600 tests) — over the M guide, mostly PostgreSQL contract tests.

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=d6ec7a754dcfbd75ff42307f657d8704b06a67a5** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/19#pullrequestreview-5835458922)

- _Must fix_
  1. **A project-bound session can write tenant-level rows, including demoting a project row to tenant-level, which leaks it to every other project.**
     - Where: `backend/app/core/tenancy/rls.py:77` (`WITH CHECK (VISIBLE_ROW_SQL)` admits `project_id IS NULL`) and `backend/app/core/tenancy/scope.py:94` (the guard skips `obj.project_id is None`).
     - Repro: under `CTX_A`, `s.get_one(Widget, a1).project_id = None` commits. A `CTX_B` session then lists `['a1', 'b1', 'b2', 'b3', 't1-library']`. In the same way, an A-bound user (e.g. a PER-15 client rep) can create tenant-level rows that every project of the tenant sees.
     - Why it blocks: edge spec §6 `WITH CHECK (project_id = current_project)` is strict, and diff §4 requires "no cross-project leakage for external clients/subcontractors". This deviation is not recorded; TD-053 only covers *reads* of tenant-level rows. The same `FOR ALL USING` also lets a project session DELETE/UPDATE tenant-level rows.
     - Fixed looks like this. The write side requires the exact scope: `tenant_id = tenant AND project_id IS NOT DISTINCT FROM current_project`, so a project context writes only its project and a tenant-level context writes only tenant-level rows. Do this in RLS (e.g. separate SELECT and write policies, or USING for UPDATE/DELETE plus WITH CHECK both exact) and in `_guard_writes` (compare `obj.project_id != context.project_id` including `None`, for both new and dirty rows).
     - Tests: an ORM test for demotion and for creating a tenant-level row from `CTX_A` → `OutOfScopeError`. A PG test where raw-SQL INSERT/UPDATE to `project_id 

- _Suggestions (non-blocking)_
  - **TD-053 / TD-054 are fine as follow-ups.** TD-054: no `TO` clause (PUBLIC) is strictly tighter than `TO kerp_app_role`, and under FORCE a role with no applicable policy is default-denied anyway. TD-053 (read side): no tenant-scoped domain table exists yet (`app/modules/` is empty), so nothing leaks today. The decision belongs with the projects/persona model. Coordinator/planner: add TD-053 to T-0010 and T-0014 Notes (or `blocked_by`) so it cannot be skipped. The implementer may only edit T-0007.
  - ORM bulk `update()`/`delete()` via `session.execute` gets neither the loader criteria (`scope.py:49` handles `is_select` only) nor the flush guard. RLS covers it on PG, but on SQLite it is unscoped. Consider adding `is_update`/`is_delete` to the criteria filter, or document the limitation in the README table.
  - `test_worker_binds_the_job_context_not_the_enqueuers` (`tests/test_rls_bypass.py:86`): `threading.Thread` never inherits contextvars on 3.12, so `inherited_tenant is None` holds trivially, and binding lives on `session.info`, not contextvars. The meaningful assertions are the scoped reads. Consider an asyncio-task variant when the SAQ worker exists.
  - `test_migrated_schema_has_rls_on_every_model_table` passes vacuously today (no model tables carry `tenant_id`). That is fine as a forward guard, since `test_a_migration_that_forgets_rls_is_caught` proves the checker.
  - `rls_enforcement_problems` could also flag "runtime role owns RLS tables": an owner can `ALTER TABLE ... NO FORCE`. Consider calling it at startup on PostgreSQL.
  - The `noqa: E402` import at the bottom of `ap

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=5d159060a326064c4d12d38165f1cc5f561b1f18** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/19#pullrequestreview-5835577576)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - Declining the "runtime role owns RLS tables" flag is acceptable for CI, since FORCE plus `test_table_owner_running_a_migration_is_still_subject_to_rls` cover it. For production, ADR 0001 expects migrations to run as a separate owner role. Consider a `later` TODO with the deploy/infra task so the runtime role does not own tables: an owner can `ALTER TABLE ... NO FORCE`.
  - `test_get_or_404_rechecks_identity_map_hits` plants the stale context through `s.info[SESSION_INFO_KEY]`. That is fine as a white-box test of the defence-in-depth check.
  - TD-053 (read visibility of tenant-level rows, e.g. the project row for client-portal users) remains a hard pre-condition for T-0010 and T-0014, and is now noted on both.
  KERP-REVIEW: APPROVED patch-id=5d159060a326064c4d12d38165f1cc5f561b1f18

---

## PR #18 — T-0028: CI hardening: detect in ci-gate needs, least-privilege GHCR publish, SHA-pinned actions, base image digest + Renovate, drop empty postgres extra
task `T-0028` · merged 2026-11-10 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/18)

**TODOs raised**

- TD-047 — automate requirements lock refresh (deferred)
- TD-048 — pin actions in promote.yml / review-gate.yml and service images (later)

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=c3d0306ec80e0c7ad345938c1eb37b8665738ace** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/18#pullrequestreview-5835276726)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `scripts/ci_gate.py:39-40`: the "detect not green → want success" override is effectively dead code, because `detect` is already flagged by its own entry. `test_detect_failure_without_outputs_fails` would still pass with the override deleted. Either drop the override or add a test that asserts the specific downstream problems.
  - `backend-publish` rebuilds the image instead of pushing the smoke-tested artifact. Because of the digest pin, hashed locks and the `cache-from` of the same scope, it should be a full cache hit. A later hardening option is to export the tested image (`outputs: type=docker,dest=…` + artifact) or to push by digest from `backend-image` on push events. A `later` TODO would do.
  - The push path (`backend-publish` + gate on `push`) is first exercised after merge. Watch the first `dev` push run.
  - TD-047/TD-048 bodies are empty. A line of context helps triage.
  - Task status is still `in_progress`. The coordinator flips it at merge.
  KERP-REVIEW: APPROVED patch-id=c3d0306ec80e0c7ad345938c1eb37b8665738ace

---

## PR #17 — plan: TODO triage — user decisions 2026-11-09
task `T-0028` · merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/17)

**TODOs raised**

- TD-043, TD-044, TD-045, TD-046

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=40cde012e38e6a38ae28fe9c356ee5ba952cd1fd** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/17#pullrequestreview-5822765757)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `tracker/tasks/T-0015.md`: the note says ADR 0003 (TD-034) must exist before this starts, but only a note records it. Adding `blocked_by: [TD-034]` would make the pipeline's spec check enforce it. The same applies to TD-041 before T-0131, if you want it enforced.
  - The `tasks:` front-matter on TD-013 (`[]`), TD-020, TD-024, TD-028, TD-030, TD-031 and TD-037 still points at the original tasks and does not include T-0028/T-0029/T-0030/T-0031. Adding the new task ids would make the board link them.
  - TD-022 resolution: the "trust an upstream proxy request-id header" question is closed implicitly by "defaults accepted". Log shipping is deferred to deploy, which TD-023/TD-046 partly cover. Consider one sentence saying so explicitly.
  - T-0028 bundles six concerns into one S task. It should still be under 300 lines, but split off the Renovate/digest item if it grows.
  KERP-REVIEW: APPROVED patch-id=40cde012e38e6a38ae28fe9c356ee5ba952cd1fd

---

## PR #16 — chore(tracker): remote-aware TODO id allocation + check-ids collision gate
merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/16)

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=a8a14ec80ab58dd655b94549fec078bbff79bca5** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/16#pullrequestreview-5822022190)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - The PR body's "Open collision: PR #15" section is stale. #15 has since merged into `dev` (4b3856a) with its TODOs at TD-040..042, so nothing is pending on #15. Update the body before squash-merging so the commit message doesn't carry outdated guidance.
  - .github/workflows/ci.yml:44-48: pass `github.base_ref` and `github.event.pull_request.head.sha` through `env:` and quote them as `"$BASE_REF"`. Interpolating expressions directly into `run:` is the pattern GitHub's hardening guide warns against. The risk is low here because the base is a maintainer-controlled branch, so this is optional.
  - tracker.py:403: `gh pr list --json files` returns at most about 100 files per PR (GraphQL page). A very large PR could hide a TD file. That's acceptable given the `check-ids` backstop, but it could be noted in the docstring.
  - `check-ids` compares only against the base. A collision between two still-open PRs only shows up after one merges, either as a git add/add conflict or when the other PR's CI re-runs. The "Known limit" section already covers this, and it could be mentioned in WORKFLOW.md as well.
  KERP-REVIEW: APPROVED patch-id=a8a14ec80ab58dd655b94549fec078bbff79bca5

---

## PR #15 — T-0017: CEL runtime substrate: celpy (server) + JS CEL (client) + shared conformance_fixtures.json harness
task `T-0017` · merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/15)

**TODOs raised**

- TD-032 (deferred) — close or accept the 18 parity gaps before T-0131 (celpy mixed int/double, `== null`, NaN, string indexing, `string(bool)`, error-in-macro codes; cel-js `uint(-1)`, intermediate int64 overflow, `has()` on map literal, int-keyed maps)
- TD-033 (spec-gap) — spec §2.1 hold-gating example uses non-standard `count(list)`
- TD-034 (decision) — typed JSON value encoding for CEL contexts needs sign-off for sync ingest (T-0126) and the Designer

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=9fde0f05ca1761ba60b727506eaf285a3d106754** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/15#pullrequestreview-5821648151)

- _Must fix_
  1. **Merge conflict / TODO id collision.** `tracker/todo/TD-032.md`, `TD-033.md`, `TD-034.md` are add/add conflicts. On `dev` those ids already belong to other items: tenant_id FK, soft-delete filtering, Inspection-ERP port. `backend/pyproject.toml`, `backend/requirements.lock`, `docs/TASKS.md` and `docs/TODO.md` also conflict.
     Fixed means:
     - Rebase onto `dev`.
     - Re-create the three TODOs with `tracker.py new-todo`, which gives the next free ids (TD-038+).
     - Update every reference: the `known_gap.todo` values in `tests/contract/conformance_fixtures.json:244-261`, `tests/contract/README.md`, `backend/app/core/cel/README.md:26,35,37`, `frontend/src/lib/cel/README.md`, `tracker/tasks/T-0017.md` Notes.
     - Regenerate `requirements.lock` from the merged `pyproject.toml` and re-render the boards.
     - Get CI green on the PR, **including `backend-image`**.
     Note that the well-formedness test only checks that `tracker/todo/<id>.md` exists. After a naive merge it would pass while pointing at an unrelated TODO.
  2. **Server fail-open gaps.** The server is the hard gate (EDGE-SCREENS §2, "Hard Server-Side Gating"). Pinning these gaps is honest, but several of them make the server return a value where CEL says error, or return a wrong value. Each one can turn a blocking rule into a passing one:
     - `gap.int_div_double` (fixtures `:248`): `1 / 2.0` gives int `0`. So `fields.x >= 10 / fields.y` compares against a truncated int, and the double-left ordering then succeeds.
     - `gap.nan` / `gap.negative_infinity` (`:249-250`): `0.0/0.0` and `-1.0/0.0` both give `+Infinity`.

- _Should fix (non-blocking)_
  - **Divergences with no fixture.** I found these by probing:
    - `has(a)` on a top-level variable: server `false`, client `PARSE_ERROR`.
    - `[1,2].all(x)`: server `INVALID_ARGUMENT`, client `NO_MATCHING_OVERLOAD` (spec: parse error).
    - `fields.r.filter(v, 1)`: server returns the list, client `EVAL_ERROR`.
    Add fixtures for them (pinned gaps, or fixed if you're already overriding functions).
  - **Timestamp decoding differs.** The `$timestamp` regex is case-insensitive on both sides (`values.py:25-26`, `values.ts:21`). For `2024-01-01t00:00:00.123z` the client accepts the value but the server's `datetime.fromisoformat` raises `ValueError`, so ingest would crash on a context the client accepted. Drop the `i` / `IGNORECASE` flags, or normalise to upper case before parsing.
  - **Fail-closed contract for T-0018.** Record it in the CEL TD and the `app/core/cel` README: a gate passes only on `CelOk(value=True)` (`is True`), and every `CelError` or non-bool result blocks. Consider adding T-0018 to the TD's `tasks`.
  - **Expression length cap.** Programs are cached by expression text, with no length limit. Consider capping the expression length (e.g. 4 KiB) on both sides, so 1024 cache entries can't pin arbitrarily large strings.
  - **Error classification depends on celpy message text.** `errors.py` matches the message prefix `undeclared reference to '` and the messages `does not support field selection` and `match error`. It is safe: an unknown error falls back to `EVAL_ERROR` / `NO_MATCHING_OVERLOAD` / `NO_SUCH_KEY` and never raises, and the `<0.6` pin plus the fixtures would catc

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=6b23d14c9c65aefac3dc00eb069a0aedc7a68910** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/15#pullrequestreview-5821854481)

- _Must fix_
  1. **`filter()` / `exists_one()` with a non-bool predicate still fails open on the server.** Fixture `gap.filter_non_bool_predicate`; `backend/app/core/cel/runtime.py` `_environment()`.
     - celpy uses Python truthiness here. `fields.r.filter(v, v)` with `[1.0, 0.0]` gives `[1.0]`, and `fields.r.exists_one(v, v)` gives `true`. The client gives `NO_MATCHING_OVERLOAD` for both.
     - The realistic trigger is a null or unset flag on a list element: `test_pack.punches.filter(p, p.cat_a_open).size() == 0` passes on the server when `cat_a_open` is `null`. That is exactly the §2.1 hold-gating shape.
     - TD-041 says macros are hard-wired and can't be fixed. They can, without patching celpy. `celpy.Environment(runner_class=...)` accepts an `InterpretedRunner` subclass, and its `evaluate()` can build an `Evaluator` subclass that overrides `build_macro_eval(child)`. When `str(child.children[1])` is `filter` or `exists_one`, the override wraps the returned function: a bool or `CELEvalError` passes through, anything else raises `TypeError` (which the wrapper maps to NO_MATCHING_OVERLOAD).
     - I checked this locally. `r.filter(v, v)` and `r.exists_one(v, v)` then give TypeError, while `filter(v, v > 0.5)`, `exists_one(v, v > 0.5)` and `map(v, v * 2.0)` are unchanged. It is about 20 lines.
     Fixed means: the server enforces a bool predicate for `filter` and `exists_one`, and `gap.filter_non_bool_predicate` becomes a parity fixture. Add parity fixtures for `exists_one` and for a `null` predicate value (e.g. `fields.p.filter(p, p.open)` with `open: null`).
  2. **`string()` still returns a

- _Suggestions (non-blocking)_
  - **`type(string)` returns the helper metaclass.** Because of the `string` trick, `type(string)` is `_SameAsStringType`, so `type(type('a')) == type(string)` is `false` on the server and `true` on the client. This is exotic. Pin it as a gap in TD-041, or make the metaclass compare equal to celpy's type-of-type.
  - **`string(1e20)` differs.** The server gives `'1e+20'` and the client gives `'100000000000000000000'`. Add it as a pinned gap.
  - **`{1: 2.0}[1u]` differs, but fails closed.** The server gives `NO_MATCHING_OVERLOAD` and the client gives `2.0`. Add a pinned gap.
  - **Size.** About 1,500 changed lines without fixtures and locks. It is still one coherent unit, so I accept it as before.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=6b23d14c9c65aefac3dc00eb069a0aedc7a68910

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=7be7f1d9e51474c11f7b8c2ec969604caa6a65a8** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/15#pullrequestreview-5821969685)

- _Must fix_
  None.

- _Suggestions (non-blocking; please add to TD-041 so they aren't lost)_
  - **`string(double)` is not pinned where the server deviates.** The server uses Python `str()`, so `string(2.0)` gives `"2.0"`, `string(-0.0)` gives `"-0.0"` and `string(123456789.0)` gives `"123456789.0"`. CEL (cel-go `'g'` formatting) and the client both give `"2"`, `"0"` and `"123456789"`. Pin `gap.string_of_whole_double` (python), or format with `'%.17g'`-style shortest repr minus the trailing `.0`. Gating rules don't compare stringified doubles, so this doesn't block.
  - **The server's `string(timestamp)` and `string(duration)` drop sub-second precision.** `string(timestamp('…00.5Z'))` gives `"…00Z"` and `string(duration('1.5s'))` gives `"1s"`. The pinned client gaps use whole-second inputs, so this server defect is unpinned. Either reject both on the server (matching the client), or format the fractional part.
  - **Error-code-only divergences (both runtimes fail closed):**
    - `[1,2].all(x, x / 0 > 1 || false)`: server NO_MATCHING_OVERLOAD, client DIVIDE_BY_ZERO.
    - Ternary with a non-bool branch inside `all`: server NO_MATCHING_OVERLOAD, client EVAL_ERROR.
    - `[1,2].all(x, x > 1 ? 'a' : false)`: server `false`, which is CEL-correct because false absorbs the error; the client gives EVAL_ERROR.
    - 3-argument `map()`: server INVALID_ARGUMENT, client returns a value.
    They're worth a line in TD-041 before T-0131.
  KERP-REVIEW: APPROVED patch-id=7be7f1d9e51474c11f7b8c2ec969604caa6a65a8

---

## PR #14 — plan: TD-002 one-engine — port/rewrite verdicts per component (ADR 0002)
task `T-0102` · merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/14)

**TODOs raised**

- **TD-039** (decision, non-blocking): should any proposal extras that the spec consolidation dropped come back? These are write-back, type inheritance, migration preview, test-fill, the section library, report slots, `auto_create_itp`, competency assignment and programmes. Most are unbuilt in AIP too. **Default: out of scope for Phase 1.**

- **TD-038** (later): add reportlab/openpyxl to the heavy-lib lazy-import lint when Inspection-ERP `core/reporting` is ported (T-0115/T-0119).

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=47a5be45439bf35165f5a08dbe90380ad9657ec1** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/14#pullrequestreview-5821471995)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `tracker/todo/TD-002.md:19`: the date prefix is doubled ("2026-11-09: 2026-11-09:"). `new-note` already adds the date, so drop the manual prefix.
  - `docs/decisions/0002-port-one-engine.md`: some "Lands in" cells name tasks that are missing from **Affects** and have no notes: T-0116 and T-0117 (IERP findings), T-0122 and T-0124 (`gates.py` catalogue) and T-0127 (offline outbox). The Consequences section says "each affected task's notes name its source files". Either add one-line pointers to those tasks or add them to Affects. Smaller gaps of the same kind:
    - The T-0110 note omits `form-renderer/logic/types.ts`.
    - The T-0111 note omits the AIP/IERP `inspections/*` reference rows.
    - The T-0113 note omits the IERP `studio/FormBuilder` reference.
    - The T-0114 note omits the IERP `modules/itp` reference.
  - ADR 0002, row `core/reporting`: this brings in **reportlab**, which no spec doc names (the spec PDF tooling is PyMuPDF). reportlab is also not in the T-0022 heavy-lib list (`backend/pyproject.toml` ~L39-50), so "fits the lazy-import rule" is not enforced. Consider a `later` TODO to add `reportlab` (and `openpyxl`, if the Excel renderer comes along) to that list when T-0115/T-0119 port it.
  - ADR 0002 cites "ADR 0001", which is not on `dev` yet (the PR body explains this). Land ADR 0001 soon so the reference resolves. DEVELOPMENT-PLAN still lists TD-001 as open, which is consistent for now.
  - `tracker/todo/TD-032.md`: `source: planner`, while other planner TODOs use `kerp-planner`. This is cosmetic, and validate accepts it.
  KERP-REVIEW: APPROVED patch-id=47a5be45439bf3

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=1dfe976fb7c1b1d939e0eb499745d5e1241d5730** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/14#pullrequestreview-5821524756)

- _Must fix_
  1. **TD-035 collides with open PR #13** (`task/T-0005-db-layer`, head 33e524d). That branch adds `tracker/todo/TD-035.md` ("Optimistic locking on BaseRecordMixin.revision", kind `later`, source reviewer). It also already uses TD-032, TD-033 and TD-034. Whichever PR merges second will get an add/add conflict on `tracker/todo/TD-035.md`. If someone resolves that conflict by keeping one file, the references to TD-035 in this PR point at the wrong TODO. The affected lines are `tracker/todo/TD-035.md:2`, `docs/decisions/0002-port-one-engine.md:109`, `docs/DEVELOPMENT-PLAN.md:75` and `tracker/todo/TD-002.md:19`.
     **Fixed looks like:** Rename the dropped-extras decision to an ID no branch uses. TD-037 is free on every remote branch as of this review, and TD-036 (this PR) is also unused elsewhere. Update every reference listed above, run `python scripts/tracker.py render` so `docs/TODO.md` is regenerated, and re-run validate and `render --check`.

- _Suggestions (non-blocking)_
  - TD-036 has `source: kerp-reviewer`. That is accurate because it came from round 1, but the planner wrote it. Either value is fine.
  - Process note, outside this PR: `claudeK/inspection-erp-core-decision-c55c37` (PR #12) adds TD-034, and PR #13 adds TD-034 too. Concurrent branches keep picking the same next TODO ID. Consider a reservation step in `tracker.py new-todo`, for example one that scans `origin/*` for used IDs, raised as a `later` TODO.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=1dfe976fb7c1b1d939e0eb499745d5e1241d5730

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=d4cc369eca06b0df4208ff4487d6696bcece8713** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/14#pullrequestreview-5821588753)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - The PR body is slightly stale. Its gate line says "32 todos" but the count is now 33 after TD-036. Its closing note says ADR 0001 is on an "unpushed branch", but it is open as #12.
  - #12 also edits `docs/DEVELOPMENT-PLAN.md` (line 75, the open-decisions list) and `docs/TODO.md`. Whichever PR merges second needs a rebase that merges both open-decision edits. It must also re-run `tracker.py render` rather than hand-resolve the board.
  KERP-REVIEW: APPROVED patch-id=d4cc369eca06b0df4208ff4487d6696bcece8713

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=ec61bc25108131aae526678ee41e0ae5c4157f72** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/14#pullrequestreview-5821618646)

- _Must fix_
  1. `tracker/todo/TD-036.md:2` and `tracker/todo/TD-037.md:2`: these IDs collide with PR #13. **Fixed looks like** renaming them to the next IDs that are free on dev and every open PR. Today those are **TD-038** (reportlab/openpyxl heavy-lib lint) and **TD-039** (dropped AIP-proposal extras), because PR #13 uses 032/033/035/036/037 and PR #15 uses 032–034. Update the `id:` field and the filenames.
  2. Update every reference to match: `docs/decisions/0002-port-one-engine.md:77` (TD-036), `docs/decisions/0002-port-one-engine.md:109` (TD-037), `docs/DEVELOPMENT-PLAN.md:75` (TD-037), `tracker/todo/TD-002.md:19` (TD-037). Then run `python scripts/tracker.py render` and commit `docs/TODO.md`. Confirm with `git grep -E 'TD-03[67]'` that no stale references remain in this PR's files.
  (If the coordinator would rather have PR #13 renumber, it is already marked done, so moving these two TODOs here is the cheaper fix.)

- _Suggestions (non-blocking)_
  - This is outside this PR, but PR #13 and PR #15 both add `TD-032` and `TD-033`. The coordinator should renumber one of them before either merges.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=ec61bc25108131aae526678ee41e0ae5c4157f72

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=be136ab9ee101afc6e34c7c8a90fcd61349fb7df** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/14#pullrequestreview-5821666171)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - None new.
  KERP-REVIEW: APPROVED patch-id=be136ab9ee101afc6e34c7c8a90fcd61349fb7df

---

## PR #13 — T-0005: Database layer + BaseRecordMixin + Alembic + metadata contract test
task `T-0005` · merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/13)

**TODOs raised**

- TD-032: `tenant_id` FK to the tenants directory table (spec §1.1) once that table exists
- TD-033: default soft-delete filtering on ORM reads (opt-in to include deleted rows)

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=369e736f2533ab51615a476fd3f73f3f1f8c9b6a** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/13#pullrequestreview-5821473817)

- _Must fix_
  1. **`backend/app/core/db.py:279-303` (`Database.session` / `get_db`): the unit of work commits after the HTTP response has already been sent.** With the locked FastAPI 0.162.2, the code after `yield` in a default-scope yield dependency runs after the response. I checked this: a dependency that raises after `yield` still returns `200 {"ok":true}` to the client. The sessionmaker uses `autoflush=False`, so pending INSERTs and UPDATEs are flushed at `commit()`. That means IntegrityErrors, RLS `WITH CHECK` violations (T-0007) and serialization failures only surface there, so the client gets a success response for a write that was rolled back. **Fixed looks like:** the commit happens before the response. For example, export `DbSession = Annotated[Session, Depends(get_db, scope="function")]` (I checked that `scope="function"` turns the same failure into a 500 on 0.162), require it in place of bare `Depends(get_db)`, and raise the `fastapi` floor in `pyproject.toml` to a version that supports `scope=`. Add a test that drives a write through the dependency that fails at commit (e.g. a `Widget` without `tenant_id`) and asserts a 500 and that no row was written. The existing `test_get_db_dependency_yields_session` (`tests/test_db.py:225`) only runs `SELECT 1`, so it never exercises commit.
  2. **`backend/tests/test_migrations.py:71`: `pytest.skip(...)` without a TD id.** CLAUDE.md §4 does not allow this, and it actually fires in CI's PostgreSQL step (`SKIPPED [1] tests/test_migrations.py:71`). The skip isn't needed: the test sets `KERP_MIGRATION_DATABASE_URL` to a SQLite file, which t

- _Suggestions (non-blocking)_
  - `db.py:126,223`: the docstring calls `revision` an "optimistic-concurrency counter", but nothing compares it, so concurrent writers just both increment it. Consider SQLAlchemy `version_id_col` (via `__mapper_args__` on the mixin), which emits `WHERE revision = :old` and raises `StaleDataError`. Or reword the docstring and leave a `later` TODO for the sync/conflict tasks.
  - `pyproject.toml:73`: the `postgres` marker is declared but never used. Remove it or use it.
  - `pyproject.toml:12`: `alembic.ini` uses `path_separator = os`, which is Alembic ≥ 1.16. Raise the floor from `>=1.14`.
  - CI's PG step runs migrations as `kerp_app` (it has no `KERP_MIGRATION_DATABASE_URL`, and `KERP_TEST_OWNER_URL` is unused), so the owner/runtime role split is not exercised yet. That is worth covering when T-0007 adds RLS.
  - Size: the T-0005-only diff is about 1,130 lines, including about 240 lines of the hash lock and about 460 lines of tests; production code is about 300 lines. That is acceptable given the composition, and does not block.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=369e736f2533ab51615a476fd3f73f3f1f8c9b6a

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=c5e0d11c08f7035230128ef9fcee78be986de532** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/13#pullrequestreview-5821522017)

- _Suggestions (non-blocking)_
  - Nothing yet stops a future endpoint from using bare `Depends(get_db)` (default scope) and bringing back the post-response commit. A small test or lint that greps `app/` for `Depends(get_db` outside `db.py` would lock this in. That fits T-0007 or the module loader task.
  - `postgres = []` in `pyproject.toml`: once CI's install line no longer references the extra, drop it.
  KERP-REVIEW: APPROVED patch-id=c5e0d11c08f7035230128ef9fcee78be986de532

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=efabcb5974617b6fb91c763bd13243c1979ab65e** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/13#pullrequestreview-5821616982)

- _Suggestions (non-blocking)_
  - The last T-0005.md note says "squash-merged to dev" before that has happened. It will be true once the coordinator merges, so leaving it is fine.
  KERP-REVIEW: APPROVED patch-id=efabcb5974617b6fb91c763bd13243c1979ab65e

---

## PR #12 — plan: resolve TD-001 — selective port of Inspection-ERP core (ADR 0001)
task `T-0005` · merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/12)

**TODOs raised**

- TD-034 — port/rewrite assessment of the remaining Inspection-ERP core packages (review round 1)

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=ab293c3cb3528aa225f8d48b5aec7bb6b2b40381** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/12#pullrequestreview-5821460101)

- _Must fix_
  1. `tracker/todo/TD-001.md:17` and the ADR decision table (`docs/decisions/0001-port-inspection-erp-core.md:34-50`): the note says "Scanned … backend/app/core. Per-component verdict in …", but `backend/app/core/` also has `events.py`, `jobs/`, `sync/`, `workflow/`, `forms/`, `schema_engine/`, `items/`, `files/`, `mapping/` and `reporting/`. None of them get a verdict. Several overlap KERP Phase 0/1 work: the ARQ worker vs IE's thread-pool job runner, the offline sync core, the forms one-engine, the workflow FSM and the event bus/outbox. Leaving them out is fine because TD-001's title only names six components. But the ADR currently reads as exhaustive, and CLAUDE.md §5 requires a TODO in the same PR for anything found but not done. **Fixed =** add an "Out of scope of this ADR" row or paragraph that lists those packages, and raise a `later` (or `decision`) TODO via `tracker.py new-todo` for a port/rewrite assessment of them. Name the obvious ones in it, e.g. `jobs/runner.py` is a thread pool, so it is not portable under the ARQ rule. Then re-render.
  2. `docs/decisions/0001-port-inspection-erp-core.md:6` vs `:44` / `:55`: the **Affects** line lists T-0005/6/7/8/11 plus "the `run_checks` shell task". It leaves out T-0009, and it names T-0018 only by description. The `deps.py` row says it lands in "T-0008 / T-0010", yet T-0010 has no note and is not in TD-001 `tasks:`. That contradicts the Consequences claim that "each later task's notes name its Inspection-ERP source file". **Fixed =** Affects lists exactly T-0005, T-0006, T-0007, T-0008, T-0009, T-0010, T-0011, T-0018. Also a

- _Suggestions (non-blocking)_
  - IE's `core/auth/security.py:1` docstring says "see ADR 0001", which is IE's own ADR. When the "Adapted from Inspection-ERP …" credit is applied (ADR :61), that reference will wrongly point at KERP's ADR 0001. Worth one sentence in Consequences saying to rewrite source ADR references as `Inspection-ERP ADR nnnn`.
  - The ADR says "rename the setting to `app.current_tenant_id`" (IE uses `app.tenant_id`). Consider mentioning that the `NULLIF(current_setting(..., true), '')` guard must wrap both the tenant and project expressions.
  - IE's `database.py` uses `modified_at`/`modified_by`/`revision`, while KERP's mixin names `updated_at`. Spell out the rename list in T-0005's note so the implementer doesn't keep both.
  - The PR body says "25 todos"; `validate` now reports 31. Refresh the counts.

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=d63406c3939588e276f517be2bbc35fd00d32ed4** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/12#pullrequestreview-5821479333)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - TD-034 has `source: reviewer`, but it was raised by the implementer in response to review. `implementer` would be more accurate.
  - Consider adding TD-034 to the "Open decisions" line in `docs/DEVELOPMENT-PLAN.md:74-75`, next to TD-003/004/013.
  - The TD-032/033 gap depends on stacked PR #13 merging. If #13 is dropped or renumbered, the gap is harmless (`validate` accepts it), but it's worth noting in the coordinator's merge order.

---

## PR #10 — T-0022: Lint rule: heavy libs (fitz, cv2, rectpack, celpy, pyhanko) lazily imported
task `T-0022` · merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/10)

**TODOs raised**

- TD-026 (later) — add each new heavy dependency (DXF/XER parsers, PDF compile libs, …) to the TID253 list in the PR that introduces it
- TD-027 (spec-gap) — CLAUDE.md §6 / dev plan say ARQ worker; consolidation §3 says "SAQ replaces ARQ"

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=1f5473c71c59d882bd0b922fa6a994dfee1abad8** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/10#pullrequestreview-5821320095)

- _Must fix_
  1. `backend/pyproject.toml:38-49` + `backend/tests/test_lint_heavy_imports.py`: heavy imports at module scope inside `try:`/`except`, a non-`TYPE_CHECKING` `if`, or a class body are not caught, so the goal "heavy libs imported lazily" is only partly enforced. **Fixed looks like:** add a small AST-based contract test that walks `backend/app/**/*.py`. It should report any `Import`/`ImportFrom` of a banned module (read from the same `banned-module-level-imports` list, submodules included) that is not inside a `FunctionDef`/`AsyncFunctionDef` and not inside an `if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` body. Add synthetic-sample tests proving it flags the `try`, plain-`if` and class-body forms and allows the function-level and `TYPE_CHECKING` forms. Keep TID253 for fast editor and CI feedback.
  2. `backend/README.md:62-63`, `backend/pyproject.toml:39-40`, `tracker/tasks/T-0022.md:29-31`: these say `ruff check .` "fails on a top-level import of any listed module" and imply the only exemptions are functions and `TYPE_CHECKING`. That is not accurate for TID253 alone. **Fixed looks like:** the wording explains that two checks share the job (TID253 for plain module-level imports, the AST test from item 1 for nested module-scope imports), and the task Notes record the TID253 limitation.

- _Suggestions (non-blocking)_
  - `importlib.import_module("fitz")` / `__import__` at module scope gets past both checks. That is fine to leave, but consider one line in the README.
  - `test_lazy_and_type_checking_heavy_import_passes_lint` asserts that *all* rules are silent on the lazy sample. If someone later selects `PLC0415` (import-outside-top-level), this test breaks for an unrelated reason. Filtering to TID253 would make the test's intent explicit.
  - Test robustness on Linux CI looks fine: it uses `sys.executable -m ruff`, an absolute `--config`, pytest tmp dirs, `check=True` so config errors surface, and nothing depends on the cwd.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=1f5473c71c59d882bd0b922fa6a994dfee1abad8

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=13c55302150f1a87708671797b1562411d14065a** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/10#pullrequestreview-5821358277)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - The AST check only scans `backend/app/`. If the SAQ/ARQ worker entry point ever lives outside `app/`, extend `APP_DIR` to cover it, or note this under TD-026.
  KERP-REVIEW: APPROVED patch-id=13c55302150f1a87708671797b1562411d14065a

---

## PR #11 — T-0004: Backend Dockerfile (multi-stage, --only-binary) + CI image build to GHCR
task `T-0004` · merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/11)

**TODOs raised**

- TD-028 (later) — pin base image by digest + automated refresh (Dependabot/Renovate) for image and locks
- TD-029 (later) — image is amd64-only (locks target x86_64-manylinux_2_28)
- TD-017 resolved — hashed lock files with sync tests
(Implementer raised these as TD-026/027; renumbered by the coordinator because PR 10 holds those numbers.)

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=6a2dcb207e9115ee2eb251b06f1b1197405a32fd** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/11#pullrequestreview-5821351383)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `.github/workflows/ci.yml:114` — `packages: write` is also granted on same-repo `pull_request` runs. Fork PRs are already read-only, and the token is not passed to the PR's image. Still, a separate `backend-image-publish` job (`if: github.event_name == 'push'`, the only job with `packages: write`) would give least privilege. Consider adding `persist-credentials: false` to checkout as well.
  - `.github/workflows/ci.yml:121,165,174` — this job holds a write token, so consider pinning `docker/*` actions to full commit SHAs. This fits under TD-028 (Dependabot/Renovate would keep the SHAs current).
  - `.github/workflows/ci.yml:168-175` — the push step rebuilds instead of pushing the exact image that was smoke-tested. With the GHA cache and hashed locks the result should be the same, but pushing the tested image (or building once with `push` after the smoke test using the same digest) would remove any doubt.
  - Pre-existing, not from this PR: `detect` is not in `ci-gate.needs`. If `detect` fails, every dependent job is skipped and `ci-gate` still passes. Worth a `bug`/`later` TODO.
  - `backend/tests/test_container.py:13-15` — the tests import `packaging`, but it is not declared in `[project.optional-dependencies].dev`. It is only present because pytest depends on it. Declare it explicitly.
  - `backend/tests/test_container.py` (`test_dockerignore_excludes`) — also assert that the first non-comment entry is `*`, so the file stays an allow-list.
  - `tracker/tasks/T-0004.md:22` — the GHCR-push criterion is ticked before it has been seen to happen (the PR body correctly leaves it unticked

---

## PR #9 — T-0021: Observability + security headers (request id, structured logs, CSP/HSTS)
task `T-0021` · merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/9)

**TODOs raised**

- TD-022 (spec-gap) — sign-off on security header values + log schema; HSTS preload; expose `/docs`/`/openapi.json` in production?; trust upstream proxy request id?
- TD-023 (later) — tracing/metrics (OpenTelemetry, Prometheus), propagate request id into ARQ jobs

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=ecde46bc844d9a92357b3d29d9daeac36a7591fa** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/9#pullrequestreview-5821182774)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `app/core/logging.py` `configure_logging`: set `logging.getLogger("httpx")` and `logging.getLogger("httpcore")` to WARNING. Otherwise outbound request URLs, which may carry tokens in the query string, get logged once integration adapters exist. If deferred, raise a `risk` TODO.
  - `app/core/request_context.py:47-55` and `backend/README.md`: state in code that, because the middleware handles everything before the response starts, an `app.add_exception_handler(Exception, …)` handler is never called. Alternatively, have the middleware call a pluggable error renderer. That way the future error-envelope task finds this.
  - `tests/conftest.py`: move the `_restore_logging` autouse fixture from `test_logging.py` into conftest, so the global logging changes made by every `create_app()` are undone after each test.
  - Once the app is served behind a proxy with `root_path`, `scope["path"]` will no longer match `app.docs_url` exactly. The docs pages would then get the strict CSP. That fails safe, but it would break Swagger UI. Worth noting under TD-022.
  - `JsonFormatter` uses `ensure_ascii=False`. On a Windows console with a non-UTF-8 code page, non-ASCII messages trigger handler errors. `ensure_ascii=True` avoids that, and log shippers read the escapes fine.
  - On client disconnect, `CancelledError` is not caught but the `finally` still logs `status=500`. Consider a distinct status such as 499 or a `disconnected` flag.
  KERP-REVIEW: APPROVED patch-id=ecde46bc844d9a92357b3d29d9daeac36a7591fa

---

## PR #8 — T-0002: Frontend scaffold: Vite + React 18 + TS strict, Tailwind, TanStack Query, Dexie, router, i18n, Vitest
task `T-0002` · merged 2026-11-09 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/8)

**TODOs raised**

- TD-018 (later) — wire `npm run gen:api` once the backend exposes an OpenAPI schema
- TD-019 (later) — PWA manifest + service worker
- TD-020 (later) — ESLint + frontend lint gate (changes CLAUDE.md §4 → needs sign-off)

(Renumbered from TD-016..018 by the coordinator during rebase — T-0001 had already taken TD-016/017.)

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=0fdf9e718e3524c3c8f74d8ff71d5fd9474a00bb** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/8#pullrequestreview-5821103911)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `frontend/src/db/db.ts:26`: the master §1.3 envelope calls the timestamp `timestamp_utc`, but this uses `created_at_utc`. That rename isn't in the task Notes. Consolidation §6 doesn't rename this field. `vector_clock` being left out is fine, since T-0126/T-0127 own it. Either align the name now, which is cheap while the table is unused and still on Dexie `version(1)`, or add a line to Notes so T-0126/T-0127 make the call. Otherwise renaming an indexed field later needs a Dexie upgrade function.
  - `frontend/src/db/db.ts:19`: `tenant_id` in the client envelope matches the spec. T-0126 must still take the tenant from the JWT on replay and treat this field as informational only (CLAUDE.md §6). Worth a line in T-0126 or the code comment.
  - `frontend/src/api/README.md`: many OpenAPI generators clear their output directory, so when TD-018 is done, either move the README or make sure the generator keeps it.
  - `frontend/index.html:2`: `lang="en"` is hard-coded. When a second locale arrives, sync `document.documentElement.lang` from i18n, the same way `Layout.tsx` sets `document.title`.
  - Size: about 600 non-lockfile lines, above the S guideline of about 300. That's acceptable for a scaffold and not worth splitting now.
  KERP-REVIEW: APPROVED patch-id=0fdf9e718e3524c3c8f74d8ff71d5fd9474a00bb

---

## PR #7 — T-0001: Backend scaffold: FastAPI app factory, settings, /health
task `T-0001` · merged 2026-11-08 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/7)

**TODOs raised**

- TD-016 (later) — move TestClient transport from httpx to httpx2 (Starlette deprecation warning)

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=40c0756b2437d2afa851c97becbb3efdd20a388c** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/7#pullrequestreview-5819747958)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `backend/app/core/config.py:37`: nothing stops `debug=True` together with `environment="production"`. Consider a `model_validator` that rejects that pair, either now or when auth lands.
  - `backend/pyproject.toml:11-26`: dependencies have lower bounds only and there is no lock file, which is why Starlette 1.x has already changed behaviour (TD-016). Consider a `later` TODO for a lock (`uv lock` or pinned constraints) before the Dockerfile task (T-0004) needs reproducible builds.
  - `backend/app/api/health.py:18`: `app_settings` is a general dependency. Move it to `app/core/deps.py` once a second router needs it.
  - `backend/app/api/health.py:25`: `async def` would skip the threadpool hop for a handler that does no I/O. This is a micro-optimisation only.
  KERP-REVIEW: APPROVED patch-id=40c0756b2437d2afa851c97becbb3efdd20a388c

---

## PR #6 — plan: check — Phase 2 spec-check
task `T-0201` · merged 2026-11-08 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/6)

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=b6c07318e4b0508ca295020ae346a5990cca131d** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/6#pullrequestreview-5819431190)

- _Must fix_
  _None._

- _Suggestions (non-blocking)_
  - Item anchor terminology drifts between `type=weld` (T-0203 goal, spec) and `kind=weld` (T-0203 acceptance, PR body). Align to whatever T-0103's Item model actually names the discriminator at implementation time.
  KERP-REVIEW: APPROVED patch-id=b6c07318e4b0508ca295020ae346a5990cca131d
  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  https://claude.ai/code/session_01EMc6bYqRMaqBG6gPV2bhpJ
  ---
  _Generated by [Claude Code](https://claude.ai/code)_

---

## PR #5 — plan: check — Phase 1 spec-check
task `T-0101` · merged 2026-11-08 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/5)

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=7a50f48ba5e78936521559a62c134efd4c40d702** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/5#pullrequestreview-5819396331)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - If T-0112 overruns ~800 lines during build, split the OCR cert-ingest path into its own task as the planner already anticipated.
  KERP-REVIEW: APPROVED patch-id=7a50f48ba5e78936521559a62c134efd4c40d702
  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  https://claude.ai/code/session_01EMc6bYqRMaqBG6gPV2bhpJ
  ---
  _Generated by [Claude Code](https://claude.ai/code)_

---

## PR #4 — plan: check — Phase 0 spec-check
task `T-0001` · merged 2026-11-08 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/4)

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=06cc3e1b06b94a638046fceff70435e976952670** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/4#pullrequestreview-5819326528)

## KERP review — round 1

Planning-only PR: diff touches 14 Phase 0 task files under `tracker/` and no application code, matching the stated scope. Reviewed against the merged spec (consolidation diff precedence 1, master + edge) and `CLAUDE.md`.

**Verified**
- **Acceptance criteria** are now concrete and TDD-able across all 14 tasks (e.g. T-0001 `create_app()` + `/health`→200 `{status,version}`; T-0005 enumerated mixin columns + clean autogenerate; T-0011 disabled module → 404; T-0014 non-admin → 403; T-0018 CEL-rule-set-as-data + findings persisted + failing rule blocks + signature frozen at T-0131; T-0023 idempotent re-run).
- **Spec refs resolve and are correct.** All three referenced d …

---

## PR #3 — Consolidate spec (merge old+new) + rebuild tracker to the 12-domain model
task `T-0131` · merged 2026-11-08 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/3)

**Comment by `kingdonk-ops`** · [link](https://github.com/kingdonk-ops/Kerp-/pull/3#issuecomment-6724813380)

**`review-gate` is red, but this is not a code failure and there is nothing to fix with a push.**

The job log says exactly:

```
patch-id=d9a37369f2b5b0163304b20494dc065ac8e49255
FAIL: no KERP-REVIEW marker from an allowed reviewer yet
```

`review-gate` passes only once an independent `kerp-reviewer` (allowed reviewer: `kingdonk-ops`) posts a `KERP-REVIEW: APPROVED patch-id=d9a37369f2b5b0163304b20494dc065ac8e49255` marker. No such marker exists yet, so the gate is red by design — it's the review step, not a CI correctness failure.

Status on the current head (`d9c2b4b`):
- `ci-gate` ✅ · `tracker` ✅ · `detect` ✅ · `backend`/`frontend`/`e2e` skipped (no such code yet)
- `review-gate` ⏳ awaiting the independent approval marker

Re-running `review-gate` will fail identically until a marker is posted, so I have not re-run it. Per the pipeline, review must come from a **different** agent/reviewer, so I am intentionally not self-approving. This PR is ready for `/kerp-run` steps 4–5 (independent review → merge).

---
_Generated by [Claude Code](https://claude.ai/code)_

---

## PR #2 — plan: spec is Inspection-ERP PRs #23 + #24 (archive #22, adopt AIP port)
task `T-0102` · merged 2026-11-08 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/2)

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=72eeb0aeca457051dd54f844a1c192fb2b449465** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/2#pullrequestreview-5816788802)

- _Must fix_
  1. **`tracker/tasks/T-0109.md` (title + Goal, lines 3 and 15–16): this task is now unblocked but asks for something the adopted spec replaces.** It says "Page/layout designer backend (oe_asset_layout)… shape depends on TD-002". MODULE-SPEC-11 §2 (line 39) says the new spec *"Supersedes the standalone `oe_asset_layout` 'Form/Inspection-Designer' note in `asset_management`"*: under adoption, layouts come from the one shared `forms`/`item_types` engine. An implementer picking this up would build the very thing SPEC-11 removes. **Fixed looks like:** rewrite the goal and acceptance criteria so asset-type layout and register pages come from the shared engine (SPEC-11 §2–§3, PROPOSAL §1). Add `MODULE-SPEC-11-inspection-requests.md` to `spec:`. Alternatively, drop the task and fold it into T-0110 or T-0102, with a Change Log note.
  2. **`tracker/tasks/T-0110.md` Goal (line 16): the task still offers the rejected option.** It says *"If TD-002 = adopt: one engine… Otherwise: thin `forms` per MODULE-SPECS."* TD-002 is decided, and SPEC-11 §2 says it *"Replaces the thin `forms`"*. **Fixed looks like:** state the adopted shape as the requirement (one field-type registry and one sandboxed TS/Python evaluator shared with `item_types`; template kinds; `schema_hash` revision immutability per SPEC-11 §2), and delete the "Otherwise" branch. Make the same edit in **`tracker/tasks/T-0102.md`** (Goal line 15, "Shape depends on TD-002"): say that it builds the asset-schema half of the shared engine per SPEC-11 §3, and add SPEC-11 to its `spec:` list. Without that ref, T-0102 never points at the se

- _Suggestions (non-blocking)_
  - `tracker/tasks/E-01-01.md:16`: "Build per SPEC-11 if TD-002 adopts the proposal" should become "Build per SPEC-11 (TD-002: adopted)".
  - Remove the now-inert `blocked_by: [TD-002]` from T-0102, T-0109, T-0110 and E-01-01, so the front matter matches the "unblocked" claim. `tracker.py` ignores resolved TODOs, so leaving it is harmless.
  - The body `## Spec` sections in T-0002, T-0013, T-0022 and T-0025 are now empty while their `spec:` front matter lists DEVELOPMENT-PLAN. Add the bullet back to keep body and front matter in sync.
  - T-0013 (audit) now cites only DEVELOPMENT-PLAN, which mentions audit only in passing (§1 principles 10–11). Consider adding MODULE-SPECS or a `spec-gap` TODO if there is no audit section.
  - `tracker/todo/TD-010.md:13`: the body still says "Re-sync docs/spec/architecture-suite-reference…" (stale path); it could note the archive location.
  - Bump the `docs/DEVELOPMENT-PLAN.md` Change Log for the TD-002 resolution and the spec-source correction, if one is kept there.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=72eeb0aeca457051dd54f844a1c192fb2b449465
  ---
  _Generated by [Claude Code](https://claude.ai/code)_

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=846cfbe46fd43e20e803bad2f65db00ac3ec07b4** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/2#pullrequestreview-5816797747)

- _Must fix_
  1. **Some SPEC-11 §2/§3 features are now in no task and no TODO** (CLAUDE.md §5 requires this; the planner checklist requires "nothing in the spec dropped"). This PR claims §2–§3 coverage by rewriting T-0102/T-0109/T-0110 and adding T-0131, and TD-011 records only forms F13 and F14 as deferred. I found no task or TODO for any of these:
     - **§3 F4**: auto-create ITP/inspection from default templates. T-0102 adds the `auto_create_*`/`default_*_template_id` columns, but no task builds the behaviour.
     - **§3 F3**: `is_component` / `allows_components`.
     - **§3 F1**: generated register pages. Only the T-0109 parenthetical mentions it; there is no criterion.
     - **§2 F10**: an instrument-reference field blocks a reading on an out-of-calibration gauge, via `eligibility`. T-0110 defines the field type only; E-01-01 does not mention the forms-side gate.
     - **§2 F11**: context auto-fill (asset tag, RSW, location, procedure).
     - **§2 F1**: sections + grid/tabular layout in the revision schema.
     - **§2 F8, TS half**: the shared TS evaluator package. T-0110 builds Python plus fixtures "so a later TS port proves parity", and no frontend task or TODO owns that port.
     **Fixed looks like:** each of these has a home. Either add an acceptance criterion to an existing task (for example F4 into T-0111 or T-0109, F3 into T-0103, F1 layout into T-0131, F11 into T-0131 or T-0111, F10 as a criterion in E-01-01 §5 or a TODO linked to T-0110), or raise `deferred`/`missed` TODO(s), for example by extending TD-011, that name the features and tasks. Re-render the boards.

- _Suggestions (non-blocking)_
  - `tracker/tasks/T-0108.md`: "Default configuration packs… Taxonomy, layouts, disciplines, ITPs" still cites only SPEC-08 and says "layouts", the superseded concept. It also overlaps T-0109's unified starter packs (§3 F11, §2 F15). Point it at SPEC-11 and say it provisions packs in T-0109's seed format, so two pack formats don't get built.
  - T-0110 has `order: 1001` and T-0131 has `order: 1002`, so `plan_order` and the board list them after T-0130, below the tasks that depend on them (T-0102 at order 2, T-0109, T-0111). Readiness is still correct, but renumbering, for example T-0110 to 0 and T-0131 to 1, would make the board read in build order.
  - T-0102: note that `default_itp_template_id` / `default_inspection_template_id` are references resolved through the `forms` service, not cross-module FKs (CLAUDE.md §6, no cross-module table access).
  - Before calling F13 "Phase 6" in TD-011, check that `reporting` really is Phase 6 in DEVELOPMENT-PLAN §7.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=846cfbe46fd43e20e803bad2f65db00ac3ec07b4
  ---
  _Generated by [Claude Code](https://claude.ai/code)_

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=846cfbe46fd43e20e803bad2f65db00ac3ec07b4** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/2#pullrequestreview-5816801820)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - T-0109's F1 criterion says "UI in T-0127", but `tracker/tasks/T-0127.md` has no matching criterion. Add one when T-0127 is next spec-checked.
  - Board order still lists some tasks before their dependencies (T-0102 at order 2 before T-0110 at order 10; T-0108/T-0109 before T-0131). Readiness is unaffected; renumber in a later planning pass if wanted.
  - T-0131 is now on the heavy side of M (5 entities, endpoints, trigger, lint, auto-fill, layout). The implementer should split off section library/option sets if it goes past ~800 lines.
  - TD-013: prioritise fixing it, because it weakens the merge gate for every `plan:` PR.
  KERP-REVIEW: APPROVED patch-id=846cfbe46fd43e20e803bad2f65db00ac3ec07b4
  ---
  _Generated by [Claude Code](https://claude.ai/code)_

---

## PR #1 — chore: GitHub settings + rulesets setup script
merged 2026-11-08 · by `kingdonk-ops` · [PR](https://github.com/kingdonk-ops/Kerp-/pull/1)

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=636abb1bd87283310a8b62f48eb76548f27be85d** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/1#pullrequestreview-5816719311)

- _Must fix_
  1. `scripts/setup_github.sh:62` and `:75`: the required status checks carry no `integration_id`, so **any** source can satisfy `ci-gate` / `review-gate`, including a plain commit status. The agents act as the repo owner, so `gh api -X POST repos/kingdonk-ops/Kerp-/statuses/<sha> -f context=review-gate -f state=success` would satisfy the dev gate without any review. That is the same bypass the header comment (lines 7-9) removes bypass actors to prevent. Fixed: pin both checks to the GitHub Actions app, e.g. `{"context": "ci-gate", "integration_id": 15368}, {"context": "review-gate", "integration_id": 15368}` on dev, and the same for `ci-gate` on main. (I confirmed this PR's existing check runs come from app id 15368.)
  2. `scripts/setup_github.sh:29-35`: `setvar` always overwrites. Re-running the script (which the header tells you to do after break-glass, and calls idempotent) resets `KERP_ALLOWED_REVIEWERS` to only the current `gh` login. That silently drops any reviewer logins added the way `docs/WORKFLOW.md:48` says to add them, and their markers then stop counting. `KERP_PROMOTE_EVERY` also gets reset to 5. Fixed: create these variables only if they are missing (skip the PATCH when the variable exists), or for `KERP_ALLOWED_REVIEWERS`, merge the current login into the existing comma list instead of replacing it.

- _Suggestions (non-blocking)_
  - Line 35: `"$(gh api user -q .login)"` used as an argument is not covered by `set -e`. If it fails, setvar runs with an empty value. Assign it to a variable first (`login=$(gh api user -q .login)`) so a failure aborts.
  - Line 40: if two rulesets ever share a name, `id` has two lines and the PUT URL breaks. Add `| first` / `head -n1`, or fail when there is more than one match. `?includes_parents=false` would also keep org-level rulesets out if the repo ever moves to an org.
  - Line 88: existing labels are never updated (color drift on re-run). If you care, fall back to `PATCH labels/<name>`.
  - Main ruleset: anyone with write access can still push any CI-green dev commit straight to main. The rule enforces "CI-green fast-forward", not "only promote.yml". That is acceptable, but `WORKFLOW.md:26` says "written only by promote.yml". Either note it there as convention, or look at an `update` rule with a GitHub Actions bypass if GitHub supports that for `GITHUB_TOKEN`.
  - Out of scope for this PR, but worth watching in the smoke test: `review-gate` runs from both `pull_request` and `pull_request_review`, so one SHA can carry several `review-gate` check runs (a failed one from before the review). Confirm the merge box goes green after the marker is posted.
  Gates: `tracker.py validate` OK (105 tasks, 10 todos) · `render --check` up to date · `pytest scripts/tests` 11 passed · `bash -n` OK. Not run: backend, frontend and e2e, because those directories do not exist yet.
  KERP-REVIEW: CHANGES_REQUESTED patch-id=636abb1bd87283310a8b62f48eb76548f27be85d
  ---
  _Generated by [Claude Code](h

**Review by `kingdonk-ops` — KERP-REVIEW: CHANGES_REQUESTED patch-id=3f6694b4a48e82e35bfd6b18c9a5b9ac1eb8ade8** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/1#pullrequestreview-5816724140)

- _Must fix_
  1. `scripts/setup_github.sh:30`: `getvar` returns the error JSON when a variable is missing, not an empty string. When a request fails, `gh api` still writes the error response body to **stdout** and skips the `-q` filter. `2>/dev/null || true` only hides stderr and the exit code. I checked this on this repo with a read-only call: `out="$(gh api repos/kingdonk-ops/Kerp-/labels/no-such-label -q .name 2>/dev/null || true)"` gives `{"message":"Not Found",...,"status":"404"}`. On a repo where these variables don't exist yet:
     - `setvar_default KERP_PROMOTE_EVERY 5` (line 40) sees a non-empty `cur`, prints `var KERP_PROMOTE_EVERY={"message":"Not Found"...} (kept)` and never creates the variable.
     - `KERP_ALLOWED_REVIEWERS` (lines 42–50): `CUR` is the 404 JSON, so the `-z` branch is skipped and `grep` finds no match. The script then sends a PATCH to a variable that doesn't exist and gets a 404, and `set -e` stops the script **before step 4**. No rulesets get created, and the reviewer allow-list stays empty, so the review gate fails closed.
     **Fixed looks like:** print only on success, for example `getvar() { local v; if v="$(gh api "$R/actions/variables/$1" -q .value 2>/dev/null)"; then printf '%s' "$v"; fi; }`. Better still, check for an actual 404 (for example with `gh api -i`, or by matching the status) so that other errors (401, 403, network) stop the script instead of being read as "missing". Then `setvar_default` would try to create a variable that may already exist.

- _Suggestions (non-blocking)_
  - `scripts/setup_github.sh:57`: `upsert_ruleset` has the same stdout pattern. If listing the rulesets fails, `id` becomes the error JSON, and the next step sends a PUT to `rulesets/{json}`. That fails loudly, which is fine, but the `|| true` adds nothing. Drop it so the list call itself fails clearly.
  - `scripts/setup_github.sh:45`: `review_gate.py` strips whitespace around each login (line 114), but the membership check here doesn't. A list like `alice, bob` would get `bob` appended a second time. You could trim with `tr ',' '\n' | sed 's/^ *//;s/ *$//'`, or by piping through `xargs -n1`.

**Review by `kingdonk-ops` — KERP-REVIEW: APPROVED patch-id=52be72f01637c49afdeb743af1d16f4f57c9ec71** · [full review](https://github.com/kingdonk-ops/Kerp-/pull/1#pullrequestreview-5816727055)

- _Must fix_
  None.

- _Suggestions (non-blocking)_
  - `getvar` reads only the first 100 variables and does not paginate. This is fine at current scale; `gh api --paginate` with `--slurp`, or a guard on `total_count`, would make it future-proof.
  - If a variable exists with an empty value, it is treated as missing and the POST returns 409. The script then aborts, which is a safe failure. To handle this case properly, test for presence separately from the value.
  KERP-REVIEW: APPROVED patch-id=52be72f01637c49afdeb743af1d16f4f57c9ec71
  ---
  _Generated by [Claude Code](https://claude.ai/code)_

---
