/**
 * Twin editor state with undo/redo. Pure and framework-free so it can be unit-tested.
 * - Every discrete edit pushes the previous twin onto `past`.
 * - Continuous gestures (drag/rotate/resize) call gestureStart, then transient edits,
 *   then gestureEnd, which records ONE history entry for the whole gesture.
 */
import type { ClearZone, Hazard, KeepoutZone, Primitive, Threshold, Twin, Waypoint } from "../types";

export type EntityKind = "primitive" | "waypoint" | "threshold" | "keepout" | "clear" | "hazard";

export interface EntityMap {
  primitive: Primitive;
  waypoint: Waypoint;
  threshold: Threshold;
  keepout: KeepoutZone;
  clear: ClearZone;
  hazard: Hazard;
}

export type Selection = { kind: EntityKind; id: string } | null;

export type EditOp =
  | { op: "update"; kind: EntityKind; id: string; patch: Record<string, unknown> }
  | { op: "add"; kind: EntityKind; item: EntityMap[EntityKind] }
  | { op: "delete"; kind: EntityKind; id: string }
  | { op: "rename"; kind: Exclude<EntityKind, "hazard">; id: string; newId: string }
  | { op: "replace"; twin: Twin };

export interface EditorState {
  twin: Twin | null;
  saved: Twin | null;
  past: Twin[];
  future: Twin[];
  selection: Selection;
  gestureBase: Twin | null;
}

export type EditorAction =
  | { type: "load"; twin: Twin }
  | { type: "edit"; op: EditOp; transient?: boolean; select?: Selection }
  | { type: "gestureStart" }
  | { type: "gestureEnd" }
  | { type: "gestureCancel" }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "select"; selection: Selection }
  | { type: "saved"; twin: Twin };

export const HISTORY_LIMIT = 100;

export const initialEditorState: EditorState = {
  twin: null,
  saved: null,
  past: [],
  future: [],
  selection: null,
  gestureBase: null,
};

const LIST_KEY: Record<EntityKind, keyof Twin> = {
  primitive: "primitives",
  waypoint: "waypoints",
  threshold: "thresholds",
  keepout: "keepout_zones",
  clear: "clear_zones",
  hazard: "hazards",
};

type AnyEntity = EntityMap[EntityKind];

export function listOf(twin: Twin, kind: EntityKind): AnyEntity[] {
  return ((twin[LIST_KEY[kind]] as AnyEntity[] | undefined) ?? []) as AnyEntity[];
}

/** Hazards have no id in the schema; the editor addresses them by index ("0", "1", ...). */
export function entityId(kind: EntityKind, e: AnyEntity, index: number): string {
  return kind === "hazard" ? String(index) : (e as { id: string }).id;
}

export function findEntity<K extends EntityKind>(twin: Twin | null, kind: K, id: string): EntityMap[K] | undefined {
  if (!twin) return undefined;
  const list = listOf(twin, kind);
  const idx = list.findIndex((e, i) => entityId(kind, e, i) === id);
  return idx >= 0 ? (list[idx] as EntityMap[K]) : undefined;
}

function withList(twin: Twin, kind: EntityKind, list: AnyEntity[]): Twin {
  return { ...twin, [LIST_KEY[kind]]: list };
}

/** Apply an edit. Returns the SAME reference when the op is a no-op (unknown id, duplicate id, ...). */
export function applyOp(twin: Twin, op: EditOp): Twin {
  if (op.op === "replace") return op.twin;
  const list = listOf(twin, op.kind);
  switch (op.op) {
    case "update": {
      const idx = list.findIndex((e, i) => entityId(op.kind, e, i) === op.id);
      if (idx < 0) return twin;
      const cur = list[idx] as unknown as Record<string, unknown>;
      let changed = false;
      for (const k of Object.keys(op.patch)) if (!deepEqual(cur[k], op.patch[k])) changed = true;
      if (!changed) return twin;
      const next = list.slice();
      next[idx] = { ...cur, ...op.patch } as unknown as AnyEntity;
      return withList(twin, op.kind, next);
    }
    case "add": {
      if (op.kind !== "hazard") {
        const id = (op.item as { id: string }).id;
        if (!id || list.some((e) => (e as { id: string }).id === id)) return twin;
      }
      return withList(twin, op.kind, [...list, op.item]);
    }
    case "delete": {
      const idx = list.findIndex((e, i) => entityId(op.kind, e, i) === op.id);
      if (idx < 0) return twin;
      return withList(
        twin,
        op.kind,
        list.filter((_, i) => i !== idx),
      );
    }
    case "rename": {
      const newId = op.newId.trim();
      if (!newId || newId === op.id) return twin;
      if (list.some((e) => (e as { id: string }).id === newId)) return twin;
      const idx = list.findIndex((e) => (e as { id: string }).id === op.id);
      if (idx < 0) return twin;
      const next = list.slice();
      next[idx] = { ...(list[idx] as object), id: newId } as AnyEntity;
      return withList(twin, op.kind, next);
    }
  }
}

