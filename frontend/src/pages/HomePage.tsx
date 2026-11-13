import { useTranslation } from "react-i18next";

import { useAuth } from "../auth/AuthProvider";
import { useScopeLabel } from "../projects/ProjectSwitcher";

/** Overview group landing page. */
export function HomePage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const scope = useScopeLabel();

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
