# settings — runtime settings registry and deployment settings (T-0044, T-0230)

Thresholds a System admin can change without a redeploy (diff §4.7), declared in code and
stored one value per tenant and key. The deployment settings of diff §4.4 (T-0044) are seven
entries of this registry. A deployment is one hidden tenant (diff §4.1), so "deployment" and
"tenant" settings are the same thing. The tenant stays in code and RLS and is never taken or
returned by the API.

| File | What |
|---|---|
| `registry.py` | `SettingDef`, `DeploymentCap`, `SettingRule`, `SettingsRegistry`, the process-wide `REGISTRY` (`register`, `add_rule`), value checks, the secret guard |
| `setting_defs.py` | this module's entries: the seven T-0044 settings and the grace rule |
| `models.py` | `oe_settings_value` (`BaseRecordMixin`, tenant-level, standard RLS; migration `0037`); the legacy `oe_settings_deployment` (migration `0017`, read-only), `SHIPPED_DEFAULTS`, bounds |
| `cache.py` | `RedisSettingsCache` (Redis + pub/sub invalidation), `NullCache`, `install` / `open_cache` |
| `deployment_config.py` | the read-only deployment configuration and its secret guard |
| `schemas.py` | `DeploymentSettingsRead` / `Update`, `SettingEntryRead`, `SettingValueUpdate`, `DeploymentConfigRead` |
| `service.py` | `get_value`, `get_values`, `client_values`, `set_values`, `registry_entries`; deployment settings: `get_settings`, `project_defaults`, `update_settings`, `stored_record`, `ensure_record` (first-run, T-0023) |
| `router.py` | `/api/v1/settings/...` (below) |

## Declaring an entry

A module declares its entries in its own `setting_defs.py` and lists `settings` in its
manifest's `depends_on`. The module loader imports every `setting_defs.py` in the API and in
the worker (`import_setting_defs`), so the registry is complete in every process.

```python
from app.modules.settings.registry import SettingDef, register

MAX_THINGS = SettingDef(
    key="max_things",
    category="things",
    type="int",
    default=5,
    minimum=1,
    maximum=50,
    description="Most things per record",
)
register(MAX_THINGS)
```

and reads it with `settings.service.get_value(session, MAX_THINGS.key)`.

| Field | Meaning |
|---|---|
| `key`, `category` | `snake_case`, 3–64 characters; the key is unique in the deployment |
| `type` | `int`, `float`, `bool`, `string`, `enum`, `int_list` |
| `minimum`, `maximum` | required for `int`, `float` and `int_list` (each item) |
| `choices` | the values of an `enum` |
| `max_length` | longest `string` or most items of an `int_list` (default 255) |
| `check` | extra check of a `string` (IANA zone, ISO 4217, BCP 47) |
| `client_visible` | returned by the client bootstrap (`client_values`; the endpoint is T-0231) |
| `cap` | `DeploymentCap(env, limit)`: a value of the deployment configuration the entry can never exceed |

`add_rule(SettingRule(keys, check))` adds a rule across keys, checked on the values a change
would leave behind (`offline_grace_hours` ≤ 24 × `offline_window_days`).

**No secret is ever an entry.** `register` refuses a key that names a secret (`password`,
`secret`, `credential`, `private_key`, `access_key` anywhere; `token`, `key`, `pin`, `salt`,
`signature`, `dsn`, `cert` as the last word) and any type outside the six. A key that only
mentions one (`cosign_pin_token_ttl_minutes`, `api_key_rate_per_minute`) is fine.

## Entries

| Key | Category | Default | Bounds | Declared by |
|---|---|---|---|---|
| `offline_window_days` | `offline` | 7 | 1–14 | settings (diff §4.2) |
| `offline_grace_hours` | `offline` | 24 | 1 to 24 × `offline_window_days` (TD-257) | settings |
| `max_checkout_clock_skew_s` | `offline` | 5 | 1–300 | settings (diff §6.4.1) |
| `default_status` | `project_defaults` | `ACTIVE` | `ACTIVE`, `ON_HOLD`, `CLOSED` | settings |
| `default_site_timezone` | `project_defaults` | `Australia/Perth` | IANA zone from `tzdata` | settings |
| `default_currency_code` | `project_defaults` | `AUD` | active ISO 4217 | settings |
| `default_locale` | `project_defaults` | `en-AU` | BCP 47 `ll[-Ssss][-RR\|-nnn]` | settings |
| `max_upload_size_mb` | `uploads_media` | 100 | 1–5120, capped by `KERP_UPLOAD_MAX_BYTES` | uploads |
| `presigned_url_ttl_minutes` | `uploads_media` | 15 | 1–60, capped by `KERP_UPLOAD_URL_SECONDS` | uploads |
| `tablet_proxy_max_px`, `tablet_proxy_quality` | `uploads_media` | 1280, 72 | 640–2560, 40–95 | uploads (read by T-0247) |
| `report_image_max_px`, `report_image_quality` | `uploads_media` | 1024, 68 | 480–2048, 40–95 | uploads (read by T-0247) |

