/**
 * Occupancy grid helpers, mirroring clearance_core/grid.py exactly.
 * PNG values: 0 = occupied, 255 = free, 127 = unknown. Row 0 = max y (north up).
 * Cell (r, c) center = (origin_x + (c + 0.5) * res, origin_y + (H - r - 0.5) * res).
 */
import type { Occupancy, XY } from "../types";

export const OCC = 0;
export const UNKNOWN = 127;
export const FREE = 255;

export interface OccGrid {
  data: Uint8Array; // row-major H x W
  width: number;
  height: number;
  res: number;
  origin: XY;
}

/** grid.py Grid.world_to_cell: c = floor((x-ox)/res); r = h-1-floor((y-oy)/res) */
export function worldToCell(g: Pick<OccGrid, "height" | "res" | "origin">, x: number, y: number): [number, number] {
  const c = Math.floor((x - g.origin[0]) / g.res);
  const r = g.height - 1 - Math.floor((y - g.origin[1]) / g.res);
  return [r, c];
}

/** grid.py Grid.cell_to_world: x = ox+(c+.5)res ; y = oy+(h-r-.5)res */
export function cellToWorld(g: Pick<OccGrid, "height" | "res" | "origin">, r: number, c: number): XY {
  return [g.origin[0] + (c + 0.5) * g.res, g.origin[1] + (g.height - r - 0.5) * g.res];
}

/**
 * Continuous "image pixel" coordinates used for drawing: cell (r,c) spans px in [c, c+1], py in [r, r+1].
 * world -> px:  px = (x-ox)/res,  py = H - (y-oy)/res
 */
export function worldToPx(g: Pick<OccGrid, "height" | "res" | "origin">, x: number, y: number): XY {
  return [(x - g.origin[0]) / g.res, g.height - (y - g.origin[1]) / g.res];
}

export function pxToWorld(g: Pick<OccGrid, "height" | "res" | "origin">, px: number, py: number): XY {
  return [g.origin[0] + px * g.res, g.origin[1] + (g.height - py) * g.res];
}

export function inBounds(g: Pick<OccGrid, "width" | "height">, r: number, c: number): boolean {
  return r >= 0 && c >= 0 && r < g.height && c < g.width;
}

export function cellValue(g: OccGrid, r: number, c: number): number {
  return inBounds(g, r, c) ? g.data[r * g.width + c] : UNKNOWN;
}

/** Unknown is treated as blocked in sim, like Grid.blocked(). */
export function isBlocked(g: OccGrid, r: number, c: number): boolean {
  return cellValue(g, r, c) !== FREE;
}

export function worldBounds(g: OccGrid): { minX: number; minY: number; maxX: number; maxY: number } {
  return {
    minX: g.origin[0],
    minY: g.origin[1],
    maxX: g.origin[0] + g.width * g.res,
    maxY: g.origin[1] + g.height * g.res,
  };
}

/** Quantise a raw PNG luminance to the three legal values (PNG resampling/antialias tolerant). */
export function quantise(v: number): number {
  if (v < 64) return OCC;
  if (v > 191) return FREE;
  return UNKNOWN;
}

export function gridFromRGBA(rgba: Uint8ClampedArray, width: number, height: number, occ: Occupancy): OccGrid {
  const data = new Uint8Array(width * height);
  for (let i = 0; i < width * height; i++) data[i] = quantise(rgba[i * 4]);
  return { data, width, height, res: occ.resolution_m ?? 0.025, origin: occ.origin_xy ?? [0, 0] };
}

/** Decode an occupancy PNG (browser only). */
export async function loadOccupancyPng(url: string, occ: Occupancy): Promise<OccGrid> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`occupancy ${res.status}`);
  const blob = await res.blob();
  const bmp = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  canvas.width = bmp.width;
  canvas.height = bmp.height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(bmp, 0, 0);
  const img = ctx.getImageData(0, 0, bmp.width, bmp.height);
  return gridFromRGBA(img.data, bmp.width, bmp.height, occ);
}

export interface GridPalette {
  occ: [number, number, number];
  free: [number, number, number];
  unknown: [number, number, number];
}

/** Render to an RGBA buffer (one pixel per cell, same orientation as the PNG). */
export function gridToRGBA(g: OccGrid, pal: GridPalette): Uint8ClampedArray {
  const out = new Uint8ClampedArray(g.width * g.height * 4);
  for (let i = 0; i < g.data.length; i++) {
    const v = g.data[i];
    const col = v === OCC ? pal.occ : v === FREE ? pal.free : pal.unknown;
    out[i * 4] = col[0];
    out[i * 4 + 1] = col[1];
    out[i * 4 + 2] = col[2];
    out[i * 4 + 3] = 255;
  }
  return out;
}
