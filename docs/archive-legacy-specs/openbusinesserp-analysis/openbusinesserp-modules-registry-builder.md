# OpenBusinessERP — Module Registry (`/modules`) & Module Builder

> Fourth companion doc. Documents the **`/modules` registry** and the **module-builder** (runtime, no-code/AI module creation), grounded in `frontend/src/features/modules` and `frontend/src/features/module-builder`. Paths under `frontend/`.

## Routes (from `src/app/App.tsx`)
| Route | Component | Purpose |
|---|---|---|
| `/modules` | `ModulesPage` (3,017 lines) | The **registry**: company profiles, packs, data packages, and system modules. |
| `/modules/developer-guide` | `ModuleDeveloperGuide` | In-product guide to building a module by code. |
| `/module-builder` | `ModuleBuilderPage` (431 lines) | **Runtime module builder**: register of modules built on this instance + the build wizard. |
| `/modules/:moduleKey` | `GeneratedModulePage` | Renders a generated module's list/form from its served `ui-spec`. |

Both the registry and the builder are **read-for-all, write-for-admin**, and the server enforces it on every call (the pages just hide controls).

---

## 1. `/modules` — the registry (`ModulesPage.tsx`)

Layout: `PageHeader` (with a **"Build a module — developer guide"** link to `/modules/developer-guide`) → dismissible intro (links to the developer guide) → `AdvancedModeNotice` → a **"Find a module"** search → a **4-tab** panel (`useTabKeyboardNav`, deep-linkable via `?tab=`):

| Tab (`MODULE_TAB_IDS`) | Label | What it holds |
|---|---|---|
| `profiles` | **Company Profiles** | Pre-shaped company profiles; `profileDelta`/`profileShapes` show what switching profile would add/remove. |
| `partner-packs` (alias `?tab=packs`) | **Packs** | **Country** and **Industry** packs (`PackType` badges: country=Globe/blue, industry=HardHat). Install via `PartnerPackApplyDialog` / remove via `PartnerPackDeactivateDialog`; `useInstallPack`/`useRescanPacks`/`useAppliedPack`; `canInstallPacks` gates it. A pack can enable/disable a bundle of modules, load a demo, apply branding, and add validation-rule packs & methodology. |
| `data-packages` | **Data Packages** | Reference/data bundles (regional registries etc.). |
| `system` | **System Modules** | The instance-wide module on/off list — the same data as **Settings → Integrations** (`/v1/modules/`), shown here too. |

- **Module display names** resolve through `resolveModuleDisplayName` + manifest i18n (`translateManifestText`), never raw snake_case.
- **"Request a custom module"** (`RequestCustomModuleDialog`, launched from the sidebar CTA): a full-viewport modal with two CTA cards routing to a hosted contact form on the marketing site (lead capture / CRM happens there — no in-app form), plus a link to the in-product developer guide. This is the commercial "we'll build it for you" path.
- The registry distinguishes **instance-wide module state** (the module loader) from a user's **personal sidebar** preferences (`useModuleStore`, "Edit menu"), same as the Settings panel.

---

## 2. `/module-builder` — runtime module builder (`ModuleBuilderPage.tsx`)

**What it is.** A no-code/AI builder that **creates a working module at runtime** — its own list, form, validation and API — that **appears in the menu immediately with no restart** and **survives platform upgrades** (it belongs to the instance, stored under a server `runtime_root`). Backend: `backend/app/modules/module_builder`, mounted at `/api/v1/module-builder`.

**The page** (register + launcher):
- `PageHeader` with an admin-only **"Build a module"** (`Wand2`) button that opens `ModuleBuilderWizard`.
- **AI-availability banner**, driven by the builder's own `vocabulary.assistant_available` (admin-only fetch): green "AI connected — draft from a sentence", or amber "No AI connected — start from a template or build by hand" with a link to **Settings → AI**. Deliberately tri-state (undefined = not answered, never shown as "off").
- A collapsible **how-it-works** (4 steps) and an optional `<details>` showing the server `runtime_root` (for admins taking backups).
- **Installed list** as cards: display name, `v{version}`, a `{entity} · N fields · M rules` summary, the module's `base_path` (mono, shown in full — it differs per instance), **Open the module** (`/modules/{key}`), **ExtendModuleButton** (upgrade), and an admin-only **remove** (trash).
- **Quarantine:** a module whose generated files the server could not vouch for is shown with a `QuarantineBadge`/`QuarantineMessage`; its routes are unmounted (its `base_path` answers nothing) but its records are kept and it can still be removed.
- **Removal** dialog (`WideModal`): removes the module immediately; **records are kept unless** the "Also delete everything recorded with it" box is ticked (then an irreversible warning). Reinstalling restores kept data.

---

## 3. The build wizard (`ModuleBuilderWizard.tsx` + `wizard/*`)

