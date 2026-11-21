import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation, useNavigate } from "react-router";

import { LoginError, useAuth, type LoginFailure } from "../auth/AuthProvider";
import { ssoErrorCode, type SsoErrorCode } from "../auth/sso";
import { SsoSignIn, useSsoStartUrl } from "../auth/SsoSignIn";
import { ThemeToggle } from "../theme/ThemeToggle";

const PRIMARY = "bg-brand font-medium text-on-brand";
const SECONDARY = "border border-line bg-panel font-medium text-ink";

/**
 * Sign-in screen. When the deployment offers Entra ID SSO, "Sign in with Microsoft" is the
 * primary, default action and password sign-in stays below it for everyone, internal users
 * included (diff §4.5 *SSO promoted, password kept*); without SSO, password only.
 */
export function LoginPage() {
  const { t } = useTranslation();
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const ssoStartUrl = useSsoStartUrl();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<LoginFailure | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const state = location.state as { from?: unknown; ssoError?: unknown } | null;
  const from = typeof state?.from === "string" && state.from !== "/login" ? state.from : "/";
  // An SSO refusal from the callback landing (T-0055); hidden once a password attempt starts.
  const ssoError: SsoErrorCode | null =
    state?.ssoError !== undefined && failure === null && !submitting
      ? ssoErrorCode(state.ssoError)
      : null;

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
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-surface p-4 text-ink">
      <div className="w-full max-w-sm space-y-4 rounded-lg border border-line bg-panel p-6 shadow-sm">
        <p className="text-lg font-semibold text-brand">{t("app.name")}</p>
        <h1 className="text-2xl font-bold">{t("auth.login.title")}</h1>
        {ssoError && (
          <p role="alert" className="text-sm text-danger">
            {t(`auth.sso.errors.${ssoError}`)}
          </p>
        )}
        {ssoStartUrl && (
          <>
            <SsoSignIn startUrl={ssoStartUrl} />
            <p className="border-t border-line pt-4 text-sm text-muted">
              {t("auth.sso.passwordDivider")}
            </p>
          </>
        )}
        <form onSubmit={(event) => void onSubmit(event)} className="space-y-4">
          <label className="block">
            <span className="text-sm">{t("auth.login.email")}</span>
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => { setEmail(e.target.value); }}
              className="mt-1 min-h-touch w-full rounded border border-line bg-panel px-2"
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
              className="mt-1 min-h-touch w-full rounded border border-line bg-panel px-2"
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
            data-variant={ssoStartUrl ? "secondary" : "primary"}
            className={`min-h-touch w-full rounded px-3 disabled:opacity-60 ${ssoStartUrl ? SECONDARY : PRIMARY}`}
          >
            {submitting ? t("auth.login.submitting") : t("auth.login.submit")}
          </button>
        </form>
      </div>
      <ThemeToggle />
    </main>
  );
}
