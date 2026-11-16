import { useTranslation } from "react-i18next";

import { useProjectScope } from "../projects/ProjectScopeProvider";

/**
 * Lobby route (diff §4.1): where users without a project land and where the picker's
 * "Browse / join another project" entry leads. Placeholder until the lobby screens of T-0041
 * (lobby list, project-code entry, active requests) replace it.
 */
export function LobbyPage() {
  const { t } = useTranslation();
  const { projects, isLoading, isError } = useProjectScope();

  return (
    <section className="space-y-2">
      <h1 className="text-2xl font-bold">{t("lobby.title")}</h1>
      {!isLoading && !isError && projects.length === 0 && <p>{t("lobby.noProjects")}</p>}
      <p className="text-sm text-muted">{t("lobby.placeholder")}</p>
    </section>
  );
}
