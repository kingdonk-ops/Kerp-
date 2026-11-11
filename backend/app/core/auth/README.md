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
| `models.py` | `auth_user` table (tenant-level rows, RLS) — migration `0003` |
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
`ratelimit.py`): an attempt is counted before the secret is checked; past the free attempts
the next attempt is refused with `429` + `Retry-After` without checking the secret; a right
answer clears the count. Subjects are hashed into the keys.

| Scope | Key | Free | Delay | Hard cap |
|---|---|---|---|---|
| PIN unlock | session | 3 | 30 s doubling to 15 min | 10th wrong PIN revokes the session |
| Password login | tenant + e-mail | 5 | 30 s doubling to 15 min | none (no permanent lock-out) |
| Magic-link request | tenant + e-mail | 3 / h | 1 min doubling to 15 min | none |

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
context vars. The project dimension is not bound yet (TD-122); roles/personas are T-0010.

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
