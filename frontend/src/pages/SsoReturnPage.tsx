import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import { useServices, useSessionStorage } from "../app/services";
import { useAuth } from "../auth/AuthProvider";
import { SSO_SIGNED_IN, ssoErrorCode, type SsoErrorCode } from "../auth/sso";
import type { InviteOutcomeState } from "../invite/InvitePage";
import { clearPendingInvite, INVITE_PATH } from "../invite/pendingInvite";
import { rememberProjectScope } from "../projects/ProjectScopeProvider";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Router state the sign-in screen reads to show an SSO refusal (never the raw URL value). */
export interface SsoRefusalState {
  ssoError: SsoErrorCode;
}

function refused(code: SsoErrorCode) {
  const state: SsoRefusalState = { ssoError: code };
  return <Navigate to="/login" replace state={state} />;
}

/**
 * `/login/sso-return`: where the Entra ID callback (T-0054) sends the browser (diff §4.5).
 *
 * - `?error=<code>` → the sign-in screen with the translated refusal (unknown → generic).
 * - `?status=signed_in[&joined_project_id=<id>]` → the server set the session's `httpOnly`
 *   refresh cookie; the tab adopts it (`/auth/refresh` gives the in-memory access token) and
 *   lands like any sign-in (diff §4.1: `/` opens the only / last used / first project, or
 *   the lobby). A just-joined project is remembered first, so it opens; `&invite_error=<code>`
 *   (T-0053) lands on `/invite` with the neutral message for that code instead.
 *
 * No token ever travels in this URL; nothing from it is shown or logged except a known code.
 */
export function SsoReturnPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const { status, user, adoptCookieSession } = useAuth();
  const { preferences } = useServices();
  const session = useSessionStorage();
  const navigate = useNavigate();
  const started = useRef(false);
  const [adopted, setAdopted] = useState(false);

  const error = params.get("error");
  const signedIn = error === null && params.get("status") === SSO_SIGNED_IN;
  const joined = params.get("joined_project_id");
  const inviteError = params.get("invite_error");

  useEffect(() => {
    // The SSO start carried any pending invite to the server (T-0053): the tab drops it.
    clearPendingInvite(session);
  }, [session]);

  useEffect(() => {
    // Once per landing: a second adoption would only drop the fresh access token again.
    if (!signedIn || started.current) return;
    started.current = true;
    adoptCookieSession();
    setAdopted(true);
  }, [signedIn, adoptCookieSession]);

  useEffect(() => {
    if (!adopted || status !== "authenticated" || !user) return;
    // A `joined_project_id` that is not a UUID is ignored: the normal landing applies.
    if (joined !== null && UUID.test(joined)) rememberProjectScope(preferences, user.id, joined);
    // A refused invite (T-0053): `/invite` shows its neutral message (codes only, never raw).
    const outcome: InviteOutcomeState = { inviteError };
    if (inviteError !== null) void navigate(INVITE_PATH, { replace: true, state: outcome });
    else void navigate("/", { replace: true });
  }, [adopted, status, user, joined, inviteError, preferences, navigate]);

  if (error !== null) return refused(ssoErrorCode(error));
  if (!signedIn) return <Navigate to="/login" replace />;
  // The refresh was refused (no cookie, expired, revoked): the session is gone again.
  if (adopted && status === "anonymous") return refused("SSO_FAILED");
  // `/` shows the retry for a session that is there but could not be loaded.
  if (adopted && status === "error") return <Navigate to="/" replace />;

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface p-4 text-ink">
      <p role="status" className="text-muted">
        {t("auth.sso.signingIn")}
      </p>
    </main>
  );
}
