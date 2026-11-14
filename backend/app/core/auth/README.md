# app/core/auth — users, asymmetric JWTs, JWKS, revocation (T-0008), field-worker auth (T-0009)

Diff §4 "Auth hardening". Per ADR 0001: scrypt hashing and the credential → tenant → RLS →
user dependency shape are ported from Inspection-ERP `core/auth/`; the token layer is
rewritten (HS256 → RS256/EdDSA + JWKS + `jti` revocation in Redis).

| File | What |
|---|---|
| `passwords.py` | scrypt hash/verify (stdlib, constant-time, parameters stored in the hash) |
| `keys.py` | Ed25519 / RSA signing key, retired verification keys, RFC 7638 `kid`, JWKS document |
| `tokens.py` | `TokenService`: issue/verify access (15 min) and refresh tokens |
| `revocation.py` | `RevocationStore`: Redis (prod) / in-memory (dev, tests) — `jti` + session blacklist, one-time refresh tokens |
| `runtime.py` | `AuthRuntime` (keys + tokens + store) built lazily from settings, cached on `app.state.auth` |
| `bearer.py` | `get_bearer`: verifies the `Authorization: Bearer` token once per request (no DB) |
| `deps.py` | `CurrentPrincipal` (401 unless a valid token of an active user), `require_claims` |
| `providers.py` | `AuthProvider` seam + `LocalPasswordProvider` (Entra ID SSO later: TD-043) |
| `service.py` | `AuthService` login / refresh / logout / magic link / PIN unlock; `create_user`, `set_password`; internal staff rule (`is_internal_user`, T-0049) |
| `field.py` | Magic-link tokens + delivery seam, PIN rules (T-0009) |
| `lockout.py` | `ProgressiveLockout` + policies, Redis-backed (T-0009) |
| `models.py` | `auth_user` table (tenant-level rows, RLS) — migration `0003`, internal staff flag + Entra link in `0013` (T-0049, ADR 0009); `auth_project_membership` (T-0010) — migration `0006`, persona CHECK widened for PER-16 in `0012` (T-0076) |
| `access.py` | Persona matrix, capability tiers, SoD, scope resolution, `require_capability` / `require_hold_release` guards (T-0010) |
| `router.py`, `schemas.py` | HTTP endpoints below |

## Endpoints

| Method + path | Auth | Result |
|---|---|---|
| `GET /.well-known/jwks.json` | none | `{"keys": [...]}` public JWKs (active + retired), `Cache-Control: public, max-age=300` |
| `POST /api/v1/auth/login` | none | `{email, password}` → `TokenResponse` (`no-store`) + refresh cookie; a `tenant_id` field is `422`; `429` when locked |
| `POST /api/v1/auth/refresh` | refresh cookie + Origin | new `TokenResponse` + cookie (rotation); `401` for PIN-gated or ended sessions; `403` cross-origin |
| `POST /api/v1/auth/logout` | bearer | `204`; revokes the token and its whole login session; clears the refresh cookie |
| `GET /api/v1/auth/me` | bearer | `{id, tenant_id, email, display_name, is_internal}` (`is_internal` read-only, below) |
| `POST /api/v1/auth/magic-link` | none | `{email}` → always `202`; sends a 15-min one-time link to an active user; `429` when throttled |
| `POST /api/v1/auth/magic-link/redeem` | none | `{token, pin}` → `TokenResponse` + cookie of a new **PIN-gated** session |
| `POST /api/v1/auth/pin/unlock` | refresh cookie + Origin | `{pin}` → rotated `TokenResponse` + cookie; `429` when locked; `403` cross-origin |

`TokenResponse` is `{access_token, token_type, expires_in, refresh_expires_in}`: no response
body ever carries the refresh token (below).

**One hidden tenant (T-0037, diff §4.1, TD-120).** A deployment serves one tenant,
`KERP_DEFAULT_TENANT_ID`; login and magic-link requests carry no tenant field and nothing
names the tenant to the user. Production refuses to start without it (app startup). That the
id names an existing tenant row is checked once first-run provisioning creates the tenant
table (T-0023, TD-248).

## Internal staff (T-0049, ADR 0009)

Diff §4.1 *Internal staff*. `auth_user` carries (migration `0013`, additive):

| Column | Type | Meaning |
|---|---|---|
| `is_internal` | boolean, not null, default `false` | Derived internal-staff flag (below) |
| `entra_oid` | string(64), nullable | Object id of the linked **member** account of the company Entra tenant; unique per tenant (`uq_auth_user_tenant_entra_oid`). Set by the first SSO sign-in (T-0054) |
| `entra_tid` | string(64), nullable | The Entra tenant id that vouched for the link |

