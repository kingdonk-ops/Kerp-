# OpenBusinessERP — User Management (`/users`) & System/Module Settings

> Third companion doc (with `openbusinesserp-architecture.md` and `openbusinesserp-frontend-ux.md`). Documents the full **`/users` User Management page** and the **system/module settings** surface, grounded in `frontend/src`. Paths under `frontend/`.

---

## 1. User Management page — `/users`

**File:** `src/features/users/UserManagementPage.tsx` (1,246 lines) · **API:** `src/features/users/api.ts`.
The admin panel for managing people, roles, status and per-user module access. It **surfaces the users API** (the backend stays authoritative); every control is role-gated.

### 1.1 Access gating
- `canListUsers = useHasPermission('users.list')` → **manager+**. Below that the page renders `<NoAccessState/>` instead of firing the list call (`:760, 926-935`).
- `isAdmin` (from `useAuthStore`) unlocks **Invite** (create), because `POST /v1/users/` is gated `users.create = admin`; managers can view but not invite (`:754-764, 953-962`).
- The list query is `enabled: canListUsers`, so a manager with no permission never triggers a 403.

### 1.2 API surface (`api.ts`)
| Call | Endpoint | Notes |
|---|---|---|
| `fetchUsers({is_active,limit,offset})` | `GET /v1/users/` | List; `users.list` (admin/manager). |
| `inviteUser(data)` | `POST /v1/users/` | Admin-create; **honours the chosen role** (unlike open `/auth/register`, which forces viewer). Strong password: **≥12 chars, ≥1 letter, ≥1 digit** (mirrored client-side). |
| `updateUser(id,{full_name,role,is_active,locale})` | `PATCH /v1/users/{id}` | Role change / activate-deactivate / rename. |
| `deleteUser(id)` | `DELETE /v1/users/{id}` | **Erase/anonymise in place** — personal fields stripped, account can't log in, but their projects/history keep resolving. Server refuses deleting the **last active admin (409)** and **self (400)**. |
| `getUserModuleAccess(id)` / `setUserModuleAccess(id,payload)` | `GET`/`PATCH /v1/users/{id}/module-access/` | Per-user module visibility + access level (§1.6). |

### 1.3 Layout
Breadcrumb → `PageHeader` (title + subtitle + ModuleGuide + admin-only **Invite User**) → `DismissibleInfo` intro (links to **Governance** and **Audit Log**) → **stats cards** (Total / Active / Admins / Managers) → **search + status filter** (All/Active/Inactive) → **users table** → **role legend**. Uses the Datum tokens (`bg-oe-blue`, `surface-*`, `content-*`) throughout.

### 1.4 Users table
Columns: avatar + Name · Email · **Role (dropdown)** · Status badge · Last login · Actions. Each row's actions: **Access** (opens the per-user module panel), **Deactivate/Activate** (lock/unlock icon), **Delete** (trash).
- **Roles:** `admin` Crown/red · `manager` ShieldCheck/amber · `editor` Edit3/blue · `viewer` Eye/neutral, ranked `admin3>manager2>editor1>viewer0` (`ROLE_CONFIG`, `ROLE_RANK`).
- **`RoleDropdown`** is a portal-anchored popover (escapes table overflow), re-positions on scroll/resize, Escape-closes.

### 1.5 Safety guards (self-lockout & destructive confirms)
All in the page's handlers (`:840-909`):
- **Self-protection:** a user **cannot change their own role, deactivate themselves, or delete themselves** from this list — those controls are disabled with explanatory tooltips, and the handlers early-return on `userId === currentUserId` (self id from `/v1/users/me/`).
- **Role downgrade** (new rank < current) → **warning confirm** ("they will immediately lose the higher role's access").
- **Deactivate** → **danger confirm**; activate is silent (non-destructive).
- **Delete** → **danger confirm** spelling out erase-but-keep-history semantics.
Errors/successes surface via the toast store; the list invalidates (`['users']`) after each mutation.

### 1.6 Invite modal
`InviteModal` (shared `WideModal`: focus-trap, Esc, backdrop dismiss, scroll-lock). Fields: full name, email, password (with live "min 12 + letter + digit" validation), and a **4-button role picker** (admin/manager/editor/viewer). Submits to `inviteUser` → `POST /v1/users/`.

### 1.7 Per-user Module Access panel (`ModuleAccessPanel`)
A second authorization layer **on top of** the org role — a per-user overlay of **which modules are visible and at what level**:
- Loads `GET /v1/users/{id}/module-access/`; edits tracked as `dirty`; saves via `PATCH …/module-access/` (toast on success).
- **Modules grouped** into 6 buckets: `core` (projects, boq, costs, assemblies, validation), `planning_finance` (schedule, costmodel, finance, procurement, changeorders), `communication` (tasks, meetings, rfi, correspondence, transmittals), `quality_safety` (inspections, ncr, safety, punchlist, submittals), `documents_bim` (documents, cde, bim_hub, fieldreports, contacts), `ai_analytics` (ai, takeoff, reporting, risk).
- Per module: a **visibility toggle** + an **access-level selector** (`none / view / edit / full`); setting a level ≠ none also flips visibility on.
- **Custom role name** free-text field (e.g. "Site Engineer") saved alongside.
- **Presets:** `all` (full on every module), `viewer` (view everywhere), `minimal` (only core modules at edit). Footer shows "N / total modules enabled".

