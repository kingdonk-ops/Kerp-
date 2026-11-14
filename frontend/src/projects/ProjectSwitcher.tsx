import { useTranslation } from "react-i18next";

import { useProjectScope } from "./ProjectScopeProvider";

const ALL_VALUE = "__all__";

/** Top-bar project switcher with the All-Projects portfolio scope (diff §6). */
export function ProjectSwitcher() {
  const { t } = useTranslation();
  const { scope, projects, isLoading, setScope } = useProjectScope();

  return (
    <label className="flex items-center gap-2 text-sm">
      <span>{t("scope.label")}</span>
      <select
        className="min-h-touch min-w-touch rounded border border-line bg-panel px-2 text-ink"
        disabled={isLoading}
        value={scope.kind === "all" ? ALL_VALUE : scope.projectId}
        onChange={(event) => {
          const value = event.target.value;
          setScope(value === ALL_VALUE ? { kind: "all" } : { kind: "project", projectId: value });
        }}
      >
        <option value={ALL_VALUE}>{t("scope.allProjects")}</option>
        {projects.map((project) => (
          <option key={project.id} value={project.id}>
            {t("scope.projectOption", { code: project.code, name: project.name })}
          </option>
        ))}
      </select>
      {!isLoading && projects.length === 0 && (
        <span className="text-muted">{t("scope.noProjects")}</span>
      )}
    </label>
  );
}

/** Human-readable label of the current scope. */
export function useScopeLabel(): string {
  const { t } = useTranslation();
  const { scope, project } = useProjectScope();
  if (scope.kind === "all" || !project) return t("scope.allProjects");
  return t("scope.projectOption", { code: project.code, name: project.name });
}
