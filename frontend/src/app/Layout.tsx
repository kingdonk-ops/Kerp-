import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router";

import { useAuth } from "../auth/AuthProvider";
import { ProjectScopeProvider } from "../projects/ProjectScopeProvider";
import { ProjectSwitcher } from "../projects/ProjectSwitcher";
import { NAV_GROUPS } from "./nav";

/** Authenticated app shell: top bar (switcher, user) + 8-group side navigation. */
export function Layout() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();

  useEffect(() => {
    document.title = t("app.name");
  }, [t]);

  return (
    <ProjectScopeProvider>
      <div className="min-h-screen bg-surface text-ink">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:bg-panel focus:p-2"
        >
          {t("app.skipToContent")}
        </a>
        <header className="flex flex-wrap items-center gap-4 border-b border-line bg-panel px-4 py-2">
          <span className="text-lg font-semibold text-brand">{t("app.name")}</span>
          <ProjectSwitcher />
          <div className="ml-auto flex items-center gap-3 text-sm">
            {user && <span>{t("auth.signedInAs", { name: user.display_name })}</span>}
            <button type="button" className="underline" onClick={() => void logout()}>
              {t("auth.logout")}
            </button>
          </div>
        </header>
        <div className="flex">
          <nav aria-label={t("nav.label")} className="w-56 shrink-0 border-r border-line p-2">
            <ul className="space-y-1">
              {NAV_GROUPS.map((group) => (
                <li key={group.id}>
                  <NavLink
                    to={group.path}
                    end={group.path === "/"}
                    className={({ isActive }) =>
                      `flex items-center justify-between rounded px-3 py-2 ${
                        isActive ? "bg-brand text-on-brand" : "hover:bg-line"
                      }`
                    }
                  >
                    <span>{t(`nav.groups.${group.id}.title`)}</span>
                    {group.pillar && (
                      <span className="rounded bg-pillar px-1 text-xs text-ink">
                        {t("nav.pillar")}
                      </span>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <main id="main" className="min-w-0 flex-1 p-4">
            <Outlet />
          </main>
        </div>
      </div>
    </ProjectScopeProvider>
  );
}
