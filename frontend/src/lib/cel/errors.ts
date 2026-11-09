// KERP CEL error taxonomy (tests/contract/README.md). Must equal `error_codes` in
// tests/contract/conformance_fixtures.json and backend/app/core/cel/errors.py (asserted by
// both conformance suites).
export const CEL_ERROR_CODES = [
  "PARSE_ERROR",
  "UNDECLARED_REFERENCE",
  "NO_SUCH_KEY",
  "INDEX_OUT_OF_RANGE",
  "NO_MATCHING_OVERLOAD",
  "DIVIDE_BY_ZERO",
  "INT_OVERFLOW",
  "INVALID_ARGUMENT",
  "EVAL_ERROR",
] as const;

export type CelErrorCode = (typeof CEL_ERROR_CODES)[number];

/** @marcbachmann/cel-js error `code` -> KERP code. Unlisted codes fall back by error class. */
export const CEL_JS_CODE_MAP: Readonly<Record<string, CelErrorCode>> = {
  unknown_variable: "UNDECLARED_REFERENCE",
  no_such_key: "NO_SUCH_KEY",
  index_out_of_bounds: "INDEX_OUT_OF_RANGE",
  index_out_of_range: "INDEX_OUT_OF_RANGE",
  no_such_overload: "NO_MATCHING_OVERLOAD",
  no_matching_overload: "NO_MATCHING_OVERLOAD",
  invalid_logical_operand: "NO_MATCHING_OVERLOAD",
  cannot_index_type: "NO_MATCHING_OVERLOAD",
  invalid_index_type: "NO_MATCHING_OVERLOAD",
  invalid_comparison_type: "NO_MATCHING_OVERLOAD",
  unsupported_type: "NO_MATCHING_OVERLOAD",
  division_by_zero: "DIVIDE_BY_ZERO",
  modulo_by_zero: "DIVIDE_BY_ZERO",
  numeric_overflow: "INT_OVERFLOW",
  // Thrown by the parser only for integer literals outside int64/uint64.
  invalid_integer: "INT_OVERFLOW",
  invalid_hex_integer: "INT_OVERFLOW",
  int_conversion_error: "INVALID_ARGUMENT",
  uint_conversion_error: "INVALID_ARGUMENT",
  invalid_regular_expression: "INVALID_ARGUMENT",
  invalid_timestamp: "INVALID_ARGUMENT",
  invalid_duration: "INVALID_ARGUMENT",
  // Misused macro, e.g. has() without a field selection: a parse error in the CEL spec.
  invalid_macro_argument: "PARSE_ERROR",
};
