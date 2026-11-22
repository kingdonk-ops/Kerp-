import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";

import { useServices } from "../app/services";
import { useProjectScope } from "../projects/ProjectScopeProvider";
import { useAdminAccess } from "./access";
import {
  AddToProjectError,
  AdminApiError,
  addToProjects,
  assignMember,
  directGrants,
  fetchMembers,
  fetchProjects,
  lookupUser,
  mergeGrants,
  removeMember,
  type GrantIn,
  type UserSummary,
} from "./api";
import { PendingInvites } from "./PendingInvites";
import { ProjectAdminPanel } from "./ProjectAdminPanel";
import { PersonaFields, SITE_PERSONA, toGrants, type Persona } from "./personas";
import { PROJECTS_KEY } from "./ProjectsPage";
import { BUTTON, Dialog, INPUT, PRIMARY } from "./ui";

/** Editing an existing member (this project, complete set) or adding a user (several projects). */
interface Editing { user: UserSummary; existing: boolean }

/**
 * Admin → project members (diff §4.1, edge spec §5): PER-09 of the project or a System admin
 * assigns and removes personas with site scope; team-derived grants are shown, never edited.
 */
export function MembersPage() {
  const { t } = useTranslation();
  const { api } = useServices();
  const { projectId = "" } = useParams();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [removing, setRemoving] = useState<UserSummary | null>(null);
  const [email, setEmail] = useState("");
  const key = ["admin", "members", projectId];
  const members = useQuery({ queryKey: key, queryFn: () => fetchMembers(api, projectId) });
  const projects = useQuery({ queryKey: PROJECTS_KEY, queryFn: () => fetchProjects(api) });
  const current = projects.data?.find((p) => p.id === projectId);
  const label = current
    ? t("admin.accessRequests.project", { code: current.code, name: current.name })
    : "";
  const lookup = useMutation({
    mutationFn: () => lookupUser(api, email),
    onSuccess: (user) => {
      if (user) setEditing({ user, existing: false });
    },
  });
  const remove = useMutation({
    mutationFn: (user: UserSummary) => removeMember(api, projectId, user.id),
    onSuccess: (res) => {
      done(t(res === "team" ? "admin.members.teamOnly" : "admin.members.removed"));
    },
  });
  const done = (message: string) => {
    setEditing(null);
    setRemoving(null);
    setNotice(message);
    void queryClient.invalidateQueries({ queryKey: ["admin", "members"] });
  };
  const forbidden = members.error instanceof AdminApiError && members.error.status === 403;

  return (
    <section className="space-y-3">
      <h1 className="text-2xl font-bold">{t("admin.members.title", { project: label })}</h1>
      {current && <ProjectAdminPanel project={current} label={label} />}
      <p role="status">{notice}</p>
      <form
        className="flex flex-wrap items-end gap-touch-gap"
        onSubmit={(e) => {
          e.preventDefault();
          lookup.mutate();
        }}
      >
        <label className="block grow space-y-1">
          <span>{t("admin.members.addEmail")}</span>
          <input
            type="email"
            className={INPUT}
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
            }}
          />
        </label>
        <button type="submit" className={BUTTON} disabled={!email.includes("@") || lookup.isPending}>
          {t("admin.members.find")}
        </button>
      </form>
      {lookup.data === null && <p role="alert">{t("admin.members.notFound")}</p>}
      {(lookup.isError || remove.isError) && <p role="alert">{t("admin.actionError")}</p>}
      {editing && (
        <AssignDialog
          key={editing.user.id}
          projectId={projectId}
          projectLabel={label}
          editing={editing}
          current={directGrants(members.data?.find((m) => m.user_id === editing.user.id))}
          onClose={() => {
            setEditing(null);
          }}
          onDone={() => {
            done(t("admin.members.saved"));
          }}
        />
      )}
      {removing && (
        <Dialog
          title={t("admin.members.removeTitle", { name: removing.display_name, project: label })}
          onClose={() => {
            setRemoving(null);
          }}
          actions={
            <button
              type="button"
              className={PRIMARY}
              disabled={remove.isPending}
              onClick={() => {
                remove.mutate(removing);
              }}
            >
              {t("admin.members.remove")}
            </button>
          }
        >
          <p>{t("admin.members.removeBody")}</p>
        </Dialog>
      )}
      {members.isPending && <p>{t("app.loading")}</p>}
      {members.isError && <p role="alert">{t(forbidden ? "admin.forbidden" : "admin.loadError")}</p>}
      {members.data?.length === 0 && <p>{t("admin.members.empty")}</p>}
      <ul className="space-y-2">
        {members.data?.map((m) => {
          const user = { id: m.user_id, email: m.email, display_name: m.display_name };
          const direct = m.grants.some((g) => g.source === "direct");
          return (
            <li key={m.user_id} className="space-y-1 rounded border border-line bg-panel p-3">
              <p className="font-semibold">
                {t("admin.accessRequests.requester", { name: m.display_name, email: m.email })}
              </p>
              <ul className="text-sm">
                {m.grants.map((g) => (
                  <li key={`${g.persona}-${g.source_id ?? "direct"}`}>
                    {t("admin.accessRequests.persona", { code: g.persona, name: t(`personas.${g.persona}`) })}
                    {g.site_node_id && ` · ${t("admin.members.site", { site: g.site_node_id })}`}
                    {g.team_name && ` · ${t("admin.members.team", { team: g.team_name })}`}
                  </li>
                ))}
              </ul>
              <div className="flex flex-wrap gap-touch-gap">
                <button
                  type="button"
                  className={BUTTON}
                  onClick={() => {
                    setEditing({ user, existing: true });
                  }}
                >
                  {t("admin.members.edit")}
                </button>
                {direct && (
                  <button
                    type="button"
                    className={BUTTON}
                    onClick={() => {
                      setRemoving(user);
                    }}
                  >
                    {t("admin.members.remove")}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {!forbidden && <PendingInvites projectId={projectId} />}
    </section>
  );
}

/**
 * Personas + site for one user. Editing sets the complete individual set on this project and
 * keeps each PER-07 site it holds as its own checkbox (PER-07 may be held once per site), so an
 * unrelated edit never drops a site. Adding also offers the admin's other projects, except
 * while PER-07 is chosen: its site is a node of this project's hierarchy. A `409` SoD answer
 * (PER-09 + PER-02/06/07/11) names the project; "Save anyway" confirms that project only.
 */
function AssignDialog({
  projectId,
  projectLabel,
  editing,
  current,
  onClose,
  onDone,
}: {
  projectId: string;
  projectLabel: string;
  editing: Editing;
  current: GrantIn[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const { api } = useServices();
  const { projects } = useProjectScope();
  const { adminProjectIds } = useAdminAccess();
  const others = projects.filter((p) => p.id !== projectId && adminProjectIds.includes(p.id));
  const held = editing.existing ? current : [];
  const heldSites = held.flatMap((g) =>
    g.persona === SITE_PERSONA && g.site_node_id ? [g.site_node_id] : [],
  );
  const [chosen, setChosen] = useState<ReadonlySet<Persona>>(
    new Set(held.filter((g) => g.persona !== SITE_PERSONA).map((g) => g.persona)),
  );
  const [kept, setKept] = useState<ReadonlySet<string>>(new Set(heldSites));
  const [site, setSite] = useState("");
  const [targets, setTargets] = useState<ReadonlySet<string>>(new Set());
  /** Projects still to save after a SoD answer, the warned one first; empty: no warning. */
  const [pending, setPending] = useState<string[]>([]);
  const picked = toGrants(chosen, site);
  const keptGrants = [...kept].map((id) => ({ persona: SITE_PERSONA, site_node_id: id }));
  const grants = picked && mergeGrants(picked, keptGrants);
  const siteChosen = chosen.has(SITE_PERSONA);
  const save = useMutation({
    mutationFn: async (confirmSod: boolean): Promise<string[]> => {
      if (editing.existing) {
        const res = await assignMember(api, projectId, editing.user.id, grants ?? [], confirmSod);
        return res === "sod" ? [projectId] : [];
      }
      const extra = siteChosen ? [] : others.filter((p) => targets.has(p.id)).map((p) => p.id);
      const ids = confirmSod ? pending : [projectId, ...extra];
      return addToProjects(api, editing.user.id, ids, grants ?? [], confirmSod);
    },
    onSuccess: (left) => {
      setPending(left);
      if (left.length === 0) onDone();
    },
  });
  const nameOf = (id: string | undefined) => {
    const p = projects.find((x) => x.id === id);
    return p && id !== projectId
      ? t("admin.accessRequests.project", { code: p.code, name: p.name })
      : projectLabel;
  };
  /** Any change of what is saved withdraws a pending SoD warning. */
  const changed =
    <T,>(set: (v: T) => void) =>
    (v: T) => {
      set(v);
      setPending([]);
    };
  const sod = pending.length > 0;
  const failedAt = save.error instanceof AddToProjectError ? save.error.projectId : null;

  return (
    <Dialog
      title={t(editing.existing ? "admin.members.editTitle" : "admin.members.addTitle", {
        name: editing.user.display_name,
      })}
      onClose={onClose}
      actions={
        <button
          type="button"
          className={PRIMARY}
          disabled={!grants?.length || save.isPending}
          onClick={() => {
            save.mutate(sod);
          }}
        >
          {t(sod ? "admin.members.confirmSod" : "admin.members.save")}
        </button>
      }
    >
      <p>{editing.user.email}</p>
      {heldSites.length > 0 && (
        <fieldset className="space-y-1">
          <legend className="font-semibold">{t("admin.members.heldSites")}</legend>
          {heldSites.map((id) => (
            <label key={id} className="flex min-h-touch items-center gap-touch-gap">
              <input
                type="checkbox"
                className="size-touch shrink-0"
                checked={kept.has(id)}
                onChange={() => {
                  const next = new Set(kept);
                  if (!next.delete(id)) next.add(id);
                  changed(setKept)(next);
                }}
              />
              {t("admin.members.heldSite", { site: id })}
            </label>
          ))}
        </fieldset>
      )}
      <PersonaFields
        legend={t("admin.members.personasLegend")}
        chosen={chosen}
        onChange={changed(setChosen)}
        site={site}
        onSite={changed(setSite)}
      />
      {!editing.existing && others.length > 0 && (
        <fieldset className="space-y-1">
          <legend className="font-semibold">
            {t("admin.members.projectsLegend", { project: projectLabel })}
          </legend>
          {siteChosen && <p className="text-sm">{t("admin.members.siteThisProject")}</p>}
          {others.map((p) => (
            <label key={p.id} className="flex min-h-touch items-center gap-touch-gap">
              <input
                type="checkbox"
                className="size-touch shrink-0"
                checked={!siteChosen && targets.has(p.id)}
                disabled={sod || siteChosen}
                onChange={() => {
                  const next = new Set(targets);
                  if (!next.delete(p.id)) next.add(p.id);
                  setTargets(next);
                }}
              />
              {t("admin.accessRequests.project", { code: p.code, name: p.name })}
            </label>
          ))}
        </fieldset>
      )}
      {save.isError && (
        <p role="alert">
          {failedAt
            ? t("admin.members.failedAt", { project: nameOf(failedAt) })
            : t("admin.actionError")}
        </p>
      )}
      {sod && (
        <p role="alert" className="rounded border-2 border-warning p-2">
          {t("admin.members.sodWarning", { project: nameOf(pending[0]) })}
        </p>
      )}
    </Dialog>
  );
}
