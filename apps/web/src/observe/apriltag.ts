/**
 * AprilTag 36h11 detection + solvePnP with OpenCV.js 4.10 (npm @techstark/opencv-js, pinned in package.json),
 * bundled with the app and loaded lazily from this origin; no CDN at run time.
 * Uses the objdetect ArUco API (OpenCV >= 4.7), which ships the AprilTag dictionaries.
 */
import opencvUrl from "../../node_modules/@techstark/opencv-js/dist/opencv.js?url";
import { intrinsicsFromFov, markerObjectPoints, rodrigues, siteFromCamera, type Vec3 } from "./pose";

export const OPENCV_URL = opencvUrl;
const DICT_APRILTAG_36h11 = 20; // cv::aruco::PredefinedDictionaryType, used when the enum is not exported
const SOLVEPNP_IPPE_SQUARE = 7;

/* eslint-disable @typescript-eslint/no-explicit-any */
type CV = any;
let loading: Promise<CV> | null = null;

export function loadOpenCV(timeoutMs = 60000): Promise<CV> {
  if (loading) return loading;
  loading = new Promise<CV>((resolve, reject) => {
    const w = window as any;
    const ready = (cv: CV) => {
      if (cv && typeof cv.then === "function") cv.then((m: CV) => resolve(m), reject);
      else if (cv?.Mat) resolve(cv);
      else if (cv) cv.onRuntimeInitialized = () => resolve(cv);
    };
    if (w.cv) return ready(w.cv);
    const s = document.createElement("script");
    s.src = OPENCV_URL;
    s.async = true;
    s.onload = () => ready(w.cv);
    s.onerror = () => reject(new Error("OpenCV.js failed to load"));
    document.head.appendChild(s);
    setTimeout(() => reject(new Error("OpenCV.js load timed out")), timeoutMs);
  });
  loading.catch(() => (loading = null));
  return loading;
}

export interface Detection {
  corners: [number, number][]; // TL, TR, BR, BL (pixels)
  T_site_cam: number[];
  reprojErrPx: number;
}

export class TagDetector {
  private detector: any;
  private K: [number, number, number, number];
  constructor(
    private cv: CV,
    public w: number,
    public h: number,
    public markerId = 0,
    public sizeM = 0.16,
    hfovDeg = 66,
  ) {
    const dictId = cv.DICT_APRILTAG_36h11 ?? DICT_APRILTAG_36h11;
    const dict = cv.getPredefinedDictionary(dictId);
    const Det = cv.aruco_ArucoDetector ?? cv.ArucoDetector;
    const Params = cv.aruco_DetectorParameters ?? cv.DetectorParameters;
    const Refine = cv.aruco_RefineParameters ?? cv.RefineParameters;
    if (!Det || !Params) throw new Error("this OpenCV.js build has no ArUco detector");
    const params = new Params();
    // AprilTag corner refinement gives the sub-pixel corners solvePnP needs
    if ("cornerRefinementMethod" in params) params.cornerRefinementMethod = cv.CORNER_REFINE_APRILTAG ?? 3;
    this.detector = Refine ? new Det(dict, params, new Refine(10, 3, true)) : new Det(dict, params);
    this.K = intrinsicsFromFov(w, h, hfovDeg);
  }

  get intrinsics() {
    return this.K;
  }

  /** Detect on an RGBA canvas/ImageData. Returns null when the configured marker id is not visible. */
  detect(source: HTMLCanvasElement): Detection | null {
    const cv = this.cv;
    const rgba = cv.imread(source);
    const gray = new cv.Mat();
    const corners = new cv.MatVector();
    const rejected = new cv.MatVector();
    const ids = new cv.Mat();
    try {
      cv.cvtColor(rgba, gray, cv.COLOR_RGBA2GRAY);
      this.detector.detectMarkers(gray, corners, ids, rejected);
      for (let i = 0; i < ids.rows; i++) {
        if (ids.data32S[i] !== this.markerId) continue;
        const c = corners.get(i);
        const d = c.data32F;
        const pts: [number, number][] = [
          [d[0], d[1]],
          [d[2], d[3]],
          [d[4], d[5]],
          [d[6], d[7]],
        ];
        c.delete();
        const pose = this.solve(pts);
        return pose ? { corners: pts, ...pose } : null;
      }
      return null;
    } finally {
      rgba.delete();
      gray.delete();
      corners.delete();
      rejected.delete();
      ids.delete();
    }
  }

  private solve(pts: [number, number][]): { T_site_cam: number[]; reprojErrPx: number } | null {
    const cv = this.cv;
    const obj = markerObjectPoints(this.sizeM);
    const objM = cv.matFromArray(4, 3, cv.CV_64F, obj.flat());
    const imgM = cv.matFromArray(4, 2, cv.CV_64F, pts.flat());
    const [fx, fy, cx, cy] = this.K;
    const K = cv.matFromArray(3, 3, cv.CV_64F, [fx, 0, cx, 0, fy, cy, 0, 0, 1]);
    const dist = cv.Mat.zeros(1, 5, cv.CV_64F);
    const rvec = new cv.Mat();
    const tvec = new cv.Mat();
    try {
      let ok = false;
      try {
        ok = cv.solvePnP(objM, imgM, K, dist, rvec, tvec, false, cv.SOLVEPNP_IPPE_SQUARE ?? SOLVEPNP_IPPE_SQUARE);
      } catch {
        ok = cv.solvePnP(objM, imgM, K, dist, rvec, tvec, false, cv.SOLVEPNP_ITERATIVE ?? 0);
      }
      if (!ok) return null;
      const r = Array.from(rvec.data64F as Float64Array) as Vec3;
      const t = Array.from(tvec.data64F as Float64Array) as Vec3;
      return { T_site_cam: siteFromCamera(r, t), reprojErrPx: reprojection(obj, pts, r, t, this.K) };
    } finally {
      objM.delete();
      imgM.delete();
      K.delete();
      dist.delete();
      rvec.delete();
      tvec.delete();
    }
  }
}

function reprojection(obj: Vec3[], img: [number, number][], r: Vec3, t: Vec3, K: [number, number, number, number]): number {
  const R = rodrigues(r);
  let e = 0;
  obj.forEach((p, i) => {
    const x = R[0] * p[0] + R[1] * p[1] + R[2] * p[2] + t[0];
    const y = R[3] * p[0] + R[4] * p[1] + R[5] * p[2] + t[1];
    const z = R[6] * p[0] + R[7] * p[1] + R[8] * p[2] + t[2];
    e += Math.hypot(K[0] * (x / z) + K[2] - img[i][0], K[1] * (y / z) + K[3] - img[i][1]);
  });
  return e / obj.length;
}
