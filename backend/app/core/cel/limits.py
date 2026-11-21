"""Cost limits for CEL expressions (T-0341), mirrored by ``frontend/src/lib/cel/limits.ts``.

The values must equal the ``limits`` block of ``tests/contract/conformance_fixtures.json``
(asserted by ``tests/test_cel_conformance.py``; the client constants are asserted the same way).

Static caps (checked at compile time, ``PARSE_ERROR``), measured on the parse tree without
recursion so that the check itself cannot hit the recursion limit:

- **Macro nesting**: the number of loop macros (``all``, ``exists``, ``exists_one``, ``map``,
  ``filter``) along one path, counting a macro only inside another macro's *arguments* (the
  part evaluated once per element). A macro in another macro's receiver runs once, so
  chained (``l.filter(...).all(...)``) and sibling macros are not nested.
- **Nesting depth**, two measures, both capped at ``MAX_NESTING_DEPTH``:
  the *syntax-tree depth* (every operator, selection, index, call, list/map literal and
  literal/identifier is one level; parentheses are not nodes), and the *parser depth*
  (every nested sub-expression — parentheses, call arguments, list elements, map keys and
  values, index expressions, ternary branches — is one level, and each ``.field``,
  ``.method()`` or ``[index]`` of a selection chain adds one more). The parser depth is
  the ``maxDepth`` limit of cel-js (the client parser), so the client enforces it by
  configuration; the server computes the same number here.

The step budget (``MAX_EVAL_STEPS``) is spent by the strict evaluator (``strict.py``): one
step per parse-tree node visited, including every visit inside a macro body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from lark import Token, Tree

MAX_EXPRESSION_BYTES: Final = 4096
MAX_MACRO_NESTING: Final = 3
MAX_NESTING_DEPTH: Final = 100
MAX_EVAL_STEPS: Final = 50_000

LOOP_MACROS: Final = frozenset({"all", "exists", "exists_one", "map", "filter"})

# celpy parse-tree nodes that are one node of the CEL syntax tree.
_CHAIN: Final = frozenset({"member_dot", "member_dot_arg", "member_index"})
_ALWAYS_NODES: Final = _CHAIN | frozenset(
    {"literal", "ident", "dot_ident", "ident_arg", "dot_ident_arg", "list_lit", "map_lit"}
)
# Operator nodes: one syntax-tree node only with two children (else a pass-through wrapper).
_OPERATORS: Final = frozenset(
    {"conditionalor", "conditionaland", "relation", "addition", "multiplication", "unary"}
)


# Single-child wrappers between a selection-chain node and its receiver.
_WRAPPERS: Final = frozenset({"member", "primary"})


class LimitExceededError(Exception):
    """An expression exceeds a static cost cap (reported as ``PARSE_ERROR``)."""


def _is_node(tree: Tree[Token]) -> bool:
    if tree.data in _ALWAYS_NODES:
        return True
    if tree.data in _OPERATORS:
        return len(tree.children) == 2
    return tree.data == "expr" and len(tree.children) == 3  # ternary


def _receiver(tree: Tree[Token]) -> object:
    """First child of a selection-chain node, through single-child ``_WRAPPERS``."""
    from lark import Tree

    child = tree.children[0] if tree.children else None
    while isinstance(child, Tree) and child.data in _WRAPPERS and len(child.children) == 1:
        child = child.children[0]
    return child


def _chain_bottom(tree: Tree[Token]) -> tuple[int, object]:
    """Number of selection-chain nodes from ``tree`` down, and the node below the chain."""
    from lark import Tree

    length, node = 0, cast(object, tree)
    while isinstance(node, Tree) and node.data in _CHAIN:
        length += 1
        node = _receiver(node)
    return length, node


def _is_negative_literal(node: object) -> bool:
    """celpy lexes ``-1`` as one literal; cel-js (and the CEL syntax tree) as ``-(1)``."""
    from lark import Token, Tree

    if not (isinstance(node, Tree) and node.data == "literal" and node.children):
        return False
    token = node.children[0]
    return isinstance(token, Token) and str(token).startswith("-")


@dataclass(frozen=True, slots=True)
class Nesting:
    """The three nesting measures of an expression (see the module docstring)."""

    tree_depth: int
    parser_depth: int
    macro_nesting: int


def measure(ast: Tree[Token]) -> Nesting:
    """Measure ``ast`` in one iterative pass (no recursion, any depth)."""
    from lark import Token, Tree

    tree_max = parser_max = macro_max = 0
    # (node, syntax-tree depth above, parser depth of the enclosing sub-expression,
    #  macro nesting above, position in a selection chain: None outside a chain or not yet
    #  known, 0 for the node below a chain)
    stack: list[tuple[Tree[Token], int, int, int, int | None]] = [(ast, 0, 0, 0, None)]
    while stack:
        node, depth, units, macros, chain = stack.pop()
        if _is_node(node):
            depth += 1
        if node.data == "literal" and chain is None and _is_negative_literal(node):
            depth += 1  # the unary minus
        if node.data == "expr":
            units += 1
        receiver_units, receiver_chain, argument_macros = units, None, macros
        if node.data in _CHAIN:
            if chain is None:  # top of a chain: measure it once
                chain, bottom = _chain_bottom(node)
                if _is_negative_literal(bottom):
                    depth += 1  # cel-js: -(<whole chain>)
            units += chain
            receiver_chain = chain - 1  # 0: the node below the chain
            names = {str(c) for c in node.children if isinstance(c, Token)}
            if node.data == "member_dot_arg" and names & LOOP_MACROS:
                argument_macros = macros + 1
        tree_max = max(tree_max, depth)
        parser_max = max(parser_max, units)
        macro_max = max(macro_max, argument_macros)
        subtrees = [c for c in node.children if isinstance(c, Tree)]
        for index, child in enumerate(subtrees):
            if node.data in _CHAIN and index == 0:
                # The receiver: parsed before the chain, evaluated once (not per element).
                stack.append((child, depth, receiver_units, macros, receiver_chain))
            elif node.data in _WRAPPERS and chain is not None:
                stack.append((child, depth, units, macros, chain))  # pass-through wrapper
            elif node.data == "expr" and len(node.children) == 3 and index == 1:
                stack.append((child, depth, units + 1, macros, None))  # ternary true branch
            else:
                stack.append((child, depth, units, argument_macros, None))
    return Nesting(tree_max, parser_max, macro_max)


def check_limits(ast: Tree[Token]) -> None:
    """Raise ``LimitExceededError`` if ``ast`` exceeds the macro-nesting or depth caps."""
    nesting = measure(ast)
    if max(nesting.tree_depth, nesting.parser_depth) > MAX_NESTING_DEPTH:
        raise LimitExceededError(f"expression nested deeper than {MAX_NESTING_DEPTH} levels")
    if nesting.macro_nesting > MAX_MACRO_NESTING:
        raise LimitExceededError(f"more than {MAX_MACRO_NESTING} nested loop macros")
