import type { CoachMessage, CoachWire } from "../types";

/**
 * Split one wire coach message ({"type":"coach","site_id","coverage","prompt","t","status"?},
 * docs/CONTRACTS.md "Coach message") into the coverage / prompt / status updates the coach page renders.
 * Anything that is not a well-formed coach message yields no updates.
 */
export function coachFromWire(raw: unknown): CoachMessage[] {
  if (!raw || typeof raw !== "object") return [];
  const m = raw as Partial<CoachWire> & { type?: unknown };
  if (m.type !== "coach") return [];
  const out: CoachMessage[] = [];
  const c = m.coverage;
  if (c && typeof c === "object" && Array.isArray(c.cells) && c.cells.length === c.width * c.height && c.width > 0 && c.height > 0) {
    out.push({ type: "coverage", cell_m: c.cell_m, origin: [c.origin[0], c.origin[1]], width: c.width, height: c.height, cells: c.cells });
  }
  if (typeof m.prompt === "string" && m.prompt) out.push({ type: "prompt", text: m.prompt, t: typeof m.t === "number" ? m.t : undefined });
  if (m.status && typeof m.status === "object") out.push({ type: "status", device: m.status.device, frames: m.status.frames, tracking: m.status.tracking });
  return out;
}
