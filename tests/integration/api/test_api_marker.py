"""Marker PDF prints the AprilTag at true size and decodes back; the QR PNG carries the join payload."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import cv2
import numpy as np
import pytest
from api_testkit import create_site

pdfium = pytest.importorskip("pypdfium2")

DPI = 200


def _render(pdf: bytes) -> tuple[np.ndarray, tuple[float, float]]:
    doc = pdfium.PdfDocument(pdf)
    assert len(doc) == 1
    page = doc[0]
    size = page.get_size()
    img = np.array(page.render(scale=DPI / 72).to_pil().convert("L"))
    return img, size


def _detect(img):
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11),
                                  cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(img)
    return corners, ids


def test_marker_pdf_true_size_and_decodes(client):
    site = create_site(client, "Marker Cafe")
    r = client.get(f"/sites/{site['site_id']}/marker.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-") and r.content.rstrip().endswith(b"%%EOF")
    img, (w_pt, h_pt) = _render(r.content)
    assert abs(w_pt - 595.276) < 0.05 and abs(h_pt - 841.89) < 0.05  # A4
    corners, ids = _detect(img)
    assert ids is not None and ids.flatten().tolist() == [0]
    q = corners[0].reshape(4, 2)
    edges_px = [np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)]
    edges_mm = [e / DPI * 25.4 for e in edges_px]
    assert all(abs(e - 160.0) < 1.0 for e in edges_mm), edges_mm
    # tag is upright and centred horizontally on the page
    cx_mm = q[:, 0].mean() / DPI * 25.4
    assert abs(cx_mm - 105.0) < 1.0


def test_marker_custom_size_and_bounds(client):
    r = client.post("/sites", json={"name": "Small", "marker_size_m": 0.12})
    sid = r.json()["site_id"]
    assert r.json()["marker"]["size_m"] == 0.12
    img, _ = _render(client.get(f"/sites/{sid}/marker.pdf").content)
    corners, ids = _detect(img)
    edge_mm = np.linalg.norm(corners[0][0][0] - corners[0][0][1]) / DPI * 25.4
    assert abs(edge_mm - 120.0) < 1.0
    assert client.post("/sites", json={"name": "Huge", "marker_size_m": 0.25}).status_code == 422


def _decode_qr(img) -> str:
    """OpenCV's default QR reader misses about 3% of these payloads; its ArUco-based reader (and a 2x upscale) reads
    the rest. Over 400 random join URLs this decodes every one."""
    for det in (cv2.QRCodeDetector(), cv2.QRCodeDetectorAruco()):
        for im in (img, cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)):
            text, _, _ = det.detectAndDecode(im)
            if text:
                return text
    return ""


def test_qr_png_decodes_to_site_payload(client):
    site = create_site(client, "QR Cafe")
    r = client.get(f"/sites/{site['site_id']}/qr.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_GRAYSCALE)
    text = _decode_qr(img)
    # the QR encodes the join URL; the API base, site name and (for the operator) a join token ride along
    url = urlsplit(text)
    assert url.path == f"/join/{site['site_id']}" == urlsplit(site["join_url"]).path
    q = parse_qs(url.query)
    assert q["api"] == ["http://testserver"] and q["name"] == ["QR Cafe"] and set(q) == {"api", "name", "token"}
    assert parse_qs(urlsplit(site["join_url"]).query)["token"] == [site["qr"]["token"]]
    # each QR request issues a fresh short-lived join token, so the QR and the create response differ only there
    assert {k: v for k, v in q.items() if k != "token"} == {
        k: v for k, v in parse_qs(urlsplit(site["qr"]["join_url"]).query).items() if k != "token"}
    payload = site["qr"]
    assert payload["site_id"] == site["site_id"] and payload["api_base"] == "http://testserver"
    assert payload["marker"] == {"family": "tag36h11", "id": 0, "size_m": 0.16}


def test_public_base_urls(make_client):
    c = make_client(public_api_base="http://192.168.1.20:8000", public_web_base="http://192.168.1.20:5173")
    site = create_site(c)
    assert site["qr"]["api_base"] == "http://192.168.1.20:8000"
    assert site["join_url"] == (f"http://192.168.1.20:5173/join/{site['site_id']}"
                                "?api=http%3A%2F%2F192.168.1.20%3A8000&name=Corner%20Cafe"
                                f"&token={site['qr']['token']}")
    assert site["marker_pdf_url"].startswith("http://192.168.1.20:8000/")
