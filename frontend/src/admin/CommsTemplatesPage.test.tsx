// Admin → Communications → E-mail templates (T-0229, diff §4.7 'Editor details'). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json } from "../test/fakeApi";
import { draftBody, mailProblems } from "./comms";
import { MAIL_NOT_READY, MAIL_READY, OLD_REVISION, PREVIEW, REVISION, TEMPLATES } from "./commsFixtures";
import { renderAdmin } from "./testing";

vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/admin/communications/templates";
const BASE = "/api/v1/mail/templates";
const handlers = (extra = {}) => ({
  [`GET ${BASE}`]: () => json(TEMPLATES),
  "GET /api/v1/settings/deployment-config": () => json(MAIL_READY),
  [`GET ${BASE}/auth.magic_link/en-AU/revisions`]: () => json([]),
  [`GET ${BASE}/auth.password_changed/en-AU/revisions`]: () => json([]),
  [`GET ${BASE}/access_requests.rejected_no_reason/en-AU/revisions`]: () => json([REVISION, OLD_REVISION]),
  ...extra,
});
const SYS = { projects: [], systemAdmin: true };
/** `t()` over keys built at run time (the typed `t` takes literal keys only). */
const tr = i18n.t.bind(i18n) as unknown as (key: string, vars?: Record<string, unknown>) => string;
const open = async (key: string) => {
  fireEvent.click(await screen.findByRole("button", { name: tr("admin.comms.templates.open", { key }) }, WAIT));
  return within(await screen.findByRole("region", { name: tr("admin.comms.templates.editorTitle", { key }) }, WAIT));
};
const field = (editor: { getByLabelText: (text: string) => HTMLElement }, name: string) =>
  editor.getByLabelText(tr(`admin.comms.templates.fields.${name}`)) as HTMLInputElement;

describe("template list", () => {
  it("groups the templates by audience and marks required and reworded ones", async () => {
    renderAdmin(PATH, handlers(), SYS);
    const list = within(await screen.findByRole("region", { name: tr("admin.comms.templates.listTitle") }, WAIT));
    expect(list.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      tr("admin.comms.templates.audiences.internal_staff"),
      tr("admin.comms.templates.audiences.external_party"),
      tr("admin.comms.templates.audiences.deployment_wide"),
    ]);
    const internal = within(list.getByRole("region", { name: tr("admin.comms.templates.audiences.internal_staff") }));
    expect(internal.getAllByText(tr("admin.comms.templates.required"))).toHaveLength(2);
    const external = within(list.getByRole("region", { name: tr("admin.comms.templates.audiences.external_party") }));
    expect(external.getByText(tr("admin.comms.templates.overridden"))).toBeInTheDocument();
  });
});

describe("mail health", () => {
  it("shows no banner when the deployment can send", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await screen.findByRole("region", { name: tr("admin.comms.templates.listTitle") }, WAIT);
    expect(screen.queryByText(tr("admin.comms.templates.health.title"))).not.toBeInTheDocument();
  });

  it("says why e-mail cannot be sent, never showing the configuration's values", async () => {
    renderAdmin(PATH, handlers({ "GET /api/v1/settings/deployment-config": () => json(MAIL_NOT_READY) }), SYS);
    const banner = await screen.findByText(tr("admin.comms.templates.health.title"), {}, WAIT);
    expect(banner.closest("[role=alert]")).toHaveTextContent(tr("admin.comms.templates.health.notConfigured"));
    expect(banner.closest("[role=alert]")).toHaveTextContent(tr("admin.comms.templates.health.noSender"));
    expect(document.body.textContent).not.toContain("no-reply@");
  });

  it("derives the problems from transport and sender", () => {
    expect(mailProblems(MAIL_READY)).toEqual([]);
    expect(mailProblems(MAIL_NOT_READY)).toEqual(["notConfigured", "noSender"]);
  });
});

