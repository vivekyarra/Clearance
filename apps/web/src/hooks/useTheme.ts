import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

function initial(): Theme {
  try {
    const t = localStorage.getItem("clearance.theme");
    if (t === "light" || t === "dark") return t;
  } catch {
    /* ignore */
  }
  return matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyInitialTheme() {
  document.documentElement.dataset.theme = initial();
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initial);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("clearance.theme", theme);
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new Event("clearance-theme"));
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

/** Re-render on theme switch (canvas views read CSS variables). */
export function useThemeTick(): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const f = () => setN((x) => x + 1);
    window.addEventListener("clearance-theme", f);
    return () => window.removeEventListener("clearance-theme", f);
  }, []);
  return n;
}

export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const v = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(v.slice(0, 6), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
