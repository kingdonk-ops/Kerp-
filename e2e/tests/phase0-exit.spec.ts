/**
 * Phase 0 exit journey (DEVELOPMENT-PLAN.md, Phases table; T-0027): tenant/project created,
 * user authed and scoped, module toggled, rule set run, on the real stack.
 *
 * One serial journey in one browser context. Every step asserts what the user sees or what
 * the API answers. UI text comes from the app's catalogue (`t`), never typed here.
 */
import { expect, test, type BrowserContext, type Locator, type Page } from "@playwright/test";

import { NAV_GROUP_IDS } from "../../frontend/src/app/nav";
import { SESSION_STORAGE_KEY } from "../../frontend/src/auth/tokenStore";
import { call, captureLogin, json, type Session } from "../support/api";
import {
  ADMIN_EMAIL,
  ADMIN_NAME,
  ADMIN_PASSWORD,
  API_URL,
  BASE_URL,
  QA_PASSWORD,
  TENANT_ID,
} from "../support/env";
import { t } from "../support/i18n";
import { runState, type RunState } from "../support/state";

interface Project {
  id: string;
  code: string;
  name: string;
}
interface Member {
  user_id: string;
  email: string;
  grants: { persona: string; source: string }[];
}
interface ModuleState {
  name: string;
  toggleable: boolean;
  enabled: boolean;
}
interface Rule {
  rule_key: string;
  expression: string;
  version: number;
}
interface RuleSet {
  id: string;
  key: string;
  kind: string;
  layer: string;
  module: string | null;
  rules: Rule[];
}
interface TryResult {
  outcome: "pass" | "fail" | "error";
  finding: { rule_key: string; severity: string; action: string; outcome: string } | null;
}

const persona = (code: string) =>
  t("admin.accessRequests.persona", { code, name: t(`personas.${code}`) });
const requester = (name: string, email: string) =>
  t("admin.accessRequests.requester", { name, email });

// The qty rule of the rule-set step: a `check` rule sees `fields` (CEL context of the kind).
const QTY_RULE = {
  engine: "cel",
  expression: "fields.qty > 0",
  action: "block",
  severity: "error",
} as const;

test.describe.configure({ mode: "serial" });