describe("template editor", () => {
  it("edits the shipped text, inserts a placeholder at the cursor and saves", async () => {
    let saved: unknown;
    const calls = renderAdmin(
      PATH,
      handlers({
        [`PUT ${BASE}/auth.magic_link/en-AU`]: (req: { body: unknown }) => {
          saved = req.body;
          return json({ ...REVISION, template_key: "auth.magic_link", version: 1 });
        },
      }),
      SYS,
    );
    const editor = await open("auth.magic_link");
    expect(editor.getByText(tr("admin.comms.templates.requiredNote"))).toBeInTheDocument();
    const subject = field(editor, "subject");
    expect(subject).toHaveValue("Your {app_name} sign-in link");
    fireEvent.change(subject, { target: { value: "Sign in now" } });
    fireEvent.focus(subject);
    subject.setSelectionRange(4, 4);
    const placeholders = within(editor.getByRole("region", { name: tr("admin.comms.templates.placeholdersTitle") }));
    expect(placeholders.getAllByRole("button").map((b) => b.textContent)).toEqual(["{app_name}", "{expires_minutes}"]);
    fireEvent.click(placeholders.getByRole("button", { name: tr("admin.comms.templates.insert", { name: "{app_name}" }) }));
    expect(subject).toHaveValue("Sign{app_name} in now");

    fireEvent.click(editor.getByRole("button", { name: tr("admin.comms.templates.save") }));
    expect(await editor.findByText(tr("admin.comms.templates.saved", { version: 1 }), {}, WAIT)).toBeInTheDocument();
    expect(saved).toEqual({
      subject: "Sign{app_name} in now",
      intro: "Hello {user_name}, synthetic intro.",
      outro: "Synthetic outro.",
      action_label: "Sign in",
    });
    await waitFor(() => { expect(calls.filter((c) => c.method === "GET" && c.path === BASE).length).toBeGreaterThan(1); }, WAIT);
  });

  it("refuses empty texts of a required template before sending", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    const editor = await open("auth.magic_link");
    fireEvent.change(field(editor, "intro"), { target: { value: "  " } });
    fireEvent.change(field(editor, "actionLabel"), { target: { value: "" } });
    fireEvent.click(editor.getByRole("button", { name: tr("admin.comms.templates.save") }));
    expect(editor.getAllByText(tr("admin.comms.templates.problems.empty"))).toHaveLength(2);
    expect(field(editor, "intro")).toHaveAttribute("aria-invalid", "true");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("shows the server's refusals per field, translated", async () => {
    renderAdmin(
      PATH,
      handlers({
        [`PUT ${BASE}/auth.magic_link/en-AU`]: () =>
          json(
            {
              detail: [
                { loc: ["body", "intro"], msg: "plain text only: HTML is not allowed", type: "value_error" },
                { loc: ["body", "subject"], msg: "unknown placeholder {secret}", type: "value_error" },
              ],
            },
            422,
          ),
      }),
      SYS,
    );
    const editor = await open("auth.magic_link");
    fireEvent.click(editor.getByRole("button", { name: tr("admin.comms.templates.save") }));
    expect(await editor.findByText(tr("admin.comms.templates.problems.html"), {}, WAIT)).toBeInTheDocument();
    expect(editor.getByText(tr("admin.comms.templates.problems.placeholder"))).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("plain text only: HTML");
  });

  it("sends null, never an empty string, for the action label of a template without a link", async () => {
    let saved: unknown;
    renderAdmin(
      PATH,
      handlers({
        [`PUT ${BASE}/auth.password_changed/en-AU`]: (req: { body: unknown }) => {
          saved = req.body;
          return json({ ...REVISION, version: 1 });
        },
      }),
      SYS,
    );
    const editor = await open("auth.password_changed");
    expect(editor.queryByLabelText(tr("admin.comms.templates.fields.actionLabel"))).not.toBeInTheDocument();
    fireEvent.click(editor.getByRole("button", { name: tr("admin.comms.templates.save") }));
    await editor.findByText(tr("admin.comms.templates.saved", { version: 1 }), {}, WAIT);
    expect(saved).toMatchObject({ action_label: null });
    expect(draftBody({ subject: "s", intro: "i", outro: "o", actionLabel: "  " }, true).action_label).toBeNull();
  });

  it("reloads the texts after a concurrent save", async () => {
    let list = TEMPLATES;
    renderAdmin(
      PATH,
      handlers({
        [`GET ${BASE}`]: () => json(list),
        [`PUT ${BASE}/auth.magic_link/en-AU`]: () => {
          list = TEMPLATES.map((tpl) =>
            tpl.key === "auth.magic_link"
              ? { ...tpl, locales: tpl.locales.map((l) => ({ ...l, override: { ...REVISION, subject: "Saved elsewhere" } })) }
              : tpl,
          );
          return json({ detail: "conflict" }, 409);
        },
      }),
      SYS,
    );
    const editor = await open("auth.magic_link");
    fireEvent.change(field(editor, "subject"), { target: { value: "Mine" } });
    fireEvent.click(editor.getByRole("button", { name: tr("admin.comms.templates.save") }));
    expect(await editor.findByText(tr("admin.comms.templates.conflict"), {}, WAIT)).toBeInTheDocument();
    expect(field(editor, "subject")).toHaveValue("Saved elsewhere");
  });

  it("previews the draft in a sandboxed frame only", async () => {
    const calls = renderAdmin(PATH, handlers({ [`POST ${BASE}/auth.magic_link/en-AU/preview`]: () => json(PREVIEW) }), SYS);
    const editor = await open("auth.magic_link");
    fireEvent.change(field(editor, "subject"), { target: { value: "Draft subject" } });
    fireEvent.click(editor.getByRole("button", { name: tr("admin.comms.templates.preview") }));
    const preview = within(await editor.findByRole("region", { name: tr("admin.comms.templates.previewTitle") }, WAIT));
    expect(preview.getByText(tr("admin.comms.templates.previewSubject", { subject: PREVIEW.subject }))).toBeInTheDocument();
    expect(preview.getByText(PREVIEW.text)).toBeInTheDocument();
    const frame = preview.getByTitle(tr("admin.comms.templates.previewHtml"));
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(frame.getAttribute("srcdoc")).toBe(PREVIEW.html);
    // The HTML is never put into the page itself.
    expect(document.querySelector("script")).toBeNull();
    expect(calls.find((c) => c.path.endsWith("/preview"))?.body).toMatchObject({ subject: "Draft subject" });
  });

  it("queues a test to the caller's own address and explains the rate limit", async () => {
    let status = 202;
    renderAdmin(
      PATH,
      handlers({
        [`POST ${BASE}/auth.magic_link/en-AU/test-send`]: () =>
          status === 202
            ? json({ send_id: "00000000-0000-4000-8000-00000000c009" }, 202)
            : new Response(JSON.stringify({ detail: "x" }), { status: 429, headers: { "Content-Type": "application/json" } }),
      }),
      SYS,
    );
    const editor = await open("auth.magic_link");
    const button = editor.getByRole("button", { name: tr("admin.comms.templates.testSend") });
    fireEvent.click(button);
    expect(await editor.findByText(tr("admin.comms.templates.testSent"), {}, WAIT)).toBeInTheDocument();
    status = 429;
    fireEvent.click(button);
    expect(await editor.findByText(tr("admin.comms.templates.testLimitedLater"), {}, WAIT)).toBeInTheDocument();
  });

  it("resets to the shipped default after confirmation", async () => {
    let list = TEMPLATES;
    const calls = renderAdmin(
      PATH,
      handlers({
        [`GET ${BASE}`]: () => json(list),
        [`DELETE ${BASE}/access_requests.rejected_no_reason/en-AU`]: () => {
          list = TEMPLATES.map((tpl) => ({ ...tpl, locales: tpl.locales.map((l) => ({ ...l, override: null })) }));
          return new Response(null, { status: 204 });
        },
      }),
      SYS,
    );
    const editor = await open("access_requests.rejected_no_reason");
    expect(field(editor, "subject")).toHaveValue(REVISION.subject);
    fireEvent.click(editor.getByRole("button", { name: tr("admin.comms.templates.reset") }));
    const dialog = within(
      await screen.findByRole("dialog", { name: tr("admin.comms.templates.resetTitle", { key: "access_requests.rejected_no_reason" }) }),
    );
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(dialog.getByRole("button", { name: tr("admin.comms.templates.reset") }));
    expect(await editor.findByText(tr("admin.comms.templates.resetDone"), {}, WAIT)).toBeInTheDocument();
    await waitFor(() => { expect(field(editor, "subject")).toHaveValue("Your request for {project_code}"); }, WAIT);
  });

  it("lists the revisions and loads one into the editor", async () => {
    renderAdmin(PATH, handlers(), SYS);
    const editor = await open("access_requests.rejected_no_reason");
    const revisions = within(editor.getByRole("region", { name: tr("admin.comms.templates.revisionsTitle") }));
    const items = await revisions.findAllByRole("listitem", {}, WAIT);
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent(tr("admin.comms.templates.current"));
    fireEvent.click(revisions.getByRole("button", { name: tr("admin.comms.templates.load", { version: 1 }) }));
    expect(field(editor, "subject")).toHaveValue(OLD_REVISION.subject);
  });

  it("says when a template has no revisions", async () => {
    renderAdmin(PATH, handlers(), SYS);
    const editor = await open("auth.magic_link");
    expect(await editor.findByText(tr("admin.comms.templates.noRevisions"), {}, WAIT)).toBeInTheDocument();
  });
});

describe("template access", () => {
  it("refuses a PER-09 without calling the mail API", async () => {
    const calls = renderAdmin(PATH, handlers());
    expect(await screen.findByText(tr("admin.comms.forbiddenSystem"), {}, WAIT)).toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/v1/mail/") || c.path.startsWith("/api/v1/settings/"))).toBe(false);
  });
});
