"""direct fetch (pages.fetch_text) only reaches public http(s) hosts, re-checks every redirect, caps the body."""
import httpx
import pytest

from agents.catalog_builder import pages
from agents.catalog_builder.pages import UnsafeURLError, check_public_url, fetch_text

PUBLIC = {"vendor.example": ["93.184.216.34"], "cdn.example": ["2606:2800:220:1:248:1893:25c8:1946"],
          "intranet.example": ["10.0.0.5"], "rebind.example": ["93.184.216.34", "127.0.0.1"]}


def resolver(host, port):
    if host in PUBLIC:
        return PUBLIC[host]
    import ipaddress
    try:
        ipaddress.ip_address(host.strip("[]"))
        return [host.strip("[]")]
    except ValueError:
        raise OSError("no such host")


class RecordedTransport(httpx.BaseTransport):
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def handle_request(self, request):
        self.seen.append(str(request.url))
        status, headers, body = self.routes[str(request.url)]
        return httpx.Response(status, headers=headers, content=body, request=request)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://vendor.example/x", "gopher://vendor.example/", "javascript:alert(1)",
    "http://127.0.0.1/", "http://localhost.invalid/", "http://10.0.0.5/", "http://192.168.1.1/",
    "http://172.16.0.1/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/", "http://[fe80::1]/",
    "http://[::ffff:127.0.0.1]/", "http://0.0.0.0/", "http://224.0.0.1/", "http://100.64.0.1/",
    "http://intranet.example/", "http://rebind.example/", "http://user:pw@vendor.example/", "http:///nohost",
])
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeURLError):
        check_public_url(url, resolver=resolver)


@pytest.mark.parametrize("url", ["https://vendor.example/spec", "http://cdn.example/a.pdf", "http://93.184.216.34/"])
def test_public_urls_allowed(url):
    check_public_url(url, resolver=resolver)


def test_redirect_to_private_address_is_blocked():
    t = RecordedTransport({
        "https://vendor.example/spec": (302, {"location": "http://169.254.169.254/latest/meta-data/"}, b""),
    })
    with pytest.raises(UnsafeURLError):
        fetch_text("https://vendor.example/spec", transport=t, resolver=resolver)
    assert t.seen == ["https://vendor.example/spec"]  # the metadata address was never requested


def test_redirect_to_other_scheme_is_blocked():
    t = RecordedTransport({"https://vendor.example/a": (301, {"location": "file:///etc/passwd"}, b"")})
    with pytest.raises(UnsafeURLError):
        fetch_text("https://vendor.example/a", transport=t, resolver=resolver)


def test_redirect_chain_is_limited():
    routes = {f"https://vendor.example/{i}": (302, {"location": f"/{i + 1}"}, b"") for i in range(10)}
    with pytest.raises(UnsafeURLError, match="redirects"):
        fetch_text("https://vendor.example/0", transport=RecordedTransport(routes), resolver=resolver)


def test_relative_redirect_followed_and_text_returned():
    t = RecordedTransport({
        "https://vendor.example/old": (301, {"location": "/new"}, b""),
        "https://vendor.example/new": (200, {"content-type": "text/html"}, b"<p>Max speed</p><p>1.2 m/s</p>"),
    })
    final, text = fetch_text("https://vendor.example/old", transport=t, resolver=resolver)
    assert final == "https://vendor.example/new" and "1.2 m/s" in text


def test_body_cap(monkeypatch):
    big = b"<p>" + b"a" * 2000 + b"</p>"
    t = RecordedTransport({"https://vendor.example/big": (200, {"content-type": "text/html"}, big)})
    with pytest.raises(UnsafeURLError, match="exceeds|bytes"):
        fetch_text("https://vendor.example/big", transport=t, resolver=resolver, max_bytes=1000)
    t = RecordedTransport({"https://vendor.example/big": (200, {"content-length": "999999999"}, b"x")})
    with pytest.raises(UnsafeURLError):
        fetch_text("https://vendor.example/big", transport=t, resolver=resolver)
    assert pages.MAX_BODY_BYTES == 5 * 1024 * 1024


def test_build_direct_fetch_records_refusal(monkeypatch):
    from agents.catalog_builder import build

    class NoTavily:
        def extract(self, urls):
            return {"results": [], "failed_results": []}

    spec = build.build_spec("Acme", "R1", "B_amr", tavily=NoTavily(), urls=["http://127.0.0.1:8000/admin"],
                            direct_fetch=True, save_pages=False)
    notes = build.catalog.read_notes(spec)
    assert any("direct fetch failed" in c and "UnsafeURLError" in c for c in notes["comments"])
    assert spec.max_speed_mps is None
