import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation, useNavigate } from "react-router";

import { LOBBY_PATH } from "../app/nav";
import { useServices, useSessionStorage } from "../app/services";
import { AuthCard, CardLink } from "../auth/AuthCard";
import { useAuth } from "../auth/AuthProvider";
import { NewPasswordForm, PRIMARY_BUTTON } from "../password/NewPasswordForm";
import { PasswordRefusedError } from "../password/policy";
import { rememberProjectScope } from "../projects/ProjectScopeProvider";
import {
  acceptInvite,
  acceptWithPassword,
  InviteError,
  inviteProblem,
  previewInvite,
  setupPreview,
  type InviteProblem,
} from "./api";
import {
  clearPendingInvite,
  loadPendingInvite,
  parseInviteFragment,
  savePendingInvite,
  type PendingInvite,
} from "./pendingInvite";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Router state of `/invite` after a sign-in that carried the invite (`LoginPage`). */
export interface InviteOutcomeState {
  joinedProjectId?: string | null;
  inviteError?: string | null;
}

/** The sign-in screen with the pending invite carried through (diff §4.1). */
export const SIGN_IN_WITH_INVITE = { invite: true } as const;

/**
 * `/invite#<invite_token>[.<setup_token>]` (diff §4.1 Invites). The tokens are read from the
 * fragment once, kept in this tab's `sessionStorage` for the sign-in, removed from the address
 * bar at once and sent only in POST bodies; `index.html` sends no referrer. Signed in → the
 * "Join project" confirmation; not signed in → "Create your password" (e-mailed link of an
 * account without credentials) or the sign-in screen with the invite carried through.
 */
export function InvitePage() {
  const { t } = useTranslation();
  const session = useSessionStorage();
  const { status, retry } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [invite] = useState<PendingInvite | null>(() => {
    if (location.hash === "") return loadPendingInvite(session);
    const fromLink = parseInviteFragment(location.hash);
    if (fromLink) savePendingInvite(session, fromLink);
    else clearPendingInvite(session);
    return fromLink;
  });
  const outcome = (location.state ?? {}) as InviteOutcomeState;
  // Set by the join / first-password step; this page outlives that step's component.
  const [joined, setJoined] = useState<string | null>(null);

  useEffect(() => {
    if (location.hash !== "") void navigate({ hash: "" }, { replace: true, state: location.state as unknown });
  }, [location.hash, location.state, navigate]);

  // Nothing goes on while the tokens are still in the address bar.
  if (location.hash !== "") return <Waiting text={t("app.loading")} />;
  if (typeof outcome.inviteError === "string") {
    return <Problem problem={inviteProblem(outcome.inviteError)} />;
  }
  const landIn = joined ?? outcome.joinedProjectId;
  if (typeof landIn === "string") return <Landing projectId={landIn} />;
  if (invite === null) return <Problem problem="gone" />;
  if (status === "loading") return <Waiting text={t("app.loading")} />;
  if (status === "error") {
    return (
      <AuthCard title={t("invite.title")}>
        <p role="alert">{t("app.loadError")}</p>
        <button type="button" className={PRIMARY_BUTTON} onClick={retry}>
          {t("app.retry")}
        </button>
      </AuthCard>
    );
  }
  if (status === "authenticated") return <Join inviteToken={invite.inviteToken} onJoined={setJoined} />;
  if (invite.setupToken) {
    return <Setup invite={invite} setupToken={invite.setupToken} onJoined={setJoined} />;
  }
  return <Navigate to="/login" replace state={SIGN_IN_WITH_INVITE} />;
}

function Waiting({ text }: { text: string }) {
  const { t } = useTranslation();
  return (
    <AuthCard title={t("invite.title")}>
      <p role="status" className="text-muted">
        {text}
      </p>
    </AuthCard>
  );
}

/**
 * Opens the joined project once the session's user is known: remembered as the scope (T-0042)
 * with a fresh project list, then `/` lands in it and the lobby is skipped.
 */
function Landing({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const { preferences } = useServices();
  const queryClient = useQueryClient();
  const { status, user } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (status !== "authenticated" || !user) return;
    if (UUID.test(projectId)) {
      queryClient.removeQueries({ queryKey: ["projects", "mine"] });
      rememberProjectScope(preferences, user.id, projectId);
    }
    void navigate("/", { replace: true });
  }, [status, user, projectId, preferences, queryClient, navigate]);

  if (status === "anonymous") return <Navigate to="/login" replace />;
  return <Waiting text={t("invite.opening")} />;
}

