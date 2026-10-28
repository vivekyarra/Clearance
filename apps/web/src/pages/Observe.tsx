import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { loadOpenCV, TagDetector } from "../observe/apriltag";
import { BlurGuard, type BlurReason } from "../observe/blurguard";
import { FaceTracker, loadFaceDetector, obscureFrame, pixelate, type Box } from "../observe/faceblur";
import { cameraPosition, PoseStabilizer } from "../observe/pose";
import type { ObserverPose } from "../types";

type Phase = "intro" | "starting" | "running" | "uploading" | "done" | "error";
type Lock = { state: "searching" | "holding" | "locked" | "moved"; heldS: number };

const REC_FPS = 8;
const DETECT_MS = 180;
const MAX_REC_S = 30 * 60;
const STILL_EVERY_S = 90;
const MAX_STILLS = 20;
const HFOV_DEG = 66;
const MAX_DETECT_MS = 250; // a slower detection counts as a detector failure (after warm-up)

function deviceModel(): { model: string; os: string } {
  const ua = navigator.userAgent;
  const android = /Android\s([\d.]+);\s*([^;)]+)/.exec(ua);
  if (android) return { model: android[2].trim(), os: `Android ${android[1]}` };
  return { model: /iPhone|iPad/.test(ua) ? "iOS device" : "Browser camera", os: navigator.platform || "" };
}

const fmtTime = (s: number) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

