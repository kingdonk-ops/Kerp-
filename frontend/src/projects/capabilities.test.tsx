// Client-side capability gate for A/S actions (ADR 0016 §4, T-0276). Synthetic data.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { projectCapabilities, RequireCapability, type Capability } from "./capabilities";
import type { Persona, ProjectSummary } from "./directory";

const ALL_PERSONAS: Persona[] = [
  "PER-01", "PER-02", "PER-03", "PER-04", "PER-05", "PER-06", "PER-07", "PER-08",
  "PER-09", "PER-10", "PER-11", "PER-12", "PER-13", "PER-14", "PER-15", "PER-16",
];

function project(personas: Persona[], access: ProjectSummary["access"] = "member"): ProjectSummary {
  return { id: "p-syn", code: "SYN-009", name: "Synthetic Test Unit", personas, access };
}

const caps = (p: ProjectSummary) => [...projectCapabilities(p)].sort();

describe("projectCapabilities (mirror of the backend persona matrix)", () => {
  it("gives A only to PER-02/06/07/11 and S only to PER-15", () => {
    const approvers = ALL_PERSONAS.filter((p) => projectCapabilities(project([p])).has("A"));
    const signers = ALL_PERSONAS.filter((p) => projectCapabilities(project([p])).has("S"));
    expect(approvers).toEqual(["PER-02", "PER-06", "PER-07", "PER-11"]);
    expect(signers).toEqual(["PER-15"]);
  });

  it("maps tiers: manager/editor V C E, admin adds admin, portal and viewer V", () => {
    expect(caps(project(["PER-01"]))).toEqual(["C", "E", "V"]);
    expect(caps(project(["PER-14"]))).toEqual(["C", "E", "V"]);
    expect(caps(project(["PER-09"]))).toEqual(["C", "E", "V", "admin"]);
    expect(caps(project(["PER-15"]))).toEqual(["S", "V"]);
    expect(caps(project(["PER-16"]))).toEqual(["V"]);
    expect(caps(project([]))).toEqual([]);
  });

  it("gives system_admin access V C E admin and never A or S", () => {
    expect(caps(project([], "system_admin"))).toEqual(["C", "E", "V", "admin"]);
  });

  it("takes A and S from the caller's own personas only", () => {
    expect(caps(project(["PER-06"]))).toEqual(["A", "C", "E", "V"]);
    expect(caps(project(["PER-15"], "member"))).toContain("S");
  });
});

describe("RequireCapability", () => {
  const gate = (p: ProjectSummary | null, capability: Capability) =>
    render(
      <RequireCapability project={p} capability={capability}>
        <button type="button">synthetic action</button>
      </RequireCapability>,
    );

  it("hides an approve action on a project reached through system_admin only", () => {
    gate(project([], "system_admin"), "A");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("hides a sign action on a project reached through system_admin only", () => {
    gate(project([], "system_admin"), "S");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows admin-tier actions on such a project", () => {
    gate(project([], "system_admin"), "E");
    expect(screen.getByRole("button", { name: "synthetic action" })).toBeInTheDocument();
  });

  it("shows an approve action to a holder of an approving persona", () => {
    gate(project(["PER-07"]), "A");
    expect(screen.getByRole("button", { name: "synthetic action" })).toBeInTheDocument();
  });

  it("hides everything when no project is resolved", () => {
    gate(null, "V");
    expect(screen.queryByRole("button")).toBeNull();
  });
});
