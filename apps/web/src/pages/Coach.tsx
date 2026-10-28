import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import type { ConnState } from "../api/types";
import type { CoachMessage } from "../types";

type Coverage = Extract<CoachMessage, { type: "coverage" }>;

/** Capture coach screen (PLAN 7.1): live coverage map + latest prompt from the scanner, via WebSocket. */
export default function CoachPage() {
  const { site = "" } = useParams();
  const [cov, setCov] = useState<Coverage | null>(null);
  const [prompt, setPrompt] = useState<{ text: string; level?: string } | null>(null);
  const [status, setStatus] = useState<{ device?: string; frames?: number; tracking?: string } | null>(null);
  const [conn, setConn] = useState<ConnState>("connecting");
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const c = api.openCoach(site);
    c.onState(setConn);
    c.onMessage((m) => {
      if (m.type === "coverage") setCov(m);
      else if (m.type === "prompt") setPrompt({ text: m.text, level: m.level });
      else if (m.type === "status") setStatus(m);
    });
    try {
      navigator.wakeLock?.request("screen").catch(() => undefined);
    } catch {
      /* wake lock unsupported */
    }
    return () => c.close();
  }, [site]);

  useEffect(() => {
    const el = canvas.current;
    if (!el || !cov) return;
    const w = el.clientWidth;
    const h = el.clientHeight;
    el.width = w * devicePixelRatio;
    el.height = h * devicePixelRatio;
    const ctx = el.getContext("2d")!;
    ctx.scale(devicePixelRatio, devicePixelRatio);
    ctx.clearRect(0, 0, w, h);
    const s = Math.min(w / cov.width, h / cov.height);
    const ox = (w - cov.width * s) / 2;
    const oy = (h - cov.height * s) / 2;
    const col = ["rgba(255,255,255,0.05)", "rgba(240,167,58,0.75)", "rgba(60,207,127,0.65)", "rgba(60,207,127,0.85)", "rgba(60,207,127,1)"];
    for (let r = 0; r < cov.height; r++)
      for (let c = 0; c < cov.width; c++) {
        // row 0 = max y (north up), same convention as the occupancy grid
        ctx.fillStyle = col[Math.min(4, cov.cells[r * cov.width + c] ?? 0)];
        ctx.fillRect(ox + c * s + 1, oy + r * s + 1, s - 2, s - 2);
      }
  }, [cov]);

  const total = cov ? cov.cells.length : 0;
  const good = cov ? cov.cells.filter((v) => v >= 2).length : 0;
  return (
    <div className="phone" style={{ padding: 16, gap: 14 }}>
      <div className="row between">
        <span className="pill">Clearance · capture coach</span>
        <span className={`pill ${conn === "open" ? "ok" : conn === "error" ? "err" : "warn"}`}>{conn === "open" ? "Live" : conn}</span>
      </div>
      <div className={`pill ${prompt?.level === "warn" ? "warn" : ""}`} style={{ fontSize: 22, padding: "16px 18px", borderRadius: 16, lineHeight: 1.3, justifyContent: "center", textAlign: "center", minHeight: 90 }}>
        {prompt?.text ?? "Waiting for the scanner to start"}
      </div>
      <canvas ref={canvas} style={{ flex: 1, width: "100%", minHeight: 200 }} />
      <div className="row between" style={{ opacity: 0.85 }}>
        <span>{total ? `${Math.round((good / total) * 100)}% of the floor seen from 2+ angles` : "Coverage map appears when scanning starts"}</span>
        <span>{status ? `${status.device ?? "scanner"} · ${status.frames ?? 0} frames · tracking ${status.tracking ?? "–"}` : ""}</span>
      </div>
      <div className="row" style={{ gap: 14, fontSize: 13, opacity: 0.75 }}>
        <span>
          <span className="dot" style={{ color: "rgba(240,167,58,1)" }} /> one angle
        </span>
        <span>
          <span className="dot" style={{ color: "rgba(60,207,127,1)" }} /> two or more angles
        </span>
      </div>
    </div>
  );
}