> So OpenBusinessERP has **three authorization tiers** surfaced in the UI: (1) the org **role** (this page's dropdown), (2) **business-role presets** (Settings → Team → Job roles), and (3) this **per-user module visibility/access** overlay. All sit above the backend permission registry documented in `openbusinesserp-architecture.md` §5.

---

## 2. System / Module settings

**File:** `src/features/settings/ModulesSettings.tsx` (678 lines), surfaced in **Settings → Integrations**.

### 2.1 What it is (and the distinction it draws)
The panel's own docstring clarifies **two separate "module is on" systems**:
1. **Backend module loader** (`GET /v1/modules/`, `POST /v1/modules/{name}/enable|disable`) — decides whether a module's **routes and models are mounted at all**; **instance-wide, admin-only**. **This panel drives that.**
2. **`useModuleStore`** — a **per-user sidebar view preference**, edited from the sidebar's "Edit menu". Not this panel.

The panel states which it is and links to the other, to end the long-standing confusion.

### 2.2 Data model (`SettingsModule`, from `/v1/modules/`)
`name, version, display_name, display_name_i18n?, description, author, category, depends[], optional_depends[], has_router, loaded, enabled, is_core`.

### 2.3 Layout & behaviour
- **Installed modules** card: a **summary** ("Optional modules installed: X of Y"), a **search** (name/key/description), and **category-grouped, collapsible** lists. Manifest `category` is free text, so it's mapped to a **closed key set** (business, regional, extension, controls/project_controls, enterprise, integration, infra, estimation, developer_tools, compliance) + an **"Other"** fallback — so no snake_case English leaks into the 43 locales.
- **Each module row:** display name (i18n-resolved), **"Always on" badge** for core, version, description, a **"Required by: …"** warning when other enabled modules depend on it, an **"Installing this also installs: …"** hint, and a **toggle switch** (`role="switch"`, busy spinner).
- **Core modules** card: collapsed by default ("Show always-on modules (N)") because the locked core rows (the comment cites ~119 core vs ~69 optional of ~188 loaded) would otherwise bury the changeable ones. Core rows are permanently locked.
- **Pointers card:** links to the **Module registry** page (`/modules` — industry packs, the **module builder**, developer guide) and reminds users that tidying their *own* menu is the sidebar's "Edit menu", not this instance-wide panel.

### 2.4 Dependency-aware toggling (client-side, pre-empting the server)
- A **reverse-dependency index** (`dependents`) is computed from the single list response (rather than 188 calls to `/v1/modules/dependency-tree/{name}`).
- **Disable:** if any **enabled** module still `depends` on this one, the toggle is **blocked** with a warning toast (pre-empting the server's 400); otherwise a **confirm** ("its screens and API routes stop being served for everyone here; your data is kept") then `POST …/disable`.
- **Enable:** pulls in any **disabled dependencies**; the success toast names what was "Also installed" (the backend `enable_module` walks `depends`).
- A module is **locked** in the UI when it's core, the caller isn't admin, or it has blocking dependents.
- Non-admins see a read-only **"Only an administrator can install or remove modules"** hint; the toggles are disabled.
- Query key `['system-modules']` is **shared with the Modules registry page and the Sidebar** (which hides routes whose backing module is disabled), so one successful toggle refreshes all three.

### 2.5 Related system-settings surfaces (same Settings hub)
From `settingsGuide.ts` / `SettingsPage.tsx` (detailed in the frontend-ux doc §4): **General** (company profile, mail, payment), **Regional** (locale/format/currency/country packs), **AI** (LLM provider + key management), **Advanced** (backup & restore, DB setup wizard, vector status, translations), **Account**, **Dashboard** (personal widget layout), **Documents**, **E-invoicing**, **Team** (users/roles/presets/licence).

---

## 3. Status summary

| Asked | Status | Where |
|---|---|---|
| Full `/users` User Management page | ✅ documented — list, invite, role dropdown, activate/deactivate, erase, self-guards, per-user module-access matrix | `features/users/UserManagementPage.tsx`, `api.ts` |
| System / module settings | ✅ documented — instance-wide enable/disable via the module loader, category groups, dependency-aware toggles, core-locked list, registry link | `features/settings/ModulesSettings.tsx` |

Both are **admin-centric and permission-gated**: `/users` needs `users.list` to view and `users.create`/admin to mutate; module install/remove is admin-only and instance-wide.

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`.*
