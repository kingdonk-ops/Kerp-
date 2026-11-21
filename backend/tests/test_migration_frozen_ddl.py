"""Migrations replay the RLS and ledger DDL that shipped, frozen per revision (T-0033, TD-060).

A revision never calls ``app.core.tenancy.rls`` / ``app.core.audit.guard`` at upgrade time:
it inlines what the helpers returned when it was written, in module-level constants

* ``FROZEN_RLS_DDL``: ``{table: statements}`` (row-level security: ``ALTER TABLE ... ENABLE |
  FORCE ROW LEVEL SECURITY``, ``DROP POLICY IF EXISTS``, ``CREATE POLICY``, and the
  ``CREATE OR REPLACE FUNCTION`` a policy calls),
* ``FROZEN_LEDGER_GUARD_DDL`` / ``FROZEN_LEDGER_GUARD_DROP_DDL``: ``{table: statements}`` of
  the append-only guard and its downgrade.

The drift test replays every revision's frozen RLS DDL in order, per table, and compares the
result with what the live helpers emit today (``apply_rls_policies``). When a helper changes,
the newest revision touching a table no longer matches: the author adds a **new** revision
with the new DDL frozen in it, never edits a shipped one.
"""

from __future__ import annotations

import ast
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.audit.guard import ledger_guard_drop_sql, ledger_guard_sql
from app.core.audit.models import TABLE as LEDGER_TABLE
from app.core.db import Base, import_all_models
from app.core.tenancy.rls import (
    TENANTS_TABLE,
    rls_policy_sql,
    rls_tables,
    table_policy,
    tenant_census_policy_sql,
)

BACKEND = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND / "migrations" / "versions"
ALEMBIC_INI = BACKEND / "alembic.ini"
LIVE_HELPER_MODULES = frozenset({"app.core.tenancy.rls", "app.core.audit.guard"})

Ddl = Mapping[str, Sequence[str]]

_ALTER = re.compile(r"ALTER TABLE (\w+) (ENABLE|FORCE) ROW LEVEL SECURITY")
_DROP = re.compile(r"DROP POLICY IF EXISTS (\w+) ON (\w+)")
_CREATE = re.compile(r"CREATE POLICY (\w+) ON (\w+) .+")
_FUNCTION = re.compile(r"CREATE OR REPLACE FUNCTION (\w+)\(.+")


def _normal(statement: str) -> str:
    """Whitespace-insensitive form: PostgreSQL parses both alike."""
    return " ".join(statement.split())


@dataclass
class _Replay:
    """The RLS state a sequence of statements leaves behind, per table."""

    flags: set[tuple[str, str]] = field(default_factory=set)
    policies: dict[tuple[str, str], str] = field(default_factory=dict)
    functions: dict[str, str] = field(default_factory=dict)
    function_tables: dict[str, set[str]] = field(default_factory=dict)

    def apply(self, table: str, statement: str, where: str) -> None:
        sql = _normal(statement)
        if (m := _ALTER.fullmatch(sql)) and m[1] == table:
            self.flags.add((table, m[2]))
        elif (m := _DROP.fullmatch(sql)) and m[2] == table:
            self.policies.pop((table, m[1]), None)
        elif (m := _CREATE.fullmatch(sql)) and m[2] == table:
            self.policies[(table, m[1])] = sql
        elif m := _FUNCTION.fullmatch(sql):
            self.functions[m[1]] = sql
            self.function_tables.setdefault(m[1], set()).add(table)
        else:
            raise ValueError(f"{where}: not an RLS statement on {table}: {statement!r}")

    def table(self, table: str) -> tuple[set[str], dict[str, str]]:
        flags = {flag for name, flag in self.flags if name == table}
        return flags, {name: sql for (owner, name), sql in self.policies.items() if owner == table}


