import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import { useAdminAccess } from "./access";
import { AdminApiError, fetchGrantedUsers, setAdminGrant, type AdminGrantKind } from "./api";
import { SystemTabs } from "./ui";

const KEY = ["admin", "grantedUsers"] as const;
/** The two tenant-level grants (diff §4.1); neither is a persona or confers `A`/`S`. */
const GRANTS = [
  { kind: "system_admin", label: "admin.system.systemAdmin" },
  { kind: "create_projects", label: "admin.system.projectAdmin" },
] as const satisfies readonly { kind: AdminGrantKind; label: string }[];

/** Admin → System → Admin grants: appoint / remove System admins and Project Admins. */
export function SystemGrantsPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const { systemAdmin } = useAdminAccess();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const list = useQuery({ queryKey: KEY, queryFn: () => fetchGrantedUsers(api) });
  const change = useMutation({
    mutationFn: (v: { userId: string; kind: AdminGrantKind; on: boolean }) =>
      setAdminGrant(api, v.userId, v.kind, v.on),
    onSuccess: (res) => {
      setNotice(t(res === "last" ? "admin.system.lastSystemAdmin" : "admin.system.saved"));
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: KEY }),
  });
  const forbidden = list.error instanceof AdminApiError && list.error.status === 403;
  const cell = "border-b border-line p-2 text-left align-top";

  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-bold">{t("admin.system.grantsTitle")}</h1>
      <SystemTabs systemAdmin={systemAdmin} />
      <p>{t("admin.system.grantsNote")}</p>
      <p role="status">{notice}</p>
      {change.isError && <p role="alert">{t("admin.actionError")}</p>}
      {list.isPending && <p>{t("app.loading")}</p>}
      {list.isError && (
        <p role="alert">{t(forbidden ? "admin.system.forbidden" : "admin.loadError")}</p>
      )}
      {list.data && (
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse bg-panel text-sm">
            <thead>
              <tr>
                <th className={cell}>{t("admin.system.user")}</th>
                {GRANTS.map((g) => (
                  <th key={g.kind} className={cell}>
                    {t(g.label)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {list.data.map((u) => (
                <tr key={u.id}>
                  <td className={cell}>
                    {u.display_name}
                    {!u.is_active && ` ${t("admin.system.inactive")}`}
                    <br />
                    <span className="text-muted">{u.email}</span>
                  </td>
                  {GRANTS.map((g) => (
                    <td key={g.kind} className={cell}>
                      <input
                        type="checkbox"
                        className="size-touch"
                        aria-label={t("admin.system.grantLabel", {
                          grant: t(g.label),
                          name: u.display_name,
                        })}
                        checked={u.grants.includes(g.kind)}
                        disabled={change.isPending}
                        onChange={(e) => {
                          change.mutate({ userId: u.id, kind: g.kind, on: e.target.checked });
                        }}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
