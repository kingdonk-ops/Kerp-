// Form-open budget (diff §6.3, T-0045): a synthetic 300-question template opens in the Dynamic
// Form Renderer (first interactive render) in < 1.5 s, median of 5 runs, headless Chrome with
// the CPU throttle of `budgets.json`. Measures only; the reporter (`reporter.ts`) judges the
// budget, so a breach is a warning until T-0146. This test fails only if the measurement is broken.
import { render } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { cdp } from "vitest/browser";

import "../i18n";
import { DynamicForm } from "../shells/form/DynamicForm";
import { NFR_CONFIG } from "./budgets";
import { NFR_META_KEY, type Measurement } from "./report";
import { syntheticTemplate } from "./syntheticTemplate";

const budget = NFR_CONFIG.budgets.formOpen;

/** Resolves after the next frame is painted: rAF runs before paint, the macrotask after it. */
const nextPaint = () =>
  new Promise<void>((resolve) => {
    requestAnimationFrame(() => {
      setTimeout(resolve, 0);
    });
  });

describe("NFR budget: form open (diff §6.3)", () => {
  beforeAll(async () => {
    await cdp().send("Emulation.setCPUThrottlingRate", { rate: NFR_CONFIG.cpuThrottleRate });
  });

  afterAll(async () => {
    await cdp().send("Emulation.setCPUThrottlingRate", { rate: 1 });
  });

  it(`opens a ${String(budget.size)}-question template (${String(budget.runs)} runs)`, async ({ task }) => {
    const template = syntheticTemplate(budget.size);
    const noop = () => undefined;
    const samplesMs: number[] = [];
    for (let run = 0; run < budget.runs; run += 1) {
      const start = performance.now();
      // `render` commits synchronously (act); the first control taking focus marks it
      // interactive, and the next painted frame ends the measurement.
      const view = render(<DynamicForm template={template} onSubmit={noop} />);
      const first = view.container.querySelector<HTMLElement>("input, select");
      first?.focus();
      await nextPaint();
      samplesMs.push(performance.now() - start);

      // Sanity (outside the timing): every question rendered and the form is interactive.
      expect(view.container.querySelectorAll("label")).toHaveLength(budget.size);
      expect(first).not.toBeNull();
      expect(document.activeElement).toBe(first);
      view.unmount();
    }
    const measurement: Measurement = { budget: "formOpen", samplesMs };
    Object.assign(task.meta, { [NFR_META_KEY]: measurement });
    expect(samplesMs).toHaveLength(budget.runs);
  });
});
