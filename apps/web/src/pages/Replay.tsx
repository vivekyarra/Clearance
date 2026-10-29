import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { TwinView } from "../components/TwinView";
import { ErrorBox, EstimateBadge, Loading } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { type Palette, type View, drawPed, drawRobot, w2s, withAlpha } from "../lib/draw";
import { robotFootprint } from "../lib/geometry";
import type { OccGrid } from "../lib/grid";
import { pct } from "../lib/stats";
import type { EpisodeEvent, EpisodeFrame, RobotSpec, SimBatch, Twin, XY } from "../types";

const Scene3D = lazy(() => import("../three/Scene3D"));
const SplatView = lazy(() => import("../three/SplatView"));

interface Data {
  batch: SimBatch;
  frames: EpisodeFrame[];
  twin: Twin;
  grid: OccGrid;
  robot: RobotSpec | null;
}

async function findTwin(batch: SimBatch, siteHint: string | null, analysis: string | null): Promise<Twin> {
  let site = siteHint;
  if (!site && analysis) site = (await api.getAnalysis(analysis).catch(() => null))?.site_id ?? null;
  if (site) return api.getTwin(site, batch.twin_version);
  for (const s of await api.listSites()) {
    const t = await api.getTwin(s.site_id, batch.twin_version).catch(() => null);
    if (t && t.twin_id === batch.twin_id) return t;
  }
  throw new Error(`twin ${batch.twin_id} v${batch.twin_version} not found`);
}

export default function ReplayPage() {
  const { batchId = "", n = "0" } = useParams();
  const [q] = useSearchParams();
  const nav = useNavigate();
  const ep = Math.max(0, Number(n) || 0);
  const data = useAsync<Data>(async () => {
    const batch = await api.getBatch(batchId);
    const [frames, twin, robots] = await Promise.all([api.getEpisode(batchId, ep), findTwin(batch, q.get("site"), q.get("analysis")), api.listRobots().catch(() => [])]);
    const grid = await api.occupancy(twin);
    return { batch, frames, twin, grid, robot: robots.find((r) => r.robot_id === batch.robot_id) ?? null };
  }, [batchId, ep]);
  const go = (k: number) => nav(`/replay/${encodeURIComponent(batchId)}/${k}?${q.toString()}`);

  if (data.error) return <main className="page"><ErrorBox error={data.error} onRetry={data.reload} /></main>;
  if (!data.data) return <Loading label="Loading episode" />;
  return <Player d={data.data} ep={ep} go={go} analysis={q.get("analysis")} />;
}

interface Marker {
  e: EpisodeEvent;
  t: number;
}

