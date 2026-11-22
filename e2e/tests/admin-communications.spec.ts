/**
 * Admin → Communications journey (T-0229, diff §4.7, §6.5), on the real stack: the System
 * admin opens Communications from the navigation, counts a broadcast's audience and sends it
 * after confirming, rewords an automated e-mail template (placeholder chip, sandboxed preview,
 * revision) and resets it, reads the delivery ledger; a signed-out visitor uses the broadcast
 * opt-out page `/unsubscribe#<token>` (TD-601).
 *
 * One serial journey in one browser context. UI text comes from the app's catalogue (`t`).
 */
import { expect, test, type BrowserContext, type Locator, type Page } from "@playwright/test";

import { captureLogin, json, type Session } from "../support/api";
import { ADMIN_EMAIL, ADMIN_PASSWORD, BASE_URL } from "../support/env";
import { t } from "../support/i18n";
import { runState } from "../support/state";

interface Project {
  id: string;
  code: string;
  name: string;
}
interface Broadcast {
  sender_id: string;
  subject: string;
  created_at: string;
}
interface MailTemplate {
  key: string;
  locales: { locale: string; shipped: { subject: string } }[];
}

/** The template the journey rewords; nothing else in the suite sends it. */
const TEMPLATE = "notifications.broadcast";
/** The broadcasts a sender may queue per 24 h (notifications README, T-0228). */
const BROADCAST_LIMIT = 5;

test.describe.configure({ mode: "serial" });

