import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { BUTTON, PRIMARY } from "../admin/ui";
import { useServices } from "../app/services";
import { useProjectScope } from "../projects/ProjectScopeProvider";
import {
  fetchMine,
  fileRequest,
  MINE_KEY,
  withdrawRequest,
  type MyRequest,
  type RequestStatus,
} from "./api";
import { useAvailableFrom, useHasPassed, useResultMessage } from "./messages";
import { useOpenProject } from "./useOpenProject";

const CHIP: Record<RequestStatus, string> = {
  pending: "border-warning",
  approved: "border-success",
  rejected: "border-danger",
  withdrawn: "border-line",
};

/**
 * The lobby's **Active requests** panel (diff §4.1, T-0050): the requester's only outcome
 * channel, since project notifications cannot reach a non-member. Reloaded whenever the lobby
 * opens and whenever the window regains focus.
 */
export function ActiveRequests({ notify }: { notify: (message: string) => void }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const headingId = useId();
  const mine = useQuery({
    queryKey: MINE_KEY,
    queryFn: () => fetchMine(api),
    refetchOnMount: "always",
  });
  const { refetch } = mine;

  useEffect(() => {
    const onFocus = () => {
      void refetch();
    };
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
    };
  }, [refetch]);

  // An approval shown here that the picker does not have yet reloads the picker once.
  const { projects } = useProjectScope();
  const reloadedFor = useRef(new Set<string>());
  useEffect(() => {
    const missing = (mine.data ?? []).filter(
      (r) =>
        r.status === "approved" &&
        !projects.some((p) => p.id === r.project_id) &&
        !reloadedFor.current.has(r.id),
    );
    if (missing.length === 0) return;
    for (const r of missing) reloadedFor.current.add(r.id);
    void queryClient.invalidateQueries({ queryKey: ["projects", "mine"] });
  }, [mine.data, projects, queryClient]);

  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <h2 id={headingId} className="text-xl font-semibold">
        {t("lobby.requests.title")}
      </h2>
      {mine.isPending && <p>{t("app.loading")}</p>}
      {mine.isError && <p role="alert">{t("lobby.loadError")}</p>}
      {mine.data?.length === 0 && <p>{t("lobby.requests.empty")}</p>}
      <ul className="space-y-2">
        {mine.data?.map((r) => (
          <RequestCard key={r.id} request={r} notify={notify} />
        ))}
      </ul>
    </section>
  );
}

function RequestCard({ request, notify }: { request: MyRequest; notify: (message: string) => void }) {
  const { t } = useTranslation();
  const label = t("lobby.project", { code: request.project_code, name: request.project_name });
  return (
    <li aria-label={label} className="space-y-1 rounded border border-line bg-panel p-3">
      <p className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{label}</span>
        <span className={`rounded-full border-2 px-2 text-sm ${CHIP[request.status]}`}>
          {t(`lobby.requests.status.${request.status}`)}
        </span>
      </p>
      {request.status === "rejected" && request.rejection_reason && (
        <p className="whitespace-pre-wrap">
          {t("lobby.requests.reason", { reason: request.rejection_reason })}
        </p>
      )}
      <Actions request={request} notify={notify} />
    </li>
  );
}

function Actions({ request, notify }: { request: MyRequest; notify: (message: string) => void }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const open = useOpenProject();
  const describe = useResultMessage();
  const availableFrom = useAvailableFrom();
  const [refusal, setRefusal] = useState<string | null>(null);
  // Withdrawn requests have no cooldown (`request_again_at` is null).
  const allowed = useHasPassed(request.status === "rejected" ? request.request_again_at : null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: MINE_KEY });

  const withdraw = useMutation({
    mutationFn: () => withdrawRequest(api, request.id),
    onSuccess: async (done) => {
      if (done) notify(t("lobby.requests.withdrawn"));
      await refresh();
    },
  });
  // A new request after a rejected or withdrawn one needs no lookup ticket (T-0050).
  const again = useMutation({
    mutationFn: () => fileRequest(api, { projectId: request.project_id }),
    onSuccess: async (res) => {
      if (res.kind === "ok") notify(t("lobby.sent"));
      else setRefusal(describe(res));
      await refresh();
    },
  });

  const failed = withdraw.isError || again.isError;
  return (
    <>
      {request.status === "pending" && (
        <button
          type="button"
          className={BUTTON}
          disabled={withdraw.isPending}
          onClick={() => {
            withdraw.mutate();
          }}
        >
          {t("lobby.requests.withdraw")}
        </button>
      )}
      {request.status === "approved" && (
        <button
          type="button"
          className={PRIMARY}
          onClick={() => {
            void open(request.project_id);
          }}
        >
          {t("lobby.requests.openProject")}
        </button>
      )}
      {(request.status === "rejected" || request.status === "withdrawn") && (
        <div className="space-y-1">
          <button
            type="button"
            className={BUTTON}
            disabled={!allowed || again.isPending}
            onClick={() => {
              setRefusal(null);
              again.mutate();
            }}
          >
            {t("lobby.requests.requestAgain")}
          </button>
          {!allowed && <p className="text-sm">{availableFrom(request.request_again_at)}</p>}
        </div>
      )}
      {refusal && <p role="alert">{refusal}</p>}
      {failed && <p role="alert">{t("lobby.actionError")}</p>}
    </>
  );
}
