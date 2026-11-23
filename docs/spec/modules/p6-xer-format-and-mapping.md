# Module detail — P6 XER file format, parser contract and field mapping

Companion to [p6-import-and-rsw-matching.md](p6-import-and-rsw-matching.md) (the "P6 spec").
The P6 spec says **what** the import does (identity, mapping profile, change report, field
ownership, checkout merge). This file says **how the file is read**: the XER layout, the parser
contract, which tables and columns are interpreted, how each lands in the `oe_p6_*` mirror,
and what the synthetic fixtures must contain. Primavera XML (T-0105) must produce the same
staging shape (§6), so this file is also the contract for that parser.

Precedence: module detail (3). Built by T-0104 (parser and staging, mapping) and T-0105 (XML).

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
| Header | First line, tag `ERMHDR`. Fields in order: export version, export date, export type (`Project`), login, user name, database, module, base currency. Version and export date are stored on `oe_p6_import`. Login and user name are personal data. They are **not** stored: only the fact that they were present. |
| Line ending | CRLF in the observed file. Accept CRLF and LF. |
| Encoding | Windows-1252 in the observed file (`£`, `¥`, `€` currency symbols). Rule: a UTF-8 BOM, or strict UTF-8 decoding of the whole file succeeding → UTF-8. Otherwise cp1252 with replacement. Record the detected encoding and the count of replacement characters (warning if > 0). |
| Empty value | Empty string = `NULL`. |
| Quotes | No CSV quoting. A literal `"` inside a value is written doubled (`""`). Evidence: an HTML memo whose DOCTYPE reads `""-//W3C//DTD HTML…` and a UDF value `Changed from ""C"" to ""G""`. The parser decodes `""` → `"`. |
| Embedded line breaks | Multi-line values (memos, calendar data, OBS description) use `0x7F` as the line separator. The parser maps `0x7F` → `\n` in memo and description text. It keeps calendar data byte-exact (§4.4). |
| Dates | `YYYY-MM-DD HH:MM`, no seconds, no offset (§3.1). `1899-12-30 00:00` is P6's null sentinel → `NULL`. |
| Numbers | Decimal point `.`, no thousands separator, may carry long fractions (`14026.6333333333`). Parsed as `Decimal`, never float. |
| Booleans | `Y` / `N`. |
| Enumerations | P6 codes such as `TT_Task`, `TK_NotStart`, `PR_FS`, `CS_MSO`. Mapped per §4. An unknown code is kept raw with a warning, never rejected. |
| End marker | `%E`. A missing end marker means the file is truncated. That is an error and nothing is staged. |

A file can contain **several projects** (several `PROJECT` rows, child rows tagged with
`proj_id`). The observed file has one. When there are several, the upload preview lists them
(`proj_short_name`, `name` of the root WBS, activity count) and the planner picks one. Only
that project's rows are staged.

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
- **Row width.** More values than `%F` columns → error for that row (row skipped, import
  `failed` if the row is required). Fewer values → missing trailing values are `NULL`.
- **Limits (configurable, tenant setting, defaults):** file ≤ 100 MB; line ≤ 4 MB (calendar and
  memo blobs are long); ≤ 500,000 rows in total. Exceeding a limit fails the parse with a clear
  message. The observed 5.5 MB file has 14,384 lines; its longest values are calendar blobs.
- **Version.** Export versions 15.1 to 24.x are supported. Any other version parses with a
  warning. The version is never used to switch behaviour silently.
- **Traceability.** Every staged row carries `source_line` (1-based line of its `%R`).
- **Referential checks** (after tokenising; each failure is a *warning* and the row is kept
  unless stated otherwise):
  - `TASK.wbs_id` not in `PROJWBS` → activity staged with `wbs_id = NULL`. It is shown on the
    unmatched list.
  - `TASKPRED` endpoint not in the file, or `pred_proj_id ≠ proj_id` → stored as an *external*
    relationship (`external_pred_task_ref`). It is not linked.
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
  project's `site_timezone` (projects spec §1.2), and never converted at import.
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
`TASKRSRC` quantities are resource units in the resource's unit (labour = hours). They are
mirrored as numbers with the resource's `cost_qty_type`. Costs are never mirrored (§4.6).

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
| `wbs_id` **R** | (file key) | Resolves `parent_wbs_id` and `TASK.wbs_id` |
| `guid` | `external_id` | Persistent identity. Fallback when absent: the `short_name` path from the root. |
| `parent_wbs_id` **R** | `parent_id`, `tree_path ltree` | The project node (`proj_node_flag = Y`) is the root. Its own parent points outside the file (to the EPS) and is ignored. |
| `wbs_short_name` **R** | `short_name` | Not unique (29 distinct across 135 nodes): only the path is. |
| `wbs_name` **R** | `name` | |
| `seq_num` | `sort_order` | Sibling order |
| `proj_node_flag` | `is_project_node` | |
| `status_code` | `status` | `WS_Open` / `WS_Whatif` / `WS_Closed` |

