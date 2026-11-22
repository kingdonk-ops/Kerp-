// Admin → Communications APIs (diff §4.7, §6.5; T-0229): broadcast e-mail (T-0228, System
// admin and PER-09 for its own projects), the delivery ledger (T-0226) and automated template
// overrides (T-0227), both `system_admin` only (else 403).
import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";
import { AdminApiError } from "./api";

export type Broadcast = components["schemas"]["BroadcastRead"];
export type BroadcastAudience = components["schemas"]["BroadcastAudienceWrite"];
export type BroadcastCategory = NonNullable<BroadcastAudience["category"]>;
export type MailCounts = components["schemas"]["MailCountsRead"];
export type MailStats = components["schemas"]["MailStatsRead"];
export type MailSend = components["schemas"]["MailSendRead"];
export type MailSendStatus = MailSend["status"];
export type MailTemplate = components["schemas"]["MailTemplateRead"];
export type MailTemplateAudience = MailTemplate["audience"];
export type TemplateRevision = components["schemas"]["MailTemplateOverrideRead"];
export type TemplatePreview = components["schemas"]["MailPreviewRead"];

export const BROADCAST_CATEGORIES: readonly BroadcastCategory[] = ["internal", "external", "client"];
export const SEND_STATUSES: readonly MailSendStatus[] = [
  "queued",
  "sent",
  "delivered",
  "bounced",
  "complained",
  "failed",
  "suppressed",
];
/** Editor groups, in display order (diff §4.7 'Editor details'). */
export const TEMPLATE_AUDIENCES: readonly MailTemplateAudience[] = [
  "internal_staff",
  "client",
  "external_party",
  "deployment_wide",
];
/** Rows per page of the broadcast and send registers. */
export const COMMS_PAGE_SIZE = 50;
/** The server's limits on a broadcast (`BroadcastWrite`). */
export const MAX_SUBJECT = 200;
export const MAX_BODY = 10_000;

