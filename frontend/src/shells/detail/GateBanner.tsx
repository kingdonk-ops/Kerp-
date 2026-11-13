import { useTranslation } from "react-i18next";

export type GateState = "open" | "passed" | "hold" | "blocked";

/** Verification gate of a record, e.g. the latest `run_checks` outcome (server data). */
export interface GateStatus {
  state: GateState;
  /** The gate/transition name, already translated or server-provided. */
  label: string;
  /** Failed checks that hold or block the gate. */
  reasons?: readonly string[];
}

const STYLE: Record<GateState, string> = {
  open: "border-line bg-panel",
  passed: "border-success bg-panel",
  hold: "border-warning bg-panel",
  blocked: "border-danger bg-panel",
};

/** Gate-status banner at the top of a DetailPage; hold/blocked are announced as alerts. */
export function GateBanner({ gate }: { gate: GateStatus }) {
  const { t } = useTranslation();
  const urgent = gate.state === "hold" || gate.state === "blocked";
  return (
    <div
      role={urgent ? "alert" : "status"}
      aria-label={t("shells.detail.gate.title")}
      data-gate-state={gate.state}
      className={`rounded border-l-4 px-4 py-3 ${STYLE[gate.state]}`}
    >
      <p className="font-semibold">
        {t("shells.detail.gate.summary", { state: t(`shells.detail.gate.states.${gate.state}`), label: gate.label })}
      </p>
      {gate.reasons !== undefined && gate.reasons.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-sm">
          {gate.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