The three `offline` entries and `max_upload_size_mb` are client-visible. The other entries of
diff §4.7 are declared by the task that reads them (T-0140, T-0156, T-0162, T-0167, T-0169,
T-0174, T-0277, T-0289, T-0308).

## Values

`oe_settings_value` holds one live row per tenant and key (partial unique index), the value as
JSON. **No row means the code default**; reading never writes. A value out of bounds, over the
deployment cap or against a rule is refused (`InvalidSettingError`, 422) and **never clamped**.
A stored value the entry no longer accepts (bounds tightened in code) is ignored in favour of
the default, and the effective value is never above the cap (for a cap lowered after the value
was stored). A concurrent first write of the same key loses with 409.

First-run provisioning (T-0023, `app/modules/onboarding`) stores the shipped defaults of the
seven deployment settings (`ensure_record`, audited `{"action": "provision", "values": …}`;
left alone if any is stored).

`oe_settings_deployment` (T-0044) is **read-only**: migration `0037` copied every live row
into `oe_settings_value` and nothing reads or writes the old table. Dropping it is a
destructive migration and needs an ADR (TD-511).

**Region is not a setting here.** It is deployment configuration (environment, like the
database URL): AWS `ap-southeast-2` for Australian deployments, `eu-central-1` only if an EU
deployment is ever needed (diff §4.4). It is shown, read-only, by `GET /deployment-config`.

## Cache

API and worker processes install a `RedisSettingsCache` at startup (`KERP_REDIS_URL`; without
Redis there is no cache and every read is the database's). A read answers from the process's
own copy, then Redis (`kerp:settings:values:<tenant>:<generation>`), then the database. After
a change **commits**, the writer starts a new generation for the tenant and publishes the
tenant id on `kerp:settings:invalidate`; every process listens (one daemon thread) and drops
its copy at once. A transaction that changed a setting reads the database until it ends, and a
rolled-back change publishes nothing. Copies also expire (30 s in the process, 60 s in Redis),
which bounds staleness if Redis is unreachable at the moment of a change. A Redis failure is
never an error for the caller.

## New projects

`projects.create_project` calls `project_defaults(session)` and fills `status`,
`site_timezone`, `currency_code` and `locale` that the request left out, so it always passes
all four values explicitly. The column defaults on `oe_projects_project` (and the DB-side
defaults for `site_timezone` and `status` that T-0063 adds) are only fallback literals; they
equal `SHIPPED_DEFAULTS` (tested). Currency and locale get no DB default. Changing a default
never touches existing projects.

The projects module depends on this one (`depends_on=("settings",)`); this module depends on
nothing, which is why it is its own module and not part of `admin` (which depends on
`projects`; TD-295).

## API (`/api/v1/settings`)

| Endpoint | Who | What |
|---|---|---|
| `GET /deployment` | any signed-in user (no membership needed) | the seven deployment settings (T-0044 contract) |
| `PATCH /deployment` | `system_admin` (else 403) | change the given fields; 422 for invalid values, nulls, unknown fields (`tenant_id`, `region`) or an empty body |
| `GET /registry` | `system_admin` | every entry: `key`, `category`, `type`, `description`, `value`, `default`, `is_default`, `minimum`, `maximum`, `choices`, `max_length`, `client_visible`, `cap`, `cap_source`; by category then key |
| `PATCH /registry/{key}` | `system_admin` | body `{"value": …}`; 422 out of bounds, wrong type, over the cap or against a rule; 404 for an undeclared key |
| `GET /deployment-config` | `system_admin` | read-only deployment configuration: refresh-token hours, PIN idle minutes, PIN lockout schedule, region, storage region, mail transport and sender, malware scanner, the upload limits that cap the registry, AI provider (`none`) |

The `system_admin` flag is the one the request session was bound with at authentication
(`access.CallerIsSystemAdmin`), never from the request. RLS is the standard policy: every
session of the tenant reads the rows and only a tenant-level session writes them; the router
restricts writes to System admins (a System-admin-only write policy in the database is
TD-296).

`GET /deployment-config` shows an explicit allow-list of `Settings` fields
(`deployment_config.FIELDS`). `read` refuses to run if the list holds a secret: any
`SecretStr`, any field hidden from `repr()`, any `*_url` or `*_file`, any secret-like name
(tested against every field of `Settings`). The mail reply-to of diff §4.7 is not shown yet:
the deployment has no such configuration (TD-510).

## Audit

Tenant-level chain `deployment-settings:<tenant>`, for both `PATCH` endpoints:
`{"action": "update", "changes": {key: [old, new]}}` with the actor. A change that changes
nothing writes nothing.

## Tests

`tests/test_deployment_settings.py` (T-0044 contract, unchanged) and
`tests/test_settings_registry.py` (SQLite and PostgreSQL; the cache on `fakeredis`).
