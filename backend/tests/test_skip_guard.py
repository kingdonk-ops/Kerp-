"""``KERP_FAIL_ON_SKIP=1`` turns any skipped test into a failing run (``make test-pg``, T-0003).

Without it, a missing or misconfigured local PostgreSQL would make the RLS tests skip and
the run would still look green.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

SAMPLE = """
import pytest

def test_runs():
    assert True

@pytest.mark.skipif({skip}, reason="no stack")
def test_maybe_skipped():
    assert True
"""


def _run(
    tmp_path: Path, *, skip: bool, fail_on_skip: str | None
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_sample.py").write_text(SAMPLE.format(skip=skip), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "KERP_FAIL_ON_SKIP"}
    env["PYTHONPATH"] = str(BACKEND)
    if fail_on_skip is not None:
        env["KERP_FAIL_ON_SKIP"] = fail_on_skip
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:cov",
            "-p",
            "tests.skip_guard",
            "-c",
            str(tmp_path / "pytest.ini"),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("fail_on_skip", [None, "0", ""])
def test_skips_are_allowed_by_default(tmp_path: Path, fail_on_skip: str | None) -> None:
    result = _run(tmp_path, skip=True, fail_on_skip=fail_on_skip)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_skip_fails_the_run_when_requested(tmp_path: Path) -> None:
    result = _run(tmp_path, skip=True, fail_on_skip="1")
    assert result.returncode == pytest.ExitCode.TESTS_FAILED, result.stdout + result.stderr
    assert "KERP_FAIL_ON_SKIP" in result.stdout
    assert "1 test(s) skipped" in result.stdout


def test_no_skips_pass_when_requested(tmp_path: Path) -> None:
    result = _run(tmp_path, skip=False, fail_on_skip="1")
    assert result.returncode == 0, result.stdout + result.stderr


def test_conftest_registers_the_guard() -> None:
    from tests import conftest, skip_guard

    assert conftest.pytest_sessionfinish is skip_guard.pytest_sessionfinish
