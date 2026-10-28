import { useEffect, useMemo, useState, type Dispatch } from "react";
import { Icon } from "../components/Icon";
import { deg, polygonArea, rad } from "../lib/geometry";
import type { OccGrid } from "../lib/grid";
import type { ClearZone, Hazard, KeepoutZone, Primitive, Threshold, Twin, Waypoint, WaypointKind } from "../types";
import { type EditorAction, type EntityKind, type Selection, findEntity, nextId } from "./reducer";
import { LABELS, LOW_CONFIDENCE, WAYPOINT_KINDS, r3 } from "./tools";
import { MAX_CLEAR_ZONES, MAX_CLEAR_ZONE_M2, unseenOnRoute } from "./unseen";

/** Numeric input that commits on Enter/blur so each edit is one undo step. */
function Num({ value, onCommit, step = 0.01, digits = 3, suffix, min }: { value: number; onCommit: (v: number) => void; step?: number; digits?: number; suffix?: string; min?: number }) {
  const [text, setText] = useState(fmt(value, digits));
  useEffect(() => setText(fmt(value, digits)), [value, digits]);
  const commit = () => {
    const v = Number(text);
    if (text.trim() === "" || !Number.isFinite(v) || (min !== undefined && v < min)) return setText(fmt(value, digits));
    if (Math.abs(v - value) > 1e-9) onCommit(v);
  };
  return (
    <div className="input-group">
      <input
        className="input sm num"
        value={text}
        inputMode="decimal"
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setText(fmt(value, digits));
            (e.target as HTMLInputElement).blur();
          }
          if (e.key === "ArrowUp" || e.key === "ArrowDown") {
            e.preventDefault();
            const v = (Number(text) || 0) + (e.key === "ArrowUp" ? step : -step) * (e.shiftKey ? 10 : 1);
            onCommit(Number(v.toFixed(digits)));
          }
        }}
        style={suffix ? { height: 28 } : { height: 28, borderRadius: "var(--radius-sm)" }}
      />
      {suffix && (
        <span className="suffix" style={{ height: 28 }}>
          {suffix}
        </span>
      )}
    </div>
  );
}

const fmt = (v: number, d: number) => (Number.isFinite(v) ? String(Number(v.toFixed(d))) : "");

function Text({ value, onCommit, list, mono }: { value: string; onCommit: (v: string) => void; list?: string; mono?: boolean }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <input
      className={`input sm ${mono ? "mono" : ""}`}
      value={text}
      list={list}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => text.trim() && text !== value ? onCommit(text.trim()) : setText(value)}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <label className="field" style={{ gap: 3 }}>
    <span>{label}</span>
    {children}
  </label>
);

interface Props {
  twin: Twin;
  grid?: OccGrid | null;
  selection: Selection;
  dispatch: Dispatch<EditorAction>;
  onFocus: (xy: [number, number]) => void;
}

