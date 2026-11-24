# Module detail — P6 XER file format, parser contract and field mapping

Companion to [p6-import-and-rsw-matching.md](p6-import-and-rsw-matching.md) (the "P6 spec").
The P6 spec says **what** the import does (identity, mapping profile, change report, field
ownership, checkout merge). This file says **how the file is read**: the XER layout, the parser
contract, which tables and columns are interpreted, how each lands in the `oe_p6_*` mirror,
and what the synthetic fixtures must contain. Primavera XML (T-0105) must produce the same
staging shape (§6), so this file is also the contract for that parser.

Precedence: module detail (3). Built by T-0352 (import lifecycle up to `parsed`, parser and staging), T-0104 (mapping, preview diff and commit) and T-0105 (XML).

**Evidence.** A real client construction schedule (P6 export version 24.12, 5.5 MB, one
project) was re-profiled column by column on 2026-11-23. **It is client data. It is never
committed, quoted or used as a fixture.** Every name, code and example in this file and in
the fixtures is synthetic and keeps only the *shape* of what was observed.

---

## 1. File layout

An XER file is line-oriented, tab-separated text:

```
ERMHDR <TAB> 24.12 <TAB> 2026-06-04 <TAB> Project <TAB> <login> <TAB> <user name> <TAB> <db> <TAB> Project Management <TAB> USD
%T <TAB> TABLE_NAME                    ← a table starts
%F <TAB> col_a <TAB> col_b <TAB> …     ← column names (once per table, right after %T)
%R <TAB> val_a <TAB> val_b <TAB> …     ← one row (0..n)
…
%E                                     ← end of file (last line)
```

| Element | Rule |
|---|---|
| Header | First line, tag `ERMHDR`. Fields in order: export version, export date, export type (`Project`), login, user name, database, module, base currency. Version and export date are stored on `oe_p6_import` as `xer_version` and `xer_exported_on`. Login and user name are personal data. They are **not** stored: only the fact that they were present. |
| Line ending | CRLF in the observed file. Accept CRLF and LF. |
| Encoding | Windows-1252 in the observed file (`£`, `¥`, `€` currency symbols). Rule: a UTF-8 BOM, or strict UTF-8 decoding of the whole stored file succeeding → UTF-8. This check is a cheap first pass over the bytes before the streaming parse, which then decodes line by line. Otherwise cp1252 with replacement. Record the detected encoding and the count of replacement characters (warning if > 0). |
| Empty value | Empty string = `NULL`. |
| Quotes | No CSV quoting. A literal `"` inside a value is written doubled (`""`). Evidence: an HTML memo whose DOCTYPE reads `""-//W3C//DTD HTML…` and a UDF value `Changed from ""C"" to ""G""`. The parser decodes `""` → `"`. |
| Embedded line breaks | Multi-line values (memos, calendar data, OBS description) use `0x7F` as the line separator. The parser maps `0x7F` → `\n` in memo and description text. It keeps calendar data byte-exact (§4.4). |
| Dates | `YYYY-MM-DD HH:MM`, no seconds, no offset (§3.1). `1899-12-30 00:00` is P6's null sentinel → `NULL`. |
| Numbers | Decimal point `.`, no thousands separator, may carry long fractions (`14026.6333333333`). Parsed as `Decimal`, never float. |
| Booleans | `Y` / `N`. |
| Enumerations | P6 codes such as `TT_Task`, `TK_NotStart`, `PR_FS`, `CS_MSO`. Mapped per §4. An unknown code is kept raw with a warning, never rejected. |
| End marker | `%E`. A missing end marker means the file is truncated. That is an error and nothing is staged. |

A file can contain **several projects** (several `PROJECT` rows, child rows tagged with
`proj_id`). The observed file has one. When there are several, the parse stops after a
pre-scan in status **`awaiting_project_choice`**: `summary.projects` lists each one
(`proj_short_name`, `guid`, root WBS name, activity count). The planner picks one with
`POST …/imports/{id}/select-project` (`source_project_guid`). Only then are that project's rows
staged, and the import moves on to `parsed` (P6 spec §4, §10). A single-project file skips
this step.

---

## 2. Parser contract

- **Pure stdlib, streaming.** The parser reads the stored original line by line and yields
  `(table, source_line, row_dict)`. It never loads the whole file into a DOM. It lives in
  `backend/app/modules/asset_management/p6/xer.py`. It has no I/O beyond the byte stream it is
  given, so it is unit-testable without a database. Runs in the SAQ **batch** queue (P6 spec §3).
- **Generic first, interpreted second.** Every `%T/%F/%R` table is tokenised. Only the tables in
  §4 are interpreted. Unknown tables and columns are counted in the parse summary and otherwise
  ignored. They stay recoverable because the original is stored (P6 spec §1 principle 7).
- **Columns by name, never by position.** P6 versions add, drop and reorder columns. A missing
  *required* column (marked **R** in §4) is an error naming table + column. A missing optional
  column is `NULL`.
- **Row width.** More values than `%F` columns makes the row malformed. Fewer values are fine:
  the missing trailing values are `NULL`.