**Rule.** `is_internal` is true when `entra_oid` is set (T-0054 links members only and refuses
guests) **or** the e-mail's domain is on `KERP_INTERNAL_EMAIL_DOMAINS`: exact, case-insensitive
match, so `eu.company.example` is not `company.example`. False otherwise. It is recomputed by
`create_user` and at every successful sign-in (password, magic-link redeem; SSO with T-0054),
never at refresh or PIN unlock (they continue a session). Removing a domain takes effect at the
user's next sign-in. A sign-in writes the row only when the flag changes.

**Never input, never a claim.** No request body accepts `is_internal`, `entra_oid` or
`entra_tid` (`422`; the admin create/update schemas of T-0014 must keep it that way) and the
tokens carry no such claim. `/auth/me` shows the stored flag, read-only. Other modules read it
only through `service.is_internal_user(session, user_id)` (false for an unknown user or one of
another tenant), never the table. **It grants nothing:** not a persona or capability; a
non-member internal user gets `404` on a project like anyone else. Its one use is the lobby
list (T-0040).

**`KERP_INTERNAL_EMAIL_DOMAINS`** (deployment configuration): comma-separated domains, e.g.
`company.example,company-group.example`. Entries are trimmed, lower-cased and de-duplicated;
an entry with `@`, whitespace or `*` stops the app at startup (settings validation). Empty or
unset: no domain rule, only the Entra link makes an account internal.

## Refresh cookie (web SPA, T-0037)

Diff §4.2, TD-203. The refresh token travels only as the cookie
`kerp_refresh`: `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/api/v1/auth`, `Max-Age` =
seconds left in the session. Script on the page never sees it, and it is sent on no other API
request. Login, magic-link redeem, refresh and PIN unlock set it; logout clears it.

**CSRF:** besides `SameSite=Strict`, `/auth/refresh` and `/auth/pin/unlock` check the
`Origin` header before they look at the cookie: it must equal the allowed web origin,
`KERP_WEB_ORIGIN` (e.g. `https://kerp.example.com`), or, when that is unset, the API's own
origin (SPA and API under one URL, diff §4.1). A missing or other `Origin` is `403`. The
other endpoints need no check: login and redeem take credentials the attacker does not have,
logout needs the bearer token. The JSON-body transport for the Capacitor Android build, behind
a native-origin allow-list, comes with T-0043.

The SPA (`frontend/src/auth/tokenStore.ts`) keeps the access token in memory and only a
"signed in" marker in `sessionStorage`; it removes the refresh token earlier builds stored there.

Every rejection is `401 {"detail": "Not authenticated"}` with a `WWW-Authenticate: Bearer`
challenge (`error="invalid_token"` when a token was presented). Unknown user, wrong
password, inactive user and wrong tenant are indistinguishable, and an unknown user still
costs one scrypt round. An unreachable revocation store is `503` (fail closed).

## Field workers: magic link + PIN (T-0009)

`field.py`, `lockout.py`. The PIN is a **local session unlock only**: it never authenticates
on its own. A magic link starts a session on the device and sets that session's 6-digit PIN
(scrypt hash in Redis under `pin:<sid>`; weak PINs such as `111111` / `123456` are refused).
`/auth/refresh` refuses such a session; only `/auth/pin/unlock` with the device's refresh
token **and** the PIN rotates it. The refresh token without the PIN, or the PIN without the
refresh token, gets nothing. Interpretation recorded in TD-135.

**Progressive lockout** (Redis, shared by every worker; replaces Inspection-ERP's in-memory
`ratelimit.py`): an attempt is counted before the secret is checked; once the immediate tries
are used up, each failure locks the subject and attempts during the lock are refused with
`429` + `Retry-After` without checking the secret; a right answer clears the count. Check,
count and lock are one atomic store operation (`begin_attempt`: WATCH/MULTI on Redis), so a
burst cannot slip past a lock another request is arming (TD-191). Subjects
are hashed into the keys. If Redis evicts a `pin:<sid>` key the PIN gate fails open: run
Redis with `maxmemory-policy noeviction` (TD-142 tracks a token-claim remedy).

