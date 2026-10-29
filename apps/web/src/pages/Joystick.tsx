import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import type { ConnState } from "../api/types";
import type { LiveFrame } from "../types";

const MAX = 1.4; // m/s

/** Touch joystick: drives one pedestrian in the live session over WS /live/{session}. */
export default function JoystickPage() {
  const { session = "" } = useParams();
  const base = useRef<HTMLDivElement>(null);
  const [knob, setKnob] = useState<[number, number]>([0, 0]);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [frame, setFrame] = useState<LiveFrame | null>(null);
  const [stops, setStops] = useState(0);
  const c = useRef<ReturnType<typeof api.openLive> | null>(null);
  const vel = useRef<[number, number]>([0, 0]);
  const active = useRef<number | null>(null);

  useEffect(() => {
    const conn = api.openLive(session, "controller");
    c.current = conn;
    conn.onState(setConn);
    conn.onFrame((f) => {
      setFrame(f);
      const n = f.events?.filter((e) => e.type === "stop").length ?? 0;
      if (n) {
        setStops((s) => s + n);
        navigator.vibrate?.(60);
      }
    });
    const t = setInterval(() => conn.sendJoystick(vel.current[0], vel.current[1]), 50); // 20 Hz
    return () => {
      clearInterval(t);
      conn.sendJoystick(0, 0);
      conn.close();
    };
  }, [session]);

  const update = (clientX: number, clientY: number) => {
    const r = base.current!.getBoundingClientRect();
    const R = r.width / 2;
    let dx = (clientX - (r.left + R)) / R;
    let dy = (clientY - (r.top + R)) / R;
    const m = Math.hypot(dx, dy);
    if (m > 1) {
      dx /= m;
      dy /= m;
    }
    setKnob([dx, dy]);
    // screen up = +y in the site frame (top-down views are north-up)
    vel.current = [dx * MAX, -dy * MAX];
  };
  const release = () => {
    active.current = null;
    setKnob([0, 0]);
    vel.current = [0, 0];
    c.current?.sendJoystick(0, 0);
  };

  const speed = Math.hypot(knob[0], knob[1]) * MAX;
  return (
    <div className="phone" style={{ alignItems: "center", justifyContent: "space-between", padding: "22px 16px 40px", background: "radial-gradient(circle at 50% 30%, #182033, #07090d)" }}>
      <div className="row between" style={{ width: "100%" }}>
        <span className="pill">Clearance · joystick</span>
        <span className={`pill ${conn === "open" ? "ok" : conn === "error" ? "err" : "warn"}`}>{conn === "open" ? "Connected" : conn === "error" ? "Offline" : "Connecting"}</span>
      </div>
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: 40, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{speed.toFixed(1)} m/s</div>
        <div style={{ opacity: 0.7 }}>You are the green pedestrian. Walk into the robot's path.</div>
      </div>
      <div
        ref={base}
        className="joy-base"
        onPointerDown={(e) => {
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
          active.current = e.pointerId;
          update(e.clientX, e.clientY);
        }}
        onPointerMove={(e) => active.current === e.pointerId && update(e.clientX, e.clientY)}
        onPointerUp={release}
        onPointerCancel={release}
      >
        <div className="joy-knob" style={{ transform: `translate(${knob[0] * 106}%, ${knob[1] * 106}%)` }} />
      </div>
      <div className="row" style={{ gap: 10 }}>
        <span className="pill">robot {frame?.robot.state ?? "–"}</span>
        <span className="pill warn">{stops} stops caused</span>
      </div>
    </div>
  );
}
