import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import type { components } from "../api/schema";
import { useServices } from "../app/services";
import {
  AdminApiError,
  approveRequest,
  fetchPending,
  rejectRequest,
  type DecisionResult,
  type GrantIn,
  type PendingRequest,
} from "./api";
import { BUTTON, Dialog, formatAge, PRIMARY } from "./ui";

type Persona = components["schemas"]["Persona"];
/** PER-01 … PER-16, in code order (edge spec §5.2). */
const PERSONAS = Array.from(
  { length: 16 },
  (_, i) => `PER-${String(i + 1).padStart(2, "0")}` as Persona,
);
/** Site-scoped persona: names its site (backend `GrantIn`). */
const SITE_PERSONA: Persona = "PER-07";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const REASON_MAX = 500;
const KEY = ["accessRequests", "pending"] as const;

interface Decision { kind: "approve" | "reject"; request: PendingRequest }

/** Admin → Access requests (diff §4.1, §6.1): pending requests the caller may decide. */
export function AccessRequestsPage() {
  const { t, i18n } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState<Decision | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const list = useQuery({ queryKey: KEY, queryFn: () => fetchPending(api) });

  const decided = (result: DecisionResult, request: PendingRequest, kind: Decision["kind"]) => {
    if (result === "sod") return;
    setOpen(null);
    setNotice(
      result === "gone"
        ? t("admin.accessRequests.alreadyDecided")
        : t(kind === "approve" ? "admin.accessRequests.approved" : "admin.accessRequests.rejected"),
    );
    queryClient.setQueryData<PendingRequest[]>(KEY, (rows) =>
      rows?.filter((r) => r.id !== request.id),
    );
    void queryClient.invalidateQueries({ queryKey: KEY });
  };

  const forbidden = list.error instanceof AdminApiError && list.error.status === 403;
  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-bold">{t("admin.accessRequests.title")}</h1>
      <p role="status">{notice}</p>
      {open?.kind === "approve" && (
        <ApproveDialog
          request={open.request}
          onClose={() => { setOpen(null); }}
          onDone={(res) => { decided(res, open.request, "approve"); }}
        />
      )}
      {open?.kind === "reject" && (
        <RejectDialog
          request={open.request}
          onClose={() => { setOpen(null); }}
          onDone={(res) => { decided(res, open.request, "reject"); }}
        />
      )}
      {list.isPending && <p>{t("app.loading")}</p>}
      {list.isError && <p role="alert">{t(forbidden ? "admin.forbidden" : "admin.loadError")}</p>}
      {list.data?.length === 0 && <p>{t("admin.accessRequests.empty")}</p>}
      <ul className="space-y-2">
        {list.data?.map((r) => (
          <li
            key={r.id}
            aria-label={r.requester.display_name}
            className="space-y-1 rounded border border-line bg-panel p-3"
          >
            <p className="font-semibold">
              {t("admin.accessRequests.requester", {
                name: r.requester.display_name,
                email: r.requester.email,
              })}
            </p>
            <p>{t("admin.accessRequests.project", { code: r.project_code, name: r.project_name })}</p>
            <p className="whitespace-pre-wrap">{r.message ?? t("admin.accessRequests.noMessage")}</p>
            <p className="text-sm text-muted">
              {t("admin.accessRequests.requested", { age: formatAge(r.created_at, i18n.language) })}
            </p>
            <div className="flex flex-wrap gap-touch-gap">
              <button
                type="button"
                className={PRIMARY}
                onClick={() => {
                  setOpen({ kind: "approve", request: r });
                }}
              >
                {t("admin.accessRequests.approve")}
              </button>
              <button
                type="button"
                className={BUTTON}
                onClick={() => {
                  setOpen({ kind: "reject", request: r });
                }}
              >
                {t("admin.accessRequests.reject")}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

interface DialogProps {
  request: PendingRequest;
  onClose: () => void;
  onDone: (result: DecisionResult) => void;
}

function ApproveDialog({ request, onClose, onDone }: DialogProps) {
  const { t } = useTranslation();
  const { api } = useServices();
  const [chosen, setChosen] = useState<ReadonlySet<Persona>>(new Set());
  const [site, setSite] = useState("");
  const [sod, setSod] = useState(false);
  const needsSite = chosen.has(SITE_PERSONA);
  const siteValid = !needsSite || UUID.test(site.trim());
  const grants: GrantIn[] = PERSONAS.filter((p) => chosen.has(p)).map((persona) =>
    persona === SITE_PERSONA ? { persona, site_node_id: site.trim() } : { persona },
  );
  const approve = useMutation({
    mutationFn: (confirmSod: boolean) => approveRequest(api, request.id, grants, confirmSod),
    onSuccess: (res) => {
      if (res === "sod") setSod(true);
      onDone(res);
    },
  });
  const toggle = (p: Persona) => {
    const next = new Set(chosen);
    if (!next.delete(p)) next.add(p);
    setChosen(next);
    setSod(false);
  };
  const project = t("admin.accessRequests.project", {
    code: request.project_code,
    name: request.project_name,
  });


  return (
    <Dialog
      title={t("admin.accessRequests.approveTitle", { name: request.requester.display_name })}
      onClose={onClose}
      actions={
        sod ? (
          <button
            type="button"
            className={PRIMARY}
            disabled={approve.isPending}
            onClick={() => {
              approve.mutate(true);
            }}
          >
            {t("admin.accessRequests.confirmSod")}
          </button>
        ) : (
          <button
            type="button"
            className={PRIMARY}
            disabled={grants.length === 0 || !siteValid || approve.isPending}
            onClick={() => {
              approve.mutate(false);
            }}
          >
            {t("admin.accessRequests.approve")}
          </button>
        )
      }
    >
      <fieldset className="space-y-1">
        <legend className="font-semibold">
          {t("admin.accessRequests.personasLegend", { project })}
        </legend>
        {PERSONAS.map((p) => (
          <label key={p} className="flex min-h-touch items-center gap-touch-gap">
            <input
              type="checkbox"
              className="size-touch shrink-0"
              checked={chosen.has(p)}
              onChange={() => {
                toggle(p);
              }}
            />
            {t("admin.accessRequests.persona", { code: p, name: t(`personas.${p}`) })}
          </label>
        ))}
      </fieldset>
      {needsSite && (
        <label className="block space-y-1">
          <span>{t("admin.accessRequests.site")}</span>
          <input
            className="block min-h-touch w-full rounded border border-line bg-panel px-2"
            value={site}
            aria-invalid={!siteValid}
            onChange={(e) => {
              setSite(e.target.value);
            }}
          />
          {!siteValid && site !== "" && (
            <span className="text-sm text-danger">{t("admin.accessRequests.siteInvalid")}</span>
          )}
        </label>
      )}
      {approve.isError && <p role="alert">{t("admin.actionError")}</p>}
      {sod && (
        <p role="alert" className="rounded border-2 border-warning p-2">
          {t("admin.accessRequests.sodWarning")}
        </p>
      )}
    </Dialog>
  );
}

function RejectDialog({ request, onClose, onDone }: DialogProps) {
  const { t } = useTranslation();
  const { api } = useServices();
  const hintId = useId();
  const [reason, setReason] = useState("");
  const reject = useMutation({
    mutationFn: () => rejectRequest(api, request.id, reason),
    onSuccess: onDone,
  });
  return (
    <Dialog
      title={t("admin.accessRequests.rejectTitle", { name: request.requester.display_name })}
      onClose={onClose}
      actions={
        <button
          type="button"
          className={PRIMARY}
          disabled={reject.isPending}
          onClick={() => {
            reject.mutate();
          }}
        >
          {t("admin.accessRequests.reject")}
        </button>
      }
    >
      <label className="block space-y-1">
        <span>{t("admin.accessRequests.reason")}</span>
        <textarea
          className="block w-full rounded border border-line bg-panel p-2"
          rows={4}
          maxLength={REASON_MAX}
          value={reason}
          aria-describedby={hintId}
          onChange={(e) => {
            setReason(e.target.value);
          }}
        />
      </label>
      <p id={hintId} className="text-sm">
        {t("admin.accessRequests.reasonHint")}{" "}
        <span aria-live="polite">
          {t("admin.accessRequests.reasonCount", { count: reason.length })}
        </span>
      </p>
      {reject.isError && <p role="alert">{t("admin.actionError")}</p>}
    </Dialog>
  );
}
