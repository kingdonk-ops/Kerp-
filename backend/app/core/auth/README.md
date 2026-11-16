# app/core/auth — users, asymmetric JWTs, JWKS, revocation (T-0008), field-worker auth (T-0009, T-0038), device registry (T-0043)

This is the identity module (`module: identity` in the tracker): it lives in `app/core/` because
every request's authentication depends on it (T-0008, T-0010 notes).

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
| `service.py` | `AuthService` login / refresh / logout / magic link / PIN unlock; `create_user`, `set_password`; user reads for the admin module (`list_users`, `get_user`, `users_by_id`, `find_user_by_email`, T-0014); internal staff rule (`is_internal_user`, T-0049) |
| `field.py` | Magic-link tokens + delivery seam, PIN rules and the `PIN_REQUIRED` / `PIN_INVALID` / `PIN_EXHAUSTED` answers (T-0009, T-0038) |
| `lockout.py` | `ProgressiveLockout` + policies, Redis-backed (T-0009); offline failures counted with `extra` (T-0038) |
| `models.py` | `auth_user` table (tenant-level rows, RLS) — migration `0003`, internal staff flag + Entra link in `0013` (T-0049, ADR 0009); `auth_project_membership` (T-0010) — migration `0006`, persona CHECK widened for PER-16 in `0012` (T-0076), membership-based RLS in `0015` (T-0014); `oe_identity_device` (T-0043) — migration `0016`, `wipe_secret_hash` in `0018` (T-0080), `pin_hash` + `pin_set_at` in `0019` (T-0038) |
| `memberships.py` | The membership store's write side (`project_grants`, `set_user_grants`, `revoke_project`; revoke = soft delete, re-grant revives), used by the admin module (T-0014) and the projects module (creator PER-09, revoke on delete; T-0039) |
| `access.py` | Persona matrix, capability tiers, SoD, scope resolution, `ProjectAccess` (`has`, `can_release_hold`) (T-0010); admin-grant lookup seam, `is_system_admin` binding, `CallerIsSystemAdmin`, `sod_warning` (T-0014); `CallerAdminGrants` (the caller's own `system_admin` / `create_projects`, cached with the scope; T-0039) |
| `devices.py` | Device registry (`oe_identity_device`): enrol + session binding, `resolve_request_device` (last seen, revoked → `DEVICE_REVOKED`), revoke, wipe secret + uniform wipe confirmation (T-0080), `record_sync` for the sync endpoints (T-0043); the device PIN (`set_pin`, `pin_device`, T-0038) |
| `device_api.py` | Device endpoints: enrol (with the PIN), PIN change, own / per-user lists, revoke (self, `system_admin`, PER-09 of a shared project; cross-project wipe warning), wipe-confirmed with the wipe secret (T-0043, T-0080, T-0038) |
| `device_signal.py` | `X-KERP-Device-Id` header, `DeviceRevokedError` → `401` with `code: DEVICE_REVOKED` (no database) |
| `router.py`, `schemas.py` | HTTP endpoints below |

## Endpoints

| Method + path | Auth | Result |
|---|---|---|
| `GET /.well-known/jwks.json` | none | `{"keys": [...]}` public JWKs (active + retired), `Cache-Control: public, max-age=300` |
| `POST /api/v1/auth/login` | none | `{email, password}` → `TokenResponse` (`no-store`) + refresh cookie (native app: token in the body); a `tenant_id` field is `422`; `429` when locked |
| `POST /api/v1/auth/refresh` | refresh cookie + Origin (native app: `{refresh_token}` body) | new `TokenResponse` + cookie (rotation); `401 {code: PIN_REQUIRED}` for a session of a device with a PIN idle `KERP_PIN_IDLE_MINUTES` or longer (token not spent); `401` for ended sessions; `403` cross-origin |
| `POST /api/v1/auth/logout` | bearer | `204`; revokes the token and its whole login session; clears the refresh cookie |
| `GET /api/v1/auth/me` | bearer | `{id, tenant_id, email, display_name, is_internal}` (`is_internal` read-only, below) |
| `POST /api/v1/auth/magic-link` | none | `{email}` → always `202`; sends a 15-min one-time link to an active user; `429` when throttled |
| `POST /api/v1/auth/magic-link/redeem` | none | `{token}` → `TokenResponse` + cookie of a new session (on an enrolled device: bound to it, PIN kept) |
| `POST /api/v1/auth/pin/unlock` | refresh cookie + Origin (native app: `refresh_token` in the body) | `{pin, failed_offline?}` → rotated `TokenResponse` + cookie; `401 {code: PIN_INVALID \| PIN_EXHAUSTED}`; `429` when locked; `403` cross-origin |
| `POST /api/v1/devices/enrol` | bearer | `{device_id, platform, os_version, app_version, pin?}` → device + `wipe_secret`; `201` new (the only response carrying the secret), `200` known (`wipe_secret: null`); binds the session to the device; sets the PIN once; `409` another account's id, a session already bound elsewhere, or `code: PIN_ALREADY_SET` |
| `PUT /api/v1/devices/{device_id}/pin` | bearer (a session bound to the device) | `{pin}` → `204`; a new device PIN (the old one is not needed); `403` another session, `404` not the caller's active device |
| `GET /api/v1/devices` | bearer | the caller's devices (last seen, last sync, status) |
| `GET /api/v1/users/{user_id}/devices` | bearer | a user's devices: the user, a `system_admin`, or a PER-09 of a project the user belongs to; `403` a shared project without PER-09; `404` otherwise |
| `POST /api/v1/devices/{device_id}/revoke` | bearer | `{confirm_cross_project_wipe?}` → revoked device + `role` + `cross_project_warning`; same callers as above; `409` until a PER-09 confirms the cross-project wipe |
| `POST /api/v1/devices/{device_id}/wipe-confirmed` | none | `{wipe_secret}` → `204`: the revoked device reports its wipe; every refusal (unknown id, active device, wrong or missing secret) is the same `404 {"detail": "Not Found"}` (T-0080); `wipe_secret` at most 128 characters (`422`); per client address `WIPE_CONFIRM_POLICY` (11 attempts back to back, then the doubling lock, 1 h window; a right secret clears it) answers `429` + `Retry-After` before any lookup or audit entry (T-0212) |

`TokenResponse` is `{access_token, token_type, expires_in, refresh_expires_in, session_expires_at}`
(`session_expires_at`: the session's end, sign-in + 12 h, the same on every rotation and PIN
unlock; a field client starts its offline grace there, diff §4.2, T-0038): no response
body carries the refresh token (below), except for an allow-listed native app
(`refresh_token`, *Native app origins*). Every token endpoint and every authenticated request
reads `X-KERP-Device-Id`; a revoked device is `401` with `code: DEVICE_REVOKED` (*Devices*).
Sign-in (login, magic-link redeem) checks the credentials first: only then is the device
checked (T-0080, *Device hardening*).

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
`entra_tid` (`422`; so do the admin user create/update schemas, T-0014, TD-265) and the
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
logout needs the bearer token. The JSON-body transport for the Capacitor Android build sits
behind a native-origin allow-list (*Native app origins*, T-0043).

The SPA (`frontend/src/auth/tokenStore.ts`) keeps the access token in memory and only a
"signed in" marker in `sessionStorage`; it removes the refresh token earlier builds stored there.

Every rejection is `401 {"detail": "Not authenticated"}` with a `WWW-Authenticate: Bearer`
challenge (`error="invalid_token"` when a token was presented). Unknown user, wrong
password, inactive user and wrong tenant are indistinguishable, and an unknown user still
costs one scrypt round. An unreachable revocation store is `503` (fail closed).

## Native app origins: refresh token in the body (T-0043, TD-203)

Diff §4.2. The Capacitor Android build keeps its refresh token in the keystore-backed store and
cannot use the `SameSite=Strict` cookie, so it gets the token in the JSON body. The server
chooses the transport **from the request's `Origin` header only**:

- `Origin` is on `KERP_NATIVE_APP_ORIGINS` (and is neither `KERP_WEB_ORIGIN` nor the API's own
  origin): login, magic-link redeem, refresh and PIN unlock return `refresh_token` in the
  body and set **no** cookie; `/refresh` and `/pin/unlock` take the token from the body
  (`{"refresh_token": "..."}`, a missing one is `401`) and ignore any cookie.
- Every other request, including one **without** `Origin`, from the web origin or from a
  foreign origin: the cookie transport above. A body token is ignored on `/refresh` (the
  cookie is still required) and refused with `422` on `/pin/unlock`.

The client-reported device `platform` (enrolment) and any request flag play no part: a web
session on an `android` device still gets the cookie (`tests/test_native_transport.py`). Script
on the web origin (XSS) therefore never sees a refresh token in a body. Optional later
hardening: device-bound attestation (e.g. Play Integrity) on top of the origin check.

## API CORS and the native-origin allow-list

| Variable | Default | Meaning |
|---|---|---|
| `KERP_WEB_ORIGIN` | unset | The origin the web SPA is served from (cookie endpoints' Origin check). Normalised to `scheme://host[:port]` |
| `KERP_NATIVE_APP_ORIGINS` | empty | Comma-separated origins of the native field app, e.g. `https://localhost` (Capacitor Android, `androidScheme: https`) or `capacitor://localhost`. Each entry must be a bare origin (no path, query, user info or `*`); it is lower-cased and the default port dropped |

- **Startup fails** (settings validation) when an entry is malformed, when the list contains
  `KERP_WEB_ORIGIN`, or when the list is set but `KERP_WEB_ORIGIN` is not (the SPA's origin
  must be known to check it; set it even when SPA and API share one URL). At request time the
  API's own origin never gets the body transport either.
- **CORS** is configured from the same list and nothing else: `CORSMiddleware` allows exactly
  these origins, methods `GET POST PUT PATCH DELETE`, headers `Authorization`, `Content-Type`
  and `X-KERP-Device-Id`, exposes `Retry-After` and `WWW-Authenticate`, and **never** allows
  credentials (the body transport needs no cookie). Without native origins no CORS headers are
  sent at all; the web SPA is same-origin or served from `KERP_WEB_ORIGIN` behind the proxy.

## Devices (T-0043, diff §4.3)

Every field client (Capacitor Android build, field PWA) enrols after each sign-in
(`POST /api/v1/devices/enrol`) with a `device_id` it generated once per installation (UUID),
its `platform` (`android | ios | web`), OS and app version. `oe_identity_device` (tenant-level,
`BaseRecordMixin`, standard RLS policy, unique `(tenant_id, device_id)`) keeps user,
`enrolled_at`, `last_seen_at`, `last_sync_at`, status `active | revoked`, `revoked_by`,
`revoked_at`, `wipe_confirmed_at`, `wipe_secret_hash` (T-0080, migration `0018`). Who may see
or revoke which device is decided in
`device_api.py`; the table's policy itself is the standard tenant-level one.

- **Session binding.** Enrolment binds the login session (`sid`) to the device in the
  revocation store (`device:sid:<sid>`, and the device's session set `device:sids:<device_id>`,
  both with the refresh lifetime). A session belongs to at most one device; a `device_id`
  belongs to the account that enrolled it (another account: `409`).
- **Requests through a device.** Clients send `X-KERP-Device-Id` on every request. On each
  authenticated request (`CurrentPrincipal`) the session's bound device, or else the header if
  it names an enrolled device of the same user, is the request's device: `last_seen_at` is
  updated (at most once a minute) and the audit ledger records its id
  (`current_device_id`, ADR 0010). A header naming another device than the session's is
  `401`; a malformed header `400`. `devices.record_sync` sets `last_sync_at` (for the sync
  endpoints).
- **Revoke.** A `system_admin`, a PER-09 of a project the device's user belongs to, or the user
  (user decision Q4a). Others: `403` for a member of a shared project without PER-09, else
  `404`. The revoke marks the row revoked, commits it with its audit entry, and only then
  revokes every session bound to the device (`revoked:sid:*`) with a marker
  (`revoked:device-sid:*`): a failed commit never tells a still-active device to wipe, and if
  the kill fails after the commit, the per-request check of the bound device's row still
  refuses those sessions. When the revoker acts as PER-09 and the device's user also belongs to
  projects the revoker does not administer, the API answers
  `409 {code: cross_project_wipe_confirmation_required, last_sync_at}` until repeated with
  `confirm_cross_project_wipe: true` (the wipe deletes unsynced work of those projects too;
  planner mitigation for Q4a). The other projects are looked up server-side
  (`access.user_has_projects_beyond`, a yes/no answer) and never named in the answer. Audited
  on the tenant-level chain `device:<device_id>`: actor, `role` (`self` / `system_admin` /
  `per_09` with `project_ids`), `cross_project_warning`, `warning_confirmed`,
  `sessions_revoked` (the sessions bound at the revoke). Revoking again changes nothing and is
  not audited again.
- **The wipe signal.** Every later call from a revoked device is `401
  {"detail": "Device revoked", "code": "DEVICE_REVOKED"}`: an access token or refresh/unlock of
  a session killed by the revoke, and any call (login, magic-link redeem, refresh, unlock,
  authenticated request, enrolment) naming the revoked device in `X-KERP-Device-Id` (that
  session is then killed too). Sign-in names a revoked device this way only once the
  credentials are right (*Device hardening* below). The client wipes its local store (records,
  outbox, media, keys; T-0143) and calls `POST /api/v1/devices/{device_id}/wipe-confirmed`
  without a token (its sessions are dead) but with its wipe secret: `204`, `wipe_confirmed_at`
  set once and audited. A plain `401` (expired, logged out) never means "wipe". A wiped client
  enrols again under a new `device_id`; a revoked id stays revoked.
- **Enrolment audit.** The first enrolment of a device is appended to `device:<device_id>`
  (never with the wipe secret).

### Device hardening (T-0080, diff §4.3; TD-286, TD-291)

- **Per-device wipe secret.** The enrolment that creates the device row issues a wipe secret:
  32 bytes from the OS CSPRNG (256 bits), URL-safe base64 (`devices.new_wipe_secret`). It is in
  that one `201` response (`wipe_secret`) and never again: re-enrolment answers
  `wipe_secret: null`, and no list or other response carries it. The row keeps only its
  SHA-256 (`wipe_secret_hash`, hex; a 256-bit random secret needs no slow hash); the secret is
  never written to the database, the audit ledger or the logs. A device enrolled before
  migration `0018` has no secret, so it **cannot confirm its wipe** (it gets the uniform
  refusal); acceptable because no device is in production before the pilot (planner default).
- **Uniform wipe-confirmed answer.** `POST /api/v1/devices/{device_id}/wipe-confirmed`
  `{wipe_secret}` records `wipe_confirmed_at` (once) only for a revoked device with the right
  secret. An unknown id, an active device, a device without a secret, and a wrong or missing
  secret (no body, `{}`) all get the same `404 {"detail": "Not Found"}` after the same work:
  one lookup, one SHA-256 of the presented secret, one `hmac.compare_digest` against the
  stored digest (a fixed dummy digest when there is none). The OpenAPI lists only `204` and
  that `404` (plus FastAPI's `422` for a malformed body, which does not depend on the device).
  Confirmations (`wipe_confirmed`) and refusals (`wipe_refused` with `reason`
  `unknown_device | no_secret | wrong_secret | not_revoked`, for admins only) are appended to
  `device:<device_id>`; never the secret.
- **Credentials before the device.** Password login authenticates first; magic-link redeem
  validates the link **without spending it** (`RevocationStore.peek_magic_link`). Wrong
  credentials, or an unknown, expired or spent link, get the usual uniform `401` whatever
  `X-KERP-Device-Id` names (unknown, active or revoked): no device-status oracle to an
  unauthenticated caller. With valid credentials a revoked device is still
  `401 {code: DEVICE_REVOKED}` so the client wipes; a valid magic link is then left unspent
  (the same link redeems later from an active device) and is spent only once the device check
  passes. Refresh, PIN unlock, authenticated requests and enrolment are unchanged (their
  caller already holds a session). The SSO exchange gets the same rule with T-0054.
- **Client contract (T-0143).** On `201` from enrolment the client stores `wipe_secret` in the
  keystore-backed secure store **per account** (with that account's local store, keyed by the
  `device_id`); it never logs it or sends it anywhere else. On `DEVICE_REVOKED` it wipes the
  account's local store (records, outbox, media, keys) **except** the wipe secret, then calls
  `POST /api/v1/devices/{device_id}/wipe-confirmed` with `{"wipe_secret": "<secret>"}` (no
  token), retrying on network errors and `5xx`; `204` or `404` ends it, and the secret is then
  deleted. A `200` re-enrolment (`wipe_secret: null`) keeps the stored secret; a client that
  has lost it enrols under a new `device_id` after its next wipe.

## Field workers: magic link + the device PIN, hybrid (T-0009, T-0038)

`field.py`, `lockout.py`. Diff §4.2 *Field PIN, hybrid* (TD-135, decision 8a). The PIN
belongs to the **enrolled device**, not to a server session, and it never authenticates on
its own: a PIN alone never starts or renews a session.

- **Set once, kept.** The 6-digit PIN is set at enrolment (`POST /devices/enrol` with
  `pin`; weak PINs such as `111111` / `123456` are refused, `422`). Its scrypt hash is on the
  device row (`pin_hash`, `pin_set_at`; migration `0019`); nothing about it is kept per
  session. Enrolment never replaces a PIN (`409 {code: PIN_ALREADY_SET}`). A magic-link or
  password sign-in starts an ordinary session; on an enrolled device of the user
  (`X-KERP-Device-Id`) the session is bound to the device at once, and the PIN stays.
- **Change.** `PUT /devices/{device_id}/pin` from a session bound to the device (so after an
  online sign-in there; the old PIN is not needed, so a forgotten PIN is reset by signing in).
  Audited as `pin_changed` on `device:<device_id>` (enrolment with a PIN: `pin_set`); never
  the PIN.
- **Renewal after idle.** `/auth/refresh` rotates a session of a device with a PIN normally
  while its last renewal (the refresh token's `iat`: sign-in, rotation or unlock) is less
  than `KERP_PIN_IDLE_MINUTES` ago (default 30). After a longer idle it answers
  `401 {"detail": "PIN required", "code": "PIN_REQUIRED"}` and leaves the token unspent;
  `/auth/pin/unlock` with that refresh token **and** the PIN renews the session (any time,
  not only after idle). Sessions not bound to a device, or of a device without a PIN, are
  never PIN-gated.
- **Which device.** The session's device is the token's `did` claim (set when the session is
  bound at sign-in, and carried by every rotation), else the store's binding (a session
  bound by its enrolment, until its first rotation). The gate therefore does not depend on a
  Redis key once the claim exists (TD-142, resolved). Open residual, TD-303: the first
  refresh token of a session bound by enrolment rather than at sign-in relies on the
  binding until its first rotation, so clients send `X-KERP-Device-Id` at sign-in and Redis
  runs with `noeviction`. The claim also answers `DEVICE_REVOKED` on refresh and unlock
  without the binding. A session naming a device that is no longer an active
  device of its user is refused.
- **Answers of `/auth/pin/unlock`** (only after the refresh token checked out): `200` new
  pair; `401 {code: PIN_INVALID}` wrong PIN (ask again); `401 {code: PIN_EXHAUSTED}` the
  10th wrong PIN and every attempt after it (sign in again on the device); `429` +
  `Retry-After` while locked; plain `401` for a session without a device PIN or a dead
  token; `DEVICE_REVOKED` as everywhere.

**PIN lockout per device.** The numbers are T-0009's: 4 tries back to back, then 30 s
doubling to 15 min; the **10th wrong PIN ends PIN unlock on that device** until a sign-in
there: a new session bound to the device (sign-in naming it, or the first enrolment of a
session with neither a `did` claim nor a store binding) or a PIN change clears the counter.
Re-enrolling from a session already bound clears nothing, even if its binding was evicted. Nothing is revoked: not the session (its access token keeps
working until it expires), not the device; PIN failures never trigger a wipe. From the 10th
failure on no lock is armed (`LockoutPolicy.lock_after`), so later attempts get
`PIN_EXHAUSTED`, not `429`. **Offline failures:** the device counts wrong PINs made offline
with the same numbers and reports them with its next unlock as `failed_offline` (0 to 1000,
clamped to 10); they are added to the device's counter before the attempt, in the same
atomic step, so the attempt's number and lock follow the same escalation. They are applied
only when the attempt runs: a `429` applies nothing, so the client keeps the count until any
other answer.

**Progressive lockout** (Redis, shared by every worker; replaces Inspection-ERP's in-memory
`ratelimit.py`): an attempt is counted before the secret is checked; once the immediate tries
are used up, each failure locks the subject and attempts during the lock are refused with
`429` + `Retry-After` without checking the secret; a right answer clears the count. Check,
count and lock are one atomic store operation (`begin_attempt`: WATCH/MULTI on Redis), so a
burst cannot slip past a lock another request is arming (TD-191). Subjects
are hashed into the keys. Run Redis with `maxmemory-policy noeviction` all the same: an
evicted counter forgets failures (and an evicted session binding is covered by the `did`
claim only after the session's first rotation).

| Scope | Key | Immediate tries | Then locked | Hard cap |
|---|---|---|---|---|
| PIN unlock | tenant + device | 4 (plus reported offline failures) | 30 s doubling to 15 min | 10th wrong PIN ends PIN unlock on the device until a sign-in there; nothing revoked (counter window = refresh lifetime + 1 day: it outlives the session it is counted in, and a new session on the device clears it) |
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
(`access`|`refresh`), `iat`, `nbf`, `exp`; `did` (the session's bound device, when known:
T-0038) is optional. Header: `kid` (RFC 7638 thumbprint), `alg`
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

**System admin flag and user binding (T-0014, edge spec §6 amendments).** In the same short
unit of work that reads the memberships, `resolve_request_scope` (bound to the caller, so the
own-rows policies apply, TD-152) asks the registered admin-grant lookup for the caller's own
grants and binds `system_admin` as `TenancyContext.is_system_admin` → `app.is_system_admin`;
the token's `sub` is bound as `app.current_user_id`, and every project of the caller's
memberships as `app.member_project_ids`. None of these is ever read from a token claim, request
parameter or header. Core imports no module: the admin module registers its lookup with
`register_admin_grant_lookup` at import (no lookup → nobody is a System admin). Neither admin
grant is a persona or adds a capability (no `A`/`S`, TD-151). `sod_warning(personas)` flags
PER-09 together with a persona that has `A` on one project (TD-151, derived from `PERSONAS`).

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
  modules. `ProjectAccess.has` is true for `V` only and `can_release_hold` is never true; it has
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

Project-scoped endpoints bind the request session with
`projects.service.bind_project(session, principal, grants, project_id, require=Capability.X)`
(T-0217): no membership → `404`; a member without `require` → `403`; then the session is
rebound to the project (scope from the memberships, never the request) and a deleted project is
`404`. It returns the caller's `ProjectAccess`, for further decisions such as
`access.can_release_hold(kind, site_node_id=...)` once the hold's site is known.

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
| `KERP_PIN_IDLE_MINUTES` | `30` | 1–1440 and at least `KERP_ACCESS_TOKEN_MINUTES` (else startup fails); a session of a device with a PIN idle this long needs the PIN to renew (*Field workers*) |
| `KERP_DEFAULT_TENANT_ID` | unset | The deployment's one tenant (TD-120); required in production |
| `KERP_INTERNAL_EMAIL_DOMAINS` | empty | Comma-separated e-mail domains of internal staff (exact match, no wildcards; see *Internal staff*); a malformed entry refuses startup |
| `KERP_WEB_ORIGIN` | unset | Web SPA origin allowed on the cookie endpoints; unset = the API's own origin as the request sees it (behind a TLS-terminating proxy set this, or trust the proxy with `FORWARDED_ALLOW_IPS`); required with `KERP_NATIVE_APP_ORIGINS` |
| `KERP_NATIVE_APP_ORIGINS` | empty | Native-app origins that get the refresh token in the body, and the API's CORS allow-list (*API CORS and the native-origin allow-list*) |
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
