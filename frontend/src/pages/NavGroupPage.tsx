import { useTranslation } from "react-i18next";

import type { NavGroupId } from "../app/nav";
import { useScopeLabel } from "../projects/ProjectSwitcher";

/** Landing page of a navigation group until its module screens exist. */
export function NavGroupPage({ group }: { group: NavGroupId }) {
  const { t } = useTranslation();
  const scope = useScopeLabel();

  return (
    <section className="space-y-2">
      <h1 className="text-2xl font-bold">{t(`nav.groups.${group}.title`)}</h1>
      <p>{t(`nav.groups.${group}.description`)}</p>
      <p className="text-sm text-muted" data-testid="scope-indicator">
        {t("scope.current", { scope })}
      </p>
      <p className="text-sm text-muted">{t("nav.placeholder")}</p>
    </section>
  );
}
