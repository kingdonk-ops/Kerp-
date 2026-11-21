import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate, useSearchParams } from "react-router";

import { useServices } from "../app/services";
import { useAuth } from "../auth/AuthProvider";
import { SSO_SIGNED_IN, ssoErrorCode, type SsoErrorCode } from "../auth/sso";
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
 *   the lobby). A just-joined project is remembered first, so it opens.
 *
 * No token ever travels in this URL; nothing from it is shown or logged except a known code.
 */
export function SsoReturnPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const { status, user, adoptCookieSession } = useAuth();
  const { preferences } = useServices();
  const navigate = useNavigate();
  const started = useRef(false);
  const [adopted, setAdopted] = useState(false);

  const error = params.get("error");
  const signedIn = error === null && params.get("status") === SSO_SIGNED_IN;
  const joined = params.get("joined_project_id");

  useEffect(() => {
    // Once per landing: a second adoption would only drop the fresh access token again.
    if (!signedIn || started.current) return;
    started.current = true;
    adoptCookieSession();
    setAdopted(true);
  }, [signedIn, adoptCookieSession]);

  useEffect(() => {
    if (!adopted || status !== "authenticated" || !user) return;
    if (joined !== null && UUID.test(joined)) rememberProjectScope(preferences, user.id, joined);
    void navigate("/", { replace: true });
  }, [adopted, status, user, joined, preferences, navigate]);

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
