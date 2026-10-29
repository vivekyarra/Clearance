import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import QRCode from "qrcode";
import type { StageStatus, VerdictLabel } from "../types";
import { Icon } from "./Icon";

export function ReferenceBadge() {
  return (
    <span className="badge" title="Room built from known ground-truth geometry, bundled for evaluation">
      Reference site
    </span>
  );
}

export function EstimateBadge() {
  return (
    <span className="badge violet" title="Results come from Monte Carlo simulation on the measured twin">
      Simulated result
    </span>
  );
}

const VERDICT_CLASS: Record<VerdictLabel, string> = { Cleared: "green", "Cleared with changes": "amber", "Not cleared": "red" };

export function VerdictBadge({ label, size }: { label: VerdictLabel; size?: "lg" }) {
  return (
    <span className={`badge ${VERDICT_CLASS[label]} ${size ?? ""}`}>
      <Icon name={label === "Cleared" ? "check" : label === "Not cleared" ? "x" : "alert"} size={13} stroke={2.4} />
      {label}
    </span>
  );
}

export function Spinner() {
  return <span className="spinner" />;
}

export function Progress({ value, tone }: { value: number; tone?: "green" }) {
  return (
    <div className={`progress ${tone ?? ""}`}>
      <i style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }} />
    </div>
  );
}

const STAGE_LABEL: Record<string, string> = {
  candidates: "Candidate robots",
  feasibility: "Route feasibility",
  simulation: "Monte Carlo batches",
  investigation: "Investigation",
  optimizer: "Layout changes",
  verdicts: "Verdicts",
  report: "Clearance Report",
};

export function StageList({ stages }: { stages: Record<string, StageStatus> }) {
  return (
    <div className="stack-sm">
      {Object.entries(stages).map(([name, s]) => (
        <div key={name} className="row" style={{ gap: 12 }}>
          <span style={{ width: 18, display: "grid", placeItems: "center" }}>
            {s.state === "done" ? (
              <span style={{ color: "var(--green)" }}>
                <Icon name="check" size={15} stroke={2.4} />
              </span>
            ) : s.state === "running" ? (
              <Spinner />
            ) : s.state === "failed" ? (
              <span style={{ color: "var(--red)" }}>
                <Icon name="x" size={15} stroke={2.4} />
              </span>
            ) : (
              <span className="dot" style={{ color: "var(--border-strong)" }} />
            )}
          </span>
          <span style={{ width: 170 }} className={s.state === "pending" ? "muted" : ""}>
            {STAGE_LABEL[name] ?? name}
          </span>
          <div style={{ flex: 1, minWidth: 80 }}>
            <Progress value={s.progress ?? (s.state === "done" ? 1 : 0)} tone={s.state === "done" ? "green" : undefined} />
          </div>
          <span className="small muted num" style={{ width: 150, textAlign: "right" }}>
            {s.message ?? (s.state === "running" ? `${Math.round((s.progress ?? 0) * 100)}%` : s.state)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function QR({ text, size = 160 }: { text: string; size?: number }) {
  const [src, setSrc] = useState<string>();
  useEffect(() => {
    QRCode.toDataURL(text, { margin: 1, width: size * 2, errorCorrectionLevel: "M" }).then(setSrc, () => setSrc(undefined));
  }, [text, size]);
  return src ? <img src={src} width={size} height={size} alt={`QR code for ${text}`} style={{ borderRadius: 8, background: "#fff", padding: 6 }} /> : <div className="skeleton" style={{ width: size, height: size }} />;
}

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label={title}>
        <div className="card-head">
          <h3>{title}</h3>
          <button className="btn ghost sm icon" onClick={onClose} aria-label="Close">
            <Icon name="x" />
          </button>
        </div>
        <div className="card-body">{children}</div>
        {footer && (
          <div className="card-body row" style={{ justifyContent: "flex-end", borderTop: "1px solid var(--border)" }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="drawer" role="dialog">
      <div className="card-head">
        <h3>{title}</h3>
        <button className="btn ghost sm icon" onClick={onClose} aria-label="Close">
          <Icon name="x" />
        </button>
      </div>
      <div className="drawer-body">{children}</div>
    </div>
  );
}

interface Toast {
  id: number;
  text: string;
  err?: boolean;
}
const ToastCtx = createContext<(text: string, err?: boolean) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastHost({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const n = useRef(0);
  const push = useCallback((text: string, err?: boolean) => {
    const id = ++n.current;
    setItems((x) => [...x, { id, text, err }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 3200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-host">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.err ? "err" : ""}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function ErrorBox({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="error-box row between">
      <span>{error.message}</span>
      {onRetry && (
        <button className="btn sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="empty row" style={{ justifyContent: "center" }}>
      <Spinner /> {label}
    </div>
  );
}

export function NumberField({ label, value, onChange, suffix, step = 1, min, hint }: { label: string; value: number; onChange: (v: number) => void; suffix?: string; step?: number; min?: number; hint?: string }) {
  return (
    <label className="field">
      <span>{label}</span>
      <div className="input-group">
        <input className="input num" type="number" step={step} min={min} value={Number.isFinite(value) ? value : ""} onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))} style={suffix ? undefined : { borderRadius: "var(--radius-sm)" }} />
        {suffix && <span className="suffix">{suffix}</span>}
      </div>
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}
