import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { INPUT, PRIMARY } from "../admin/ui";
import { useServices } from "../app/services";
import { lookupCode, type FileResult } from "./api";
import { ProjectEntry } from "./LobbyList";

/**
 * Project-code entry, for every user (diff §4.1, TD-202): an exact code finds one project and
 * its lookup ticket, which the access request then carries (T-0050). A code never grants
 * access; an unknown code gets a neutral message.
 */
export function CodeLookup({ onSent }: { onSent: (result: FileResult) => void }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const headingId = useId();
  const inputId = useId();
  const [code, setCode] = useState("");
  const lookup = useMutation({ mutationFn: (value: string) => lookupCode(api, value) });
  const result = lookup.data;
  const trimmed = code.trim();

  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <h2 id={headingId} className="text-xl font-semibold">
        {t("lobby.code.title")}
      </h2>
      <form
        className="flex flex-wrap items-end gap-touch-gap"
        onSubmit={(event) => {
          event.preventDefault();
          if (trimmed) lookup.mutate(trimmed);
        }}
      >
        <div className="grow">
          <label htmlFor={inputId} className="block">
            {t("lobby.code.label")}
          </label>
          <input
            id={inputId}
            className={INPUT}
            autoComplete="off"
            value={code}
            onChange={(e) => {
              setCode(e.target.value);
            }}
          />
        </div>
        <button type="submit" className={PRIMARY} disabled={!trimmed || lookup.isPending}>
          {t("lobby.code.find")}
        </button>
      </form>
      {lookup.isError && <p role="alert">{t("lobby.actionError")}</p>}
      {result?.kind === "notFound" && <p role="status">{t("lobby.code.notFound")}</p>}
      {result?.kind === "throttled" && <p role="alert">{t("lobby.code.throttled")}</p>}
      {result?.kind === "found" && (
        <ul>
          <ProjectEntry
            key={result.match.lookup_ticket}
            project={result.match}
            lookupTicket={result.match.lookup_ticket}
            onSent={(res) => {
              lookup.reset();
              setCode("");
              onSent(res);
            }}
          />
        </ul>
      )}
    </section>
  );
}
