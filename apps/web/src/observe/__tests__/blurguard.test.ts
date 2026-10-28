import { describe, expect, it } from "vitest";
import { BlurGuard } from "../blurguard";

const ok = (ms = 20) => ({ ok: true as const, durationMs: ms });

function healthy(): BlurGuard {
  const g = new BlurGuard(250, 400, 2);
  g.detectorReady();
  return g;
}

describe("BlurGuard (fail closed)", () => {
  it("blurs the whole frame and blocks recording until the detector is ready", () => {
    const g = new BlurGuard();
    expect(g.decide(0)).toEqual({ blurFull: true, stopRecording: true, reason: "loading" });
    expect(g.canRecord(0)).toBe(false);
  });

  it("uses face boxes only after a clean, recent detection", () => {
    const g = healthy();
    expect(g.decide(0).reason).toBe("stale"); // ready but nothing detected yet
    expect(g.frame(ok(), 100)).toEqual({ blurFull: false, stopRecording: false, reason: null });
    expect(g.canRecord(150)).toBe(true);
  });

  it("an unavailable detector never records", () => {
    const g = new BlurGuard();
    g.detectorUnavailable();
    expect(g.decide(0)).toEqual({ blurFull: true, stopRecording: true, reason: "unavailable" });
  });

  it("a detector error blurs that frame and every later one, and stops recording", () => {
    const g = healthy();
    g.frame(ok(), 0);
    expect(g.frame({ ok: false }, 33)).toEqual({ blurFull: true, stopRecording: true, reason: "error" });
    // later clean detections do not reopen the face boxes
    for (let t = 66; t < 2000; t += 33) expect(g.frame(ok(), t)).toEqual({ blurFull: true, stopRecording: true, reason: "error" });
    expect(g.canRecord(2000)).toBe(false);
    expect(g.failure).toBe("error");
  });

  it("errors that keep repeating stay closed past the old 600 ms box hold", () => {
    const g = healthy();
    g.frame(ok(), 0);
    for (let t = 33; t < 5000; t += 33) {
      const d = g.frame({ ok: false }, t);
      expect(d.blurFull).toBe(true);
      expect(d.stopRecording).toBe(true);
    }
  });

  it("a slow detection after warm-up is a timeout failure", () => {
    const g = healthy();
    g.frame(ok(900), 0); // warm-up call 1: slow, frame fully blurred, not latched
    expect(g.frame(ok(900), 33)).toMatchObject({ blurFull: true, stopRecording: false, reason: "slow" });
    expect(g.frame(ok(), 66).blurFull).toBe(false);
    expect(g.frame(ok(251), 99)).toEqual({ blurFull: true, stopRecording: true, reason: "timeout" });
    expect(g.frame(ok(), 132).reason).toBe("timeout");
  });

  it("treats non-finite or negative durations as a timeout", () => {
    for (const ms of [Number.NaN, Number.POSITIVE_INFINITY, -1]) {
      const g = healthy();
      g.frame(ok(), 0);
      g.frame(ok(), 10);
      expect(g.frame(ok(ms), 20)).toMatchObject({ blurFull: true, stopRecording: true });
    }
  });

  it("goes back to a full blur when detections stop arriving", () => {
    const g = healthy();
    g.frame(ok(), 0);
    expect(g.decide(400).blurFull).toBe(false);
    expect(g.decide(401)).toEqual({ blurFull: true, stopRecording: false, reason: "stale" });
    expect(g.decide(Number.NaN).blurFull).toBe(true);
  });

  it("random outcome sequences never show face boxes after a failure", () => {
    let seed = 12345;
    const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);
    for (let run = 0; run < 200; run++) {
      const g = new BlurGuard(250, 400, 3);
      g.detectorReady();
      let failedAt = -1;
      for (let i = 0; i < 200; i++) {
        const r = rnd();
        const outcome = r < 0.02 ? ({ ok: false } as const) : ok(r < 0.05 ? 400 : 20);
        const d = g.frame(outcome, i * 33);
        if (g.failure && failedAt < 0) failedAt = i;
        if (failedAt >= 0) {
          expect(d.blurFull).toBe(true);
          expect(d.stopRecording).toBe(true);
        }
        if (!d.blurFull) expect(outcome.ok && outcome.durationMs <= 250).toBe(true);
      }
    }
  });
});
