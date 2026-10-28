/** Minimal .npy (v1/v2/v3) reader for little-endian float32/float64/uint8 C-order arrays. */
export interface NpyArray {
  shape: number[];
  data: Float32Array;
}

export function parseNpy(buf: ArrayBuffer): NpyArray {
  const u8 = new Uint8Array(buf);
  if (u8[0] !== 0x93 || String.fromCharCode(...u8.slice(1, 6)) !== "NUMPY") throw new Error("not an npy file");
  const major = u8[6];
  const dv = new DataView(buf);
  const headerLen = major === 1 ? dv.getUint16(8, true) : dv.getUint32(8, true);
  const headerStart = major === 1 ? 10 : 12;
  const header = new TextDecoder("latin1").decode(u8.slice(headerStart, headerStart + headerLen));
  const descr = /'descr':\s*'([^']+)'/.exec(header)?.[1];
  const fortran = /'fortran_order':\s*True/.test(header);
  const shapeStr = /'shape':\s*\(([^)]*)\)/.exec(header)?.[1] ?? "";
  const shape = shapeStr
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map(Number);
  if (fortran) throw new Error("fortran-order npy not supported");
  const offset = headerStart + headerLen;
  const n = shape.reduce((a, b) => a * b, 1);
  let data: Float32Array;
  switch (descr) {
    case "<f4":
      data = new Float32Array(buf.slice(offset, offset + n * 4));
      break;
    case "<f8": {
      const f64 = new Float64Array(buf.slice(offset, offset + n * 8));
      data = Float32Array.from(f64);
      break;
    }
    case "|u1":
    case "<u1":
      data = Float32Array.from(u8.subarray(offset, offset + n));
      break;
    default:
      throw new Error(`unsupported npy dtype ${descr}`);
  }
  return { shape, data };
}