export function Inspector({ twin, grid, selection, dispatch, onFocus }: Props) {
  const upd = (kind: EntityKind, id: string, patch: Record<string, unknown>) => dispatch({ type: "edit", op: { op: "update", kind, id, patch } });
  const del = (kind: EntityKind, id: string) => dispatch({ type: "edit", op: { op: "delete", kind, id }, select: null });
  if (!selection) return <Overview twin={twin} grid={grid} dispatch={dispatch} onFocus={onFocus} />;
  const e = findEntity(twin, selection.kind, selection.id);
  if (!e) return <Overview twin={twin} grid={grid} dispatch={dispatch} onFocus={onFocus} />;

  const head = (title: string, badge?: React.ReactNode) => (
    <div className="row between" style={{ marginBottom: 12 }}>
      <div className="row" style={{ gap: 8 }}>
        <h3>{title}</h3>
        {badge}
      </div>
      <button className="btn sm ghost icon" title="Deselect (Esc)" onClick={() => dispatch({ type: "select", selection: null })}>
        <Icon name="x" size={14} />
      </button>
    </div>
  );

  switch (selection.kind) {
    case "primitive": {
      const p = e as Primitive;
      const low = (p.confidence ?? 1) < LOW_CONFIDENCE;
      const pose = (patch: Partial<Primitive["pose"]>) => upd("primitive", p.id, { pose: { ...p.pose, ...patch } });
      return (
        <div className="side-section">
          {head(p.id, <span className={`badge ${p.source === "user" ? "blue" : ""}`}>{p.source ?? "auto"}</span>)}
          {low && (
            <div className="note row between" style={{ background: "var(--amber-soft)", color: "var(--amber)", marginBottom: 12 }}>
              <span>Detected with {Math.round((p.confidence ?? 0) * 100)}% confidence. Check it against the room.</span>
              <button className="btn sm" onClick={() => upd("primitive", p.id, { confidence: 1 })}>
                Confirm
              </button>
            </div>
          )}
          <div className="stack-sm">
            <div className="grid2" style={{ gap: 8 }}>
              <Row label="Label">
                <Text value={p.label} list="labels" onCommit={(v) => upd("primitive", p.id, { label: v, movable: ["chair", "stool", "cart", "table", "box"].includes(v) ? true : p.movable })} />
              </Row>
              <Row label="Shape">
                <select className="select sm" value={p.type} onChange={(ev) => upd("primitive", p.id, { type: ev.target.value, size: ev.target.value === "cylinder" ? [p.size[0], p.size[0], p.size[2]] : p.size })}>
                  <option value="box">Box</option>
                  <option value="cylinder">Cylinder</option>
                </select>
              </Row>
              <Row label="X">
                <Num value={p.pose.xyz[0]} suffix="m" onCommit={(v) => pose({ xyz: [r3(v), p.pose.xyz[1], p.pose.xyz[2]] })} />
              </Row>
              <Row label="Y">
                <Num value={p.pose.xyz[1]} suffix="m" onCommit={(v) => pose({ xyz: [p.pose.xyz[0], r3(v), p.pose.xyz[2]] })} />
              </Row>
              {p.type === "box" ? (
                <>
                  <Row label="Length">
                    <Num value={p.size[0]} suffix="m" min={0.02} onCommit={(v) => upd("primitive", p.id, { size: [r3(v), p.size[1], p.size[2]] })} />
                  </Row>
                  <Row label="Width">
                    <Num value={p.size[1]} suffix="m" min={0.02} onCommit={(v) => upd("primitive", p.id, { size: [p.size[0], r3(v), p.size[2]] })} />
                  </Row>
                  <Row label="Rotation">
                    <Num value={deg(p.pose.yaw ?? 0)} step={1} digits={1} suffix="°" onCommit={(v) => pose({ yaw: Math.round(rad(v) * 10000) / 10000 })} />
                  </Row>
                </>
              ) : (
                <Row label="Diameter">
                  <Num value={p.size[0]} suffix="m" min={0.02} onCommit={(v) => upd("primitive", p.id, { size: [r3(v), r3(v), p.size[2]] })} />
                </Row>
              )}
              <Row label="Height">
                <Num value={p.size[2]} suffix="m" min={0.01} onCommit={(v) => upd("primitive", p.id, { size: [p.size[0], p.size[1], r3(v)], pose: { ...p.pose, xyz: [p.pose.xyz[0], p.pose.xyz[1], r3(v / 2)] } })} />
              </Row>
            </div>
            <div className="row" style={{ gap: 16, marginTop: 4 }}>
              <label className="check">
                <input type="checkbox" checked={!!p.movable} onChange={(ev) => upd("primitive", p.id, { movable: ev.target.checked })} /> Movable
              </label>
              <label className="check">
                <input type="checkbox" checked={!!p.fragile} onChange={(ev) => upd("primitive", p.id, { fragile: ev.target.checked })} /> Fragile
              </label>
            </div>
            <div className="small muted">Confidence {Math.round((p.confidence ?? 1) * 100)}% · movable objects are jittered in simulation and can be moved by the layout search.</div>
            <div className="row" style={{ marginTop: 6 }}>
              <button
                className="btn sm"
                onClick={() => {
                  const id = nextId(p.id.replace(/\d+$/, "") || "B", (twin.primitives ?? []).map((q) => q.id));
                  dispatch({ type: "edit", op: { op: "add", kind: "primitive", item: { ...p, id, source: "user", pose: { ...p.pose, xyz: [r3(p.pose.xyz[0] + 0.3), r3(p.pose.xyz[1] - 0.3), p.pose.xyz[2]] } } }, select: { kind: "primitive", id } });
                }}
              >
                Duplicate
              </button>
              <button className="btn sm danger" onClick={() => del("primitive", p.id)}>
                <Icon name="trash" size={14} /> Delete
              </button>
            </div>
          </div>
          <datalist id="labels">
            {LABELS.map((l) => (
              <option key={l} value={l} />
            ))}
          </datalist>
        </div>
      );
    }
    case "waypoint": {
      const w = e as Waypoint;
      return (
        <div className="side-section">
          {head("Waypoint")}
          <div className="stack-sm">
            <Row label="Name">
              <Text mono value={w.id} onCommit={(v) => dispatch({ type: "edit", op: { op: "rename", kind: "waypoint", id: w.id, newId: v.toUpperCase().replace(/\s+/g, "_") } })} />
            </Row>
            <Row label="Kind">
              <select className="select sm" value={w.kind} onChange={(ev) => upd("waypoint", w.id, { kind: ev.target.value as WaypointKind })}>
                {WAYPOINT_KINDS.map((k) => (
                  <option key={k.kind} value={k.kind}>
                    {k.label}
                  </option>
                ))}
              </select>
            </Row>
            <div className="grid3" style={{ gap: 8 }}>
              <Row label="X">
                <Num value={w.xy[0]} suffix="m" onCommit={(v) => upd("waypoint", w.id, { xy: [r3(v), w.xy[1]] })} />
              </Row>
              <Row label="Y">
                <Num value={w.xy[1]} suffix="m" onCommit={(v) => upd("waypoint", w.id, { xy: [w.xy[0], r3(v)] })} />
              </Row>
              <Row label="Heading">
                <Num value={deg(w.yaw ?? 0)} step={5} digits={0} suffix="°" onCommit={(v) => upd("waypoint", w.id, { yaw: Math.round(rad(v) * 10000) / 10000 })} />
              </Row>
            </div>
            <div className="small muted">Names must be unique; Clearance check routes refer to waypoints by name.</div>
            <div className="row">
              <button className="btn sm danger" onClick={() => del("waypoint", w.id)}>
                <Icon name="trash" size={14} /> Delete
              </button>
            </div>
          </div>
        </div>
      );
    }
    case "threshold": {
      const t = e as Threshold;
      const len = Math.hypot(t.segment[1][0] - t.segment[0][0], t.segment[1][1] - t.segment[0][1]);
      const flagged = t.status === "flagged_manual" && t.height_m == null;
      return (
        <div className="side-section">
          {head(`Threshold ${t.id}`, <span className={`badge ${flagged ? "amber" : t.status === "user_entered" ? "green" : ""}`}>{flagged ? "measure on site" : t.status === "user_entered" ? "entered by you" : "measured"}</span>)}
          <div className="stack-sm">
            {flagged && <div className="note" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>This step is near the scan's noise floor. Measure it with a ruler and enter the height: robots with a lower limit are then checked exactly.</div>}
            <Row label="Height">
              <Num value={t.height_m != null ? t.height_m * 100 : NaN} step={0.1} digits={1} suffix="cm" min={0} onCommit={(v) => upd("threshold", t.id, { height_m: r3(v / 100), status: "user_entered" })} />
            </Row>
            <div className="small muted num">Door width {len.toFixed(2)} m</div>
            <div className="row">
              {t.height_m != null && t.status !== "flagged_manual" && (
                <button className="btn sm" onClick={() => upd("threshold", t.id, { height_m: null, status: "flagged_manual" })}>
                  Clear height
                </button>
              )}
              <button className="btn sm danger" onClick={() => del("threshold", t.id)}>
                <Icon name="trash" size={14} /> Delete
              </button>
            </div>
          </div>
        </div>
      );
    }
    case "keepout": {
      const k = e as KeepoutZone;
      return (
        <div className="side-section">
          {head(`Keep-out ${k.id}`)}
          <div className="stack-sm">
            <Row label="Reason">
              <Text value={k.reason ?? ""} onCommit={(v) => upd("keepout", k.id, { reason: v })} />
            </Row>
            <div className="small muted num">
              {k.polygon.length} corners · {polygonArea(k.polygon).toFixed(2)} m²
            </div>
            <div className="small muted">Robots never plan through keep-out zones, and the layout search never moves objects into them.</div>
            <div className="row">
              <button className="btn sm danger" onClick={() => del("keepout", k.id)}>
                <Icon name="trash" size={14} /> Delete
              </button>
            </div>
          </div>
        </div>
      );
    }
    case "clear": {
      const z = e as ClearZone;
      const area = polygonArea(z.polygon);
      return (
        <div className="side-section">
          {head(`Confirmed clear ${z.id}`, <span className="badge green">confirmed clear</span>)}
          <div className="stack-sm">
            <Row label="Note">
              <Text value={z.reason ?? ""} onCommit={(v) => upd("clear", z.id, { reason: v })} />
            </Row>
            <div className="small muted num">
              {z.polygon.length} corners · {area.toFixed(2)} m²
            </div>
            {area > MAX_CLEAR_ZONE_M2 && (
              <div className="note" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}>
                Saving accepts up to {MAX_CLEAR_ZONE_M2} m² per zone. Split it into smaller zones or rescan this area.
              </div>
            )}
            <div className="small muted">
              {z.confirmed_by ? `Confirmed by ${z.confirmed_by}${z.confirmed_at ? ` · ${z.confirmed_at.slice(0, 16).replace("T", " ")} UTC` : ""}` : "Recorded with your name and the time when you save."}
            </div>
            <div className="small muted">Unseen (grey) floor inside counts as free in simulation. Measured obstacles stay, and the Clearance Report lists every confirmed zone.</div>
            <div className="row">
              <button className="btn sm danger" onClick={() => del("clear", z.id)}>
                <Icon name="trash" size={14} /> Delete
              </button>
            </div>
          </div>
        </div>
      );
    }
    case "hazard": {
      const h = e as Hazard;
      return (
        <div className="side-section">
          {head("Hazard", <span className="badge amber">{Math.round((h.confidence ?? 0) * 100)}%</span>)}
          <div className="stack-sm">
            <Row label="Label">
              <Text value={h.label} onCommit={(v) => upd("hazard", selection.id, { label: v })} />
            </Row>
            <div className="small muted">
              Source: {h.source ?? "vision_llm"}
              {h.frame_refs?.length ? ` · frames ${h.frame_refs.join(", ")}` : ""}
            </div>
            <div className="small muted">Hazards are listed under manual checks in the Clearance Report. Drag to correct the position.</div>
            <div className="row">
              <button className="btn sm danger" onClick={() => del("hazard", selection.id)}>
                <Icon name="trash" size={14} /> Dismiss
              </button>
            </div>
          </div>
        </div>
      );
    }
  }
}

