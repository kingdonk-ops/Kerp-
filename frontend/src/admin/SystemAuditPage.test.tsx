// Admin → System → Audit log (T-0225, diff §4.7, §6.5). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json, type Recorded } from "../test/fakeApi";
import { auditQuery, invalidFields, NO_AUDIT_FILTER } from "./audit";
import { ACTOR, DEVICE_ID, PROJECT_A, PROJECT_ENTRY, TENANT_ENTRY } from "./systemFixtures";
import { renderAdmin } from "./testing";

vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/admin/system/audit";
const LIST = "/api/v1/admin/system/audit";
const JOB = "00000000-0000-4000-8000-00000000d777";
const SYS = { projects: [], systemAdmin: true };
const handlers = (extra = {}) => ({
  [`GET ${LIST}`]: () => json({ items: [PROJECT_ENTRY, TENANT_ENTRY], next_cursor: null }),
  ...extra,
});
const region = (name: string) => within(screen.getByRole("region", { name }));
const entry = (name: string) =>
  within(region(i18n.t("admin.audit.entriesTitle")).getByRole("listitem", { name }));
const loaded = () => screen.findByRole("listitem", { name: `${PROJECT_ENTRY.chain_key} #4` }, WAIT);
const lists = (calls: Recorded[]) => calls.filter((c) => c.path === LIST);
const job = (status: string, extra = {}) => ({
  id: JOB,
  kind: "admin.audit_verify_all",
  queue: "batch",
  status,
  progress: 0,
  message: null,
  result: null,
  error: null,
  attempts: 1,
  created_at: "2026-11-21T08:00:00Z",
  started_at: null,
  finished_at: null,
  ...extra,
});

