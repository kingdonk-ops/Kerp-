import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router";

import { useServices, useSessionStorage } from "../app/services";
import { AuthCard, CardLink } from "../auth/AuthCard";
import { useAuth } from "../auth/AuthProvider";
import { loadPendingInvite } from "../invite/pendingInvite";
import { NewPasswordForm } from "./NewPasswordForm";
import { PasswordRefusedError, PasswordRequestError, confirmPasswordReset } from "./policy";
import { FORGOT_PASSWORD_PATH } from "./PasswordEmailPage";

export const RESET_PASSWORD_PATH = "/reset-password";

/** A reset token: `secrets.token_urlsafe` output only. */
const TOKEN = /^[A-Za-z0-9_-]{16,512}$/;

/** Router state the sign-in screen reads after a reset: a translated success notice. */
export interface PasswordChangedState {
  notice: "passwordChanged";
  invite?: boolean;
}

/**
 * `/reset-password#<token>` (diff §4.2): the token is read from the fragment once, kept in
 * memory only and removed from the address bar (`index.html` sends no referrer). A new
 * password ends every session of the user, this tab's too, so the user then signs in with it.
 */
export function ResetPasswordPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const session = useSessionStorage();
  const { status, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [token] = useState(() => {
    const raw = location.hash.replace(/^#/, "");
    return TOKEN.test(raw) ? raw : null;
  });

  useEffect(() => {
    if (location.hash !== "") void navigate({ hash: "" }, { replace: true });
  }, [location.hash, navigate]);

  const reset = useMutation({
    mutationFn: (password: string) => confirmPasswordReset(api, token ?? "", password),
    onSuccess: async () => {
      // The server revoked every session of the user, this tab's included.
      if (status !== "anonymous") await logout();
      const state: PasswordChangedState = {
        notice: "passwordChanged",
        invite: loadPendingInvite(session) !== null,
      };
      void navigate("/login", { replace: true, state });
    },
  });
  const err = reset.error;
  const reason = err instanceof PasswordRequestError ? err.reason : null;

  if (token === null || reason === "linkInvalid") {
    return (
      <AuthCard title={t("password.reset.title")}>
        <p role="alert">{t("password.reset.linkInvalid")}</p>
        <CardLink to={FORGOT_PASSWORD_PATH}>{t("password.reset.requestNew")}</CardLink>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("password.reset.title")}>
      <NewPasswordForm
        submitLabel={t("password.reset.submit")}
        busy={reset.isPending}
        refusal={err instanceof PasswordRefusedError ? err.refusal : null}
        error={
          err && !(err instanceof PasswordRefusedError)
            ? t(reason === "throttled" ? "password.throttled" : "password.failed")
            : null
        }
        onSubmit={(password) => {
          reset.mutate(password);
        }}
      />
    </AuthCard>
  );
}
