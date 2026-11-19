# Deploying local passwords and self-service password reset (T-0077)

External users sign in with e-mail + password and reset it themselves by e-mail (spec diff
§4.2 *Local passwords*, *Self-service password reset*). Details of the behaviour:
`backend/app/core/auth/README.md` (*Passwords*, *Self-service password reset*).

## 1. Settings

| Variable | Default | Meaning |
|---|---|---|
| `KERP_PASSWORD_MIN_LENGTH` | `8` | Minimum length of every new password (8 to 64). **A value below 8 stops the API and the worker at start-up.** Raising it affects new passwords only; nobody is forced to change an existing one |
| `KERP_PASSWORD_BREACH_CHECK` | `false` | `true` additionally refuses passwords listed by the breached-password range API (section 3) |
| `KERP_PASSWORD_RESET_TTL_MINUTES` | `60` | Lifetime of an e-mailed reset link (5 to 1440 minutes) |
| `KERP_APP_NAME` | `KERP` | The deployment name; passwords made of it are refused |

There is no setting for composition rules or password expiry: the policy has neither.

## 2. Prerequisites of the reset

- **Outbound e-mail** (`docs/deploy/outbound-email.md`): the reset link and the "your password
  was changed" notice are sent through the same SES transport and SAQ worker as every other
  e-mail. Without a running worker the requests are accepted (`202`) but no link arrives.
- **`KERP_WEB_ORIGIN`**: the link is `<KERP_WEB_ORIGIN>/reset-password#<token>`.
- **`KERP_REDIS_URL`**: reset tokens (as SHA-256 only) and the rate-limit counters live in the
  same Redis as token revocation. Flushing that Redis kills outstanding reset links (users
  ask again) and signs nobody out.
- **Client address behind a proxy.** Two rate limits are keyed by the client address. Behind a
  TLS-terminating proxy or load balancer, trust it with `FORWARDED_ALLOW_IPS` (as for the
  login lockout); otherwise every user shares the proxy's address and its limit.

## 3. The optional breach lookup

With `KERP_PASSWORD_BREACH_CHECK=true` every new password is looked up in the public
k-anonymity range API `https://api.pwnedpasswords.com/range/<prefix>`:

- Only the first 5 hexadecimal characters of the password's SHA-1 leave the deployment
  (never the password, its full hash, the account or the e-mail address).
- The API process needs outbound HTTPS to `api.pwnedpasswords.com` (allow it on the egress
  firewall / proxy). The call has a 3-second timeout.
- A lookup that fails for any reason is logged (`kerp.auth.password_policy`, warning) and the
  password is accepted: an outage of that service never blocks a reset or an invite.

Leave it off where outbound calls to third parties are not permitted; the bundled blocklist
and the context-word rule apply either way.

## 4. Operations

- **Rate limits** (answer `429` with `Retry-After`): reset requests per e-mail address (4 back
  to back, then 1 minute doubling to 15 minutes, counted over 1 hour) and per client address
  (20 back to back, then 30 seconds doubling to 15 minutes); confirmations per client address
  (11 back to back) and per link (the 10th refused password kills the link).
- **Audit**: the tenant's `auth:password-reset` chain records requests, confirmations and
  refusals with the user id and a reason; never the token, the password or the address.
- **After a reset** every session of the user ends on all devices at its next server
  contact; device enrolment and PIN stay, and an offline field device keeps unlocking
  locally within its offline grace.
- **Logs** never contain the token or the password (the JSON formatter redacts them). If an
  error tracker (Sentry) is added, install `app.core.logging.scrub_sentry_event` as its
  `before_send` hook.
