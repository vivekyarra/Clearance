import type { Api } from "./types";

export type { Api } from "./types";

function offlineRequested(): boolean {
  if (import.meta.env.VITE_OFFLINE === "1") return true;
  try {
    const q = new URLSearchParams(location.search).get("offline");
    if (q === "1") localStorage.setItem("clearance.offline", "1");
    if (q === "0") localStorage.removeItem("clearance.offline");
    return localStorage.getItem("clearance.offline") === "1";
  } catch {
    return false;
  }
}

/** Set once in main.tsx before the first render (ES live binding). */
export let api: Api;

export async function loadApi(): Promise<Api> {
  if (offlineRequested()) {
    const m = await import("../offline/api");
    api = m.offlineApi;
  } else {
    const m = await import("./http");
    api = m.httpApi;
  }
  return api;
}

export function setOffline(on: boolean) {
  try {
    if (on) localStorage.setItem("clearance.offline", "1");
    else localStorage.removeItem("clearance.offline");
  } catch {
    /* ignore */
  }
  location.reload();
}