function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || !a || !b) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a as object);
  const kb = Object.keys(b as object);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
}

function pushPast(past: Twin[], t: Twin): Twin[] {
  const next = [...past, t];
  return next.length > HISTORY_LIMIT ? next.slice(next.length - HISTORY_LIMIT) : next;
}

function validSelection(twin: Twin | null, sel: Selection): Selection {
  if (!sel || !twin) return null;
  return findEntity(twin, sel.kind, sel.id) ? sel : null;
}

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "load":
      return { ...initialEditorState, twin: action.twin, saved: action.twin };
    case "select":
      return { ...state, selection: validSelection(state.twin, action.selection) };
    case "edit": {
      if (!state.twin) return state;
      const next = applyOp(state.twin, action.op);
      let selection = state.selection;
      if (action.select !== undefined) selection = action.select;
      if (action.op.op === "rename" && selection && selection.kind === action.op.kind && selection.id === action.op.id && next !== state.twin)
        selection = { kind: selection.kind, id: action.op.newId.trim() };
      if (next === state.twin) return selection === state.selection ? state : { ...state, selection: validSelection(next, selection) };
      const inGesture = action.transient && state.gestureBase !== null;
      return {
        ...state,
        twin: next,
        past: inGesture ? state.past : pushPast(state.past, state.twin),
        future: inGesture ? state.future : [],
        selection: validSelection(next, selection),
      };
    }
    case "gestureStart":
      return state.twin ? { ...state, gestureBase: state.twin } : state;
    case "gestureEnd": {
      const base = state.gestureBase;
      if (!base || !state.twin) return { ...state, gestureBase: null };
      if (base === state.twin) return { ...state, gestureBase: null };
      return { ...state, gestureBase: null, past: pushPast(state.past, base), future: [] };
    }
    case "gestureCancel":
      if (!state.gestureBase) return state;
      return { ...state, twin: state.gestureBase, gestureBase: null, selection: validSelection(state.gestureBase, state.selection) };
    case "undo": {
      if (!state.twin || state.past.length === 0 || state.gestureBase) return state;
      const prev = state.past[state.past.length - 1];
      return {
        ...state,
        twin: prev,
        past: state.past.slice(0, -1),
        future: [state.twin, ...state.future],
        selection: validSelection(prev, state.selection),
      };
    }
    case "redo": {
      if (!state.twin || state.future.length === 0 || state.gestureBase) return state;
      const [next, ...rest] = state.future;
      return {
        ...state,
        twin: next,
        past: pushPast(state.past, state.twin),
        future: rest,
        selection: validSelection(next, state.selection),
      };
    }
    case "saved":
      // Server returned the new immutable version. Keep history so the user can keep editing.
      return { ...state, twin: action.twin, saved: action.twin, selection: validSelection(action.twin, state.selection) };
  }
}

export const isDirty = (s: EditorState) => s.twin !== s.saved;
export const canUndo = (s: EditorState) => s.past.length > 0 && !s.gestureBase;
export const canRedo = (s: EditorState) => s.future.length > 0 && !s.gestureBase;

export function nextId(prefix: string, existing: string[]): string {
  const used = new Set(existing);
  for (let i = 1; ; i++) {
    const id = `${prefix}${i}`;
    if (!used.has(id)) return id;
  }
}