/** A neutral refusal with a way on: the lobby, and sign-out for the wrong user (diff §4.1). */
function Problem({ problem }: { problem: InviteProblem }) {
  const { t } = useTranslation();
  const session = useSessionStorage();
  const { status, logout } = useAuth();
  const navigate = useNavigate();
  const wrongUser = problem === "wrongUser";

  useEffect(() => {
    // The wrong user may still sign out and sign in as the invited one with this invite.
    if (!wrongUser) clearPendingInvite(session);
  }, [wrongUser, session]);

  return (
    <AuthCard title={t("invite.title")}>
      <p role="alert">{t(`invite.problem.${problem}`)}</p>
      {wrongUser && status === "authenticated" && (
        <button
          type="button"
          className={PRIMARY_BUTTON}
          onClick={() => {
            void logout().then(() => navigate("/login", { replace: true, state: SIGN_IN_WITH_INVITE }));
          }}
        >
          {t("invite.signOut")}
        </button>
      )}
      <CardLink
        to={LOBBY_PATH}
        onClick={() => {
          clearPendingInvite(session);
        }}
      >
        {t("invite.toLobby")}
      </CardLink>
    </AuthCard>
  );
}

/** Signed in: preview, then the one-click "Join project". */
type OnJoined = (projectId: string) => void;

function Join({ inviteToken, onJoined }: { inviteToken: string; onJoined: OnJoined }) {
  const { t } = useTranslation();
  const { api } = useServices();
  const session = useSessionStorage();
  // No token in the query key: keys are visible to tooling; the entry is dropped when unused.
  const preview = useQuery({
    queryKey: ["invite", "preview"],
    queryFn: () => previewInvite(api, inviteToken),
    retry: false,
    gcTime: 0,
    staleTime: Infinity,
  });
  const accept = useMutation({
    mutationFn: () => acceptInvite(api, inviteToken),
    onSuccess: (projectId) => {
      clearPendingInvite(session);
      onJoined(projectId);
    },
  });

  const failed = accept.error ?? preview.error;
  if (failed) return <Problem problem={failed instanceof InviteError ? failed.problem : "unavailable"} />;
  if (!preview.data) return <Waiting text={t("app.loading")} />;
  const { project_code: code, project_name: name, inviter_name: inviter } = preview.data;

  return (
    <AuthCard title={t("invite.join.title")}>
      <p>{t("invite.join.body", { project: t("scope.projectOption", { code, name }), inviter })}</p>
      <button
        type="button"
        className={PRIMARY_BUTTON}
        disabled={accept.isPending}
        onClick={() => {
          accept.mutate();
        }}
      >
        {t("invite.join.submit")}
      </button>
      <CardLink
        to={LOBBY_PATH}
        onClick={() => {
          clearPendingInvite(session);
        }}
      >
        {t("invite.join.notNow")}
      </CardLink>
    </AuthCard>
  );
}

/** Not signed in with the e-mailed link: "Create your password" when the account has none. */
function Setup({
  invite,
  setupToken,
  onJoined,
}: {
  invite: PendingInvite;
  setupToken: string;
  onJoined: OnJoined;
}) {
  const { t } = useTranslation();
  const { api } = useServices();
  const session = useSessionStorage();
  const { startSession } = useAuth();
  const preview = useQuery({
    queryKey: ["invite", "setup"],
    queryFn: () => setupPreview(api, invite.inviteToken, setupToken),
    retry: false,
    gcTime: 0,
    staleTime: Infinity,
  });
  const create = useMutation({
    mutationFn: (password: string) =>
      acceptWithPassword(api, invite.inviteToken, setupToken, password),
    onSuccess: (tokens) => {
      clearPendingInvite(session);
      startSession(tokens);
      onJoined(tokens.joined_project_id ?? "");
    },
  });

  const err = create.error ?? preview.error;
  const problem = err instanceof InviteError ? err.problem : null;
  if (problem === "signInRequired" || preview.data?.can_set_password === false) {
    return <Navigate to="/login" replace state={SIGN_IN_WITH_INVITE} />;
  }
  if (problem !== null || preview.isError) return <Problem problem={problem ?? "unavailable"} />;
  if (!preview.data) return <Waiting text={t("app.loading")} />;
  const { project_code: code, project_name: name } = preview.data;

  return (
    <AuthCard title={t("invite.setup.title")}>
      {code && name && (
        <p>{t("invite.setup.project", { project: t("scope.projectOption", { code, name }) })}</p>
      )}
      <NewPasswordForm
        submitLabel={t("invite.setup.submit")}
        busy={create.isPending}
        refusal={err instanceof PasswordRefusedError ? err.refusal : null}
        error={err && !(err instanceof PasswordRefusedError) ? t("password.failed") : null}
        onSubmit={(password) => {
          create.mutate(password);
        }}
      />
    </AuthCard>
  );
}
