// Admin → Access requests (T-0051, diff §4.1, §6.1). Synthetic data only.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { json } from "../test/fakeApi";
import type { PendingRequest } from "./api";
import { ADMIN_PROJECT, renderAdmin } from "./testing";
import { formatAge } from "./ui";

const PATH = "/admin/access-requests";
const HOUR = 3_600_000;
const REQ: PendingRequest = {
  id: "00000000-0000-4000-8000-000000000201",
  project_id: ADMIN_PROJECT.id,
  project_code: ADMIN_PROJECT.code,
  project_name: ADMIN_PROJECT.name,
  requester: {
    id: "00000000-0000-4000-8000-000000000301",
    email: "fitter@example.test",
    display_name: "Synthetic Fitter",
  },
  message: "Joining the shutdown crew.",
  created_at: new Date(Date.now() - 50 * HOUR).toISOString(),
};
const DECIDE = `/api/v1/access-requests/${REQ.id}`;

function pendingOnce(...lists: PendingRequest[][]) {
  let call = 0;
  return () => json(lists[Math.min(call++, lists.length - 1)]);
}

// Wait on the cheap text query first: role queries over the whole shell are slow under load.
const card = async () => {
  await screen.findByText(REQ.message ?? "");
  return screen.getByRole("listitem", { name: REQ.requester.display_name });
};
const button = (name: string) => screen.getByRole("button", { name });
const dialog = () => screen.getByRole("dialog");

