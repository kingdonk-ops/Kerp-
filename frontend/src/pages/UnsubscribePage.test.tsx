// /unsubscribe#<token>: the broadcast opt-out page (T-0229, TD-601). Synthetic data only.
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { renderAt } from "../invite/testing";
import { json } from "../test/fakeApi";

const TOKEN = "syntheticOptOutToken_0123456789-abcdefABCDEF";
const PATH = "/api/v1/notifications/broadcasts/unsubscribe";

describe("unsubscribe page", () => {
  it("reads the token from the fragment, removes it, waits for the click and posts it in the body", async () => {
    const { calls, router } = renderAt(`/unsubscribe#${TOKEN}`, { [`POST ${PATH}`]: () => json({ status: "ok" }) });
    await screen.findByRole("heading", { name: i18n.t("unsubscribe.title") });
    expect(router.state.location.hash).toBe("");
    expect(router.state.location.pathname).toBe("/unsubscribe");
    // Nothing on load: link scanners prefetch.
    expect(calls.some((c) => c.path === PATH)).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: i18n.t("unsubscribe.confirm") }));
    expect(await screen.findByRole("status")).toHaveTextContent(i18n.t("unsubscribe.done"));
    const call = calls.find((c) => c.path === PATH);
    expect(call?.body).toEqual({ token: TOKEN });
    expect(call?.query).toBe("");
  });

  it("works signed in too, and shows the same neutral answer", async () => {
    renderAt(`/unsubscribe#${TOKEN}`, { [`POST ${PATH}`]: () => json({ status: "ok" }) }, { signedIn: true });
    fireEvent.click(await screen.findByRole("button", { name: i18n.t("unsubscribe.confirm") }));
    expect(await screen.findByRole("status")).toHaveTextContent(i18n.t("unsubscribe.done"));
  });

  it("says the link is incomplete without a token and calls nothing", async () => {
    const { calls } = renderAt("/unsubscribe");
    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("unsubscribe.linkInvalid"));
    expect(screen.queryByRole("button", { name: i18n.t("unsubscribe.confirm") })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path === PATH)).toBe(false);
  });

  it("offers a retry when the server is unavailable", async () => {
    renderAt(`/unsubscribe#${TOKEN}`, { [`POST ${PATH}`]: () => json({ detail: "Unavailable" }, 503) });
    fireEvent.click(await screen.findByRole("button", { name: i18n.t("unsubscribe.confirm") }));
    expect(await screen.findByRole("alert")).toHaveTextContent(i18n.t("unsubscribe.failed"));
    expect(screen.getByRole("button", { name: i18n.t("unsubscribe.confirm") })).toBeEnabled();
  });
});
