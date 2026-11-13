import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation, useNavigate } from "react-router";

import { LoginError, useAuth, type LoginFailure } from "../auth/AuthProvider";

export function LoginPage() {
  const { t } = useTranslation();
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<LoginFailure | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const state = location.state as { from?: unknown } | null;
  const from = typeof state?.from === "string" && state.from !== "/login" ? state.from : "/";

  if (status === "authenticated") return <Navigate to={from} replace />;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setFailure(null);
    try {
      await login(email, password);
      void navigate(from, { replace: true });
    } catch (error) {
      setFailure(error instanceof LoginError ? error.reason : "failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface p-4 text-ink">
      <form
        onSubmit={(event) => void onSubmit(event)}
        className="w-full max-w-sm space-y-4 rounded-lg border border-line bg-panel p-6 shadow-sm"
      >
        <p className="text-lg font-semibold text-brand">{t("app.name")}</p>
        <h1 className="text-2xl font-bold">{t("auth.login.title")}</h1>
        <label className="block">
          <span className="text-sm">{t("auth.login.email")}</span>
          <input
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(e) => { setEmail(e.target.value); }}
            className="mt-1 w-full rounded border border-line px-2 py-1"
          />
        </label>
        <label className="block">
          <span className="text-sm">{t("auth.login.password")}</span>
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => { setPassword(e.target.value); }}
            className="mt-1 w-full rounded border border-line px-2 py-1"
          />
        </label>
        {failure && (
          <p role="alert" className="text-sm text-danger">
            {t(`auth.login.${failure}`)}
          </p>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded bg-brand px-3 py-2 font-medium text-on-brand disabled:opacity-60"
        >
          {submitting ? t("auth.login.submitting") : t("auth.login.submit")}
        </button>
      </form>
    </main>
  );
}
