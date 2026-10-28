import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { DEFAULT_LAYERS, EditorCanvas, type EditorCanvasHandle, type Layers } from "../editor/EditorCanvas";
import { Inspector } from "../editor/Inspector";
import { canRedo, canUndo, editorReducer, findEntity, initialEditorState, isDirty, nextId } from "../editor/reducer";
import { TOOLS, WAYPOINT_KINDS, r3, type Tool } from "../editor/tools";
import { Icon } from "../components/Icon";
import { ErrorBox, Loading, ReferenceBadge, useToast } from "../components/ui";
import type { OccGrid } from "../lib/grid";
import { useAsync } from "../hooks/useAsync";
import type { WaypointKind, XY } from "../types";

const LAYER_LABELS: [keyof Layers, string][] = [
  ["occupancy", "Scan"],
  ["primitives", "Objects"],
  ["keepouts", "Keep-out"],
  ["clearZones", "Confirmed clear"],
  ["waypoints", "Waypoints"],
  ["thresholds", "Thresholds"],
  ["hazards", "Hazards"],
  ["lowConfidence", "Low confidence"],
  ["grid", "Metre grid"],
];

export default function EditorPage() {
  const { id = "" } = useParams();
  const [state, dispatch] = useReducer(editorReducer, initialEditorState);
  const [grid, setGrid] = useState<OccGrid | null>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [wpKind, setWpKind] = useState<WaypointKind>("dest");
  const [layers, setLayers] = useState<Layers>(DEFAULT_LAYERS);
  const [cursor, setCursor] = useState<{ xy: XY | null; cell: string; zoom: number }>({ xy: null, cell: "", zoom: 0 });
  const [hint, setHint] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [showLayers, setShowLayers] = useState(false);
  const canvasRef = useRef<EditorCanvasHandle>(null);
  const toast = useToast();
  const site = useAsync(() => api.getSite(id), [id]);

  const load = useAsync(async () => {
    const t = await api.getTwin(id);
    const g = await api.occupancy(t);
    return { t, g };
  }, [id]);
  useEffect(() => {
    if (load.data) {
      dispatch({ type: "load", twin: load.data.t });
      setGrid(load.data.g);
    }
  }, [load.data]);

  const dirty = isDirty(state);
  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const save = useCallback(async () => {
    if (!state.twin || saving || !isDirty(state)) return;
    setSaving(true);
    try {
      const saved = await api.saveTwin(id, state.twin);
      dispatch({ type: "saved", twin: saved });
      try {
        setGrid(await api.occupancy(saved));
      } catch {
        /* keep the current grid if the new raster is not ready yet */
      }
      toast(`Saved as twin v${saved.version}`);
    } catch (e) {
      toast(`Save failed: ${(e as Error).message}`, true);
    } finally {
      setSaving(false);
    }
  }, [id, state, saving, toast]);

  // global shortcuts
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.closest("input,textarea,select")) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        dispatch({ type: e.shiftKey ? "redo" : "undo" });
        return;
      }
      if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        dispatch({ type: "redo" });
        return;
      }
      if (mod && e.key.toLowerCase() === "s") {
        e.preventDefault();
        save();
        return;
      }
      const twin = state.twin;
      const sel = state.selection;
      if (mod && e.key.toLowerCase() === "d" && sel?.kind === "primitive" && twin) {
        e.preventDefault();
        const p = findEntity(twin, "primitive", sel.id)!;
        const nid = nextId(p.id.replace(/\d+$/, "") || "B", (twin.primitives ?? []).map((q) => q.id));
        dispatch({ type: "edit", op: { op: "add", kind: "primitive", item: { ...p, id: nid, source: "user", pose: { ...p.pose, xyz: [r3(p.pose.xyz[0] + 0.3), r3(p.pose.xyz[1] - 0.3), p.pose.xyz[2]] } } }, select: { kind: "primitive", id: nid } });
        return;
      }
      if (mod) return;
      if ((e.key === "Delete" || e.key === "Backspace") && sel && tool !== "keepout" && tool !== "clear") {
        e.preventDefault();
        dispatch({ type: "edit", op: { op: "delete", kind: sel.kind, id: sel.id }, select: null });
        return;
      }
      if (e.key === "Escape") {
        if (tool !== "select") setTool("select");
        else dispatch({ type: "select", selection: null });
        return;
      }
      if (sel && twin && e.key.startsWith("Arrow")) {
        e.preventDefault();
        const d = e.shiftKey ? 0.1 : 0.01;
        const dx = e.key === "ArrowLeft" ? -d : e.key === "ArrowRight" ? d : 0;
        const dy = e.key === "ArrowDown" ? -d : e.key === "ArrowUp" ? d : 0;
        if (sel.kind === "primitive") {
          const p = findEntity(twin, "primitive", sel.id)!;
          dispatch({ type: "edit", op: { op: "update", kind: "primitive", id: p.id, patch: { pose: { ...p.pose, xyz: [r3(p.pose.xyz[0] + dx), r3(p.pose.xyz[1] + dy), p.pose.xyz[2]] } } } });
        } else if (sel.kind === "waypoint") {
          const w = findEntity(twin, "waypoint", sel.id)!;
          dispatch({ type: "edit", op: { op: "update", kind: "waypoint", id: w.id, patch: { xy: [r3(w.xy[0] + dx), r3(w.xy[1] + dy)] } } });
        }
        return;
      }
      if (e.key.toLowerCase() === "r" && sel?.kind === "primitive" && twin) {
        const p = findEntity(twin, "primitive", sel.id)!;
        let yaw = (p.pose.yaw ?? 0) + ((e.shiftKey ? -15 : 15) * Math.PI) / 180;
        if (yaw > Math.PI) yaw -= 2 * Math.PI;
        if (yaw < -Math.PI) yaw += 2 * Math.PI;
        dispatch({ type: "edit", op: { op: "update", kind: "primitive", id: p.id, patch: { pose: { ...p.pose, yaw: Math.round(yaw * 10000) / 10000 } } } });
        return;
      }
      if (e.key === "0") {
        canvasRef.current?.fit();
        return;
      }
      const t = TOOLS.find((x) => x.key.toLowerCase() === e.key.toLowerCase());
      if (t) setTool(t.id);
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [state, tool, save]);

  if (load.error) return <main className="page"><ErrorBox error={load.error} onRetry={load.reload} /></main>;
  if (!state.twin || !grid) return <Loading label="Loading twin" />;
  const twin = state.twin;
  const sel = state.selection;
  const counts = { prim: (twin.primitives ?? []).length, flagged: (twin.thresholds ?? []).filter((t) => t.status === "flagged_manual" && t.height_m == null).length };
  const selLabel = sel ? (sel.kind === "hazard" ? `hazard #${Number(sel.id) + 1}` : `${sel.kind} ${sel.id}`) : "none";

  return (
    <div className="editor">
      <div className="editor-rail">
        {TOOLS.map((t) => (
          <button key={t.id} className={`tool ${tool === t.id ? "on" : ""}`} title={`${t.label} (${t.key})`} onClick={() => setTool(t.id)}>
            <Icon name={t.icon} size={18} />
            <kbd>{t.key}</kbd>
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <button className={`tool ${showLayers ? "on" : ""}`} title="Layers" onClick={() => setShowLayers((x) => !x)}>
          <Icon name="layers" size={18} />
        </button>
      </div>
      <div className="editor-main">
        <div className="editor-bar">
          <Link to={`/sites/${id}`} className="btn sm ghost">
            <Icon name="chevronLeft" size={14} /> {site.data?.name ?? "Site"}
          </Link>
          <span className="badge">Twin v{twin.version}</span>
          {twin.geometry_source === "reference" && <ReferenceBadge />}
          {dirty && <span className="badge amber">Unsaved changes</span>}
          <div style={{ width: 1, height: 20, background: "var(--border)", margin: "0 4px" }} />
          <button className="btn sm icon" title="Undo (Ctrl+Z)" disabled={!canUndo(state)} onClick={() => dispatch({ type: "undo" })}>
            <Icon name="undo" size={15} />
          </button>
          <button className="btn sm icon" title="Redo (Ctrl+Shift+Z)" disabled={!canRedo(state)} onClick={() => dispatch({ type: "redo" })}>
            <Icon name="redo" size={15} />
          </button>
          <button className="btn sm icon" title="Zoom out" onClick={() => canvasRef.current?.zoom(1 / 1.25)}>
            <Icon name="minus" size={15} />
          </button>
          <button className="btn sm icon" title="Zoom in" onClick={() => canvasRef.current?.zoom(1.25)}>
            <Icon name="plus" size={15} />
          </button>
          <button className="btn sm icon" title="Fit (0)" onClick={() => canvasRef.current?.fit()}>
            <Icon name="fit" size={15} />
          </button>
          {tool === "waypoint" && (
            <div className="seg" style={{ marginLeft: 6 }}>
              {WAYPOINT_KINDS.map((k) => (
                <button key={k.kind} className={wpKind === k.kind ? "on" : ""} onClick={() => setWpKind(k.kind)}>
                  {k.label}
                </button>
              ))}
            </div>
          )}
          <div style={{ flex: 1 }} />
          <span className="small muted hide-sm" title="Earlier Clearance checks keep the version they ran on">Saves as twin v{(twin.version ?? 0) + 1}</span>
          <button className="btn sm primary" disabled={!dirty || saving} onClick={save} title="Save (Ctrl+S)">
            <Icon name="save" size={14} /> {saving ? "Saving" : "Save version"}
          </button>
        </div>
        <EditorCanvas ref={canvasRef} state={state} grid={grid} dispatch={dispatch} tool={tool} setTool={setTool} wpKind={wpKind} layers={layers} onCursor={setCursor} setHint={setHint} />
        {hint && <div className="hint-float" style={{ top: 58 }}>{hint}</div>}
        {showLayers && (
          <div className="card" style={{ position: "absolute", left: 10, bottom: 40, padding: 12, zIndex: 5, width: 190 }}>
            <h4 style={{ marginBottom: 8 }}>Layers</h4>
            <div className="stack-sm">
              {LAYER_LABELS.map(([k, l]) => (
                <label key={k} className="check small">
                  <input type="checkbox" checked={layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} /> {l}
                </label>
              ))}
            </div>
          </div>
        )}
        <div className="statusbar">
          <span>{cursor.xy ? `x ${cursor.xy[0].toFixed(2)}  y ${cursor.xy[1].toFixed(2)} m` : "x –  y –"}</span>
          <span>{cursor.cell || "–"}</span>
          <span>{cursor.zoom ? `${Math.round(cursor.zoom)} px/m` : ""}</span>
          <span>selected: {selLabel}</span>
          <span style={{ marginLeft: "auto" }}>
            {counts.prim} objects · {counts.flagged} thresholds to measure · history {state.past.length}
          </span>
        </div>
      </div>
      <aside className="editor-side">
        <Inspector twin={twin} grid={grid} selection={sel} dispatch={dispatch} onFocus={(xy) => canvasRef.current?.focus(xy)} />
        <div className="side-section small muted">
          <h4>Shortcuts</h4>
          <div className="kv" style={{ fontSize: 12 }}>
            <dt>Drag</dt>
            <dd>move · handles resize · round handle rotates</dd>
            <dt>Shift</dt>
            <dd>rotate in 15° steps</dd>
            <dt>Alt</dt>
            <dd>move without 1 cm snapping</dd>
            <dt>Arrows</dt>
            <dd>nudge 1 cm (Shift 10 cm)</dd>
            <dt>R</dt>
            <dd>rotate 15°</dd>
            <dt>Ctrl+D</dt>
            <dd>duplicate</dd>
            <dt>F</dt>
            <dd>confirm floor: drag a box or click corners over grey floor</dd>
            <dt>0</dt>
            <dd>fit the view</dd>
            <dt>Space</dt>
            <dd>hold to pan · wheel zooms</dd>
          </div>
        </div>
      </aside>
    </div>
  );
}

