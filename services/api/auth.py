"""Bearer-token authorization for the Clearance API.

Principals (who a bearer token speaks for):
  operator  CLEARANCE_OPERATOR_TOKEN: every write route (sites, twins, Clearance checks, catalog review,
            live sessions). Generated into <data>/.operator_token on first local start when unset.
  join      short-lived, site-scoped token carried by the site QR code (`token` query item). Phones use it
            to register captures on that site and to open or join its coach session.
  capture   per-capture device token returned by POST /sites/{id}/captures. It outlives the join token so
            a phone can resume uploads and complete its own capture, and nothing else.
  live      per-session token returned by POST /live; opens the session WebSocket (joystick / coach links).
Upload PUTs are authorized by the signed token in their URL (store.presign_put), not by a bearer token.
Read routes stay public so the sample and reference sites can be browsed without a token.
"""
from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from typing import Optional

from services.api.security import TokenError, TokenSigner

MIN_OPERATOR_TOKEN_LEN = 16


@dataclass(frozen=True)
class Principal:
    kind: str                       # operator | join | capture | live
    site_id: Optional[str] = None
    capture_id: Optional[str] = None
    session_id: Optional[str] = None

    @property
    def is_operator(self) -> bool:
        return self.kind == "operator"


OPERATOR = Principal("operator")


class Auth:
    def __init__(self, operator_token: str, signer: TokenSigner, *, join_ttl_s: float, capture_ttl_s: float,
                 live_ttl_s: float):
        if not operator_token or len(operator_token) < MIN_OPERATOR_TOKEN_LEN:
            raise ValueError(f"operator token must be at least {MIN_OPERATOR_TOKEN_LEN} characters")
        self._operator = operator_token.encode("utf-8")
        self.signer = signer
        self.join_ttl_s = join_ttl_s
        self.capture_ttl_s = capture_ttl_s
        self.live_ttl_s = live_ttl_s

    # ---- issue
    def join_token(self, site_id: str) -> tuple[str, int]:
        return self.signer.sign({"t": "join", "s": site_id}, self.join_ttl_s), int(time.time() + self.join_ttl_s)

    def capture_token(self, capture_id: str, site_id: str) -> str:
        return self.signer.sign({"t": "cap", "cid": capture_id, "s": site_id}, self.capture_ttl_s)

    def live_token(self, session_id: str, site_id: str) -> str:
        return self.signer.sign({"t": "live", "sid": session_id, "s": site_id}, self.live_ttl_s)

    # ---- resolve
    def resolve(self, token: Optional[str]) -> Optional[Principal]:
        if not token or len(token) > 4096:
            return None
        if hmac.compare_digest(token.encode("utf-8", "replace"), self._operator):
            return OPERATOR
        try:
            p = self.signer.verify(token)
        except TokenError:
            return None
        kind, site = p.get("t"), p.get("s")
        if not isinstance(site, str):
            return None
        if kind == "join":
            return Principal("join", site_id=site)
        if kind == "cap" and isinstance(p.get("cid"), str):
            return Principal("capture", site_id=site, capture_id=p["cid"])
        if kind == "live" and isinstance(p.get("sid"), str):
            return Principal("live", site_id=site, session_id=p["sid"])
        return None  # upload tokens and anything else are not bearer credentials


def bearer_from_headers(headers) -> Optional[str]:
    h = headers.get("authorization") or ""
    scheme, _, value = h.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None
