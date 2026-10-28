import { useEffect, useMemo, useState } from "react";
import type { AnalysisDoc } from "../../types";
import { replayDoc, replayDuration } from "./stages";

/**
 * Offline mode: replay a recorded (finished) Clearance check stage by stage on a timer, so the
 * waiting view and its animation can be shown without services/api.
 */
export function useReplay(final: AnalysisDoc | undefined, active: boolean): { doc: AnalysisDoc | null; elapsed: number | null; skip: () => void } {
  const enabled = active && final?.status === "done";
  const [t0, setT0] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const [skipped, setSkipped] = useState(false);
  const total = useMemo(() => (final && enabled ? replayDuration(final) : 0), [final, enabled]);

  useEffect(() => {
    if (enabled && t0 == null && !skipped) setT0(performance.now());
  }, [enabled, t0, skipped]);
  useEffect(() => {
    if (!enabled || t0 == null || skipped) return;
    let id = 0;
    const tick = () => {
      const n = performance.now();
      setNow(n);
      if ((n - t0) / 1000 < total) id = window.setTimeout(tick, 200);
    };
    tick();
    return () => clearTimeout(id);
  }, [enabled, t0, skipped, total]);

  const elapsed = enabled && !skipped ? (t0 == null ? 0 : Math.max(0, (now - t0) / 1000)) : null;
  const doc = useMemo(() => (final && elapsed != null ? replayDoc(final, elapsed) : null), [final, elapsed]);
  return { doc, elapsed: doc ? elapsed : null, skip: () => setSkipped(true) };
}
