import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router";

import { unsubscribe } from "../admin/comms";
import { useServices } from "../app/services";
import { AuthCard } from "../auth/AuthCard";

/** An opt-out token: `secrets.token_urlsafe` output only (a real one is 43 characters). */
const TOKEN = /^[A-Za-z0-9_-]{16,512}$/;

/**
 * `/unsubscribe#<token>` (diff §4.7 broadcast e-mail; T-0228, TD-601): the opt-out link of a
 * broadcast e-mail. No sign-in. The token is read from the fragment once, kept in memory only
 * and removed from the address bar. Nothing happens on load (link scanners prefetch): only the
 * button posts the token, in the body, never in a URL. The server answers every token alike,
 * so the page always shows the same neutral confirmation.
 */
export function UnsubscribePage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const location = useLocation();
  const navigate = useNavigate();
  const [token] = useState(() => {
    const raw = location.hash.replace(/^#/, "");
    return TOKEN.test(raw) ? raw : null;
  });

  useEffect(() => {
    if (location.hash !== "") void navigate({ hash: "" }, { replace: true });
  }, [location.hash, navigate]);

  const confirm = useMutation({ mutationFn: (value: string) => unsubscribe(api, value) });

  return (
    <AuthCard title={t("unsubscribe.title")}>
      {token === null ? (
        <p role="alert">{t("unsubscribe.linkInvalid")}</p>
      ) : confirm.isSuccess ? (
        <p role="status">{t("unsubscribe.done")}</p>
      ) : (
        <>
          <p>{t("unsubscribe.body")}</p>
          <button
            type="button"
            className="min-h-touch w-full rounded bg-brand px-3 text-on-brand"
            disabled={confirm.isPending}
            onClick={() => { confirm.mutate(token); }}
          >
            {t("unsubscribe.confirm")}
          </button>
          {confirm.isError && <p role="alert">{t("unsubscribe.failed")}</p>}
        </>
      )}
    </AuthCard>
  );
}
