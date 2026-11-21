/**
 * The app's own message catalogue, so the journey finds controls by the text the user sees
 * without hard-coding English strings: a copy change in the catalogue never breaks a test,
 * and a missing key fails loudly.
 */
import { en } from "../../frontend/src/i18n/locales/en";

export function t(key: string, vars: Record<string, string> = {}): string {
  let node: unknown = en;
  for (const part of key.split(".")) {
    if (typeof node !== "object" || node === null || !(part in node)) {
      throw new Error(`no message ${key}`);
    }
    node = (node as Record<string, unknown>)[part];
  }
  if (typeof node !== "string") throw new Error(`message ${key} is not a string`);
  return node.replace(/\{\{(\w+)\}\}/g, (_match, name: string) => {
    const value = vars[name];
    if (value === undefined) throw new Error(`message ${key} needs {{${name}}}`);
    return value;
  });
}