Observed: depth up to 7 below the project node. Activities sit at depths 3–7, and 5 summary
nodes carry activities directly (allowed). Earned-value and cost columns are ignored.
**The WBS is not the asset tree.** 40 of the 62 assets named in activity names appear under
more than one WBS node. The WBS stays a schedule mirror (P6 spec §2, ADR 0003).

### 4.4 `CALENDAR` → `oe_p6_calendar` (new)

| XER column | Target | Notes |
|---|---|---|
| `clndr_id` **R** | (file key) | |
| `clndr_name` **R** | `name` | Not unique (76 distinct of 77) |
| `clndr_type` | `calendar_type` | `CA_Base` / `CA_Project` / `CA_Rsrc` |
| `proj_id` | — | Project calendars carry it; global ones do not |
| `base_clndr_id` | `base_calendar_id` | Inheritance |
| `default_flag` | `is_default` | |
| `day_hr_cnt`, `week_hr_cnt`, `month_hr_cnt`, `year_hr_cnt` | same names | Day hours drive the days display (§3.2) |
| `clndr_data` | `raw_data text` | Kept byte-exact. It is a nested `(0||…()(…))` structure: weekday work periods `(s|06:00|f|12:00)` plus exceptions, `0x7F` separated. **Interpreting it (working-day maths, look-ahead in working days) is later (TD-613).** |

Identity of a calendar across imports: `(name, calendar_type)`. Calendars are written per import
(snapshot) because they are small.

### 4.5 `TASK` → `oe_p6_activity` + `oe_p6_activity_version`

#### 4.5.1 Identity and descriptive columns

| XER column | Target | Notes |
|---|---|---|
| `task_id` | (file key) | Internal P6 id; changes between databases. Never identity. |
| `task_code` **R** | `oe_p6_activity.external_activity_id` | **Identity** (P6 spec §6). Unique in the observed file (2,987 of 2,987). Prefixes seen: `C…` (activities), `MS…` (milestones). |
| `guid` | `oe_p6_activity.p6_guid` | Renumber detection (P6 spec §6) |
| `task_name` **R** | `name` (raw) | Fed to the `p6_grammar` rules (§5). Not unique: 27 names repeat. |
| `wbs_id` **R** | `wbs_id` | via §4.3 |
| `clndr_id` | `calendar_id`, plus `calendar_day_hours` | §3.2 |
| `task_type` **R** | `task_type` | `TT_Task` → `task`, `TT_Rsrc` → `resource_dependent`, `TT_Mile` → `start_milestone`, `TT_FinMile` → `finish_milestone`, `TT_LOE` → `level_of_effort`, `TT_WBS` → `wbs_summary`. Observed: 2,964 / 1 / 16 / 6 / 0 / 0. |
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
| `pred_proj_id` | `is_external` | ≠ `proj_id` → external (§2) |
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
  `logical_data_type`: `FT_TEXT` → `udf_text`, `FT_INT`/`FT_FLOAT_2_DECIMALS`/`FT_MONEY` →
  `udf_number`, `FT_START_DATE`/`FT_END_DATE`/`FT_STATICTYPE` date → `udf_date`,
  `FT_INDICATOR` → `udf_code_id`/text. **`FT_MONEY` UDFs are dropped**: money is never mirrored
  from P6. Observed: labels `Comment` and `Notes`; values `New` (1,222), `Secondary` (125) and
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
| `RSRC` | `oe_p6_resource` (identity `(project_id, short_name)`, `p6_guid`) | `rsrc_short_name` **R**, `rsrc_name`, `parent_rsrc_id` → `parent_id`, `rsrc_type` (`RT_Labor`/`RT_Equip`/`RT_Mat`), `clndr_id`, `active_flag`, `def_qty_per_hr`, `cost_qty_type` (unit). **Not kept:** `email_addr`, `employee_code`, phone numbers (personal data), `curr_id`, `ot_*`. |
| `TASKRSRC` | `oe_p6_resource_assignment` (per import, keyed `(task_code, resource short_name)`) | `target_qty`, `remain_qty`, `act_reg_qty` + `act_ot_qty` → `actual_qty`, `target_qty_per_hr`, `remain_qty_per_hr`, `target_start/end_date`, `restart/reend_date` (`_local`), `role_id`. **Not kept:** every `*cost*`, `cost_per_qty*`, `rate_type`, `acct_id`. |

