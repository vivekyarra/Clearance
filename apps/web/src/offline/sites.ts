/**
 * Reference sites in offline mode: summaries, twins and occupancy grids come from the recorded fixtures
 * (sample_sites/ served through services/api). Edited twins are re-stamped locally over the recorded grid.
 */
import { OCC, type OccGrid, worldToCell } from "../lib/grid";
import { pointInPolygon, primitiveFootprint } from "../lib/geometry";
import type { SiteSummary, Twin, XY } from "../types";
import { INDEX, type ServerSite } from "./fixtures";

export function siteFromServer(s: ServerSite): SiteSummary {
  return {
    site_id: s.site_id,
    name: s.name,
    kind: s.kind ?? s.type,
    area_m2: s.area_m2 ?? null,
    geometry_source: s.geometry_source,
    created_at: s.created_at,
    twin_version: s.twin_version ?? s.latest_twin_version ?? null,
  };
}

export function referenceSites(): SiteSummary[] {
  return INDEX.sites.map(siteFromServer);
}

export function fillPoly(g: OccGrid, poly: XY[], v: number) {
  const xs = poly.map((p) => p[0]);
  const ys = poly.map((p) => p[1]);
  const [r0, c0] = worldToCell(g, Math.min(...xs), Math.max(...ys));
  const [r1, c1] = worldToCell(g, Math.max(...xs), Math.min(...ys));
  for (let rr = Math.max(0, r0); rr <= Math.min(g.height - 1, r1); rr++)
    for (let cc = Math.max(0, c0); cc <= Math.min(g.width - 1, c1); cc++) {
      const x = g.origin[0] + (cc + 0.5) * g.res;
      const y = g.origin[1] + (g.height - rr - 0.5) * g.res;
      if (pointInPolygon([x, y], poly)) g.data[rr * g.width + cc] = v;
    }
}

export function rasterizeTwinOffline(twin: Twin, base: OccGrid): OccGrid {
  // Offline save path: stamp the edited primitives over the recorded grid (services/api re-rasterizes from the scan).
  const g: OccGrid = { ...base, data: base.data.slice() };
  for (const p of twin.primitives ?? []) fillPoly(g, primitiveFootprint(p), OCC);
  return g;
}
