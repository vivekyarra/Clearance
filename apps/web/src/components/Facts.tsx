import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { formatFactValue } from "../lib/facts";
import type { Citation, Fact, FactOrigin } from "../types";
import { Icon } from "./Icon";

export interface FactActions {
  /** returns a label + handler for the primary action of this fact, or null */
  primary(f: Fact): { label: string; run: () => void; href?: string } | null;
}

interface Ctx {
  facts: Map<string, Fact>;
  citations: Citation[];
  actions?: FactActions;
}

const FactCtx = createContext<Ctx>({ facts: new Map(), citations: [] });

export function FactProvider({ facts, citations, actions, children }: { facts: Fact[]; citations?: Citation[]; actions?: FactActions; children: ReactNode }) {
  return <FactCtx.Provider value={{ facts: new Map(facts.map((f) => [f.fact_id, f])), citations: citations ?? [], actions }}>{children}</FactCtx.Provider>;
}

export const useFacts = () => useContext(FactCtx);

const ORIGIN_LABEL: Record<FactOrigin["type"], string> = {
  sim_batch: "Simulation batch",
  measurement: "Measured on the twin",
  source_url: "Cited source",
  user_input: "Your input",
  formula: "Formula",
  investigation: "Investigation step",
};

const ORIGIN_ICON: Record<FactOrigin["type"], string> = {
  sim_batch: "play",
  measurement: "ruler",
  source_url: "link",
  user_input: "doc",
  formula: "bolt",
  investigation: "eye",
};

export function FactChips({ ids }: { ids: string[] }) {
  return (
    <>
      {ids.map((id) => (
        <FactChip key={id} id={id} />
      ))}
    </>
  );
}

export function FactChip({ id }: { id: string }) {
  const { facts } = useFacts();
  const f = facts.get(id);
  const [open, setOpen] = useState<DOMRect | null>(null);
  const ref = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        ref={ref}
        type="button"
        className={`fact ${f ? f.origin.type : "missing"}`}
        title={f ? `${f.label}: ${formatFactValue(f)}` : `${id}: not in the facts pack`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(open ? null : ref.current!.getBoundingClientRect());
        }}
      >
        {f && <Icon name={ORIGIN_ICON[f.origin.type]} size={9} stroke={2.6} />}
        {id}
      </button>
      {open && <FactPopover id={id} anchor={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function FactPopover({ id, anchor, onClose }: { id: string; anchor: DOMRect; onClose: () => void }) {
  const { facts, citations, actions } = useFacts();
  const f = facts.get(id);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => !box.current?.contains(e.target as Node) && onClose();
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const s = () => onClose();
    setTimeout(() => document.addEventListener("mousedown", h));
    window.addEventListener("keydown", k);
    window.addEventListener("scroll", s, true);
    return () => {
      document.removeEventListener("mousedown", h);
      window.removeEventListener("keydown", k);
      window.removeEventListener("scroll", s, true);
    };
  }, [onClose]);
  const left = Math.min(window.innerWidth - 336, Math.max(8, anchor.left - 20));
  const below = anchor.bottom + 230 < window.innerHeight;
  const style = below ? { left, top: anchor.bottom + 6 } : { left, bottom: window.innerHeight - anchor.top + 6 };
  const cite = f?.origin.type === "source_url" ? citations.find((c) => c.url === f.origin.ref) : undefined;
  const act = f ? actions?.primary(f) : null;
  return createPortal(
    <div className="popover" ref={box} style={style}>
      {!f ? (
        <div className="error-box">
          <code>{id}</code> is not in this report's facts pack.
        </div>
      ) : (
        <div className="stack-sm">
          <div className="row between">
            <span className={`fact ${f.origin.type}`} style={{ cursor: "default" }}>
              {f.fact_id}
            </span>
            <span className="small muted">{ORIGIN_LABEL[f.origin.type]}</span>
          </div>
          <div className="v">{formatFactValue(f)}</div>
          <div>{f.label}</div>
          <div className="small muted" style={{ wordBreak: "break-all" }}>
            <code>{f.origin.ref}</code>
          </div>
          {cite && (
            <div className="note">
              {cite.quote && <div style={{ fontStyle: "italic", marginBottom: 4 }}>"{cite.quote}"</div>}
              <div>Retrieved {cite.retrieved_at ? new Date(cite.retrieved_at).toLocaleDateString(undefined, { dateStyle: "medium" }) : "date not recorded"}</div>
            </div>
          )}
          {act &&
            (act.href ? (
              <a className="btn sm primary" href={act.href} target="_blank" rel="noreferrer" onClick={onClose}>
                {act.label} <Icon name="external" size={13} />
              </a>
            ) : (
              <button
                className="btn sm primary"
                onClick={() => {
                  onClose();
                  act.run();
                }}
              >
                {act.label}
              </button>
            ))}
        </div>
      )}
    </div>,
    document.body,
  );
}
