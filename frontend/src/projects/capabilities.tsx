import type { ReactNode } from "react";

import type { Persona, ProjectSummary } from "./directory";

/**
 * Project capabilities (edge spec §5.2; backend `app.core.auth.access.Capability`): view,
 * create, edit, approve, sign, and project administration.
 */
export type Capability = "V" | "C" | "E" | "A" | "S" | "admin";

const VCE: readonly Capability[] = ["V", "C", "E"];
const ADMIN_TIER: readonly Capability[] = [...VCE, "admin"];

/**
 * What each persona may do, mirroring `PERSONAS` in `app.core.auth.access` (tier capabilities
 * plus `A` for hold-release / commercial approval and `S` for the signatory). The API does not
 * send capabilities yet (TD-500); the server still decides every call.
 */
const PERSONA_CAPABILITIES: Readonly<Record<Persona, readonly Capability[]>> = {
  "PER-01": VCE,
  "PER-02": [...VCE, "A"],
  "PER-03": VCE,
  "PER-04": VCE,
  "PER-05": VCE,
  "PER-06": [...VCE, "A"],
  "PER-07": [...VCE, "A"],
  "PER-08": VCE,
  "PER-09": ADMIN_TIER,
  "PER-10": VCE,
  "PER-11": [...VCE, "A"],
  "PER-12": VCE,
  "PER-13": VCE,
  "PER-14": VCE,
  "PER-15": ["V", "S"],
  "PER-16": ["V"],
};

/**
 * The caller's capabilities on one project: the union of their personas there, plus the Admin
 * tier (`V`/`C`/`E`/`admin`) for access through the `system_admin` grant. `A` and `S` come only
 * from the caller's own personas, never from `system_admin` (ADR 0016 §4).
 */
export function projectCapabilities(project: ProjectSummary): ReadonlySet<Capability> {
  const caps = new Set<Capability>(project.access === "system_admin" ? ADMIN_TIER : []);
  for (const persona of project.personas) {
    for (const cap of PERSONA_CAPABILITIES[persona]) caps.add(cap);
  }
  return caps;
}

/**
 * Renders `children` only when the caller holds `capability` on `project`. Approve and sign
 * actions (`A`, `S`) go through this gate, so a project reached through `system_admin` alone
 * never offers them; in the All-Projects scope each row passes its own project.
 */
export function RequireCapability({
  project,
  capability,
  children,
}: {
  project: ProjectSummary | null;
  capability: Capability;
  children: ReactNode;
}) {
  if (!project || !projectCapabilities(project).has(capability)) return null;
  return <>{children}</>;
}
