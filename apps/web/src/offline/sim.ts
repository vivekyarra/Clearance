/**
 * Compact 2D navigation model used only in offline mode, so replays, Pinch Map data and the live
 * session behave plausibly without the Python simulator (jobs/sim). It follows the same rules as
 * PLAN 7.7 in simplified form: global A* path, safety stop, near-miss detection, replan, timeout.
 */
import { type OccGrid, isBlocked, worldToCell } from "../lib/grid";
import { gauss, mulberry32 } from "../lib/rng";
import type { EpisodeEvent, EpisodeFrame, LiveFrame, RobotSpec, Twin, XY } from "../types";

const COARSE = 0.1;

export interface Planner {
  w: number;
  h: number;
  origin: XY;
  inflated: Uint8Array; // 1 = blocked for the robot centre
  /** extra step cost near obstacles, so routes keep a margin the way real planners do */
  penalty: Float32Array;
}

export function makePlanner(grid: OccGrid, radius: number): Planner {
  const f = Math.max(1, Math.round(COARSE / grid.res));
  const w = Math.ceil(grid.width / f);
  const h = Math.ceil(grid.height / f);
  const blocked = new Uint8Array(w * h);
  for (let r = 0; r < grid.height; r++)
    for (let c = 0; c < grid.width; c++) if (isBlocked(grid, r, c)) blocked[Math.floor(r / f) * w + Math.floor(c / f)] = 1;
  // Brushfire distance (in coarse cells) from blocked cells, then threshold at the robot radius.
  const dist = new Float32Array(w * h).fill(1e9);
  const q: number[] = [];
  for (let i = 0; i < w * h; i++) if (blocked[i]) {
    dist[i] = 0;
    q.push(i);
  }
  for (let qi = 0; qi < q.length; qi++) {
    const i = q[qi];
    const r = Math.floor(i / w);
    const c = i % w;
    for (let dr = -1; dr <= 1; dr++)
      for (let dc = -1; dc <= 1; dc++) {
        const rr = r + dr;
        const cc = c + dc;
        if (rr < 0 || cc < 0 || rr >= h || cc >= w) continue;
        const j = rr * w + cc;
        const nd = dist[i] + (dr && dc ? Math.SQRT2 : 1);
        if (nd < dist[j]) {
          dist[j] = nd;
          q.push(j);
        }
      }
  }
  const inflated = new Uint8Array(w * h);
  const rc = radius / COARSE + 0.75;
  const penalty = new Float32Array(w * h);
  const soft = 3; // coarse cells (0.3 m) of preferred margin beyond the footprint
  for (let i = 0; i < w * h; i++) {
    inflated[i] = dist[i] <= rc ? 1 : 0;
    const m = dist[i] - rc;
    penalty[i] = m < soft ? 2.5 * (1 - m / soft) : 0;
  }
  // grid origin is the bottom-left corner; coarse rows also count from the top
  return { w, h, origin: grid.origin, inflated, penalty };
}

const toCell = (p: Planner, x: number, y: number): [number, number] => [
  p.h - 1 - Math.floor((y - p.origin[1]) / COARSE),
  Math.floor((x - p.origin[0]) / COARSE),
];
const toWorld = (p: Planner, r: number, c: number): XY => [p.origin[0] + (c + 0.5) * COARSE, p.origin[1] + (p.h - r - 0.5) * COARSE];

function freeAt(p: Planner, r: number, c: number) {
  return r >= 0 && c >= 0 && r < p.h && c < p.w && !p.inflated[r * p.w + c];
}

/** Nearest free coarse cell (waypoints may sit close to furniture). */
function snap(p: Planner, r: number, c: number): [number, number] | null {
  for (let rad = 0; rad < 8; rad++)
    for (let dr = -rad; dr <= rad; dr++)
      for (let dc = -rad; dc <= rad; dc++) if (freeAt(p, r + dr, c + dc)) return [r + dr, c + dc];
  return null;
}

