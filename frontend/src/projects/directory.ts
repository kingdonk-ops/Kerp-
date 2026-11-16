import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";

/** One project of the signed-in user, with their personas on it (`GET /api/v1/me/projects`). */
export type ProjectSummary = components["schemas"]["MyProject"];
export type Persona = components["schemas"]["Persona"];

/**
 * Personas with the `portfolio` scope (edge spec §5.2; backend `app.core.auth.access`): they
 * read every project they are a member of in one session, so the picker offers them the
 * All-Projects scope (diff §6, §6.1). The server still binds the allowed projects itself.
 */
export const PORTFOLIO_PERSONAS: ReadonlySet<Persona> = new Set<Persona>([
  "PER-01",
  "PER-10",
  "PER-11",
  "PER-12",
  "PER-13",
]);

/** True when the user holds a portfolio-scope persona on any of their projects. */
export function hasPortfolioPersona(projects: readonly ProjectSummary[]): boolean {
  return projects.some((project) => project.personas.some((p) => PORTFOLIO_PERSONAS.has(p)));
}

/** Projects ordered by name, then code (the "first by name" fallback of diff §4.1). */
export function byName(projects: readonly ProjectSummary[]): ProjectSummary[] {
  return [...projects].sort(
    (a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) ||
      a.code.localeCompare(b.code),
  );
}

export class ProjectsLoadError extends Error {
  constructor(readonly status: number) {
    super(`GET /api/v1/me/projects failed with ${String(status)}`);
    this.name = "ProjectsLoadError";
  }
}

/** The caller's live projects from their memberships (T-0014). */
export async function fetchMyProjects(api: ApiClient): Promise<ProjectSummary[]> {
  const { data, response } = await api.GET("/api/v1/me/projects");
  if (!data) throw new ProjectsLoadError(response.status);
  return byName(data);
}
