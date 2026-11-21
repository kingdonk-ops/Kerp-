# app/core/auth — users, asymmetric JWTs, JWKS, revocation (T-0008), field-worker auth (T-0009, T-0038), device registry (T-0043), Entra ID SSO (T-0054), password policy + self-service reset (T-0077)

This is the identity module (`module: identity` in the tracker): it lives in `app/core/` because
every request's authentication depends on it (T-0008, T-0010 notes).

Diff §4 "Auth hardening". Per ADR 0001: scrypt hashing and the credential → tenant → RLS →
user dependency shape are ported from Inspection-ERP `core/auth/`; the token layer is
rewritten (HS256 → RS256/EdDSA + JWKS + `jti` revocation in Redis).

| File | What |
|---|---|
| `passwords.py` | scrypt hash/verify (stdlib, constant-time, parameters stored in the hash) |
| `password_policy.py`, `data/common_passwords.txt` | The one password policy (T-0077): `check_password` (minimum length, NFKC, bundled blocklist + patterns + context words, optional breach lookup), codes `PASSWORD_TOO_SHORT` / `PASSWORD_TOO_LONG` / `PASSWORD_TOO_COMMON` (*Passwords*) |
| `password_reset.py`, `password_reset_router.py` | Self-service password reset by e-mail (T-0077): `PasswordResetService` (neutral request, post-response issue, single-use hashed token, confirm), endpoints `/auth/password-reset/{request,confirm}` (*Self-service password reset*) |
| `keys.py` | Ed25519 / RSA signing key, retired verification keys, RFC 7638 `kid`, JWKS document |
| `tokens.py` | `TokenService`: issue/verify access (15 min) and refresh tokens |
| `revocation.py` | `RevocationStore`: Redis (prod) / in-memory (dev, tests) — `jti` + session blacklist, one-time refresh tokens |
| `runtime.py` | `AuthRuntime` (keys + tokens + store) built lazily from settings, cached on `app.state.auth` |
| `bearer.py` | `get_bearer`: verifies the `Authorization: Bearer` token once per request (no DB) |
| `deps.py` | `CurrentPrincipal` (401 unless a valid token of an active user), `require_claims` |
| `providers.py` | `AuthProvider` seam (generic over the credentials) + `LocalPasswordProvider` |
| `entra.py` | `EntraOidcProvider` (the second provider, T-0054): tenant-pinned config, ID-token verification, `EntraJwks` (cached, rate-limited refetch), member rule, verified-e-mail matching + `oid` link, `HttpEntraTransport` (stdlib HTTPS), refusal reasons → client codes |
| `sso.py`, `sso_router.py` | SSO flow state in the store (`state` entries, native exchange codes, `acct_claim_missing` marker), the `auth:sso:entra` audit chain; endpoints `/auth/config`, `/auth/sso/entra/{start,callback,exchange}`, `/.well-known/assetlinks.json` (T-0054) |
| `service.py` | `AuthService` login / refresh / logout / magic link / PIN unlock; `start_session` (the one way any provider's sign-in becomes a KERP session); `create_user`, `set_password`, `set_user_password` (the one way a password is set, under the policy: T-0077); user reads for the admin module (`list_users`, `get_user`, `users_by_id`, `find_user_by_email`, T-0014; `user_counts` for the System console stats, T-0219); internal staff rule (`is_internal_user`, T-0049) |
| `field.py` | Magic-link tokens + delivery seam, PIN rules and the `PIN_REQUIRED` / `PIN_INVALID` / `PIN_EXHAUSTED` answers (T-0009, T-0038) |
| `magic_link.py` | Magic links for Field Workers only (T-0057): `magic_link_eligible` (PER-14 via `load_grants`, plus registered providers), the eligibility extension point (`register_magic_link_eligibility`), `MagicLinkJob` (the post-response half of a request), refusal reason `magic_link_not_field_worker` |
| `post_auth.py` | The post-authentication hook (T-0052): what a sign-in does with an `invite_token`. Empty by default; the module owning invites registers it (*Post-authentication hook*) |
| `lockout.py` | `ProgressiveLockout` + policies, Redis-backed (T-0009); offline failures counted with `extra` (T-0038) |
| `models.py` | `auth_user` table (tenant-level rows, RLS) — migration `0003`, internal staff flag + Entra link in `0013` (T-0049, ADR 0009); `auth_project_membership` (T-0010) — migration `0006`, persona CHECK widened for PER-16 in `0012` (T-0076), membership-based RLS in `0015` (T-0014), grant sources (`source_type`, `source_id`) in `0025` (T-0073, ADR 0008); `oe_identity_device` (T-0043) — migration `0016`, `wipe_secret_hash` in `0018` (T-0080), `pin_hash` + `pin_set_at` in `0019` (T-0038) |
| `memberships.py` | The membership store's write side for **individual** (`direct`) rows (`project_grants`, `set_user_grants`, `revoke_project`; revoke = soft delete, re-grant revives) and the member list with sources (`project_member_grants`), used by the admin module (T-0014) and the projects module (creator PER-09, revoke on delete; T-0039) |
| `team_grants.py` | Team-derived grants (T-0073, ADR 0008): `sync_team_link_grants`, `revoke_team_link_grants`, `team_link_grants` for the teams module, and the startup check (below) |
| `access.py` | Persona matrix, capability tiers, SoD, scope resolution, `ProjectAccess` (`has`, `can_release_hold`) (T-0010); admin-grant lookup seam, `is_system_admin` binding, `CallerIsSystemAdmin`, `sod_warning` (T-0014); `system_admin` capabilities on every project, `system_admin_holders` (T-0275); `CallerAdminGrants` (the caller's own `system_admin` / `create_projects`, cached with the scope; T-0039) |
| `devices.py` | Device registry (`oe_identity_device`): enrol + session binding, `resolve_request_device` (last seen, revoked → `DEVICE_REVOKED`), revoke, wipe secret + uniform wipe confirmation (T-0080), `record_sync` for the sync endpoints (T-0043); `device_counts` (System console stats, T-0219); the device PIN (`set_pin`, `pin_device`, T-0038) |
| `device_api.py` | Device endpoints: enrol (with the PIN), PIN change, own / per-user lists, revoke (self, `system_admin`, PER-09 of a shared project; cross-project wipe warning), wipe-confirmed with the wipe secret (T-0043, T-0080, T-0038) |
| `device_signal.py` | `X-KERP-Device-Id` header, `DeviceRevokedError` → `401` with `code: DEVICE_REVOKED` (no database) |
| `router.py`, `schemas.py` | HTTP endpoints below |

## Endpoints

| Method + path | Auth | Result |
|---|---|---|
| `GET /.well-known/jwks.json` | none | `{"keys": [...]}` public JWKs (active + retired), `Cache-Control: public, max-age=300` |
| `POST /api/v1/auth/login` | none | `{email, password, invite_token?}` → `TokenResponse` (`no-store`) + refresh cookie (native app: token in the body); a `tenant_id` field is `422`; `429` when locked |
| `POST /api/v1/auth/refresh` | refresh cookie + Origin (native app: `{refresh_token}` body) | new `TokenResponse` + cookie (rotation); `401 {code: PIN_REQUIRED}` for a session of a device with a PIN idle `KERP_PIN_IDLE_MINUTES` or longer (token not spent); `401` for ended sessions; `403` cross-origin |
| `POST /api/v1/auth/logout` | bearer | `204`; revokes the token and its whole login session; clears the refresh cookie |
| `GET /api/v1/auth/me` | bearer | `{id, tenant_id, email, display_name, is_internal}` (`is_internal` read-only, below) |
| `GET /api/v1/auth/config` | none | `{password: {available: true}, sso: {provider: "entra", preferred: true, start_url} \| null}` (*Entra ID SSO*) |
| `GET /api/v1/auth/sso/entra/start` | none | `?device_id&app_challenge` → `302` to the tenant's authorize endpoint + pre-auth cookie; `404` without SSO. An `invite_token` in the query is ignored (never in a URL) |
| `POST /api/v1/auth/sso/entra/start` | none | a top-level form post (`application/x-www-form-urlencoded`: `invite_token`, `device_id?`, `app_challenge?`) → `303` to the authorize endpoint + pre-auth cookie; the sign-in then accepts the invite (T-0052); `415` other content types, `422` malformed fields |
| `GET /api/v1/auth/sso/entra/callback` | pre-auth cookie | `303` to the web landing (refresh cookie set) or the App Link (exchange code); refusals `?error=<code>` |
| `POST /api/v1/auth/sso/entra/exchange` | native origin | `{code, verifier, device_id}` → `TokenResponse` with `refresh_token` (native body); `401` uniform; `401 {code: DEVICE_REVOKED}`; `403` other origins |
| `GET /.well-known/assetlinks.json` | none | Digital Asset Links of the Android app (from configuration); `404` unconfigured |
| `POST /api/v1/auth/magic-link` | none | `{email, invite_token?}` → always the same `202`; after the response, sends a 15-min one-time link to an active **Field Worker** (PER-14) only (*Magic links for Field Workers only*); `429` when throttled |
| `POST /api/v1/auth/magic-link/redeem` | none | `{token}` → `TokenResponse` + cookie of a new session (on an enrolled device: bound to it, PIN kept); eligibility re-checked, else the neutral `401` |
| `POST /api/v1/auth/password-reset/request` | none | `{email}` → always the same `202` (unknown, deactivated and active accounts alike); after the response, e-mails an active account the single-use link `/reset-password#<token>` (*Self-service password reset*); `429` + `Retry-After` when the client or the mailbox is throttled |
| `POST /api/v1/auth/password-reset/confirm` | none | `{token, new_password}` → `200 {status: "password_changed"}` (no tokens, nobody signed in; every session of the user revoked); `410 {code: RESET_LINK_INVALID}` for any dead link; `422 {detail, code, min_length, max_length}` when the policy refuses the password (link still usable); `429` when throttled |
| `POST /api/v1/auth/pin/unlock` | refresh cookie + Origin (native app: `refresh_token` in the body) | `{pin, failed_offline?}` → rotated `TokenResponse` + cookie; `401 {code: PIN_INVALID \| PIN_EXHAUSTED}`; `429` when locked; `403` cross-origin |
| `POST /api/v1/devices/enrol` | bearer | `{device_id, platform, os_version, app_version, pin?}` → device + `wipe_secret`; `201` new (the only response carrying the secret), `200` known (`wipe_secret: null`); binds the session to the device; sets the PIN once; `409` another account's id, a session already bound elsewhere, or `code: PIN_ALREADY_SET` |
| `PUT /api/v1/devices/{device_id}/pin` | bearer (a session bound to the device) | `{pin}` → `204`; a new device PIN (the old one is not needed); `403` another session, `404` not the caller's active device |
| `GET /api/v1/devices` | bearer | the caller's devices (last seen, last sync, status) |
| `GET /api/v1/users/{user_id}/devices` | bearer | a user's devices: the user, a `system_admin`, or a PER-09 of a project the user belongs to; `403` a shared project without PER-09; `404` otherwise |
| `POST /api/v1/devices/{device_id}/revoke` | bearer | `{confirm_cross_project_wipe?}` → revoked device + `role` + `cross_project_warning`; same callers as above; `409` until a PER-09 confirms the cross-project wipe |
| `POST /api/v1/devices/{device_id}/wipe-confirmed` | none | `{wipe_secret}` → `204`: the revoked device reports its wipe; every refusal (unknown id, active device, wrong or missing secret) is the same `404 {"detail": "Not Found"}` (T-0080); `wipe_secret` at most 128 characters (`422`); per client address `WIPE_CONFIRM_POLICY` (11 attempts back to back, then the doubling lock, 1 h window; the first confirmation of a device clears it, a repeat does not) answers `429` + `Retry-After` before any lookup or audit entry (T-0212) |

`TokenResponse` is `{access_token, token_type, expires_in, refresh_expires_in, session_expires_at}`
(`session_expires_at`: the session's end, sign-in + 12 h, the same on every rotation and PIN
unlock; a field client starts its offline grace there, diff §4.2, T-0038): no response
body carries the refresh token (below), except for an allow-listed native app
(`refresh_token`, *Native app origins*). Every token endpoint and every authenticated request
reads `X-KERP-Device-Id`; a revoked device is `401` with `code: DEVICE_REVOKED` (*Devices*).
Sign-in (login, magic-link redeem) checks the credentials first: only then is the device
checked (T-0080, *Device hardening*). A sign-in that carried an `invite_token` adds
`joined_project_id` or `invite_error` (*Post-authentication hook*).

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

## Entra ID SSO, identity only (T-0054, diff §4.5, TD-043)

`entra.py`, `sso.py`, `sso_router.py`. A second `AuthProvider` (`EntraOidcProvider`) for
**company staff**: it proves who a member of the company Entra tenant is, and nothing else.
Success issues the **same KERP session** as a password sign-in (`AuthService.start_session`:
15-minute access token, refresh ending 12 h after sign-in, `session_expires_at`, device
binding). External users never use it.

### App registration (one per deployment)

- **Single-tenant** (accounts in this organisational directory only).
- **One web redirect URI**: `https://<deployment>/api/v1/auth/sso/entra/callback` (the Android
  build uses it too, *Native handoff*). Set the same value as `KERP_ENTRA_REDIRECT_URI`.
- **Scopes** `openid profile email` only. **No API permissions**, no group or app-role claims.
- **Optional ID-token claims `email`, `acct`, `xms_edov`** (Token configuration → Add optional
  claim → ID). **Without `acct` every SSO sign-in is refused** (`acct_claim_missing`, below).
- A client secret (a certificate credential later: TD-322) stored in the secret store
  and injected as `KERP_ENTRA_CLIENT_SECRET`. Never in the repo.

SSO is offered when `KERP_ENTRA_TENANT_ID` and `KERP_ENTRA_CLIENT_ID` are both set (and the
deployment tenant is). The authority and issuer are pinned to
`https://login.microsoftonline.com/<KERP_ENTRA_TENANT_ID>/v2.0`; the settings accept only a
GUID, so `common` / `organizations` can never be configured. With SSO enabled the server logs a
start-up warning naming the required optional claims.

### Flow

1. `GET /api/v1/auth/config` tells the sign-in screen what to offer: with SSO,
   `sso: {provider: "entra", preferred: true, start_url}`; `password: {available: true}`
   always. **SSO is promoted, never enforced**: there is no SSO-only mode, and password login
   works for every active account with a password, internal and Entra-linked ones included.
2. `GET /api/v1/auth/sso/entra/start` (top-level navigation) creates `state`, `nonce` and a
   PKCE S256 verifier (256 random bits each) and stores them in Redis under
   `sha256(state)` for 10 minutes, single use, with the enrolling `device_id` and, for the
   native app, its `app_challenge` (the S256 hash of the app's own verifier). A sign-in that
   accepts a project invite starts with a **form post** of the same path instead, so the
   `invite_token` travels in the body and never in a URL (T-0052); the state entry then also
   holds the invite's reference (never the token), and after the sign-in the landing gets
   `joined_project_id` or `invite_error` next to `status=signed_in` (the native app: in the
   exchange's token response). It sets the random pre-auth cookie `kerp_sso_preauth`
   (`HttpOnly; Secure; SameSite=Lax; Path=/api/v1/auth/sso; Max-Age=600`), whose SHA-256 is
   in the state entry, and redirects (`302`) to the tenant's authorize endpoint
   (`response_type=code`, `response_mode=query`, `code_challenge_method=S256`).
3. `GET /api/v1/auth/sso/entra/callback` takes the state entry (`GETDEL`: unknown, reused or
   expired → refused), then compares the pre-auth cookie's hash (missing or foreign → refused,
   the state already consumed: login-CSRF binding), and clears the cookie on every answer. It
   redeems the code with the verifier and the client secret, then verifies the ID token:
   RS256 against the tenant JWKS, `iss` = the pinned issuer, `tid` = the configured tenant,
   `aud` = the client id (one string), `exp` / `nbf` / `iat` with at most 2 minutes of leeway,
   `nonce` = the stored one. Then the member rule and the matching below.
4. **Web**: a revoked `device_id` from the state is refused only now (credentials first,
   T-0080); otherwise a new session, the refresh cookie (`kerp_refresh`, as for password
   login) and `303` to `<KERP_WEB_ORIGIN or deployment>/login/sso-return?status=signed_in`.
   The SPA then calls `/auth/refresh` (cookie transport) for its access token; no token is ever
   in a URL. Landing page: `/login/sso-return` in the SPA (T-0055, `frontend/README.md`).
5. **Native** (the state has an `app_challenge`; `device_id` is then required): never a token
   in a redirect. A single-use **exchange code** (256 bits, stored as SHA-256, 60 s, bound to
   the user, the `device_id` and the app challenge) and `303` to the verified App Link
   `https://<deployment>/app/sso-return?code=…`. The app redeems it with
   `POST /api/v1/auth/sso/entra/exchange` `{code, verifier, device_id}` from an origin on
   `KERP_NATIVE_APP_ORIGINS` (else `403`, the code untouched) and gets the native body
   transport (`refresh_token` in the body). The code is spent on the first attempt
   (`GETDEL`): a second redemption, one after 60 s, a wrong verifier or another `device_id`
   all get the same `401 {"detail": "Not authenticated"}`; a reuse is audited
   (`exchange_code_reused`). **Device check after credentials** (T-0080, TD-291): only with
   the right code and verifier is the device looked at, so with bad credentials an unknown, an
   active and a revoked `device_id` get the same answer; with valid credentials a revoked device
   is `401 {code: DEVICE_REVOKED}`. The callback never checks the device of a native flow.

Refusals redirect to the same landing (web or App Link) with `?error=<code>`, a key the client
translates (`auth.sso.errors.<code>` in the SPA catalogue):

| `code` | Reasons (logged; never shown) | Message |
|---|---|---|
| `SSO_GUEST` | `guest` (`acct` ≠ 0), `acct_claim_missing`, `foreign_idp` (`idp` ≠ pinned issuer) | Sign in with the method from your invitation |
| `SSO_NO_ACCOUNT` | `no_account` | No KERP account for this e-mail, ask your administrator |
| `SSO_ACCOUNT_DEACTIVATED` | `account_deactivated` | Account deactivated |
| `SSO_EMAIL_UNVERIFIED` | `email_missing`, `email_unverified` | No verified e-mail on the Microsoft account |
| `SSO_ACCOUNT_LINKED_ELSEWHERE` | `oid_mismatch` | Linked to another Microsoft account |
| `DEVICE_REVOKED` | `device_revoked` (web flow with a revoked device) | The client wipes (*Devices*) |
| `SSO_FAILED` | everything else: `state_invalid`, `preauth_cookie_mismatch`, `provider_error`, `token_exchange_failed` (incl. a PKCE mismatch), `jwks_unavailable`, `id_token_*`, `nonce_mismatch` | Sign-in with Microsoft failed, try again |

### Members only, matching, no roles

- **Members only.** `acct` must be `0` (a missing `acct` is not proof: refused as
  `acct_claim_missing`); an `idp` claim different from the pinned issuer is a guest. Guests
  are never linked and never become internal; they use password (or a magic link as Field
  Workers).
- **Matching.** An account linked to the token's `oid` (`entra_oid`) signs in by `oid` only;
  the e-mail claim is then ignored. Otherwise the first sign-in matches an existing **active**
  account by the `email` claim (case-insensitive) **only when it is verified**: `xms_edov` is
  `true`, or its domain is on `KERP_INTERNAL_EMAIL_DOMAINS`. No `preferred_username` fallback;
  a missing or unverified `email` is refused. A match stores `entra_oid` / `entra_tid` (the
  link, audited `sso_linked`). An account linked to a different `oid` is refused; no account is
  refused (KERP never creates accounts from SSO); a deactivated account is refused.
- **No group-to-role mapping.** Group, app-role and directory-role claims are ignored. A
  successful sign-in recomputes `is_internal` (true: the account is linked) and nothing else
  about access: personas, PER-09, admin grants and teams stay in KERP.

### JWKS, logging, audit, health

- **JWKS.** Fetched from `…/<tenant>/discovery/v2.0/keys`, cached in the process for 24 h (a
  failed reload keeps the known keys). A token with an unknown `kid` triggers a refetch only
  when the deployment-wide limit allows (`SET NX` on `sso:entra:jwks-refetch`, 60 s, shared by
  every worker), so forged `kid`s cost Microsoft at most one request a minute.
- **No token contents** are logged or audited: the security log (`kerp.auth.sso`) gets
  `entra sso sign-in refused: <reason>` with `sso_reason` / `sso_flow`. Transport errors carry
  the HTTP status only.
- **Audit ledger**, tenant-level chain `auth:sso:entra`: `sso_linked`, `sso_signed_in`,
  `sso_verified` (native callback, before the exchange) and `sso_refused` with `reason` for
  every refusal of a verified identity (guest, unverified e-mail, no account, deactivated,
  `oid` mismatch, revoked device, exchange verifier / device mismatch, code reuse). Refusals
  before an identity is proven (bad state, cookie or token) go to the security log only, so
  unauthenticated requests cannot grow the ledger (as for wipe confirmations, T-0212).
- **`acct` prerequisite.** A refusal for a missing `acct` sets a 24-hour marker; `/health` then
  answers `warnings: ["sso_acct_claim_missing"]`. The deploy docs (`backend/README.md`) say that
  without the `acct` optional claim every SSO sign-in is refused.

### Android App Link (native handoff)

`GET /.well-known/assetlinks.json` serves, from `KERP_ANDROID_APP_PACKAGE` and
`KERP_ANDROID_APP_CERT_FINGERPRINTS`, the statement
`delegate_permission/common.handle_all_urls` for that package and certificate(s), so Android
verifies the App Link `https://<deployment>/app/sso-return` (host = the redirect URI's host).
Never a custom URL scheme. The app (T-0143) opens `start_url?device_id=…&app_challenge=…` in
Custom Tabs, keeps its verifier in the keystore-backed store, and redeems the code from the App
Link with `/exchange`.

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
  caller already holds a session). The SSO exchange follows the same rule (T-0054, *Entra ID SSO*).
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

Unknown addresses lock exactly like real ones. Link delivery is the `MagicLinkSender` seam;
the default `MailMagicLinkSender` queues the `auth.magic_link` e-mail through the outbound
e-mail service in the post-response unit of work (below) (`app/core/mail/`, T-0058): sent by the worker
after the commit, the token only in the link fragment (`<KERP_WEB_ORIGIN>/magic-link#<token>`),
never in the send log. The development log transport prints the e-mail; production refuses to
start without SES.

## Magic links for Field Workers only (T-0057)

`magic_link.py`. Diff §4.2 *Magic links for Field Workers only* (product owner, 2026-11-14;
TD-136). Only an **active** account holding **PER-14 Field Worker** on at least one project
may sign in by magic link. Everyone else (other personas only, no membership, a deactivated
account, an unknown address) signs in by password or, for internal staff, Entra ID SSO.

- **Eligibility** is one core function, `magic_link_eligible(session, candidate)`: the
  effective grants from `access.load_grants` (direct and team-derived rows alike, T-0073;
  soft-deleted rows excluded, and deleting a project revokes its memberships, so PER-14 on a
  deleted project never counts) contain PER-14, **or** a registered eligibility provider says
  yes. Providers are the extension point for reasons core does not own: the module that owns
  invites registers one at start-up (`register_magic_link_eligibility`, T-0052: a valid
  pending invite naming PER-14 for the account's e-mail; the admin module's
  `invites.invited_field_worker`). Core itself registers none. Core reads no module table
  and never imports `app.modules`.
- **Neutral answer.** `POST /auth/magic-link` answers the same `202`, with the same (empty)
  body and headers, for an eligible account, an ineligible or deactivated one, an unknown
  address and another tenant's address. For anyone but an eligible account no link is stored
  and no e-mail is queued.
- **No timing oracle.** The request does the same work on every path: it counts the attempt
  in the per-mailbox lockout (unchanged, T-0068 / TD-224) and schedules one `MagicLinkJob`
  (tenant + normalised e-mail), identical for every address. Everything that differs runs in
  `AuthService.issue_magic_link` **after the response has been sent** (a FastAPI post-response
  task in the API process, its own unit of work): the account lookup, the eligibility check,
  the token and its store write, the `auth.magic_link` e-mail (send-log row; the SAQ
  interactive queue delivers it after that unit of work commits) and the refusal audit. A
  failure there is logged, never raised (the client has its `202`). A crash of the API
  process between the response and the task loses that one request; the user asks again.
- **Refusal record.** An active account that is not eligible is logged (`kerp.auth.magic_link`,
  user id + reason) and audited on the tenant's `auth:magic-link` chain as
  `{"action": "magic_link_refused", "reason": "magic_link_not_field_worker", "user_id": ...}`:
  never the e-mail or a token. Unknown and deactivated addresses leave no record.
- **Redeem re-checks.** `POST /auth/magic-link/redeem` checks eligibility again after the
  link is spent: a link issued while the user held PER-14 is refused with the neutral invalid
  link answer (`401`, as for a garbage link) once the last PER-14 grant was removed, the
  project deleted, or the account deactivated.

## Passwords (T-0077, diff §4.2 *Local passwords*)

One policy for every password that is set: reset, first password at invite acceptance
(T-0059), change, and accounts created with a password. NIST SP 800-63B rev. 4 style,
product-owner decision 2026-11-14.

- **Length only.** At least `KERP_PASSWORD_MIN_LENGTH` characters (default **8**; the settings
  refuse a value below 8, so the app does not start), at most 256. Every printable character,
  spaces and Unicode are allowed. No composition rules, no expiry, no hints or questions.
- **NFKC.** The password is NFKC-normalised before it is checked and before it is hashed
  (scrypt, unchanged), so composed / decomposed accents and full-width forms are one password.
  Length counts normalised characters. Sign-in checks the NFKC form of what was typed and,
  when that differs, also the text exactly as typed (a password stored before T-0077); an
  unknown account burns the same number of scrypt rounds.
- **Blocklist.** `data/common_passwords.txt` (bundled; generic, publicly known weak
  passwords), obvious patterns (one character or a unit of up to 4 repeated; counting and
  keyboard runs in either direction) and **context words**: the account's e-mail, its local
  part (also without separators), `kerp` and the deployment name (`KERP_APP_NAME`). A listed
  or context word with up to 6 leading / trailing digits or symbols (`Password1!`,
  `kerp2026`, `Summer2026!`) is refused too; a listed word inside a longer passphrase is fine.
- **Optional breach lookup.** `KERP_PASSWORD_BREACH_CHECK=true` asks the k-anonymity range API
  (`api.pwnedpasswords.com/range/<first 5 hex of the SHA-1>`, with response padding; stdlib
  HTTPS, 3 s timeout) whether the password is in a known breach. Only those 5 characters
  leave the deployment. Any failure (network, timeout, malformed answer) is logged and
  **never blocks** the password. Off by default; needs outbound HTTPS to that host.
- **Rejections say why**: `PasswordPolicyError.code` is `PASSWORD_TOO_SHORT`,
  `PASSWORD_TOO_LONG` (over 256) or `PASSWORD_TOO_COMMON` (blocklist, pattern, context word,
  breached); the message never contains the password. Endpoints answer `422 {detail, code,
  min_length, max_length}`; the client translates by `code` (screens: T-0078).

**Public API.** Modules never hash a password or write `auth_user` (CLAUDE.md §6):

```python
from app.core.auth.password_policy import PasswordPolicyError, check_password
from app.core.auth.service import set_user_password

check_password(password, email=email)            # validate only → the normalised password
set_user_password(session, user_id, password)    # validate + scrypt + password_changed_at
```

**First password + session (T-0059, diff §4.1).** A module that has proved a request comes
from an account's mailbox (the admin module: both tokens of the e-mailed invite) calls

```python
pair = AuthService(runtime).start_session_with_first_password(session, user_id, password)
```

in its own unit of work. Core checks the policy (`PasswordPolicyError`: nothing changed),
hashes, locks the account row and reads it again (`lock_user`), refuses a gone or
deactivated account (`AuthenticationError`), an account that already has credentials
(`CredentialsExistError`; `has_credentials(user)`: a password or a linked Entra ID identity)
and a revoked `device_id` (`DeviceRevokedError`), and only then stores the hash and issues
the normal session (`start_session`). A first password is never set over existing
credentials, so two concurrent calls cannot both succeed.

`set_user_password` (and `set_password(user, ...)` for code that already holds the `User`)
raises `PasswordPolicyError` and changes nothing when the policy refuses; `OutOfScopeError`
for an account outside the session's tenant. `create_user(..., password=...)` applies the same
policy. Setting a password stamps `password_changed_at`: every access and refresh token
issued before it stops working at its next use (all devices; exact to the microsecond,
T-0037 / T-0061) and so does every magic link issued before it. Device enrolment and the
device PIN are not touched.

**A password change and token issuing are ordered by a row lock** (`service.lock_user`, PR #125
review). The change stamps `password_changed_at` while holding the user's row
(`SELECT ... FOR NO KEY UPDATE`; on SQLite, which has no row locks, a no-op write takes the
database's write lock). Every path that issues tokens reads the row again under the same lock
after the credential check and holds it until the tokens exist:

- `start_session` (password, magic link, SSO) locks and re-reads. For a password or magic-link
  sign-in it refuses (`401`) when the account was deactivated or `password_hash` /
  `password_changed_at` changed since the row was read, i.e. while scrypt was running. An SSO
  sign-in does not depend on the password, so it only takes the lock.
- Refresh and PIN unlock (`_rotate`) run `load_active_user(..., for_update=True)`.
- The post-authentication hook (T-0052, *Post-authentication hook*) commits mid-request,
  which releases the lock. It runs only after the tokens were issued, and never for a
  sign-in refused here, so an invite is not accepted off an old-password sign-in that lost
  against a reset.

So a token is either issued before the stamp (older `iat`: revoked by it) or after the change
committed (the issuing path saw the new row and refused or is a legitimate later sign-in). No
session started with the old password survives the change, however the requests interleave.
The stamp and `iat` come from the hosts' clocks: API hosts must be time-synchronised.

## Self-service password reset (T-0077, diff §4.2)

`password_reset.py`, `password_reset_router.py`.

- **Neutral request, no timing oracle.** `POST /auth/password-reset/request` answers the same
  `202` (empty body, same headers) for an unknown, a deactivated and an active account, and
  for another tenant's address. Inside the request it only counts the attempt in two
  lockouts (per client address, then per tenant + e-mail) and schedules one
  `PasswordResetJob` (tenant + normalised e-mail), identical on every path. Everything that
  differs runs in `PasswordResetService.issue` **after the response has been sent** (a FastAPI
  post-response task, its own unit of work, like magic links): the account lookup, the
  token, its store writes, the `auth.password_reset` e-mail (a send-log row; the SAQ
  interactive queue delivers it after that unit of work commits, T-0058) and the audit entry.
  Nothing is ever sent from a request. A failure there is logged, never raised. A crash of
  the API process between the response and the task loses that one request; the user asks
  again.
- **Who gets a link.** Every **active** account of the deployment's tenant, with or without
  a password (a brand-new invitee or an SSO-only account sets its first password this way).
  Unknown and deactivated addresses get nothing.
- **Token.** `secrets.token_urlsafe(32)` (256 random bits), only ever in the fragment of
  `<KERP_WEB_ORIGIN>/reset-password#<token>`. The store (Redis; in memory outside
  production) holds its SHA-256 only, TTL `KERP_PASSWORD_RESET_TTL_MINUTES` (default 60):
  `pwreset:token:<sha256>` → `{uid, tid}` and `pwreset:user:<tid>:<uid>` → the digest of the
  account's **one** live token. A new request overwrites the pointer and deletes the earlier
  token, so the earlier link is dead; a token the pointer does not name is dead whatever
  order concurrent requests ran in. The token is never in the send log, the audit ledger, a
  log line or the SAQ job arguments.
- **Confirm.** `POST /auth/password-reset/confirm {token, new_password}` looks the token up
  without spending it, checks the password against the policy (a refused password answers
  `422` with its `code` and **keeps the link usable**), then takes the token atomically
  (`GETDEL`: of concurrent confirmations exactly one gets `200`, the others `410`) and sets
  the password in the request's unit of work. That revokes **every session and refresh
  token** of the user on all devices and every outstanding magic link (*Passwords*), and
  deletes the pointer (no other reset link survives). Device enrolment and PIN stay; a device
  that is offline keeps unlocking locally within its offline grace (diff §4.2), and its old
  session is refused at its next server contact (`/auth/refresh`, `/auth/pin/unlock`: plain
  `401`, never `DEVICE_REVOKED`). The response is `200 {"status": "password_changed"}`,
  `Cache-Control: no-store`, **no tokens and no cookie**: the user signs in with the new
  password. An `auth.password_changed` notice is queued to the account's address.
- **One neutral `410`** (`{"detail": ..., "code": "RESET_LINK_INVALID"}`) for an unknown,
  used, expired or superseded token, a token of an account deactivated meanwhile, and a
  token whose attempts are used up; decided before the password is looked at.
- **Rate limits** (`lockout.py`; `429` + `Retry-After`, decided before any lookup):
  requests per tenant + e-mail with `MAGIC_LINK_REQUEST_POLICY` (the same per-mailbox cap as
  magic links, T-0068 / TD-224: 4 back to back, then 1 min doubling to 15 min over 1 h) on
  its own counter; requests per client address with `PASSWORD_RESET_CLIENT_POLICY` (20 back
  to back, then 30 s doubling to 15 min over 1 h); confirmations per client address with
  `PASSWORD_RESET_CONFIRM_CLIENT_POLICY` (11 back to back, then the same escalation; every
  refusal is audited, as for wipe confirmations); confirm attempts **per token** with
  `PASSWORD_RESET_TOKEN_POLICY` (only attempts with a live token count: 5 refused passwords
  back to back, then 30 s doubling; the 10th refused attempt kills the link).
- **Audit** on the tenant's `auth:password-reset` chain: `password_reset_requested`
  (`user_id`), `password_reset_confirmed` (`user_id`, `first_password`) and
  `password_reset_refused` with `stage` (`request` / `confirm`) and `reason`
  (`unknown_account`, `account_inactive`, `invalid_token`, `attempts_exhausted`, or the policy
  code), plus `user_id` when one is known. Never the token, the password or the e-mail
  address. A throttled call (`429`) is refused before any database work and is not audited.
- **Logs.** `kerp.auth.password_reset` logs user ids and reasons only. The JSON log formatter
  redacts credential-named fields and `token=...` / `"new_password": "..."` pairs
  (`app/core/logging.py`); `scrub_sentry_event` is the matching `before_send` hook for an
  error tracker (none is wired in yet).

## Post-authentication hook (T-0052, diff §4.1 Invites)

`post_auth.py`. `POST /auth/login`, `POST /auth/magic-link` and the form-posted
`/auth/sso/entra/start` accept an optional `invite_token` (body only, at most 512
characters). Invites belong to the admin module and core never imports `app.modules`
(`tests/test_auth_magic_link_field_workers.py` checks every file here), so acceptance runs
through a hook the admin module registers at start-up
(`register_post_authentication_hook`), like the magic-link eligibility provider:

- `reference(invite_token)` gives the opaque reference core carries instead of the token (the
  admin module: the token's SHA-256). Core computes it the moment the token arrives. The
  magic-link record (`inv`, next to `uid` / `tid` under the hashed link), the SSO `state`
  entry and the native exchange code hold the reference only; the magic-link URL holds
  neither.
- `after_authentication(session, user, reference)` runs in `AuthService.start_session`, the
  one place every sign-in (password, magic-link redeem, SSO callback, native exchange) becomes
  a session, right after the tokens are issued. Its outcome rides on the `TokenPair` and
  becomes `joined_project_id` or `invite_error` in the token response (the web SSO landing:
  query parameters).

**Empty by default**: with no hook registered a sign-in works as before and `invite_token` is
ignored. **An invite never blocks a sign-in**: a refused token is an `invite_error`; a hook
that raises is logged without the token or the reference, its work is rolled back (the
sign-in's own writes are committed before the hook runs) and the response carries
`invite_error: INVITE_UNAVAILABLE`. A refresh or a PIN unlock never runs the hook.

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
memberships (individual and team-derived, T-0073) as `app.member_project_ids`. None of these is ever read from a token claim, request
parameter or header. Core imports no module: the admin module registers its lookup with
`register_admin_grant_lookup` at import (no lookup → nobody is a System admin). Neither admin
grant is a persona, and neither ever adds `A`/`S` (TD-151). `sod_warning(personas,
system_admin=)` flags PER-09, or the user's `system_admin` grant, together with a persona that
has `A` on one project (TD-151, ADR 0016; derived from `PERSONAS`); `system_admin_holders`
answers which users hold `system_admin` (own rows through a side session bound to each user
when the caller's session cannot read them).

**System admin capabilities (ADR 0016 §4, T-0275).** `project_access(grants, project,
system_admin=)` takes the flag the request session was bound with (never the request): for a
`system_admin` the result has the Admin tier (`V`, `C`, `E`, `admin`, PER-09 equivalent) with
or without a membership, plus whatever its own personas there give. `A`, `S` and
`can_release_hold` come only from those personas. `acting_personas` adds PER-09 for persona-gated
configuration rules (it has no `A`); a `system_admin` is never site-filtered (`site_node_ids`
is `None`). `project_access` stays pure: callers load the project under the session scope
(`projects.service.bind_project`, `get_project`), so an unknown, deleted or other-tenant
project is 404 before any 403.

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

## Team-derived grants (T-0073, ADR 0008)

Diff §4.1 *Teams*: **effective access to a project = the user's individual memberships ∪ the
grants from the teams linked to it.** Core never imports the teams module, so the derived
grants live in `auth_project_membership` itself, keyed by source:

- `source_type` `direct` (an individual assignment, `source_id` NULL) or `team_link`
  (`source_id` = the team-project link id, no FK). CHECKs: the value set, and
  `(source_type = 'direct') = (source_id IS NULL)`.
- Unique `(tenant_id, member_project_id, user_id, source_type, source_id, persona,
  site_node_id)` `NULLS NOT DISTINCT`, soft-deleted rows included (a re-grant revives its
  row), and a partial unique index on live team rows `(tenant_id, member_project_id, user_id,
  source_id)`: one live persona per user and link. SQLite treats NULLs as distinct, so the
  write paths look a row up (deleted ones included) before inserting.
- `load_grants` reads every live row whatever its source; the same grant from both sources
  collapses into one `Grant`. `resolve_scope`, `project_access`, the allowed project list,
  `/api/v1/me/projects` and the membership-based RLS count derived grants with no change.
  Direct and derived rows never overwrite each other: removing one source leaves the other.

**The API the teams module calls** (`team_grants.py`; a tenant-level `system_admin` session
only, else `NotSystemAdminError`, mapped to 403 by the caller; RLS and the ORM write guard
refuse other sessions' writes to derived rows as well):

| Function | What |
|---|---|
| `sync_team_link_grants(session, *, link_id, project_id, persona, site_node_id, user_ids, confirm_sod=False, reason=None) -> None` | The live rows of `link_id` become exactly one per user in `user_ids` with that project, persona and site. Rows that are gone or carry an old project, persona or site are soft-deleted first (flushed), then new ones inserted or matching soft-deleted ones restored; unchanged rows are not rewritten. `user_ids` is stored as given, never filtered by account status (pass every non-deleted team member) |
| `revoke_team_link_grants(session, *, link_id, reason=None) -> None` | Soft-deletes every live row of the link |
| `team_link_grants(session) -> Mapping[UUID, frozenset[(user_id, project_id, persona, site_node_id)]]` | Every link id with live derived rows in the tenant, including ids the teams tables no longer know (orphans, for reconcile, T-0075) |

- **Link roles** (TD-251): PER-01 to PER-14 except PER-09, and PER-16 (`LINK_PERSONAS`). PER-09
  and PER-15 are individual only; they, an unknown persona, a PER-07 without a site, a site on
  any other persona, or an unknown user raise `ValueError`.
- **Separation of duties across sources.** A sync that would give a hold-authority persona
  (PER-02, PER-06, PER-07, PER-11) on the project to a user holding **individual** PER-09 there
  raises `SodConfirmationRequired` (`project_id`, `user_ids`) unless `confirm_sod=True`; only
  users who get a new or changed row count, so re-syncing confirmed rows asks for nothing. The
  other direction is the admin module's: assigning individual PER-09 to a user with a
  team-derived hold persona needs `confirm_sod` there (`sod_warning` over all of the user's
  grants on the project).
- **Audit.** Each call that changes rows appends one entry per affected project on chain
  `membership:<project_id>`: `action` (`team_link_sync` | `team_link_revoke`), `link_id`,
  `added` / `removed` user ids (a re-role lists the user in both), the persona and site, and
  `sod_warning` (with `sod_user_ids` when confirmed), and `reason` when the caller gives one
  (`"reconcile"` from `teams reconcile`, T-0075); the actor is the session user. A no-op
  writes nothing.
- **Deactivated users** keep their derived rows: a deactivated account cannot authenticate, so
  the rows grant nothing, and reactivation restores team access with no re-sync.
- **PER-09 member admin** (admin module): lists derived rows (`source: team`, the link id,
  `team_name` filled in by the admin module through the teams service, T-0013); PUT and DELETE change individual rows only;
  DELETE of a user whose access is only team-derived is 409 `managed_through_team`.
  `memberships.revoke_project` (project delete) refuses while derived rows remain on the
  project (`TeamGrantsPresentError`, 409 `team_links_present`): unlink its teams first.

**Startup check, fail closed.** When the module loader has not loaded `teams` and live
`team_link` rows exist, the app refuses to start (`OrphanedTeamGrantsError`), because nothing
would maintain them. The check runs before any tenant is bound, so on PostgreSQL it calls the
`SECURITY DEFINER` function `kerp_live_team_link_grant_count()` (migration `0025`, pinned
`search_path`, `EXECUTE` revoked from `PUBLIC`, granted to the migration role and `kerp_app`),
which returns only the count across **all tenants**, never row data; on SQLite it is a plain
count. **To disable teams**: unlink every team while the module is still loaded (which revokes
the rows), or call `revoke_team_link_grants` for every link id from `team_link_grants()` in a
`system_admin` session of each tenant; then remove the module.

**Migration `0025`** is additive (every existing row becomes `direct`). Its downgrade is
destructive: it deletes every `team_link` row first (a user may hold the same persona directly
and through a team, which the old key rejects), then restores the old constraint, policies and
columns and drops the function.

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
| `KERP_PASSWORD_MIN_LENGTH` | `8` | 8–64: minimum length of every new password (*Passwords*); a value below 8 refuses startup |
| `KERP_PASSWORD_BREACH_CHECK` | `false` | `true`: also refuse passwords found by the k-anonymity breach range lookup (outbound HTTPS to `api.pwnedpasswords.com`; a failed lookup never blocks) |
| `KERP_PASSWORD_RESET_TTL_MINUTES` | `60` | 5–1440: lifetime of an e-mailed password-reset link |
| `KERP_INTERNAL_EMAIL_DOMAINS` | empty | Comma-separated e-mail domains of internal staff (exact match, no wildcards; see *Internal staff*); a malformed entry refuses startup |
| `KERP_WEB_ORIGIN` | unset | Web SPA origin allowed on the cookie endpoints; unset = the API's own origin as the request sees it (behind a TLS-terminating proxy set this, or trust the proxy with `FORWARDED_ALLOW_IPS`); required with `KERP_NATIVE_APP_ORIGINS` |
| `KERP_NATIVE_APP_ORIGINS` | empty | Native-app origins that get the refresh token in the body, and the API's CORS allow-list (*API CORS and the native-origin allow-list*) |
| `KERP_REDIS_URL` | unset | Revocation store |
| `KERP_ENTRA_TENANT_ID` | unset | The company Entra tenant (a GUID; `common` / `organizations` / domain names refused at startup). With the client id: SSO offered |
| `KERP_ENTRA_CLIENT_ID` | unset | The app registration's client id (a GUID) |
| `KERP_ENTRA_CLIENT_SECRET` | unset | **Secret** (secret store / environment only): the client secret for the code redemption. Required in production with SSO |
| `KERP_ENTRA_REDIRECT_URI` | unset | `https://<deployment>/api/v1/auth/sso/entra/callback` exactly as registered; required in production with SSO (elsewhere: the request's own origin). Its origin is also the App Link host |
| `KERP_ANDROID_APP_PACKAGE` | unset | Android package name for `assetlinks.json` |
| `KERP_ANDROID_APP_CERT_FINGERPRINTS` | empty | Comma-separated SHA-256 signing-certificate fingerprints (`AA:BB:…`, 32 bytes) |

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
