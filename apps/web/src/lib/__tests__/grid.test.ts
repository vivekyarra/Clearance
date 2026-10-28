import { describe, expect, it } from "vitest";
import { FREE, OCC, UNKNOWN, cellToWorld, gridFromRGBA, pxToWorld, quantise, worldToCell, worldToPx } from "../grid";

// Expected values follow clearance_core/grid.py:
//   c = floor((x-ox)/res); r = H-1-floor((y-oy)/res)
//   x = ox+(c+.5)res;     y = oy+(H-r-.5)res
const g = { height: 4, width: 6, res: 0.5, origin: [-1, 2] as [number, number] };

describe("grid coordinates match clearance_core/grid.py", () => {
  it("world_to_cell", () => {
    expect(worldToCell(g, -1, 2)).toEqual([3, 0]); // bottom-left corner -> last row
    expect(worldToCell(g, -0.75, 3.75)).toEqual([0, 0]); // top-left cell center
    expect(worldToCell(g, 1.74, 2.26)).toEqual([3, 5]);
    expect(worldToCell(g, -1.01, 2)).toEqual([3, -1]); // floor, not truncation, outside the grid
    expect(worldToCell(g, -1, 4.0)).toEqual([-1, 0]); // top edge belongs to the row above
  });
  it("cell_to_world returns cell centers and round-trips", () => {
    expect(cellToWorld(g, 0, 0)).toEqual([-0.75, 3.75]);
    expect(cellToWorld(g, 3, 5)).toEqual([1.75, 2.25]);
    for (let r = 0; r < g.height; r++)
      for (let c = 0; c < g.width; c++) {
        const [x, y] = cellToWorld(g, r, c);
        expect(worldToCell(g, x, y)).toEqual([r, c]);
      }
  });
  it("pixel space puts cell (r,c) in [c,c+1] x [r,r+1]", () => {
    const [x, y] = cellToWorld(g, 2, 3);
    expect(worldToPx(g, x, y)).toEqual([3.5, 2.5]);
    expect(pxToWorld(g, 3.5, 2.5)).toEqual([x, y]);
  });
  it("quantises PNG values to occupied / unknown / free", () => {
    expect([quantise(0), quantise(40), quantise(127), quantise(130), quantise(250), quantise(255)]).toEqual([OCC, OCC, UNKNOWN, UNKNOWN, FREE, FREE]);
    const rgba = new Uint8ClampedArray([0, 0, 0, 255, 127, 127, 127, 255, 255, 255, 255, 255, 200, 200, 200, 255]);
    const grid = gridFromRGBA(rgba, 2, 2, { uri: "x", resolution_m: 0.025, origin_xy: [1, 1] });
    expect(Array.from(grid.data)).toEqual([OCC, UNKNOWN, FREE, FREE]);
    expect(grid.res).toBe(0.025);
  });
});