| Scope | Key | Immediate tries | Then locked | Hard cap |
|---|---|---|---|---|
| PIN unlock | session | 4 | 30 s doubling to 15 min | 10th wrong PIN revokes the session (counter window = refresh lifetime + 1 day, so it outlives the 12 h session; until T-0038 moves the PIN to the device) |
| Password login | tenant + e-mail + client IP | 6 | 30 s doubling to 15 min | none (no permanent lock-out) |
| Password login backstop | tenant + e-mail, any client | 51 per 24 h | 30 s doubling to 15 min | none |
| Magic-link request | tenant + e-mail | 4 per hour | 1 min doubling to 15 min | none |

Password login is keyed by client (T-0068, TD-191): someone else's failures in the
per-client lock lock only their own client. The per-account backstop caps guessing from many
addresses, and is the remaining way to lock a known user out: the first backstop lock costs an
attacker 51 failures (at once from 9+ addresses, or over hours from one), after which about one request per lock expiry keeps the
account locked for every client within its 24 h window (TD-225, owner decision pending).
The client is `request.client.host`. Behind a reverse proxy, set uvicorn's
`FORWARDED_ALLOW_IPS` to the proxy's address so the real client comes from
`X-Forwarded-For`; without it every user appears as the proxy and the lock is per account
again (as before T-0068, never worse). Never set `FORWARDED_ALLOW_IPS=*` on a uvicorn that
clients reach directly: they could then choose their own `X-Forwarded-For` and get a fresh
per-client lock on every request (only the backstop would remain). IPv6 clients can rotate
within their /64 (TD-226).

Unknown addresses lock exactly like real ones. Link delivery is the `MagicLinkSender` seam:
development logs the token, production logs "not delivered" until e-mail exists (TD-084).

## Tokens

Claims: `iss`, `aud`, `sub` (user), `tid` (tenant), `sid` (login session), `jti`, `type`
(`access`|`refresh`), `iat`, `nbf`, `exp`. Header: `kid` (RFC 7638 thumbprint), `alg`
(`EdDSA` or `RS256`, from the key), `typ` (`at+jwt` for access tokens). Verification looks
the key up by `kid` and accepts only that key's algorithm (no `none`, no HS/RS confusion).

- **Access**: 15 minutes (`KERP_ACCESS_TOKEN_MINUTES`, capped at 15).
- **Refresh**: `KERP_REFRESH_TOKEN_HOURS` (default 12, TD-121), counted from sign-in.
  Single use: a refresh consumes it atomically in Redis and issues a new pair in the same
  `sid` whose `exp` is the session's end, so rotation never extends a session; at sign-in
  + 12 h the refresh is `401` (exact, no clock leeway) and the user signs in again. Access
  tokens never outlive the session either. Replaying a used refresh token revokes the whole
  session (reuse detection).
- **Logout** revokes the access `jti` and the `sid`; every token of that login dies.
- **Password change** (`set_password`) sets `password_changed_at` (microseconds). A token is
  valid only if its `iat` is not before it. `iat` and `exp` are fractional NumericDates with
  microsecond precision, so the rule is exact (T-0061): a token issued before the change is
  rejected and one issued after it (e.g. the login straight after `set_password`) is
  accepted, even within the same second. Tokens from before T-0037 (whole-second `iat`) issued
  in the second of a change are rejected. Deactivating a user locks them out on their next
  request.

## Request flow (tenant from the JWT only)

`get_db` → `get_tenancy_context` (`app/core/tenancy/context.py`) → `get_bearer`: a valid,
unrevoked access token binds the request session to `TenancyContext(tid, user_id=sub)`
before any query, so RLS and the ORM scope confine it to the token's tenant. No token or a
bad token leaves the session unbound (RLS shows it nothing). Endpoints that need a user take
`principal: CurrentPrincipal`, which also sets the `current_tenant_id` / `current_user_id`
context vars. The session also gets the read-only allowed projects from the user's
memberships (below). The write project is not bound from the request yet (TD-122).

## Personas, tiers and scope (T-0010)

Edge spec §5. A membership (`auth_project_membership`, a tenant-level row) gives a user one
persona (`PER-01`…`PER-16`) on one project (`member_project_id`); a Site Inspector (`PER-07`)
membership also names its `site_node_id`. Revoking soft-deletes the row. `access.py` holds the
matrix:

