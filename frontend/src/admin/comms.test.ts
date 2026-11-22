// The client's reading of the server's refusals (T-0229). Synthetic data only.
import { describe, expect, it } from "vitest";

import { refusedFields } from "./comms";

/**
 * Every reason `override_problems` / `_placeholder_problem` (backend app/core/mail/templates.py)
 * answers today, verbatim, with the message the editor shows for it. A reworded or new reason
 * in the backend must be added here (and to `refusalReason`), until the API sends stable codes
 * (TD-610).
 */
const REASONS = [
  ["must not be empty", "empty"],
  ["at most 200 characters", "tooLong"],
  ["at most 2000 characters", "tooLong"],
  ["plain text only: no control characters or line breaks here", "lineBreak"],
  ["plain text only: HTML is not allowed", "html"],
  ["no links: the action link is added when the e-mail is sent", "url"],
  ["unbalanced braces: write placeholders as {name}", "placeholder"],
  ["the link secret is never shown in the text", "placeholder"],
  ["unknown placeholder {secret}; allowed: {app_name}, {user_name}", "placeholder"],
  ["this template has no action link", "other"],
] as const;

describe("refusedFields", () => {
  it.each(REASONS)("maps %j to %s", (msg, reason) => {
    const error = { detail: [{ loc: ["body", "intro"], msg, type: "value_error" }] };
    expect(refusedFields(error)).toEqual({ intro: reason });
  });

  it("maps every field and ignores anything that is not a field list", () => {
    const detail = [
      { loc: ["body", "subject"], msg: "must not be empty" },
      { loc: ["body", "action_label"], msg: "at most 80 characters" },
      { loc: ["body", "unknown"], msg: "must not be empty" },
    ];
    expect(refusedFields({ detail })).toEqual({ subject: "empty", actionLabel: "tooLong" });
    expect(refusedFields({ detail: "Unknown mail template" })).toEqual({});
    expect(refusedFields(undefined)).toEqual({});
  });
});
