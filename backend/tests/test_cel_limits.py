"""CEL cost limits: nested-macro cap, nesting-depth cap and step budget (T-0341).

Audit 2026-11-21 items 1 and 11. The shared values live in the ``limits`` block of
``tests/contract/conformance_fixtures.json`` (asserted in ``test_cel_conformance.py``); the
at/above-cap parity fixtures run there on both runtimes. All expressions are synthetic.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator

import pytest

from app.core.cel import CelError, CelErrorCode, CelOk, compile_expression, evaluate
from app.core.cel.limits import (
    MAX_EVAL_STEPS,
    MAX_MACRO_NESTING,
    MAX_NESTING_DEPTH,
    Nesting,
    measure,
)
from app.core.cel.strict import STEP_BUDGET_MESSAGE

# The audit's rule: four nested .all() over a 10-item list, 172 bytes, ~1 s to evaluate.
AUDIT_RULE = (
    "fields.items.all(a, fields.items.all(b, fields.items.all(c, fields.items.all(d, "
    "a.v + b.v + c.v + d.v >= 0.0 && a.v + b.v + c.v + d.v < 1000000.0 && d.v != -100000000.0))))"
)
SIX_NESTED = (
    "fields.items.all(a, fields.items.all(b, fields.items.all(c, fields.items.all(d, "
    "fields.items.all(e, fields.items.all(f, a.v + f.v >= 0.0))))))"
)
TEN_ITEMS = {"fields": {"items": [{"v": float(i)} for i in range(10)]}}
DEEP_PARENS = "(" * 1500 + "1" + ")" * 1500


def nested(open_: str, close: str, count: int, inner: str) -> str:
    return open_ * count + inner + close * count


def parse(expression: str) -> Nesting:
    import celpy

    return measure(celpy.Environment().compile(expression))


def test_audit_rule_is_172_bytes() -> None:
    assert len(AUDIT_RULE.encode("utf-8")) == 172


@pytest.mark.parametrize("expression", [AUDIT_RULE, SIX_NESTED], ids=["four", "six"])
def test_the_audit_rules_are_refused_at_save_quickly(expression: str) -> None:
    assert compile_expression("true") is None  # warm up: celpy builds its parser once per process
    started = time.perf_counter()
    error = compile_expression(expression, ["fields"])
    result = evaluate(expression, TEN_ITEMS)
    elapsed = time.perf_counter() - started
    assert error is not None
    assert error.code is CelErrorCode.PARSE_ERROR
    assert f"more than {MAX_MACRO_NESTING} nested loop macros" in error.message
    assert isinstance(result, CelError)
    assert result.code is CelErrorCode.PARSE_ERROR
    assert elapsed < 1.0


def test_three_nested_macros_are_allowed() -> None:
    expression = (
        "fields.items.all(a, fields.items.all(b, fields.items.all(c, a.v + b.v >= c.v - 9.0)))"
    )
    assert compile_expression(expression, ["fields"]) is None
    assert evaluate(expression, TEN_ITEMS) == CelOk(True)


@pytest.mark.parametrize(
    ("expression", "macros"),
    [
        ("l.all(x, x > 0.0)", 1),
        ("l.map(x, l.filter(y, y > x))", 2),
        ("l.exists(a, l.exists_one(b, l.map(c, c).size() > 0))", 3),
        # siblings and chains are counted per path, not in total
        ("l.all(a, true) && l.all(b, true) && l.all(c, true) && l.all(d, true)", 1),
        ("l.filter(a, true).map(b, b).filter(c, true).all(d, true)", 1),
        # a macro in another macro's receiver is evaluated once, not per element
        ("l.map(a, l.map(b, b)).all(x, x.all(y, y > 0.0))", 2),
        # only the loop macros count
        ("l.all(a, has(a.b) && size(l) > 0 && 'x'.contains('x'))", 1),
    ],
)
def test_macro_nesting_is_counted_along_one_path(expression: str, macros: int) -> None:
    assert parse(expression).macro_nesting == macros
    assert compile_expression(expression) is None


@pytest.mark.parametrize(
    ("expression", "tree", "parser"),
    [
        ("1", 1, 1),
        ("(1)", 1, 2),
        ("1 + 2 + 3", 3, 1),
        ("-1", 2, 1),  # celpy lexes one negative literal; CEL (and cel-js) has -(1)
        ("-x", 2, 1),
        ("a.b.c", 3, 3),  # each selection of a chain is one parser level
        ("a.b(1)", 2, 3),
        ("f(1)", 2, 2),
        ("[[1]]", 3, 3),
        ("{'k': 1}", 2, 2),
        ("true ? 1 : 2", 2, 2),
        ("l[0][1]", 3, 4),
    ],
)
def test_nesting_measures(expression: str, tree: int, parser: int) -> None:
    nesting = parse(expression)
    assert (nesting.tree_depth, nesting.parser_depth) == (tree, parser)


@pytest.mark.parametrize("call", ["compile", "evaluate"])
def test_1500_nested_parentheses_are_a_parse_error_not_a_recursion_error(call: str) -> None:
    result = compile_expression(DEEP_PARENS) if call == "compile" else evaluate(DEEP_PARENS, {})
    assert isinstance(result, CelError)
    assert result.code is CelErrorCode.PARSE_ERROR
    assert "recursion" not in result.message
    assert f"deeper than {MAX_NESTING_DEPTH} levels" in result.message


def test_the_depth_measure_itself_does_not_recurse() -> None:
    previous = sys.getrecursionlimit()
    sys.setrecursionlimit(200)
    try:
        nesting = parse(DEEP_PARENS)
    finally:
        sys.setrecursionlimit(previous)
    assert nesting.parser_depth == 1501


@pytest.fixture
def low_recursion_limit() -> Iterator[None]:
    """Start below celpy's own 2,500: ``evaluate`` must provide the headroom it needs."""
    previous = sys.getrecursionlimit()
    sys.setrecursionlimit(1000)
    try:
        yield
    finally:
        sys.setrecursionlimit(max(previous, sys.getrecursionlimit()))


