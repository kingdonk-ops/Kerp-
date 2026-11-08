import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { i18n } from "../i18n";
import { AppRoot } from "./AppRoot";

describe("App smoke test", () => {
  it("renders the home route inside all providers", async () => {
    render(<AppRoot initialEntries={["/"]} />);

    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("home.title") }),
    ).toBeInTheDocument();
    expect(await screen.findByText(i18n.t("home.queryStatus.ready"))).toBeInTheDocument();
    expect(document.title).toBe(i18n.t("app.name"));
  });

  it("renders the not-found route for unknown paths", async () => {
    render(<AppRoot initialEntries={["/does-not-exist"]} />);

    expect(
      await screen.findByRole("heading", { level: 1, name: i18n.t("notFound.title") }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: i18n.t("notFound.backHome") })).toHaveAttribute(
      "href",
      "/",
    );
  });
});
