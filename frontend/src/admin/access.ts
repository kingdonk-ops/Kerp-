import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { useServices } from "../app/services";
import { useAuth } from "../auth/AuthProvider";
import { useProjectScope } from "../projects/ProjectScopeProvider";
import { fetchIsSystemAdmin } from "./api";

export interface AdminAccess {
  /** Projects where the user holds PER-09 (from `/me/projects`). */
  adminProjectIds: string[];
  systemAdmin: boolean;
  /** PER-09 on some project or System admin: the Admin screens of T-0051 are offered. */
  projectAdmin: boolean;
  isLoading: boolean;
}

/** What the signed-in user administers (diff §4.1); the server still decides every call. */
export function useAdminAccess(): AdminAccess {
  const { api } = useServices();
  const { user } = useAuth();
  const { projects, isLoading } = useProjectScope();
  const userId = user?.id;
  const sys = useQuery({
    queryKey: ["auth", "systemAdmin", userId],
    enabled: userId !== undefined,
    queryFn: () => fetchIsSystemAdmin(api, userId ?? ""),
  });
  return useMemo(() => {
    const adminProjectIds = projects.filter((p) => p.personas.includes("PER-09")).map((p) => p.id);
    const systemAdmin = sys.data === true;
    return {
      adminProjectIds,
      systemAdmin,
      projectAdmin: systemAdmin || adminProjectIds.length > 0,
      isLoading: isLoading || sys.isPending,
    };
  }, [projects, isLoading, sys.data, sys.isPending]);
}
