import { useCallback, useEffect, useMemo, useRef } from "react";
import { type Palette, type View, drawRobot, polyPath, w2s, withAlpha } from "../../lib/draw";
import { normAngle, robotFootprint } from "../../lib/geometry";
import type { OccGrid } from "../../lib/grid";
import { makePlanner, planPath } from "../../offline/sim";
import type { RobotSpec, Twin, XY } from "../../types";
import { TwinView } from "../TwinView";
import { type Motion, type Route, buildRoute, loopDuration, poseAt } from "./route";

/** Neutral body tints for the robots (readable on light and dark maps). */
const TINTS = ["#6f7b8c", "#8c8173", "#66817a", "#7d7390"];
const MOTION: Motion = { speed: 1.5, pause: 0.9 };
const GHOST_LIFE = 2.6;
const MAX_GHOSTS = 28;

interface Ghost {
  born: number;
  robot: number;
  phase: number;
  side: number;
}

export interface RobotStageProps {
  twin: Twin;
  grid: OccGrid;
  robots: RobotSpec[];
  sequence: string[];
  pinch: { xy: XY; gap: number | null } | null;
  /** completed simulation batches; each increase releases a burst of ghost runs */
  batchesDone: number;
  reduced: boolean;
  caption?: string | null;
  height: number;
}

export function planRoute(twin: Twin, grid: OccGrid, sequence: string[]): Route | null {
  const wps = new Map((twin.waypoints ?? []).map((w) => [w.id, w.xy]));
  const stops = sequence.map((id) => wps.get(id)).filter((p): p is XY => !!p);
  if (stops.length < 2) return null;
  let planner: ReturnType<typeof makePlanner> | null = null;
  try {
    planner = makePlanner(grid, 0.16);
  } catch {
    planner = null;
  }
  const legs: XY[][] = [];
  for (let k = 0; k + 1 < stops.length; k++) {
    let leg: XY[] | null = null;
    try {
      leg = planner ? planPath(planner, stops[k], stops[k + 1]) : null;
    } catch {
      leg = null;
    }
    // keep the exact waypoint positions at both ends so the pauses line up with the markers
    legs.push(leg && leg.length >= 2 ? [stops[k], ...leg.slice(1, -1), stops[k + 1]] : [stops[k], stops[k + 1]]);
  }
  return buildRoute(legs);
}

export function RobotStage(p: RobotStageProps) {
  const route = useMemo(() => planRoute(p.twin, p.grid, p.sequence), [p.twin, p.grid, p.sequence]);
  const feet = useMemo(() => p.robots.slice(0, 4).map((r) => ({ name: r.model || r.robot_id, poly: robotFootprint(r.footprint_polygon_m).poly })), [p.robots]);
  const scene = useRef({ route, feet, pinch: p.pinch, reduced: p.reduced, ghosts: [] as Ghost[], yaw: [] as number[], last: 0 });
  scene.current.route = route;
  scene.current.feet = feet;
  scene.current.pinch = p.pinch;
  scene.current.reduced = p.reduced;

  const prevBatches = useRef(p.batchesDone);
  useEffect(() => {
    const d = p.batchesDone - prevBatches.current;
    prevBatches.current = p.batchesDone;
    if (d <= 0 || p.reduced || !route) return;
    const s = scene.current;
    const now = performance.now() / 1000;
    const T = loopDuration(route, MOTION);
    const n = Math.min(10, 3 + 2 * d);
    for (let i = 0; i < n; i++) s.ghosts.push({ born: now + i * 0.06, robot: Math.floor(Math.random() * Math.max(1, s.feet.length)), phase: Math.random() * T, side: (Math.random() - 0.5) * 0.5 });
    if (s.ghosts.length > MAX_GHOSTS) s.ghosts.splice(0, s.ghosts.length - MAX_GHOSTS);
  }, [p.batchesDone, p.reduced, route]);

  // Animated mode keeps one overlay function for the life of the view (no effect restarts); the
  // reduced-motion static frame is redrawn when its inputs change.
  const staticKey = p.reduced ? `${p.pinch?.xy.join(",")}|${feet.length}|${route?.length}` : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const overlay = useCallback((ctx: CanvasRenderingContext2D, v: View, pal: Palette) => drawScene(ctx, v, pal, p.grid, scene.current), [p.grid, staticKey]);

  return (
    <div className="cp-map">
      <TwinView
        twin={p.twin}
        grid={p.grid}
        height={p.height}
        overlay={overlay}
        animate={!p.reduced}
        showHazards={false}
        legend={
          <>
            <span>
              <i style={{ background: TINTS[0] }} />
              Candidate robots
            </span>
            <span>
              <i style={{ background: "var(--fg-faint)", opacity: 0.5 }} />
              Simulated runs
            </span>
            {p.pinch && (
              <span>
                <i style={{ background: "var(--amber)", borderRadius: 99 }} />
                Narrowest gap
              </span>
            )}
          </>
        }
      />
      {p.caption && (
        <div className="cp-caption" key={p.caption} aria-live="polite">
          <span className="cp-caption-tag">Nemotron hypothesis</span>
          <span className="cp-caption-text">{p.caption}</span>
        </div>
      )}
    </div>
  );
}

