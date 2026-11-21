import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { formatTime } from "../admin/ui";
import type { FileResult } from "./api";

// `setTimeout` takes at most 2^31 - 1 ms (~24.8 days); longer waits re-arm.
const MAX_DELAY_MS = 2_147_483_647;

/** "available from <local time>" of a cooldown end, in the UI language. */
export function useAvailableFrom(): (iso: string | null) => string {
  const { t, i18n } = useTranslation();
  return useCallback(
    (iso: string | null) =>
      iso
        ? t("lobby.requests.availableFrom", { time: formatTime(iso, i18n.language) ?? "" })
        : t("lobby.actionError"),
    [t, i18n.language],
  );
}

/** The user-facing message of a filing result (a refusal, or "sent" for `ok`). */
export function useResultMessage(): (result: FileResult) => string {
  const { t } = useTranslation();
  const availableFrom = useAvailableFrom();
  return useCallback(
    (result: FileResult) => {
      switch (result.kind) {
        case "cooldown":
          return availableFrom(result.retryAfter);
        case "pending":
          return t("lobby.requests.alreadyPending");
        case "member":
          return t("lobby.requests.alreadyMember");
        case "expired":
          return t("lobby.code.expired");
        case "unavailable":
          return t("lobby.unavailable");
        case "ok":
          return t("lobby.sent");
      }
    },
    [t, availableFrom],
  );
}

/**
 * `true` once `iso` has passed (or when it is absent or not a valid time: the server still
 * enforces the cooldown with a 409); re-renders at that moment.
 */
export function useHasPassed(iso: string | null): boolean {
  const parsed = iso ? new Date(iso).getTime() : 0;
  const until = Number.isFinite(parsed) ? parsed : 0;
  const [now, setNow] = useState(() => Date.now());
  const passed = now >= until;
  useEffect(() => {
    if (passed) return;
    const timer = setTimeout(
      () => {
        setNow(Date.now());
      },
      Math.min(until - Date.now(), MAX_DELAY_MS),
    );
    return () => {
      clearTimeout(timer);
    };
  }, [passed, until, now]);
  return passed;
}
