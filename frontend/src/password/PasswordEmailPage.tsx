import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import { useServices } from "../app/services";
import { AuthCard, CardLink } from "../auth/AuthCard";
import { FIELD, PRIMARY_BUTTON } from "./NewPasswordForm";
import { PasswordRequestError, requestPasswordEmail } from "./policy";

export const FORGOT_PASSWORD_PATH = "/forgot-password";

/** Router state of `/forgot-password`: `setup` for an invitee without a password (T-0053). */
export interface PasswordEmailState {
  purpose?: "setup";
  /** Back to the sign-in screen of the pending invite. */
  invite?: boolean;
}

/**
 * "Forgot password?" and "Send me an e-mail to set my password" (diff §4.1, §4.2): one e-mail
 * field; every address gets the same neutral confirmation (`POST /auth/password-reset/request`
 * answers 202 for all), so the screen reveals no accounts.
 */
export function PasswordEmailPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const state = (useLocation().state ?? {}) as PasswordEmailState;
  const setup = state.purpose === "setup";
  const [email, setEmail] = useState("");
  const send = useMutation({ mutationFn: () => requestPasswordEmail(api, email.trim()) });
  const reason = send.error instanceof PasswordRequestError ? send.error.reason : "failed";

  return (
    <AuthCard title={t(setup ? "password.request.setupTitle" : "password.request.title")}>
      {send.isSuccess ? (
        <p role="status">{t(setup ? "password.request.setupSent" : "password.request.sent")}</p>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            send.mutate();
          }}
        >
          <p className="text-sm text-muted">
            {t(setup ? "password.request.setupIntro" : "password.request.intro")}
          </p>
          <label className="block">
            <span className="text-sm">{t("auth.login.email")}</span>
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
              }}
              className={FIELD}
            />
          </label>
          {send.isError && (
            <p role="alert" className="text-sm text-danger">
              {t(reason === "throttled" ? "password.throttled" : "password.failed")}
            </p>
          )}
          <button type="submit" disabled={send.isPending} className={PRIMARY_BUTTON}>
            {t("password.request.submit")}
          </button>
        </form>
      )}
      <CardLink to="/login" state={state.invite ? { invite: true } : undefined}>
        {t("password.backToSignIn")}
      </CardLink>
    </AuthCard>
  );
}
