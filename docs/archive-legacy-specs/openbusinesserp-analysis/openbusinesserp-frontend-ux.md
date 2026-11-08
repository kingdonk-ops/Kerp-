# OpenBusinessERP — Frontend, UX & Settings Screens

> Companion to `openbusinesserp-architecture.md`. Documents the **website layout, design system/colours, menu/navigation, logo/branding, the Settings hub, the Settings → Team / Members / Job-roles surface, and the admin Permissions Matrix** — grounded in `frontend/src`. Every claim cites a file; paths are under `frontend/` unless noted.
>
> Stack recap: **React 18.3 + TypeScript 5.9 + Vite 6**, **zustand 5** state, **i18next 26** (44 languages, 4 RTL), Tailwind with CSS-variable tokens, Playwright e2e.

---

## 1. Design system & colours

Colours are **CSS custom properties** on `:root` in `src/index.css` (1,857 lines), mapped to Tailwind utilities in `tailwind.config.js` (341 lines) — so components use semantic class names (`bg-oe-blue`, `text-content-primary`, `border-border-light`, `bg-surface-secondary`) and theme automatically.

**Brand / accent**
- **Primary brand: `--oe-blue: #1747d6`** with `-hover #2155e6`, `-active #123bb5`, `-dark #0f319a`, `-subtle #eef2fd`, and an AA-tuned `-text #1239a8` (`index.css:16-37`). Used everywhere as `text-oe-blue` / `bg-oe-blue` and alpha forms (`bg-oe-blue/10`).
- Secondary accent: `--oe-purple #9333ea` (+subtle/text).

**Surfaces, text, borders, semantics** (`index.css`, `tailwind.config.js:45-85`)
- Surfaces: `--oe-bg #ffffff`, `-secondary #f6f7f9`, `-tertiary #fafbfc`, `-elevated` → Tailwind `surface.primary/secondary/tertiary/elevated`.
- Text hierarchy: `content-primary/secondary/tertiary/quaternary`.
- Borders: `border` `#d9dce3`, `border-light #e8eaef`, `border-focus`.
- Semantic palette: `semantic.error/warning/success/info` (+ subtle/text variants), used for badges and status pills.

**Theming mechanism**
- Each hex token also has a **channel-triplet var** (e.g. `--oe-blue-ch: 23 71 214`) so Tailwind alpha modifiers (`/10`, `/30`) compose correctly (`tailwind.config.js:10-33`).
- **Light/dark** is driven by overriding the vars (dark-mode values are AA-contrast tuned — the comments document a 51-file migration to a `-text` token that passes AA in both modes, `index.css:21-37`).
- **RTL**: four RTL languages; `dir="rtl"` on `<html>` flips the sidebar anchor and horizontal paddings via CSS rules (`AppLayout.tsx:132-153`).
- Radius, `content` max-width, and a large animation set (e.g. `pulse-border` for uninstalled converters) are tokenised too.

---

## 2. Website layout / shell

