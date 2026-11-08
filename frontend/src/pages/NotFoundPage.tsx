import { useTranslation } from "react-i18next";
import { Link } from "react-router";

export function NotFoundPage() {
  const { t } = useTranslation();

  return (
    <section className="space-y-2">
      <h1 className="text-2xl font-bold">{t("notFound.title")}</h1>
      <p>{t("notFound.body")}</p>
      <Link to="/" className="text-blue-700 underline">
        {t("notFound.backHome")}
      </Link>
    </section>
  );
}