An activity may have several assignments (9 observed with two). `RSRCRATE`, `CURRTYPE` and
cost columns are never read beyond tokenising (CLAUDE.md §6: money is a read-only mirror from
the ERP).

### 4.7 Tables not interpreted

| Tables | Why |
|---|---|
| `CURRTYPE`, `RSRCRATE`, `FINTMPL`, `ACCOUNT`, `PROJCOST`, `COSTTYPE`, `TASKFIN`, `TRSRCFIN`, `PROJFUND`, `BUDGCHNG` | Money: never from P6 |
| `WBSMEMO`, `TASKMEMO`, `MEMOTYPE` | HTML notebook text. Not imported now. If a later feature needs it, sanitise HTML to text, never render raw. Counted in the summary. |
| `OBS`, `USERS`, `RSRCROLE`, `ROLES`, `ROLERATE`, `PROJPCAT`, `PCATTYPE`, `PCATVAL`, `RCATTYPE`, `RCATVAL`, `RSRCRCAT` | Organisation and personnel structures; KERP has its own (teams, ADR 0008) |
| `TASKPROC` (activity steps), `TASKNOTE`, `ISSUHIST`, `PROJISSU`, `PROJTHRS`, `RISKTYPE`, `SHIFT`, `SHIFTPER`, `UMEASURE`, `NONWORK`, `PHASE`, `PROJEST`, `DOCUMENT`, `TASKDOC` | Not needed by the Phase 1 mapping; recoverable from the original |

---

## 5. Activity-name grammar — corpus evidence and shipped default

This section refines P6 spec §5.1 with the measured corpus. The grammar itself stays a
`p6_grammar` rule set in the rule store (ADR 0004 Amendment E).

### 5.1 What the names look like

| Shape of `task_name` | Count | Example (synthetic) |
|---|---|---|
| `<asset> / <type> / <code>` with code `9`, `9.9`, `9-9`, `9.9-9`, optional ` Online`/` Shut` | **2,867** | `CV-12 / SCA Erect / 512.1-16` |
| code list with spaced dashes | 48 | `CV-12 / WFE / 902 - 903 - 904` |
| code plus a qualifier word | 27 | `CV-12 / B / 305 OHP`, `305 - Access`, `305-North`, `305 Props` |
| unspaced chain of three or more numbers | 10 | `CV-12 / P / 410-2-3` |
| three segments, no code | 8 | `CV-12 / Custom / Painters` |
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

### 5.2 Shipped default `p6_grammar` rules (default pending TD-612)

Tried in `sort_order`. The first match wins. RE2 named groups:

1. **Single code** (P6 spec §5.1), extended with an optional `qualifier` group. The qualifier
   vocabulary is a rule parameter (default `OHP`, `Access`, `Props`, `North`,
   `Water Diversion`). It is stored on the link as `qualifier`; the node is the same node as
   without it.
   ```
   ^(?P<asset>.+?) / (?P<activity_type>[^/]+?) / (?P<anchor_code>\d+(?:\.\d+)?)(?:-(?P<suffix>\d+))?(?: (?P<window>Online|Shut))?(?:\s*-?\s*(?P<qualifier>OHP|Access|Props|North|Water Diversion))?$
   ```
2. **Code list.** RE2 cannot capture repeats, so the whole list is one group. The mapper splits
   it on the rule parameter `list_separator` (default `" - "`). Each element becomes its own
   work-anchor node and its own `oe_p6_anchor_link` with `relationship_type = shared` and
   `allocation_pct = NULL`. **Listed codes only: a list is never expanded as a range**
   (`728 - 732` links 728 and 732, not 729–731).
   ```
   ^(?P<asset>.+?) / (?P<activity_type>[^/]+?) / (?P<anchor_list>\d+(?:\.\d+)?(?: - \d+(?:\.\d+)?)+)(?: (?P<window>Online|Shut))?(?:\s*-?\s*(?P<qualifier>OHP|Access|Props))?$
   ```
