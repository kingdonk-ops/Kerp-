import { useTranslation } from "react-i18next";
import { Navigate } from "react-router";

import { LOBBY_PATH } from "../app/nav";
import { useAuth } from "../auth/AuthProvider";
import { useProjectScope } from "../projects/ProjectScopeProvider";
import { useScopeLabel } from "../projects/ProjectSwitcher";

/**
 * Overview group landing page, and the post-sign-in landing of diff §4.1: the picker's
 * scope opens the user's only project, or the last used / first by name of several; a user
 * with no project goes to the lobby.
 */
export function HomePage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const { projects, isLoading, isError } = useProjectScope();
  const scope = useScopeLabel();

  if (isLoading) {
    return (
      <p role="status" className="text-muted">
        {t("scope.loading")}
      </p>
    );
  }
  if (!isError && projects.length === 0) return <Navigate to={LOBBY_PATH} replace />;

  return (
    <section className="space-y-2">
      <h1 className="text-2xl font-bold">{t("nav.groups.overview.title")}</h1>
      {user && <p>{t("home.welcome", { name: user.display_name })}</p>}
      <p>{t("nav.groups.overview.description")}</p>
      <p className="text-sm text-muted" data-testid="scope-indicator">
        {t("scope.current", { scope })}
      </p>
    </section>
  );
}
