import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router";

import { useAuth } from "../auth/AuthProvider";
import { ProjectScopeProvider } from "../projects/ProjectScopeProvider";
import { ProjectSwitcher } from "../projects/ProjectSwitcher";
import { ThemeToggle } from "../theme/ThemeToggle";
import { NAV_GROUPS } from "./nav";

/**
 * Authenticated app shell: top bar (switcher, theme, user) + 8-group navigation. Field
 * breakpoints (diff §6.3): on phones the navigation wraps into rows of buttons above the
 * content, from `md` a side bar. Every control is a 48 px glove target (`min-h-touch`).
 */
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
          className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:inline-flex focus:min-h-touch focus:items-center focus:bg-panel focus:px-4"
        >
          {t("app.skipToContent")}
        </a>
        <header className="flex flex-wrap items-center gap-touch-gap border-b border-line bg-panel px-4 py-2">
          <span className="text-lg font-semibold text-brand">{t("app.name")}</span>
          <ProjectSwitcher />
          <div className="flex flex-wrap items-center gap-touch-gap text-sm md:ml-auto">
            {user && <span>{t("auth.signedInAs", { name: user.display_name })}</span>}
            <ThemeToggle />
            <button
              type="button"
              className="min-h-touch min-w-touch rounded border border-line bg-panel px-3 underline"
              onClick={() => void logout()}
            >
              {t("auth.logout")}
            </button>
          </div>
        </header>
        <div className="flex flex-col md:flex-row">
          <nav
            aria-label={t("nav.label")}
            className="shrink-0 border-b border-line p-2 md:w-56 md:border-r md:border-b-0"
          >
            <ul className="flex flex-wrap gap-touch-gap md:flex-col">
              {NAV_GROUPS.map((group) => (
                <li key={group.id} className="shrink-0">
                  <NavLink
                    to={group.path}
                    end={group.path === "/"}
                    className={({ isActive }) =>
                      `flex min-h-touch items-center justify-between gap-2 rounded px-3 whitespace-nowrap ${
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
