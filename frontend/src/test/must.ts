/** Test helper: narrow a possibly-missing value, failing the test loudly if it is absent. */
export function must<T>(value: T | null | undefined, what = "value"): T {
  if (value === null || value === undefined) throw new Error(`expected ${what} to be present`);
  return value;
}
