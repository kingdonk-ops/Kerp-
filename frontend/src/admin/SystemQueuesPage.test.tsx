// Admin → System → Queues (T-0224, diff §6.5). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { i18n } from "../i18n";
import { json } from "../test/fakeApi";
import { PAGE_SIZE, type FailedJob } from "./system";
import { FAILED_JOB, FAILED_TENANT_JOB, QUEUES } from "./systemFixtures";
import { renderAdmin } from "./testing";

vi.setConfig({ testTimeout: 20_000 });
const WAIT = { timeout: 5000 };

const PATH = "/admin/system/queues";
const RETRY = `/api/v1/admin/system/queues/failed/${FAILED_JOB.id}/retry`;
const ABORT_ID = "00000000-0000-4000-8000-00000000e001";
const ABORT = `/api/v1/admin/system/queues/${ABORT_ID}/abort`;
const handlers = (extra = {}) => ({
  "GET /api/v1/admin/system/queues": () => json(QUEUES),
  "GET /api/v1/admin/system/queues/failed": () => json([FAILED_JOB, FAILED_TENANT_JOB]),
  ...extra,
});
const SYS = { projects: [], systemAdmin: true };
const region = (name: string) => within(screen.getByRole("region", { name }));
const loaded = () => screen.findByText(FAILED_JOB.kind, {}, WAIT);
const failedRow = (kind: string) =>
  within(region(i18n.t("admin.queues.failedTitle")).getByRole("listitem", { name: kind }));

