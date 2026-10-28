"""Generate the AprilTag (tag36h11) reference image bundled in the Clearance iOS app.

The PNG is cropped exactly to the tag's outer black border (8x8 cells for tag36h11 with a
1-cell border): this is the square whose edge length is `marker.size_m` (default 0.160 m).
ARKit's ARReferenceImage.physicalWidth refers to the whole image, so the app passes size_m
directly. This is also the `markerLength` convention of cv2.aruco used by the observer PWA.

Usage (from repo root):
  python apps/ios/tools/make_marker.py                 # writes the bundled reference PNG
  python apps/ios/tools/make_marker.py --printable out.png --size-m 0.16 --dpi 300
The script self-checks every image it writes by re-detecting the tag with cv2.aruco.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "Clearance" / "Resources" / "tag36h11_id0.png"
CELLS = 8  # 6x6 data + 1-cell black border on each side


def make_tag(tag_id: int = 0, cell_px: int = 100) -> np.ndarray:
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    img = cv2.aruco.generateImageMarker(d, tag_id, CELLS * cell_px, borderBits=1)
    assert img.shape == (CELLS * cell_px, CELLS * cell_px)
    return img


def detect_ids(img: np.ndarray) -> list[int]:
    """Detect on a copy padded with white (detector needs a quiet zone)."""
    pad = max(8, img.shape[0] // 8)
    padded = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    det = cv2.aruco.ArucoDetector(d, cv2.aruco.DetectorParameters())
    _, ids, _ = det.detectMarkers(padded)
    return [] if ids is None else [int(i) for i in ids.ravel()]


def make_printable(tag_id: int, size_m: float, dpi: int) -> np.ndarray:
    """Tag at true size for printing at `dpi`, with a 1-cell white quiet zone and a caption."""
    px = int(round(size_m / 0.0254 * dpi))
    cell = max(1, px // CELLS)
    tag = cv2.resize(make_tag(tag_id, 50), (px, px), interpolation=cv2.INTER_NEAREST)
    margin = cell * 2
    caption_h = max(40, dpi // 4)
    page = np.full((px + 2 * margin + caption_h, px + 2 * margin), 255, np.uint8)
    page[margin:margin + px, margin:margin + px] = tag
    text = f"tag36h11 id{tag_id}  black square = {size_m * 100:.1f} cm (measure after printing)"
    scale = caption_h / 90.0
    cv2.putText(page, text, (margin, margin + px + int(caption_h * 0.7)), cv2.FONT_HERSHEY_SIMPLEX,
                scale, 0, max(1, int(scale * 2)), cv2.LINE_AA)
    return page


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, default=0)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--cell-px", type=int, default=100)
    ap.add_argument("--printable", type=Path, default=None)
    ap.add_argument("--size-m", type=float, default=0.160)
    ap.add_argument("--dpi", type=int, default=300)
    a = ap.parse_args()

    tag = make_tag(a.id, a.cell_px)
    ids = detect_ids(tag)
    if ids != [a.id]:
        raise SystemExit(f"self-check failed: detected {ids}, expected [{a.id}]")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(a.out), tag)
    print(f"wrote {a.out} ({tag.shape[1]}x{tag.shape[0]}), self-check ids={ids}")

    if a.printable:
        page = make_printable(a.id, a.size_m, a.dpi)
        if a.id not in detect_ids(page):
            raise SystemExit("self-check failed on printable page")
        a.printable.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(a.printable), page)
        print(f"wrote {a.printable} ({page.shape[1]}x{page.shape[0]} px at {a.dpi} dpi)")


if __name__ == "__main__":
    main()
