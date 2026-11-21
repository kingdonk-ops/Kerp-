import { useTranslation } from "react-i18next";

import type { components } from "../api/schema";
import type { GrantIn } from "./api";

export type Persona = components["schemas"]["Persona"];
/** PER-01 … PER-16, in code order (edge spec §5.2). */
export const PERSONAS = Array.from(
  { length: 16 },
  (_, i) => `PER-${String(i + 1).padStart(2, "0")}` as Persona,
);
/** Site-scoped persona: names its site (backend `GrantIn`). */
export const SITE_PERSONA: Persona = "PER-07";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The chosen personas and site as grants; `null` while PER-07 lacks a valid site id. */
export function toGrants(chosen: ReadonlySet<Persona>, site: string): GrantIn[] | null {
  if (chosen.has(SITE_PERSONA) && !UUID.test(site.trim())) return null;
  return PERSONAS.filter((p) => chosen.has(p)).map((persona) =>
    persona === SITE_PERSONA ? { persona, site_node_id: site.trim() } : { persona },
  );
}

/**
 * Persona checkboxes (PER-01…16) and, once PER-07 is chosen, its site (an asset node id until
 * the hierarchy has a picker, TD-394). Personas are the only source of hold or sign-off rights.
 */
export function PersonaFields({
  legend,
  chosen,
  onChange,
  site,
  onSite,
}: {
  legend: string;
  chosen: ReadonlySet<Persona>;
  onChange: (next: ReadonlySet<Persona>) => void;
  site: string;
  onSite: (site: string) => void;
}) {
  const { t } = useTranslation();
  const siteInvalid = chosen.has(SITE_PERSONA) && site !== "" && !UUID.test(site.trim());
  return (
    <>
      <fieldset className="space-y-1">
        <legend className="font-semibold">{legend}</legend>
        {PERSONAS.map((p) => (
          <label key={p} className="flex min-h-touch items-center gap-touch-gap">
            <input
              type="checkbox"
              className="size-touch shrink-0"
              checked={chosen.has(p)}
              onChange={() => {
                const next = new Set(chosen);
                if (!next.delete(p)) next.add(p);
                onChange(next);
              }}
            />
            {t("admin.accessRequests.persona", { code: p, name: t(`personas.${p}`) })}
          </label>
        ))}
      </fieldset>
      {chosen.has(SITE_PERSONA) && (
        <label className="block space-y-1">
          <span>{t("admin.accessRequests.site")}</span>
          <input
            className="block min-h-touch w-full rounded border border-line bg-panel px-2"
            value={site}
            aria-invalid={siteInvalid}
            onChange={(e) => {
              onSite(e.target.value);
            }}
          />
          {siteInvalid && (
            <span className="text-sm text-danger">{t("admin.accessRequests.siteInvalid")}</span>
          )}
        </label>
      )}
    </>
  );
}