`src/app/layout/AppLayout.tsx` is the authenticated shell (mounted once per signed-in session by `App.tsx`'s `AppShell`). Structure:

```
┌───────────────────────────────────────────────────────────────┐
│ Sidebar (fixed desktop / slide-in mobile, RTL-aware, swipe)    │
│  → Sidebar.tsx                                                  │
├───────────────────────────────────────────────────────────────┤
│ Main column (lg:pl-sidebar)                                     │
│  DesktopToolbar  (desktop app only: back/fwd/reload/home/addr)  │
│  Header          (sticky; i18n page title; menu button)         │
│  <main>          page content (px-4 sm:px-7)                    │
└───────────────────────────────────────────────────────────────┘
Floating / global overlays mounted at shell level:
  DemoBanner · GlobalProgress · FloatingQueuePanel (bg tasks) ·
  DwgUploadIndicator · FloatingRecentButton ·
  FloatingChatButton + FloatingChatPanel (AI dock, Alt+A, push-mode) ·
  ReviewPromptCard · FeedbackDialog
```

Notable shell behaviours (all in `AppLayout.tsx`):
- **Mobile sidebar** with backdrop, body-scroll lock, and **swipe gestures** (RTL-aware edge-swipe to open/close — `:91-102`).
- **AI assistant dock** pushes the page aside on wide screens via `oe-ai-dock-shell` + `--oe-ai-dock-offset`; conversation survives navigation (`:104-120, 180-188`).
- **White-labelling**: `useBrandingStore.companyName` drives both the browser-tab suffix and the sidebar brand, falling back to **"OpenBusinessERP"** (`:52-75`); `CustomBranding.tsx` renders the logo in the sidebar.
- **Offline-first**: `useOfflineSync()` replays queued mutations on reconnect; a global-presence WebSocket reports route/idle status.
- **Desktop build**: `DesktopToolbar` renders window chrome (the product ships as a desktop app too); renders null on web.

---

## 3. Menu / navigation, logo & branding

`src/app/layout/Sidebar.tsx` (2,684 lines) + `navCatalog.ts` (1,089 lines) build a **grouped, workspace-aware** sidebar.

- **Logo/branding:** the sidebar header renders `CustomBranding` — the tenant's own company name/logo when white-labelled, else the OpenBusinessERP mark. There is a real branding store (`useBrandingStore`), unlike inspection-erp's generated monogram.
- **Grouped nav** (`navCatalog.ts`), each group an i18n `labelKey` with lucide icons (`routeIcons.ts`):
  - **Learn** — Video guides, Cases.
  - **Home** (`grp_overview`) — Dashboard (`/`), Projects, Documents/Files, Inbox, Timeline.
  - **Sales / Commercial** (`grp_commercial`) — CRM, Quotes & invoices (`/sales`), Products & services (`/items`), Inventory, Import data, Contacts, Contracts, Payment clock, Tax withholding, Tax rates, E-invoice clearance, Subcontractors.
  - **Purchasing** (`grp_procurement`) — Purchasing, Procurement, Bid management, RFQ bidding, Tendering, Supplier catalogs.
  - **Resources/Inventory** (`grp_resources`) — Equipment, … (plus the many construction/finance groups).
- **Simple vs Advanced mode:** items flagged `advancedOnly` hide in Simple mode; a **company workspace preset** (`workspaces.ts`, `PRESET_WORKSPACES`) can redefine Simple mode to show a curated set first. Pinned and Recent rows are tracked separately.
- **Terminology overrides:** `navCatalog` remaps a row's `labelKey` through a terminology layer (`terminology.ts`) so labels can be re-worded per deployment/industry.
- **Header** (`Header.tsx`, 2,108 lines): sticky top bar with the i18n'd page title (`resolvePageTitleKey`), the mobile menu button, and global actions (search, learn, subscribe/support, bug report, language).

---

## 4. Settings hub

`src/features/settings/SettingsPage.tsx` (2,579 lines) is a **tabbed hub**; the guided-tour catalog (`settingsGuide.ts`) enumerates the sections:

| Tab / section | Component(s) | What it does |
|---|---|---|
| **General** | `BusinessSettingsHub`, `CompanyDocumentsSettings`, `MailSettings`, `PaymentSettings`, `TextSizeSetting` | Company profile, mail server, payment details, UI text size. |
| **Regional** | `RegionalSettings` | Locale, number/date formats, currency, country/regional packs. |
| **AI** | the AI-provider panel in `SettingsPage.tsx` | Choose an LLM provider from a large catalog — Anthropic, OpenAI, Gemini, OpenRouter, Mistral, Groq, DeepSeek, Together, Fireworks, Perplexity, Cohere, AI21, xAI, Zhipu, Yandex, Baidu, Kimi, **Ollama (keyless/local)**, and an **OpenAI-compatible endpoint / vLLM** (base-URL, optional key) — with API-key entry (show/hide), tool-calling mode (auto/on/off) and timeout (`SettingsPage.tsx:122-276, 464-831`). |
| **Integrations** | `ModulesSettings`, `WebhookLeads`, connectors | Enable/disable modules; inbound webhooks; connectors. |
| **Advanced** | `BackupRestore`, DB setup wizard, `VectorStatusCard`, `ReferenceDataPanel`, `TranslationManager` | Backup & restore, database setup, vector-index status, reference data, translations. |
| **Account** | `DeleteAccountDialog`, profile | Account management incl. delete-account. |
| **Dashboard** | widget settings (`useWidgetSettingsStore`) | Reorder / show / hide personal dashboard sections (saved to this browser). |
| **Documents** | `DocumentDesignSettings`, `BusinessDocumentsSettings`, `DocumentTemplatesPanel` | Branded document design + templates for quotes/invoices. |
| **E-invoicing** | `EInvoiceSettings` | EN 16931 / Peppol / XRechnung / ZUGFeRD configuration. |
| **Team** | `SettingsTeamPanel` (→ §5) | Members, roles, job-role presets, licence. |

Settings is where the **profile, team and permissions** surfaces live — the part inspection-erp lacks.

---

## 5. Settings → Team / Members / Job-roles (the user-manager)

`src/features/settings/SettingsTeamPanel.tsx` (474 lines) + `SettingsRolePresets.tsx` (145 lines). It **surfaces** existing users/business-roles APIs rather than rebuilding auth, and is fully permission-gated.

**Gating** (`SettingsTeamPanel.tsx:99-138`)
- `canListMembers = role === 'admin' | 'manager'` (mirrors backend `users.list`).
- `isAdmin` unlocks the write affordances.
- A caller lacking `users.list` gets a **read-only "your membership" card** (built from `GET /v1/users/me/`) instead of controls that would 403 — exactly the "only for those with the permission" behaviour.

**Cards**
1. **Team & workspace** — member/active/admins/managers counts + role-breakdown chips (`:152-244`).
2. **Members** — list from `GET /v1/users/` with avatar, name/email, **role chip** (admin=Crown/red, manager=ShieldCheck/amber, editor=Edit3/blue, viewer=Eye/neutral), active/inactive badge, "You" marker. Admin-only **Invite User** and **Manage users** buttons navigate to `/users` (the full User Management page). Non-admins/managers see an honest "Member list is admin-only" panel (`:246-373`).
3. **Job roles** (`SettingsRolePresets`, shown to admin/manager) — the **business-role presets** surface:
   - A per-member **`<select>`** assigns a preset (Owner/Admin, Accountant, Sales, Purchasing & Warehouse, Read-only, or "Custom (role)") via `PUT /v1/business-roles/users/{id}/`; disabled unless `canEdit` (admin) (`SettingsRolePresets.tsx:76-106`).
   - A **preset × permission matrix** (from `GET /v1/business-roles/`) shows ✓/– for each business permission per preset (`:108-139`) — the UI for `core/business_roles.business_matrix()`.
4. **License & plan** — honest AGPL Community-Edition panel: version/env/modules-loaded from `GET /api/health`, AGPL-3.0 badge, and a **commercial-license request link** (no in-app payment flow, because none exists server-side) (`:377-471`).

---

## 6. Admin → Permissions Matrix

`src/features/admin/PermissionsMatrixPage.tsx` (1,490 lines) is the front end for the editable RBAC registry (backend documented in the architecture doc §5.4):

- **Data/gating:** `GET /api/v1/admin/permissions/matrix` (needs `audit.view` = manager+). Below that role the page renders a **read-only matrix**; admins get edit controls (`PermissionsMatrixPage.tsx:17-25, 531`).
- **Edits:** `PATCH /api/v1/admin/permissions/{key}` (per-permission `min_role`) and `POST /api/v1/admin/permissions/preset/{name}` (apply a baseline), both admin-only, with **optimistic cache updates + rollback** (`:602-616`), a per-role **edit mode**, a **preset-apply confirmation modal**, and **CSV export** of the snapshot (`:117, 547-564`).
- Renders roles × modules × permissions with per-cell toggles; permission keys shown in mono with human-cased action labels.

---

## 7. Permission gating in the UI (client mirror)

`src/shared/lib/permissionGates.ts` + `src/shared/auth/AdminOnly.tsx`:
- A **`PERMISSION_MIN_ROLE` map** mirrors the backend's minimum role per permission (e.g. `users.list: manager`, `payroll.read: manager`, `schedule.purge: admin`, `boq.delete: editor`). `useHasPermission()` reads the live role from `useAuthStore` and decides **whether the UI fires a call at all** — so a low-privilege role never triggers a 403 it can't pass (keeps pages usable and client-error reports honest). **The backend remains the sole authority** (`permissionGates.ts:3-22, 70-80`).
- A CI script (`scripts/check_role_mirrors_match_the_backend.py`) fails the build on drift between this mirror and `permission_registry`, so the client never silently diverges from server policy.
- `AdminOnly.tsx` conditionally renders admin-only affordances.

---

## 8. Status summary (asked-for items)

| Asked | Status in OpenBusinessERP | Where |
|---|---|---|
| Site layout | ✅ fixed sidebar + sticky header + desktop chrome + floating AI/queue/recent | `app/layout/AppLayout.tsx` |
| Colours / design | ✅ `--oe-*` CSS-var tokens, brand `#1747d6`, semantic palette, light/dark, RTL | `src/index.css`, `tailwind.config.js` |
| Menu / menus | ✅ grouped, workspace-aware, Simple/Advanced, terminology overrides | `navCatalog.ts`, `Sidebar.tsx` |
| Logo | ✅ real white-label branding (company name + logo), fallback "OpenBusinessERP" | `CustomBranding.tsx`, `useBrandingStore` |
| Settings | ✅ full tabbed hub (General/Regional/AI/Integrations/Advanced/Account/Dashboard/Documents/E-invoice/Team) | `settings/SettingsPage.tsx` |
| Profile / account | ✅ Account tab + delete-account; self-membership card | `settings/*`, `SettingsTeamPanel.tsx` |
| **User manager** | ✅ Members list + invite + Manage users (`/users`), admin/manager-gated | `SettingsTeamPanel.tsx`, `features/users` |
| **Permissions management** | ✅ **editable RBAC matrix** (admin) + **job-role presets** (preset×permission matrix) | `admin/PermissionsMatrixPage.tsx`, `settings/SettingsRolePresets.tsx` |

This is the capability you pointed to: **under Settings (Team → Job roles) and Admin → Permissions Matrix, visible to managers+ and editable by admins** — present in OpenBusinessERP, absent from inspection-erp.

---

## 9. Not covered here (offer)

Not yet read in depth: the full `/users` **User Management page** (`features/users`), the Header's global-search / command palette internals, and per-feature screens for the 192 modules. Say the word and I'll document any of those next.

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`. Frontend paths under `frontend/`.*
