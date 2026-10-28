/**
 * Operator-confirmed clear floor, client side: which unseen (UNKNOWN) cells the default delivery route
 * would cross, and which cells the clear zones open. Mirrors jobs/sim/raster.py::apply_clear_zones:
 * inside a clear zone UNKNOWN counts as free; OCCUPIED cells are never changed.
 */
import { pointInPolygon } from "../lib/geometry";
import { FREE, OCC, UNKNOWN, cellToWorld, inBounds, worldToCell, type OccGrid } from "../lib/grid";
import type { ClearZone, Twin, XY } from "../types";

export const MAX_CLEAR_ZONE_M2 = 4;
export const MAX_CLEAR_ZONES = 20;

/** Cells (row-major indices) whose centers lie inside any clear zone. */
export function clearZoneMask(g: OccGrid, zones: ClearZone[] | undefined): Uint8Array {
  const m = new Uint8Array(g.width * g.height);
  for (const z of zones ?? []) {
    if (z.polygon.length < 3) continue;
    const xs = z.polygon.map((p) => p[0]);
    const ys = z.polygon.map((p) => p[1]);
    const [r0, c0] = worldToCell(g, Math.min(...xs), Math.max(...ys));
    const [r1, c1] = worldToCell(g, Math.max(...xs), Math.min(...ys));
    for (let r = Math.max(0, r0); r <= Math.min(g.height - 1, r1); r++)
      for (let c = Math.max(0, c0); c <= Math.min(g.width - 1, c1); c++) if (pointInPolygon(cellToWorld(g, r, c), z.polygon)) m[r * g.width + c] = 1;
  }
  return m;
}

/** Grid with UNKNOWN -> FREE inside the clear zones (OCC untouched). */
export function applyClearZones(g: OccGrid, zones: ClearZone[] | undefined): OccGrid {
  if (!zones?.length) return g;
  const m = clearZoneMask(g, zones);
  const data = g.data.slice();
  for (let i = 0; i < data.length; i++) if (m[i] && data[i] === UNKNOWN) data[i] = FREE;
  return { ...g, data };
}

/** Default delivery route, as services/api default_task: dock, pickup, every destination, dock. */
export function defaultRoute(twin: Twin): string[] | null {
  const wps = twin.waypoints ?? [];
  const dock = wps.find((w) => w.kind === "dock")?.id;
  const pickup = wps.find((w) => w.kind === "pickup")?.id;
  const dests = wps.filter((w) => w.kind === "dest").map((w) => w.id);
  if (!pickup || !dests.length) return null;
  const seq = [pickup, ...dests];
  return dock ? [dock, ...seq, dock] : seq;
}

const UNKNOWN_COST = 4;

/** Cheapest 4-connected path (free cost 1, unseen cost 4, occupied impassable); Dial's bucket queue, O(cells). */
export function cheapestPath(g: OccGrid, a: number, b: number): number[] | null {
  const n = g.width * g.height;
  const dist = new Int32Array(n).fill(-1);
  const prev = new Int32Array(n).fill(-1);
  const done = new Uint8Array(n);
  const B = UNKNOWN_COST + 1;
  const buckets: number[][] = Array.from({ length: B }, () => []);
  dist[a] = 0;
  buckets[0].push(a);
  let pending = 1;
  let d = 0;
  const W = g.width;
  while (pending > 0) {
    const bucket = buckets[d % B];
    while (bucket.length) {
      const i = bucket.pop()!;
      pending--;
      if (done[i] || dist[i] !== d) continue;
      done[i] = 1;
      if (i === b) return walk(prev, b);
      const r = (i / W) | 0;
      const c = i - r * W;
      for (const [dr, dc] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const rr = r + dr;
        const cc = c + dc;
        if (!inBounds(g, rr, cc)) continue;
        const j = rr * W + cc;
        const v = g.data[j];
        if (done[j] || (v === OCC && j !== b)) continue;
        const nd = d + (v === FREE ? 1 : UNKNOWN_COST);
        if (dist[j] < 0 || nd < dist[j]) {
          dist[j] = nd;
          prev[j] = i;
          buckets[nd % B].push(j);
          pending++;
        }
      }
    }
    d++;
  }
  return null;
}

function walk(prev: Int32Array, b: number): number[] {
  const out = [b];
  for (let i = prev[b]; i >= 0; i = prev[i]) out.push(i);
  return out.reverse();
}

export interface UnseenPatch {
  xy: XY;
  cells: number;
}

/**
 * Unseen patches the default route would have to cross (after clear zones are applied).
 * A patch is one 4-connected component of UNKNOWN cells touched by the cheapest path of any leg.
 * Returns null when no route can be formed yet (missing pickup / destination).
 */
export function unseenOnRoute(twin: Twin, raw: OccGrid): UnseenPatch[] | null {
  const seq = defaultRoute(twin);
  if (!seq) return null;
  const g = applyClearZones(raw, twin.clear_zones);
  const wps = new Map((twin.waypoints ?? []).map((w) => [w.id, w.xy] as const));
  const cellOf = (xy: XY) => {
    const [r, c] = worldToCell(g, xy[0], xy[1]);
    return inBounds(g, r, c) ? r * g.width + c : -1;
  };
  const touched = new Set<number>();
  const seen = new Set<string>();
  for (let k = 0; k + 1 < seq.length; k++) {
    const key = `${seq[k]}>${seq[k + 1]}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const a = cellOf(wps.get(seq[k])!);
    const b = cellOf(wps.get(seq[k + 1])!);
    if (a < 0 || b < 0) continue;
    for (const i of cheapestPath(g, a, b) ?? []) if (g.data[i] === UNKNOWN) touched.add(i);
  }
  const label = new Int32Array(g.width * g.height).fill(-1);
  const patches: UnseenPatch[] = [];
  for (const s of touched) {
    if (label[s] >= 0) continue;
    const id = patches.length;
    const stack = [s];
    label[s] = id;
    let cells = 0;
    while (stack.length) {
      const i = stack.pop()!;
      cells++;
      const r = (i / g.width) | 0;
      const c = i - r * g.width;
      for (const [dr, dc] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const rr = r + dr;
        const cc = c + dc;
        if (!inBounds(g, rr, cc)) continue;
        const j = rr * g.width + cc;
        if (label[j] < 0 && g.data[j] === UNKNOWN) {
          label[j] = id;
          stack.push(j);
        }
      }
    }
    const r = (s / g.width) | 0;
    patches.push({ xy: cellToWorld(g, r, s - r * g.width), cells });
  }
  return patches;
}