describe("audit log", () => {
  it("lists entries with chain, actor, device and event; details show hashes and payload", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const row = entry(`${PROJECT_ENTRY.chain_key} #4`);
    expect(row.getByText(ACTOR)).toBeInTheDocument();
    expect(row.getByText(DEVICE_ID)).toBeInTheDocument();
    expect(row.getByText("inspection_signed")).toBeInTheDocument();
    expect(row.getByText(PROJECT_A)).toBeInTheDocument();
    fireEvent.click(row.getByText(i18n.t("admin.audit.details")));
    expect(row.getByText("b".repeat(64))).toBeVisible();
    expect(row.getByText("a".repeat(64))).toBeVisible();
    expect(row.getByText(/"sod_warning": true/)).toBeVisible();
    const tenant = entry(`${TENANT_ENTRY.chain_key} #9`);
    expect(tenant.getByText(i18n.t("admin.audit.tenantLevel"))).toBeInTheDocument();
  });

  it("filters by actor, device, chain prefix, event, flag and dates on the server", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const filters = region(i18n.t("admin.audit.filters"));
    type FilterKey = "actor" | "device" | "chainPrefix" | "event" | "flag" | "from" | "to";
    const type = (field: FilterKey, value: string) => {
      fireEvent.change(filters.getByLabelText(i18n.t(`admin.audit.${field}`)), { target: { value } });
    };
    type("actor", ACTOR);
    type("device", DEVICE_ID);
    type("chainPrefix", "inspections:");
    type("event", "inspection_signed");
    type("flag", "sod_warning");
    type("from", "2026-11-01");
    type("to", "2026-11-21");
    expect(lists(calls)).toHaveLength(1);
    fireEvent.click(filters.getByRole("button", { name: i18n.t("admin.audit.apply") }));
    await waitFor(() => { expect(lists(calls)).toHaveLength(2); }, WAIT);
    const query = new URLSearchParams(lists(calls)[1]?.query);
    expect(query.get("actor_id")).toBe(ACTOR);
    expect(query.get("device_id")).toBe(DEVICE_ID);
    expect(query.get("chain_key_prefix")).toBe("inspections:");
    expect(query.get("event")).toBe("inspection_signed");
    expect(query.get("flag")).toBe("sod_warning");
    expect(query.get("created_from")).toBe(new Date("2026-11-01T00:00:00").toISOString());
    // The "to" day is included: the exclusive bound is the next local midnight.
    expect(query.get("created_to")).toBe(new Date("2026-11-22T00:00:00").toISOString());
  });

  it("refuses filters the server would reject, without calling it", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const filters = region(i18n.t("admin.audit.filters"));
    fireEvent.change(filters.getByLabelText(i18n.t("admin.audit.actor")), { target: { value: "not-a-uuid" } });
    expect(filters.getByRole("button", { name: i18n.t("admin.audit.apply") })).toBeDisabled();
    expect(filters.getByText(i18n.t("admin.audit.invalid"))).toBeInTheDocument();
    expect(filters.getByLabelText(i18n.t("admin.audit.actor"))).toHaveAttribute("aria-invalid", "true");
    expect(lists(calls)).toHaveLength(1);
  });

  it("pages with the cursor", async () => {
    const calls = renderAdmin(
      PATH,
      handlers({
        [`GET ${LIST}`]: (req: Recorded) =>
          req.query.includes("cursor=c1")
            ? json({ items: [TENANT_ENTRY], next_cursor: null })
            : json({ items: [PROJECT_ENTRY], next_cursor: "c1" }),
      }),
      SYS,
    );
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.system.next") }));
    await screen.findByRole("listitem", { name: `${TENANT_ENTRY.chain_key} #9` }, WAIT);
    expect(lists(calls).at(-1)?.query).toContain("cursor=c1");
    expect(screen.getByRole("button", { name: i18n.t("admin.system.next") })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.system.previous") }));
    await loaded();
  });

  it("verifies one chain with an inline result, intact or broken", async () => {
    let broken = false;
    const calls = renderAdmin(
      PATH,
      handlers({
        "POST /api/v1/admin/system/audit/verify": () =>
          json({
            chain_key: PROJECT_ENTRY.chain_key,
            project_id: PROJECT_A,
            ok: !broken,
            length: 12,
            head_hash: "e".repeat(64),
            first_broken_seq: broken ? 3 : null,
            problem_count: broken ? 2 : 0,
            problems: broken ? ["seq 3: content_hash mismatch", "seq 4: prev_hash mismatch"] : [],
          }),
      }),
      SYS,
    );
    await loaded();
    const row = entry(`${PROJECT_ENTRY.chain_key} #4`);
    fireEvent.click(row.getByRole("button", { name: i18n.t("admin.audit.verify") }));
    expect(
      await row.findByText(i18n.t("admin.audit.chainOk", { length: 12, head: "e".repeat(64) }), {}, WAIT),
    ).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/audit/verify"))?.body).toEqual({
      chain_key: PROJECT_ENTRY.chain_key,
      project_id: PROJECT_A,
    });
    broken = true;
    fireEvent.click(row.getByRole("button", { name: i18n.t("admin.audit.verify") }));
    expect(await row.findByText(i18n.t("admin.audit.chainBroken", { seq: 3, problems: 2 }), {}, WAIT)).toBeInTheDocument();
    expect(row.getByText("seq 3: content_hash mismatch")).toBeInTheDocument();
  });

  it("says when a chain no longer exists (404)", async () => {
    renderAdmin(PATH, handlers({ "POST /api/v1/admin/system/audit/verify": () => json({ detail: "Not found" }, 404) }), SYS);
    await loaded();
    const row = entry(`${TENANT_ENTRY.chain_key} #9`);
    fireEvent.click(row.getByRole("button", { name: i18n.t("admin.audit.verify") }));
    expect(await row.findByText(i18n.t("admin.audit.chainGone"), {}, WAIT)).toBeInTheDocument();
  });

  it("verifies all chains as a job with progress and a result summary", async () => {
    let polls = 0;
    const calls = renderAdmin(
      PATH,
      handlers({
        "POST /api/v1/admin/system/audit/verify-all": () =>
          json({ id: JOB, kind: "admin.audit_verify_all", status: "queued" }, 202),
        [`GET /api/v1/jobs/${JOB}`]: () => {
          polls += 1;
          return polls === 1
            ? json(job("running", { progress: 40 }))
            : json(
                job("succeeded", {
                  progress: 100,
                  result: {
                    ok: false,
                    chains: 7,
                    entries: 120,
                    broken_chains: 1,
                    first_broken: { project_id: PROJECT_A, chain_key: "inspections:synthetic-001", seq: 3 },
                  },
                }),
              );
        },
      }),
      SYS,
    );
    await loaded();
    const all = region(i18n.t("admin.audit.verifyAllTitle"));
    fireEvent.click(all.getByRole("button", { name: i18n.t("admin.audit.verifyAll") }));
    expect(await all.findByText(i18n.t("admin.audit.jobStatus.running", { progress: 40 }), {}, WAIT)).toBeInTheDocument();
    expect(all.getByRole("progressbar", { name: i18n.t("admin.audit.progress") })).toHaveAttribute("value", "40");
    expect(
      await all.findByText(
        i18n.t("admin.audit.allBroken", { broken: 1, chains: 7, entries: 120, chain: "inspections:synthetic-001", seq: 3 }),
        {},
        { timeout: 8000 },
      ),
    ).toBeInTheDocument();
    // Wait past the poll interval: a finished job is not polled again.
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(calls.filter((c) => c.path === `/api/v1/jobs/${JOB}`)).toHaveLength(2);
  });

  it("stops polling when the job cannot be read", async () => {
    const calls = renderAdmin(
      PATH,
      handlers({
        "POST /api/v1/admin/system/audit/verify-all": () =>
          json({ id: JOB, kind: "admin.audit_verify_all", status: "queued" }, 202),
        [`GET /api/v1/jobs/${JOB}`]: () => json({ detail: "Not found" }, 404),
      }),
      SYS,
    );
    await loaded();
    const all = region(i18n.t("admin.audit.verifyAllTitle"));
    fireEvent.click(all.getByRole("button", { name: i18n.t("admin.audit.verifyAll") }));
    expect(await all.findByText(i18n.t("admin.actionError"), {}, WAIT)).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(calls.filter((c) => c.path === `/api/v1/jobs/${JOB}`)).toHaveLength(1);
  });

  it("is refused to anyone but a System admin, without calling the API", async () => {
    const calls = renderAdmin(PATH, handlers());
    expect(await screen.findByText(i18n.t("admin.system.forbiddenConsole"), {}, WAIT)).toBeInTheDocument();
    expect(lists(calls)).toHaveLength(0);
    expect(screen.queryByRole("link", { name: i18n.t("admin.system.auditTab") })).not.toBeInTheDocument();
  });

  it("says so when the server answers 403", async () => {
    renderAdmin(PATH, handlers({ [`GET ${LIST}`]: () => json({ detail: "Forbidden" }, 403) }), SYS);
    expect(await screen.findByText(i18n.t("admin.system.forbiddenConsole"), {}, WAIT)).toBeInTheDocument();
  });
});

describe("audit filter helpers", () => {
  it("sends only the set filters and checks ids and flags", () => {
    expect(auditQuery(NO_AUDIT_FILTER)).toEqual({});
    expect(auditQuery({ ...NO_AUDIT_FILTER, event: "  x  " })).toEqual({ event: "x" });
    expect(invalidFields({ ...NO_AUDIT_FILTER, actor: ACTOR, device: "1", flag: "9bad" })).toEqual(["device", "flag"]);
  });
});
