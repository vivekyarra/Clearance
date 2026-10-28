/** Looping robot motion along a mission route: travel each leg, pause at every stop, repeat. */
import type { XY } from "../../types";

export interface Route {
  pts: XY[];
  /** cumulative distance at each point */
  cum: number[];
  /** distance along the route of each stop (route start, every waypoint) */
  stops: number[];
  length: number;
}

/** Build a route from per-leg polylines (each leg starts where the previous one ends). */
export function buildRoute(legs: XY[][]): Route | null {
  const pts: XY[] = [];
  const stops: number[] = [0];
  const cum: number[] = [];
  for (const leg of legs) {
    if (!leg.length) continue;
    for (let i = pts.length ? 1 : 0; i < leg.length; i++) {
      const p = leg[i];
      const prev = pts[pts.length - 1];
      cum.push(prev ? cum[cum.length - 1] + Math.hypot(p[0] - prev[0], p[1] - prev[1]) : 0);
      pts.push(p);
    }
    stops.push(cum[cum.length - 1] ?? 0);
  }
  const length = cum[cum.length - 1] ?? 0;
  if (pts.length < 2 || length <= 1e-6) return null;
  return { pts, cum, stops: [...new Set(stops)], length };
}

export function pointAt(r: Route, s: number): XY {
  const d = Math.max(0, Math.min(r.length, s));
  let lo = 0;
  let hi = r.cum.length - 1;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (r.cum[m] <= d) lo = m;
    else hi = m;
  }
  const seg = r.cum[hi] - r.cum[lo] || 1;
  const f = (d - r.cum[lo]) / seg;
  const a = r.pts[lo];
  const b = r.pts[hi];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

/** Heading from a short look-behind / look-ahead window, which rounds the corners of grid paths. */
export function headingAt(r: Route, s: number, win = 0.3): number {
  const a = pointAt(r, s - win);
  const b = pointAt(r, s + win);
  return Math.atan2(b[1] - a[1], b[0] - a[0]);
}

export interface Motion {
  speed: number; // m/s
  pause: number; // s at each stop
}

export function loopDuration(r: Route, m: Motion): number {
  return r.length / m.speed + (r.stops.length - 1) * m.pause;
}

const ease = (x: number) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);

/** Distance along the route at time t (looping), with eased starts/stops between waypoints. */
export function distanceAt(r: Route, m: Motion, t: number): { s: number; stopped: boolean } {
  const T = loopDuration(r, m);
  let tt = ((t % T) + T) % T;
  for (let k = 0; k + 1 < r.stops.length; k++) {
    const a = r.stops[k];
    const b = r.stops[k + 1];
    const legT = (b - a) / m.speed;
    if (tt < legT) return { s: a + (b - a) * ease(legT > 0 ? tt / legT : 1), stopped: false };
    tt -= legT;
    if (tt < m.pause) return { s: b, stopped: true };
    tt -= m.pause;
  }
  return { s: r.length, stopped: true };
}

export function poseAt(r: Route, m: Motion, t: number): { x: number; y: number; yaw: number; stopped: boolean } {
  const { s, stopped } = distanceAt(r, m, t);
  const [x, y] = pointAt(r, s);
  return { x, y, yaw: headingAt(r, Math.min(r.length - 0.05, Math.max(0.05, s))), stopped };
}
