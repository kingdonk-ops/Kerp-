import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { useServices } from "../app/services";
import { useAdminAccess } from "./access";
import {
  createProject,
  fetchProjects,
  setLobbyVisible,
  updateProject,
  type Project,
  type ProjectUpdate,
  type SaveResult,
} from "./api";
import { BUTTON, Dialog, INPUT, PRIMARY } from "./ui";

/** Every project query (`["projects", …]`): the list here and the picker's `/me/projects`. */
const PROJECTS = ["projects"] as const;
export const PROJECTS_KEY = ["projects", "all"] as const;
const CODE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$/;
const STATUSES = ["ACTIVE", "ON_HOLD", "CLOSED"] as const;
const SETTINGS = ["site_timezone", "currency_code", "locale"] as const;

/**
 * Backbone → Projects (diff §4.1, §6.1): the caller's projects; a Project Admin creates one;
 * PER-09 (or a System admin) edits settings, *visible in lobby* and members.
 */
export function ProjectsPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const access = useAdminAccess();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const [open, setOpen] = useState<"new" | Project | null>(null);
  const list = useQuery({ queryKey: PROJECTS_KEY, queryFn: () => fetchProjects(api) });
  const lobby = useMutation({
    mutationFn: (v: { id: string; visible: boolean }) => setLobbyVisible(api, v.id, v.visible),
    onSettled: () => queryClient.invalidateQueries({ queryKey: PROJECTS_KEY }),
  });
  const done = (message: string) => {
    setOpen(null);
    setNotice(message);
    void queryClient.invalidateQueries({ queryKey: PROJECTS });
  };
  const administers = (p: Project) => access.systemAdmin || access.adminProjectIds.includes(p.id);

  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-bold">{t("admin.projects.title")}</h1>
      <p role="status">{notice}</p>
      <button
        type="button"
        className={PRIMARY}
        onClick={() => {
          setOpen("new");
        }}
      >
        {t("admin.projects.new")}
      </button>
      {open === "new" && (
        <NewProjectDialog
          onClose={() => {
            setOpen(null);
          }}
          onDone={() => {
            done(t("admin.projects.created"));
          }}
        />
      )}
      {open && open !== "new" && (
        <SettingsDialog
          project={open}
          onClose={() => {
            setOpen(null);
          }}
          onDone={() => {
            done(t("admin.projects.savedSettings"));
          }}
        />
      )}
      {lobby.isError && <p role="alert">{t("admin.actionError")}</p>}
      {list.isPending && <p>{t("app.loading")}</p>}
      {list.isError && <p role="alert">{t("admin.loadError")}</p>}
      {list.data?.length === 0 && <p>{t("admin.projects.empty")}</p>}
      <ul className="space-y-2">
        {list.data?.map((p) => (
          <li key={p.id} className="space-y-1 rounded border border-line bg-panel p-3">
            <p className="font-semibold">
              {t("admin.accessRequests.project", { code: p.code, name: p.name })}
            </p>
            <p className="text-sm">{t(`admin.projects.status.${p.status}`)}</p>
            {administers(p) && (
              <div className="flex flex-wrap items-center gap-touch-gap">
                <label className="flex min-h-touch items-center gap-2">
                  <input
                    type="checkbox"
                    className="size-touch"
                    checked={p.visible_in_lobby}
                    disabled={lobby.isPending}
                    onChange={(e) => {
                      lobby.mutate({ id: p.id, visible: e.target.checked });
                    }}
                  />
                  {t("admin.projects.lobby")}
                </label>
                <button
                  type="button"
                  className={BUTTON}
                  onClick={() => {
                    setOpen(p);
                  }}
                >
                  {t("admin.projects.settings")}
                </button>
                <Link
                  to={`/admin/projects/${p.id}/members`}
                  className={`${BUTTON} flex items-center`}
                >
                  {t("admin.projects.members")}
                </Link>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

interface DialogProps {
  onClose: () => void;
  onDone: () => void;
}

/** Save through `run`; a refusal (`forbidden`, `duplicate`) stays in the dialog. */
function useSave(run: () => Promise<SaveResult>, onDone: () => void) {
  return useMutation({
    mutationFn: run,
    onSuccess: (res) => {
      if (res === "ok") onDone();
    },
  });
}

function Refusal({ save }: { save: ReturnType<typeof useSave> }) {
  const { t } = useTranslation();
  const res = save.data;
  if (save.isError) return <p role="alert">{t("admin.actionError")}</p>;
  if (res === "forbidden" || res === "duplicate") {
    return <p role="alert">{t(`admin.projects.${res}`)}</p>;
  }
  return null;
}

function Field({ label, value, onChange, hint }: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
}) {
  const hintId = useId();
  return (
    <div className="space-y-1">
      <label className="block space-y-1">
        <span>{label}</span>
        <input
          className={INPUT}
          value={value}
          aria-describedby={hint ? hintId : undefined}
          onChange={(e) => {
            onChange(e.target.value);
          }}
        />
      </label>
      {hint && (
        <p id={hintId} className="text-sm">
          {hint}
        </p>
      )}
    </div>
  );
}

/** A new project takes the deployment's defaults (T-0044); the creator becomes its PER-09. */
function NewProjectDialog({ onClose, onDone }: DialogProps) {
  const { t } = useTranslation();
  const { api } = useServices();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const save = useSave(() => createProject(api, code, name.trim()), onDone);
  return (
    <Dialog
      title={t("admin.projects.newTitle")}
      onClose={onClose}
      actions={
        <button
          type="button"
          className={PRIMARY}
          disabled={!CODE.test(code) || name.trim() === "" || save.isPending}
          onClick={() => {
            save.mutate();
          }}
        >
          {t("admin.projects.create")}
        </button>
      }
    >
      <Field label={t("admin.projects.code")} value={code} onChange={setCode} hint={t("admin.projects.codeHint")} />
      <Field label={t("admin.projects.name")} value={name} onChange={setName} />
      <p className="text-sm">{t("admin.projects.defaults")}</p>
      <Refusal save={save} />
    </Dialog>
  );
}

/** PER-09 edits the project's settings; only changed fields are sent (`PATCH`). */
function SettingsDialog({ project, onClose, onDone }: DialogProps & { project: Project }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const [form, setForm] = useState({
    name: project.name,
    status: project.status,
    site_timezone: project.site_timezone,
    currency_code: project.currency_code,
    locale: project.locale,
  });
  const changes: ProjectUpdate = Object.fromEntries(
    Object.entries(form)
      .map(([k, v]) => [k, v.trim()] as const)
      .filter(([k, v]) => v !== project[k as keyof typeof form]),
  );
  const save = useSave(() => updateProject(api, project.id, changes), onDone);
  const set = (key: keyof typeof form) => (value: string) => {
    setForm({ ...form, [key]: value });
  };
  const labels = { site_timezone: "timezone", currency_code: "currency", locale: "locale" } as const;
  return (
    <Dialog
      title={t("admin.projects.settingsTitle", {
        project: t("admin.accessRequests.project", { code: project.code, name: project.name }),
      })}
      onClose={onClose}
      actions={
        <button
          type="button"
          className={PRIMARY}
          disabled={Object.keys(changes).length === 0 || form.name.trim() === "" || save.isPending}
          onClick={() => {
            save.mutate();
          }}
        >
          {t("admin.projects.save")}
        </button>
      }
    >
      <Field label={t("admin.projects.name")} value={form.name} onChange={set("name")} />
      <label className="block space-y-1">
        <span>{t("admin.projects.statusLabel")}</span>
        <select
          className={INPUT}
          value={form.status}
          onChange={(e) => {
            set("status")(e.target.value);
          }}
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {t(`admin.projects.status.${s}`)}
            </option>
          ))}
        </select>
      </label>
      {SETTINGS.map((key) => (
        <Field key={key} label={t(`admin.projects.${labels[key]}`)} value={form[key]} onChange={set(key)} />
      ))}
      <Refusal save={save} />
    </Dialog>
  );
}
