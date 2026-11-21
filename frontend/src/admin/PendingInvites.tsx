import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import { AdminApiError } from "./api";
import { fetchInvites, resendInvite, revokeInvite, type OpenInvite, type SentInvite } from "./invites";
import { BUTTON, Dialog, formatTime, INPUT, PRIMARY } from "./ui";

type Action = "resend" | "revoke";

/** Re-renders when the earliest future `resend_available_at` passes, so Resend enables itself. */
function useNow(invites: readonly OpenInvite[] | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const next = (invites ?? [])
      .map((i) => (i.resend_available_at ? Date.parse(i.resend_available_at) : NaN))
      .filter((at) => at > now)
      .sort((a, b) => a - b)[0];
    if (next === undefined) return;
    const timer = setTimeout(() => {
      setNow(Date.now());
    }, next - now + 50);
    return () => {
      clearTimeout(timer);
    };
  }, [invites, now]);
  return now;
}

/**
 * Open invitations of a project on its members screen (diff §4.1 Re-send; T-0052 API): the
 * PER-09s of the project and System admins see pending and expired (never accepted) invites
 * with **Resend** (after a confirmation; the new link is shown once, with `invite_token` only)
 * and **Revoke**. Accepted and revoked invites are not listed, so they offer no Resend.
 */
export function PendingInvites({ projectId }: { projectId: string }) {
  const { t, i18n } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const key = ["admin", "invites", projectId];
  const invites = useQuery({ queryKey: key, queryFn: () => fetchInvites(api, projectId) });
  const now = useNow(invites.data);
  const [confirming, setConfirming] = useState<{ invite: OpenInvite; action: Action } | null>(null);
  // The new link lives only here until closed: never cached, stored or logged.
  const [sent, setSent] = useState<SentInvite | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const time = (iso: string | null) => formatTime(iso, i18n.language) ?? "";
  const headingId = useId();
  const copy = async (link: string) => {
    try {
      await navigator.clipboard.writeText(link);
      setNotice(t("invite.panel.copied"));
    } catch {
      // No clipboard access: the link stays selectable in its field.
    }
  };

  const act = useMutation({
    mutationFn: async ({ invite, action }: { invite: OpenInvite; action: Action }) =>
      action === "resend"
        ? resendInvite(api, projectId, invite.id)
        : { kind: await revokeInvite(api, projectId, invite.id) },
    onSuccess: (result, { invite }) => {
      setConfirming(null);
      if (result.kind === "sent") {
        setSent(result.invite);
        setNotice(t("invite.panel.resent", { email: invite.email }));
      } else if (result.kind === "throttled") {
        setNotice(
          result.retryAt
            ? t("invite.panel.throttled", { time: time(result.retryAt.toISOString()) })
            : t("invite.panel.throttledLater"),
        );
      } else {
        setNotice(t(result.kind === "ok" ? "invite.panel.revoked" : "invite.panel.gone"));
      }
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: key }),
  });

  // A member without PER-09 is told so by the members list above.
  if (invites.error instanceof AdminApiError && invites.error.status === 403) return null;

  return (
    <section className="space-y-2" aria-labelledby={headingId}>
      <h2 id={headingId} className="text-xl font-semibold">
        {t("invite.panel.title")}
      </h2>
      <p role="status">{notice}</p>
      {act.isError && <p role="alert">{t("admin.actionError")}</p>}
      {sent && (
        <div className="space-y-2 rounded border-2 border-brand bg-panel p-3">
          <label className="block space-y-1">
            <span>{t("invite.panel.link")}</span>
            <input readOnly className={INPUT} value={sent.invite_link} onFocus={(e) => { e.target.select(); }} />
          </label>
          <div className="flex flex-wrap gap-touch-gap">
            <button
              type="button"
              className={BUTTON}
              onClick={() => {
                void copy(sent.invite_link);
              }}
            >
              {t("invite.panel.copy")}
            </button>
            <button type="button" className={BUTTON} onClick={() => { setSent(null); }}>
              {t("invite.panel.close")}
            </button>
          </div>
        </div>
      )}
      {confirming && (
        <Dialog
          title={t(
            confirming.action === "resend" ? "invite.panel.resendTitle" : "invite.panel.revokeTitle",
            { email: confirming.invite.email },
          )}
          onClose={() => { setConfirming(null); }}
          actions={
            <button
              type="button"
              className={PRIMARY}
              disabled={act.isPending}
              onClick={() => { act.mutate(confirming); }}
            >
              {t(confirming.action === "resend" ? "invite.panel.resend" : "invite.panel.revoke")}
            </button>
          }
        >
          <p>
            {t(confirming.action === "resend" ? "invite.panel.resendBody" : "invite.panel.revokeBody")}
          </p>
        </Dialog>
      )}
      {invites.isPending && <p>{t("app.loading")}</p>}
      {invites.isError && <p role="alert">{t("admin.loadError")}</p>}
      {invites.data?.length === 0 && <p>{t("invite.panel.empty")}</p>}
      <ul className="space-y-2">
        {invites.data?.map((invite) => {
          const availableAt = invite.resend_available_at;
          const waiting = availableAt !== null && Date.parse(availableAt) > now;
          return (
            <li key={invite.id} aria-label={invite.email} className="space-y-1 rounded border border-line bg-panel p-3">
              <p className="font-semibold">
                {invite.email}
                {invite.status === "expired" && ` · ${t("invite.panel.expired")}`}
              </p>
              <ul className="text-sm">
                {invite.grants.map((g) => (
                  <li key={`${g.persona}-${g.site_node_id ?? ""}`}>
                    {t("admin.accessRequests.persona", { code: g.persona, name: t(`personas.${g.persona}`) })}
                    {g.site_node_id && ` · ${t("admin.members.site", { site: g.site_node_id })}`}
                  </li>
                ))}
              </ul>
              <p className="text-sm">
                {t("invite.panel.expires", { time: time(invite.expires_at) })} ·{" "}
                {t("invite.panel.lastSent", { time: time(invite.last_sent_at) })}
              </p>
              {waiting && <p className="text-sm">{t("invite.panel.resendFrom", { time: time(availableAt) })}</p>}
              <div className="flex flex-wrap gap-touch-gap">
                <button
                  type="button"
                  className={BUTTON}
                  disabled={waiting}
                  onClick={() => { setConfirming({ invite, action: "resend" }); }}
                >
                  {t("invite.panel.resend")}
                </button>
                <button
                  type="button"
                  className={BUTTON}
                  onClick={() => { setConfirming({ invite, action: "revoke" }); }}
                >
                  {t("invite.panel.revoke")}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
