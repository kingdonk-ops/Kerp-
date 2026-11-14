import { render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { applyTheme } from "../theme/theme";
import { auditA11y, FIELD_VIEWPORTS, MIN_TARGET_PX, setViewport } from "./a11y";

// The harness itself must fail on WCAG 2.1 AA violations, not just report green.
describe("a11y harness", () => {
  afterEach(() => {
    applyTheme("standard");
  });

  it("reports low text contrast", async () => {
    await setViewport(FIELD_VIEWPORTS[0]);
    const { container } = render(
      <main>
        <p style={{ color: "#bbbbbb", background: "#ffffff" }}>Synthetic low-contrast text</p>
      </main>,
    );
    const rules = (await auditA11y(container, "standard")).map((f) => f.rule);
    expect(rules).toContain("color-contrast");
  });

  it("reports a target under 48 px and accepts the touch token", async () => {
    await setViewport(FIELD_VIEWPORTS[0]);
    const { container } = render(
      <main className="flex flex-col gap-touch-gap">
        <button type="button" data-testid="small" style={{ width: 24, height: 24 }}>
          A
        </button>
        <button type="button" data-testid="glove" className="min-h-touch min-w-touch">
          B
        </button>
      </main>,
    );
    const findings = await auditA11y(container, "standard");
    const axeTargets = findings.filter((f) => f.rule === "target-size").map((f) => f.target);
    expect(axeTargets).toEqual(['button[data-testid="small"]']);
    const gloveTargets = findings.filter((f) => f.rule === "glove-target").map((f) => f.target);
    expect(gloveTargets).toEqual(['button "A"']);
    const glove = container.querySelector('[data-testid="glove"]');
    expect(glove?.getBoundingClientRect().height).toBeGreaterThanOrEqual(48);
  });

  it("reports a small control that axe accepts for the free space around it", async () => {
    await setViewport(FIELD_VIEWPORTS[0]);
    const { container } = render(
      <main className="p-16">
        <input type="checkbox" aria-label="Synthetic option" />
      </main>,
    );
    const findings = await auditA11y(container, "standard");
    expect(findings.map((f) => f.rule)).toEqual(["glove-target"]);
  });

  it("swaps the semantic colour tokens in the outdoor theme", () => {
    const { getByText } = render(<p className="bg-surface text-muted">Synthetic muted text</p>);
    const standard = getComputedStyle(getByText("Synthetic muted text")).color;
    applyTheme("outdoor");
    const outdoor = getComputedStyle(getByText("Synthetic muted text")).color;
    expect(outdoor).not.toBe(standard);
    expect(outdoor).toBe("oklch(0.3 0 0)");
  });

  it("sizes the touch tokens at 48 px with 8 px spacing", () => {
    const { getByTestId } = render(
      <div className="flex gap-touch-gap">
        <span data-testid="first" className="size-touch" />
        <span data-testid="second" className="size-touch" />
      </div>,
    );
    const first = getByTestId("first").getBoundingClientRect();
    const second = getByTestId("second").getBoundingClientRect();
    expect([first.width, first.height]).toEqual([MIN_TARGET_PX, MIN_TARGET_PX]);
    expect(second.left - first.right).toBe(8);
  });

  it("holds the outdoor theme to enhanced contrast (7:1)", async () => {
    await setViewport(FIELD_VIEWPORTS[0]);
    // #767676 on white is 4.54:1: AA, not AAA.
    const ui = (
      <main>
        <p style={{ color: "#767676", background: "#ffffff" }}>Synthetic grey text</p>
      </main>
    );
    const standard = render(ui);
    expect(await auditA11y(standard.container, "standard")).toEqual([]);
    standard.unmount();

    applyTheme("outdoor");
    const outdoor = render(ui);
    const rules = (await auditA11y(outdoor.container, "outdoor")).map((f) => f.rule);
    expect(rules).toContain("color-contrast-enhanced");
  });
});
