const P: Record<string, string> = {
  cursor: "M5 3l14 7-6 2-2 6z",
  box: "M4 6h16v12H4z",
  circle: "M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z",
  polygon: "M5 8l7-5 7 5-3 11H8z",
  pin: "M12 21s-6-5.7-6-11a6 6 0 0 1 12 0c0 5.3-6 11-6 11zM12 8a2 2 0 1 0 0 4a2 2 0 1 0 0-4z",
  ruler: "M3 17L17 3l4 4L7 21zM7 13l2 2M10 10l2 2M13 7l2 2",
  hand: "M8 11V5a1.5 1.5 0 0 1 3 0v5M11 10V4a1.5 1.5 0 0 1 3 0v6M14 10V6a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-1a6 6 0 0 1-5-3l-2-4a1.5 1.5 0 0 1 2.6-1.5L8 14",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  undo: "M9 14L4 9l5-5M4 9h11a5 5 0 0 1 0 10h-4",
  redo: "M15 14l5-5-5-5M20 9H9a5 5 0 0 0 0 10h4",
  save: "M5 3h11l3 3v15H5zM8 3v5h8V3M8 21v-7h8v7",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14",
  play: "M7 4l13 8-13 8z",
  pause: "M7 4h4v16H7zM13 4h4v16h-4z",
  sun: "M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8zM12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  moon: "M20 14A8 8 0 0 1 10 4a8 8 0 1 0 10 10z",
  phone: "M7 2h10v20H7zM11 18h2",
  cube: "M12 2l9 5v10l-9 5-9-5V7zM12 12l9-5M12 12v10M12 12L3 7",
  map: "M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14",
  flag: "M5 21V4h11l-2 4 2 4H5",
  alert: "M12 3l10 18H2zM12 10v5M12 18v.5",
  check: "M5 12l5 5L20 7",
  x: "M6 6l12 12M18 6L6 18",
  chevronLeft: "M15 6l-6 6 6 6",
  chevronRight: "M9 6l6 6-6 6",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
  qr: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 18h2v2h-2zM14 18h2v2h-2zM18 14h2v2h-2z",
  camera: "M4 7h3l2-3h6l2 3h3v13H4zM12 10a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7z",
  robot: "M6 8h12v10H6zM12 4v4M9 12h.01M15 12h.01M9 15h6M3 11v4M21 11v4",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
  rotate: "M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z",
  joystick: "M12 3a3 3 0 1 0 0 6a3 3 0 1 0 0-6zM12 9v7M6 16h12v4H6z",
  scan: "M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4M4 12h16",
  ar: "M12 2l8 4.5v9L12 20l-8-4.5v-9zM12 11l8-4.5M12 11v9M12 11L4 6.5",
  doc: "M6 2h9l4 4v16H6zM14 2v5h5M9 13h7M9 17h7",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
  hazard: "M12 3l10 18H2zM12 10v5M12 18v.5",
};

export function Icon({ name, size = 16, stroke = 1.8, className }: { name: keyof typeof P | string; size?: number; stroke?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={P[name] ?? P.circle} />
    </svg>
  );
}