| Persona | Role | Tier | Scope | Capabilities | Hold release |
|---|---|---|---|---|---|
| PER-01 | Operations Management | Manager | portfolio | V C E | — |
| PER-02 | Project Manager | Manager | assigned | V C E A (commercial) | — |
| PER-03 | Site Superintendent | Manager | single | V C E | — |
| PER-04 | Trade Supervisor | Editor | area (TD-149) | V C E | — |
| PER-05 | Area Planner | Editor | single | V C E | — |
| PER-06 | QA/QC Lead | Manager | single | V C E A | quality |
| PER-07 | Site Inspector | Editor | site | V C E A | quality, own site only |
| PER-08 | Storekeeper / Yard Lead | Editor | single | V C E | — |
| PER-09 | Project Administrator | Admin | single | V C E admin | none (SoD block) |
| PER-10 | Procurement Specialist | Editor | portfolio | V C E | — |
| PER-11 | HSE Manager | Manager | portfolio | V C E A | safety |
| PER-12 | Human Resources Master | Editor | portfolio | V C E | — |
| PER-13 | Quality Director | Manager | portfolio | V C E | — |
| PER-14 | Field Worker | Editor | single | V C E | — |
| PER-15 | Client Representative | Portal | subtree (TD-149) | V S | — |
| PER-16 | Project Viewer | Viewer | single | V | — |

- **Capabilities**: V/C/E from the default tier (Manager, Editor, Admin = V/C/E, Admin adds
  `administer`; Portal = V; Viewer = V), `A` where the matrix gives hold or commercial (A)
  authority (PER-02, 06, 07, 11), `S` for the client representative (PER-15).
  Interpretation: TD-151.
- **PER-16 Project Viewer** (T-0076, TD-251, ADR 0008): read-only across the project's
  modules. Every `require_capability` other than `V` and every hold guard answer `403`; it has
  no SoD implications and is not a portfolio persona. Migration `0012` widens the persona
  CHECK; its downgrade refuses while an active PER-16 membership exists and purges revoked
  (soft-deleted) PER-16 rows.
- **Scope**: portfolio personas (PER-01/10/11/12/13) put their projects in the session's
  allowed list (multi-project reads); every other persona reads only the bound project.
  PER-07 is limited to its sites (`SiteScopedMixin`). Area (PER-04) and subtree (PER-15)
  filters are not enforced yet (TD-149).
- **SoD**: hold release is an explicit grant: quality → PER-06, PER-07 (own site only);
  safety → PER-11. `SOD_BLOCKED` forbids any hold authority for PER-09 and `sod_violations()`
  checks the matrix against it.

Guards: `Depends(require_capability(Capability.ADMINISTER))`,
`Depends(require_hold_release(HoldKind.QUALITY))` (then
`access.can_release_hold(kind, site_node_id=...)` for the hold's site). No membership in the
session's bound project (or no bound project) → `404`; a member without the authority → `403`.

## Keys and configuration

| Variable | Default | Meaning |
|---|---|---|
| `KERP_JWT_ALGORITHM` | `EdDSA` | `EdDSA` (Ed25519) or `RS256` (RSA ≥ 2048); must match the key |
| `KERP_JWT_PRIVATE_KEY` | unset | PEM private key (secret; never logged) |
| `KERP_JWT_PRIVATE_KEY_FILE` | unset | Path to the PEM instead |
| `KERP_JWT_RETIRED_PUBLIC_KEYS` | `[]` | JSON list of public PEMs still accepted and published |
| `KERP_JWT_ISSUER` / `KERP_JWT_AUDIENCE` | `kerp` / `kerp-api` | `iss` / `aud` |
| `KERP_ACCESS_TOKEN_MINUTES` | `15` | 1–15 |
| `KERP_REFRESH_TOKEN_HOURS` | `12` | 1–24; session length from sign-in |
| `KERP_DEFAULT_TENANT_ID` | unset | The deployment's one tenant (TD-120); required in production |
| `KERP_INTERNAL_EMAIL_DOMAINS` | empty | Comma-separated e-mail domains of internal staff (exact match, no wildcards; see *Internal staff*); a malformed entry refuses startup |
| `KERP_WEB_ORIGIN` | unset | Web SPA origin allowed on the cookie endpoints; unset = the API's own origin as the request sees it (behind a TLS-terminating proxy set this, or trust the proxy with `FORWARDED_ALLOW_IPS`) |
| `KERP_REDIS_URL` | unset | Revocation store |

Outside production, a missing key means an **ephemeral** key per process and a missing
Redis URL an in-memory store (both logged as warnings). In production both are required;
the runtime is built on first use, so a misconfigured deployment fails its first auth
request (startup check: TD-124). Generate a key with:

```bash
openssl genpkey -algorithm ed25519 -out jwt-ed25519.pem
```

**Rotation:** set the new key, move the old public key into `KERP_JWT_RETIRED_PUBLIC_KEYS`,
and remove it after the refresh lifetime has passed.

Tests never need a live Redis: they use `fakeredis` (`tests/auth_support.py`) and keys
generated at test time.
