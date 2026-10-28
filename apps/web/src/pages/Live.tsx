import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { ConnState } from "../api/types";
import { Icon } from "../components/Icon";
import { TwinView } from "../components/TwinView";
import { ErrorBox, Loading, QR } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { type Palette, type View, drawPed, drawRobot, w2s, withAlpha } from "../lib/draw";
import { robotFootprint } from "../lib/geometry";
import type { EpisodeEvent, LiveFrame } from "../types";

interface LoggedEvent extends EpisodeEvent {
  t: number;
}

export default function LivePage() {
  const { session = "" } = useParams();
  const setup = useAsync(async () => {
    const info = await api.getLiveInfo(session);
    const twin = await api.getTwin(info.site_id);
    const [grid, robots] = await Promise.all([api.occupancy(twin), api.listRobots().catch(() => [])]);
    return { info, twin, grid, robot: robots.find((r) => r.robot_id === info.robot_id) ?? null };
  }, [session]);
  const [frame, setFrame] = useState<LiveFrame | null>(null);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [log, setLog] = useState<LoggedEvent[]>([]);
  const connRef = useRef<ReturnType<typeof api.openLive> | null>(null);
  const keys = useRef(new Set<string>());

  useEffect(() => {
    if (!setup.data) return;
    const c = api.openLive(session, "viewer");
    connRef.current = c;
    c.onState(setConn);
    c.onFrame((f) => {
      setFrame(f);
      if (f.events?.length) setLog((l) => [...f.events.map((e) => ({ ...e, t: f.t })), ...l].slice(0, 200));
    });
    return () => c.close();
  }, [setup.data, session]);

  // keyboard steering on the console itself (WASD / arrows) for desktop testing
  useEffect(() => {
    const send = () => {
      const k = keys.current;
      const vx = (k.has("d") || k.has("arrowright") ? 1 : 0) - (k.has("a") || k.has("arrowleft") ? 1 : 0);
      const vy = (k.has("w") || k.has("arrowup") ? 1 : 0) - (k.has("s") || k.has("arrowdown") ? 1 : 0);
      const n = Math.hypot(vx, vy) || 1;
      connRef.current?.sendJoystick((vx / n) * 1.2, (vy / n) * 1.2);
    };
    const down = (e: KeyboardEvent) => {
      const k = e.key.toLowerCase();
      if (!["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(k)) return;
      e.preventDefault();
      if (!keys.current.has(k)) {
        keys.current.add(k);
        send();
      }
    };
    const up = (e: KeyboardEvent) => {
      keys.current.delete(e.key.toLowerCase());
      send();
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, []);

  const foot = robotFootprint(setup.data?.robot?.footprint_polygon_m).poly;
  const overlay = useCallback(
    (ctx: CanvasRenderingContext2D, v: View, pal: Palette) => {
      if (!frame || !setup.data) return true;
      const g = setup.data.grid;
      for (const e of log.slice(0, 30)) {
        const age = (frame.t ?? 0) - e.t;
        if (age > 4 || age < 0) continue;
        const [sx, sy] = w2s(g, v, e.xy[0], e.xy[1]);
        ctx.strokeStyle = withAlpha(e.type === "stop" ? pal.amber : pal.red, 1 - age / 4);
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.arc(sx, sy, 10 + age * 6, 0, Math.PI * 2);
        ctx.stroke();
      }
      frame.peds.forEach((p, i) => drawPed(ctx, g, v, p, pal, i === (frame.controlled_ped ?? 0)));
      const r = frame.robot;
      drawRobot(ctx, g, v, foot, r.x, r.y, r.yaw, pal, r.state === "stopped" ? pal.amber : pal.accent);
      return true;
    },
    [frame, log, setup.data, foot],
  );

  if (setup.error) return <main className="page"><ErrorBox error={setup.error} onRetry={setup.reload} /></main>;
  if (!setup.data) return <Loading label="Starting live session" />;
  const { twin, grid, robot, info } = setup.data;
  // POST /live returns a joystick_url carrying the session token; Console stores it for this page.
  let joyUrl = `${location.origin}/joystick/${encodeURIComponent(session)}`;
  try {
    joyUrl = sessionStorage.getItem(`clearance.joy.${session}`) ?? joyUrl;
  } catch { /* storage unavailable */ }
  const stops = log.filter((e) => e.type === "stop").length;
  const near = log.filter((e) => e.type === "near_miss" || e.type === "collision").length;

  return (
    <main className="page" style={{ maxWidth: 1320 }}>
      <div className="crumbs">
        <Link to="/">Sites</Link> / <Link to={`/sites/${info.site_id}`}>{info.site_id}</Link> / Live stress test
      </div>
      <div className="page-head">
        <div>
          <div className="row">
            <h1>Live stress test</h1>
            <span className="badge">Manual test, kept apart from simulation statistics</span>
          </div>
          <div className="sub">
            {robot ? `${robot.vendor} ${robot.model}` : info.robot_id} · {info.density ?? "normal"} traffic · session <span className="mono">{session}</span>
          </div>
        </div>
        <span className={`badge ${conn === "open" ? "green" : conn === "error" ? "red" : "amber"}`}>
          <span className="dot" /> {conn === "open" ? "Streaming" : conn}
        </span>
      </div>
      <div className="grid2" style={{ gridTemplateColumns: "minmax(0, 1fr) 300px" }}>
        <div className="card" style={{ overflow: "hidden" }}>
          <TwinView twin={twin} grid={grid} height="min(66vh, 640px)" overlay={overlay} animate />
          <div className="card-body row small muted" style={{ gap: 18 }}>
            <span>t {frame ? frame.t.toFixed(1) : "0.0"} s</span>
            <span>robot {frame?.robot.state ?? "–"}</span>
            <span>{frame ? frame.robot.v.toFixed(2) : "0.00"} m/s</span>
            <span>{stops} stops</span>
            <span>{near} near-misses</span>
            <span style={{ marginLeft: "auto" }}>Keyboard: WASD steers the green pedestrian</span>
          </div>
        </div>
        <div className="stack">
          <div className="card card-pad stack-sm" style={{ alignItems: "center", textAlign: "center" }}>
            <QR text={joyUrl} size={170} />
            <div style={{ fontWeight: 600 }}>Scan to take control</div>
            <div className="small muted">Your phone becomes a joystick for the green pedestrian (max 1.4 m/s).</div>
            <a className="btn sm" href={joyUrl} target="_blank" rel="noreferrer">
              <Icon name="joystick" size={14} /> Open joystick here
            </a>
          </div>
          <div className="card">
            <div className="card-head">
              <h3>Events</h3>
              <button className="btn sm ghost" onClick={() => setLog([])}>
                Clear
              </button>
            </div>
            <div className="card-body stack-sm" style={{ maxHeight: 300, overflow: "auto" }}>
              {!log.length && <span className="small muted">Walk into the robot's path to trigger a safety stop.</span>}
              {log.slice(0, 60).map((e, i) => (
                <div key={i} className="row between small">
                  <span className={`badge ${e.type === "stop" ? "amber" : e.type === "success" ? "green" : e.type === "replan" ? "blue" : "red"}`}>{e.type.replace("_", "-")}</span>
                  <span className="mono muted">
                    {e.t.toFixed(1)} s · ({e.xy[0].toFixed(1)}, {e.xy[1].toFixed(1)})
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