/** Android fixed-observer PWA (PLAN 7.1): marker lock, on-device face blur, blurred recording, upload. */
export default function ObservePage() {
  const { site = "" } = useParams();
  const [phase, setPhase] = useState<Phase>("intro");
  const [err, setErr] = useState<string | null>(null);
  const [sizeCm, setSizeCm] = useState(16);
  const [cvState, setCvState] = useState<"loading" | "ready" | "error">("loading");
  const [faceState, setFaceState] = useState<"loading" | "ready" | "error">("loading");
  const [lock, setLock] = useState<Lock>({ state: "searching", heldS: 0 });
  const [recording, setRecording] = useState(false);
  const [recS, setRecS] = useState(0);
  const [faces, setFaces] = useState(0);
  const [battery, setBattery] = useState<{ level: number; charging: boolean } | null>(null);
  const [upload, setUpload] = useState(0);
  const [result, setResult] = useState<string | null>(null);

  const video = useRef<HTMLVideoElement>(null);
  const view = useRef<HTMLCanvasElement>(null);
  const raw = useRef<HTMLCanvasElement | null>(null);
  const scratch = useRef<HTMLCanvasElement | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const stills = useRef<Blob[]>([]);
  const lockedPose = useRef<ObserverPose | null>(null);
  const startedAt = useRef<string>("");
  const recStart = useRef(0);
  const lastStill = useRef(0);
  const wake = useRef<{ release(): Promise<void> } | null>(null);
  const blurFailed = useRef(false);

  // battery
  useEffect(() => {
    const nav = navigator as Navigator & { getBattery?: () => Promise<{ level: number; charging: boolean; addEventListener: (e: string, f: () => void) => void }> };
    nav.getBattery?.().then((b) => {
      const u = () => setBattery({ level: b.level, charging: b.charging });
      u();
      b.addEventListener("levelchange", u);
      b.addEventListener("chargingchange", u);
    });
  }, []);

  async function start() {
    setPhase("starting");
    setErr(null);
    blurFailed.current = false; // a fresh detector is loaded for the new camera session
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } }, audio: false });
      stream.current = s;
      const v = video.current!;
      v.srcObject = s;
      await v.play();
      try {
        wake.current = await (navigator as Navigator & { wakeLock?: { request(t: string): Promise<{ release(): Promise<void> }> } }).wakeLock?.request("screen") ?? null;
      } catch {
        /* screen may dim; recording continues */
      }
      setPhase("running");
    } catch (e) {
      setErr(location.protocol !== "https:" && location.hostname !== "localhost" ? "The camera needs HTTPS. Open this page over https:// or from localhost." : `Camera unavailable: ${(e as Error).message}`);
      setPhase("error");
    }
  }

  // processing loop
  useEffect(() => {
    if (phase !== "running") return;
    const v = video.current!;
    const out = view.current!;
    const w = v.videoWidth || 1280;
    const h = v.videoHeight || 720;
    out.width = w;
    out.height = h;
    raw.current = raw.current ?? document.createElement("canvas");
    raw.current.width = w;
    raw.current.height = h;
    scratch.current = scratch.current ?? document.createElement("canvas");
    const rctx = raw.current.getContext("2d", { willReadFrequently: true })!;
    const octx = out.getContext("2d")!;
    const stab = new PoseStabilizer(2.0, 0.02);
    const tracker = new FaceTracker(700, 0.25);
    const guard = new BlurGuard(MAX_DETECT_MS, 400, 15);
    let detector: TagDetector | null = null;
    let face: Awaited<ReturnType<typeof loadFaceDetector>> | null = null;
    let alive = true;
    let raf = 0;
    let lastDetect = 0;
    let corners: [number, number][] | null = null;
    let boxes: Box[] = [];

    loadOpenCV()
      .then((cv) => {
        if (!alive) return;
        detector = new TagDetector(cv, w, h, 0, sizeCm / 100, HFOV_DEG);
        setCvState("ready");
      })
      .catch(() => setCvState("error"));
    loadFaceDetector()
      .then((f) => {
        if (alive) {
          face = f;
          guard.detectorReady();
          setFaceState("ready");
        } else f.close();
      })
      .catch(() => {
        guard.detectorUnavailable();
        setFaceState("error");
      });

    const loop = () => {
      if (!alive) return;
      const now = performance.now();
      rctx.drawImage(v, 0, 0, w, h);
      // faces first: every displayed or recorded pixel goes through the blur. The detector runs on the exact
      // frame that is drawn (the raw canvas), and the guard fails closed: no detector, an error, a slow call
      // or a stale result blurs the whole frame; an error or timeout also stops recording for good.
      let found: Box[] = [];
      let decision = guard.decide(now);
      if (face) {
        const t0 = performance.now();
        try {
          found = face.detectForVideo(raw.current!, now).detections.map((d) => d.boundingBox!).filter(Boolean).map((b) => ({ x: b.originX, y: b.originY, w: b.width, h: b.height }));
          decision = guard.frame({ ok: true, durationMs: performance.now() - t0 }, now);
        } catch {
          found = [];
          decision = guard.frame({ ok: false }, now);
        }
      }
      if (decision.blurFull) {
        boxes = [];
        obscureFrame(octx, raw.current!, scratch.current!);
      } else {
        boxes = tracker.update(found, now);
        octx.drawImage(raw.current!, 0, 0);
        pixelate(octx, boxes, scratch.current!);
      }
      if (decision.stopRecording && guard.failure) failClosed(guard.failure);
      // marker detection on the raw frame (in memory only, never stored)
      if (detector && now - lastDetect > DETECT_MS) {
        lastDetect = now;
        let det = null;
        try {
          det = detector.detect(raw.current!);
        } catch {
          det = null;
        }
        corners = det?.corners ?? null;
        const t = now / 1000;
        const locked = lockedPose.current;
        if (locked) {
          if (det) {
            const a = cameraPosition(locked.T_site_cam);
            const b = cameraPosition(det.T_site_cam);
            if (Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]) > 0.05) {
              lockedPose.current = null;
              stab.reset();
              setLock({ state: "moved", heldS: 0 });
            }
          }
        } else {
          const r = stab.push(t, det?.T_site_cam ?? null);
          if (r.stable && r.pose) {
            lockedPose.current = { T_site_cam: r.pose, K: detector.intrinsics, w, h, K_source: `assumed_hfov_${HFOV_DEG}deg`, marker_size_m: sizeCm / 100, locked_at: new Date().toISOString(), reproj_err_px: det?.reprojErrPx ?? null };
            setLock({ state: "locked", heldS: r.heldS });
            navigator.vibrate?.([80, 40, 80]);
            out.toBlob((b) => b && stills.current.length < MAX_STILLS && stills.current.push(b), "image/jpeg", 0.85);
          } else setLock((l) => (l.state === "moved" && !det ? l : { state: det ? "holding" : "searching", heldS: r.heldS }));
        }
      }
      if (corners) {
        octx.strokeStyle = lockedPose.current ? "#3ccf7f" : "#f0a73a";
        octx.lineWidth = 4;
        octx.beginPath();
        corners.forEach(([x, y], i) => (i ? octx.lineTo(x, y) : octx.moveTo(x, y)));
        octx.closePath();
        octx.stroke();
      }
      setFaces(boxes.length);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
      face?.close();
    };
  }, [phase, sizeCm]);

  // recording clock + stills + auto stop
  useEffect(() => {
    if (!recording) return;
    const id = setInterval(() => {
      const s = (performance.now() - recStart.current) / 1000;
      setRecS(s);
      if (s - lastStill.current > STILL_EVERY_S && stills.current.length < MAX_STILLS) {
        lastStill.current = s;
        view.current?.toBlob((b) => b && stills.current.push(b), "image/jpeg", 0.85);
      }
      if (s >= MAX_REC_S) stopRec();
    }, 500);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recording]);

  /** The face detector failed while running: stop recording (kept for upload: it was blurred) and lock it. */
  function failClosed(reason: BlurReason) {
    if (blurFailed.current) return;
    blurFailed.current = true;
    setFaceState("error");
    const r = recorder.current;
    if (r) {
      r.onstop = null; // no automatic upload; the person decides below
      r.stop();
      recorder.current = null;
      setRecording(false);
    }
    stream.current?.getTracks().forEach((t) => t.stop());
    void wake.current?.release().catch(() => undefined);
    setErr(
      `Face blur stopped working (${reason === "timeout" ? "the detector became too slow" : "detector error"}), so the camera and recording stopped to protect privacy.` +
        (r ? " Everything recorded before that was blurred; you can upload it below." : " Start the camera again to retry."),
    );
    setPhase("error");
  }

  function startRec() {
    if (blurFailed.current) return;
    const c = view.current!;
    const s = c.captureStream(REC_FPS);
    const types = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm", "video/mp4"];
    const mimeType = types.find((t) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(t));
    const r = new MediaRecorder(s, { mimeType, videoBitsPerSecond: 1_500_000 });
    chunks.current = [];
    r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
    r.start(5000);
    recorder.current = r;
    recStart.current = performance.now();
    lastStill.current = 0;
    startedAt.current = new Date().toISOString();
    setRecording(true);
  }

  function stopRec() {
    const r = recorder.current;
    if (!r) return;
    r.onstop = () => void uploadAll();
    r.stop();
    recorder.current = null;
    setRecording(false);
  }

  async function uploadAll() {
    setPhase("uploading");
    setUpload(0);
    try {
      const videoBlob = new Blob(chunks.current, { type: chunks.current[0]?.type || "video/webm" });
      const ext = videoBlob.type.includes("mp4") ? "mp4" : "webm";
      const poseBlob = new Blob([JSON.stringify(lockedPose.current)], { type: "application/json" });
      const stillNames = stills.current.map((_, i) => `stills/${String(i).padStart(3, "0")}.jpg`);
      const reg = await api.registerCapture(site, {
        device: { ...deviceModel(), tier: "T3_FIXED_OBSERVER" },
        role: "observer",
        marker: { family: "tag36h11", id: 0, size_m: sizeCm / 100 },
        files: { video: `observer.${ext}`, observer_pose: "observer_pose.json", frames: stillNames.length ? "stills/" : null, poses: null },
        extra_files: stillNames,
        privacy: { faces_blurred_on_device: true, method: "mediapipe_face_detector+pixelate", raw_frames_uploaded: false },
        started_at: startedAt.current,
        duration_s: Math.round(recS),
      });
      const files: [string, Blob][] = [[`observer.${ext}`, videoBlob], ["observer_pose.json", poseBlob], ...stillNames.map((n, i) => [n, stills.current[i]] as [string, Blob])];
      const total = files.reduce((s, [, b]) => s + b.size, 0) || 1;
      let done = 0;
      for (const [name, blob] of files) {
        const url = reg.upload_urls?.[name];
        if (!url) continue;
        await api.uploadFile(url, blob, (f) => setUpload((done + f * blob.size) / total));
        done += blob.size;
        setUpload(done / total);
      }
      await api.completeCapture(reg.capture_id);
      setResult(reg.capture_id);
      setPhase("done");
      stream.current?.getTracks().forEach((t) => t.stop());
      await wake.current?.release().catch(() => undefined);
    } catch (e) {
      setErr(`Upload failed: ${(e as Error).message}. The recording stays on this page; retry below.`);
      setPhase("error");
    }
  }

  const canRecord = lock.state === "locked" && faceState === "ready" && !blurFailed.current && !recording;
  const lowBattery = battery && battery.level < 0.2 && !battery.charging;

  const shared = (
    <>
      <video ref={video} playsInline muted style={{ display: "none" }} />
      <canvas ref={view} className="view" style={{ display: phase === "running" || phase === "uploading" ? "block" : "none", position: "fixed", inset: 0, width: "100%", height: "100%", objectFit: "contain", background: "#000" }} />
    </>
  );
  return (
    <>
      {shared}
      {body()}
    </>
  );

  function body() {
  if (phase === "intro" || phase === "error" || phase === "done")
    return (
      <div className="phone" style={{ padding: "28px 18px", gap: 16, background: "radial-gradient(circle at 50% 0%, #1a2440, #07090d)" }}>
        <span className="pill">Clearance · fixed observer</span>
        <h1 style={{ fontSize: 26 }}>{phase === "done" ? "Upload complete ✓" : "Record foot traffic"}</h1>
        {phase === "done" ? (
          <div style={{ opacity: 0.8 }}>
            Capture <span className="mono">{result}</span> is processing. You can take the phone off the stand.
          </div>
        ) : (
          <>
            <ol style={{ opacity: 0.85, lineHeight: 1.6, paddingLeft: 20, margin: 0 }}>
              <li>Put the phone on a stand with the floor marker in view.</li>
              <li>Hold still for 2 seconds until it shows Locked ✓. Do not move the phone after that.</li>
              <li>Faces are blurred on this phone before anything is recorded. Raw frames are never uploaded.</li>
            </ol>
            <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span style={{ opacity: 0.7, fontSize: 13 }}>Measured marker size (black square)</span>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input className="input" type="number" step={0.1} min={4} max={17} value={sizeCm} onChange={(e) => setSizeCm(Number(e.target.value))} style={{ width: 110, background: "rgba(255,255,255,0.1)", color: "#fff", borderColor: "rgba(255,255,255,0.25)" }} />
                cm
              </span>
            </label>
            {err && <div className="pill err" style={{ borderRadius: 12, padding: 12 }}>{err}</div>}
            <div style={{ flex: 1 }} />
            {phase === "error" && chunks.current.length > 0 ? (
              <button className="bigbtn" onClick={() => void uploadAll()}>
                Retry upload
              </button>
            ) : (
              <button className="bigbtn" onClick={start}>
                Start camera
              </button>
            )}
          </>
        )}
      </div>
    );

  return (
    <div className="phone" style={{ background: "transparent" }}>
      <div className="hud" style={{ zIndex: 2 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span className={`pill ${lock.state === "locked" ? "ok" : lock.state === "moved" ? "err" : lock.state === "holding" ? "warn" : ""}`}>
            {lock.state === "locked" ? "Locked ✓" : lock.state === "moved" ? "Phone moved: relocking" : lock.state === "holding" ? `Hold still ${Math.max(0, 2 - lock.heldS).toFixed(1)} s` : cvState === "loading" ? "Loading marker detector" : cvState === "error" ? "Marker detector unavailable" : "Point at the floor marker"}
          </span>
          <span className="pill" style={{ fontSize: 12 }}>
            {faceState === "ready" ? `Face blur on${faces ? ` · ${faces} blurred` : ""}` : faceState === "loading" ? "Loading face blur" : "Face blur unavailable"}
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
          {recording && <span className="pill rec">{fmtTime(recS)}</span>}
          {battery && <span className={`pill ${lowBattery ? "warn" : ""}`}>{lowBattery ? "Battery low: plug in" : `Battery ${Math.round(battery.level * 100)}%${battery.charging ? " ⚡" : ""}`}</span>}
        </div>
      </div>
      <div className="hud-bottom" style={{ zIndex: 2 }}>
        {phase === "uploading" ? (
          <>
            <div className="progress" style={{ background: "rgba(255,255,255,.15)" }}>
              <i style={{ width: `${Math.round(upload * 100)}%` }} />
            </div>
            <div style={{ textAlign: "center" }}>Uploading blurred recording {Math.round(upload * 100)}%</div>
          </>
        ) : recording ? (
          <button className="bigbtn red" onClick={stopRec}>
            Stop and upload
          </button>
        ) : (
          <>
            {faceState === "error" && <div className="pill err" style={{ borderRadius: 12 }}>Face blur could not load, so recording stays off to protect privacy.</div>}
            <button className="bigbtn" disabled={!canRecord} onClick={startRec}>
              {lock.state !== "locked" ? "Waiting for lock" : faceState !== "ready" ? "Waiting for face blur" : "Start recording (up to 30 min)"}
            </button>
          </>
        )}
      </div>
    </div>
  );
  }
}