Three screens, each with one obvious next step (`STEPS = ['describe','proposal','create']`, then `'done'`; an **upgrade** path uses `['proposal','create']`):
1. **Describe** (`DescribeScreen`) — a sentence (when an AI provider is connected), a **template**, or build by hand (`MIN_DESCRIPTION` guard).
2. **Proposal** (`ProposalScreen`) — review the drafted entity/fields/rules; tick **suggestions** (link fields, features: status/due/export/comments) — nothing is applied until ticked; `FineTune` for detailed edits; a `RefusalNotice` surfaces a model/server refusal.
3. **Create** (`CreateScreen` → `DoneScreen`) — a plain summary with the files and address one click away, then **Create module** installs it.

Suggestions come from the assistant **or** from deterministic server rules (`suggestForSpec`), so templates and the by-hand path get the same proposals.

---

## 4. Builder API & generated-module runtime (`module-builder/api.ts`, 767 lines)

Two halves of one feature: describing/installing a module, and then using it. Base `'/v1/module-builder'`.

**Spec model** (mirrors `spec.py`): `ModuleSpec{ key, display_name, description, category(community|integration|regional), icon, version, author, entity, rules[], drafted_by(assistant|wizard), schema_version, features }`. An **entity** has typed **fields** (`text/long_text/integer/number/money/date/datetime/boolean/select/link`); `link` fields point at another module's records (`contract/contact/schedule_activity/document/user`). **Features:** `status` (2–8 states), `due` (deadline + reminder), `export`, `comments`. **Rules:** `required/positive/not_future/range/one_of/order` with severity error/warning.

**Build endpoints:**
| Fn | Endpoint | Notes |
|---|---|---|
| `fetchVocabulary` | `GET /vocabulary` | Allowed field types, rule kinds, reserved names/keys, `max_fields`, `assistant_available`, link targets, features, `upgrade`. The wizard reads capability from here, so the frontend can't offer what the server doesn't support (`extra="forbid"` → a stray key is a 422). |
| `draftSpec` | `POST /draft` | AI: sentence → spec (long-running). |
| `suggestForSpec` | `POST /suggest` | Deterministic, AI-free suggestions. |
| `previewModule` | `POST /preview` | Renders **every file** that would be written (none are), returning a **`review_token`**. |
| `installModule` | `POST /` | Writes + loads the module; **requires the `review_token`** — the review step is a server-enforced rule, not just a screen. Atomic: installed-and-serving, or nothing left behind. |
| `previewUpgrade` / `upgradeModule` | `POST /{key}/upgrade[/preview]` | Add fields/features to an installed module in place; existing records preserved (new field empty, new status at first stage). |
| `fetchInstalledModules` | `GET /` | `{items, total, runtime_root}`; readable by any signed-in user. |
| `uninstallModule` | `DELETE /{key}?drop_data=` | Records kept unless `drop_data=true`. |

**Money/quantities are strings end-to-end** (Decimal → JSON string) — never parsed to JS numbers (a contract sum must not be "approximately right"). `base_path` always comes from the server (`url_prefix_for` in `spec.py`), never rebuilt client-side.

**Generated-module runtime API** (mirrors the generated `router.py`): `GET {base_path}/ui-spec` (the screen description the renderer reads), list/get/create/update/delete records, and `GET {base_path}/export?format=csv|xlsx` when the `export` feature is on. A generated validator returns field-level findings on 422 (`findingsFromError`); upgrades/installs can return structured **refusals** (`upgrade_not_additive`, `layout_conflict`, `module_quarantined`, `links_switched_off`, …) parsed by `upgradeRefusalFrom`.

---

## 5. `/modules/:moduleKey` and `/modules/developer-guide`
- **`GeneratedModulePage`** renders a built module's list + record form entirely from its served `ui-spec` (no compiled screen ships for a runtime module); `RecordFormModal` edits rows; `LinkPicker` resolves `link` fields via `lookupRecords`/`lookupLabels`.
- **`ModuleDeveloperGuide`** (`moduleDeveloperGuide.md`) is the in-product guide for building a module by code — the hand-written alternative to the wizard.

---

## 6. Gating & safety summary
- **Read:** any signed-in user can see the registry and the installed-modules list.
- **Write (install/remove/upgrade/toggle, apply packs):** **admin-only**, enforced server-side; the UI hides the controls for non-admins and shows read-only hints.
- **Safety rails:** preview-before-install with a server-checked `review_token`; **quarantine** of unverifiable generated code (routes unmounted, data kept); **data-retention-by-default** on uninstall (opt-in drop); non-additive/layout-conflict **refusals** rather than silent data rewrites; AI only *suggests* (nothing applied until ticked, nothing created until "Create module").

| Asked | Status | Where |
|---|---|---|
| `/modules` registry | ✅ 4 tabs (Profiles/Packs/Data Packages/System), packs install/deactivate, request-custom, dev-guide link | `features/modules/ModulesPage.tsx` |
| Module builder page | ✅ runtime builder register + wizard + uninstall/drop-data + quarantine + AI banner | `features/module-builder/ModuleBuilderPage.tsx`, `ModuleBuilderWizard.tsx`, `api.ts` |

*Grounded in `kingdonk-ops/openbusinesserp` @ `a96d525`.*