def rls_drift(revisions: Sequence[tuple[str, Ddl]], live: Ddl) -> list[str]:
    """Why the frozen RLS DDL of ``revisions`` (base to head) is not what ``live`` emits.

    One message per drifted table, naming it and the newest revision that touches it.
    """
    applied, expected = _Replay(), _Replay()
    newest: dict[str, str] = {}
    for revision, ddl in revisions:
        for table, statements in ddl.items():
            newest[table] = revision
            for statement in statements:
                applied.apply(table, statement, f"revision {revision}")
    for table, statements in live.items():
        for statement in statements:
            expected.apply(table, statement, "live helpers")
    problems: list[str] = []
    for table in sorted(live):
        if table not in newest:
            problems.append(
                f"{table}: no migration revision applies its RLS DDL; add a new revision with"
                f" the helper output frozen in FROZEN_RLS_DDL[{table!r}]"
            )
            continue
        got_flags, got = applied.table(table)
        want_flags, want = expected.table(table)
        changed = sorted(
            name for name in got.keys() | want.keys() if got.get(name) != want.get(name)
        )
        if got_flags != want_flags:
            changed.append(f"ROW LEVEL SECURITY {sorted(want_flags)}")
        if changed:
            problems.append(_drifted(table, newest[table], changed))
    for name, sql in sorted(expected.functions.items()):
        if applied.functions.get(name) != sql:
            for table in sorted(expected.function_tables[name]):
                problems.append(_drifted(table, newest.get(table, "none"), [f"function {name}()"]))
    return problems


def _drifted(table: str, revision: str, changed: list[str]) -> str:
    return (
        f"{table}: the live RLS helpers (app.core.tenancy.rls) no longer match the DDL the"
        f" migrations apply (newest revision touching it: {revision};"
        f" differs: {', '.join(changed)})."
        f" Add a NEW migration revision with the new helper output frozen in FROZEN_RLS_DDL;"
        f" never edit revision {revision}."
    )


def _scripts() -> list[tuple[str, ModuleType]]:
    """Every revision module, base to head."""
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    return [(s.revision, s.module) for s in reversed(list(script.walk_revisions()))]


def _frozen(name: str) -> list[tuple[str, Ddl]]:
    return [(rev, getattr(module, name)) for rev, module in _scripts() if hasattr(module, name)]


def _live_rls() -> dict[str, list[str]]:
    """What ``apply_rls_policies`` runs for each RLS table of the models today."""
    import_all_models()
    tables = Base.metadata.tables
    live: dict[str, list[str]] = {}
    for table in rls_tables(Base.metadata):
        live[table] = rls_policy_sql(table, table_policy(tables[table]))
        if table == TENANTS_TABLE:
            live[table] += tenant_census_policy_sql(table)
    return live


# --- the convention ---------------------------------------------------------------------------


