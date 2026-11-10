"""ci-gate verdict for .github/workflows/ci.yml (T-0028).

Reads ``NEEDS`` (``toJSON(needs)`` of the ci-gate job) and ``EVENT_NAME``. Every needed
job must be ``success``. A job may be ``skipped`` only when it is expected to skip: its
component does not exist yet (``detect`` output ``false``) or, for the publish job, the
event is not a push. Anything else (failure, cancelled, unexpected skip, missing job,
``detect`` itself not green) fails the gate.
"""
from __future__ import annotations

import json
import os
import sys

# job -> (detect output that enables it or None, push-only)
EXPECTED: dict[str, tuple[str | None, bool]] = {
    "detect": (None, False),
    "tracker": (None, False),
    "backend": ("backend", False),
    "backend-image": ("backend", False),
    "backend-publish": ("backend", True),
    "frontend": ("frontend", False),
    "e2e": ("e2e", False),
}


def evaluate(needs: dict[str, dict], event_name: str) -> list[str]:
    """Return the list of problems; empty means the gate passes."""
    problems: list[str] = []
    detect = needs.get("detect", {})
    flags = detect.get("outputs") or {}
    for job, (component, push_only) in EXPECTED.items():
        result = needs.get(job, {}).get("result")
        should_run = (component is None or flags.get(component) == "true") and (
            not push_only or event_name == "push"
        )
        want = "success" if should_run else "skipped"
        if job != "detect" and detect.get("result") != "success":
            want = "success"  # nothing can be trusted to have skipped legitimately
        if result != want:
            problems.append(f"{job}: {result or 'missing'} (expected {want})")
    return problems


def main() -> int:
    needs = json.loads(os.environ.get("NEEDS") or "{}")
    event_name = os.environ.get("EVENT_NAME", "")
    for job in EXPECTED:
        print(f"{job}={needs.get(job, {}).get('result', 'missing')}")
    problems = evaluate(needs, event_name)
    for problem in problems:
        print(f"::error::ci-gate: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