function Player({ d, ep, go, analysis }: { d: Data; ep: number; go: (n: number) => void; analysis: string | null }) {
  const { frames, batch, twin, grid, robot } = d;
  const dur = frames.length ? frames[frames.length - 1].t : 0;
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(2);
  const [mode, setMode] = useState<"2d" | "3d" | "splat">("2d");
  const tRef = useRef(0);
  tRef.current = t;
  const foot = useMemo(() => robotFootprint(robot?.footprint_polygon_m), [robot]);
  const markers = useMemo<Marker[]>(() => frames.flatMap((f) => f.events.map((e) => ({ e, t: f.t }))), [frames]);
  const outcome = markers.find((m) => m.e.type === "fail") ? "fail" : markers.find((m) => m.e.type === "success") ? "success" : "running";
  const failReason = (markers.find((m) => m.e.type === "fail")?.e.detail as { reason?: string } | undefined)?.reason;

  useEffect(() => {
    setT(0);
    setPlaying(true);
  }, [ep, batch.batch_id]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const loop = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      const nt = Math.min(dur, tRef.current + dt * speed);
      setT(nt);
      if (nt >= dur) setPlaying(false);
      else raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, dur]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,select")) return;
      if (e.code === "Space") {
        e.preventDefault();
        setPlaying((p) => (tRef.current >= dur ? (setT(0), true) : !p));
      }
      if (e.key === "ArrowRight") setT((x) => Math.min(dur, x + 1));
      if (e.key === "ArrowLeft") setT((x) => Math.max(0, x - 1));
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [dur]);

  const state = useMemo(() => interp(frames, t), [frames, t]);
  const trail = useMemo(() => frames.filter((f) => f.t <= t).map((f) => [f.robot[0], f.robot[1]] as XY), [frames, t]);

  const overlay = useCallback(
    (ctx: CanvasRenderingContext2D, v: View, pal: Palette) => {
      if (!state) return;
      // trail
      ctx.save();
      ctx.strokeStyle = withAlpha(pal.accent, 0.55);
      ctx.lineWidth = 2;
      ctx.beginPath();
      trail.forEach(([x, y], i) => {
        const [sx, sy] = w2s(grid, v, x, y);
        if (i) ctx.lineTo(sx, sy);
        else ctx.moveTo(sx, sy);
      });
      ctx.stroke();
      ctx.restore();
      // event markers
      for (const m of markers) {
        if (m.t > t) continue;
        const age = t - m.t;
        const [sx, sy] = w2s(grid, v, m.e.xy[0], m.e.xy[1]);
        ctx.save();
        if (m.e.type === "stop") {
          const a = Math.max(0.25, 1 - age / 3);
          ctx.strokeStyle = withAlpha(pal.amber, a);
          ctx.fillStyle = withAlpha(pal.amber, a * 0.25);
          ctx.lineWidth = 2.5;
          ctx.beginPath();
          ctx.arc(sx, sy, 14, 0, Math.PI * 2);
          ctx.fill();
          ctx.stroke();
        } else if (m.e.type === "near_miss" || m.e.type === "collision") {
          const pulse = age < 1.5 ? 10 + (age % 0.5) * 30 : 6;
          ctx.strokeStyle = withAlpha(pal.red, age < 1.5 ? 1 - (age % 0.5) * 1.6 : 0.8);
          ctx.fillStyle = pal.red;
          ctx.lineWidth = 2.5;
          ctx.beginPath();
          ctx.arc(sx, sy, pulse, 0, Math.PI * 2);
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(sx, sy, 3.5, 0, Math.PI * 2);
          ctx.fill();
        } else if (m.e.type === "fail") {
          ctx.strokeStyle = pal.red;
          ctx.lineWidth = 3.5;
          ctx.beginPath();
          ctx.moveTo(sx - 9, sy - 9);
          ctx.lineTo(sx + 9, sy + 9);
          ctx.moveTo(sx + 9, sy - 9);
          ctx.lineTo(sx - 9, sy + 9);
          ctx.stroke();
          const label = `fail: ${String((m.e.detail as { reason?: string })?.reason ?? "")}`;
          ctx.font = "700 12px Inter, system-ui, sans-serif";
          const tw = ctx.measureText(label).width;
          ctx.fillStyle = pal.red;
          ctx.fillRect(sx + 14, sy - 22, tw + 12, 20);
          ctx.fillStyle = "#fff";
          ctx.fillText(label, sx + 20, sy - 8);
        } else if (m.e.type === "success") {
          ctx.fillStyle = pal.green;
          ctx.beginPath();
          ctx.arc(sx, sy, 9, 0, Math.PI * 2);
          ctx.fill();
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 2.5;
          ctx.beginPath();
          ctx.moveTo(sx - 4, sy);
          ctx.lineTo(sx - 1, sy + 3);
          ctx.lineTo(sx + 4, sy - 3);
          ctx.stroke();
        } else if (m.e.type === "replan") {
          ctx.fillStyle = pal.accent;
          ctx.beginPath();
          ctx.arc(sx, sy, 4, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.restore();
      }
      for (const p of state.peds) drawPed(ctx, grid, v, p, pal);
      const [x, y, yaw, vel] = state.robot;
      const stopped = vel < 0.02 && t > 0.5 && t < dur;
      drawRobot(ctx, grid, v, foot.poly, x, y, yaw, pal, stopped ? pal.amber : pal.accent);
      return true;
    },
    [state, trail, markers, t, grid, foot, dur],
  );

  const res = batch.results;
  const splat = (twin.assets?.splat && (api.twinAssetUrl?.(twin, "splat") ?? twin.assets.splat)) || undefined;
  return (
    <main className="page" style={{ maxWidth: 1320 }}>
      <div className="crumbs">
        <Link to="/">Sites</Link> / <Link to={`/sites/${twin.site_id}`}>{twin.site_id}</Link>
        {analysis && (
          <>
            {" "}
            / <Link to={`/analyses/${analysis}`}>Clearance Report</Link>
          </>
        )}{" "}
        / Replay
      </div>
      <div className="page-head">
        <div>
          <div className="row">
            <h1>{robot ? `${robot.vendor} ${robot.model}` : batch.robot_id}</h1>
            <EstimateBadge />
            <span className={`badge ${outcome === "fail" ? "red" : outcome === "success" ? "green" : ""}`}>{outcome === "fail" ? `Failed${failReason ? `: ${failReason.replace(/_/g, " ")}` : ""}` : outcome === "success" ? "Mission complete" : "In progress"}</span>
          </div>
          <div className="sub">
            Episode {ep + 1} of {batch.n_episodes} · {batch.density} traffic · {batch.geometry_bound} geometry · batch <span className="mono">{batch.batch_id}</span>
            {res ? ` · batch success ${pct(res.success_rate)} (n=${res.n_episodes ?? batch.n_episodes})` : ""}
          </div>
        </div>
        <div className="row">
          <div className="seg">
            <button className={mode === "2d" ? "on" : ""} onClick={() => setMode("2d")}>
              Top-down
            </button>
            <button className={mode === "3d" ? "on" : ""} onClick={() => setMode("3d")}>
              3D
            </button>
            <button className={mode === "splat" ? "on" : ""} disabled={!splat} title={splat ? "Photoreal splat of the scanned room" : "No splat for this twin yet"} onClick={() => setMode("splat")}>
              Splat
            </button>
          </div>
          <button className="btn sm" disabled={ep <= 0} onClick={() => go(ep - 1)}>
            <Icon name="chevronLeft" size={14} /> Prev
          </button>
          <button className="btn sm" disabled={ep + 1 >= batch.n_episodes} onClick={() => go(ep + 1)}>
            Next <Icon name="chevronRight" size={14} />
          </button>
        </div>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        {mode === "2d" && (
          <TwinView
            twin={twin}
            grid={grid}
            height="min(64vh, 640px)"
            overlay={overlay}
            className="replay-map"
            legend={
              <>
                <span>
                  <i style={{ background: "var(--accent)" }} />
                  robot ({foot.approx ? "standard 0.5 m footprint" : robot?.footprint_approx ? "footprint from vendor L×W" : "spec footprint"})
                </span>
                <span>
                  <i style={{ background: "var(--fg-muted)", borderRadius: 5 }} />
                  pedestrian
                </span>
                <span>
                  <i style={{ background: "var(--amber)" }} />
                  safety stop
                </span>
                <span>
                  <i style={{ background: "var(--red)" }} />
                  near-miss / fail
                </span>
              </>
            }
          />
        )}
        {mode === "3d" && (
          <Suspense fallback={<div className="skeleton" style={{ height: 560 }} />}>
            <Scene3D twin={twin} grid={grid} foot={foot.poly} robotHeight={robot?.height_m ?? 1.0} state={state} height={560} />
          </Suspense>
        )}
        {mode === "splat" && splat && (
          <Suspense fallback={<div className="skeleton" style={{ height: 560 }} />}>
            <SplatView url={splat} height={560} />
          </Suspense>
        )}
        <div className="card-body stack-sm" style={{ borderTop: "1px solid var(--border)" }}>
          <div className="row" style={{ gap: 12 }}>
            <button
              className="btn primary icon"
              onClick={() => {
                if (t >= dur) setT(0);
                setPlaying((p) => !p);
              }}
              title="Play / pause (Space)"
            >
              <Icon name={playing ? "pause" : "play"} />
            </button>
            <div style={{ position: "relative", flex: 1, height: 28, display: "flex", alignItems: "center" }}>
              <input type="range" min={0} max={dur || 1} step={0.05} value={t} onChange={(e) => (setPlaying(false), setT(Number(e.target.value)))} style={{ width: "100%", accentColor: "var(--accent)" }} aria-label="Scrub" />
              {markers
                .filter((m) => m.e.type !== "replan")
                .map((m, i) => (
                  <span
                    key={i}
                    title={`${m.e.type} at ${m.t.toFixed(1)} s`}
                    onClick={() => setT(m.t)}
                    style={{
                      position: "absolute",
                      left: `calc(${(m.t / (dur || 1)) * 100}% - 3px)`,
                      top: 0,
                      width: 6,
                      height: 6,
                      borderRadius: 3,
                      cursor: "pointer",
                      background: m.e.type === "stop" ? "var(--amber)" : m.e.type === "success" ? "var(--green)" : "var(--red)",
                    }}
                  />
                ))}
            </div>
            <span className="mono small num" style={{ width: 96, textAlign: "right" }}>
              {t.toFixed(1)} / {dur.toFixed(1)} s
            </span>
            <select className="select sm" style={{ width: 80 }} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Speed">
              {[0.5, 1, 2, 4, 8].map((s) => (
                <option key={s} value={s}>
                  {s}×
                </option>
              ))}
            </select>
          </div>
          <div className="row small muted" style={{ gap: 18 }}>
            <span>speed {state ? state.robot[3].toFixed(2) : "0.00"} m/s</span>
            <span>{markers.filter((m) => m.e.type === "stop" && m.t <= t).length} stops</span>
            <span>{markers.filter((m) => m.e.type === "near_miss" && m.t <= t).length} near-misses</span>
            <span>{state?.peds.length ?? 0} pedestrians</span>
            <span style={{ marginLeft: "auto" }}>Space play/pause · ← → seek 1 s</span>
          </div>
        </div>
      </div>
    </main>
  );
}

export function interp(frames: EpisodeFrame[], t: number): { robot: [number, number, number, number]; peds: XY[] } | null {
  if (!frames.length) return null;
  let lo = 0;
  let hi = frames.length - 1;
  if (t <= frames[0].t) return frames[0];
  if (t >= frames[hi].t) return frames[hi];
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (frames[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const a = frames[lo];
  const b = frames[hi];
  const k = (t - a.t) / Math.max(1e-6, b.t - a.t);
  let dyaw = b.robot[2] - a.robot[2];
  while (dyaw > Math.PI) dyaw -= 2 * Math.PI;
  while (dyaw < -Math.PI) dyaw += 2 * Math.PI;
  const robot: [number, number, number, number] = [a.robot[0] + (b.robot[0] - a.robot[0]) * k, a.robot[1] + (b.robot[1] - a.robot[1]) * k, a.robot[2] + dyaw * k, a.robot[3] + (b.robot[3] - a.robot[3]) * k];
  const peds = a.peds.length === b.peds.length ? a.peds.map((p, i) => [p[0] + (b.peds[i][0] - p[0]) * k, p[1] + (b.peds[i][1] - p[1]) * k] as XY) : a.peds;
  return { robot, peds };
}
