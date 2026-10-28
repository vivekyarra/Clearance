"""Printable site marker (AprilTag 36h11) as a vector A4 PDF, and the site QR code PNG.

The PDF is written directly (vector rectangles + standard Helvetica), so the tag prints at exactly the
requested edge length when printed at 100 % / "Actual size". The tag edge is the outer edge of the black
border (8 x 8 cells for 36h11), matching the OpenCV/AprilTag marker-length convention used for PnP.
"""
from __future__ import annotations

import io
import zlib
from datetime import datetime, timezone

import numpy as np

A4_MM = (210.0, 297.0)
PT_PER_MM = 72.0 / 25.4
MAX_TAG_MM = 170.0
MIN_TAG_MM = 40.0


def tag_cells(tag_id: int = 0) -> np.ndarray:
    """8x8 boolean array, True = black cell, row 0 = top of the printed tag."""
    import cv2

    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    n = d.markerSize + 2
    img = cv2.aruco.generateImageMarker(d, int(tag_id), n, borderBits=1)
    return img < 128


def _esc(text: str) -> str:
    text = text.encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _Page:
    """Tiny PDF content-stream builder in millimetres with a top-left origin."""

    def __init__(self, w_mm: float, h_mm: float):
        self.w, self.h = w_mm, h_mm
        self.ops: list[str] = []

    def _x(self, mm: float) -> float:
        return mm * PT_PER_MM

    def _y(self, mm_from_top: float) -> float:
        return (self.h - mm_from_top) * PT_PER_MM

    def rect(self, x, y, w, h, *, fill=True, gray=0.0, line_w_mm=0.3):
        X, Y = self._x(x), self._y(y + h)
        W, H = self._x(w), h * PT_PER_MM
        if fill:
            self.ops.append(f"{gray:.3f} g {X:.4f} {Y:.4f} {W:.4f} {H:.4f} re f")
        else:
            self.ops.append(f"{gray:.3f} G {self._x(line_w_mm):.3f} w {X:.4f} {Y:.4f} {W:.4f} {H:.4f} re S")

    def line(self, x1, y1, x2, y2, *, line_w_mm=0.3):
        self.ops.append(f"0 G {self._x(line_w_mm):.3f} w {self._x(x1):.4f} {self._y(y1):.4f} m "
                        f"{self._x(x2):.4f} {self._y(y2):.4f} l S")

    def text(self, x, y_baseline, s, *, size=10.0, bold=False):
        font = "F2" if bold else "F1"
        self.ops.append(f"0 g BT /{font} {size:.2f} Tf {self._x(x):.4f} {self._y(y_baseline):.4f} Td ({_esc(s)}) Tj ET")

    def stream(self) -> bytes:
        return "\n".join(self.ops).encode("latin-1")


def _write_pdf(page: _Page, title: str) -> bytes:
    content = zlib.compress(page.stream())
    w_pt, h_pt = page.w * PT_PER_MM, page.h * PT_PER_MM
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w_pt:.4f} {h_pt:.4f}] "
         f"/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>").encode(),
        b"<< /Length " + str(len(content)).encode() + b" /Filter /FlateDecode >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        f"<< /Title ({_esc(title)}) /Producer (Clearance API) >>".encode("latin-1"),
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def marker_pdf(site_id: str, site_name: str, *, size_m: float = 0.160, tag_id: int = 0) -> bytes:
    edge = float(size_m) * 1000.0
    if not (MIN_TAG_MM <= edge <= MAX_TAG_MM):
        raise ValueError(f"marker edge {edge:.0f} mm does not fit an A4 page ({MIN_TAG_MM:.0f}-{MAX_TAG_MM:.0f} mm)")
    W, H = A4_MM
    pg = _Page(W, H)
    left = 20.0

    pg.text(left, 17, "Clearance site marker", size=18, bold=True)
    pg.text(left, 24, f"Site: {site_name[:60]}  ({site_id})", size=10)
    pg.text(left, 30, f"AprilTag family 36h11, id {tag_id}. Black square edge = {edge:.0f} mm.", size=10)
    pg.text(left, 36, "Print at 100% / \"Actual size\". Switch off \"Fit to page\" and any scaling.", size=10, bold=True)

    # tag, centred, with a wide white quiet zone
    cells = tag_cells(tag_id)
    n = cells.shape[0]
    cell = edge / n
    x0, y0 = (W - edge) / 2.0, 44.0
    for r in range(n):
        c = 0
        while c < n:
            if cells[r, c]:
                c1 = c
                while c1 < n and cells[r, c1]:
                    c1 += 1
                pg.rect(x0 + c * cell, y0 + r * cell, (c1 - c) * cell, cell)
                c = c1
            else:
                c += 1
    y_tag_end = y0 + edge

    # dimension line under the tag
    yd = y_tag_end + 9.0
    pg.line(x0, yd, x0 + edge, yd, line_w_mm=0.25)
    pg.line(x0, yd - 2.5, x0, yd + 2.5, line_w_mm=0.25)
    pg.line(x0 + edge, yd - 2.5, x0 + edge, yd + 2.5, line_w_mm=0.25)
    pg.text(W / 2.0 - 7.0, yd - 1.2, f"{edge:.0f} mm", size=9, bold=True)

    # 100 mm scale bar, 10 mm segments
    yb, bar_h, bx = yd + 8.0, 4.0, (W - 100.0) / 2.0
    for i in range(10):
        if i % 2 == 0:
            pg.rect(bx + i * 10.0, yb, 10.0, bar_h)
    pg.rect(bx, yb, 100.0, bar_h, fill=False, line_w_mm=0.25)
    for i in range(0, 11):
        pg.line(bx + i * 10.0, yb + bar_h, bx + i * 10.0, yb + bar_h + 1.5, line_w_mm=0.2)
        label = str(i * 10)
        pg.text(bx + i * 10.0 - 0.9 * len(label), yb + bar_h + 5.0, label, size=6.5)
    pg.text(bx + 100.0 + 3.0, yb + bar_h - 0.5, "mm (scale bar = 100 mm)", size=7)

    yi = yb + bar_h + 13.0
    lines = [
        ("MEASURE ME before you start:", True),
        (f"1. Measure the black square's outer edge with a ruler. It must read {edge:.0f} mm.", False),
        ("2. Check the scale bar too: 0 to 100 must read exactly 100 mm.", False),
        ("3. If the edge differs, enter the measured length in the capture app before scanning.", False),
        ("4. Tape the sheet flat on the floor near the entrance. Keep it in place until every capture is uploaded.", False),
        ("5. Keep the white border around the black square visible and uncovered.", False),
    ]
    for i, (s, b) in enumerate(lines):
        pg.text(left, yi + i * 5.6, s, size=9.5 if not b else 10.5, bold=b)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pg.text(left, H - 8.0, f"Generated by Clearance API on {stamp}. The marker centre is the site origin.", size=7)
    return _write_pdf(pg, f"Clearance marker - {site_id}")


def qr_png(payload: str, *, box_size: int = 8) -> bytes:
    import qrcode  # type: ignore
    from qrcode.constants import ERROR_CORRECT_M  # type: ignore

    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=box_size, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
