/**
 * Fail-closed face blur decisions for the observer page.
 *
 * Every frame is drawn only after the face detector ran on it. Anything other than a clean, timely detection
 * (detector missing or still loading, a thrown error, a call slower than `maxDetectMs`, or no successful
 * detection for `staleMs`) blurs the whole frame. A thrown error, or a slow call after the first
 * `warmupCalls` calls (first calls compile GPU programs), also latches a failure: recording stops and stays
 * off until the camera is restarted, because a detector that failed once cannot be trusted with later faces.
 */
export type BlurReason = "loading" | "unavailable" | "error" | "timeout" | "slow" | "stale";

export interface BlurDecision {
  /** blur the entire frame instead of pixelating the detected boxes */
  blurFull: boolean;
  /** stop (and do not start) recording */
  stopRecording: boolean;
  /** why the frame is fully blurred; null when the face boxes are used */
  reason: BlurReason | null;
}

export type DetectOutcome = { ok: true; durationMs: number } | { ok: false };

export class BlurGuard {
  private ready = false;
  private unavailable = false;
  private failed: "error" | "timeout" | null = null;
  private lastOk: number | null = null;
  private calls = 0;

  constructor(
    public maxDetectMs = 250,
    public staleMs = 400,
    public warmupCalls = 15,
  ) {}

  /** The detector finished loading. */
  detectorReady(): void {
    this.ready = true;
  }

  /** The detector could not be loaded. */
  detectorUnavailable(): void {
    this.unavailable = true;
  }

  /** Record one detection call on the frame about to be drawn and decide how to draw that frame. */
  frame(outcome: DetectOutcome, now: number): BlurDecision {
    this.calls += 1;
    if (!outcome.ok) {
      this.failed = "error";
      return this.decide(now);
    }
    const timely = Number.isFinite(outcome.durationMs) && outcome.durationMs >= 0 && outcome.durationMs <= this.maxDetectMs;
    if (!timely) {
      if (this.calls > this.warmupCalls) this.failed = this.failed ?? "timeout";
      const d = this.decide(now);
      return d.stopRecording ? d : { blurFull: true, stopRecording: false, reason: "slow" };
    }
    if (!this.failed) this.lastOk = now;
    return this.decide(now);
  }

  /** Decision for a frame drawn without a new detection (detector not loaded, failed to load, or skipped). */
  decide(now: number): BlurDecision {
    if (this.failed) return { blurFull: true, stopRecording: true, reason: this.failed };
    if (this.unavailable) return { blurFull: true, stopRecording: true, reason: "unavailable" };
    if (!this.ready) return { blurFull: true, stopRecording: true, reason: "loading" };
    if (this.lastOk === null || !(now - this.lastOk <= this.staleMs)) return { blurFull: true, stopRecording: false, reason: "stale" };
    return { blurFull: false, stopRecording: false, reason: null };
  }

  /** Recording may start only while the detector is healthy right now. */
  canRecord(now: number): boolean {
    const d = this.decide(now);
    return !d.blurFull && !d.stopRecording;
  }

  get failure(): "error" | "timeout" | null {
    return this.failed;
  }
}
