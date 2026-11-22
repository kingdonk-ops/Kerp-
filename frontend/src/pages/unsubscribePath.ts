/**
 * Route of the broadcast opt-out page (T-0229, TD-601); the e-mails link to
 * `<KERP_WEB_ORIGIN>/unsubscribe#<token>` (backend notifications `broadcasts.py`). Its own
 * module so the router can name it without pulling the lazily loaded page into the main chunk.
 */
export const UNSUBSCRIBE_PATH = "/unsubscribe";
