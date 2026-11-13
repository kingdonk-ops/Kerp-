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
| `service.py` | `AuthService` login / refresh / logout / magic link / PIN unlock; `create_user`, `set_password` |
| `field.py` | Magic-link tokens + delivery seam, PIN rules (T-0009) |
| `lockout.py` | `ProgressiveLockout` + policies, Redis-backed (T-0009) |
| `models.py` | `auth_user` table (tenant-level rows, RLS) — migration `0003`; `auth_project_membership` (T-0010) — migration `0006` |
| `access.py` | Persona matrix, capability tiers, SoD, scope resolution, `require_capability` / `require_hold_release` guards (T-0010) |
| `router.py`, `schemas.py` | HTTP endpoints below |

## Endpoints

| Method + path | Auth | Result |
|---|---|---|
| `GET /.well-known/jwks.json` | none | `{"keys": [...]}` public JWKs (active + retired), `Cache-Control: public, max-age=300` |
| `POST /api/v1/auth/login` | none | `{email, password, tenant_id?}` → `TokenResponse` (`no-store`); `429` when locked |
| `POST /api/v1/auth/refresh` | none | `{refresh_token}` → new `TokenResponse` (rotation); `401` for PIN-gated sessions |
| `POST /api/v1/auth/logout` | bearer | `204`; revokes the token and its whole login session |
| `GET /api/v1/auth/me` | bearer | `{id, tenant_id, email, display_name}` |
| `POST /api/v1/auth/magic-link` | none | `{email, tenant_id?}` → always `202`; sends a 15-min one-time link to an active user; `429` when throttled |
| `POST /api/v1/auth/magic-link/redeem` | none | `{token, pin}` → `TokenResponse` of a new **PIN-gated** session |
| `POST /api/v1/auth/pin/unlock` | none | `{refresh_token, pin}` → rotated `TokenResponse`; `429` when locked |

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
| PIN unlock | session | 4 | 30 s doubling to 15 min | 10th wrong PIN revokes the session (counter lives as long as the session's refresh token, not per day) |
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
- **Refresh**: `KERP_REFRESH_TOKEN_DAYS` (default 14, TD-121). Single use: a refresh
  consumes it atomically in Redis and issues a new pair in the same `sid`. Replaying a used
  refresh token revokes the whole session (reuse detection).
- **Logout** revokes the access `jti` and the `sid`; every token of that login dies.
- **Password change** (`set_password`) invalidates tokens issued before it; deactivating a
  user locks them out on their next request.

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
persona (`PER-01`…`PER-15`) on one project (`member_project_id`); a Site Inspector (`PER-07`)
membership also names its `site_node_id`. Revoking soft-deletes the row. `access.py` holds the
matrix:

- **Capabilities**: V/C/E from the default tier (Manager, Editor, Admin = V/C/E, Admin adds
  `administer`; Portal = V), `A` where the matrix gives hold or commercial (A) authority
  (PER-02, 06, 07, 11), `S` for the client representative (PER-15). Interpretation: TD-151.
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
| `KERP_REFRESH_TOKEN_DAYS` | `14` | 1–90 |
| `KERP_DEFAULT_TENANT_ID` | unset | Tenant of a login without `tenant_id` (TD-120) |
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
