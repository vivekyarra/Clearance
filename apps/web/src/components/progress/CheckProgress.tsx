import { useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { useAsync, useInterval } from "../../hooks/useAsync";
import type { OccGrid } from "../../lib/grid";
import type { AnalysisDoc, Investigation, RobotSpec, Twin } from "../../types";
import { RobotStage } from "./RobotStage";
import { StagePanel } from "./StagePanel";
import { counters, formatElapsed, latestHypothesis, narrowestGap, overallProgress, stageRows, startedAtMs } from "./stages";
import "./progress.css";

export interface CheckProgressProps {
  id: string;
  doc: AnalysisDoc;
  /** every stage is complete and the report is loading */
  finishing?: boolean;
  /** replay of a recorded check (offline mode): elapsed seconds of the replay */
  replayElapsed?: number | null;
  onSkip?: () => void;
}

type DocX = AnalysisDoc & { twin?: Twin; robots?: Record<string, RobotSpec>; twin_version?: number };

export function usePrefersReducedMotion(): boolean {
  const q = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
  const [v, setV] = useState(!!q?.matches);
  useEffect(() => {
    if (!q) return;
    const f = () => setV(q.matches);
    q.addEventListener?.("change", f);
    return () => q.removeEventListener?.("change", f);
  }, [q]);
  return v;
}

function useNarrow(px: number): boolean {
  const [v, setV] = useState(() => typeof innerWidth === "number" && innerWidth < px);
  useEffect(() => {
    const f = () => setV(innerWidth < px);
    addEventListener("resize", f);
    return () => removeEventListener("resize", f);
  }, [px]);
  return v;
}

export function CheckProgress({ id, doc, finishing, replayElapsed, onSkip }: CheckProgressProps) {
  const d = doc as DocX;
  const reduced = usePrefersReducedMotion();
  const narrow = useNarrow(760);
  const siteId = d.site_id ?? d.inputs?.site_id ?? d.twin?.site_id ?? "";
  const version = d.twin_version ?? d.inputs?.twin_version;

  // twin + occupancy grid, loaded once per site/version
  const scene = useAsync<{ twin: Twin; grid: OccGrid } | null>(async () => {
    let twin: Twin | null = null;
    if (siteId) twin = await api.getTwin(siteId, version).catch(() => null);
    twin = twin ?? d.twin ?? null;
    if (!twin) return null;
    const grid = await api.occupancy(twin);
    return { twin, grid };
  }, [siteId, version]);
  const catalog = useAsync(() => api.listRobots().catch(() => [] as RobotSpec[]), []);

  // live investigation (GET /analyses/{id}/investigation) while that stage runs
  const invRunning = String(doc.stages?.investigation?.state ?? "") === "running" && replayElapsed == null;
  const [inv, setInv] = useState<Investigation | null>(null);
  useEffect(() => setInv(null), [id]);
  useInterval(() => void api.getInvestigation(id).then(setInv, () => undefined), 4000, invRunning);
  useEffect(() => {
    if (invRunning) void api.getInvestigation(id).then(setInv, () => undefined);
  }, [invRunning, id]);

  const rows = useMemo(() => stageRows(doc.stages), [doc.stages]);
  const c = counters(doc, inv);
  const pinch = useMemo(() => narrowestGap(doc), [doc]);
  const hyp = invRunning || String(doc.stages?.investigation?.state ?? "") === "running" ? latestHypothesis(doc, inv) : null;

  const robots = useMemo(() => {
    const inp = d.inputs as { robot_ids?: string[]; robots?: string[] } | undefined;
    const ids = (d.candidates?.length ? d.candidates : (inp?.robot_ids ?? inp?.robots ?? [])).map((x) => (typeof x === "string" ? x : x.robot_id));
    const byId = new Map<string, RobotSpec>([...(catalog.data ?? []).map((r) => [r.robot_id, r] as const), ...Object.entries(d.robots ?? {})]);
    return ids.map((i) => byId.get(i)).filter((r): r is RobotSpec => !!r).slice(0, 4);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(d.candidates), JSON.stringify(d.inputs), catalog.data, d.robots ? Object.keys(d.robots).join() : ""]);
  const robotsKey = robots.map((r) => r.robot_id).join();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const stableRobots = useMemo(() => robots, [robotsKey]);
  const seqKey = (d.inputs?.task?.sequence ?? []).join(">");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const sequence = useMemo(() => d.inputs?.task?.sequence ?? [], [seqKey]);

  // elapsed time, ticking once a second
  const [firstSeen] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useInterval(() => setNow(Date.now()), 1000, !finishing && doc.status !== "failed");
  const elapsed = replayElapsed != null ? replayElapsed * 1000 : now - (startedAtMs(doc) ?? firstSeen);

  const failed = doc.status === "failed";
  const pct = Math.round((finishing ? 1 : overallProgress(rows)) * 100);
  const running = rows.find((r) => r.state === "running");
  const title = failed ? "Clearance check stopped" : finishing ? "Opening the Clearance Report" : "Clearance check in progress";
  const sub = failed
    ? "A stage could not finish. The stage list shows where."
    : finishing
      ? "Every stage is complete."
      : running
        ? `${running.label}${running.note ? ` · ${running.note}` : ""}`
        : "Waiting for a worker to pick up the check.";

  return (
    <div className={`cp ${finishing ? "is-finishing" : ""}`}>
      <div className="cp-head">
        <div>
          <h1>{title}</h1>
          <div className="sub cp-sub">{sub}</div>
        </div>
        <div className="cp-meta">
          <div className="cp-ring" style={{ ["--p" as string]: pct }} role="img" aria-label={`${pct}% complete`}>
            <span className="num">{pct}%</span>
          </div>
          <div className="cp-elapsed">
            <span className="muted small">Elapsed</span>
            <span className="num">{formatElapsed(elapsed)}</span>
          </div>
        </div>
      </div>

      <div className="cp-body">
        <div className="cp-visual card">
          {scene.data ? (
            <RobotStage twin={scene.data.twin} grid={scene.data.grid} robots={stableRobots} sequence={sequence} pinch={pinch} batchesDone={c.batchesDone} reduced={reduced} caption={hyp} height={narrow ? 280 : 420} />
          ) : (
            <div className="skeleton" style={{ height: narrow ? 280 : 420 }} />
          )}
          <div className="cp-counters">
            <Counter label="Robots screened" value={c.candidates} />
            <Counter label="Batches" value={c.batchesDone || c.batchesTotal ? `${c.batchesDone}${c.batchesTotal ? ` / ${c.batchesTotal}` : ""}` : null} />
            <Counter label="Episodes" value={c.episodes != null ? c.episodes.toLocaleString() : null} />
            <Counter label="Experiments" value={c.experiments} />
          </div>
        </div>
        <div className="cp-side card card-pad">
          <StagePanel rows={rows} />
          {replayElapsed != null && (
            <div className="cp-replay small muted">
              Replaying the recorded Clearance check (offline mode).
              {onSkip && (
                <button className="btn sm" onClick={onSkip}>
                  Skip to the report
                </button>
              )}
            </div>
          )}
          {!failed && !finishing && replayElapsed == null && <div className="cp-replay small muted">The Clearance Report opens here when every stage is done.</div>}
        </div>
      </div>
      <span className="sr-only" aria-live="polite">
        {running ? `${running.label} running` : title}
      </span>
    </div>
  );
}

function Counter({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div className={`cp-counter ${value == null ? "is-empty" : ""}`}>
      <span className="num">{value ?? "–"}</span>
      <span className="small muted">{label}</span>
    </div>
  );
}
