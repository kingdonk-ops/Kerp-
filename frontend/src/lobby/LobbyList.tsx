import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { BUTTON, PRIMARY } from "../admin/ui";
import { useServices } from "../app/services";
import { fetchLobby, LOBBY_KEY, type FileResult, type LobbyProject } from "./api";
import { RequestForm } from "./RequestForm";
import { useOpenProject } from "./useOpenProject";

/**
 * The lobby list (T-0040, diff §4.1): the lobby-visible projects (code, name), shown to
 * **internal** users only; the caller mounts it only for them, so external users never ask.
 */
export function LobbyList({ onSent }: { onSent: (result: FileResult) => void }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const headingId = useId();
  const list = useQuery({ queryKey: LOBBY_KEY, queryFn: () => fetchLobby(api) });

  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <h2 id={headingId} className="text-xl font-semibold">
        {t("lobby.list.title")}
      </h2>
      {list.isPending && <p>{t("app.loading")}</p>}
      {list.isError && <p role="alert">{t("lobby.loadError")}</p>}
      {list.data?.length === 0 && <p>{t("lobby.list.empty")}</p>}
      <ul className="space-y-2">
        {list.data?.map((p) => (
          <ProjectEntry key={p.id} project={p} onSent={onSent} />
        ))}
      </ul>
    </section>
  );
}

/** One found project: 'Open' for a member, else 'Request access' (with the code's ticket). */
export function ProjectEntry({
  project,
  lookupTicket,
  onSent,
}: {
  project: LobbyProject;
  lookupTicket?: string;
  onSent: (result: FileResult) => void;
}) {
  const { t } = useTranslation();
  const open = useOpenProject();
  const [requesting, setRequesting] = useState(false);
  const label = t("lobby.project", { code: project.code, name: project.name });

  return (
    <li aria-label={label} className="space-y-2 rounded border border-line bg-panel p-3">
      <p className="font-semibold">{label}</p>
      {project.member ? (
        <button
          type="button"
          className={PRIMARY}
          onClick={() => {
            void open(project.id);
          }}
        >
          {t("lobby.open")}
        </button>
      ) : requesting ? (
        <RequestForm
          projectId={project.id}
          {...(lookupTicket ? { lookupTicket } : {})}
          onDone={(res) => {
            setRequesting(false);
            onSent(res);
          }}
          onCancel={() => {
            setRequesting(false);
          }}
        />
      ) : (
        <button
          type="button"
          className={BUTTON}
          onClick={() => {
            setRequesting(true);
          }}
        >
          {t("lobby.requestAccess")}
        </button>
      )}
    </li>
  );
}
