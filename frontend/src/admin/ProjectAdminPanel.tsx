import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { useServices } from "../app/services";
import { useAdminAccess } from "./access";
import { AdminApiError, type Project } from "./api";
import { deleteProject, setLegalHold } from "./audit";
import type { ActionResult } from "./system";
import { BUTTON, Dialog, INPUT, PRIMARY } from "./ui";

/** Legal-hold reasons are 1-500 characters after trimming (T-0223). */
const MAX_REASON = 500;

/**
 * The project admin page's project section (diff §4.7, §6.5; T-0223): the legal-hold state,
 * its toggle with a mandatory reason for System admins only, and Delete project, refused with
 * `LEGAL_HOLD_ACTIVE` while the project is on hold.
 */
export function ProjectAdminPanel({ project, label }: { project: Project; label: string }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const access = useAdminAccess();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const titleId = useId();
  const [open, setOpen] = useState<"hold" | "delete" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const on = project.legal_hold;
  const hold = useMutation({
    mutationFn: (reason: string) => setLegalHold(api, project.id, !on, reason),
    onSuccess: (res) => { setNotice(t(res.legal_hold ? "admin.projectAdmin.holdSet" : "admin.projectAdmin.holdCleared")); },
    onError: (e) => { setNotice(refusal(e)); },
    onSettled: () => {
      setOpen(null);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => deleteProject(api, project.id),
    onSuccess: (res: ActionResult) => {
      if (res.kind === "ok") {
        void queryClient.invalidateQueries({ queryKey: ["projects"] });
        void navigate("/backbone/projects");
        return;
      }
      const conflicts = t("admin.projectAdmin.conflicts", { returnObjects: true }) as Record<string, string>;
      setNotice(res.kind === "gone" ? t("admin.projectAdmin.gone") : (conflicts[res.code] ?? t("admin.actionError")));
    },
    onError: (e) => { setNotice(refusal(e)); },
    onSettled: () => { setOpen(null); },
  });

  function refusal(e: unknown): string {
    if (e instanceof AdminApiError && e.status === 403) return t("admin.projectAdmin.forbidden");
    if (e instanceof AdminApiError && e.status === 404) return t("admin.projectAdmin.gone");
    return t("admin.actionError");
  }
  const ask = (which: "hold" | "delete") => {
    setNotice(null);
    setOpen(which);
  };

  return (
    <section aria-labelledby={titleId} className="space-y-2 rounded border border-line p-3">
      <h2 id={titleId} className="text-xl font-semibold">
        {t("admin.projectAdmin.title")}
      </h2>
      <p>
        <span className="font-semibold">{t("admin.projectAdmin.legalHold")}: </span>
        {t(on ? "admin.projectAdmin.holdOn" : "admin.projectAdmin.holdOff")}
      </p>
      {notice && <p role="status">{notice}</p>}
      {open === "hold" && (
        <HoldDialog
          on={on}
          label={label}
          pending={hold.isPending}
          onClose={() => { setOpen(null); }}
          onConfirm={(reason) => { hold.mutate(reason); }}
        />
      )}
      {open === "delete" && (
        <Dialog
          title={t("admin.projectAdmin.deleteTitle", { project: label })}
          onClose={() => { setOpen(null); }}
          actions={
            <button type="button" className={PRIMARY} disabled={remove.isPending} onClick={() => { remove.mutate(); }}>
              {t("admin.projectAdmin.delete")}
            </button>
          }
        >
          <p>{t("admin.projectAdmin.deleteBody")}</p>
        </Dialog>
      )}
      <div className="flex flex-wrap gap-touch-gap">
        {access.systemAdmin && (
          <button type="button" className={BUTTON} onClick={() => { ask("hold"); }}>
            {t(on ? "admin.projectAdmin.clearHold" : "admin.projectAdmin.setHold")}
          </button>
        )}
        <button type="button" className={BUTTON} onClick={() => { ask("delete"); }}>
          {t("admin.projectAdmin.delete")}
        </button>
      </div>
    </section>
  );
}

function HoldDialog({
  on,
  label,
  pending,
  onClose,
  onConfirm,
}: {
  on: boolean;
  label: string;
  pending: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState("");
  const action = t(on ? "admin.projectAdmin.clearHold" : "admin.projectAdmin.setHold");
  return (
    <Dialog
      title={t(on ? "admin.projectAdmin.clearTitle" : "admin.projectAdmin.setTitle", { project: label })}
      onClose={onClose}
      actions={
        <button
          type="button"
          className={PRIMARY}
          disabled={reason.trim() === "" || pending}
          onClick={() => { onConfirm(reason); }}
        >
          {action}
        </button>
      }
    >
      <label className="block space-y-1">
        <span>{t("admin.projectAdmin.reason")}</span>
        <input
          type="text"
          className={INPUT}
          required
          maxLength={MAX_REASON}
          value={reason}
          onChange={(e) => { setReason(e.target.value); }}
        />
      </label>
    </Dialog>
  );
}
