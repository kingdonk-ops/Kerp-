// KERP CEL JSON <-> @marcbachmann/cel-js values (encoding: tests/contract/README.md).
// Plain JSON numbers are always CEL doubles; ints, timestamps and the other types JSON
// cannot express are single-key `$tag` objects. backend/app/core/cel/values.py implements
// the same encoding, which is what makes the two runtimes comparable.

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

/** A CEL result has no KERP CEL JSON form (bytes, type values, non-string map keys). */
export class UnencodableValueError extends Error {
  override name = "UnencodableValueError";
}

/** An int result outside int64 (cel-js uses unbounded bigint for some operations). */
export class IntOverflowError extends Error {
  override name = "IntOverflowError";
}

const INT64_MIN = -(2n ** 63n);
const INT64_MAX = 2n ** 63n - 1n;
const INT_RE = /^-?(0|[1-9][0-9]*)$/;
const RFC3339_RE = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/; // uppercase T and Z only, like the server
const DOUBLE_NAMES: Readonly<Record<string, number>> = {
  NaN: Number.NaN,
  Infinity: Number.POSITIVE_INFINITY,
  "-Infinity": Number.NEGATIVE_INFINITY,
};

/** cel-js runtime classes that are not exported; resolved from sample values. */
export interface RuntimeClasses {
  uint: abstract new (...args: never[]) => { valueOf(): bigint };
  duration: abstract new (...args: never[]) => { seconds: bigint; nanos: number };
}

/** Decode an evaluation context; throws `TypeError` if malformed. */
export function decodeContext(context: Readonly<Record<string, JsonValue>>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(context).map(([name, value]) => [name, decodeValue(value)]));
}

export function decodeValue(value: JsonValue): unknown {
  if (value === null || typeof value === "boolean" || typeof value === "number" || typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) return value.map(decodeValue);
  if (typeof value !== "object" || Object.getPrototypeOf(value) !== Object.prototype) {
    throw new TypeError(`not a KERP CEL JSON value: ${typeof value}`);
  }
  const keys = Object.keys(value);
  const tag = keys[0];
  if (keys.length === 1 && tag !== undefined && tag.startsWith("$")) {
    return decodeTag(tag, value[tag] as JsonValue);
  }
  return Object.fromEntries(keys.map((key) => [key, decodeValue(value[key] as JsonValue)]));
}

function decodeTag(tag: string, inner: JsonValue): unknown {
  if (typeof inner !== "string") throw new TypeError(`${tag} value must be a string`);
  if (tag === "$int") {
    const parsed = INT_RE.test(inner) ? BigInt(inner) : undefined;
    if (parsed === undefined || parsed < INT64_MIN || parsed > INT64_MAX) {
      throw new TypeError(`invalid $int: ${inner}`);
    }
    return parsed;
  }
  if (tag === "$double") {
    const parsed = DOUBLE_NAMES[inner];
    if (parsed === undefined) throw new TypeError(`invalid $double: ${inner}`);
    return parsed;
  }
  if (tag === "$timestamp") {
    const match = RFC3339_RE.exec(inner);
    if (match === null) throw new TypeError(`invalid $timestamp (RFC 3339 with offset): ${inner}`);
    // Date has millisecond precision; truncate the fraction explicitly (the server does too).
    const millis = (match[2] ?? "").slice(0, 4).padEnd(4, "0");
    return new Date(`${match[1]}${millis === "0000" ? ".000" : millis}${match[3]}`);
  }
  throw new TypeError(`unsupported tag in a context: ${tag}`);
}

/** Encode a CEL result; throws `IntOverflowError` / `UnencodableValueError`. */
export function encodeValue(value: unknown, classes?: RuntimeClasses): JsonValue {
  if (value === null || typeof value === "boolean" || typeof value === "string") return value;
  if (typeof value === "bigint") {
    if (value < INT64_MIN || value > INT64_MAX) throw new IntOverflowError(`int out of int64 range: ${value}`);
    return { $int: value.toString() };
  }
  if (typeof value === "number") {
    if (Number.isNaN(value)) return { $double: "NaN" };
    if (!Number.isFinite(value)) return { $double: value > 0 ? "Infinity" : "-Infinity" };
    return value;
  }
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) throw new UnencodableValueError("invalid timestamp");
    return { $timestamp: value.toISOString() };
  }
  if (classes !== undefined && value instanceof classes.uint) return { $uint: value.valueOf().toString() };
  if (classes !== undefined && value instanceof classes.duration) {
    return { $duration: formatDuration(value.seconds, value.nanos) };
  }
  if (Array.isArray(value)) return value.map((item: unknown) => encodeValue(item, classes));
  if (value instanceof Map) {
    const entries: [string, JsonValue][] = [];
    for (const [key, item] of value as Map<unknown, unknown>) {
      if (typeof key !== "string") throw new UnencodableValueError("map with non-string keys");
      entries.push([key, encodeValue(item, classes)]);
    }
    return Object.fromEntries(entries);
  }
  if (typeof value === "object") {
    const proto: unknown = Object.getPrototypeOf(value);
    if (proto === Object.prototype || proto === null) {
      return Object.fromEntries(
        Object.entries(value).map(([key, item]: [string, unknown]) => [key, encodeValue(item, classes)]),
      );
    }
  }
  throw new UnencodableValueError(`no KERP CEL JSON form for ${String(value)}`);
}

function formatDuration(seconds: bigint, nanos: number): string {
  const total = seconds * 1_000_000_000n + BigInt(nanos);
  const sign = total < 0n ? "-" : "";
  const magnitude = total < 0n ? -total : total;
  const whole = magnitude / 1_000_000_000n;
  const fraction = magnitude % 1_000_000_000n;
  const fractionText = fraction === 0n ? "" : `.${fraction.toString().padStart(9, "0")}`.replace(/0+$/, "");
  return `${sign}${whole}${fractionText}s`;
}
