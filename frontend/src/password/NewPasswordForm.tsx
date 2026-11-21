import { useId, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { PolicyRefusal } from "./policy";

export const FIELD = "mt-1 min-h-touch w-full rounded border border-line bg-panel px-2";
export const PRIMARY_BUTTON =
  "min-h-touch w-full rounded bg-brand px-3 font-medium text-on-brand disabled:opacity-60";

interface NewPasswordFormProps {
  submitLabel: string;
  busy: boolean;
  /** The server's last policy refusal; its `min_length` feeds the length hint from then on. */
  refusal: PolicyRefusal | null;
  /** Another failure, already translated. */
  error?: string | null;
  onSubmit: (password: string) => void;
  /** Shown above the fields (e.g. the read-only account details). */
  children?: ReactNode;
}

/**
 * New password + confirmation (diff §4.2 *Local passwords*): no composition rules, paste
 * allowed, a show/hide toggle and a live length hint. The policy itself is the server's: its
 * refusals are shown translated, never the raw `detail`.
 */
export function NewPasswordForm({
  submitLabel,
  busy,
  refusal,
  error,
  onSubmit,
  children,
}: NewPasswordFormProps) {
  const { t } = useTranslation();
  const hintId = useId();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [shown, setShown] = useState(false);
  const [mismatch, setMismatch] = useState(false);
  const count = Array.from(password).length; // code points, as the server counts
  const min = refusal?.minLength ?? null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setMismatch(password !== confirm);
    if (password === confirm) onSubmit(password);
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {children}
      <label className="block">
        <span className="text-sm">{t("password.form.new")}</span>
        <input
          type={shown ? "text" : "password"}
          required
          autoComplete="new-password"
          aria-describedby={hintId}
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
          }}
          className={FIELD}
        />
      </label>
      <p id={hintId} aria-live="polite" className="text-sm text-muted">
        {min === null
          ? t("password.form.length", { n: count })
          : t("password.form.lengthMin", { n: count, min })}
      </p>
      <label className="block">
        <span className="text-sm">{t("password.form.confirm")}</span>
        <input
          type={shown ? "text" : "password"}
          required
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => {
            setConfirm(e.target.value);
          }}
          className={FIELD}
        />
      </label>
      <button
        type="button"
        aria-pressed={shown}
        onClick={() => {
          setShown(!shown);
        }}
        className="min-h-touch rounded border border-line bg-panel px-3"
      >
        {t("password.form.show")}
      </button>
      {(mismatch || refusal !== null || Boolean(error)) && (
        <p role="alert" className="text-sm text-danger">
          {mismatch
            ? t("password.form.mismatch")
            : refusal
              ? t(`password.policy.${refusal.code}`, {
                  min: refusal.minLength ?? "",
                  max: refusal.maxLength ?? "",
                })
              : error}
        </p>
      )}
      <button type="submit" disabled={busy} className={PRIMARY_BUTTON}>
        {submitLabel}
      </button>
    </form>
  );
}