describe("access-request inbox", () => {
  it("lists pending requests with requester, e-mail, project, message and age", async () => {
    renderAdmin(PATH, { "GET /api/v1/access-requests/pending": () => json([REQ]) });

    const item = await card();
    expect(item).toHaveTextContent(
      i18n.t("admin.accessRequests.requester", { name: "Synthetic Fitter", email: REQ.requester.email }),
    );
    expect(item).toHaveTextContent("SYN-001 — Synthetic Refinery Unit");
    expect(item).toHaveTextContent("Joining the shutdown crew.");
    expect(item).toHaveTextContent(
      i18n.t("admin.accessRequests.requested", { age: formatAge(REQ.created_at, "en") }),
    );
    expect(formatAge(REQ.created_at, "en")).toBe("2 days ago");
  });

  it("tells a user who administers nothing so (403)", async () => {
    renderAdmin(PATH, {
      "GET /api/v1/access-requests/pending": () => json({ detail: "Forbidden" }, 403),
    });
    expect(await screen.findByText(i18n.t("admin.forbidden"))).toBeInTheDocument();
  });

  it("approves with personas and a site for PER-07; the request leaves the list", async () => {
    const calls = renderAdmin(PATH, {
      "GET /api/v1/access-requests/pending": pendingOnce([REQ], []),
      [`POST ${DECIDE}/approve`]: () => json({ status: "approved" }),
    });
    fireEvent.click(within(await card()).getByRole("button", { name: i18n.t("admin.accessRequests.approve") }));
    const submit = within(dialog()).getByRole("button", { name: i18n.t("admin.accessRequests.approve") });
    expect(submit).toBeDisabled();

    fireEvent.click(screen.getByRole("checkbox", { name: /PER-14/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /PER-07/ }));
    expect(submit).toBeDisabled(); // PER-07 names its site
    fireEvent.change(screen.getByLabelText(i18n.t("admin.accessRequests.site")), {
      target: { value: "00000000-0000-4000-8000-000000000401" },
    });
    fireEvent.click(submit);

    expect(await screen.findByText(i18n.t("admin.accessRequests.approved"))).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(await screen.findByText(i18n.t("admin.accessRequests.empty"))).toBeInTheDocument();
    expect(calls.find((c) => c.path === `${DECIDE}/approve`)?.body).toEqual({
      grants: [
        { persona: "PER-07", site_node_id: "00000000-0000-4000-8000-000000000401" },
        { persona: "PER-14" },
      ],
      confirm_sod: false,
    });
  });

  it("shows the SoD warning and re-submits with confirm_sod only after confirmation", async () => {
    const calls = renderAdmin(PATH, {
      "GET /api/v1/access-requests/pending": pendingOnce([REQ], []),
      [`POST ${DECIDE}/approve`]: (req) =>
        (req.body as { confirm_sod: boolean }).confirm_sod
          ? json({ status: "approved", sod_warning: true })
          : json({ detail: { code: "sod_confirmation_required", sod_warning: true } }, 409),
    });
    fireEvent.click(within(await card()).getByRole("button", { name: i18n.t("admin.accessRequests.approve") }));
    fireEvent.click(screen.getByRole("checkbox", { name: /PER-09/ }));
    fireEvent.click(screen.getByRole("checkbox", { name: /PER-02/ }));
    fireEvent.click(within(dialog()).getByRole("button", { name: i18n.t("admin.accessRequests.approve") }));

    expect(await within(dialog()).findByRole("alert")).toHaveTextContent(
      i18n.t("admin.accessRequests.sodWarning"),
    );
    const approves = () => calls.filter((c) => c.path === `${DECIDE}/approve`);
    expect(approves()).toHaveLength(1);
    fireEvent.click(button(i18n.t("admin.accessRequests.confirmSod")));

    expect(await screen.findByText(i18n.t("admin.accessRequests.approved"))).toBeInTheDocument();
    expect(approves().map((c) => (c.body as { confirm_sod: boolean }).confirm_sod)).toEqual([
      false,
      true,
    ]);
  });

  it("rejects with an optional reason the requester will see (≤ 500, with a counter)", async () => {
    const calls = renderAdmin(PATH, {
      "GET /api/v1/access-requests/pending": pendingOnce([REQ], []),
      [`POST ${DECIDE}/reject`]: () => json({ status: "rejected" }),
    });
    fireEvent.click(within(await card()).getByRole("button", { name: i18n.t("admin.accessRequests.reject") }));
    expect(dialog()).toHaveTextContent(i18n.t("admin.accessRequests.reasonHint"));
    const reason = screen.getByLabelText(i18n.t("admin.accessRequests.reason"));
    expect(reason).toHaveAttribute("maxLength", "500");
    fireEvent.change(reason, { target: { value: "Crew is complete." } });
    expect(dialog()).toHaveTextContent(i18n.t("admin.accessRequests.reasonCount", { count: 17 }));
    fireEvent.click(within(dialog()).getByRole("button", { name: i18n.t("admin.accessRequests.reject") }));

    expect(await screen.findByText(i18n.t("admin.accessRequests.rejected"))).toBeInTheDocument();
    expect(calls.find((c) => c.path === `${DECIDE}/reject`)?.body).toEqual({
      reason: "Crew is complete.",
    });
    expect(await screen.findByText(i18n.t("admin.accessRequests.empty"))).toBeInTheDocument();
  });

  it("shows a neutral message and refreshes the list on 409 (already decided)", async () => {
    const calls = renderAdmin(PATH, {
      "GET /api/v1/access-requests/pending": pendingOnce([REQ], []),
      [`POST ${DECIDE}/reject`]: () => json({ detail: "not pending" }, 409),
    });
    fireEvent.click(within(await card()).getByRole("button", { name: i18n.t("admin.accessRequests.reject") }));
    fireEvent.click(within(dialog()).getByRole("button", { name: i18n.t("admin.accessRequests.reject") }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      i18n.t("admin.accessRequests.alreadyDecided"),
    );
    expect(await screen.findByText(i18n.t("admin.accessRequests.empty"))).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/api/v1/access-requests/pending")).toHaveLength(2);
    expect(calls.find((c) => c.path === `${DECIDE}/reject`)?.body).toEqual({ reason: null });
  });
});

describe("Admin nav items (diff §6.1)", () => {
  const nav = () => screen.findByRole("navigation", { name: i18n.t("nav.label") });
  const link = (name: string) => within(screen.getByRole("navigation")).queryByRole("link", { name });

  it("are shown to a PER-09 holder", async () => {
    renderAdmin("/admin");
    await nav();
    await waitFor(() => {
      expect(link(i18n.t("nav.items.accessRequests"))).toHaveAttribute("href", PATH);
    });
    expect(link(i18n.t("nav.items.system"))).toHaveAttribute("href", "/admin/system/devices");
  });

  it("are shown to a System admin without projects", async () => {
    renderAdmin("/admin", {}, { projects: [], systemAdmin: true });
    await nav();
    await waitFor(() => {
      expect(link(i18n.t("nav.items.accessRequests"))).toBeInTheDocument();
    });
  });

  it("are hidden from everyone else", async () => {
    const calls = renderAdmin("/admin", {}, { projects: [{ ...ADMIN_PROJECT, personas: ["PER-07"] }] });
    await nav();
    await waitFor(() => {
      expect(calls.some((c) => c.path === "/api/v1/admin/grants")).toBe(true);
    });
    expect(link(i18n.t("nav.items.accessRequests"))).not.toBeInTheDocument();
    expect(link(i18n.t("nav.items.system"))).not.toBeInTheDocument();
  });
});
