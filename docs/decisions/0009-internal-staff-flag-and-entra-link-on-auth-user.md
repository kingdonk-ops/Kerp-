# ADR 0009 — Internal staff flag and Entra link on `auth_user`

- **Status:** accepted (product-owner decision 6, 2026-11-14; diff §4.1 *Internal staff*, §4.5)
- **Date:** 2026-11-14
- **Affects:** T-0049 (this change), T-0054 (Entra ID SSO sets the link), T-0040 (lobby list),
  T-0014 (admin user list), the `auth_user` core table (migration `0013`)

## Context

The lobby project list is for company staff only; external users (client representatives,
subcontractors) reach a project only by project code or invite (diff §4.1). KERP therefore
needs to know, per account, whether it belongs to internal staff. CLAUDE.md §6 requires an ADR
for core-table changes, and `auth_user` is a core table.

## Decision

1. **New columns on `auth_user`** (additive migration `0013`):
   - `is_internal` boolean, not null, default `false` (existing accounts start external and are
     recomputed at their next sign-in);
   - `entra_oid`, `entra_tid` nullable strings: the Entra object id and tenant id of the company
     **member** account the KERP account is linked to; unique index on
     `(tenant_id, entra_oid)` (unlinked accounts, `NULL`, never collide).
2. **Derivation rule.** `is_internal` is true when the account is linked to a member account
   of the company Entra tenant (`entra_oid` set) **or** its e-mail domain is on the deployment
   list `KERP_INTERNAL_EMAIL_DOMAINS` (comma-separated, lower-cased, exact match, no wildcards; a
   subdomain is another domain; empty = no domain rule). It is false otherwise.
3. **When.** Recomputed at account creation and at every successful sign-in (password, magic
   link, and SSO once T-0054 lands); not at refresh or PIN unlock, which continue a session.
   Removing a domain from the list therefore takes effect at the user's next sign-in.
4. **Never from input.** No request schema accepts `is_internal` or the Entra columns (they are
   `422` on the sign-in requests, and T-0014's user create/update schemas must reject them too);
   tokens carry no such claim. The flag is exposed read-only on `/auth/me` and the admin user
   list. Other modules read it only through `app.core.auth.service.is_internal_user`.
5. **It grants nothing.** The flag is not a persona or capability: project access still comes
   only from memberships and team links (ADR 0008); a non-member internal user gets `404` on a
   project like anyone else.

## Why `entra_oid` is the stable link

The Entra object id (`oid`) identifies a user within the company tenant and never changes or
gets reused, whereas the e-mail / UPN can be renamed or reassigned to another person. Matching
later sign-ins by `oid` (diff §4.5) means a renamed mailbox keeps its KERP account and a
reassigned address cannot take over someone else's. `tid` is stored with it so the link
records which tenant vouched for it. Only **member** accounts are ever linked (T-0054 refuses
guest tokens), so an Entra link alone is enough to call the account internal, independently of
its e-mail domain.

## Consequences

- Until an account signs in again after the list changes, its stored flag may be stale; the
  lobby (T-0040) reads the stored flag, which is the accepted behaviour (diff §4.1).
- The downgrade of `0013` drops the columns; Entra links made by T-0054 would be lost and are
  re-made at the next SSO sign-in by verified e-mail.
