"""Storage-key validation and signed upload tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path

_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._\-]{0,127}$")
_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]{0,127}$")
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


class BadKey(ValueError):
    pass


class TokenError(ValueError):
    pass


def safe_key(key: str, *, max_len: int = 512) -> str:
    """Accept only relative POSIX keys made of plain segments (no '..', no hidden files, no drive letters)."""
    if not isinstance(key, str) or not key or len(key) > max_len:
        raise BadKey("key must be a non-empty string")
    if "\\" in key or "\x00" in key or ":" in key or key.startswith("/"):
        raise BadKey(f"illegal characters in key: {key!r}")
    for seg in key.split("/"):
        if seg in ("", ".", "..") or not _SEGMENT.match(seg):
            raise BadKey(f"illegal path segment in key: {key!r}")
        if seg.split(".")[0].upper() in _WIN_RESERVED:
            raise BadKey(f"reserved name in key: {key!r}")
    return key


def safe_id(value: str, what: str = "id") -> str:
    if not isinstance(value, str) or not _ID.match(value):
        raise BadKey(f"invalid {what}: {value!r}")
    return value


def _resolve(p: Path) -> Path:
    """Path.resolve() without the Windows extended-length prefix: while another request os.replace()s a
    file in the same directory, realpath() can fall back to a `\\\\?\\` path, which then no longer
    compares equal to the storage root and looked like an escape (500 on racing uploads)."""
    r = p.resolve()
    s = str(r)
    prefix = "\\\\?\\"
    if s.startswith(prefix) and not s.startswith(prefix + "UNC\\"):
        r = Path(s[len(prefix):])
    return r


def resolve_under(root: Path, key: str) -> Path:
    safe_key(key)
    root_r = _resolve(Path(root))
    joined = root_r / key
    # safe_key rules out "..": only a symlinked directory or final file can point outside the root
    p = _resolve(joined.parent) / joined.name
    if p.is_symlink():
        p = _resolve(p)
    if p != root_r and root_r not in p.parents:
        raise BadKey(f"key escapes storage root: {key!r}")
    return p


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class TokenSigner:
    """Compact HMAC-SHA256 tokens: base64url(json payload) + '.' + base64url(mac)."""

    def __init__(self, secret: bytes):
        if not secret:
            raise ValueError("empty secret")
        self._secret = secret

    def _mac(self, body: str) -> str:
        return _b64e(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())

    def sign(self, payload: dict, ttl_s: float) -> str:
        payload = dict(payload, exp=int(time.time() + ttl_s))
        body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        return f"{body}.{self._mac(body)}"

    def verify(self, token: str) -> dict:
        if not isinstance(token, str) or token.count(".") != 1 or len(token) > 4096:
            raise TokenError("malformed token")
        body, mac = token.split(".")
        if not hmac.compare_digest(mac, self._mac(body)):
            raise TokenError("bad signature")
        try:
            payload = json.loads(_b64d(body))
        except Exception as e:  # noqa: BLE001
            raise TokenError("malformed payload") from e
        if not isinstance(payload, dict) or not isinstance(payload.get("exp"), int):
            raise TokenError("malformed payload")
        if payload["exp"] < time.time():
            raise TokenError("token expired")
        return payload
