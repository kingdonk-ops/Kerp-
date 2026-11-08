# Inspection ERP — Architecture Documentation Suite
## Part 4 · Frontend Layout, Design System & Admin UX

> **Why this part exists.** Parts 1–3 are backend-centric (Part 1 only touched the
> SPA shell and auth transport). This part documents the **website layout, colours,
> design system, menu/navigation, logo, Settings, Profile, and the User-Manager /
> permissions UI** — grounded in the actual `frontend/src` code.
>
> **Honesty note up front.** Some things asked about **are fully built** (design
> tokens/colours, theming, the grouped menu, Settings, the permission gating of
> nav/routes). Others **are not yet built as screens** even though the backend
> supports them — notably a **Profile editor** and an **org User-Manager / member-&-
> role management UI**. Those gaps are documented explicitly in §6–§7 rather than
> glossed over. This is a **Phase 8** frontend (`docs/tasks.md`: "Rounds 0–3
> shipped; Round 4 (module depth) shipped").

---

## 1. Design system — "Datum"

The design language is a self-contained token system called **Datum**
(`tailwind.config.ts`, `src/index.css`). It is **modern-clean-SaaS**: steel-neutral
grounds, a single brand-green accent, monospace for data/identifiers, and full
light/dark support.

### 1.1 Colour tokens (source: `src/index.css`)

Colours are **CSS custom properties**, not hard-coded Tailwind palette values, so
components theme automatically (no `dark:` colour variants needed). Tailwind maps
each token to a utility name in `tailwind.config.ts` (`bg-ground`, `text-ink`,
`bg-brand`, `border-border`, etc.).

| Token | Light | Dark | Role |
|---|---|---|---|
| `--ground` | `#f4f7f8` | `#0b1116` | App background |
| `--surface` / `-2` / `-3` | `#ffffff` / `#eef2f4` / `#e6ecef` | `#131c23` / `#0f171d` / `#1a242c` | Card & panel layers |
| `--ink` / `-muted` / `-faint` | `#0f1720` / `#54636f` / `#8797a3` | `#e8eef3` / `#9fb0bc` / `#6a7c88` | Text hierarchy |
| `--border` / `-strong` | `#e1e7ea` / `#cdd6db` | `#212d36` / `#2e3d48` | Dividers, inputs |
| **`--brand` / `-strong` / `-wash`** | **`#1a7f37`** / `#136b2c` / `#e9f4ed` | `#40ae62` / `#63c581` / `#122419` | **Brand green** (primary actions, active nav) |
| `--info` / `-wash` | `#1e6fd0` | `#5aa2f2` | Informational |
| `--warn` / `-wash` | `#b3730a` | `#e0a53c` | Warnings |
| `--crit` / `-wash` | `#c02636` | `#f0697b` | Errors / critical |

Brand foreground is fixed white (`brand.foreground: #ffffff`).

### 1.2 Typography, radius, elevation
- **Fonts:** system sans stack for UI; a **monospace** stack (`ui-monospace, SF Mono,
  JetBrains Mono…`) used deliberately for data — codes, slugs, labels, table headers
  (`font-mono` appears throughout, e.g. project codes, role chips).
- **Radius:** `--radius: 11px` (`lg`), `8px` (`md`), `6px` (`sm`).
- **Shadow:** `--shadow-sm` (cards) and `--shadow` (popovers), theme-aware.

### 1.3 Theming mechanism
`darkMode: ['selector', '[data-theme="dark"]']`. The cascade is: **light by
default** → dark via OS `@media (prefers-color-scheme: dark)` → explicit override
via `[data-theme="light"|"dark"]` on `<html>` (`src/index.css` has all three
blocks). The user toggle is `lib/useTheme` (sun/moon button in the top bar, §3).

### 1.4 Component library
`src/components/ui/*` holds **shadcn/ui-style primitives** (`Button`,
`buttonVariants`, `Card`, `Input`, `Label`, `Badge`, `Skeleton`, …) consumed by
every screen. Tailwind **preflight is off** (`corePlugins: { preflight: false }`)
during migration from a legacy shell (`src/legacy.css`); `index.css` restores
border defaults so `border` utilities render with the token colour.

---

## 2. Website layout topology

The authenticated app is a classic **fixed-sidebar + top-bar + content** SPA shell,
implemented entirely in `src/app/Layout.tsx`.

```
┌───────────────────────────────────────────────────────────────┐
│ Sidebar (w-64)        │ Top bar (h-14, sticky, blur)            │
│ ┌───────────────────┐ │  [≡ mobile]  Org › Page      ⟨proj⟩ ⟨org⟩ ☾│
│ │ Org monogram +    │ │─────────────────────────────────────────│
│ │ org name + role   │ │ OfflineBar (PWA sync status)            │
│ ├───────────────────┤ │                                         │
│ │ NAV GROUP: Overview│ │                                         │
│ │  • Dashboard       │ │   <Outlet /> — active module page       │
│ │  • Projects        │ │   (lazy-loaded via Suspense)            │
│ │ NAV GROUP: Field…  │ │                                         │
│ │  • Inspections …   │ │                                         │
│ │ …groups…           │ │                                         │
│ ├───────────────────┤ │                                         │
│ │ User avatar + name │ │                                         │
│ │ + email + logout   │ │                                         │
│ └───────────────────┘ │                                         │
└───────────────────────────────────────────────────────────────┘
```

- **Responsive:** on `md+` the sidebar is static (`md:static md:h-screen`); below
  that it is a **drawer** — hidden off-canvas (`-translate-x-full`), opened by the
  hamburger, with a dimming backdrop (`Layout.tsx:100-115, 186-195`).
- **Content** is a lazy `<Outlet/>` wrapped in `<Suspense>` with a loading fallback;
  module pages are code-split (`Layout.tsx:241-246`).
- **Project-gating overlay:** until the user has a project, every screen except
  `/projects` is replaced by a `NoProjectState` card (ADR 0014 — one active project;
  `Layout.tsx:244, 252-267`).

---

## 3. Menu / navigation

### 3.1 Sidebar (primary navigation)
Built from the **module registry**, not hard-coded. `buildNavGroups` (`app/nav.ts`)
flattens every module's `navItems` into **five ordered groups**
(`NAV_GROUPS = ['overview','field','ops','commercial','admin']`), filtering out any
item the current user may not see (§7). Rendered in `Layout.tsx:135-166`.

| Group (i18n label) | Typical items |
|---|---|
| `overview` → **Overview** | Dashboard, Projects |
| `field` → **Field & Inspection** | Inspections, ITP, Findings, Registers, Scheduler, Scope Ingest, P6 Schedule, ISO Workspace |
| `ops` → **Operations** | Workforce, Equipment, Procurement, Daily Diary, HSE |
| `commercial` → **Commercial** | Estimating/Variations, Quotes, Invoices & Dockets, Accounting |
| `admin` → **Admin** | Reports, Search, Studio, Marketplace, Billing, Settings |

- **Icons:** each nav item names a [lucide](https://lucide.dev) icon resolved via the
  `NAV_ICONS` map (`Layout.tsx:50-77`); unknown → `PanelsTopLeft` fallback. (e.g.
  Inspections = `ClipboardCheck`, Registers = `ScrollText`, Settings = `Settings`.)
- **Active state:** `bg-brand-wash text-brand-strong`; inactive is muted ink with a
  surface hover (`Layout.tsx:150-157`).
- **Group headers** are small uppercase monospace labels (`nav.group.*`).
- Clicking a link closes the mobile drawer.

### 3.2 Top bar
`Layout.tsx:186-239`, sticky with a translucent blur. Left→right:
1. **Hamburger** (mobile only) — opens the drawer.
2. **Breadcrumb:** `OrgName › CurrentPageTitle` — the page title comes from
   `routeTitleKey` matching the pathname to a module route's `titleKey` (`nav.ts:39`).
3. **Active-project picker** (`ProjectPicker`) when projects exist; otherwise a
   "+ New project" button when the tenant has none.
4. **Org switcher** — a `<select>` shown only when the user belongs to >1 org;
   changing it calls `switchOrg(slug)` (re-issues a token for that org).
5. **Theme toggle** — sun/moon button (`useTheme`).

### 3.3 Offline status
`OfflineBar` renders directly under the top bar and surfaces the PWA mutation-queue /
sync state (the offline engine in `lib/offline/`).

---

## 4. Logo & brand

There is **no uploaded image logo in the app shell.** Branding in the chrome is a
**generated monogram**:
- **Org "logo":** a brand-green rounded square showing the **organisation's
  initials** (`initials(org.name)`), in the sidebar's org-switcher button, above the
  org name and a monospace role chip (`Layout.tsx:116-133`). The default fallback
  name is **"Datum"** (the design-system name) and initial "D".
- **User avatar:** a brand-green circle with the **user's initials** in the sidebar
  footer, beside name + email + a logout button (`Layout.tsx:168-181`).
- **App/browser title:** the i18n `app.title` = **"Inspection ERP"**
  (`app/i18n.ts:9`); `index.html` carries the document title/favicon.

> A **separate, real logo capability exists on the backend** for *documents*: the
> `reporting` module's per-tenant `ie_reporting_branding` (logo + colours) feeds
> rendered PDFs/HTML (Part 2 §14, Part 3 §A5). That is report branding, not the web
> chrome; the SPA shell does not surface a logo-upload screen.

---

## 5. Settings

**Route:** `/settings` · **Component:** `modules/settings/SettingsPage.tsx` ·
**Access:** `requiresRole: ['owner','admin']` (manifest) — the nav item and route are
hidden from members/guests (§7). Layout: a centered `max-w-4xl` column of cards.

Sections actually implemented:
1. **Organisation** card — read-only org **Name**, **Slug** (mono), and a brand
   **Your-role** badge; a plan line ("Plan: …" / "No plan") with **Manage billing**
   and **Marketplace** links shown only to owner/admin (`SettingsPage.tsx:37-70`).
2. **Client share links** card — the guest-portal manager (Part 3 §B4): owner/admin
   can **create** a read-only portal link for a project, **copy** its public
   `…/portal/{token}` URL, and **revoke** it; revoked links show a crit badge
   (`SettingsPage.tsx:86-208`).

> Settings is therefore **organisation profile + plan + share links**
> (`settings.subtitle`: "Organisation profile, plan and client share links"). It does
> **not** include member management (see §7) or a per-user profile editor (§6).

---

## 6. Profile

**There is no dedicated profile screen or editor.** The user's identity surface is
the **sidebar footer** only: initials avatar, `full_name`, `email`, and a **logout**
button (`Layout.tsx:168-181`), plus the `GET /auth/me` data behind `useAuth()`.

What this means precisely:
- The backend `User` carries editable identity (`full_name`, `locale`,
  password-change invalidation — Part 2 §2), and `GET /auth/me` returns
  `id/email/full_name/locale/organisation/organisations`.
- **No UI** exists to edit name, change password, set locale, or manage one's own API
  keys (the backend `POST/GET/DELETE /auth/api-keys` endpoints have no screen).
- Org switching is available (top-bar `<select>`), which is the one self-service
  identity action surfaced.

**Gap / recommendation:** a `/profile` (or `/account`) screen wrapping
`PUT`-style updates to name/locale/password and API-key management would close this;
the backend contracts already exist.

---

## 7. User manager, roles & permissions UI

### 7.1 What the permission model is (and where it lives in the UI)
The frontend mirrors the backend's **two-axis access model** (Part 1 §3.2) as
**declarative gating in the module manifest contract** (`modules/_types.ts`):

```ts
requiresEntitlement?: string;  // tenant must hold this add-on's entitlement
requiresRole?: string[];       // org roles allowed to see this module
```

`buildNavGroups` (`app/nav.ts:20-36`) hides a module's nav items when either check
fails; the same manifest drives routing. Observed usage (`modules/*/manifest.tsx`):

| Gate | Modules |
|---|---|
| `requiresRole: ['owner','admin']` | `settings`, `billing`, `marketplace` |
| `requiresEntitlement: '<name>'` | all 14 add-ons (`accounting`, `daily_diary`, `equipment`, `estimating`, `fin_registers`, `hse`, `iso_workspace`, `p6_sync`, `procurement`, `quotes`, `scheduler`, `scope_ingest`, `workforce`) |
| none (any member) | core modules (dashboard, projects, inspections, findings, itp, registers, reports, search, studio) |

Entitlement truth comes from `useEntitlements()` over `/marketplace/catalog`; a direct
navigation to an un-entitled module is refused by the backend `402` (Part 3 §A/B).
**Roles surfaced read-only:** the sidebar org chip and the Settings "Your role" badge
display the current org role (owner/admin/member/guest).

### 7.2 What is NOT built: the User-Manager
**There is no org user-manager / member-and-role management UI, and no
project-member assignment UI**, despite the backend fully supporting both:
- **Backend has:** `GET /tenancy/members` (org members), `GET/POST
  /tenancy/projects/{id}/members`, `PUT /tenancy/projects/{id}/members/{user_id}`
  (change project role), `DELETE …` (remove) — Part 1/Part 2.
- **Frontend has:** `modules/projects/ProjectsPage.tsx` only **lists and creates
  projects** (name / code / client); its `api.ts` wires only `GET/POST
  /tenancy/projects` — **no member or role endpoints are called** (verified:
  `projects/api.ts`). No screen invites users, lists org members, or assigns
  project roles. (The only "members" UI in the codebase is **crew members** in the
  `workforce` module — a domain concept, not identity/permission management.)

**Consequence:** today, org membership and project-role assignment are managed only
via the API (or seeded by onboarding/registration, which makes the first user an
`owner`). Guests are read-only and gated in the UI by role, but **granting** a user a
role has no screen.

**Gap / recommendation:** an Admin → **Users & Access** screen (org member list with
role dropdowns + invite, and a per-project member/role editor on the project detail
page) would surface the existing backend endpoints. This is the single biggest
admin-UX gap relative to the backend's capabilities.

---

## 8. Summary — asked-for items, status

| Asked about | Status | Where |
|---|---|---|
| **Site layout** | ✅ Built | Fixed sidebar + sticky top bar + lazy content; responsive drawer (`app/Layout.tsx`) |
| **Colours** | ✅ Built | Datum tokens, brand green `#1a7f37`/`#40ae62`, semantic info/warn/crit (`src/index.css`) |
| **Design** | ✅ Built | shadcn/ui primitives, mono-for-data, radius/shadow tokens, light/dark (`components/ui/*`, `tailwind.config.ts`) |
| **Menu / menus** | ✅ Built | 5 registry-driven groups, lucide icons, breadcrumb + switchers (`app/nav.ts`, `Layout.tsx`) |
| **Logo** | ◑ Partial | Generated org/user **monograms** in chrome; real logo only for **report** branding, not shell; no logo-upload UI |
| **Settings** | ✅ Built (scoped) | Org profile + plan + client share links, owner/admin-only (`modules/settings`) |
| **Profile** | ✗ Not built | Identity shown in sidebar footer; no profile/password/locale/API-key editor |
| **User manager layout** | ✗ Not built | No org member / role / invite UI; Projects page is list+create only (backend endpoints exist) |
| **Permissions** | ◑ Enforced, partially surfaced | Nav/routes gated by `requiresRole` + `requiresEntitlement`; role shown read-only; **no permission-management UI** |

Legend: ✅ built · ◑ partial · ✗ not yet built (backend capability exists).

---

*End of Part 4. This completes the Architecture Documentation Suite:*
*`01-core-platform.md` · `02-base-modules.md` · `03-extension-modules.md` · `04-frontend-ux.md`.*
