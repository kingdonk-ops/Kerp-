import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation, useNavigate } from "react-router";

import { useSessionStorage } from "../app/services";
import { CardLink } from "../auth/AuthCard";
import { LoginError, useAuth, type LoginFailure } from "../auth/AuthProvider";
import { ssoErrorCode, type SsoErrorCode } from "../auth/sso";
import { SsoSignIn, useSsoStartUrl } from "../auth/SsoSignIn";
import type { InviteOutcomeState } from "../invite/InvitePage";
import { MagicLinkRequest } from "../invite/MagicLinkRequest";
import { clearPendingInvite, INVITE_PATH, loadPendingInvite } from "../invite/pendingInvite";
import { FORGOT_PASSWORD_PATH, type PasswordEmailState } from "../password/PasswordEmailPage";
import { ThemeToggle } from "../theme/ThemeToggle";

const PRIMARY = "bg-brand font-medium text-on-brand";
const SECONDARY = "border border-line bg-panel font-medium text-ink";

/** `/invite` opens the joined project or explains the refusal; neither (no invite hook): `/`. */
function outcomePath(outcome: InviteOutcomeState): string {
  return outcome.joinedProjectId || outcome.inviteError ? INVITE_PATH : "/";
}

/**
 * Sign-in screen. When the deployment offers Entra ID SSO, "Sign in with Microsoft" is the
 * primary, default action and password sign-in stays below it for everyone, internal users
 * included (diff §4.5 *SSO promoted, password kept*); without SSO, password only.
 * "Forgot password?" leads to the reset request (diff §4.2). Reached from an invite (router
 * state `invite`, T-0053), every sign-in carries the tab's pending `invite_token`, and the
 * screen also offers the magic link and "Send me an e-mail to set my password" (diff §4.1).
 */
export function LoginPage() {
  const { t } = useTranslation();
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const ssoStartUrl = useSsoStartUrl();
  const session = useSessionStorage();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [failure, setFailure] = useState<LoginFailure | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<InviteOutcomeState | null>(null);

  const state = location.state as {
    from?: unknown;
    ssoError?: unknown;
    invite?: unknown;
    notice?: unknown;
  } | null;
  const inviteFlow = state?.invite === true;
  const invite = useMemo(
    () => (inviteFlow ? loadPendingInvite(session) : null),
    [inviteFlow, session],
  );
  useEffect(() => {
    // A pending invite belongs to its own flow: any other sign-in drops it.
    if (!inviteFlow) clearPendingInvite(session);
  }, [inviteFlow, session]);

  const from = typeof state?.from === "string" && state.from !== "/login" ? state.from : "/";
  // An SSO refusal from the callback landing (T-0055); hidden once a password attempt starts.
  const ssoError: SsoErrorCode | null =
    state?.ssoError !== undefined && failure === null && !submitting
      ? ssoErrorCode(state.ssoError)
      : null;

  // `me` may load before the navigation below applies: the invite outcome must still win.
  if (status === "authenticated") {
    return outcome ? <Navigate to={outcomePath(outcome)} replace state={outcome} /> : <Navigate to={from} replace />;
  }

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setFailure(null);
    try {
      const result = await login(email, password, invite?.inviteToken);
      if (invite) {
        // Used: `/invite` opens the joined project or explains the refusal.
        clearPendingInvite(session);
        const next: InviteOutcomeState = {
          joinedProjectId: result.joinedProjectId,
          inviteError: result.inviteError,
        };
        setOutcome(next);
        void navigate(outcomePath(next), { replace: true, state: next });
      } else {
        void navigate(from, { replace: true });
      }
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
        {invite && <p>{t("invite.signIn.notice")}</p>}
        {state?.notice === "passwordChanged" && failure === null && (
          <p role="status">{t("password.reset.done")}</p>
        )}
        {ssoError && (
          <p role="alert" className="text-sm text-danger">
            {t(`auth.sso.errors.${ssoError}`)}
          </p>
        )}
        {ssoStartUrl && (
          <>
            <SsoSignIn startUrl={ssoStartUrl} inviteToken={invite?.inviteToken} />
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
        <CardLink
          to={FORGOT_PASSWORD_PATH}
          state={{ invite: invite !== null } satisfies PasswordEmailState}
        >
          {t("password.forgot")}
        </CardLink>
        {invite && (
          <>
            <CardLink
              to={FORGOT_PASSWORD_PATH}
              state={{ purpose: "setup", invite: true } satisfies PasswordEmailState}
            >
              {t("invite.signIn.setPassword")}
            </CardLink>
            <MagicLinkRequest email={email} inviteToken={invite.inviteToken} />
          </>
        )}
      </div>
      <ThemeToggle />
    </main>
  );
}
