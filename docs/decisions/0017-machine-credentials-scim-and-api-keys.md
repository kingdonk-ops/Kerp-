# ADR 0017 — Machine credentials: SCIM bearer tokens, API keys and service accounts

- **Status:** **accepted** for the tenant binding (§1–§3): the user approved the enterprise
  roadmap assessment on 2026-11-21 ("tenant from the token", "tenant from the key"). §4
  (service-account representation) and §5 (who manages keys) are planner defaults in force until
  TD-438 is decided.
- **Date:** 2026-11-21
- **Affects:** T-0298, T-0299, T-0300 (SCIM, phase 1), T-0307, T-0308, T-0309 (API keys,
  phase 5, epic E-05-07); `app/core/auth` (bearer resolution); the `auth_user` core table
  (`account_type`, §4); diff §4.9; CLAUDE.md §6 ("Tenant comes from the JWT only").

## Context

The enterprise roadmap (user, 2026-11-21) adds two non-interactive callers:

1. **Entra ID SCIM provisioning** (diff §4.9): Microsoft's provisioning service calls
   `/scim/v2/Users` with a long-lived bearer token configured in the Entra enterprise
   application. It cannot obtain a KERP JWT.
2. **Integrations** (E-05-07): ERP bridges, reporting tools and client systems call the API
   with an API key belonging to a service account.

CLAUDE.md §6 says the tenant comes from the JWT only. Neither caller has a JWT. Two earlier
endpoints already run without one and bind the tenant from deployment configuration: the SES
SNS endpoint (diff §4.6) and the proposed public capture links (ADR 0013). A deployment serves
exactly one hidden tenant (diff §4.1).

## Decision

1. **Tenant binding.** For a SCIM token or an API key the request binds the tenant from
   deployment configuration (`KERP_DEFAULT_TENANT_ID`) **before** any lookup, then looks the
   credential's hash up under the normal tenant RLS. No bypass role, no `SECURITY DEFINER`
   lookup, no tenant or account value from the request. The credential row's `tenant_id` is
   therefore always the deployment tenant; a credential that is not found under it is a 401.
   This is the only exception to "tenant from the JWT" besides those of diff §4.6 and ADR 0013,
   and it is limited to these two credential types.
2. **Credential handling.** 256-bit random secrets with a short visible prefix; stored only as a
   SHA-256 hash; returned once at creation or rotation (SCIM tokens and API keys both have a
   rotate action; the old secret stays valid for an overlap of at most 24 h, then fails); constant-time comparison; required expiry
   (SCIM default 365 days, max 730; API keys at most 365); revocation effective at once
   (Redis); `Authorization` headers scrubbed from logs and Sentry; every create, rotate, revoke
   and expiry audited in `admin-system:<tenant>` without the secret. Uniform 401 for missing,
   unknown, expired and revoked credentials. Per-credential rate limits.
3. **Separation of credential types.** SCIM tokens work only on `/scim/v2/*`; API keys never
   work on `/scim/v2/*`, `/auth/*`, `/signing/reauth`, device, PIN, invite acceptance, admin
   grant or `/admin/*` mutation routes; JWTs are refused on `/scim/v2/*`. A contract test over
   all routes enforces it.
4. **Service accounts (planner default, TD-438).** A service account is an `auth_user` row with
   a new column `account_type` (`human | service`, default `human`; additive core-table change
   recorded by this ADR). It has no password, SSO link, magic link, PIN, device or invite and
   can never sign in interactively. It holds project memberships like a user, so RLS, site scope
   and the module services work unchanged. A service account can **never hold an admin grant**
   (`system_admin` or any other ADR 0016 grant): the admin-grant API refuses it with 422
   (`SERVICE_ACCOUNT_NOT_GRANTABLE`), and key-authenticated requests ignore admin grants and
   ADR 0016 visibility altogether, so a key never sees a project its account is not a member
   of. Service accounts are `is_internal = false` (no Entra link, no internal domain), so the
   internal-commercial boundary (diff §4.9, T-0295) hides internal commercial data from keys;
   this is intended. SCIM never matches, updates or deactivates a service account.
   **Identifying machine writes.** `source="api"` does not identify them: it is already the
   default and the value used on the human JWT paths (`backend/app/core/db.py`). A write made
   with an API key is identified by `created_by` / the actor being an `account_type = service`
   user plus the key id (`api_key_id`) in the audit payload; a SCIM write by the SCIM token id
   (`scim_token_id`) in the audit payload with no human actor. No new `source` value is
   introduced.
5. **Authority (fixed).** An API key's capabilities are its scopes (`read` → `V`;
   `write` → `V`, `C`, `E`) intersected with the service account's memberships and the key's
   project list. `A` and `S` are always removed: no sign-off, approval, hold release, co-sign,
   attestation or confirmation of a `source="ai"` proposal is ever made with a key. Admin grants
   and ADR 0016 visibility never apply to a key (§4). Who manages keys: `system_admin` (planner
   default, TD-438).

## Consequences

- SCIM can provision and deprovision accounts (diff §4.9) without weakening JWT handling.
- CLAUDE.md §6 stays true for every human request; this ADR is the reference for the two
  machine credential types.
- The `auth_user` change is additive; existing rows become `human`.
- A multi-tenant deployment (not planned, diff §4.1) would need a tenant-qualified credential
  format and a new ADR.
