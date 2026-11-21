import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import { safeStartUrl } from "./sso";

export const AUTH_CONFIG_QUERY_KEY = ["auth", "config"] as const;

/**
 * The Entra ID start URL when the server offers SSO (`GET /api/v1/auth/config`, T-0054),
 * else `null`, also while loading and when the configuration cannot be read: the sign-in
 * screen then offers password only, as without SSO.
 */
export function useSsoStartUrl(): string | null {
  const { api } = useServices();
  const config = useQuery({
    queryKey: AUTH_CONFIG_QUERY_KEY,
    queryFn: async () => {
      const { data, response } = await api.GET("/api/v1/auth/config");
      if (!data) throw new Error(`auth config: ${String(response.status)}`);
      return data;
    },
    staleTime: 5 * 60_000,
  });
  return safeStartUrl(config.data);
}

interface SsoSignInProps {
  startUrl: string;
  /** A pending project invite (T-0053) to accept after the sign-in; sent in a form body. */
  inviteToken?: string | undefined;
}

/**
 * "Sign in with Microsoft" (diff §4.5): the primary, default action when SSO is offered.
 * A plain HTML form, so the browser leaves the SPA by a top-level navigation and keeps the
 * pre-auth cookie `/start` sets. With an invite the form posts it in the body (never a URL).
 * The button takes the focus when it appears, unless the user is already in another field.
 */
export function SsoSignIn({ startUrl, inviteToken }: SsoSignInProps) {
  const { t } = useTranslation();
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const active = document.activeElement;
    if (active === null || active === document.body) button.current?.focus();
  }, []);

  return (
    <section className="space-y-2">
      <form action={startUrl} method={inviteToken ? "post" : "get"}>
        {inviteToken && <input type="hidden" name="invite_token" value={inviteToken} />}
        <button
          ref={button}
          type="submit"
          data-variant="primary"
          className="min-h-touch w-full rounded bg-brand px-3 font-medium text-on-brand"
        >
          {t("auth.sso.start")}
        </button>
      </form>
      <p className="text-sm text-muted">{t("auth.sso.hint")}</p>
    </section>
  );
}
