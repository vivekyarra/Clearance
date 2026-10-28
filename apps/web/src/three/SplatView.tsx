import { useEffect, useRef, useState } from "react";
import { API_BASE } from "../api/http";
import { splatFormat } from "../lib/assets";

/** Gaussian splat of the scanned room (GaussianSplats3D, loaded only when this view opens). */
export default function SplatView({ url, height }: { url: string; height: number }) {
  const host = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  useEffect(() => {
    let viewer: { dispose?: () => Promise<void> | void } | null = null;
    let gone = false;
    const src = /^(https?:)?\//.test(url) ? url : `${API_BASE}/${url.replace(/^\/+/, "")}`;
    import("@mkkellogg/gaussian-splats-3d")
      .then(async (G) => {
        if (gone) return;
        const v = new G.Viewer({ rootElement: host.current, cameraUp: [0, 0, 1], initialCameraPosition: [0, -4, 3], initialCameraLookAt: [0, 0, 0], sharedMemoryForWorkers: false, selfDrivenMode: true });
        viewer = v;
        const fmt = splatFormat(src);
        await v.addSplatScene(src, { showLoadingUI: false, progressiveLoad: true, onProgress: (p: number) => setProgress(p), ...(fmt ? { format: G.SceneFormat[fmt] } : {}) });
        v.start();
      })
      .catch((e: Error) => setErr(e.message));
    return () => {
      gone = true;
      void viewer?.dispose?.();
    };
  }, [url]);
  return (
    <div style={{ position: "relative", height, background: "#000" }}>
      <div ref={host} style={{ position: "absolute", inset: 0 }} />
      {err && <div className="error-box" style={{ position: "absolute", left: 12, top: 12 }}>Splat could not load: {err}</div>}
      {!err && progress < 100 && <div className="badge" style={{ position: "absolute", left: 12, top: 12 }}>Loading splat {Math.round(progress)}%</div>}
    </div>
  );
}
