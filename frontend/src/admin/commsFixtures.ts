// Synthetic Communications data (T-0229): broadcasts, the delivery ledger, mail templates.
import type { Broadcast, MailCounts, MailSend, MailStats, MailTemplate, TemplateRevision } from "./comms";
import { DEPLOYMENT_CONFIG } from "./settingsFixtures";
import { ADMIN_PROJECT } from "./testing";

export const BROADCAST_ID = "00000000-0000-4000-8000-00000000b001";
export const SENDER_ID = "00000000-0000-4000-8000-000000000001";

export const COUNTS: MailCounts = {
  total: 40,
  queued: 2,
  sent: 36,
  delivered: 33,
  bounced: 2,
  complained: 1,
  failed: 1,
  suppressed: 1,
  delivery_rate: 0.9167,
};

export const BROADCAST: Broadcast = {
  id: BROADCAST_ID,
  sender_id: SENDER_ID,
  scope: "projects",
  project_ids: [ADMIN_PROJECT.id],
  category: "internal",
  personas: [],
  subject: "Synthetic shutdown briefing",
  body: "Briefing moved to 07:00.\nBring PPE.",
  status: "sent",
  recipient_count: 12,
  queued_count: 11,
  skipped_count: 1,
  created_at: "2026-11-20T08:00:00Z",
  finished_at: "2026-11-20T08:02:00Z",
};

export const STATS: MailStats = {
  created_from: null,
  created_to: null,
  template_key: null,
  totals: COUNTS,
  by_template: [
    { ...COUNTS, template_key: "notifications.broadcast", total: 30 },
    { ...COUNTS, template_key: "auth.magic_link", total: 10, delivery_rate: null },
  ],
};

export const SEND: MailSend = {
  id: "00000000-0000-4000-8000-00000000c001",
  template_key: "auth.magic_link",
  recipient_masked: "f***@example.test",
  recipient_hash: "0".repeat(64),
  status: "bounced",
  recipient_user_id: null,
  project_id: null,
  broadcast_id: null,
  attempts: 1,
  provider_message_id: "synthetic-message-1",
  error_code: null,
  created_at: "2026-11-20T08:00:00Z",
  sent_at: "2026-11-20T08:00:05Z",
  status_at: "2026-11-20T08:01:00Z",
};

const text = (subject: string, actionLabel: string | null) => ({
  subject,
  intro: "Hello {user_name}, synthetic intro.",
  outro: "Synthetic outro.",
  action_label: actionLabel,
});

export const REVISION: TemplateRevision = {
  ...text("Reworded rejection for {project_code}", "Open {app_name}"),
  id: "00000000-0000-4000-8000-00000000e002",
  template_key: "access_requests.rejected_no_reason",
  locale: "en-AU",
  version: 2,
  is_current: true,
  created_at: "2026-11-21T08:00:00Z",
  created_by: SENDER_ID,
};

export const OLD_REVISION: TemplateRevision = {
  ...REVISION,
  ...text("First wording for {project_code}", "Open {app_name}"),
  id: "00000000-0000-4000-8000-00000000e001",
  version: 1,
  is_current: false,
  created_at: "2026-11-20T08:00:00Z",
};

export const TEMPLATES: MailTemplate[] = [
  {
    key: "notifications.broadcast",
    audience: "deployment_wide",
    required: false,
    has_action: true,
    placeholders: ["app_name", "message", "subject"],
    locales: [{ locale: "en-AU", shipped: text("{subject}", "Unsubscribe"), override: null }],
  },
  {
    key: "auth.magic_link",
    audience: "internal_staff",
    required: true,
    has_action: true,
    placeholders: ["app_name", "expires_minutes"],
    locales: [{ locale: "en-AU", shipped: text("Your {app_name} sign-in link", "Sign in"), override: null }],
  },
  {
    key: "auth.password_changed",
    audience: "internal_staff",
    required: true,
    has_action: false,
    placeholders: ["app_name"],
    locales: [{ locale: "en-AU", shipped: text("Your {app_name} password was changed", null), override: null }],
  },
  {
    key: "access_requests.rejected_no_reason",
    audience: "external_party",
    required: false,
    has_action: true,
    placeholders: ["app_name", "project_code"],
    locales: [
      {
        locale: "en-AU",
        shipped: text("Your request for {project_code}", "Open {app_name}"),
        override: REVISION,
      },
    ],
  },
];

export const PREVIEW = {
  subject: "Your KERP sign-in link",
  text: "Hello Sam Sample, synthetic intro.",
  html: "<!doctype html><html><body><p>Hello Sam Sample</p><script>window.parent.hacked = true</script></body></html>",
};

/** A deployment that can send: SES with a sender. */
export const MAIL_READY = { ...DEPLOYMENT_CONFIG, mail_transport: "ses" as const, mail_from_address: "KERP <no-reply@mail.example.test>" };
/** A development deployment: the log transport, no sender. */
export const MAIL_NOT_READY = { ...DEPLOYMENT_CONFIG, mail_transport: "log" as const, mail_from_address: null };
