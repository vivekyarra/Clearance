import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Modal } from "../components/ui";
import { operatorToken, setOperatorToken } from "./auth";

/**
 * Operator token settings: stored in this browser (localStorage) and sent as `Authorization: Bearer ...` on
 * every API request. Opens by itself when a write route answers 401, from `?operator=1`, or with Ctrl+Shift+O.
 */
function OperatorTokenDialog({ reason, onDone }: { reason?: string; onDone: (token: string | null) => void }) {
  const [value, setValue] = useState(operatorToken() ?? "");
  const [show, setShow] = useState(false);
  const save = () => {
    setOperatorToken(value);
    onDone(value.trim() || null);
  };
  const clear = () => {
    setOperatorToken(null);
    setValue("");
    onDone(null);
  };
  return (
    <Modal
      title="Operator token"
      onClose={() => onDone(null)}
      footer={
        <>
          {operatorToken() && (
            <button className="btn ghost" onClick={clear}>
              Remove from this browser
            </button>
          )}
          <button className="btn" onClick={() => onDone(null)}>
            Cancel
          </button>
          <button className="btn primary" onClick={save} disabled={!value.trim()}>
            Save
          </button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {reason && <div className="small">{reason}</div>}
        <div className="small muted">
          Creating sites, editing twins, running Clearance checks, reviewing robots and starting live sessions need the operator token. A local API prints it once at startup and saves it to{" "}
          <span className="mono">data/.operator_token</span>; a hosted deployment uses <span className="mono">CLEARANCE_OPERATOR_TOKEN</span>. Browsing sites and reports needs no token.
        </div>
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="small muted">Token</span>
          <span style={{ display: "flex", gap: 8 }}>
            <input
              className="input mono"
              type={show ? "text" : "password"}
              autoComplete="off"
              spellCheck={false}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && value.trim() && save()}
              style={{ flex: 1 }}
              autoFocus
            />
            <button className="btn sm" onClick={() => setShow((s) => !s)}>
              {show ? "Hide" : "Show"}
            </button>
          </span>
        </label>
      </div>
    </Modal>
  );
}

let pending: Promise<string | null> | null = null;

/** Show the dialog (once at a time); resolves with the saved token, or null when cancelled. */
export function openOperatorTokenDialog(reason?: string): Promise<string | null> {
  if (pending) return pending;
  pending = new Promise((resolve) => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const done = (token: string | null) => {
      root.unmount();
      host.remove();
      pending = null;
      resolve(token);
    };
    root.render(<OperatorTokenDialog reason={reason} onDone={done} />);
  });
  return pending;
}

let installed = false;

/** `?operator=1` and Ctrl+Shift+O open the dialog on any console page. */
export function installOperatorTokenEntry(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  window.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && (e.key === "O" || e.key === "o")) {
      e.preventDefault();
      void openOperatorTokenDialog();
    }
  });
  if (new URLSearchParams(location.search).get("operator") === "1") {
    if (document.readyState === "complete") setTimeout(() => void openOperatorTokenDialog(), 0);
    else window.addEventListener("load", () => void openOperatorTokenDialog(), { once: true });
  }
}
