import type { KeyValueStorage } from "../auth/tokenStore";

/** SPA route of the invite link (`/invite#<invite_token>[.<setup_token>]`, diff §4.1). */
export const INVITE_PATH = "/invite";

/**
 * The tab's pending invite while it is carried through sign-in (T-0053). `sessionStorage`
 * only: never `localStorage`, a URL, a query key or a log. Cleared once the invite is used
 * or refused.
 */
export const PENDING_INVITE_KEY = "kerp.invite.pending";

export interface PendingInvite {
  inviteToken: string;
  /** Only in the e-mailed link: lets a brand-new invitee set a first password (T-0059). */
  setupToken: string | null;
}

/** `secrets.token_urlsafe` output; anything else is not a token and is never sent. */
const TOKEN = /^[A-Za-z0-9_-]{16,512}$/;

/** The invite in a URL fragment (`#<invite_token>` or `#<invite_token>.<setup_token>`). */
export function parseInviteFragment(hash: string): PendingInvite | null {
  const [inviteToken = "", setupToken, ...rest] = hash.replace(/^#/, "").split(".");
  if (rest.length > 0 || !TOKEN.test(inviteToken)) return null;
  if (setupToken === undefined) return { inviteToken, setupToken: null };
  return TOKEN.test(setupToken) ? { inviteToken, setupToken } : null;
}

export function savePendingInvite(storage: KeyValueStorage, invite: PendingInvite): void {
  storage.setItem(PENDING_INVITE_KEY, JSON.stringify(invite));
}

export function loadPendingInvite(storage: KeyValueStorage): PendingInvite | null {
  const raw = storage.getItem(PENDING_INVITE_KEY);
  if (raw === null) return null;
  try {
    const value = JSON.parse(raw) as Partial<PendingInvite>;
    const setup = value.setupToken ? `.${value.setupToken}` : "";
    return parseInviteFragment(`${value.inviteToken ?? ""}${setup}`);
  } catch {
    return null;
  }
}

export function clearPendingInvite(storage: KeyValueStorage): void {
  storage.removeItem(PENDING_INVITE_KEY);
}
