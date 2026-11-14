import { useTranslation } from "react-i18next";
import { Navigate, Outlet, useLocation } from "react-router";

import { useAuth } from "./AuthProvider";

/** Route guard: renders the protected routes only for a signed-in user. */
export function RequireAuth() {
  const { t } = useTranslation();
  const { status, retry } = useAuth();
  const location = useLocation();

  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (status === "error") {
    return (
      <div role="alert" className="p-4 text-danger">
        <p>{t("app.loadError")}</p>
        <button type="button" className="mt-2 min-h-touch min-w-touch rounded border border-line px-3 underline" onClick={retry}>
          {t("app.retry")}
        </button>
      </div>
    );
  }
  if (status === "loading") {
    return (
      <p role="status" className="p-4 text-muted">
        {t("app.loading")}
      </p>
    );
  }
  return <Outlet />;
}
