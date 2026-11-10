"""OpenAPI export for the frontend's ``npm run gen:api`` (TD-018, T-0011)."""

import json
from pathlib import Path

import pytest

from app.openapi import main, openapi_schema, write_schema


def test_schema_contains_the_platform_endpoints() -> None:
    paths = openapi_schema()["paths"]
    assert "/health" in paths
    assert {"get"} <= set(paths["/api/v1/modules"])
    assert {"put"} <= set(paths["/api/v1/modules/{name}"])


def test_write_schema_writes_stable_json(tmp_path: Path) -> None:
    target = tmp_path / "out" / "openapi.json"
    write_schema(target)
    first = target.read_bytes()
    write_schema(target)
    assert target.read_bytes() == first
    assert first.endswith(b"}\n")
    assert b"\r\n" not in first
    assert json.loads(first)["openapi"].startswith("3.")


def test_main_writes_the_given_path(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"
    assert main([str(target)]) == 0
    assert target.is_file()


def test_main_needs_exactly_one_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_main_reads_sys_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "argv.json"
    monkeypatch.setattr("sys.argv", ["app.openapi", str(target)])
    assert main() == 0
    assert target.is_file()
