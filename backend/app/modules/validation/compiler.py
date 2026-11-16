"""Compile-on-save for rules: kind/engine combinations and per-kind context schemas.

ADR 0004 §1, §6 and Amendment E. A rule is only stored after ``compile_rule`` accepts it;
``RuleCompileError`` carries the reason (the CEL or RE2 compile error).

The context schemas (top-level CEL variables per kind) are the implementer's reading of the
spec; they are recorded for confirmation in the T-0018 spec-gap TODO.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from app.core.cel import compile_expression
from app.core.cel.values import decode_context

# Only these kinds may use the ``regex`` engine; every other kind requires ``cel``.
REGEX_KINDS: Final = frozenset({"autolink", "p6_grammar"})

# Top-level variables a CEL rule of each kind may reference. ``params`` is the rule's own
# ``params`` (KERP CEL JSON), so a project can tune a threshold without editing the expression.
CEL_CONTEXT: Final[Mapping[str, frozenset[str]]] = {
    "check": frozenset({"record", "fields", "actor", "project", "now", "params"}),
    "digest": frozenset({"event", "actor", "project", "now", "params"}),
    "notify": frozenset({"event", "actor", "project", "now", "params"}),
    # p6-import-and-rsw-matching.md §5.1/§5.3: grammar groups + P6 activity columns.
    "p6_eligibility": frozenset(
        {
            "task_code",
            "task_name",
            "task_type",
            "wbs_path",
            "asset",
            "activity_type",
            "anchor_code",
            "suffix",
            "window",
            "params",
        }
    ),
}

# Named groups a ``p6_grammar`` pattern may define (§5.1); ``anchor_code`` is required.
P6_GRAMMAR_GROUPS: Final = frozenset({"asset", "activity_type", "anchor_code", "suffix", "window"})

# Allowed ``action`` per kind; kinds not listed accept any snake_case action.
ACTIONS: Final[Mapping[str, frozenset[str]]] = {
    "check": frozenset({"block", "warn"}),
    "p6_eligibility": frozenset({"placeholder", "no_placeholder"}),
}

MAX_PATTERN_LENGTH: Final = 1024  # characters; RE2 also caps program size and memory

# T-0035 (ADR 0004 Amendment A.6): a ``check`` rule may name, in ``params.override_binding``,
# the context paths it checks (e.g. ``["fields.equipment_id", "fields.spec_revision",
# "fields.inspection_date"]``). An override of the rule is bound to the hash of exactly those
# values. Without it the binding is the whole ``record`` and ``fields`` (any change voids).
OVERRIDE_BINDING_PARAM: Final = "override_binding"
DEFAULT_OVERRIDE_BINDING: Final = ("record", "fields")
BINDING_ROOTS: Final = frozenset({"record", "fields", "actor", "project"})
MAX_BINDING_PATHS: Final = 32
_BINDING_PATH: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,15}$")


class RuleCompileError(ValueError):
    """The rule cannot be stored; the message says why (shown to the rule author)."""


def allowed_engine(kind: str) -> str:
    return "regex" if kind in REGEX_KINDS else "cel"


def compile_rule(
    kind: str, engine: str, expression: str, params: Mapping[str, Any], action: str
) -> None:
    """Raise ``RuleCompileError`` unless the rule is valid for ``kind``."""
    allowed = allowed_engine(kind)
    if engine != allowed:
        raise RuleCompileError(f"{kind} rules must use the {allowed} engine, not {engine!r}")
    actions = ACTIONS.get(kind)
    if actions is not None and action not in actions:
        raise RuleCompileError(f"{kind} rule action must be one of {sorted(actions)}")
    try:
        decode_context({"params": dict(params)})
    except (TypeError, ValueError) as exc:
        raise RuleCompileError(f"params are not valid KERP CEL JSON: {exc}") from exc
    if kind == "check" and OVERRIDE_BINDING_PARAM in params:
        check_override_binding(params[OVERRIDE_BINDING_PARAM])
    if engine == "cel":
        error = compile_expression(expression, CEL_CONTEXT[kind])
        if error is not None:
            raise RuleCompileError(f"CEL {error.code}: {error.message}")
    else:
        groups = compile_regex(expression)
        if kind == "p6_grammar":
            unknown = sorted(groups - P6_GRAMMAR_GROUPS)
            if unknown:
                raise RuleCompileError(f"unknown named group {unknown[0]!r} in p6_grammar")
            if "anchor_code" not in groups:
                raise RuleCompileError("a p6_grammar pattern needs the named group anchor_code")


def check_override_binding(paths: object) -> None:
    """``params.override_binding`` must be 1-32 distinct dotted paths under a bindable root
    (``record``, ``fields``, ``actor``, ``project``; never ``now`` or ``params``)."""
    if not isinstance(paths, list) or not paths or len(paths) > MAX_BINDING_PATHS:
        raise RuleCompileError(
            f"params.{OVERRIDE_BINDING_PARAM} must be a list of 1-{MAX_BINDING_PATHS} paths"
        )
    for path in paths:
        if not isinstance(path, str) or not _BINDING_PATH.fullmatch(path):
            raise RuleCompileError(f"params.{OVERRIDE_BINDING_PARAM}: {path!r} is not a path")
        if path.split(".", 1)[0] not in BINDING_ROOTS:
            raise RuleCompileError(
                f"params.{OVERRIDE_BINDING_PARAM}: {path!r} must start with one of "
                f"{sorted(BINDING_ROOTS)}"
            )
    if len(set(paths)) != len(paths):
        raise RuleCompileError(f"params.{OVERRIDE_BINDING_PARAM} lists a path twice")


def compile_regex(pattern: str) -> frozenset[str]:
    """Compile ``pattern`` with RE2 (linear time, no backtracking); return its named groups."""
    import re2  # type: ignore[import-untyped]  # lazy: native library, no stubs (CLAUDE.md §6)

    if len(pattern) > MAX_PATTERN_LENGTH:
        raise RuleCompileError(f"regex pattern longer than {MAX_PATTERN_LENGTH} characters")
    options = re2.Options()
    options.log_errors = False
    try:
        compiled = re2.compile(pattern, options)
    except re2.error as exc:
        raw = exc.args[0] if exc.args else ""  # google-re2 reports the message as bytes
        detail = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        raise RuleCompileError(f"RE2: {detail}") from exc
    return frozenset(compiled.groupindex)