export function planPath(p: Planner, a: XY, b: XY): XY[] | null {
  const s = snap(p, ...toCell(p, a[0], a[1]));
  const g = snap(p, ...toCell(p, b[0], b[1]));
  if (!s || !g) return null;
  const N = p.w * p.h;
  const gs = new Float32Array(N).fill(Infinity);
  const from = new Int32Array(N).fill(-1);
  const closed = new Uint8Array(N);
  const start = s[0] * p.w + s[1];
  const goal = g[0] * p.w + g[1];
  gs[start] = 0;
  const open: [number, number][] = [[0, start]];
  const hfun = (i: number) => Math.hypot(Math.floor(i / p.w) - g[0], (i % p.w) - g[1]);
  while (open.length) {
    // binary heap would be faster; grids here are small (< 15k cells)
    let bi = 0;
    for (let k = 1; k < open.length; k++) if (open[k][0] < open[bi][0]) bi = k;
    const [, i] = open[bi];
    open[bi] = open[open.length - 1];
    open.pop();
    if (closed[i]) continue;
    closed[i] = 1;
    if (i === goal) break;
    const r = Math.floor(i / p.w);
    const c = i % p.w;
    for (let dr = -1; dr <= 1; dr++)
      for (let dc = -1; dc <= 1; dc++) {
        if (!dr && !dc) continue;
        if (!freeAt(p, r + dr, c + dc)) continue;
        if (dr && dc && (!freeAt(p, r + dr, c) || !freeAt(p, r, c + dc))) continue;
        const j = (r + dr) * p.w + c + dc;
        const ng = gs[i] + (dr && dc ? Math.SQRT2 : 1) * (1 + p.penalty[j]);
        if (ng < gs[j]) {
          gs[j] = ng;
          from[j] = i;
          open.push([ng + hfun(j), j]);
        }
      }
  }
  if (!closed[goal]) return null;
  const cells: number[] = [];
  for (let i = goal; i !== -1; i = from[i]) cells.push(i);
  cells.reverse();
  const pts = cells.map((i) => toWorld(p, Math.floor(i / p.w), i % p.w));
  return smooth(p, [a, ...pts, b]);
}

function lineFree(p: Planner, a: XY, b: XY): boolean {
  const n = Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1]) / (COARSE / 2));
  for (let k = 1; k < n; k++) {
    const t = k / n;
    const [r, c] = toCell(p, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t);
    if (!freeAt(p, r, c)) return false;
  }
  return true;
}

function smooth(p: Planner, pts: XY[]): XY[] {
  if (pts.length <= 2) return pts;
  const out: XY[] = [pts[0]];
  let i = 0;
  while (i < pts.length - 1) {
    let j = pts.length - 1;
    while (j > i + 1 && !lineFree(p, pts[i], pts[j])) j--;
    out.push(pts[j]);
    i = j;
  }
  return out;
}

export const pathLength = (pts: XY[]) => pts.reduce((s, p, i) => (i ? s + Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) : 0), 0);

export function robotRadius(robot: RobotSpec): number {
  const poly = robot.footprint_polygon_m ?? [];
  if (!poly.length) return 0.3;
  return Math.max(...poly.map(([, y]) => Math.abs(y))) + 0.03;
}

export function missionPath(twin: Twin, planner: Planner, sequence: string[]): XY[] | null {
  const wps = new Map((twin.waypoints ?? []).map((w) => [w.id, w.xy]));
  const pts: XY[] = [];
  for (let k = 0; k + 1 < sequence.length; k++) {
    const a = wps.get(sequence[k]);
    const b = wps.get(sequence[k + 1]);
    if (!a || !b) return null;
    const leg = planPath(planner, a, b);
    if (!leg) return null;
    pts.push(...(k ? leg.slice(1) : leg));
  }
  return pts;
}

// ---------------------------------------------------------------- pedestrians

interface Ped {
  x: number;
  y: number;
  tx: number;
  ty: number;
  v: number;
  hold: number; // seconds to stand still (used to create a pinch)
  controlled?: boolean;
  vx?: number;
  vy?: number;
}

function zonePoint(rnd: () => number, poly: XY[]): XY {
  const xs = poly.map((p) => p[0]);
  const ys = poly.map((p) => p[1]);
  return [Math.min(...xs) + rnd() * (Math.max(...xs) - Math.min(...xs)), Math.min(...ys) + rnd() * (Math.max(...ys) - Math.min(...ys))];
}

