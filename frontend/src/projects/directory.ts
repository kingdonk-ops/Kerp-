import type { components } from "../api/schema";
import type { ApiClient } from "../lib/api/client";

/**
 * One project of the signed-in user, with their personas on it (`GET /api/v1/me/projects`).
 * `access` is `member` through a membership, or `system_admin` when the project is reached
 * only through the caller's `system_admin` grant (ADR 0016, T-0275; no personas then).
 */
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

/** The project is reached through the caller's `system_admin` grant only (ADR 0016). */
export function isAdminAccess(project: ProjectSummary): boolean {
  return project.access === "system_admin";
}

/** True when some project is listed through the `system_admin` grant only (ADR 0016). */
export function hasSystemAdminAccess(projects: readonly ProjectSummary[]): boolean {
  return projects.some(isAdminAccess);
}

/**
 * All-Projects is offered to portfolio personas (diff §6.1) and to a `system_admin`, for whom
 * it covers every project of the tenant (ADR 0016 §1). A `system_admin` who is a member of
 * every project and holds no portfolio persona is not recognisable from this list (TD-499).
 */
export function offersPortfolio(projects: readonly ProjectSummary[]): boolean {
  return hasPortfolioPersona(projects) || hasSystemAdminAccess(projects);
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

/**
 * The caller's live projects from their memberships (T-0014); for a `system_admin`, every live
 * project of the tenant (ADR 0016, T-0275).
 */
export async function fetchMyProjects(api: ApiClient): Promise<ProjectSummary[]> {
  const { data, response } = await api.GET("/api/v1/me/projects");
  if (!data) throw new ProjectsLoadError(response.status);
  return byName(data);
}
