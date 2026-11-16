import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { LOBBY_PATH } from "../app/nav";
import { useProjectScope } from "./ProjectScopeProvider";

const ALL_VALUE = "__all__";

/**
 * Top-bar project picker (diff §4.1, §6.1): the user's projects from their memberships, the
 * All-Projects portfolio scope for portfolio personas only, and the
 * "Browse / join another project" entry that opens the lobby.
 */
export function ProjectSwitcher() {
  const { t } = useTranslation();
  const { scope, projects, portfolio, isLoading, isError, setScope } = useProjectScope();

  let status: string | null = null;
  if (isLoading) status = t("scope.loading");
  else if (isError) status = t("scope.loadError");
  else if (projects.length === 0) status = t("scope.noProjects");

  return (
    <div className="flex flex-wrap items-center gap-touch-gap text-sm">
      {status === null ? (
        <label className="flex items-center gap-2">
          <span>{t("scope.label")}</span>
          <select
            className="min-h-touch min-w-touch rounded border border-line bg-panel px-2 text-ink"
            value={scope.kind === "project" ? scope.projectId : ALL_VALUE}
            onChange={(event) => {
              const value = event.target.value;
              setScope(value === ALL_VALUE ? { kind: "all" } : { kind: "project", projectId: value });
            }}
          >
            {portfolio && <option value={ALL_VALUE}>{t("scope.allProjects")}</option>}
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {t("scope.projectOption", { code: project.code, name: project.name })}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <span className="text-muted" {...(isError ? { role: "alert" } : {})}>
          {status}
        </span>
      )}
      <Link
        to={LOBBY_PATH}
        className="inline-flex min-h-touch items-center rounded px-2 text-brand underline"
      >
        {t("scope.browseLobby")}
      </Link>
    </div>
  );
}

/** Human-readable label of the current scope. */
export function useScopeLabel(): string {
  const { t } = useTranslation();
  const { scope, project } = useProjectScope();
  if (scope.kind === "all") return t("scope.allProjects");
  if (!project) return t("scope.none");
  return t("scope.projectOption", { code: project.code, name: project.name });
}