class Crowd {
  peds: Ped[] = [];
  private acc = 0;
  constructor(
    private twin: Twin,
    private rnd: () => number,
    private perMin: number,
  ) {}
  spawn(): void {
    const tr = this.twin.traffic;
    const zones = new Map((tr?.zones ?? []).map((z) => [z.id, z.polygon]));
    const pairs = tr?.od_pairs ?? [];
    if (!pairs.length) return;
    const total = pairs.reduce((s, p) => s + p[2], 0);
    let pick = this.rnd() * total;
    let pair = pairs[0];
    for (const p of pairs) {
      pick -= p[2];
      if (pick <= 0) {
        pair = p;
        break;
      }
    }
    const a = zones.get(pair[0]);
    const b = zones.get(pair[1]);
    if (!a || !b) return;
    const [x, y] = zonePoint(this.rnd, a);
    const [tx, ty] = zonePoint(this.rnd, b);
    const [m, s] = tr?.walk_speed_mps ?? [1.2, 0.25];
    this.peds.push({ x, y, tx, ty, v: Math.max(0.5, m + s * gauss(this.rnd)), hold: 0 });
  }
  step(dt: number, isFree: (x: number, y: number) => boolean): void {
    this.acc += (this.perMin / 60) * dt;
    while (this.acc >= 1) {
      this.acc -= 1;
      if (this.peds.filter((p) => !p.controlled).length < 12) this.spawn();
    }
    for (const p of this.peds) {
      if (p.controlled) {
        const nx = p.x + (p.vx ?? 0) * dt;
        const ny = p.y + (p.vy ?? 0) * dt;
        if (isFree(nx, ny)) {
          p.x = nx;
          p.y = ny;
        } else if (isFree(nx, p.y)) p.x = nx;
        else if (isFree(p.x, ny)) p.y = ny;
        continue;
      }
      if (p.hold > 0) {
        p.hold -= dt;
        continue;
      }
      const dx = p.tx - p.x;
      const dy = p.ty - p.y;
      const d = Math.hypot(dx, dy);
      if (d < 0.1) {
        p.v = 0;
        continue;
      }
      const s = Math.min(d, p.v * dt);
      p.x += (dx / d) * s;
      p.y += (dy / d) * s;
    }
    this.peds = this.peds.filter((p) => p.controlled || p.v > 0 || p.hold > 0);
  }
}

// ---------------------------------------------------------------- robot mission

export interface MissionOptions {
  seed: number;
  density: "none" | "quiet" | "normal" | "peak";
  speed: number;
  safetyStop: number;
  /** stand a pedestrian at this point for a long time (creates a blocked_timeout failure) */
  pinchXY?: XY | null;
  dt: number;
}

export class Mission {
  t = 0;
  x: number;
  y: number;
  yaw: number;
  v = 0;
  state: "moving" | "stopped" | "rotating" | "done" | "failed" = "moving";
  private seg = 1;
  private blocked = 0;
  private stopped = false;
  private lastNear = new Map<Ped, number>();
  private pinchPlaced = false;
  readonly crowd: Crowd;
  readonly timeout: number;

  constructor(
    private path: XY[],
    private o: MissionOptions,
    twin: Twin,
    private grid: OccGrid,
  ) {
    const rnd = mulberry32(o.seed);
    const ppm = o.density === "none" ? 0 : (twin.traffic?.persons_per_min?.[o.density] ?? 4);
    this.crowd = new Crowd(twin, rnd, ppm);
    for (let k = 0; k < Math.round(ppm / 3); k++) this.crowd.spawn();
    [this.x, this.y] = path[0];
    this.yaw = Math.atan2(path[1][1] - path[0][1], path[1][0] - path[0][0]);
    this.timeout = (3 * pathLength(path)) / o.speed + 10;
  }

  isFree = (x: number, y: number) => {
    const [r, c] = worldToCell(this.grid, x, y);
    return !isBlocked(this.grid, r, c);
  };

