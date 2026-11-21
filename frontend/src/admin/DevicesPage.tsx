import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import { useAuth } from "../auth/AuthProvider";
import { useAdminAccess } from "./access";
import { fetchAdminDevices, fetchMyDevices, revokeDevice, type AdminDevice } from "./api";
import { Dialog, formatTime, PRIMARY } from "./ui";

/** Admin → System → Devices (diff §4.3): System admin all users, PER-09 its projects' members. */
export function AdminDevicesPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const access = useAdminAccess();
  const key = ["devices", "admin", access.systemAdmin, access.adminProjectIds];
  const list = useQuery({
    queryKey: key,
    enabled: !access.isLoading && access.projectAdmin,
    queryFn: () => fetchAdminDevices(api, access.systemAdmin, access.adminProjectIds),
  });
  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-bold">{t("devices.adminTitle")}</h1>
      {!access.isLoading && !access.projectAdmin ? (
        <p role="alert">{t("admin.forbidden")}</p>
      ) : (
        <DeviceTable queryKey={key} rows={list.data} isError={list.isError} withUser />
      )}
    </section>
  );
}

/** Profile → My devices (diff §4.3): the signed-in user's own devices. */
export function MyDevicesPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const { user } = useAuth();
  const key = ["devices", "mine", user?.id];
  const list = useQuery({ queryKey: key, queryFn: () => fetchMyDevices(api) });
  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-bold">{t("devices.myTitle")}</h1>
      <DeviceTable queryKey={key} rows={list.data} isError={list.isError} withUser={false} />
    </section>
  );
}

function DeviceTable({
  queryKey,
  rows,
  isError,
  withUser,
}: {
  queryKey: QueryKey;
  rows: AdminDevice[] | undefined;
  isError: boolean;
  withUser: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [revoking, setRevoking] = useState<AdminDevice | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const time = (iso: string | null) => formatTime(iso, i18n.language) ?? t("devices.never");

  if (isError) return <p role="alert">{t("admin.loadError")}</p>;
  if (!rows) return <p>{t("app.loading")}</p>;
  if (rows.length === 0) return <p>{t("devices.empty")}</p>;
  const cell = "border-b border-line p-2 text-left align-top";
  return (
    <>
      <p role="status">{notice}</p>
      {revoking && (
        <RevokeDialog
          device={revoking}
          onClose={() => { setRevoking(null); }}
          onRevoked={(done) => {
            setRevoking(null);
            setNotice(t("devices.revoked"));
            queryClient.setQueryData<AdminDevice[]>(queryKey, (old) =>
              old?.map((d) => (d.device_id === done.device_id ? { ...d, ...done } : d)),
            );
          }}
        />
      )}
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse bg-panel text-sm">
          <thead>
            <tr>
              {withUser && <th className={cell}>{t("devices.user")}</th>}
              <th className={cell}>{t("devices.platform")}</th>
              <th className={cell}>{t("devices.appVersion")}</th>
              <th className={cell}>{t("devices.lastSeen")}</th>
              <th className={cell}>{t("devices.lastSync")}</th>
              <th className={cell}>{t("devices.status")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.device_id}>
                {withUser && (
                  <td className={cell}>
                    {d.user?.display_name}
                    <br />
                    <span className="text-muted">{d.user?.email}</span>
                  </td>
                )}
                <td className={cell}>
                  {t("devices.platformOs", {
                    platform: t(`devices.platforms.${d.platform}`),
                    os: d.os_version,
                  })}
                </td>
                <td className={cell}>{d.app_version}</td>
                <td className={cell}>{time(d.last_seen_at)}</td>
                <td className={cell}>{time(d.last_sync_at)}</td>
                <td className={cell}>
                  {d.status === "active" ? (
                    <div className="flex flex-wrap items-center gap-touch-gap">
                      {t("devices.active")}
                      <button
                        type="button"
                        className={PRIMARY}
                        onClick={() => {
                          setRevoking(d);
                        }}
                      >
                        {t("devices.revoke")}
                      </button>
                    </div>
                  ) : d.wipe_confirmed_at ? (
                    t("devices.revokedWiped", { time: time(d.wipe_confirmed_at) })
                  ) : (
                    t("devices.revokedWipePending")
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/**
 * Revoke confirmation (diff §4.3): the last-sync time and the wipe of unsynced work; a PER-09
 * revoke of a user with other projects is refused (409) until the cross-project wipe is confirmed.
 */
function RevokeDialog({
  device,
  onClose,
  onRevoked,
}: {
  device: AdminDevice;
  onClose: () => void;
  onRevoked: (device: AdminDevice) => void;
}) {
  const { t, i18n } = useTranslation();
  const { api } = useServices();
  const [crossProject, setCrossProject] = useState(false);
  const revoke = useMutation({
    mutationFn: (confirm: boolean) => revokeDevice(api, device.device_id, confirm),
    onSuccess: (res) => {
      if (res.kind === "crossProject") setCrossProject(true);
      else onRevoked(res.device);
    },
  });
  return (
    <Dialog
      title={t("devices.revokeTitle")}
      onClose={onClose}
      actions={
        <button
          type="button"
          className={PRIMARY}
          disabled={revoke.isPending}
          onClick={() => {
            revoke.mutate(crossProject);
          }}
        >
          {t(crossProject ? "devices.confirmCrossProject" : "devices.confirmRevoke")}
        </button>
      }
    >
      <p>
        {t("devices.revokeLastSync", {
          time: formatTime(device.last_sync_at, i18n.language) ?? t("devices.never"),
        })}
      </p>
      <p>{t("devices.revokeWipe")}</p>
      {revoke.isError && <p role="alert">{t("admin.actionError")}</p>}
      {crossProject && (
        <p role="alert" className="rounded border-2 border-warning p-2">
          {t("devices.crossProject")}
        </p>
      )}
    </Dialog>
  );
}
