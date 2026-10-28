/**
 * Bearer tokens for the live API (services/api/auth.py):
 *  - operator token: entered once in the operator token dialog, kept in localStorage, sent on every request.
 *  - site join token: arrives in the site QR link (/join/<site>?token=...); stored per site so the observer and
 *    coach pages opened from the join page can register captures and join the coach session.
 *  - capture device token: returned by POST /sites/{id}/captures; used for that capture's upload URLs and
 *    complete call (kept in sessionStorage so a retry after a reload still works).
 *  - live session token: arrives as ?token= on joystick / coach links; opens that session's WebSocket.
 * Read routes need no token.
 */

const OPERATOR_KEY = "clearance.operator_token";
const JOIN_PREFIX = "clearance.join.";
const DEVICE_PREFIX = "clearance.device.";

function store(kind: "local" | "session"): Storage | null {
  try {
    return kind === "local" ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

export function operatorToken(): string | null {
  return store("local")?.getItem(OPERATOR_KEY)?.trim() || null;
}

export function setOperatorToken(token: string | null): void {
  const s = store("local");
  if (!s) return;
  const t = token?.trim();
  if (t) s.setItem(OPERATOR_KEY, t);
  else s.removeItem(OPERATOR_KEY);
}

interface Stored {
  token: string;
  exp: number | null;
}

/** `exp` (seconds) read from the signed token body, when it is one of ours. */
export function tokenExpiry(token: string): number | null {
  const body = token.split(".")[0];
  if (!body || token.split(".").length !== 2) return null;
  try {
    const json = JSON.parse(atob(body.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (body.length % 4)) % 4)));
    return typeof json.exp === "number" ? json.exp : null;
  } catch {
    return null;
  }
}

function readStored(s: Storage | null, key: string, nowS: number): string | null {
  const raw = s?.getItem(key);
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as Stored;
    if (v.exp !== null && v.exp < nowS) {
      s?.removeItem(key);
      return null;
    }
    return v.token;
  } catch {
    s?.removeItem(key);
    return null;
  }
}

function writeStored(s: Storage | null, key: string, token: string) {
  s?.setItem(key, JSON.stringify({ token, exp: tokenExpiry(token) } satisfies Stored));
}

export function joinToken(siteId: string, nowS = Date.now() / 1000): string | null {
  return readStored(store("local"), JOIN_PREFIX + siteId, nowS);
}

export function setJoinToken(siteId: string, token: string): void {
  writeStored(store("local"), JOIN_PREFIX + siteId, token);
}

export function deviceToken(captureId: string, nowS = Date.now() / 1000): string | null {
  return readStored(store("session"), DEVICE_PREFIX + captureId, nowS);
}

export function setDeviceToken(captureId: string, token: string): void {
  writeStored(store("session"), DEVICE_PREFIX + captureId, token);
}

/** The `token` query item of the current page (join, joystick and coach links carry one). */
export function urlToken(search = location.search): string | null {
  return new URLSearchParams(search).get("token");
}

/**
 * Remember the join token of a /join/<site>?token=... landing. Called once when the API client loads,
 * before the join page routes the phone to /observe or /coach (which drop the query string).
 */
export function captureJoinTokenFromUrl(path = location.pathname, search = location.search): void {
  const m = /^\/join\/([^/?#]+)/.exec(path);
  const tok = urlToken(search);
  if (m && tok) setJoinToken(decodeURIComponent(m[1]), tok);
}

/** Token for a site-scoped call (register capture, coach session): operator first, then the site join token. */
export function siteToken(siteId: string): string | null {
  return operatorToken() ?? joinToken(siteId);
}

export function authHeader(token: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}
