// Cost limits for CEL expressions (T-0341), mirrored by backend/app/core/cel/limits.py.
// The values must equal the `limits` block of tests/contract/conformance_fixtures.json
// (asserted by conformance.test.ts; the server constants are asserted the same way).
import type { ASTNode } from "@marcbachmann/cel-js";

/** Longest accepted expression, in UTF-8 bytes (INVALID_ARGUMENT above). */
export const MAX_EXPRESSION_BYTES = 4096;
/** Most loop macros nested inside each other's arguments along one path (PARSE_ERROR above). */
export const MAX_MACRO_NESTING = 3;
/** Deepest syntax tree and parser nesting (PARSE_ERROR above). */
export const MAX_NESTING_DEPTH = 100;
/**
 * Server evaluation step budget (EVAL_ERROR when spent). cel-js has no step hook, so the
 * client does not enforce it: the server is the hard gate (TD-533).
 */
export const MAX_EVAL_STEPS = 50_000;

export const LOOP_MACROS: ReadonlySet<string> = new Set(["all", "exists", "exists_one", "map", "filter"]);

/** An expression over a static cost cap; reported as PARSE_ERROR. */
export class LimitExceededError extends Error {
  override name = "LimitExceededError";
}

export interface Nesting {
  /** Depth of the syntax tree: every operator, selection, index, call and literal is one level. */
  treeDepth: number;
  /** Loop macros nested in each other's arguments along one path. */
  macroNesting: number;
}

function isNode(value: unknown): value is ASTNode {
  return typeof value === "object" && value !== null && typeof (value as { op?: unknown }).op === "string" && "args" in value;
}

/** Child nodes of an AST node (args holds nodes, arrays of nodes, [key, value] pairs or raw values). */
function children(args: unknown, out: ASTNode[]): ASTNode[] {
  if (isNode(args)) out.push(args);
  else if (Array.isArray(args)) for (const arg of args as unknown[]) children(arg, out);
  return out;
}

/**
 * Measure `ast` iteratively (no recursion, any depth). The parser depth (parentheses, call
 * arguments, list/map elements, selection chains) is cel-js's own `maxDepth` limit, set to
 * MAX_NESTING_DEPTH where the environment is created (evaluate.ts).
 */
export function measure(ast: ASTNode): Nesting {
  let treeDepth = 0;
  let macroNesting = 0;
  const stack: [ASTNode, number, number][] = [[ast, 1, 0]];
  for (let item = stack.pop(); item !== undefined; item = stack.pop()) {
    const [node, depth, macros] = item;
    treeDepth = Math.max(treeDepth, depth);
    const args = node.args as unknown;
    if (node.op === "rcall" && Array.isArray(args) && LOOP_MACROS.has(String(args[0]))) {
      // The receiver runs once; only the macro's own arguments run per element.
      const inner = macros + 1;
      macroNesting = Math.max(macroNesting, inner);
      for (const child of children(args[1], [])) stack.push([child, depth + 1, macros]);
      for (const child of children(args[2], [])) stack.push([child, depth + 1, inner]);
      continue;
    }
    for (const child of children(args, [])) stack.push([child, depth + 1, macros]);
  }
  return { treeDepth, macroNesting };
}

/** Throw LimitExceededError if `ast` is nested deeper than the caps allow. */
export function checkLimits(ast: ASTNode): void {
  const { treeDepth, macroNesting } = measure(ast);
  if (treeDepth > MAX_NESTING_DEPTH) {
    throw new LimitExceededError(`expression nested deeper than ${String(MAX_NESTING_DEPTH)} levels`);
  }
  if (macroNesting > MAX_MACRO_NESTING) {
    throw new LimitExceededError(`more than ${String(MAX_MACRO_NESTING)} nested loop macros`);
  }
}