test.describe("Phase 0 exit journey", () => {
  let context: BrowserContext;
  let page: Page;
  let admin: Session;
  let qa: Session;
  let project: Project;
  let modules: ModuleState[];
  // This run's data (globalSetup's state is in the environment only once setup has run).
  let state: RunState;
  let qaName: string;
  let projectCode: string;
  let projectName: string;
  let projectLabel: string;

  test.beforeAll(async ({ browser }) => {
    state = runState();
    qaName = state.seed.qa_name;
    projectCode = `E2E-${state.run}`;
    projectName = `E2E journey ${state.run}`;
    projectLabel = t("scope.projectOption", { code: projectCode, name: projectName });
    context = await browser.newContext({ baseURL: BASE_URL });
    page = await context.newPage();
  });

  test.afterAll(async () => {
    await context.close();
  });

  async function signIn(email: string, password: string): Promise<void> {
    await page.getByLabel(t("auth.login.email"), { exact: true }).fill(email);
    await page.getByLabel(t("auth.login.password"), { exact: true }).fill(password);
    await page.getByRole("button", { name: t("auth.login.submit"), exact: true }).click();
  }

  /** The page's status message saying exactly `text` (a page may hold several regions). */
  function status(text: string): Locator {
    return page.getByRole("status").and(page.getByText(text, { exact: true }));
  }

  function listItem(text: string): Locator {
    return page.getByRole("listitem").filter({ hasText: text });
  }

  test("tenant: the API is up and first-run provisioned the tenant idempotently", async () => {
    const health = await context.request.get(`${API_URL}/health`);
    expect(health.status()).toBe(200);
    expect(await health.json()).toMatchObject({ status: "ok" });
    expect(state.firstRun).toContain(`KERP_DEFAULT_TENANT_ID=${TENANT_ID}\n`);
    expect(state.firstRunAgain).toContain(`KERP_DEFAULT_TENANT_ID=${TENANT_ID}\n`);
    expect(state.firstRunAgain).toContain("tenant: present");
    expect(state.seed.tenant_id).toBe(TENANT_ID);
  });

  test("auth: a wrong password shows the generic error and starts no session", async () => {
    await page.goto("/login");
    await signIn(ADMIN_EMAIL, `${ADMIN_PASSWORD}-wrong`);
    await expect(page.getByRole("alert")).toHaveText(t("auth.login.invalid"));
    await expect(page).toHaveURL(/\/login$/);
    const marker = await page.evaluate((key) => sessionStorage.getItem(key), SESSION_STORAGE_KEY);
    expect(marker).toBeNull();
    expect((await context.request.get("/api/v1/auth/me")).status()).toBe(401);
    const refresh = await context.request.post("/api/v1/auth/refresh", {
      headers: { Origin: new URL(BASE_URL).origin },
    });
    expect(refresh.status()).toBe(401);
  });

  test("auth: the System admin signs in and lands in the shell", async () => {
    const token = await captureLogin(page, () => signIn(ADMIN_EMAIL, ADMIN_PASSWORD));
    admin = { request: context.request, token };
    await expect(page).toHaveURL(`${BASE_URL}/`);
    const nav = page.getByRole("navigation", { name: t("nav.label"), exact: true });
    for (const group of NAV_GROUP_IDS) {
      await expect(nav.getByRole("link", { name: t(`nav.groups.${group}.title`) })).toBeVisible();
    }
    await expect(page.getByText(t("auth.signedInAs", { name: ADMIN_NAME }), { exact: true }))
      .toBeVisible();
    const me = await json<{ email: string }>(admin, "GET", "/api/v1/auth/me", 200);
    expect(me.email).toBe(ADMIN_EMAIL);
  });

  test("project created (UI): Backbone → Projects → new project", async () => {
    const nav = page.getByRole("navigation", { name: t("nav.label"), exact: true });
    await nav.getByRole("link", { name: t("nav.groups.backbone.title") }).click();
    await nav.getByRole("link", { name: t("nav.items.projects"), exact: true }).click();
    await expect(page.getByRole("heading", { name: t("admin.projects.title") })).toBeVisible();
    await page.getByRole("button", { name: t("admin.projects.new"), exact: true }).click();
    const dialog = page.getByRole("dialog", { name: t("admin.projects.newTitle") });
    await dialog.getByLabel(t("admin.projects.code"), { exact: true }).fill(projectCode);
    await dialog.getByLabel(t("admin.projects.name"), { exact: true }).fill(projectName);
    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/v1/projects" && r.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: t("admin.projects.create"), exact: true }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    project = (await response.json()) as Project;
    await expect(status(t("admin.projects.created"))).toBeVisible();
    await expect(listItem(projectLabel)).toBeVisible();

    const read = await json<Project>(admin, "GET", `/api/v1/projects/${project.id}`, 200);
    expect(read).toMatchObject({ code: projectCode, name: projectName });
    const members = await json<Member[]>(
      admin, "GET", `/api/v1/admin/projects/${project.id}/members`, 200,
    );
    expect(members.map((m) => [m.email, m.grants.map((g) => g.persona)])).toEqual([
      [ADMIN_EMAIL, ["PER-09"]],
    ]);

    await listItem(projectLabel)
      .getByRole("link", { name: t("admin.projects.members"), exact: true })
      .click();
    await expect(page).toHaveURL(`${BASE_URL}/admin/projects/${project.id}/members`);
    const row = listItem(requester(ADMIN_NAME, ADMIN_EMAIL));
    await expect(row.getByText(persona("PER-09"), { exact: true })).toBeVisible();
  });

  test("persona assigned (UI): this run's QA user gets PER-06 on the new project", async () => {
    // The seed's QA user is new for this run: a member of nothing before this step.
    await expect(listItem(requester(qaName, state.seed.qa_email))).toHaveCount(0);
    await page.getByLabel(t("admin.members.addEmail"), { exact: true }).fill(state.seed.qa_email);
    await page.getByRole("button", { name: t("admin.members.find"), exact: true }).click();
    const dialog = page.getByRole("dialog", { name: t("admin.members.addTitle", { name: qaName }) });
    await dialog.getByRole("checkbox", { name: persona("PER-06"), exact: true }).check();
    await dialog.getByRole("button", { name: t("admin.members.save"), exact: true }).click();
    await expect(status(t("admin.members.saved"))).toBeVisible();
    const row = listItem(requester(qaName, state.seed.qa_email));
    await expect(row.getByText(persona("PER-06"), { exact: true })).toBeVisible();
  });

  test("module toggle (API): states listed, platform module and unknown module refused", async () => {
    const path = `/api/v1/projects/${project.id}/modules`;
    modules = await json<ModuleState[]>(admin, "GET", path, 200);
    expect(modules.length).toBeGreaterThan(0);
    for (const m of modules) {
      expect(typeof m.toggleable).toBe("boolean");
      expect(typeof m.enabled).toBe("boolean");
    }
    expect(modules.find((m) => m.name === "validation")).toMatchObject({
      toggleable: false,
      enabled: true,
    });
    const off = await call(admin, "PUT", `${path}/validation`, { enabled: false });
    expect(off.status()).toBe(409);
    const after = await json<ModuleState[]>(admin, "GET", path, 200);
    expect(after.find((m) => m.name === "validation")?.enabled).toBe(true);
    const unknown = await call(admin, "PUT", `${path}/no_such_module`, { enabled: false });
    expect(unknown.status()).toBe(404);
  });

  test("module toggle (API): a toggleable module switches off and on", async () => {
    const module = modules.find((m) => m.toggleable);
    test.skip(
      module === undefined,
      "TD-498: no shipped module is toggleable yet (decision TD-492); unskip when the first one merges",
    );
    if (module === undefined) return;
    const path = `/api/v1/projects/${project.id}/modules/${module.name}`;
    // One of the module's own project routes: a GET without further path parameters.
    const spec = (await (await admin.request.get(`${API_URL}/openapi.json`)).json()) as {
      paths: Record<string, Record<string, unknown>>;
    };
    const prefix = `/api/v1/${module.name.replaceAll("_", "-")}/projects/{project_id}`;
    const route = Object.keys(spec.paths).find(
      (p) => p.startsWith(prefix) && "get" in (spec.paths[p] ?? {}) &&
        (p.match(/\{/g) ?? []).length === 1,
    );
    expect(route, `a GET route of ${module.name} under ${prefix}`).toBeDefined();
    const url = (route ?? "").replace("{project_id}", project.id);
    expect(await json<ModuleState>(admin, "PUT", path, 200, { enabled: false })).toMatchObject({
      enabled: false,
    });
    expect((await call(admin, "GET", url)).status()).toBe(404);
    expect(await json<ModuleState>(admin, "PUT", path, 200, { enabled: true })).toMatchObject({
      enabled: true,
    });
    expect((await call(admin, "GET", url)).status()).not.toBe(404);
  });

  test("scoped (UI + API): the QA user sees exactly the new project", async () => {
    await page.getByRole("button", { name: t("auth.logout"), exact: true }).click();
    await expect(page.getByRole("heading", { name: t("auth.login.title"), exact: true }))
      .toBeVisible();
    const token = await captureLogin(page, () => signIn(state.seed.qa_email, QA_PASSWORD));
    qa = { request: context.request, token };
    await expect(page.getByText(t("auth.signedInAs", { name: qaName }), { exact: true }))
      .toBeVisible();

    const picker = page.getByRole("combobox", { name: t("scope.label"), exact: true });
    await expect(picker).toBeVisible();
    const values = await picker
      .locator("option")
      .evaluateAll((options) =>
        options.map((o) => (o as HTMLOptionElement).value).filter((v) => v !== ""),
      );
    expect(values).toEqual([project.id]);
    await picker.selectOption(project.id);
    await expect(picker).toHaveValue(project.id);
    await expect(picker.locator("option:checked")).toHaveText(projectLabel);

    await json<Project>(qa, "GET", `/api/v1/projects/${project.id}`, 200);
    expect(state.seed.demo_project_id).not.toBeNull();
    const demo = await call(qa, "GET", `/api/v1/projects/${state.seed.demo_project_id ?? ""}`);
    expect(demo.status()).toBe(404);
  });

  test("rule set run (API): the copied set, a rule saved by PER-06, Try it", async () => {
    const base = `/api/v1/validation/projects/${project.id}/rule-sets`;
    const sets = await json<RuleSet[]>(qa, "GET", base, 200);
    const smoke = sets.find((s) => s.key === state.seed.rule_set_key);
    expect(smoke).toMatchObject({ kind: "check", layer: "project", module: null, rules: [] });
    const rules = `${base}/${smoke?.id ?? ""}/rules`;

    const saved = await json<RuleSet>(qa, "PUT", `${rules}/qty_positive`, 200, QTY_RULE);
    expect(saved.rules.map((r) => [r.rule_key, r.expression, r.version])).toEqual([
      ["qty_positive", QTY_RULE.expression, 1],
    ]);

    const broken = await call(qa, "PUT", `${rules}/qty_positive`, {
      ...QTY_RULE,
      expression: "fields.qty >",
    });
    expect(broken.status()).toBe(422);
    const unchanged = (await json<RuleSet[]>(qa, "GET", base, 200)).find((s) => s.id === smoke?.id);
    expect(unchanged?.rules.map((r) => [r.rule_key, r.expression, r.version])).toEqual([
      ["qty_positive", QTY_RULE.expression, 1],
    ]);

    const pass = await json<TryResult>(qa, "POST", `${rules}/qty_positive/try`, 200, {
      context: { fields: { qty: 1 } },
    });
    expect(pass.outcome).toBe("pass");
    expect(pass.finding).toBeNull();
    const fail = await json<TryResult>(qa, "POST", `${rules}/qty_positive/try`, 200, {
      context: { fields: { qty: 0 } },
    });
    expect(fail.outcome).toBe("fail");
    expect(fail.finding).toMatchObject({
      rule_key: "qty_positive",
      outcome: "fail",
      severity: "error",
      action: "block",
    });
  });
});
