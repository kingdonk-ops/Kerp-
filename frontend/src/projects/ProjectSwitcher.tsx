import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { LOBBY_PATH } from "../app/nav";
import { isAdminAccess } from "./directory";
import { useProjectScope } from "./ProjectScopeProvider";

const ALL_VALUE = "__all__";

/**
 * Top-bar project picker (diff §4.1, §6.1): the user's projects from their memberships (every
 * project of the tenant for a `system_admin`, ADR 0016), the All-Projects portfolio scope for
 * portfolio personas and `system_admin`, and the "Browse / join another project" entry that
 * opens the lobby. A project reached without membership is labelled "Admin access" in its
 * option (so type-ahead finds it like any other) and, when picked, by a text badge that
 * describes the picker.
 */
export function ProjectSwitcher() {
  const { t } = useTranslation();
  const { scope, project, projects, portfolio, isLoading, isError, setScope } =
    useProjectScope();
  const badgeId = useId();
  const adminBadge = project !== null && isAdminAccess(project);

  let status: string | null = null;
  if (isLoading) status = t("scope.loading");
  else if (isError) status = t("scope.loadError");
  else if (projects.length === 0) status = t("scope.noProjects");

  return (
    <div className="flex flex-wrap items-center gap-touch-gap text-sm">
      {status === null ? (
        <>
          <label className="flex items-center gap-2">
            <span>{t("scope.label")}</span>
            <select
              className="min-h-touch min-w-touch rounded border border-line bg-panel px-2 text-ink"
              value={
                scope.kind === "project" ? scope.projectId : scope.kind === "all" ? ALL_VALUE : ""
              }
              aria-describedby={adminBadge ? badgeId : undefined}
              onChange={(event) => {
                const value = event.target.value;
                if (value === "") return;
                setScope(
                  value === ALL_VALUE ? { kind: "all" } : { kind: "project", projectId: value },
                );
              }}
            >
              {scope.kind === "none" && (
                <option value="" disabled>
                  {t("scope.choose")}
                </option>
              )}
              {portfolio && <option value={ALL_VALUE}>{t("scope.allProjects")}</option>}
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {isAdminAccess(p)
                    ? t("scope.projectOptionAdmin", {
                        code: p.code,
                        name: p.name,
                        badge: t("scope.adminAccess"),
                      })
                    : t("scope.projectOption", { code: p.code, name: p.name })}
                </option>
              ))}
            </select>
          </label>
          {adminBadge && (
            <span
              id={badgeId}
              data-testid="scope-admin-badge"
              title={t("scope.adminAccessHint")}
              className="inline-flex items-center rounded border border-warning px-2 font-semibold text-ink"
            >
              {t("scope.adminAccess")}
            </span>
          )}
        </>
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
