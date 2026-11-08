import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Outlet } from "react-router";

export function Layout() {
  const { t } = useTranslation();

  useEffect(() => {
    document.title = t("app.name");
  }, [t]);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:bg-white focus:p-2"
      >
        {t("app.skipToContent")}
      </a>
      <header className="border-b border-slate-200 bg-white px-4 py-3">
        <span className="text-lg font-semibold">{t("app.name")}</span>
      </header>
      <main id="main" className="mx-auto max-w-5xl p-4">
        <Outlet />
      </main>
    </div>
  );
}
