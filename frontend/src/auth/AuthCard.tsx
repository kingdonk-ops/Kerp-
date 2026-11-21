import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { ThemeToggle } from "../theme/ThemeToggle";

export const TEXT_LINK = "inline-flex min-h-touch items-center text-brand underline";

/** The centred card of the screens outside the shell (sign-in, invite, password). */
export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-surface p-4 text-ink">
      <div className="w-full max-w-sm space-y-4 rounded-lg border border-line bg-panel p-6 shadow-sm">
        <p className="text-lg font-semibold text-brand">{t("app.name")}</p>
        <h1 className="text-2xl font-bold">{title}</h1>
        {children}
      </div>
      <ThemeToggle />
    </main>
  );
}

/** A text link of these screens, 48 px tall (glove target). */
export function CardLink({
  to,
  state,
  onClick,
  children,
}: {
  to: string;
  state?: unknown;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <Link to={to} state={state} onClick={onClick} className={TEXT_LINK}>
      {children}
    </Link>
  );
}
