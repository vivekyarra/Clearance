"""Page text helpers: HTML/PDF -> plain text, and a direct HTTP fetch used when Tavily Extract is
unavailable (or by the seed-research scripts). Pure stdlib for HTML; pypdf (optional) for PDFs."""
from __future__ import annotations

import io
import re
from html import unescape
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "header", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "dl", "main", "aside", "nav", "form", "hr",
    "thead", "tbody", "figcaption", "blockquote", "pre",
}
_CELL_TAGS = {"td", "th"}
_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "iframe", "head"}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 ClearanceCatalogBuilder/0.1"
)


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in _CELL_TAGS:
            self.parts.append(" | ")

    def handle_startendtag(self, tag, attrs):
        if tag in ("br", "hr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Visible text of an HTML document, one block per line, table cells separated by ' | '."""
    p = _TextParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed markup: fall back to tag stripping
        return _collapse(unescape(re.sub(r"<[^>]+>", " ", html)))
    return _collapse("".join(p.parts))


def _collapse(text: str) -> str:
    lines = [re.sub(r"[ \t\r\f\v ]+", " ", ln).strip() for ln in text.split("\n")]
    out, blank = [], False
    for ln in lines:
        if ln.strip("| ") == "":
            if not blank and out:
                out.append("")
            blank = True
            continue
        out.append(ln)
        blank = False
    return "\n".join(out).strip()


def pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - depends on env
        raise RuntimeError("pypdf is required to read PDF datasheets (pip install pypdf)") from e
    reader = PdfReader(io.BytesIO(data))
    return _collapse("\n".join((pg.extract_text() or "") for pg in reader.pages))


def bytes_to_text(data: bytes, content_type: str = "", url: str = "") -> str:
    if "pdf" in content_type.lower() or url.lower().split("?")[0].endswith(".pdf") or data[:5] == b"%PDF-":
        return pdf_to_text(data)
    enc = "utf-8"
    m = re.search(r"charset=([\w-]+)", content_type or "")
    if m:
        enc = m.group(1)
    return html_to_text(data.decode(enc, errors="replace"))


MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5


class UnsafeURLError(ValueError):
    """The URL is not a public http(s) address (URLs can come from search results)."""


def _resolve(host: str, port: int) -> list[str]:
    import socket

    return sorted({ai[4][0] for ai in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})


def check_public_url(url: str, *, resolver=None) -> None:
    """Raise UnsafeURLError unless `url` is http/https and every address its host resolves to is a public
    (global unicast) IP: no private, loopback, link-local, multicast, reserved or unspecified ranges."""
    import ipaddress
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise UnsafeURLError(f"malformed URL {url!r}") from e
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"scheme {parts.scheme!r} is not allowed")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no host")
    if parts.username or parts.password:
        raise UnsafeURLError("credentials in URLs are not allowed")
    try:
        addrs = (resolver or _resolve)(host, port)
    except OSError as e:
        raise UnsafeURLError(f"cannot resolve {host!r}") from e
    if not addrs:
        raise UnsafeURLError(f"{host!r} resolves to no address")
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%", 1)[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise UnsafeURLError(f"{host!r} resolves to non-public address {ip}")


def fetch_text(url: str, *, timeout: float = 30.0, max_bytes: int = MAX_BODY_BYTES,
               transport=None, resolver=None) -> tuple[str, str]:
    """GET a public page and return (final_url, text). Raises on HTTP errors and on unsafe URLs.
    Redirects are followed by hand (at most MAX_REDIRECTS), re-checking every hop; the body is capped."""
    import httpx

    t = httpx.Timeout(timeout, connect=min(timeout, 10.0))
    with httpx.Client(follow_redirects=False, timeout=t, headers={"User-Agent": USER_AGENT},
                      transport=transport) as c:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check_public_url(current, resolver=resolver)
            with c.stream("GET", current) as r:
                if r.is_redirect:
                    loc = r.headers.get("location")
                    if not loc:
                        raise UnsafeURLError("redirect without a location")
                    current = str(r.url.join(loc))
                    continue
                r.raise_for_status()
                declared = r.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise UnsafeURLError(f"response is {declared} bytes (cap {max_bytes})")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise UnsafeURLError(f"response exceeds {max_bytes} bytes")
                return str(r.url), bytes_to_text(bytes(buf), r.headers.get("content-type", ""), str(r.url))
        raise UnsafeURLError(f"more than {MAX_REDIRECTS} redirects")


# ---------- page text cache (outside the repo; lets the validator re-run later) ----------
def cache_dir():
    import os
    from pathlib import Path

    d = Path(os.environ.get("CLEARANCE_CATALOG_PAGES", Path.home() / ".cache" / "pg" / "catalog_pages")) / "by_url"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _url_key(url: str) -> str:
    import hashlib

    slug = re.sub(r"[^a-z0-9]+", "_", url.lower().split("://", 1)[-1]).strip("_")[:80]
    return f"{slug}_{hashlib.sha1(url.encode()).hexdigest()[:8]}"


def cache_put(url: str, text: str, retrieved_at: str) -> str:
    import json

    d = cache_dir()
    key = _url_key(url)
    (d / f"{key}.txt").write_text(text, encoding="utf-8")
    idx_p = d / "index.json"
    idx = json.loads(idx_p.read_text(encoding="utf-8")) if idx_p.exists() else {}
    idx[url] = {"file": f"{key}.txt", "retrieved_at": retrieved_at, "chars": len(text)}
    idx_p.write_text(json.dumps(idx, indent=2), encoding="utf-8")
    return str(d / f"{key}.txt")


def cache_get(url: str) -> str | None:
    p = cache_dir() / f"{_url_key(url)}.txt"
    return p.read_text(encoding="utf-8") if p.exists() else None