function drawScene(ctx: CanvasRenderingContext2D, v: View, pal: Palette, g: OccGrid, s: { route: Route | null; feet: { name: string; poly: XY[] }[]; pinch: { xy: XY; gap: number | null } | null; reduced: boolean; ghosts: Ghost[]; yaw: number[]; last: number }): boolean {
  const now = s.reduced ? 0 : performance.now() / 1000;
  const dt = s.last ? Math.min(0.1, now - s.last) : 0;
  s.last = now;
  const r = s.route;
  if (r) {
    ctx.save();
    ctx.setLineDash([6, 6]);
    ctx.lineDashOffset = -now * 18;
    ctx.strokeStyle = withAlpha(pal.muted, 0.55);
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    polyPath(ctx, g, v, r.pts, false);
    ctx.stroke();
    ctx.restore();
  }
  if (s.pinch) {
    const [sx, sy] = w2s(g, v, s.pinch.xy[0], s.pinch.xy[1]);
    const k = s.reduced ? 0.5 : 0.5 + 0.5 * Math.sin(now * 3.2);
    ctx.save();
    ctx.fillStyle = withAlpha(pal.amber, 0.12 + 0.14 * k);
    ctx.strokeStyle = pal.amber;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(sx, sy, 9 + 7 * k, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = pal.amber;
    ctx.beginPath();
    ctx.arc(sx, sy, 3.5, 0, Math.PI * 2);
    ctx.fill();
    label(ctx, `narrowest gap${s.pinch.gap != null ? ` ${s.pinch.gap.toFixed(2)} m` : ""}`, sx, sy - 22, pal, pal.amber);
    ctx.restore();
  }
  if (!r || !s.feet.length) return false;
  const T = loopDuration(r, MOTION);
  // ghost runs (Monte Carlo feel): translucent footprints spread around the route, fading out
  s.ghosts = s.ghosts.filter((gh) => now - gh.born < GHOST_LIFE);
  for (const gh of s.ghosts) {
    const age = now - gh.born;
    if (age < 0) continue;
    const a = Math.min(1, age / 0.3) * (1 - age / GHOST_LIFE);
    const ps = poseAt(r, MOTION, now * 1.15 + gh.phase);
    const ox = -Math.sin(ps.yaw) * gh.side;
    const oy = Math.cos(ps.yaw) * gh.side;
    ctx.save();
    ctx.globalAlpha = 0.32 * a;
    drawRobot(ctx, g, v, s.feet[gh.robot % s.feet.length].poly, ps.x + ox, ps.y + oy, ps.yaw, pal, pal.muted);
    ctx.restore();
  }
  // the candidate robots, evenly spaced in time along the loop
  const n = s.feet.length;
  const labels: [string, number, number][] = [];
  s.feet.forEach((f, i) => {
    const ps = poseAt(r, MOTION, now + (i * T) / n);
    const prev = s.yaw[i];
    const yaw = prev == null || s.reduced ? ps.yaw : prev + normAngle(ps.yaw - prev) * Math.min(1, dt * 9);
    s.yaw[i] = yaw;
    drawRobot(ctx, g, v, f.poly, ps.x, ps.y, yaw, pal, TINTS[i % TINTS.length]);
    const [sx, sy] = w2s(g, v, ps.x, ps.y);
    const rad = (Math.max(...f.poly.map(([x, y]) => Math.hypot(x, y))) / g.res) * v.scale;
    labels.push([f.name, sx, sy - rad - 10]);
  });
  for (const [t, x, y] of labels) label(ctx, t, x, y, pal, pal.fg);
  return !s.reduced;
}

function label(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, pal: Palette, color: string) {
  ctx.save();
  ctx.font = "600 11px Inter, system-ui, sans-serif";
  const w = ctx.measureText(text).width + 12;
  ctx.fillStyle = withAlpha(pal.elev, 0.92);
  ctx.strokeStyle = withAlpha(color, 0.35);
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x - w / 2, y - 9, w, 18, 9);
  else ctx.rect(x - w / 2, y - 9, w, 18);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, x, y + 0.5);
  ctx.restore();
}