def test_no_revision_imports_the_live_ddl_helpers() -> None:
    offenders: list[str] = []
    for path in sorted(VERSIONS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            offenders += [f"{path.name}: {m}" for m in modules if m in LIVE_HELPER_MODULES]
    assert offenders == [], "migrations must inline frozen DDL (TD-060), not import helpers"


def test_every_rls_table_replays_exactly_the_live_helper_ddl() -> None:
    assert rls_drift(_frozen("FROZEN_RLS_DDL"), _live_rls()) == []


def test_the_ledger_guard_of_the_newest_revision_is_the_live_helper_output() -> None:
    def newest(name: str) -> tuple[str, list[str]]:
        found = [
            (rev, list(ddl[LEDGER_TABLE])) for rev, ddl in _frozen(name) if LEDGER_TABLE in ddl
        ]
        assert found, f"no revision freezes {name}[{LEDGER_TABLE!r}]"
        return found[-1]

    revision, guard = newest("FROZEN_LEDGER_GUARD_DDL")
    assert guard == ledger_guard_sql(LEDGER_TABLE), (
        f"{LEDGER_TABLE}: app.core.audit.guard.ledger_guard_sql changed since revision"
        f" {revision}; add a new migration revision with the new DDL frozen in it"
    )
    revision, drop = newest("FROZEN_LEDGER_GUARD_DROP_DDL")
    assert drop == ledger_guard_drop_sql(LEDGER_TABLE), (
        f"{LEDGER_TABLE}: ledger_guard_drop_sql changed since revision {revision}"
    )


def test_upgrade_executes_the_frozen_statements_of_each_revision() -> None:
    """The declared constants are what the revision runs (offline PostgreSQL SQL)."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes.update(url="postgresql+psycopg://offline@localhost/offline")
    cfg.attributes["configure_logger"] = False
    cfg.output_buffer = io.StringIO()
    command.upgrade(cfg, "head", sql=True)
    sections = re.split(
        r"^-- Running upgrade \S* ?-> (\S+)$", cfg.output_buffer.getvalue(), flags=re.M
    )
    by_revision = {sections[i]: _normal(sections[i + 1]) for i in range(1, len(sections), 2)}
    checked = 0
    for name in ("FROZEN_RLS_DDL", "FROZEN_LEDGER_GUARD_DDL"):
        for revision, ddl in _frozen(name):
            for table, statements in ddl.items():
                for statement in statements:
                    assert f"{_normal(statement)};" in by_revision[revision], (revision, table)
                    checked += 1
    assert checked > 100


# --- the drift check itself (synthetic DDL) ---------------------------------------------------

_ON = "ALTER TABLE t ENABLE ROW LEVEL SECURITY"
_FORCE = "ALTER TABLE t FORCE ROW LEVEL SECURITY"


def _policy(rule: str) -> str:
    return f"CREATE POLICY p ON t FOR ALL USING ({rule})"


def test_a_changed_helper_names_the_table_and_asks_for_a_new_migration() -> None:
    problems = rls_drift(
        [("0007", {"t": [_ON, _FORCE, _policy("a")]})], {"t": [_ON, _FORCE, _policy("b")]}
    )
    assert len(problems) == 1
    assert problems[0].startswith("t: ")
    assert "newest revision touching it: 0007" in problems[0]
    assert "Add a NEW migration revision" in problems[0]
    assert "never edit revision 0007" in problems[0]


def test_a_later_revision_replaces_the_policy_and_matches() -> None:
    revisions = [
        ("0001", {"t": [_ON, _FORCE, _policy("a"), "CREATE POLICY old ON t FOR SELECT USING (x)"]}),
        (
            "0002",
            {"t": ["DROP POLICY IF EXISTS p ON t", "DROP POLICY IF EXISTS old ON t", _policy("b")]},
        ),
    ]
    assert (
        rls_drift(revisions, {"t": [_ON, _FORCE, "DROP POLICY IF EXISTS p ON t", _policy("b")]})
        == []
    )
    problems = rls_drift(revisions[:1], {"t": [_ON, _FORCE, _policy("b")]})
    assert len(problems) == 1
    assert "differs: old, p" in problems[0]


def test_missing_force_is_drift() -> None:
    problems = rls_drift([("0001", {"t": [_ON, _policy("a")]})], {"t": [_ON, _FORCE, _policy("a")]})
    assert len(problems) == 1
    assert "ROW LEVEL SECURITY" in problems[0]


def test_a_table_without_frozen_ddl_is_reported() -> None:
    problems = rls_drift([], {"t": [_ON]})
    assert problems == [
        "t: no migration revision applies its RLS DDL; add a new revision with the helper"
        " output frozen in FROZEN_RLS_DDL['t']"
    ]


def test_whitespace_only_differences_are_not_drift() -> None:
    frozen = "\nCREATE OR REPLACE FUNCTION f() RETURNS int\nLANGUAGE sql AS $$ SELECT 1 $$\n"
    live = "CREATE OR REPLACE FUNCTION f() RETURNS int LANGUAGE sql AS $$  SELECT 1 $$"
    assert rls_drift([("0001", {"t": [frozen, _ON]})], {"t": [live, _ON]}) == []


def test_a_changed_policy_function_names_the_tables_that_use_it() -> None:
    def function(body: str) -> str:
        return f"CREATE OR REPLACE FUNCTION f() RETURNS int LANGUAGE sql AS $$ {body} $$"

    u_on = _ON.replace(" t ", " u ")
    problems = rls_drift(
        [("0003", {"t": [function("SELECT 1"), _ON]}), ("0004", {"u": [u_on]})],
        {"t": [function("SELECT 2"), _ON], "u": [function("SELECT 2"), u_on]},
    )
    assert [p.split(":")[0] for p in problems] == ["t", "u"]
    assert all("function f()" in p for p in problems)


def test_unknown_statements_are_refused() -> None:
    with pytest.raises(ValueError, match="revision 0001: not an RLS statement on t"):
        rls_drift([("0001", {"t": ["GRANT SELECT ON t TO PUBLIC"]})], {})
    with pytest.raises(ValueError, match="not an RLS statement on t"):
        rls_drift([("0001", {"t": ["CREATE POLICY p ON other FOR ALL USING (true)"]})], {})
