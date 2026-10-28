import { Icon } from "../Icon";
import type { StageRow } from "./stages";

export function StagePanel({ rows }: { rows: StageRow[] }) {
  return (
    <ol className="cp-stages" aria-label="Clearance check stages">
      {rows.map((r) => (
        <li key={r.key} className={`cp-stage is-${r.state}`} aria-current={r.state === "running" ? "step" : undefined}>
          <span className="cp-stage-icon" aria-hidden>
            {r.state === "done" ? (
              <Icon name="check" size={13} stroke={2.8} />
            ) : r.state === "failed" ? (
              <Icon name="x" size={13} stroke={2.8} />
            ) : r.state === "skipped" ? (
              <span className="cp-dash" />
            ) : (
              <span className="cp-dot" />
            )}
          </span>
          <div className="cp-stage-main">
            <div className="cp-stage-head">
              <span className="cp-stage-label">{r.label}</span>
              <span className="cp-stage-state num">{stateText(r)}</span>
            </div>
            <div
              className={`cp-bar ${r.state === "running" && r.progress == null ? "indeterminate" : ""}`}
              role="progressbar"
              aria-label={r.label}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={r.progress == null ? undefined : Math.round(r.progress * 100)}
            >
              <i style={r.progress == null ? undefined : { transform: `scaleX(${r.progress})` }} />
            </div>
            {r.note && (r.state === "skipped" || r.state === "failed" || r.state === "done" || r.state === "running") && <div className="cp-stage-note">{r.note}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function stateText(r: StageRow): string {
  switch (r.state) {
    case "done":
      return "Complete";
    case "running":
      return r.progress == null ? "Working" : `${Math.round(r.progress * 100)}%`;
    case "skipped":
      return "Skipped";
    case "failed":
      return "Stopped";
    default:
      return "Waiting";
  }
}
