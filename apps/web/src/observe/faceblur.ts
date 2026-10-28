/**
 * On-device face blur with MediaPipe Tasks Face Detector (web). The library, its SIMD wasm build and the
 * BlazeFace short-range model are bundled with the app (npm @mediapipe/tasks-vision, pinned in package.json;
 * model in ./models), so nothing is fetched from a CDN at run time.
 * Frames are blurred on a canvas before they are shown, recorded or saved; raw frames never leave the phone.
 * The observer page treats any detector failure as "blur the whole frame and stop recording" (blurguard.ts).
 */
import wasmBinaryPath from "../../node_modules/@mediapipe/tasks-vision/wasm/vision_wasm_internal.wasm?url";
import wasmLoaderPath from "../../node_modules/@mediapipe/tasks-vision/wasm/vision_wasm_internal.js?url";
// blaze_face_short_range.tflite (float16, v1), sha256 b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f
import modelAssetPath from "./models/blaze_face_short_range.tflite?url";

export const MP_VERSION = "0.10.14";

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FaceDetectorLike {
  detectForVideo(frame: HTMLVideoElement | HTMLCanvasElement, ts: number): { detections: { boundingBox?: { originX: number; originY: number; width: number; height: number } }[] };
  close(): void;
}

export async function loadFaceDetector(): Promise<FaceDetectorLike> {
  const mod = await import("@mediapipe/tasks-vision");
  // SIMD build only (every browser that runs this page supports wasm SIMD); keeps the bundle near 10 MB
  const fileset = { wasmLoaderPath, wasmBinaryPath };
  const make = (delegate: "GPU" | "CPU") =>
    mod.FaceDetector.createFromOptions(fileset, {
      baseOptions: { modelAssetPath, delegate },
      runningMode: "VIDEO",
      minDetectionConfidence: 0.35,
    });
  try {
    return await make("GPU");
  } catch {
    return await make("CPU");
  }
}

/**
 * Keeps recent face boxes alive for `holdMs` so a single missed detection never exposes a face,
 * and grows each box by `pad` on every side.
 */
export class FaceTracker {
  private boxes: { b: Box; t: number }[] = [];
  constructor(
    public holdMs = 600,
    public pad = 0.25,
  ) {}
  update(found: Box[], now: number): Box[] {
    for (const b of found) this.boxes.push({ b: grow(b, this.pad), t: now });
    this.boxes = this.boxes.filter((x) => now - x.t <= this.holdMs);
    return this.boxes.map((x) => x.b);
  }
}

export function grow(b: Box, pad: number): Box {
  return { x: b.x - b.w * pad, y: b.y - b.h * pad * 1.2, w: b.w * (1 + 2 * pad), h: b.h * (1 + 2.4 * pad) };
}

/** Pixelate boxes in place (coarse mosaic; not reversible from the output). */
export function pixelate(ctx: CanvasRenderingContext2D, boxes: Box[], scratch: HTMLCanvasElement, block = 14) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  const sctx = scratch.getContext("2d")!;
  for (const raw of boxes) {
    const x = Math.max(0, Math.floor(raw.x));
    const y = Math.max(0, Math.floor(raw.y));
    const w = Math.min(W - x, Math.ceil(raw.w));
    const h = Math.min(H - y, Math.ceil(raw.h));
    if (w <= 1 || h <= 1) continue;
    const sw = Math.max(1, Math.round(w / block));
    const sh = Math.max(1, Math.round(h / block));
    scratch.width = sw;
    scratch.height = sh;
    sctx.imageSmoothingEnabled = true;
    sctx.drawImage(ctx.canvas, x, y, w, h, 0, 0, sw, sh);
    ctx.save();
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(scratch, 0, 0, sw, sh, x, y, w, h);
    ctx.restore();
  }
}

/**
 * Draw `src` onto `ctx` with every detail destroyed (1/`factor` downscale, smoothed upscale, then a blur
 * filter where supported). It never draws the sharp frame first, so no intermediate canvas state shows it.
 */
export function obscureFrame(ctx: CanvasRenderingContext2D, src: CanvasImageSource & { width: number; height: number }, scratch: HTMLCanvasElement, factor = 24) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  const sw = Math.max(1, Math.round(W / factor));
  const sh = Math.max(1, Math.round(H / factor));
  scratch.width = sw;
  scratch.height = sh;
  const sctx = scratch.getContext("2d")!;
  sctx.imageSmoothingEnabled = true;
  sctx.drawImage(src, 0, 0, src.width, src.height, 0, 0, sw, sh);
  ctx.save();
  ctx.imageSmoothingEnabled = true;
  if ("filter" in ctx) ctx.filter = "blur(12px)";
  ctx.drawImage(scratch, 0, 0, sw, sh, 0, 0, W, H);
  ctx.restore();
}