- **Fatal tables.** `PROJECT`, `PROJWBS`, `TASK` and `TASKPRED` are fatal tables. A malformed row
  in one of them fails the import (`failed`, nothing staged). Malformed means too many values, an
  empty required (**R**) value, or an unparsable date or number in a required column. In every
  other table a malformed row is dropped with a warning.
- **Limits (configurable, tenant setting, defaults):** file ≤ 100 MB; line ≤ 4 MB (calendar and
  memo blobs are long); ≤ 500,000 rows in total. Exceeding a limit fails the parse with a clear
  message. The observed 5.5 MB file has 14,384 lines; its longest values are calendar blobs.
- **Version.** Export versions 15.1 to 24.x are supported. Any other version parses with a
  warning. The version is never used to switch behaviour silently.
- **Traceability.** Every staged row carries `source_line` (1-based line of its `%R`).
- **Referential checks** (after tokenising; each failure is a *warning* and the row is kept
  unless stated otherwise):
  - `TASK.wbs_id` not in `PROJWBS` → activity staged with `wbs_external_id = NULL`. It is shown on the
    unmatched list.
  - `TASKPRED` endpoint not in the file, or `pred_proj_id ≠ proj_id` → stored as an *external*
    relationship (`is_external = true`, `external_pred_task_code`). It is not linked.
  - `TASKACTV`/`UDFVALUE`/`TASKRSRC` pointing at an unknown task, code, UDF type or resource →
    row dropped with a warning.
  - `TASK.clndr_id` not in `CALENDAR` → calendar `NULL`. Days-based display falls back to the
    project default calendar (§4.4).
  - Duplicate `task_code` within the chosen project → **error** (identity would be ambiguous).
- **Output.** A parse summary (`oe_p6_import.summary`): detected encoding, header fields kept,
  per-table row counts, interpreted and ignored tables, warning and error lists
  (`code`, `table`, `source_line`, `message`), and the counts that feed the preview (activities
  by `task_type`, grammar matches, unmatched rows). Warning and error codes are i18n keys.
- **Untrusted input.** The file comes from outside the tenant. No value is evaluated. HTML in
  memos is never rendered (§4.7). The upload goes through the existing `uploads` verification
  path before the parse job is enqueued.

---

## 3. Units and time

### 3.1 Dates are schedule wall-clock time
P6 writes dates without an offset. They are wall-clock times on the activity's calendar
(06:00 shift start, 17:30 finish). **Default (TD-611, user to confirm):**

- Stored as `timestamp without time zone` columns suffixed `_local`. They are interpreted in the
  project's `site_timezone` (projects table, master spec §4.1). They are never converted at import.
- Anything that needs an instant (calendar views, readiness checks, due-date comparisons) does
  the conversion with `site_timezone` at read time. A daylight-saving shift never moves a 06:00
  shift start.