3. Everything else (unspaced chains, no code, no separators) → **Unmatched-Row Linking Screen**
   (T-0105). Never guessed.

With these defaults the observed corpus would map ≈ 2,940 activities by rule and leave
≈ 45 for manual linking (27 unsegmented, mostly milestones that are ineligible anyway; 10
chains; 8 without a code). The exact figures are a fixture assertion on the synthetic corpus,
not on the client file.

---

## 6. Staging shape (the contract XML must also meet)

Parse writes, inside one transaction per import, rows tagged with `import_id` and nothing
else points to them yet:

- `oe_p6_activity_version` rows with `external_activity_id` (= `task_code`) and
  `activity_id = NULL` until commit;
- `oe_p6_wbs` snapshot rows, `oe_p6_calendar`, `oe_p6_code_type`/`_value`, `oe_p6_udf_type`,
  `oe_p6_resource` candidates and `oe_p6_resource_assignment`, `oe_p6_relationship`;
- the grammar result per version row (`parsed jsonb`: matched rule id and groups, or
  `unmatched` with the reason).

Commit (P6 spec §6) creates or updates `oe_p6_activity` identities, sets `activity_id` and
`current_version_id`, and ensures placeholders and links. Discard marks the import `discarded`.
A discarded import's staging rows are purged after 30 days. They are the only P6 rows ever
deleted, because nothing ever referenced them.

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
| `small_rev_a.xer` | ≈ 60 activities across a 5-level WBS. Every §5.1 name shape at least once, all four `task_type`s seen, 3 constraints, all four relationship types, one negative lag, an activity with two resource assignments, two code types, a doubled-quote UDF value, cp1252 characters (`£`, `°`, `€`), `0x7F` calendar blobs, a `1899-12-30` sentinel, a CRLF line ending, two currencies and a rate row (must be ignored). |
| `small_rev_b.xer` | Revision of rev A for P6 spec §11 test 3: changed dates, a rename that changes the parsed code, a WBS move, a removed activity, a new activity, a renumber (same `guid`, new `task_code`), a changed UDF note, a new copied-project `guid`. |
| `multi_project.xer` | Two `PROJECT`s; the preview lists both, only the chosen one is staged. |
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
- X5. Each `broken/*.xer` fails or warns exactly as §2 says, with the table, line and i18n code.
- X6. Columns are matched by name: rev A with `TASK` columns reordered and an unknown extra
  column parses identically.
- X7. Unknown table → counted, ignored. Cost columns, `RSRCRATE`, `CURRTYPE`, `FT_MONEY` UDFs,
  P6 user names and resource e-mails/phones are absent from every staged row.

Staging and mapping (DB):
- X8. `multi_project.xer` stages only the chosen project.
- X9. Grammar defaults: every §5.1 shape lands as specified; `902 - 903 - 904` → three links
  `shared`; `728 - 732` → two links (no range); `305 OHP` → node `305`, `qualifier = OHP`;
  chain and no-code rows → unmatched.
- X10. The same code under two assets → two distinct work-anchor nodes.
- X11. Critical definition `from_schedule` with `CT_TotFloat`, threshold 0 → `on_critical_path`
  for exactly the fixture activities with total float ≤ 0. A project override wins.
- X12. `content_hash` is unchanged when only `update_date` differs, and changes when a date,
  code, UDF or assignment quantity changes.
- X13. `scale.xer` parses and stages within the NFR budget: **≤ 30 s and ≤ 512 MB RSS on the CI
  worker** (budget recorded for T-0146).

---

## 9. Defaults to confirm

| Item | Default in this file | TODO |
|---|---|---|
| Wall-clock dates in `site_timezone`, hours as stored truth with calendar day-hours, critical definition `from_schedule`, resources and quantities mirrored without cost, P6 user names not stored | §3, §4.5.2, §4.6 | TD-611 |
| Multi-code names (list = several nodes, never a range), qualifier words, unspaced chains to manual linking | §5.2 | TD-612 |
| Interpreting calendar work patterns and exceptions | later | TD-613 |
