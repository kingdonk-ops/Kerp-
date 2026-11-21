import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuth } from "../auth/AuthProvider";
import { ActiveRequests } from "../lobby/ActiveRequests";
import { MINE_KEY } from "../lobby/api";
import { CodeLookup } from "../lobby/CodeLookup";
import { LobbyList } from "../lobby/LobbyList";
import { useProjectScope } from "../projects/ProjectScopeProvider";

/**
 * Lobby route (diff §4.1, T-0041): where users without a project land and where the picker's
 * "Browse / join another project" entry leads. Internal users see the lobby list; everyone
 * can enter a project code; the Active requests panel shows the outcome of their requests.
 */
export function LobbyPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { projects, isLoading, isError } = useProjectScope();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const onSent = useCallback(() => {
    setNotice(t("lobby.sent"));
    void queryClient.invalidateQueries({ queryKey: MINE_KEY });
  }, [t, queryClient]);
  const internal = user?.is_internal === true;

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h1 className="text-2xl font-bold">{t("lobby.title")}</h1>
        {!isLoading && !isError && projects.length === 0 && <p>{t("lobby.noProjects")}</p>}
        {!internal && <p>{t("lobby.list.externalHint")}</p>}
        <p role="status">{notice}</p>
      </section>
      <ActiveRequests notify={setNotice} />
      <CodeLookup onSent={onSent} />
      {internal && <LobbyList onSent={onSent} />}
    </div>
  );
}