type ListItem = { kind: EntityKind; id: string; label: string; xy: [number, number] | null };
type ListTab = Exclude<EntityKind, "clear">;

function Overview({ twin, grid, dispatch, onFocus }: { twin: Twin; grid?: OccGrid | null; dispatch: Dispatch<EditorAction>; onFocus: (xy: [number, number]) => void }) {
  const [filter, setFilter] = useState("");
  const [tab, setTab] = useState<ListTab>("primitive");
  const flagged = (twin.thresholds ?? []).filter((t) => t.status === "flagged_manual" && t.height_m == null);
  const low = (twin.primitives ?? []).filter((p) => (p.confidence ?? 1) < LOW_CONFIDENCE);
  const wps = twin.waypoints ?? [];
  const needs = [
    ...flagged.map((t) => ({ sel: { kind: "threshold" as const, id: t.id }, text: `Measure threshold ${t.id}`, xy: t.segment[0], tone: "amber" })),
    ...low.map((p) => ({ sel: { kind: "primitive" as const, id: p.id }, text: `Confirm ${p.label} ${p.id} (${Math.round((p.confidence ?? 0) * 100)}%)`, xy: [p.pose.xyz[0], p.pose.xyz[1]] as [number, number], tone: "amber" })),
    ...(!wps.some((w) => w.kind === "dock") ? [{ sel: null, text: "Place a dock waypoint", xy: null, tone: "red" }] : []),
    ...(!wps.some((w) => w.kind === "pickup") ? [{ sel: null, text: "Place a pickup waypoint", xy: null, tone: "red" }] : []),
    ...(!wps.some((w) => w.kind === "dest") ? [{ sel: null, text: "Place at least one destination", xy: null, tone: "red" }] : []),
  ];
  const lists: Record<ListTab, ListItem[]> = {
    primitive: (twin.primitives ?? []).map((p) => ({ kind: "primitive", id: p.id, label: `${p.label}${p.movable ? "" : " · fixed"}`, xy: [p.pose.xyz[0], p.pose.xyz[1]] })),
    waypoint: wps.map((w) => ({ kind: "waypoint", id: w.id, label: w.kind, xy: w.xy })),
    threshold: (twin.thresholds ?? []).map((t) => ({ kind: "threshold", id: t.id, label: t.height_m != null ? `${(t.height_m * 100).toFixed(1)} cm` : "measure on site", xy: t.segment[0] })),
    keepout: [
      ...(twin.keepout_zones ?? []).map((k): ListItem => ({ kind: "keepout", id: k.id, label: k.reason || "keep-out", xy: k.polygon[0] ?? null })),
      ...(twin.clear_zones ?? []).map((z): ListItem => ({ kind: "clear", id: z.id, label: `confirmed clear · ${polygonArea(z.polygon).toFixed(2)} m²`, xy: z.polygon[0] ?? null })),
    ],
    hazard: (twin.hazards ?? []).map((h, i) => ({ kind: "hazard", id: String(i), label: h.label, xy: h.xy ?? null })),
  };
  // unseen patches on the default route (cheap on editor-size grids; recomputed only when route inputs change)
  const unseen = useMemo(() => (grid ? unseenOnRoute(twin, grid) : null), [grid, twin.waypoints, twin.clear_zones]); // eslint-disable-line react-hooks/exhaustive-deps
  const nClear = (twin.clear_zones ?? []).length;
  const items = lists[tab].filter((x) => !filter || `${x.id} ${x.label}`.toLowerCase().includes(filter.toLowerCase()));
  return (
    <>
      <div className="side-section">
        <h4>Review checklist</h4>
        {!needs.length ? (
          <div className="row small" style={{ color: "var(--green)" }}>
            <Icon name="check" size={15} /> Twin is ready for a Clearance check.
          </div>
        ) : (
          <div className="stack-sm">
            {needs.map((n, i) => (
              <button
                key={i}
                className="obj-item"
                style={{ border: 0, background: "transparent", color: `var(--${n.tone})`, textAlign: "left", font: "inherit" }}
                onClick={() => {
                  if (n.sel) dispatch({ type: "select", selection: n.sel });
                  if (n.xy) onFocus(n.xy as [number, number]);
                }}
              >
                <Icon name={n.tone === "red" ? "pin" : "alert"} size={14} /> {n.text}
              </button>
            ))}
          </div>
        )}
        {unseen && unseen.length > 0 && (
          <button
            className="obj-item"
            style={{ border: 0, background: "transparent", color: "var(--accent)", textAlign: "left", font: "inherit", marginTop: 6 }}
            title="Unseen (grey) floor is treated as blocked. Use Confirm floor (F) where you checked the floor on site."
            onClick={() => onFocus(unseen[0].xy)}
          >
            <Icon name="scan" size={14} /> {unseen.length} unseen patch{unseen.length === 1 ? "" : "es"} on the route — confirm or rescan
          </button>
        )}
        {nClear > MAX_CLEAR_ZONES && <div className="small" style={{ color: "var(--amber)", marginTop: 6 }}>Saving accepts at most {MAX_CLEAR_ZONES} confirmed clear zones ({nClear} drawn).</div>}
      </div>
      <div className="side-section">
        <div className="seg" style={{ marginBottom: 10, display: "flex" }}>
          {(
            [
              ["primitive", "Objects"],
              ["waypoint", "Waypoints"],
              ["threshold", "Doors"],
              ["keepout", "Zones"],
              ["hazard", "Hazards"],
            ] as [ListTab, string][]
          ).map(([k, l]) => (
            <button key={k} className={tab === k ? "on" : ""} style={{ flex: 1, padding: "0 4px" }} onClick={() => setTab(k)}>
              {l}
            </button>
          ))}
        </div>
        <input className="input sm" aria-label="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ marginBottom: 8 }} title="Filter by id or label" />
        <div className="obj-list">
          {items.map((x) => (
            <div
              key={`${x.kind}:${x.id}`}
              className="obj-item"
              onClick={() => {
                dispatch({ type: "select", selection: { kind: x.kind, id: x.id } });
                if (x.xy) onFocus(x.xy);
              }}
            >
              <span className="id">{tab === "hazard" ? `#${Number(x.id) + 1}` : x.id}</span>
              <span className="muted" style={x.kind === "clear" ? { color: "var(--green)" } : undefined}>{x.label}</span>
              {x.kind === "clear" && (
                <button
                  className="btn sm ghost icon"
                  title={`Delete confirmed clear zone ${x.id}`}
                  style={{ marginLeft: "auto" }}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    dispatch({ type: "edit", op: { op: "delete", kind: "clear", id: x.id }, select: null });
                  }}
                >
                  <Icon name="trash" size={13} />
                </button>
              )}
            </div>
          ))}
          {!items.length && <span className="small muted">Nothing here yet.</span>}
        </div>
      </div>
    </>
  );
}
