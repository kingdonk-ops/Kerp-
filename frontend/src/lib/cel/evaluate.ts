// Client-side CEL evaluation (@marcbachmann/cel-js, pure JS, works offline), mirrored by
// backend/app/core/cel. Same API: evaluate(expression, context) -> value | error code.
import {
  Environment,
  EvaluationError,
  ParseError,
  TypeError as CelTypeError,
  type ParseResult,
} from "@marcbachmann/cel-js";

import { CEL_JS_CODE_MAP, type CelErrorCode } from "./errors";
import {
  decodeContext,
  encodeValue,
  IntOverflowError,
  UnencodableValueError,
  type JsonValue,
  type RuntimeClasses,
} from "./values";

export type CelResult = { ok: true; value: JsonValue } | { ok: false; code: CelErrorCode; message: string };

export const PROGRAM_CACHE_SIZE = 1024;
/** Same cap as the server (backend/app/core/cel/runtime.py), counted in UTF-8 bytes. */
export const MAX_EXPRESSION_BYTES = 4096;

const utf8 = new TextEncoder();

const environment = new Environment({
  // Context variables are untyped (dyn), like celpy's activation.
  unlistedVariablesAreDyn: true,
  // celpy accepts mixed list/map literals such as [1, 'a'].
  homogeneousAggregateLiterals: false,
});

// The uint and duration classes are not exported by the package; take them from values.
const runtimeClasses: RuntimeClasses = {
  uint: (environment.evaluate("0u") as object).constructor as RuntimeClasses["uint"],
  duration: (environment.evaluate("duration('0s')") as object).constructor as RuntimeClasses["duration"],
};

const programs = new Map<string, ParseResult>();

function compile(expression: string): ParseResult {
  const cached = programs.get(expression);
  if (cached !== undefined) {
    programs.delete(expression); // re-insert: Map order is the LRU order
    programs.set(expression, cached);
    return cached;
  }
  const program = environment.parse(expression);
  programs.set(expression, program);
  if (programs.size > PROGRAM_CACHE_SIZE) {
    const oldest = programs.keys().next();
    if (oldest.done !== true) programs.delete(oldest.value);
  }
  return program;
}

/** Number of cached compiled programs (for tests). */
export function compiledProgramCount(): number {
  return programs.size;
}

/** Map a cel-js error onto the KERP taxonomy. */
export function classifyCelError(error: unknown): { ok: false; code: CelErrorCode; message: string } {
  if (error instanceof ParseError || error instanceof EvaluationError || error instanceof CelTypeError) {
    const fallback: CelErrorCode =
      error instanceof ParseError ? "PARSE_ERROR" : error instanceof CelTypeError ? "NO_MATCHING_OVERLOAD" : "EVAL_ERROR";
    return { ok: false, code: CEL_JS_CODE_MAP[error.code] ?? fallback, message: error.message };
  }
  return { ok: false, code: "EVAL_ERROR", message: error instanceof Error ? error.message : String(error) };
}

/**
 * Evaluate `expression` against `context` (top-level variables, KERP CEL JSON).
 * CEL failures are returned as `{ok: false}`; a malformed context is a caller bug and throws.
 */
export function evaluate(expression: string, context: Readonly<Record<string, JsonValue>>): CelResult {
  const activation = decodeContext(context);
  if (utf8.encode(expression).length > MAX_EXPRESSION_BYTES) {
    return { ok: false, code: "INVALID_ARGUMENT", message: `expression longer than ${MAX_EXPRESSION_BYTES} bytes` };
  }
  let result: unknown;
  try {
    result = compile(expression)(activation);
  } catch (error) {
    return classifyCelError(error);
  }
  try {
    return { ok: true, value: encodeValue(result, runtimeClasses) };
  } catch (error) {
    if (error instanceof IntOverflowError) return { ok: false, code: "INT_OVERFLOW", message: error.message };
    if (error instanceof UnencodableValueError) return { ok: false, code: "EVAL_ERROR", message: error.message };
    throw error;
  }
}
