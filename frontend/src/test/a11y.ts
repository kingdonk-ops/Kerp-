// Browser-only accessibility harness (`*.a11y.test.tsx`, Vitest browser mode in Chrome):
// axe-core on real layout and the real Tailwind CSS, so contrast and target size are measured.
import axe from "axe-core";
import { page } from "vitest/browser";

import type { ThemeId } from "../theme/theme";

/** Glove budget, diff §6.3: touch targets ≥ 48 dp (48 CSS px). Mirrors `--spacing-touch`. */
export const MIN_TARGET_PX = 48;

/** Field breakpoints (EDGE-SCREENS §1): phones 360–412 px portrait, rugged tablets. */
export const FIELD_VIEWPORTS = [
  { name: "phone 360", width: 360, height: 740 },
  { name: "phone 412", width: 412, height: 915 },
  { name: "tablet portrait", width: 800, height: 1280 },
  { name: "tablet landscape", width: 1280, height: 800 },
] as const;

export type FieldViewport = (typeof FIELD_VIEWPORTS)[number];

/** WCAG 2.1 A + AA rule sets of axe-core. */
export const WCAG21_AA_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

let configured = false;

function configureAxe(): void {
  if (configured) return;
  // axe's target-size rule (WCAG 2.2 2.5.8) defaults to 24 px; the glove budget is 48 px.
  axe.configure({
    checks: [
      { id: "target-size", options: { minSize: MIN_TARGET_PX } },
      { id: "target-offset", options: { minOffset: MIN_TARGET_PX } },
    ],
  });
  configured = true;
}

export async function setViewport(viewport: FieldViewport): Promise<void> {
  await page.viewport(viewport.width, viewport.height);
}

export interface A11yFinding {
  rule: string;
  kind: "violation" | "incomplete";
  target: string;
  summary: string;
}

/**
 * Runs axe over `root`: WCAG 2.1 AA plus the 48 px target size; the outdoor theme must also
 * reach enhanced contrast (7:1, `color-contrast-enhanced`). A contrast result axe cannot decide
 * (`incomplete`) is reported too, so contrast is never skipped silently. The strict 48 px
 * measurement (`measureGloveTargets`) is added to the findings.
 */
export async function auditA11y(root: Element, theme: ThemeId): Promise<A11yFinding[]> {
  configureAxe();
  const contrastRules = theme === "outdoor" ? ["color-contrast", "color-contrast-enhanced"] : ["color-contrast"];
  const result = await axe.run(root, {
    runOnly: { type: "tag", values: WCAG21_AA_TAGS },
    rules: {
      "target-size": { enabled: true },
      "color-contrast-enhanced": { enabled: theme === "outdoor" },
    },
    resultTypes: ["violations", "incomplete"],
  });
  const findings: A11yFinding[] = [];
  for (const rule of result.violations) {
    for (const node of rule.nodes) {
      findings.push({
        rule: rule.id,
        kind: "violation",
        target: node.target.join(" "),
        summary: node.failureSummary ?? rule.help,
      });
    }
  }
  for (const rule of result.incomplete) {
    if (!contrastRules.includes(rule.id)) continue;
    for (const node of rule.nodes) {
      findings.push({
        rule: rule.id,
        kind: "incomplete",
        target: node.target.join(" "),
        summary: node.failureSummary ?? rule.help,
      });
    }
  }
  return [...findings, ...measureGloveTargets(root)];
}

/** Controls a gloved finger operates. Inactive tabs (`tabindex=-1`) are buttons, so still in. */
const INTERACTIVE = [
  "a[href]",
  "button",
  'input:not([type="hidden"])',
  "select",
  "textarea",
  "summary",
  '[role="button"]',
  '[role="tab"]',
  '[role="checkbox"]',
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

/**
 * The glove budget measured strictly: every rendered control is at least 48 × 48 CSS px.
 * axe's `target-size` (WCAG 2.5.8) also accepts a small target with enough free space around
 * it; gloves need the size itself (diff §6.3). Visually hidden controls (e.g. the unfocused
 * skip link) are not targets.
 */
export function measureGloveTargets(root: Element): A11yFinding[] {
  const findings: A11yFinding[] = [];
  for (const element of root.querySelectorAll<HTMLElement>(INTERACTIVE)) {
    const rect = element.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) continue;
    if (getComputedStyle(element).visibility === "hidden") continue;
    const width = Math.round(rect.width * 10) / 10;
    const height = Math.round(rect.height * 10) / 10;
    if (width + 0.05 >= MIN_TARGET_PX && height + 0.05 >= MIN_TARGET_PX) continue;
    findings.push({
      rule: "glove-target",
      kind: "violation",
      target: describeElement(element),
      summary: `${String(width)} × ${String(height)} px, needs ${String(MIN_TARGET_PX)} × ${String(MIN_TARGET_PX)} px`,
    });
  }
  return findings;
}

function describeElement(element: HTMLElement): string {
  const tag = element.tagName.toLowerCase();
  const name =
    element.getAttribute("aria-label") ??
    element.getAttribute("name") ??
    element.textContent.trim().slice(0, 40);
  return name === "" ? tag : `${tag} "${name}"`;
}

/** Readable one-line-per-finding report for assertion messages. */
export function formatFindings(findings: readonly A11yFinding[]): string {
  return findings.map((f) => `[${f.kind}] ${f.rule} @ ${f.target}: ${f.summary}`).join("\n");
}