  step(): EpisodeEvent[] {
    const { dt } = this.o;
    const ev: EpisodeEvent[] = [];
    this.t += dt;
    this.crowd.step(dt, this.isFree);
    if (this.state === "done" || this.state === "failed") return ev;
    const tgt = this.path[this.seg];
    if (!this.pinchPlaced && this.o.pinchXY && Math.hypot(this.x - this.o.pinchXY[0], this.y - this.o.pinchXY[1]) < 1.6) {
      const [px, py] = this.o.pinchXY;
      this.crowd.peds.push({ x: px, y: py, tx: px, ty: py, v: 0.01, hold: 60 });
      this.pinchPlaced = true;
    }
    const want = Math.atan2(tgt[1] - this.y, tgt[0] - this.x);
    let err = want - this.yaw;
    while (err > Math.PI) err -= 2 * Math.PI;
    while (err < -Math.PI) err += 2 * Math.PI;
    const maxTurn = 1.6 * dt;
    this.yaw += Math.max(-maxTurn, Math.min(maxTurn, err));
    // safety stop: pedestrian inside stop distance in front of the robot
    const hx = Math.cos(this.yaw);
    const hy = Math.sin(this.yaw);
    let blockedNow = false;
    for (const p of this.crowd.peds) {
      const dx = p.x - this.x;
      const dy = p.y - this.y;
      const d = Math.hypot(dx, dy);
      if (d < this.o.safetyStop + 0.3 && dx * hx + dy * hy > 0.05) blockedNow = true;
      if (d < 0.5 && this.v > 0.3) {
        const last = this.lastNear.get(p) ?? -99;
        if (this.t - last > 3) ev.push({ type: "near_miss", xy: [this.x, this.y], detail: { distance_m: Number(d.toFixed(2)) } });
        this.lastNear.set(p, this.t);
      }
    }
    if (blockedNow) {
      if (!this.stopped) ev.push({ type: "stop", xy: [this.x, this.y], detail: { reason: "pedestrian_in_stop_zone" } });
      this.stopped = true;
      this.state = "stopped";
      this.v = 0;
      this.blocked += dt;
      if (Math.abs(this.blocked - 10) < dt / 2) ev.push({ type: "replan", xy: [this.x, this.y], detail: { blocked_s: 10 } });
      if (this.blocked > 20) {
        this.state = "failed";
        ev.push({ type: "fail", xy: [this.x, this.y], detail: { reason: "blocked_timeout" } });
      }
    } else {
      this.stopped = false;
      this.blocked = 0;
      if (Math.abs(err) > 0.6) {
        this.state = "rotating";
        this.v = 0;
      } else {
        this.state = "moving";
        this.v = Math.min(this.o.speed, this.v + 0.8 * dt);
        const d = Math.hypot(tgt[0] - this.x, tgt[1] - this.y);
        const s = Math.min(d, this.v * dt);
        this.x += Math.cos(want) * s;
        this.y += Math.sin(want) * s;
        if (d - s < 0.02) {
          this.seg++;
          if (this.seg >= this.path.length) {
            this.state = "done";
            this.v = 0;
            ev.push({ type: "success", xy: [this.x, this.y], detail: { mission_s: Number(this.t.toFixed(1)) } });
          }
        }
      }
    }
    if (this.t > this.timeout && this.state !== "done" && this.state !== "failed") {
      this.state = "failed";
      ev.push({ type: "fail", xy: [this.x, this.y], detail: { reason: "blocked_timeout" } });
    }
    return ev;
  }
}

export function runEpisode(twin: Twin, grid: OccGrid, path: XY[], o: Omit<MissionOptions, "dt">): EpisodeFrame[] {
  const m = new Mission(path, { ...o, dt: 0.1 }, twin, grid);
  const frames: EpisodeFrame[] = [];
  const r2 = (v: number) => Math.round(v * 100) / 100;
  frames.push({ t: 0, robot: [r2(m.x), r2(m.y), r2(m.yaw), 0], peds: m.crowd.peds.map((p) => [r2(p.x), r2(p.y)]), events: [] });
  for (let k = 0; k < 3000; k++) {
    const ev = m.step();
    frames.push({
      t: Math.round(m.t * 10) / 10,
      robot: [r2(m.x), r2(m.y), r2(m.yaw), r2(m.v)],
      peds: m.crowd.peds.map((p) => [r2(p.x), r2(p.y)]),
      events: ev.map((e) => ({ ...e, xy: [r2(e.xy[0]), r2(e.xy[1])] })),
    });
    if (m.state === "done" || m.state === "failed") break;
  }
  return frames;
}

/** Offline stand-in for the server-side LiveSession (20 Hz, one joystick-controlled pedestrian). */
export class LiveModel {
  private mission: Mission;
  private joy: Ped;
  private laps = 0;
  constructor(
    private twin: Twin,
    private grid: OccGrid,
    private path: XY[],
    private o: Omit<MissionOptions, "dt" | "pinchXY">,
  ) {
    this.mission = new Mission(path, { ...o, dt: 0.05 }, twin, grid);
    const start = (twin.waypoints ?? []).find((w) => w.kind === "entrance")?.xy ?? path[Math.floor(path.length / 2)];
    this.joy = { x: start[0], y: start[1], tx: 0, ty: 0, v: 0, hold: 0, controlled: true, vx: 0, vy: 0 };
    this.mission.crowd.peds.unshift(this.joy);
  }
  setJoystick(vx: number, vy: number) {
    const s = Math.hypot(vx, vy);
    const k = s > 1.4 ? 1.4 / s : 1;
    this.joy.vx = vx * k;
    this.joy.vy = vy * k;
  }
  step(): LiveFrame {
    const ev = this.mission.step();
    if (this.mission.state === "done" || this.mission.state === "failed") {
      const t = this.mission.t;
      this.laps++;
      this.mission = new Mission(this.path, { ...this.o, seed: this.o.seed + this.laps, dt: 0.05 }, this.twin, this.grid);
      this.mission.t = t;
      this.mission.crowd.peds.unshift(this.joy);
    }
    const m = this.mission;
    return {
      t: Math.round(m.t * 100) / 100,
      robot: { x: m.x, y: m.y, yaw: m.yaw, v: m.v, state: m.state },
      peds: m.crowd.peds.map((p) => [p.x, p.y] as XY),
      events: ev,
      controlled_ped: 0,
    };
  }
}
