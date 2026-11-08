---
description: KERP planner pass — spec-check a phase and break its needs-breakdown epics into small/medium tasks, delivered as a reviewed PR to dev.
argument-hint: "<phase e.g. 3> | <epic id e.g. E-03-02> | check"
---

Run the planning stage of the KERP pipeline for `$ARGUMENTS`.

1. `git fetch origin && git switch -c plan/<arg>-$(date +%Y%m%d) origin/dev`
2. Dispatch **kerp-planner**:
   - `check` → spec-check every `todo` task in the current phase; fix tasks or raise TODOs.
   - phase / epic id → spec-check, then break down every `needs-breakdown` epic in scope whose dependencies are met.
3. Verify: `python scripts/tracker.py validate && python scripts/tracker.py render --check && python -m pytest -q scripts/tests`.
4. Open a PR to `dev` titled `plan: <arg>` and run steps 4–5 of `/kerp-run` (independent **kerp-reviewer** review → merge). The reviewer checks: tasks are S/M, testable, spec-referenced, dependencies correct, nothing in the spec dropped.
5. Report the tasks created and any `decision`/`spec-gap` TODOs that need the human.
