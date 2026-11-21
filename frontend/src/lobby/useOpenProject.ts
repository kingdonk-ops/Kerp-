import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useNavigate } from "react-router";

import { useProjectScope } from "../projects/ProjectScopeProvider";

/**
 * Opens a project from the lobby: reloads the caller's projects first (an approval since the
 * last load adds one, diff §4.1), picks it in the top-bar picker and goes to the landing page.
 */
export function useOpenProject(): (projectId: string) => Promise<void> {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { setScope } = useProjectScope();
  return useCallback(
    async (projectId: string) => {
      await queryClient.refetchQueries({ queryKey: ["projects", "mine"] });
      setScope({ kind: "project", projectId });
      void navigate("/");
    },
    [queryClient, navigate, setScope],
  );
}
