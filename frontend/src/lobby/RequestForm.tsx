import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { BUTTON, PRIMARY } from "../admin/ui";
import { useServices } from "../app/services";
import { fileRequest, type FileResult } from "./api";
import { useResultMessage } from "./messages";

const MESSAGE_MAX = 1000;

/**
 * "Request access" with an optional message (diff §4.1). `lookupTicket`: the code lookup's
 * ticket, sent with a request for a project found by code (T-0050); none from the lobby list.
 */
export function RequestForm({
  projectId,
  lookupTicket,
  onDone,
  onCancel,
}: {
  projectId: string;
  lookupTicket?: string;
  onDone: (result: FileResult) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const { api } = useServices();
  const messageId = useId();
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<FileResult | null>(null);
  const describe = useResultMessage();
  const send = useMutation({
    mutationFn: () => fileRequest(api, { projectId, message, ...(lookupTicket ? { lookupTicket } : {}) }),
    onSuccess: (res) => {
      if (res.kind === "ok") onDone(res);
      else setResult(res);
    },
  });

  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        send.mutate();
      }}
    >
      <label htmlFor={messageId} className="block">
        {t("lobby.message")}
      </label>
      <textarea
        id={messageId}
        className="block w-full rounded border border-line bg-panel p-2"
        rows={3}
        maxLength={MESSAGE_MAX}
        value={message}
        onChange={(e) => {
          setMessage(e.target.value);
        }}
      />
      {send.isError && <p role="alert">{t("lobby.actionError")}</p>}
      {result && <p role="alert">{describe(result)}</p>}
      <div className="flex flex-wrap gap-touch-gap">
        <button type="submit" className={PRIMARY} disabled={send.isPending}>
          {t("lobby.send")}
        </button>
        <button type="button" className={BUTTON} onClick={onCancel}>
          {t("lobby.cancel")}
        </button>
      </div>
    </form>
  );
}