- `oe_p6_import.data_date_local` = `PROJECT.last_recalc_date` (P6's data date). The observed
  file is an un-progressed baseline: data date 2026-05-15, all activities `TK_NotStart`.

### 3.2 Durations, float and lags are hours
XER stores every duration, float and lag in **hours** (`*_hr_cnt`). Calendars differ in hours
per day: the observed file uses 8.8, 11.5, 12 and 24 h/day across 28 calendars used by
activities. **Default (TD-611):** store hours exactly as `Numeric(14,4)`. Also store the
activity's calendar hours per day (`day_hr_cnt`) on the version row. Days for display
= `hours / day_hr_cnt`, rounded for display only. This answers TD-567's "hours or working days"
question: both are available, hours are the stored truth.

### 3.3 Quantities
`TASKRSRC` quantities are in the resource's unit. Labour is in hours. For material resources the
unit comes from `RSRC.unit_id` → `UMEASURE.unit_abbrev`. Quantities are mirrored as numbers with
that unit. `RSRC.cost_qty_type` is the time basis of the price, not a unit; it is ignored like
every other cost field. Costs are never mirrored (§4.6).

---

## 4. Table catalogue and column mapping

**R** = required column (missing → error). Every target table uses `BaseRecordMixin` and
project-scoped RLS (P6 spec §4). Every mirrored row carries `external_source = "p6"`,
`external_id`, `synced_at` and `import_id`.

### 4.1 Interpreted tables

| XER table | Observed rows | Target | Notes |
|---|---|---|---|
| `PROJECT` | 1 | `oe_p6_import` (source project fields) + preview choice | §4.2 |
| `PROJWBS` | 135 | `oe_p6_wbs` | §4.3 |
| `TASK` | 2,987 | `oe_p6_activity` (identity) + `oe_p6_activity_version` (snapshot) | §4.5 |
| `TASKPRED` | 3,809 | `oe_p6_relationship` | §4.5.3 |
| `CALENDAR` | 77 | `oe_p6_calendar` (new) | §4.4 |
| `ACTVTYPE`, `ACTVCODE`, `TASKACTV` | 2 / 6 / 2,958 | `oe_p6_code_type`, `oe_p6_code_value` (new); values on the version row `codes jsonb` | §4.5.4 |
| `UDFTYPE`, `UDFVALUE` | 2 / 1,348 | `oe_p6_udf_type` (new); values on the version row `udfs jsonb` | §4.5.4 |
| `RSRC`, `TASKRSRC` | 13 / 2,970 | `oe_p6_resource` (new), `oe_p6_resource_assignment` (new, per import) | §4.6 |
| `SCHEDOPTIONS` | 1 | `oe_p6_import.schedule_options jsonb` (a few keys) | §4.2 |

### 4.2 `PROJECT` and `SCHEDOPTIONS` → `oe_p6_import`

| XER column | Target column | Use |
|---|---|---|
| `proj_id` **R** | (row key inside the file only) | Filters child rows. Never stored as identity: it changes between databases. |
| `proj_short_name` **R** | `source_project_short_name` | Shown on the preview. It often carries the revision (`…Rev D-4`), so it **may change between revisions**. |
| `guid` | `source_project_guid` | Re-import of a different `guid` *or* a different short name than the last committed import → warning "different source project" on the preview. The planner confirms. Never blocks: revisions are often a copied project, so both may change while `task_code`s stay. |
| `last_recalc_date` **R** | `data_date_local` | §3.1 |
| `plan_start_date`, `scd_end_date` | `project_start_local`, `project_finish_local` | Preview header |
| `critical_path_type`, `critical_drtn_hr_cnt` | `critical_path_type`, `critical_float_threshold_hours` | Default for the critical definition (§4.5.2) |
| `clndr_id` | `default_calendar_id` | Fallback calendar |
| `last_schedule_date` | `last_scheduled_local` | Preview header ("scheduled on") |
| `SCHEDOPTIONS.sched_float_type`, `sched_retained_logic`, `sched_progress_override`, `sched_calendar_on_relationship_lag`, `enable_multiple_longest_path_calc` | `schedule_options jsonb` | Context for float and critical-path interpretation; display only |

All other `PROJECT` columns (≈ 60: earned-value defaults, numbering, flags) are ignored.

### 4.3 `PROJWBS` → `oe_p6_wbs`

| XER column | Target | Notes |
|---|---|---|
| `wbs_id` **R** | (file key) | Resolves `parent_wbs_id` and `TASK.wbs_id` within the file only |
| `guid` | `external_id` | Persistent identity. Fallback when absent: the `short_name` path from the root. |
| `parent_wbs_id` **R** | `parent_id`, `tree_path ltree` | The project node (`proj_node_flag = Y`) is the root. Its own parent points outside the file (to the EPS) and is ignored. |
| `wbs_short_name` **R** | `short_name` | Not unique (29 distinct across 135 nodes): only the path is. |
| `wbs_name` **R** | `name` | |
| `seq_num` | `sort_order` | Sibling order |
| `proj_node_flag` | `is_project_node` | |
| `status_code` | `status` | `WS_Open` / `WS_Whatif` / `WS_Closed` |

`oe_p6_wbs` is a **persistent identity**, like `oe_p6_activity`. Its key is `external_id`: the
`guid`, or when that is absent the short-name path from the root. Parse writes `oe_p6_wbs_stage`
rows (`import_id`, `external_id`, `parent_external_id`, `short_name`, `name`, `sort_order`,
`source_line`). Commit then upserts the identities and updates name, parent and order. Identities
missing from the file are marked `removed_from_schedule` and never deleted. Version rows store
`wbs_external_id`, so `wbs_moved` (P6 spec §6) compares external ids, never row ids.

Observed: depth up to 7 below the project node. Activities sit at depths 3–7, and 5 summary
nodes carry activities directly (allowed). Earned-value and cost columns are ignored.
**The WBS is not the asset tree.** 40 of the 62 assets named in activity names appear under
more than one WBS node. The WBS stays a schedule mirror (P6 spec §2, ADR 0003).

### 4.4 `CALENDAR` → `oe_p6_calendar` (new)

| XER column | Target | Notes |
|---|---|---|
| `clndr_id` **R** | `source_clndr_id` | With `import_id`, the row key (no identity across imports) |
| `clndr_name` **R** | `name` | Not unique (76 distinct of 77) |
| `clndr_type` | `calendar_type` | `CA_Base` / `CA_Project` / `CA_Rsrc` |
| `proj_id` | — | Project calendars carry it; global ones do not |
| `base_clndr_id` | `base_calendar_id` | Inheritance |
| `default_flag` | `is_default` | |
| `day_hr_cnt`, `week_hr_cnt`, `month_hr_cnt`, `year_hr_cnt` | same names | Day hours drive the days display (§3.2) |
| `clndr_data` | `raw_data text` | Kept byte-exact. It is a nested `(0||…()(…))` structure: weekday work periods `(s|06:00|f|12:00)` plus exceptions, `0x7F` separated. **Interpreting it (working-day maths, look-ahead in working days) is later (TD-613).** |

Calendars have **no identity across imports**. Names repeat (76 distinct of 77), so a name is
never a key. Rows are per import and keyed `(import_id, source_clndr_id)`. A version row references
its import's calendar row and also copies `calendar_day_hours`, so float in days never needs a
cross-import lookup.

### 4.5 `TASK` → `oe_p6_activity` + `oe_p6_activity_version`

#### 4.5.1 Identity and descriptive columns

| XER column | Target | Notes |
|---|---|---|
| `task_id` | (file key) | Internal P6 id; changes between databases. Never identity. |
| `task_code` **R** | `oe_p6_activity.external_activity_id` | **Identity** (P6 spec §6). Unique in the observed file (2,987 of 2,987). Prefixes seen: `C…` (activities), `MS…` (milestones). |
| `guid` | `oe_p6_activity.p6_guid` | Renumber detection (P6 spec §6) |
| `task_name` **R** | `name` (raw) | Fed to the `p6_grammar` rules (§5). Not unique: 27 names repeat. |
| `wbs_id` **R** | `wbs_external_id` | Resolved through §4.3 (the WBS `guid`, else its short-name path); never a row id |
| `clndr_id` | `calendar_id`, plus `calendar_day_hours` | §3.2 |
| `task_type` **R** | `task_type` (mapped; the value eligibility rules see, P6 spec §5.3) | `TT_Task` → `task`, `TT_Rsrc` → `resource_dependent`, `TT_Mile` → `start_milestone`, `TT_FinMile` → `finish_milestone`, `TT_LOE` → `level_of_effort`, `TT_WBS` → `wbs_summary`. Observed: 2,964 / 1 / 16 / 6 / 0 / 0. |
| `status_code` **R** | `status` | `TK_NotStart` → `not_started`, `TK_Active` → `in_progress`, `TK_Complete` → `complete`. Mirror only: it never drives KERP state (P6 spec §6). |
| `duration_type` | `duration_type` | Kept raw (`DT_FixedDrtn`, `DT_FixedQty`, …) |
| `phys_complete_pct` | `physical_pct_complete` | P6's own % (mirror). KERP's earned progress is separate (E-04-03). |
| `priority_type` | `priority` | Raw |
| `rsrc_id` | — | Primary resource; superseded by `TASKRSRC` (§4.6) |
| `create_date`, `update_date` | `p6_created_local`, `p6_updated_local` | Change-report context |
| `create_user`, `update_user` | — | P6 user names are personal data. **Not stored.** |

#### 4.5.2 Dates, durations, float (all `p6`-owned, P6 spec §7)

| XER column | Target (`_local` = §3.1) | Meaning |
|---|---|---|
| `target_start_date`, `target_end_date` | `planned_start_local`, `planned_finish_local` | P6 "Planned" dates |
| `early_start_date`, `early_end_date` | `early_start_local`, `early_finish_local` | |
| `late_start_date`, `late_end_date` | `late_start_local`, `late_finish_local` | Answers TD-570: late finish is mirrored |
| `restart_date`, `reend_date` | `remaining_start_local`, `remaining_finish_local` | Remaining early dates |
| `act_start_date`, `act_end_date` | `actual_start_local`, `actual_finish_local` | Empty in a baseline |
| `expect_end_date` | `expected_finish_local` | |
| `suspend_date`, `resume_date` | `suspend_local`, `resume_local` | |
| `cstr_type`, `cstr_date`, `cstr_type2`, `cstr_date2` | `constraint_type`, `constraint_local`, `constraint2_type`, `constraint2_local` | Raw P6 codes (`CS_MSO`, `CS_MSOA`, `CS_MEO`, `CS_MANDSTART`, …). Observed on 20 activities. |
| `target_drtn_hr_cnt`, `remain_drtn_hr_cnt` | `planned_duration_hours`, `remaining_duration_hours` | §3.2 |
| `total_float_hr_cnt` | `total_float_hours` | Answers TD-570. Observed: 13 activities ≤ 0 h, none negative. |
| `free_float_hr_cnt` | `free_float_hours` | |
| `driving_path_flag` | `driving_path` (bool) | Answers TD-570. Observed: 22 `Y`. |
| `float_path`, `float_path_order` | `float_path`, `float_path_order` | Multiple-float-path output when present (empty here) |
| `target_work_qty`, `remain_work_qty`, `act_work_qty` | `planned_work_qty`, `remaining_work_qty`, `actual_work_qty` | Labour units (§3.3) |

Derived on the version row: `start_local` = `actual_start_local` ?? `remaining_start_local`;
`finish_local` = `actual_finish_local` ?? `remaining_finish_local` (what P6 shows as
Start/Finish).

**Critical definition default (refines P6 spec §5.6, TD-567):** project setting
`p6.critical_definition` gains the default value **`from_schedule`**. It reads the committed
import's `critical_path_type`: `CT_TotFloat` → `total_float_le` with threshold
`critical_float_threshold_hours` (observed: 0 h); `CT_DrivPath` → `longest_path` using
`driving_path`. An explicit project value overrides the schedule's.

Columns ignored: earned-value/equipment quantities that are always 0 in a baseline
(`*_equip_qty`, `act_this_per_*`), `rev_fdbk_flag`, `lock_plan_flag`, `est_wt`,
`auto_compute_act_flag`, `complete_pct_type`, `tmpl_guid`, `location_id`, `crt_path_num`,
`external_*_date`. They are recoverable from the original if a later feature needs them.

`content_hash` (change detection, P6 spec §6) = SHA-256 over the canonical JSON of every
mapped `p6`-owned column of the version row, plus its sorted codes, UDFs and resource
assignments. It excludes `source_line`, `import_id` and P6 audit dates (`update_date` changes
on every touch without a schedule change).

#### 4.5.3 `TASKPRED` → `oe_p6_relationship`

| XER column | Target | Notes |
|---|---|---|
| `task_id` **R**, `pred_task_id` **R** | `succ_activity_id`, `pred_activity_id` | Resolved through `task_code` at commit |
| `pred_type` **R** | `type` | `PR_FS`/`PR_FF`/`PR_SS`/`PR_SF` → `FS`/`FF`/`SS`/`SF`. Observed 3,456 / 212 / 130 / 11. |
| `lag_hr_cnt` | `lag_hours` | May be **negative** (lead): 11 observed |
| `pred_proj_id` | `is_external` | ≠ `proj_id` → external (§2); the predecessor's `task_code` then goes in `external_pred_task_code` and `pred_activity_id` stays `NULL` |
| `aref`, `arls` | — | Computed relationship dates, recomputed by P6. Ignored. |
| `comments` | `comments` | |

Relationships are per import (no identity across imports). The change report compares them by
`(pred task_code, succ task_code, type)`.

#### 4.5.4 Activity codes and UDFs

- `ACTVTYPE` → `oe_p6_code_type` (`name` = `actv_code_type`, `scope` = `AS_Global` /
  `AS_EPS` / `AS_Project`, `max_length` = `actv_short_len`). `ACTVCODE` → `oe_p6_code_value`
  (`short_name`, `name`, `parent` for hierarchical codes, `color`, `sort_order`).
  Identity across imports: `(type name, value short_name)`.
- `TASKACTV` → version row `codes jsonb` = `{"<type name>": {"short": "…", "name": "…"}}`.
  An activity may carry one value per code type. Observed: two global code types, one per site
  (`<site> Status`), values *Shutdown*, *Online Works*, *On-Hold Priority 1/2*. 29 activities
  carry no code.
- `UDFTYPE` → `oe_p6_udf_type` (`label`, `table_name`, `logical_data_type`). Only
  `table_name = TASK` is interpreted for now; `PROJWBS`/`RSRC` UDFs are counted and ignored.
- `UDFVALUE` (`fk_id` = `task_id`) → version row `udfs jsonb` = `{"<label>": value}`, typed by
  `logical_data_type`:

  | Logical type | Read from |
  |---|---|
  | `FT_TEXT` | `udf_text` |
  | `FT_INT`, `FT_FLOAT_2_DECIMALS` | `udf_number` |
  | `FT_START_DATE`, `FT_END_DATE` | `udf_date` |
  | `FT_STATICTYPE` (indicator) | `udf_code_id`, stored as the indicator name |
  | `FT_MONEY` | **dropped**: money is never mirrored from P6 |

  Verify the list against the P6 data dictionary of the export version during T-0352; unknown
  types are kept as text with a warning. Observed: labels `Comment` and `Notes`; values `New` (1,222), `Secondary` (125) and
  one `Changed from "C" to "G"`. The P6 spec §6 shows these revision notes on the change report.
- Mapping a code or UDF to a node attribute (for example the status code → `work_window` /
  `hold_priority`) is a **mapping-profile field map** (P6 spec §5), not parser logic.

### 4.6 `RSRC`, `TASKRSRC` → resources without money

**Default (TD-611, user to confirm): mirror resources and assignment quantities, never cost.**
They are needed by compliance readiness (T-0267: resources of anchor-linked activities) and by
planner work plans (E-04-12). The observed resources are labour trades (painter/blaster,
scaffolder, mechanical, civil) under a parent hierarchy.

| XER | Target | Columns kept |
|---|---|---|
| `RSRC` | `oe_p6_resource` (identity `(project_id, short_name)`, `p6_guid`) | `rsrc_short_name` **R**, `rsrc_name`, `parent_rsrc_id` → `parent_id`, `rsrc_type` (`RT_Labor`/`RT_Equip`/`RT_Mat`), `clndr_id`, `active_flag`, `def_qty_per_hr`, `unit_id` → `UMEASURE.unit_abbrev` (`unit`). **Not kept:** `email_addr`, `employee_code`, phone numbers (personal data), `curr_id`, `ot_*`. |
| `TASKRSRC` | `oe_p6_resource_assignment` (per import, keyed `(task_code, resource short_name)`) | `target_qty`, `remain_qty`, `act_reg_qty` + `act_ot_qty` → `actual_qty`, `target_qty_per_hr`, `remain_qty_per_hr`, `target_start/end_date`, `restart/reend_date` (`_local`), `role_id`. **Not kept:** every `*cost*`, `cost_per_qty*`, `rate_type`, `acct_id`. |

An activity may have several assignments (9 observed with two). `RSRCRATE`, `CURRTYPE` and
cost columns are never read beyond tokenising (CLAUDE.md §6: money is a read-only mirror from
the ERP).

### 4.7 Tables not interpreted

| Tables | Why |
|---|---|
| `CURRTYPE`, `RSRCRATE`, `FINTMPL`, `ACCOUNT`, `PROJCOST`, `COSTTYPE`, `TASKFIN`, `TRSRCFIN`, `PROJFUND`, `BUDGCHNG` | Money: never from P6 |
| `UMEASURE` | Read only to resolve a resource's unit (§3.3) |
| `WBSMEMO`, `TASKMEMO`, `MEMOTYPE` | HTML notebook text. Not imported now. If a later feature needs it, sanitise HTML to text, never render raw. Counted in the summary. |
| `OBS`, `USERS`, `RSRCROLE`, `ROLES`, `ROLERATE`, `PROJPCAT`, `PCATTYPE`, `PCATVAL`, `RCATTYPE`, `RCATVAL`, `RSRCRCAT` | Organisation and personnel structures; KERP has its own (teams, ADR 0008) |
| `TASKPROC` (activity steps), `TASKNOTE`, `ISSUHIST`, `PROJISSU`, `PROJTHRS`, `RISKTYPE`, `SHIFT`, `SHIFTPER`, `NONWORK`, `PHASE`, `PROJEST`, `DOCUMENT`, `TASKDOC` | Not needed by the Phase 1 mapping; recoverable from the original |

---

## 5. Activity-name grammar — corpus evidence and shipped default

This section refines P6 spec §5.1 with the measured corpus. The grammar itself stays a
`p6_grammar` rule set in the rule store (ADR 0004 Amendment E).

### 5.1 What the names look like

| Shape of `task_name` | Count | Example (synthetic) |
|---|---|---|
| `<asset> / <type> / <code>` with code `9`, `9.9`, `9-9`, `9.9-9`, optional ` Online`/` Shut` | **2,867** | `CV-12 / SCA Erect / 512.1-16` |
| …of which two codes of equal length joined by a bare dash | 14 | `CV-12 / SCA Dism / 914-915` |
| code list or range with spaced dashes | 48 | `CV-12 / WFE / 902 - 903 - 904`, `728 - 732` |
| code plus a qualifier word | 27 | `CV-12 / B / 305 OHP`, `305 - Access`, `305-North`, `305 Props` |
| bare-dash run of three or more codes | 10 | `CV-12 / SCA Erect / 804-805-806` (all 10 observed are full codes) |
| three segments, no code | 8 | `CV-12 / Prov Sums / Painters` |
| no ` / ` at all | 27 | `Contract award` (22 milestones, 4 tasks, 1 resource-dependent) |

The shipped default regex in P6 spec §5.1 matches 2,867 of 2,960 three-segment names
(96.9 %). The P6 spec §2 figure "2,960 follow the grammar" counted the separators, not the
regex. It is corrected there.

Other findings:
- **Work-anchor codes are only unique under their asset.** 108 of 297 distinct codes appear
  under more than one asset. Node identity is `(asset node, code[-suffix])`. A code alone is
  never a key (already implied by P6 spec §5.1; now a test).
- Activity-type spellings: `SCA Erect`/`SCA-Erect`, `SCA Dism`/`SCA-Dism`,
  `Prov Sums`/`Prov.Sums`. All three pairs go in the shipped alias dictionary (P6 spec §5.2).
- Activity-type distribution (matched rows): WFE 525, B 379, P 379, SCA Erect 362,
  SCA Dism 361, W 249, CP 189, SR 119, BN 119, G 64, GR/HDNR1/HDNR2 21 each, Custom 18,
  HDBN 17, C 14, Prov Sums 9.

### 5.2 Shipped default `p6_grammar` rules (user decisions 2026-11-23, TD-612)

The rules are tried in `sort_order` and the first match wins. They use RE2 named groups. One
qualifier fragment is shared by all three rules: it accepts letters after a space or a dash. A
project may narrow it to a word list through a rule parameter. A qualifier never touches the
digits of the code.

```
QUALIFIER = (?:(?: - |-| )(?P<qualifier>[A-Za-z][A-Za-z .]*?))?
```

1. **Single code** (P6 spec §5.1) with an optional `qualifier`.
   ```
   ^(?P<asset>.+?) / (?P<activity_type>[^/]+?) / (?P<anchor_code>\d+(?:\.\d+)?)(?:-(?P<suffix>\d+))?(?: (?P<window>Online|Shut))?QUALIFIER$
   ```
   - A suffix shorter than the code stays a suffix (TD-076): `900-16` and `913.1-16` are their own
     work-anchor nodes.
   - A suffix as long as the code names a **second code**: `914-915` is RSWs 914 and 915.
2. **Spaced list or range.** RE2 cannot capture repeats, so the whole run is one group
   (`anchor_list`). The mapper splits it on the rule parameter `list_separator` (default `" - "`).
   - **Two whole numbers joined by the separator are a range** (user, 2026-11-23): `728 - 732` is
     728, 729, 730, 731 and 732.
   - A range is capped by `max_range` (default 50). A longer or backwards pair is taken as listed.
   - Decimals are always listed: `410.2 - 410.5` is two codes.
   ```
   ^(?P<asset>.+?) / (?P<activity_type>[^/]+?) / (?P<anchor_list>\d+(?:\.\d+)?(?: - \d+(?:\.\d+)?)+)(?: (?P<window>Online|Shut))?QUALIFIER$
   ```
3. **Bare-dash run of three or more codes** (`anchor_chain`, user, 2026-11-23).
   - Every later part as long as the first → full codes: `804-805-806` is 804, 805 and 806, and
     `909-910-911-914` is 909, 910, 911 and 914.
   - Later parts shorter than the first → numbered items of the first: `410-2-3` is 410, 410.2 and
     410.3.
   - Gaps are never filled. A 410.1 that another contractor did, or that was found not to be
     needed, is simply not named.
   - Any other shape (a later part longer than the first, a decimal first part) falls through to
     manual linking.
   - Deliberate asymmetry (user, 2026-11-23): a bare two-part `410-2` stays one node `410-2`
     (TD-076 suffix), while the same item inside a run of three or more, `410-2-3`, is `410.2`. A
     project that numbers items with dashes everywhere can change the rule-1 suffix parameter.
   ```
   ^(?P<asset>.+?) / (?P<activity_type>[^/]+?) / (?P<anchor_chain>\d+(?:-\d+){2,})(?: (?P<window>Online|Shut))?QUALIFIER$
   ```
4. Everything else (no code, no separators, unclear runs) → **Unmatched-Row Linking Screen**
   (T-0105). Never guessed.

**Several codes in one activity.** Each code becomes its own work-anchor node and its own
`oe_p6_anchor_link`, with `relationship_type = shared` and `allocation_pct = NULL`.

**Qualifiers** (`OHP`, `Access`, `Props`, `North`, `Water Diversion`) are notes (user,
2026-11-23).
- They are stored on the link as `qualifier` and resolve to the same node as the code without
  them.
- They usually mark part of an RSW done separately: waiting on safe access, a scaffold, or
  equipment being shut down.
- Several activities on one RSW, one per part or visit, are normal: the link is many-to-many.

**Provisional sums named without a code** (`Prov Sums / Painters`) never reach the activity-type
eligibility rule, because nothing parsed. A shipped `p6_eligibility` rule therefore also takes
`name` containing `Prov Sums` / `Prov.Sums` out of scope.

**Breakdown levels from the WBS** (user, 2026-11-23: "custom for each project"). The mapping
profile may map a WBS depth to a breakdown level, for example WBS level 3 → Site and level 4 →
Area (1 = the first level under the project node). The value is the WBS name or code, and
missing nodes are created like the asset ones. This mapping only places new nodes: the WBS is
still never the asset tree (§4.3). Other projects may need none, or other depths.

Measured on the observed corpus with these defaults (rules only):

| Outcome | Activities |
|---|---|
| Parsed by a grammar and in scope | 2,923 |
| Out of scope by an eligibility rule, whether or not the name parsed (milestones, provisional sums incl. those without a code, `Custom`) | 59 |
| Not parsed and in scope → manual linking (general activities such as mobilisation and demobilisation) | 5 |

The exact figures are fixture assertions on the synthetic corpus, not on the client file.

---

## 6. Staging shape (the contract XML must also meet)

Parse writes, inside one transaction per import, rows tagged with `import_id` and nothing
else points to them yet:

- `oe_p6_activity_version` rows with `external_activity_id` (= `task_code`) and
  `activity_id = NULL` until commit;
- `oe_p6_wbs_stage` rows (§4.3), per-import `oe_p6_calendar` rows (§4.4), `oe_p6_code_type`/`_value`, `oe_p6_udf_type`,
  `oe_p6_resource` candidates and `oe_p6_resource_assignment`, `oe_p6_relationship`;
- the grammar result per version row (`parsed jsonb`: matched rule id and groups, or
  `unmatched` with the reason).

Commit (P6 spec §6) creates or updates the `oe_p6_activity` and `oe_p6_wbs` identities, sets
`activity_id` and `current_version_id`, and ensures placeholders and links. Setting
`activity_id` is the only write a version row ever receives after parse. Version rows are
otherwise immutable.

Discard marks the import `discarded`. Its staged rows are kept, unreferenced, for audit. No P6
row is ever hard-deleted: BaseRecordMixin forbids it, and KERP has no hard purge of project data
(TD-401, ADR 0018). Any future retention purge belongs to that work and must consult legal hold.

The Primavera XML parser (T-0105) maps `<Project>`, `<WBS>`, `<Activity>`, `<Relationship>`,
`<Calendar>`, `<ActivityCodeType>`/`<ActivityCode>`/`<ActivityCodeAssignment>`,
`<UDFType>`/`<UDFValue>`, `<Resource>`/`<ResourceAssignment>` to the same rows. XML dates
carry no offset either, so §3.1 applies unchanged.

---

## 7. Synthetic fixtures

`backend/tests/fixtures/p6/` holds a **generator** (`make_xer.py`, stdlib) rather than
hand-edited files, plus the small generated files committed for readability. It produces:

| Fixture | Content |
|---|---|
| `small_rev_a.xer` | ≈ 60 activities across a 5-level WBS. Every §5.1 name shape at least once (including `728 - 732`, `804-805-806`, `410-2-3`, `914-915`, `900-16`), all six mapped `task_type`s, 3 constraints, all four relationship types, one negative lag, an activity with two resource assignments, two code types, a doubled-quote UDF value, cp1252 characters (`£`, `°`, `€`), `0x7F` calendar blobs, a `1899-12-30` sentinel, a CRLF line ending, two currencies and a rate row (must be ignored). |
| `small_rev_b.xer` | Revision of rev A for P6 spec §11 test 3: changed dates, a rename that changes the parsed code, a WBS move, a removed activity, a new activity, a renumber (same `guid`, new `task_code`), a changed UDF note, a new copied-project `guid`. |
| `multi_project.xer` | Two `PROJECT`s; the pre-scan lists both (`awaiting_project_choice`), only the chosen one is staged. |
| `broken/*.xer` | Missing `%E`; row wider than `%F`; missing required column; duplicate `task_code`; dangling `wbs_id`, `pred_task_id`, code id; UTF-8 with BOM. |
| `scale.xer` (generated in CI, not committed) | ≈ 3,000 activities, 3,800 relationships, 3,000 assignments, 80 calendars, ≈ 6 MB: the observed scale. |

All names come from a synthetic vocabulary (assets `CV-01…`, `TK-…`, codes 100–999). No
string from a real schedule.

---

## 8. Acceptance tests (in addition to P6 spec §11)

Parser (pure unit tests, no DB):
- X1. `small_rev_a.xer` tokenises into the expected per-table counts. Every staged row has the
  right `source_line`.
- X2. cp1252 detected for rev A and UTF-8 for the BOM file. `£`/`€` round-trip correctly.
  Replacement count reported.
- X3. `""` decodes to `"`. `0x7F` in memo text becomes `\n`. Calendar `raw_data` is byte-exact.
- X4. `1899-12-30 00:00` → `NULL`. Dates stay wall-clock (no offset applied). Hours are
  `Decimal`, not float.
- X5. Each `broken/*.xer` fails or warns exactly as §2 says (fatal tables fail the import, other
  tables drop the row), with the table, line and i18n code.
- X6. Columns are matched by name: rev A with `TASK` columns reordered and an unknown extra
  column parses identically.
- X7. Unknown table → counted, ignored. Cost columns, `RSRCRATE`, `CURRTYPE`, `FT_MONEY` UDFs,
  P6 user names and resource e-mails/phones are absent from every staged row.

Staging and mapping (DB):
- X8. `multi_project.xer` stops in `awaiting_project_choice` with both projects in the summary;
  after `select-project` only the chosen project is staged. A single-project file never stops.
- X9. Grammar defaults: every §5.1 shape lands as specified.
  - `902 - 903 - 904` → three `shared` links; `728 - 732` → five links (728–732).
  - `804-805-806` → three links; `410-2-3` → 410, 410.2, 410.3 (no 410.1).
  - `914-915` → two links; `900-16` → one node `900-16`.
  - `305 OHP` → node `305` with `qualifier = OHP`.
  - `Prov Sums / Painters` → out of scope; a no-code row → unmatched.
- X9a. With WBS-level mappings 3 → Site and 4 → Area, new assets are created under the Site and
  Area named by their WBS; without them, behaviour is unchanged.
- X10. The same code under two assets → two distinct work-anchor nodes.
- X11. Critical definition `from_schedule` with `CT_TotFloat`, threshold 0 → `on_critical_path`
  for exactly the fixture activities with total float ≤ 0. A project override wins.
- X12. `content_hash` is unchanged when only `update_date` differs, and changes when a date,
  code, UDF or assignment quantity changes.
- X13. `scale.xer` parses and stages within the NFR budget: **≤ 30 s and ≤ 512 MB RSS**. The
  check runs in a dedicated `perf` CI job (pytest marker `perf`) on a fixed runner size, not in
  the main suite, so shared-runner noise cannot fail unrelated PRs. The budget is recorded for
  T-0146.

---

## 9. Defaults to confirm

| Item | Default in this file | TODO |
|---|---|---|
| Wall-clock dates in `site_timezone`, hours as stored truth with calendar day-hours, critical definition `from_schedule`, resources and quantities mirrored without cost, P6 user names not stored | §3, §4.5.2, §4.6 | TD-611 |
| ~~Multi-code names, ranges, qualifiers, bare-dash runs~~: decided by the user 2026-11-23 | §5.2 | TD-612 (resolved) |
| ~~Breakdown levels from the WBS~~: per project, decided by the user 2026-11-23 | §5.2 | — |
| Share an activity's hours over the days inside a work plan window | later | TD-614 |
| Interpreting calendar work patterns and exceptions | later | TD-613 |
