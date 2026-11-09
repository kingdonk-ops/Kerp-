import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

export function HomePage() {
  const { t } = useTranslation();
  // Placeholder query proving the TanStack Query provider is wired; replaced once
  // the generated API client exists.
  const ready = useQuery({ queryKey: ["app", "ready"], queryFn: () => Promise.resolve(true) });

  return (
    <section className="space-y-2">
      <h1 className="text-2xl font-bold">{t("home.title")}</h1>
      <p>{t("home.intro")}</p>
      <p className="text-sm text-slate-500" role="status">
        {ready.isSuccess ? t("home.queryStatus.ready") : t("home.queryStatus.loading")}
      </p>
    </section>
  );
}
