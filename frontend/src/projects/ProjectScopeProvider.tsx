import { useQuery } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { useServices } from "../app/services";
import type { KeyValueStorage } from "../auth/tokenStore";
import { useAuth } from "../auth/AuthProvider";
import { fetchMyProjects, offersPortfolio, type ProjectSummary } from "./directory";

/**
 * The working scope picked in the top-bar picker: one project, the All-Projects portfolio
 * scope (portfolio personas and `system_admin`, diff §6.1, ADR 0016), or none while the user
 * has no project or (a `system_admin` without membership) has not picked one yet. This is
 * a UI selection only — the server binds the allowed project list from the user's
 * memberships, never from the client (TD-053).
 */
export type ProjectScope =
  | { kind: "all" }
  | { kind: "project"; projectId: string }
  | { kind: "none" };

interface ProjectScopeValue {
  scope: ProjectScope;
  /** The selected project, or `null` for the portfolio scope and while there is none. */
  project: ProjectSummary | null;
  /** The user's projects from `GET /api/v1/me/projects`, by name (every project for a
   * `system_admin`, ADR 0016). */
  projects: ProjectSummary[];
  /** The user holds a portfolio persona or reaches projects as `system_admin`, so
   * All-Projects is offered. */
  portfolio: boolean;
  isLoading: boolean;
  isError: boolean;
  setScope: (scope: ProjectScope) => void;
}

const ProjectScopeContext = createContext<ProjectScopeValue | null>(null);

const ALL: ProjectScope = { kind: "all" };
const NONE: ProjectScope = { kind: "none" };

export function scopeStorageKey(userId: string): string {
  return `kerp.scope.${userId}`;
}

function encode(scope: ProjectScope): string | null {
  if (scope.kind === "all") return "all";
  if (scope.kind === "project") return `project:${scope.projectId}`;
  return null;
}

/**
 * Remembers `projectId` as the user's scope on this client, so the next landing opens it
 * (used after a sign-in that joined a project, diff §4.1). Dropped like any remembered scope
 * once the project is not in the user's list.
 */
export function rememberProjectScope(
  preferences: KeyValueStorage,
  userId: string,
  projectId: string,
): void {
  const raw = encode({ kind: "project", projectId });
  if (raw !== null) preferences.setItem(scopeStorageKey(userId), raw);
}

function decode(raw: string | null): ProjectScope | null {
  if (raw === "all") return ALL;
  if (raw?.startsWith("project:")) return { kind: "project", projectId: raw.slice(8) };
  return null;
}

/**
 * Landing scope of diff §4.1: the remembered scope while the user still has it (All-Projects
 * only with a portfolio persona or `system_admin` access), else the first project by name the
 * user is a member of, else none. A `system_admin` without any membership therefore lands on
 * the picker with no project chosen, not in the lobby (ADR 0016, T-0276).
 */
export function resolveScope(
  stored: ProjectScope | null,
  projects: readonly ProjectSummary[],
): ProjectScope {
  if (stored?.kind === "all" && offersPortfolio(projects)) return ALL;
  if (stored?.kind === "project" && projects.some((p) => p.id === stored.projectId)) return stored;
  const first = projects.find((p) => p.access === "member");
  return first ? { kind: "project", projectId: first.id } : NONE;
}

export function ProjectScopeProvider({ children }: { children: ReactNode }) {
  const { api, preferences } = useServices();
  const { user } = useAuth();
  const userId = user?.id ?? "anonymous";
  const key = scopeStorageKey(userId);
  const [stored, setStored] = useState<{ key: string; scope: ProjectScope | null }>(() => ({
    key,
    scope: decode(preferences.getItem(key)),
  }));
  // Another user signed in: read their stored scope.
  const storedScope = useMemo(
    () => (stored.key === key ? stored.scope : decode(preferences.getItem(key))),
    [stored, key, preferences],
  );

  const list = useQuery({
    queryKey: ["projects", "mine", userId],
    queryFn: () => fetchMyProjects(api),
  });
  const projects = useMemo(() => list.data ?? [], [list.data]);
  const loaded = list.isSuccess;

  const setScope = useCallback(
    (scope: ProjectScope) => {
      const raw = encode(scope);
      if (raw === null) preferences.removeItem(key);
      else preferences.setItem(key, raw);
      setStored({ key, scope: raw === null ? null : scope });
    },
    [key, preferences],
  );

  const scope = useMemo(
    () => (loaded ? resolveScope(storedScope, projects) : NONE),
    [loaded, storedScope, projects],
  );

  // The remembered scope is dropped from the client once it is no longer allowed (diff
  // §4.1); `resolveScope` already ignores it for this render.
  useEffect(() => {
    if (!loaded || storedScope === null) return;
    if (encode(resolveScope(storedScope, projects)) !== encode(storedScope)) {
      preferences.removeItem(key);
    }
  }, [loaded, storedScope, projects, preferences, key]);

  const value = useMemo<ProjectScopeValue>(() => {
    const project =
      scope.kind === "project" ? (projects.find((p) => p.id === scope.projectId) ?? null) : null;
    return {
      scope,
      project,
      projects,
      portfolio: offersPortfolio(projects),
      isLoading: list.isPending,
      isError: list.isError,
      setScope,
    };
  }, [scope, projects, list.isPending, list.isError, setScope]);

  return <ProjectScopeContext.Provider value={value}>{children}</ProjectScopeContext.Provider>;
}

export function useProjectScope(): ProjectScopeValue {
  const value = useContext(ProjectScopeContext);
  if (!value) throw new Error("useProjectScope must be used inside <ProjectScopeProvider>");
  return value;
}
