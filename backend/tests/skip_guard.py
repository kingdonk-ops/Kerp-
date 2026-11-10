"""pytest plugin: with ``KERP_FAIL_ON_SKIP=1`` a run that skipped any test fails.

``make test-pg`` sets it, so the PostgreSQL-only tests (RLS bypass, audit ledger) cannot
silently skip when the local stack is missing or misconfigured. Registered in ``conftest.py``.
"""

import os

import pytest


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if os.environ.get("KERP_FAIL_ON_SKIP") != "1" or exitstatus != pytest.ExitCode.OK:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = len(reporter.stats.get("skipped", [])) if reporter is not None else 0
    if skipped:
        if reporter is not None:
            reporter.write_line(
                f"KERP_FAIL_ON_SKIP=1: {skipped} test(s) skipped, failing the run "
                "(is the PostgreSQL stack up and KERP_DATABASE_URL set?)",
                red=True,
            )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