AT_CAP = {
    "parentheses": nested("(", ")", MAX_NESTING_DEPTH - 1, "true"),
    "lists": nested("[", "]", MAX_NESTING_DEPTH - 2, "true") + " != []",
    "calls": nested("bool(", ")", MAX_NESTING_DEPTH - 1, "true"),
    "sum": " + ".join(["1.0"] * (MAX_NESTING_DEPTH - 1)) + " > 0.0",
    "negations": "!" * (MAX_NESTING_DEPTH - 2) + "true",
    "maps": nested("{'k': ", "}", (MAX_NESTING_DEPTH - 2) // 2, "true") + " != {}",
    "in_macros": "l.all(a, l.all(b, l.all(c, "
    + nested("(", ")", MAX_NESTING_DEPTH - 7, "a == b")
    + " || true)))",
}


@pytest.mark.usefixtures("low_recursion_limit")
@pytest.mark.parametrize("expression", list(AT_CAP.values()), ids=list(AT_CAP))
def test_expressions_at_the_depth_cap_still_evaluate(expression: str) -> None:
    assert compile_expression(expression) is None
    assert evaluate(expression, {"l": [1.0, 2.0]}) == CelOk(True)


def test_one_level_over_the_depth_cap_is_refused() -> None:
    for expression in (
        nested("(", ")", MAX_NESTING_DEPTH, "1"),
        " + ".join(["1"] * (MAX_NESTING_DEPTH + 1)),
    ):
        error = compile_expression(expression)
        assert error is not None
        assert error.code is CelErrorCode.PARSE_ERROR


HEAVY = "l.all(a, l.all(b, l.all(c, a + b + c >= 0.0)))"
HUNDRED = {"l": [float(i) for i in range(100)]}


@pytest.mark.parametrize(
    "expression",
    [HEAVY, f"true || {HEAVY}", f"false && {HEAVY}", f"[{HEAVY}].exists(x, true)"],
    ids=["plain", "absorbed_by_or", "absorbed_by_and", "inside_a_macro"],
)
def test_the_step_budget_stops_heavy_rules_with_eval_error(expression: str) -> None:
    # 10^6 inner evaluations: minutes without the budget. The spent budget aborts the whole
    # evaluation; `true ||` / `false &&` cannot absorb it (fail closed).
    assert compile_expression(expression) is None
    started = time.perf_counter()
    result = evaluate(expression, HUNDRED)
    assert time.perf_counter() - started < 10.0  # generous: ~0.4 s on a developer machine
    assert result == CelError(CelErrorCode.EVAL_ERROR, STEP_BUDGET_MESSAGE)


def test_the_step_budget_is_per_evaluation() -> None:
    expression = "l.all(a, l.all(b, a + b >= 0.0))"  # 1,600 inner evaluations, under budget
    for _ in range(3):
        assert evaluate(expression, {"l": HUNDRED["l"][:40]}) == CelOk(True)


def test_the_step_budget_counts_every_node_visit() -> None:
    # Each element of a one-node predicate costs at least one step: a list of MAX_EVAL_STEPS
    # elements cannot be checked.
    big = {"l": [1.0] * MAX_EVAL_STEPS}
    assert evaluate("l.all(x, true)", big) == CelError(CelErrorCode.EVAL_ERROR, STEP_BUDGET_MESSAGE)
    assert evaluate("l.all(x, true)", {"l": [1.0] * 1000}) == CelOk(True)