/** Seconds of a `429`'s `Retry-After`, `null` when absent or unreadable. */
function retryAfter(response: Response): number | null {
  const seconds = Number(response.headers.get("Retry-After"));
  return Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

/** `code` of a dict `detail` or of the body itself (`{detail, code}`), else `""`. */
function errorCode(error: unknown): string {
  if (typeof error !== "object" || error === null) return "";
  const body = error as { code?: unknown; detail?: unknown };
  if (typeof body.code === "string") return body.code;
  const detail = body.detail;
  return typeof detail === "object" && detail !== null && "code" in detail ? String(detail.code) : "";
}

// --- broadcasts (T-0228) ----------------------------------------------------------------------

/** How many users the audience reaches now: a count, never a list. */
export async function previewBroadcast(api: ApiClient, audience: BroadcastAudience): Promise<number> {
  const { data, response } = await api.POST("/api/v1/notifications/broadcasts/preview", { body: audience });
  if (!data) throw new AdminApiError(response.status);
  return data.recipient_count;
}

export type SendBroadcastResult =
  | { kind: "ok"; broadcast: Broadcast }
  | { kind: "forbidden"; impersonating: boolean }
  | { kind: "outOfScope" }
  | { kind: "noRecipients" }
  | { kind: "invalid" }
  | { kind: "rateLimited"; retryAfter: number | null };

/**
 * Queue the broadcast (202). 403 / 404 / 422 / 429 come back as results, not errors. A 422
 * with a string `detail` is the empty audience (T-0228 router); any other 422 (body
 * validation the client did not catch) is `invalid`. Machine-readable codes: TD-610.
 */
export async function sendBroadcast(
  api: ApiClient,
  body: components["schemas"]["BroadcastWrite"],
): Promise<SendBroadcastResult> {
  const { data, error, response } = await api.POST("/api/v1/notifications/broadcasts", { body });
  if (data) return { kind: "ok", broadcast: data };
  switch (response.status) {
    case 403:
      return { kind: "forbidden", impersonating: errorCode(error) === "IMPERSONATION_ACTION_FORBIDDEN" };
    case 404:
      return { kind: "outOfScope" };
    case 422:
      // The empty-audience refusal is a plain-text `detail`; body validation is a list.
      return typeof (error as { detail?: unknown } | undefined)?.detail === "string"
        ? { kind: "noRecipients" }
        : { kind: "invalid" };
    case 429:
      return { kind: "rateLimited", retryAfter: retryAfter(response) };
    default:
      throw new AdminApiError(response.status);
  }
}

export async function fetchBroadcasts(api: ApiClient, offset: number): Promise<Broadcast[]> {
  const { data, response } = await api.GET("/api/v1/notifications/broadcasts", {
    params: { query: { limit: COMMS_PAGE_SIZE, offset } },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** One broadcast with its delivery counts from the send log. */
export async function fetchBroadcast(api: ApiClient, id: string): Promise<Broadcast> {
  const { data, response } = await api.GET("/api/v1/notifications/broadcasts/{broadcast_id}", {
    params: { path: { broadcast_id: id } },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** The opt-out of the e-mail's link: the token in the body, never in a URL. */
export async function unsubscribe(api: ApiClient, token: string): Promise<void> {
  const { response } = await api.POST("/api/v1/notifications/broadcasts/unsubscribe", {
    body: { token },
  });
  if (!response.ok) throw new AdminApiError(response.status);
}

// --- delivery ledger (T-0226) -----------------------------------------------------------------

/** A period as typed: `YYYY-MM-DD` in the user's time zone, both days included. */
export interface Period {
  from: string;
  to: string;
}

/** Local midnight of `date` (plus `days`) as an aware ISO time. */
function localMidnight(date: string, days = 0): string {
  const d = new Date(`${date}T00:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString();
}

/** The period as `from` / `to` query values; `to` is the next local midnight (exclusive). */
export function periodQuery(period: Period): { from?: string; to?: string } {
  return {
    ...(period.from === "" ? {} : { from: localMidnight(period.from) }),
    ...(period.to === "" ? {} : { to: localMidnight(period.to, 1) }),
  };
}

/** A period whose start is after its end (the server would answer 422). */
export function periodInvalid(period: Period): boolean {
  return period.from !== "" && period.to !== "" && period.from > period.to;
}

export async function fetchMailStats(api: ApiClient, period: Period, template: string): Promise<MailStats> {
  const { data, response } = await api.GET("/api/v1/mail/stats", {
    params: { query: { ...periodQuery(period), ...(template === "" ? {} : { template }) } },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

export interface SendFilter extends Period {
  template: string;
  status: MailSendStatus | "";
  /** An exact address: hashed by the server, never shown back. */
  address: string;
}

export const NO_SEND_FILTER: SendFilter = { template: "", status: "", from: "", to: "", address: "" };

const ADDRESS = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** An address the server would refuse (422). */
export function addressInvalid(address: string): boolean {
  return address.trim() !== "" && !ADDRESS.test(address.trim());
}

export async function fetchSends(api: ApiClient, filter: SendFilter, offset: number): Promise<MailSend[]> {
  const address = filter.address.trim();
  const { data, response } = await api.GET("/api/v1/mail/sends", {
    params: {
      query: {
        ...periodQuery(filter),
        ...(filter.template === "" ? {} : { template: filter.template }),
        ...(filter.status === "" ? {} : { status: filter.status }),
        ...(address === "" ? {} : { address }),
        limit: COMMS_PAGE_SIZE,
        offset,
      },
    },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

// --- automated templates (T-0227) -------------------------------------------------------------

export async function fetchTemplates(api: ApiClient): Promise<MailTemplate[]> {
  const { data, response } = await api.GET("/api/v1/mail/templates");
  if (!data) throw new AdminApiError(response.status);
  return data;
}

/** The templates per audience group, in `TEMPLATE_AUDIENCES` order, each sorted by key. */
export function groupTemplates(
  templates: readonly MailTemplate[],
): { audience: MailTemplateAudience; templates: MailTemplate[] }[] {
  return TEMPLATE_AUDIENCES.map((audience) => ({
    audience,
    templates: templates.filter((tpl) => tpl.audience === audience).sort((a, b) => a.key.localeCompare(b.key)),
  })).filter((group) => group.templates.length > 0);
}

/** The editable texts of a template in one locale, as typed. */
export interface TemplateDraft {
  subject: string;
  intro: string;
  outro: string;
  actionLabel: string;
}

export const TEMPLATE_FIELDS = ["subject", "intro", "outro", "actionLabel"] as const;
export type TemplateField = (typeof TEMPLATE_FIELDS)[number];
/** The server's limits (`override_problems`). */
export const MAX_TEMPLATE: Record<TemplateField, number> = {
  subject: 200,
  intro: 2000,
  outro: 2000,
  actionLabel: 80,
};

/** The request body: `action_label` is `null` (never `""`) when empty or the template has no link. */
export function draftBody(
  draft: TemplateDraft,
  hasAction: boolean,
): components["schemas"]["MailTemplateTextWrite"] {
  const label = draft.actionLabel.trim();
  return {
    subject: draft.subject,
    intro: draft.intro,
    outro: draft.outro,
    action_label: hasAction && label !== "" ? draft.actionLabel : null,
  };
}

/** The fields the server would refuse as empty or too long (it checks the rest). */
export function draftProblems(
  draft: TemplateDraft,
  template: Pick<MailTemplate, "required" | "has_action">,
): Partial<Record<TemplateField, "empty" | "tooLong">> {
  const problems: Partial<Record<TemplateField, "empty" | "tooLong">> = {};
  for (const field of TEMPLATE_FIELDS) {
    if (field === "actionLabel" && !template.has_action) continue;
    const value = draft[field];
    const mustHave = field === "subject" || field === "actionLabel" || template.required;
    if (value.trim() === "") {
      if (mustHave) problems[field] = "empty";
    } else if (value.length > MAX_TEMPLATE[field]) {
      problems[field] = "tooLong";
    }
  }
  return problems;
}

/** Why the server refused a field (its English reason mapped to a message key). */
export type RefusalReason = "empty" | "tooLong" | "lineBreak" | "html" | "url" | "placeholder" | "other";

const FIELD_OF: Record<string, TemplateField> = {
  subject: "subject",
  intro: "intro",
  outro: "outro",
  action_label: "actionLabel",
};

function refusalReason(msg: string): RefusalReason {
  if (msg.startsWith("must not be empty")) return "empty";
  if (msg.startsWith("at most")) return "tooLong";
  if (msg.includes("HTML")) return "html";
  if (msg.includes("line breaks")) return "lineBreak";
  if (msg.startsWith("no links")) return "url";
  if (msg.includes("placeholder") || msg.includes("link secret") || msg.includes("{")) return "placeholder";
  return "other";
}

/**
 * A `422`'s per-field problems (`loc: ["body", "<field>"]`); never the server's text. The
 * server's reasons are English text (`override_problems`, backend `core/mail/templates.py`);
 * `comms.test.ts` lists each one so a change there fails here. Stable codes: TD-610.
 */
export function refusedFields(error: unknown): Partial<Record<TemplateField, RefusalReason>> {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  const out: Partial<Record<TemplateField, RefusalReason>> = {};
  if (!Array.isArray(detail)) return out;
  for (const item of detail as { loc?: unknown; msg?: unknown }[]) {
    const loc = Array.isArray(item.loc) ? item.loc : [];
    const field = FIELD_OF[String(loc.at(-1))];
    if (field) out[field] = refusalReason(typeof item.msg === "string" ? item.msg : "");
  }
  return out;
}

export type SaveTemplateResult =
  | { kind: "ok"; revision: TemplateRevision }
  | { kind: "invalid"; fields: Partial<Record<TemplateField, RefusalReason>> }
  | { kind: "conflict" };

export async function saveTemplate(
  api: ApiClient,
  key: string,
  locale: string,
  draft: TemplateDraft,
  hasAction: boolean,
): Promise<SaveTemplateResult> {
  const { data, error, response } = await api.PUT("/api/v1/mail/templates/{template_key}/{locale}", {
    params: { path: { template_key: key, locale } },
    body: draftBody(draft, hasAction),
  });
  if (data) return { kind: "ok", revision: data };
  if (response.status === 422) return { kind: "invalid", fields: refusedFields(error) };
  if (response.status === 409) return { kind: "conflict" };
  throw new AdminApiError(response.status);
}

/** Back to the shipped default (revisions are kept). */
export async function resetTemplate(api: ApiClient, key: string, locale: string): Promise<void> {
  const { response } = await api.DELETE("/api/v1/mail/templates/{template_key}/{locale}", {
    params: { path: { template_key: key, locale } },
  });
  if (!response.ok) throw new AdminApiError(response.status);
}

export async function fetchRevisions(api: ApiClient, key: string, locale: string): Promise<TemplateRevision[]> {
  const { data, response } = await api.GET("/api/v1/mail/templates/{template_key}/{locale}/revisions", {
    params: { path: { template_key: key, locale } },
  });
  if (!data) throw new AdminApiError(response.status);
  return data;
}

export type PreviewResult =
  | { kind: "ok"; preview: TemplatePreview }
  | { kind: "invalid"; fields: Partial<Record<TemplateField, RefusalReason>> };

/** The draft rendered with synthetic sample values (checked like a save, never stored). */
export async function previewTemplate(
  api: ApiClient,
  key: string,
  locale: string,
  draft: TemplateDraft,
  hasAction: boolean,
): Promise<PreviewResult> {
  const { data, error, response } = await api.POST(
    "/api/v1/mail/templates/{template_key}/{locale}/preview",
    { params: { path: { template_key: key, locale } }, body: draftBody(draft, hasAction) },
  );
  if (data) return { kind: "ok", preview: data };
  if (response.status === 422) return { kind: "invalid", fields: refusedFields(error) };
  throw new AdminApiError(response.status);
}

export type TestSendResult = { kind: "ok" } | { kind: "rateLimited"; retryAfter: number | null };

/** Queue the current text with sample values to the caller's own address. */
export async function testSendTemplate(api: ApiClient, key: string, locale: string): Promise<TestSendResult> {
  const { response } = await api.POST("/api/v1/mail/templates/{template_key}/{locale}/test-send", {
    params: { path: { template_key: key, locale } },
  });
  if (response.ok) return { kind: "ok" };
  if (response.status === 429) return { kind: "rateLimited", retryAfter: retryAfter(response) };
  throw new AdminApiError(response.status);
}

// --- mail health (diff §4.7 'Editor details') -------------------------------------------------

export type MailProblem = "notConfigured" | "noSender";

/**
 * What the deployment configuration says about sending (never a credential): the `log`
 * transport delivers nothing, and no sender address means no mail can go out. Whether SES
 * verified the sender and whether bounce notifications are wired is not exposed yet (TODO in
 * the task notes).
 */
export function mailProblems(config: {
  mail_transport: string;
  mail_from_address: string | null;
}): MailProblem[] {
  const problems: MailProblem[] = [];
  if (config.mail_transport !== "ses") problems.push("notConfigured");
  if (!config.mail_from_address) problems.push("noSender");
  return problems;
}
