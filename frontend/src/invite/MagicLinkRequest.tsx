import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";

/**
 * The magic-link request on the invite sign-in screen (diff §4.1, §4.2): the `invite_token`
 * goes in the body. Only invites naming PER-14 get a link, decided by the server behind one
 * neutral `202`, so the screen says what to do when no e-mail arrives.
 */
export function MagicLinkRequest({ email, inviteToken }: { email: string; inviteToken: string }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const send = useMutation({
    mutationFn: async () => {
      const { response } = await api.POST("/api/v1/auth/magic-link", {
        body: { email: email.trim(), invite_token: inviteToken },
      });
      if (response.status !== 202) throw new Error(`magic link: ${String(response.status)}`);
    },
  });

  return (
    <section className="space-y-2 border-t border-line pt-4">
      <p className="text-sm text-muted">{t("invite.magicLink.intro")}</p>
      <button
        type="button"
        disabled={!email.includes("@") || send.isPending}
        onClick={() => {
          send.mutate();
        }}
        className="min-h-touch w-full rounded border border-line bg-panel px-3 font-medium text-ink disabled:opacity-60"
      >
        {t("invite.magicLink.submit")}
      </button>
      {send.isSuccess && <p role="status">{t("invite.magicLink.sent")}</p>}
      {send.isError && (
        <p role="alert" className="text-sm text-danger">
          {t("invite.magicLink.failed")}
        </p>
      )}
    </section>
  );
}
