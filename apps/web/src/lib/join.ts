/**
 * Site QR code (docs/CONTRACTS.md "Site QR code"): the QR encodes
 *   <web base>/join/<site_id>?api=<url-encoded API base>&name=<url-encoded site name>
 * Any phone camera opens it as the /join page; the iOS app parses it directly.
 * Query values are percent-encoded (spaces as %20, never "+", which iOS URLComponents keeps literally).
 */

function query(items: [string, string | null | undefined][]): string {
  return items
    .filter((kv): kv is [string, string] => !!kv[1])
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join("&");
}

/** Join URL for a site (used when the API did not send one, e.g. offline mode). */
export function siteJoinUrl(webOrigin: string, siteId: string, apiBase?: string, name?: string): string {
  const q = query([
    ["api", apiBase?.replace(/\/+$/, "")],
    ["name", name],
  ]);
  return `${webOrigin.replace(/\/+$/, "")}/join/${encodeURIComponent(siteId)}${q ? `?${q}` : ""}`;
}

/** API base named by the join URL's `api` query item, else the console's /api proxy on this origin. */
export function joinApiBase(search: string, origin: string): string {
  const api = new URLSearchParams(search).get("api");
  if (api && /^https?:\/\/[^/]+/i.test(api)) return api.replace(/\/+$/, "");
  return `${origin.replace(/\/+$/, "")}/api`;
}

/** clearance://join?api=…&site=…&name=… link that opens the iOS app (SiteJoinParser in ClearanceKit). */
export function appJoinLink(siteId: string, search: string, origin: string): string {
  const q = query([
    ["api", joinApiBase(search, origin)],
    ["site", siteId],
    ["name", new URLSearchParams(search).get("name")],
    ["token", new URLSearchParams(search).get("token")],
  ]);
  return `clearance://join?${q}`;
}
