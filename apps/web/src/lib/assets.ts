import type { Twin } from "../types";

/** Twin asset keys served by GET /sites/{id}/twin/assets/{name} (docs/CONTRACTS.md "Twin assets"). */
export const TWIN_ASSET_KEYS = ["splat", "mesh", "mesh_glb", "occluder_usdz", "pointcloud"] as const;
export type TwinAssetKey = (typeof TWIN_ASSET_KEYS)[number];

/** File extension of a storage key or URL path (".ply"), ignoring query and fragment. */
export function extOf(path: string): string {
  const clean = path.split(/[?#]/)[0];
  const name = clean.slice(clean.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

/**
 * URL of a twin asset. Absolute URLs in twin.assets are used as they are; storage keys go through the
 * API route, with the stored file's extension kept in the path so loaders can detect the format.
 */
export function twinAssetUrl(apiBase: string, twin: Twin, key: TwinAssetKey): string | null {
  const v = twin.assets?.[key];
  if (!v) return null;
  if (/^https?:\/\//i.test(v)) return v;
  const ext = extOf(v);
  const name = ext ? `${key.replace(/_[a-z0-9]+$/, (m) => (m === `_${ext.slice(1)}` ? "" : m))}${ext}` : key;
  const q = twin.version ? `?version=${twin.version}` : "";
  return `${apiBase}/sites/${encodeURIComponent(twin.site_id)}/twin/assets/${name}${q}`;
}

export type SplatFormat = "Ply" | "Splat" | "KSplat" | "Spz";

/** GaussianSplats3D scene format from a URL (its own detector does not ignore query strings). */
export function splatFormat(url: string): SplatFormat | null {
  switch (extOf(url)) {
    case ".ply":
      return "Ply";
    case ".splat":
      return "Splat";
    case ".ksplat":
      return "KSplat";
    case ".spz":
      return "Spz";
    default:
      return null;
  }
}