describe("queue monitor", () => {
  it("shows the counts of each queue", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const interactive = region(i18n.t("admin.queues.queueNames.interactive"));
    expect(interactive.getByText(i18n.t("admin.queues.queued", { count: 3 }))).toBeInTheDocument();
    expect(interactive.getByText(i18n.t("admin.queues.activeCount", { count: 1 }))).toBeInTheDocument();
    expect(interactive.getByText(i18n.t("admin.queues.failed", { count: 1 }))).toBeInTheDocument();
    expect(
      interactive.getByText(
        i18n.t("admin.queues.oldest", { age: i18n.t("admin.queues.seconds", { value: "12.5" }) }),
      ),
    ).toBeInTheDocument();
    const batch = region(i18n.t("admin.queues.queueNames.batch"));
    expect(batch.getByText(i18n.t("admin.queues.scheduled", { count: 4 }))).toBeInTheDocument();
    expect(batch.queryByText(/Oldest/)).not.toBeInTheDocument();
  });

  it("lists the cron jobs with their last outcome", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const cron = region(i18n.t("admin.queues.cronTitle"));
    const row = within(cron.getByRole("listitem", { name: "uploads.sweep_orphans" }));
    expect(row.getByText("*/15 * * * *")).toBeInTheDocument();
    expect(row.getByText(i18n.t("admin.queues.outcomes.succeeded"))).toBeInTheDocument();
    expect(cron.getByText("jobs.sweep")).toBeInTheDocument();
  });

  it("lists failed jobs without payloads, deployment jobs marked", async () => {
    renderAdmin(PATH, handlers(), SYS);
    await loaded();
    const row = failedRow(FAILED_JOB.kind);
    expect(row.getByText("SYN-A")).toBeInTheDocument();
    expect(row.getByText("TimeoutError")).toBeInTheDocument();
    expect(row.getByText("Synthetic failure message")).toBeInTheDocument();
    expect(failedRow(FAILED_TENANT_JOB.kind).getByText(i18n.t("admin.queues.tenantLevel"))).toBeInTheDocument();
  });

  it("retries a failed job only after confirmation", async () => {
    const calls = renderAdmin(
      PATH,
      handlers({ [`POST ${RETRY}`]: () => json({ id: FAILED_JOB.id, kind: FAILED_JOB.kind, status: "queued" }, 202) }),
      SYS,
    );
    await loaded();
    fireEvent.click(failedRow(FAILED_JOB.kind).getByRole("button", { name: i18n.t("admin.queues.retry") }));
    const dialog = within(
      screen.getByRole("dialog", { name: i18n.t("admin.queues.retryTitle", { kind: FAILED_JOB.kind }) }),
    );
    expect(calls.some((c) => c.path === RETRY)).toBe(false);
    fireEvent.click(dialog.getByRole("button", { name: i18n.t("admin.queues.retry") }));
    expect(await screen.findByText(i18n.t("admin.queues.retried"), {}, WAIT)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST" && c.path === RETRY)).toHaveLength(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("cancels a retry without calling the API", async () => {
    const calls = renderAdmin(PATH, handlers(), SYS);
    await loaded();
    fireEvent.click(failedRow(FAILED_JOB.kind).getByRole("button", { name: i18n.t("admin.queues.retry") }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: i18n.t("admin.cancel") }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("explains a retry of a job that is no longer failed", async () => {
    renderAdmin(
      PATH,
      handlers({ [`POST ${RETRY}`]: () => json({ detail: { code: "job_state", message: "x" } }, 409) }),
      SYS,
    );
    await loaded();
    fireEvent.click(failedRow(FAILED_JOB.kind).getByRole("button", { name: i18n.t("admin.queues.retry") }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: i18n.t("admin.queues.retry") }));
    expect(await screen.findByText(i18n.t("admin.queues.notFailed"), {}, WAIT)).toBeInTheDocument();
  });

  it("aborts a job by id after confirmation and checks the id first", async () => {
    let status = 200;
    const calls = renderAdmin(
      PATH,
      handlers({ [`POST ${ABORT}`]: () => json({ id: ABORT_ID, kind: "uploads.verify", status: "failed" }, status) }),
      SYS,
    );
    await loaded();
    const abort = region(i18n.t("admin.queues.abortTitle"));
    const input = abort.getByLabelText(i18n.t("admin.queues.jobId"));
    fireEvent.change(input, { target: { value: "not-a-uuid" } });
    fireEvent.click(abort.getByRole("button", { name: i18n.t("admin.queues.abort") }));
    expect(abort.getByText(i18n.t("admin.queues.jobIdInvalid"))).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.change(input, { target: { value: ABORT_ID } });
    fireEvent.click(abort.getByRole("button", { name: i18n.t("admin.queues.abort") }));
    const dialog = within(screen.getByRole("dialog", { name: i18n.t("admin.queues.abortConfirmTitle", { id: ABORT_ID }) }));
    fireEvent.click(dialog.getByRole("button", { name: i18n.t("admin.queues.abort") }));
    expect(await screen.findByText(i18n.t("admin.queues.aborted"), {}, WAIT)).toBeInTheDocument();
    expect(calls.filter((c) => c.path === ABORT)).toHaveLength(1);

    status = 409;
    fireEvent.change(input, { target: { value: ABORT_ID } });
    fireEvent.click(abort.getByRole("button", { name: i18n.t("admin.queues.abort") }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: i18n.t("admin.queues.abort") }));
    expect(await screen.findByText(i18n.t("admin.queues.notAbortable"), {}, WAIT)).toBeInTheDocument();
  });

  it("explains an abort of an unknown job", async () => {
    renderAdmin(PATH, handlers({ [`POST ${ABORT}`]: () => json({ detail: "Not Found" }, 404) }), SYS);
    await loaded();
    const abort = region(i18n.t("admin.queues.abortTitle"));
    fireEvent.change(abort.getByLabelText(i18n.t("admin.queues.jobId")), { target: { value: ABORT_ID } });
    fireEvent.click(abort.getByRole("button", { name: i18n.t("admin.queues.abort") }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: i18n.t("admin.queues.abort") }));
    expect(await screen.findByText(i18n.t("admin.queues.unknownJob"), {}, WAIT)).toBeInTheDocument();
  });

  it("pages the failed jobs", async () => {
    const page: FailedJob[] = Array.from({ length: PAGE_SIZE }, (_, i) => ({
      ...FAILED_JOB,
      id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
    }));
    const calls = renderAdmin(
      PATH,
      handlers({
        "GET /api/v1/admin/system/queues/failed": (req: { query: string }) =>
          json(req.query.includes("offset=0") ? page : [FAILED_TENANT_JOB]),
      }),
      SYS,
    );
    expect(await screen.findAllByText(FAILED_JOB.kind, {}, WAIT)).toHaveLength(PAGE_SIZE);
    fireEvent.click(screen.getByRole("button", { name: i18n.t("admin.system.next") }));
    expect(await screen.findByText(FAILED_TENANT_JOB.kind, {}, WAIT)).toBeInTheDocument();
    await waitFor(() => { expect(calls.some((c) => c.path.endsWith("/queues/failed") && c.query.includes(`offset=${String(PAGE_SIZE)}`))).toBe(
        true,
      ); },
    );
  });

  it("refuses everyone else without calling the API", async () => {
    const calls = renderAdmin(PATH, handlers());
    expect(await screen.findByText(i18n.t("admin.system.forbiddenConsole"), {}, WAIT)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: i18n.t("admin.system.queuesTab") })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path.startsWith("/api/v1/admin/system/"))).toBe(false);
  });

  it("explains a 403 from the server", async () => {
    renderAdmin(PATH, handlers({ "GET /api/v1/admin/system/queues": () => json({ detail: "Forbidden" }, 403) }), SYS);
    expect(await screen.findByText(i18n.t("admin.system.forbiddenConsole"), {}, WAIT)).toBeInTheDocument();
  });
});
