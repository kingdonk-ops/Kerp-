import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { useServices } from "../app/services";
import { useAuth } from "../auth/AuthProvider";
import { useAdminAccess } from "./access";
import { fetchProjects } from "./api";
import { createInvite } from "./invites";
import { PendingInvites } from "./PendingInvites";
import { PersonaFields, toGrants, type Persona } from "./personas";
import { PROJECTS_KEY } from "./ProjectsPage";
import { BUTTON, Dialog, formatTime, INPUT, PRIMARY } from "./ui";
import { createAccount, fetchAccounts, setAccountActive, type Account } from "./users";

const ACCOUNTS_KEY = ["admin", "accounts"] as const;

/**
 * Admin → Users (diff §4.1, §6.1): System admins create, deactivate and reactivate accounts;
 * PER-09 holders (and System admins) invite a person by e-mail to a project they administer.
 * The flags only decide what is offered: the server checks every call.
 */
export function UsersPage() {
  const { t } = useTranslation();
  const { systemAdmin, projectAdmin, isLoading } = useAdminAccess();
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">{t("admin.users.title")}</h1>
      {isLoading && <p>{t("app.loading")}</p>}
      {!isLoading && !projectAdmin && <p role="alert">{t("admin.forbidden")}</p>}
      {systemAdmin && <Accounts />}
      {projectAdmin && <Invite />}
    </section>
  );
}

/** Every account; Deactivate (after confirmation) / Reactivate, never on one's own account. */
function Accounts() {
  const { t } = useTranslation();
  const { api } = useServices();
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const headingId = useId();
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [deactivating, setDeactivating] = useState<Account | null>(null);
  const list = useQuery({ queryKey: ACCOUNTS_KEY, queryFn: () => fetchAccounts(api) });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ACCOUNTS_KEY });
  const toggle = useMutation({
    mutationFn: (v: { account: Account; active: boolean }) =>
      setAccountActive(api, v.account.id, v.active),
    onSuccess: (res, { account, active }) => {
      setDeactivating(null);
      const done = active ? "admin.users.reactivated" : "admin.users.deactivated";
      setNotice(
        res === "last" ? t("admin.users.lastSystemAdmin") : t(done, { name: account.display_name }),
      );
    },
    onSettled: refresh,
  });

  return (
    <section className="space-y-2" aria-labelledby={headingId}>
      <h2 id={headingId} className="text-xl font-semibold">
        {t("admin.users.accountsTitle")}
      </h2>
      <p role="status">{notice}</p>
      {toggle.isError && <p role="alert">{t("admin.actionError")}</p>}
      <button
        type="button"
        className={PRIMARY}
        onClick={() => {
          setCreating(true);
        }}
      >
        {t("admin.users.newAccount")}
      </button>
      {creating && (
        <CreateAccountDialog
          onClose={() => {
            setCreating(false);
          }}
          onCreated={(email) => {
            setCreating(false);
            setNotice(t("admin.users.created", { email }));
            void refresh();
          }}
        />
      )}
      {deactivating && (
        <Dialog
          title={t("admin.users.deactivateTitle", { name: deactivating.display_name })}
          onClose={() => {
            setDeactivating(null);
          }}
          actions={
            <button
              type="button"
              className={PRIMARY}
              disabled={toggle.isPending}
              onClick={() => {
                toggle.mutate({ account: deactivating, active: false });
              }}
            >
              {t("admin.users.deactivate")}
            </button>
          }
        >
          <p>{t("admin.users.deactivateBody")}</p>
        </Dialog>
      )}
      {list.isPending && <p>{t("app.loading")}</p>}
      {list.isError && <p role="alert">{t("admin.loadError")}</p>}
      <ul className="space-y-2">
        {list.data?.map((a) => {
          const self = a.id === user?.id;
          return (
            <li
              key={a.id}
              aria-label={a.display_name}
              className="space-y-1 rounded border border-line bg-panel p-3"
            >
              <p className="font-semibold">
                {a.display_name}
                {self && ` ${t("admin.users.you")}`}
              </p>
              <p className="text-sm">
                {a.email} · {t(a.is_active ? "admin.users.active" : "admin.users.inactive")}
                {a.is_internal && ` · ${t("admin.users.internal")}`}
              </p>
              {self ? (
                <p className="text-sm">{t("admin.users.selfNote")}</p>
              ) : (
                <button
                  type="button"
                  className={BUTTON}
                  disabled={toggle.isPending}
                  onClick={() => {
                    if (a.is_active) setDeactivating(a);
                    else toggle.mutate({ account: a, active: true });
                  }}
                >
                  {t(a.is_active ? "admin.users.deactivate" : "admin.users.reactivate")}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function CreateAccountDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (email: string) => void;
}) {
  const { t } = useTranslation();
  const { api } = useServices();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () => createAccount(api, email, name),
    onSuccess: (res) => {
      if (res === "ok") onCreated(email.trim());
    },
  });
  return (
    <Dialog
      title={t("admin.users.newTitle")}
      onClose={onClose}
      actions={
        <button
          type="button"
          className={PRIMARY}
          disabled={!email.includes("@") || create.isPending}
          onClick={() => {
            create.mutate();
          }}
        >
          {t("admin.users.create")}
        </button>
      }
    >
      <p>{t("admin.users.createNote")}</p>
      <label className="block space-y-1">
        <span>{t("admin.users.email")}</span>
        <input
          type="email"
          className={INPUT}
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
          }}
        />
      </label>
      <label className="block space-y-1">
        <span>{t("admin.users.displayName")}</span>
        <input
          className={INPUT}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
          }}
        />
      </label>
      {create.data === "duplicate" && <p role="alert">{t("admin.users.duplicate")}</p>}
      {create.isError && <p role="alert">{t("admin.actionError")}</p>}
    </Dialog>
  );
}

