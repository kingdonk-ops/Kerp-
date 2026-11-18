# Deploy: first run of a new deployment

A deployment serves exactly one company: **one hidden tenant** (diff §4.1). Users never see,
pick or type it; the API takes it from `KERP_DEFAULT_TENANT_ID`. First-run provisioning
(T-0023) creates that tenant, its deployment settings (diff §4.4) and the **first System
admin**. Projects come later, from Project Admins (`create_projects`) in the app.

## Steps

1. **Migrate** as the schema owner: `alembic upgrade head` (`KERP_MIGRATION_DATABASE_URL`).
   If you already chose the tenant id, set `KERP_DEFAULT_TENANT_ID` here too
   ([tenants.md](tenants.md)).
2. **First run**, as the runtime role (`KERP_DATABASE_URL`), e.g. as a one-off task with the
   backend image:

   ```bash
   KERP_ENVIRONMENT=production KERP_DATABASE_URL=… \
   KERP_INTERNAL_EMAIL_DOMAINS=company.example \
     python -m app.modules.onboarding.first_run --admin-email it.admin@company.example
   ```

   The e-mail may also come from `KERP_FIRST_ADMIN_EMAIL`. Output:

   ```text
   KERP_DEFAULT_TENANT_ID=1b0c…
   tenant: created
   deployment settings: created
   system admin account: created (it.admin@company.example, internal)
   system_admin grant: granted
   ```

3. **Configure** `KERP_DEFAULT_TENANT_ID` with the printed value for the API and the worker
   (the API refuses to start in production without it), then start them. Login needs no tenant
   input (T-0037).
4. **First sign-in.** No password is set by the command, and none is ever passed to it. The
   System admin signs in with Microsoft (Entra ID SSO, `backend/app/core/auth/README.md`):
   SSO links the account the command created by its e-mail (it never creates accounts). A
   one-time password link for deployments without SSO
   comes with the self-service password reset (T-0077, TD-360).
5. The System admin appoints Project Admins and further System admins in the app.

## Safety

- **Idempotent.** Re-running with the same input prints `present` for every step and writes
  nothing. A run interrupted halfway is completed by the next one.
- **One tenant per deployment.** If the database already holds any other tenant (also a
  soft-deleted one) the command refuses (`refused: …`, exit code `1`) and writes nothing. It
  counts the directory through the census function of migration `0029` (ADR 0012). If a tenant
  exists but neither `--tenant-id` nor `KERP_DEFAULT_TENANT_ID` names it, it refuses too.
- **First admin only.** If another active account already holds `system_admin`, a run with a
  different e-mail is refused: further admins are appointed in the app.
- **No default project in production.** `--demo` (also `create_projects` and a synthetic
  project `DEMO` with the admin as PER-09) is refused with `KERP_ENVIRONMENT=production`
  (exit code `2`).

Details: `backend/app/modules/onboarding/README.md`.
