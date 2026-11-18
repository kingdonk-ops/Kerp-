# onboarding — first-run provisioning (T-0023)

Sets a fresh deployment up under the access model of diff §4.1: **one hidden tenant**, its
**deployment settings** (diff §4.4) and the **first System admin**. Projects are created
afterwards by Project Admins (`create_projects`, T-0039), never by provisioning, except the
development-only demo project.

| File | What |
|---|---|
| `service.py` | `first_run(database, request, settings=...)`, the refusals (`FirstRunError` subclasses) |
| `first_run.py` | the command line: `python -m app.modules.onboarding.first_run` |
| `schemas.py` | `FirstRunRequest` (no password field), `FirstRunResult`, the demo project's code and name |
| `models.py` | no table: everything is written through other modules' services and core provisioning |
| `router.py` | no endpoints: provisioning is a deployment step, not an API (there is no admin yet to authorise a request) |

## Command

Run once per deployment, after `alembic upgrade head`, with the runtime database role
(`KERP_DATABASE_URL`, row-level security applies):

```bash
python -m app.modules.onboarding.first_run --admin-email admin@company.example [--admin-name "…"]
```

| Option | Default | Meaning |
|---|---|---|
| `--admin-email` | `$KERP_FIRST_ADMIN_EMAIL` | the first System admin's e-mail (required one way or the other) |
| `--admin-name` | empty | the admin's display name |
| `--tenant-id` | `$KERP_DEFAULT_TENANT_ID`, else a new id | the tenant to create; must equal `KERP_DEFAULT_TENANT_ID` when that is set |
| `--tenant-name` | `Default tenant` | internal name of the hidden tenant (never shown to users) |
| `--demo` | off | **development only** (refused with `KERP_ENVIRONMENT=production`): also grant `create_projects` and create the synthetic project `DEMO` with the admin as PER-09 |

It prints `KERP_DEFAULT_TENANT_ID=<id>` (set it for the API and the worker; login then needs no
tenant input, T-0037) and one line per step (`created` / `present`). Exit codes: `0` done or
nothing to do, `1` refused (nothing written), `2` bad usage or configuration.

There is **no password option**. The account is created without credentials, so no password is
in the repository, the environment, the shell history or a log. The admin then signs in with
Microsoft (Entra ID SSO links an existing account by e-mail, T-0054; SSO never creates
accounts, diff §4.5) or sets a password through a one-time link: the self-service reset link of
T-0077 (TD-358 until it ships). If the e-mail's domain is on `KERP_INTERNAL_EMAIL_DOMAINS`,
the account is internal from the start (T-0049).

## What a run does

1. **Census.** `provision.tenant_directory_count` counts the directory rows of every tenant
   (soft-deleted ones included): on PostgreSQL through the `SECURITY DEFINER` function
   `kerp_tenant_directory_count()` of migration `0029` (ADR 0012), which returns only a number.
   Any row other than this deployment's tenant → **refused** (`AnotherTenantError`): one tenant
   per deployment. A directory that holds a tenant while none is named (no `--tenant-id`, no
   `KERP_DEFAULT_TENANT_ID`) → refused (`TenantNotConfiguredError`).
2. **Tenant.** `provision.ensure_tenant` (T-0079), the one writer of the directory: idempotent,
   so a row made by migration `0026` or the API start-up is reused.
3. **Settings.** `settings.ensure_record` stores the shipped defaults (offline window 7 days,
   grace 24 h, clock skew 5 s; new projects `ACTIVE`, `Australia/Perth`, `AUD`, `en-AU`) unless
   a record exists, which is then left as it is.
4. **First System admin.** The account for the e-mail is created through
   `admin.create_user` (or an existing active one reused) and granted `system_admin`
   (`admin.grant`). Refused when another **active** account already holds `system_admin`
   (`SystemAdminExistsError`: further admins are appointed in the app) or the e-mail's account
   is deactivated (`InactiveAccountError`).
5. **Demo** (`--demo` only): `create_projects` and the project `DEMO` through
   `projects.create_project` with the admin as creator, so PER-09 and the project's default
   rule sets come with it.

Steps 3-5 run in one tenant-level unit of work bound as a System admin
(`TenancyContext(tenant, is_system_admin=True)`). On PostgreSQL the whole run holds a session
advisory lock, so two runs at once cannot both find an empty directory. Re-running with the same
input changes nothing; a run that changed something appends `{"action": "first_run", …}` to the
tenant-level audit chain `onboarding:<tenant>` (besides the chains of the services it calls).

## Tests

`tests/test_onboarding.py` (SQLite and PostgreSQL; the census function and its privileges on
PostgreSQL only).