/**
 * Invite by e-mail to one project with persona(s) + site (diff §4.1 Invites, T-0052). A `409`
 * SoD answer names the project; "Send anyway" sends `confirm_sod: true`, and any change
 * withdraws the warning. The copyable link (`invite_token` only) lives in this component's
 * state until closed: it never enters the query or mutation cache. Below, the chosen project's
 * open invitations (`PendingInvites`, T-0053) with Resend and Revoke.
 */
function Invite() {
  const { t, i18n } = useTranslation();
  const { api } = useServices();
  const queryClient = useQueryClient();
  const headingId = useId();
  const { systemAdmin, adminProjectIds } = useAdminAccess();
  const projects = useQuery({ queryKey: PROJECTS_KEY, queryFn: () => fetchProjects(api) });
  const options = (projects.data ?? []).filter((p) => systemAdmin || adminProjectIds.includes(p.id));
  const [picked, setPicked] = useState<string | null>(null);
  const projectId = picked ?? options[0]?.id ?? null;
  const current = options.find((p) => p.id === projectId);
  const label = current
    ? t("admin.accessRequests.project", { code: current.code, name: current.name })
    : "";
  const [email, setEmail] = useState("");
  const [chosen, setChosen] = useState<ReadonlySet<Persona>>(new Set());
  const [site, setSite] = useState("");
  const [sod, setSod] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Shown once, until closed: never cached, stored or logged.
  const [link, setLink] = useState<string | null>(null);
  const grants = toGrants(chosen, site);
  const send = useMutation({
    gcTime: 0,
    mutationFn: async ({ target, confirmSod }: { target: string; confirmSod: boolean }) => {
      const res = await createInvite(api, target, email, grants ?? [], confirmSod);
      if (res.kind !== "sent") return res;
      // Keep the link out of the mutation's cached result.
      setLink(res.invite.invite_link);
      return { kind: "sent" as const, email: res.invite.email };
    },
    onSuccess: (res, { target }) => {
      setSod(res.kind === "sod");
      if (res.kind === "sent") {
        setNotice(t("admin.users.sent", { email: res.email }));
        setEmail("");
        setChosen(new Set());
        setSite("");
        void queryClient.invalidateQueries({ queryKey: ["admin", "invites", target] });
      } else if (res.kind === "throttled") {
        const time = formatTime(res.retryAt?.toISOString(), i18n.language);
        setNotice(
          time ? t("admin.users.throttled", { time }) : t("admin.users.throttledLater"),
        );
      } else if (res.kind === "conflict") {
        setNotice(t("admin.users.conflict"));
      } else {
        setNotice(null);
      }
    },
  });
  /** Any change of what is sent withdraws a pending SoD warning. */
  const changed =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      setSod(false);
    };
  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setNotice(t("invite.panel.copied"));
    } catch {
      // No clipboard access: the link stays selectable in its field.
    }
  };

  return (
    <section className="space-y-2" aria-labelledby={headingId}>
      <h2 id={headingId} className="text-xl font-semibold">
        {t("admin.users.inviteTitle")}
      </h2>
      <p>{t("admin.users.inviteNote")}</p>
      <p role="status">{notice}</p>
      {link && (
        <div className="space-y-2 rounded border-2 border-brand bg-panel p-3">
          <label className="block space-y-1">
            <span>{t("admin.users.link")}</span>
            <input
              readOnly
              className={INPUT}
              value={link}
              onFocus={(e) => {
                e.target.select();
              }}
            />
          </label>
          <div className="flex flex-wrap gap-touch-gap">
            <button
              type="button"
              className={BUTTON}
              onClick={() => {
                void copy(link);
              }}
            >
              {t("invite.panel.copy")}
            </button>
            <button
              type="button"
              className={BUTTON}
              onClick={() => {
                setLink(null);
              }}
            >
              {t("invite.panel.close")}
            </button>
          </div>
        </div>
      )}
      {projects.isPending && <p>{t("app.loading")}</p>}
      {projects.isError && <p role="alert">{t("admin.loadError")}</p>}
      {projects.data && (
        <form
          className="max-w-lg space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (projectId) send.mutate({ target: projectId, confirmSod: sod });
          }}
        >
          <label className="block space-y-1">
            <span>{t("admin.users.project")}</span>
            <select
              className={INPUT}
              value={projectId ?? ""}
              onChange={(e) => {
                changed(setPicked)(e.target.value);
              }}
            >
              {options.map((p) => (
                <option key={p.id} value={p.id}>
                  {t("admin.accessRequests.project", { code: p.code, name: p.name })}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1">
            <span>{t("admin.users.inviteEmail")}</span>
            <input
              type="email"
              className={INPUT}
              value={email}
              onChange={(e) => {
                changed(setEmail)(e.target.value);
              }}
            />
          </label>
          <PersonaFields
            legend={t("admin.members.personasLegend")}
            chosen={chosen}
            onChange={changed(setChosen)}
            site={site}
            onSite={changed(setSite)}
          />
          {send.isError && <p role="alert">{t("admin.actionError")}</p>}
          {sod && (
            <p role="alert" className="rounded border-2 border-warning p-2">
              {t("admin.users.sodWarning", { project: label })}
            </p>
          )}
          <button
            type="submit"
            className={PRIMARY}
            disabled={!projectId || !email.includes("@") || !grants?.length || send.isPending}
          >
            {t(sod ? "admin.users.confirmSod" : "admin.users.send")}
          </button>
        </form>
      )}
      {projectId && <PendingInvites key={projectId} projectId={projectId} />}
    </section>
  );
}