test.describe("Admin → Communications journey", () => {
  let context: BrowserContext;
  let page: Page;
  let admin: Session;
  let run: string;
  let project: Project;

  test.beforeAll(async ({ browser }) => {
    run = runState().run;
    context = await browser.newContext({ baseURL: BASE_URL });
    page = await context.newPage();
  });

  test.afterAll(async () => {
    await context.close();
  });

  function status(text: string): Locator {
    return page.getByRole("status").and(page.getByText(text, { exact: true }));
  }

  test("the System admin opens Communications from the Admin navigation", async () => {
    await page.goto("/login");
    const token = await captureLogin(page, async () => {
      await page.getByLabel(t("auth.login.email"), { exact: true }).fill(ADMIN_EMAIL);
      await page.getByLabel(t("auth.login.password"), { exact: true }).fill(ADMIN_PASSWORD);
      await page.getByRole("button", { name: t("auth.login.submit"), exact: true }).click();
    });
    admin = { request: context.request, token };
    const nav = page.getByRole("navigation", { name: t("nav.label"), exact: true });
    await nav.getByRole("link", { name: t("nav.items.communications"), exact: true }).click();
    await expect(page).toHaveURL(`${BASE_URL}/admin/communications/broadcasts`);
    await expect(page.getByRole("heading", { name: t("admin.comms.broadcasts.title"), level: 1 })).toBeVisible();
    const tabs = page.getByRole("navigation", { name: t("admin.comms.tabs"), exact: true });
    await expect(tabs.getByRole("link")).toHaveText([
      t("admin.comms.broadcastsTab"),
      t("admin.comms.deliveryTab"),
      t("admin.comms.templatesTab"),
    ]);
  });

  test("broadcast: count the audience of this run's project, confirm and send", async () => {
    // A project of this run: its only member is the admin (PER-09 as creator), so the count is 1.
    project = await json<Project>(admin, "POST", "/api/v1/projects", 201, {
      code: `COM-${run}`.slice(0, 32),
      name: `E2E communications ${run}`,
    });
    const since = Date.now() - 24 * 3600 * 1000;
    const me = await json<{ id: string }>(admin, "GET", "/api/v1/auth/me", 200);
    const mine = (await json<Broadcast[]>(admin, "GET", "/api/v1/notifications/broadcasts?limit=200", 200))
      .filter((b) => b.sender_id === me.id && Date.parse(b.created_at) > since);
    test.skip(
      mine.length >= BROADCAST_LIMIT,
      "TD-608: the shared System admin used its 5 broadcasts per 24 h on this database (local re-runs); CI starts empty",
    );

    await page.reload();
    await page.getByRole("radio", { name: t("admin.comms.broadcasts.scopeProjects"), exact: true }).check();
    await page
      .getByRole("checkbox", { name: t("admin.comms.broadcasts.project", { code: project.code, name: project.name }) })
      .check();
    const subject = `E2E broadcast ${run}`;
    await page.getByLabel(t("admin.comms.broadcasts.subject"), { exact: true }).fill(subject);
    await page.getByLabel(t("admin.comms.broadcasts.body"), { exact: true }).fill(`Synthetic message.\nRun ${run}.`);
    const send = page.getByRole("button", { name: t("admin.comms.broadcasts.send"), exact: true });
    await expect(send).toBeDisabled();
    await page.getByRole("button", { name: t("admin.comms.broadcasts.count"), exact: true }).click();
    await expect(status(t("admin.comms.broadcasts.recipients", { count: "1" }))).toBeVisible();
    await send.click();

    const dialog = page.getByRole("dialog", { name: t("admin.comms.broadcasts.confirmTitle") });
    await expect(dialog.getByText(t("admin.comms.broadcasts.confirmBody", { subject, count: "1" }))).toBeVisible();
    const queued = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/v1/notifications/broadcasts" && r.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: t("admin.comms.broadcasts.confirm", { count: "1" }) }).click();
    const response = await queued;
    expect(response.status()).toBe(202);
    const sent = (await response.json()) as Broadcast & { id: string };
    const read = await json<Broadcast & { scope: string; project_ids: string[]; body: string; recipient_count: number }>(
      admin, "GET", `/api/v1/notifications/broadcasts/${sent.id}`, 200,
    );
    expect(read).toMatchObject({
      scope: "projects",
      project_ids: [project.id],
      subject,
      body: `Synthetic message.\nRun ${run}.`,
      recipient_count: 1,
    });
    await expect(status(t("admin.comms.broadcasts.queued", { count: "1" }))).toBeVisible();
    const history = page.getByRole("region", { name: t("admin.comms.broadcasts.historyTitle") });
    await expect(history.getByRole("heading", { name: subject, exact: true })).toBeVisible();
  });

  test("templates: health banner, placeholder chip, sandboxed preview, save, revision and reset", async () => {
    const templates = await json<MailTemplate[]>(admin, "GET", "/api/v1/mail/templates", 200);
    const shipped = templates.find((tpl) => tpl.key === TEMPLATE)?.locales[0]?.shipped.subject;
    expect(shipped).toBeDefined();

    await page
      .getByRole("navigation", { name: t("admin.comms.tabs"), exact: true })
      .getByRole("link", { name: t("admin.comms.templatesTab"), exact: true })
      .click();
    // The e2e API runs the development `log` transport: the banner says mail is not delivered.
    await expect(page.getByRole("alert").filter({ hasText: t("admin.comms.templates.health.title") }))
      .toContainText(t("admin.comms.templates.health.notConfigured"));
    const group = page.getByRole("region", { name: t("admin.comms.templates.audiences.deployment_wide"), exact: true });
    await group.getByRole("button", { name: t("admin.comms.templates.open", { key: TEMPLATE }) }).click();
    const editor = page.getByRole("region", { name: t("admin.comms.templates.editorTitle", { key: TEMPLATE }) });

    const subjectField = editor.getByLabel(t("admin.comms.templates.fields.subject"), { exact: true });
    const prefix = `[E2E ${run}] `;
    await subjectField.fill(prefix);
    await subjectField.focus();
    await editor.getByRole("button", { name: t("admin.comms.templates.insert", { name: "{subject}" }) }).click();
    await expect(subjectField).toHaveValue(`${prefix}{subject}`);

    await editor.getByRole("button", { name: t("admin.comms.templates.preview"), exact: true }).click();
    const preview = editor.getByRole("region", { name: t("admin.comms.templates.previewTitle") });
    await expect(preview.getByText(prefix, { exact: false }).first()).toBeVisible();
    const frame = preview.locator("iframe");
    await expect(frame).toHaveAttribute("sandbox", "");
    await expect(frame).toHaveAttribute("title", t("admin.comms.templates.previewHtml"));

    const saved = page.waitForResponse(
      (r) => r.url().endsWith(`/api/v1/mail/templates/${TEMPLATE}/en-AU`) && r.request().method() === "PUT",
    );
    await editor.getByRole("button", { name: t("admin.comms.templates.save"), exact: true }).click();
    const response = await saved;
    expect(response.status()).toBe(200);
    const revision = (await response.json()) as { version: number };
    await expect(status(t("admin.comms.templates.saved", { version: String(revision.version) }))).toBeVisible();
    const revisions = editor.getByRole("region", { name: t("admin.comms.templates.revisionsTitle") });
    await expect(
      revisions.getByRole("listitem").filter({ hasText: t("admin.comms.templates.revision", { version: String(revision.version) }) }),
    ).toContainText(t("admin.comms.templates.current"));

    await editor.getByRole("button", { name: t("admin.comms.templates.reset"), exact: true }).click();
    const dialog = page.getByRole("dialog", { name: t("admin.comms.templates.resetTitle", { key: TEMPLATE }) });
    await dialog.getByRole("button", { name: t("admin.comms.templates.reset"), exact: true }).click();
    await expect(status(t("admin.comms.templates.resetDone"))).toBeVisible();
    await expect(subjectField).toHaveValue(shipped ?? "");
    const after = await json<{ key: string; locales: { override: unknown }[] }[]>(admin, "GET", "/api/v1/mail/templates", 200);
    expect(after.find((tpl) => tpl.key === TEMPLATE)?.locales[0]?.override).toBeNull();
  });

  test("delivery: count cards and the history with masked recipients", async () => {
    await page
      .getByRole("navigation", { name: t("admin.comms.tabs"), exact: true })
      .getByRole("link", { name: t("admin.comms.deliveryTab"), exact: true })
      .click();
    const stats = page.getByRole("region", { name: t("admin.comms.delivery.statsTitle"), exact: true });
    await expect(stats.getByText(t("admin.comms.counts.deliveryRate"), { exact: true }).first()).toBeVisible();
    const history = page.getByRole("region", { name: t("admin.comms.delivery.historyTitle"), exact: true });
    await history.getByLabel(t("admin.comms.delivery.address"), { exact: true }).fill(ADMIN_EMAIL);
    const searched = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/v1/mail/sends");
    await history.getByRole("button", { name: t("admin.comms.delivery.search"), exact: true }).click();
    expect((await searched).status()).toBe(200);
    // The address is looked up, never shown back.
    await expect(page.getByText(ADMIN_EMAIL, { exact: true })).toHaveCount(0);
  });

  test("unsubscribe: the opt-out page posts the fragment's token and answers neutrally", async ({ browser }) => {
    const visitor = await browser.newContext({ baseURL: BASE_URL });
    const tab = await visitor.newPage();
    const token = `e2eUnknownToken_${run}_0123456789abcdef`;
    await tab.goto(`/unsubscribe#${token}`);
    await expect(tab.getByRole("heading", { name: t("unsubscribe.title"), level: 1 })).toBeVisible();
    await expect.poll(() => new URL(tab.url()).hash).toBe("");
    expect(new URL(tab.url()).pathname).toBe("/unsubscribe");
    const posted = tab.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/v1/notifications/broadcasts/unsubscribe",
    );
    await tab.getByRole("button", { name: t("unsubscribe.confirm"), exact: true }).click();
    const response = await posted;
    expect(response.status()).toBe(200);
    expect(new URL(response.url()).search).toBe("");
    // The token travels in the body only (the component test checks the body itself).
    await expect(tab.getByRole("status")).toHaveText(t("unsubscribe.done"));
    await visitor.close();
  });
});
