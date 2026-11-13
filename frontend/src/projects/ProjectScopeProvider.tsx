import { useQuery } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import { useServices } from "../app/services";
import { useAuth } from "../auth/AuthProvider";
import type { ProjectSummary } from "./directory";

/**
 * The working scope picked in the top-bar switcher: one project, or the All-Projects
 * portfolio scope (diff §6). This is a UI selection only — the server binds the allowed
 * project list from the user's memberships, never from the client (TD-053).
 */
export type ProjectScope = { kind: "all" } | { kind: "project"; projectId: string };

interface ProjectScopeValue {
  scope: ProjectScope;
  /** The selected project, or `null` for the portfolio scope. */
  project: ProjectSummary | null;
  projects: ProjectSummary[];
  isLoading: boolean;
  setScope: (scope: ProjectScope) => void;
}

const ProjectScopeContext = createContext<ProjectScopeValue | null>(null);

const ALL: ProjectScope = { kind: "all" };

export function scopeStorageKey(userId: string): string {
  return `kerp.scope.${userId}`;
}

function encode(scope: ProjectScope): string {
  return scope.kind === "all" ? "all" : `project:${scope.projectId}`;
}

function decode(raw: string | null): ProjectScope | null {
  if (raw === "all") return ALL;
  if (raw?.startsWith("project:")) return { kind: "project", projectId: raw.slice(8) };
  return null;
}

/** Resolves the effective scope: a stored project the user still has, else the first one. */
function resolve(stored: ProjectScope | null, projects: ProjectSummary[]): ProjectScope {
  if (stored?.kind === "all") return ALL;
  if (stored && projects.some((p) => p.id === stored.projectId)) return stored;
  const first = projects[0];
  return first ? { kind: "project", projectId: first.id } : ALL;
}

export function ProjectScopeProvider({ children }: { children: ReactNode }) {
  const { projects: directory, preferences } = useServices();
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
    queryFn: () => directory.listMyProjects(),
  });
  const projects = useMemo(() => list.data ?? [], [list.data]);

  const setScope = useCallback(
    (scope: ProjectScope) => {
      preferences.setItem(key, encode(scope));
      setStored({ key, scope });
    },
    [key, preferences],
  );

  const value = useMemo<ProjectScopeValue>(() => {
    const scope = resolve(storedScope, projects);
    const project =
      scope.kind === "project" ? (projects.find((p) => p.id === scope.projectId) ?? null) : null;
    return { scope, project, projects, isLoading: list.isPending, setScope };
  }, [storedScope, projects, list.isPending, setScope]);

  return <ProjectScopeContext.Provider value={value}>{children}</ProjectScopeContext.Provider>;
}

export function useProjectScope(): ProjectScopeValue {
  const value = useContext(ProjectScopeContext);
  if (!value) throw new Error("useProjectScope must be used inside <ProjectScopeProvider>");
  return value;
}
