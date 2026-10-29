"""Catalog builder: Tavily Search -> Tavily Extract -> Nemotron Nano extraction ->
code unit conversion -> verbatim-quote validator -> RobotSpec (verified_by_human=False).

CLI:
  python -m agents.catalog_builder.build --vendor Pudu --model BellaBot --class A_service
      [--urls URL ...] [--max-urls 4] [--direct-fetch] [--out PATH] [--print] [--force]
Offline (CLEARANCE_TAVILY_MODE=offline) it reads recorded responses in recorded/tavily/.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from urllib.parse import urlparse

from clearance_core.schemas import FieldSource, Price, RobotSpec

from agents.catalog_builder import catalog, pages
from agents.catalog_builder.extract import VerifiedField, extract_raw, verify_raw
from agents.catalog_builder.tavily_client import TavilyClient
from agents.catalog_builder.validate import (check_field, check_footprint, check_price,  # noqa: F401
                                             validate_field)  # validate_field: contract re-export

PRICE_ON_REQUEST = "price on request"
REVIEW_PENDING = "awaiting review"


# ---------------------------------------------------------------- search
def _vendor_token(vendor: str) -> str:
    return "".join(ch for ch in vendor.lower() if ch.isalnum())


def vendor_hosted(url: str, vendor: str) -> bool:
    """'Keenon Robotics' -> keenon.com, 'Pudu Robotics' -> pudurobotics.com, 'MiR' -> mir-robots.com."""
    host = urlparse(url).netloc.lower().replace("-", "").replace(".", "")
    words = [w for w in _tokens(vendor) if len(w) >= 3 and w not in ("robotics", "robots", "robot", "inc", "motors")]
    tok = _vendor_token(vendor)
    return bool(tok and tok in host) or any(w in host for w in words)


# words that turn a model name into a sibling product ("Servi" -> "Servi Plus", "BellaBot" -> "BellaBot Pro")
VARIANT_WORDS = {"plus", "pro", "max", "mini", "lite", "q", "s", "x", "ii", "iii", "flex", "premium", "mk2",
                 "gen2", "next"}


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _is_variant(tok: str | None) -> bool:
    return tok is not None and (tok in VARIANT_WORDS or tok.isdigit())


def model_match(text: str, model: str) -> int:
    """0 = names this model, 1 = no mention, 2 = names only a sibling model (e.g. '... Plus')."""
    toks, mt = _tokens(text), _tokens(model)
    if not mt or not toks:
        return 1
    joined, n = "".join(mt), len(mt)
    exact = sibling = False
    for i, t in enumerate(toks):
        nxt = toks[i + n] if i + n < len(toks) else None
        if toks[i:i + n] == mt:
            if _is_variant(nxt):
                sibling = True
            else:
                exact = True
        if n > 1 and t == joined:
            exact = exact or not _is_variant(toks[i + 1] if i + 1 < len(toks) else None)
        elif t.startswith(joined) and t != joined:
            if t[len(joined):] in VARIANT_WORDS:
                sibling = True
        # the distinctive token alone, e.g. "/product/t8" for "DINERBOT T8" or "/100" for "OTTO 100"
        last = mt[-1]
        if n > 1 and t == last and any(c.isdigit() for c in last) and len(last) >= 2:
            exact = exact or not _is_variant(toks[i + 1] if i + 1 < len(toks) else None)
    # the base model without our variant word, e.g. "/servi" when looking for "Servi Plus"
    base = list(mt)
    while len(base) > 1 and _is_variant(base[-1]):
        base.pop()
    if not exact and len(base) < n:
        k = len(base)
        if any(toks[i:i + k] == base and toks[i + k:i + n] != mt[k:] for i in range(len(toks))):
            sibling = True
    return 0 if exact else (2 if sibling else 1)


def page_rank(url: str, title: str, model: str) -> int:
    path = urlparse(url).path
    m_path, m_title = model_match(path, model), model_match(title, model)
    if m_path == 0 or (m_title == 0 and m_path != 2):
        return 0
    if m_path == 2 or m_title == 2:
        return 2
    return 1


def search_urls(tavily: TavilyClient, vendor: str, model: str, max_urls: int = 4) -> list[str]:
    """Rank: pages naming this exact model first, then vendor-hosted pages, then search score.
    Pages that name only a sibling model (e.g. 'Servi Plus' when looking for 'Servi') come last."""
    queries = [f"{vendor} {model} specifications", f"{vendor} {model} datasheet pdf"]
    seen: dict[str, dict] = {}
    for q in queries:
        for r in tavily.search(q, max_results=8).get("results", []):
            u = r.get("url")
            if u and u not in seen:
                seen[u] = r
    def rank(item):
        u, r = item
        return (page_rank(u, r.get("title") or "", model), 0 if vendor_hosted(u, vendor) else 1,
                -float(r.get("score") or 0.0))

    return [u for u, _ in sorted(seen.items(), key=rank)[:max_urls]]


# ---------------------------------------------------------------- assembly
def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=0.01, abs_tol=1e-6)
    return a == b


def assemble_spec(vendor: str, model: str, robot_class: str, per_page: list[dict], *,
                  robot_id: str | None = None, comments: list[str] | None = None,
                  page_texts: dict[str, str] | None = None) -> RobotSpec:
    """per_page: [{"url", "verified": {field: VerifiedField}, "rejected": [...], "price": dict|None,
    "extractor": str}] in source-priority order. First verified value per field wins."""
    chosen: dict[str, VerifiedField] = {}
    conflicts, rejected, extractors = [], [], []
    for pg in per_page:
        extractors.append(pg.get("extractor", ""))
        rejected.extend(pg.get("rejected", []))
        for f, vf in pg.get("verified", {}).items():
            if f not in chosen:
                chosen[f] = vf
            elif not _same(chosen[f].value, vf.value):
                conflicts.append({"field": f, "kept": chosen[f].value, "kept_url": chosen[f].url,
                                  "other": vf.value, "other_url": vf.url})

    spec = RobotSpec(robot_id=robot_id or catalog.robot_id_for(vendor, model), robot_class=robot_class,
                     vendor=vendor, model=model)
    sources: dict[str, FieldSource] = {}
    for f, vf in chosen.items():
        setattr(spec, f, vf.value)
        sources[f] = FieldSource(url=vf.url, quote=vf.quote, retrieved_at=vf.retrieved_at)
    if "footprint_polygon_m" in chosen:
        spec.footprint_approx = True

    price_pages = [pg["price"] for pg in per_page if pg.get("price")]
    public = next((p for p in price_pages if p.get("is_public") and p.get("kind", "purchase") == "purchase"), None)
    public = public or next((p for p in price_pages if p.get("is_public")), None)
    lease = next((p for p in price_pages if p.get("kind") == "lease"), None)
    on_req = next((p for p in price_pages if not p.get("is_public") and p.get("kind") != "lease"), None)
    if public:
        note = "public list price" if public.get("kind", "purchase") == "purchase" else             "starting price ('from'); the configuration may cost more"
        spec.price = Price(value=public["value"], currency=public.get("currency", "USD"), is_public=True,
                           note=note, source_url=public["url"])
        sources["price"] = FieldSource(url=public["url"], quote=public["quote"], retrieved_at=public["retrieved_at"])
    elif lease:
        per = f"/{lease['lease_period']}" if lease.get("lease_period") else ""
        spec.price = Price(value=None, currency=lease.get("currency", "USD"), is_public=False,
                           note=f"lease {lease.get('currency', 'USD')} {lease['lease_value']:g}{per}; not a purchase price",
                           source_url=lease["url"])
        sources["price"] = FieldSource(url=lease["url"], quote=lease["quote"], retrieved_at=lease["retrieved_at"])
    elif on_req:
        spec.price = Price(value=None, is_public=False, note=PRICE_ON_REQUEST, source_url=on_req["url"])
        sources["price"] = FieldSource(url=on_req["url"], quote=on_req["quote"], retrieved_at=on_req["retrieved_at"])
    else:
        spec.price = Price(value=None, is_public=False, note=PRICE_ON_REQUEST, source_url=None)
    spec.fields_sources = sources
    spec.verified_by_human = False

    notes = {
        "agent_quote_validated": False,
        "review_status": REVIEW_PENDING,
        "extractor": sorted(set(e for e in extractors if e)),
        "sources_considered": [pg["url"] for pg in per_page],
        "null_fields": [f for f in catalog.NUMERIC_FIELDS + ["footprint_polygon_m"] if getattr(spec, f) is None],
        "rejected_fields": rejected,
        "conflicts": conflicts,
        "comments": list(comments or []),
    }
    if "in_place_rotation" not in chosen:
        notes["comments"].append("drive and in_place_rotation are schema defaults; awaiting review")
    if spec.footprint_approx:
        notes["comments"].append("footprint is the vendor's L x W bounding rectangle centred on the body")
    catalog.write_notes(spec, notes)
    if page_texts is not None:
        mark_quote_validation(spec, page_texts)
    return spec


# ---------------------------------------------------------------- re-validation
def footprint_size(poly) -> tuple[float, float]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return float(f"{max(xs) - min(xs):.6g}"), float(f"{max(ys) - min(ys):.6g}")


def revalidate(spec: RobotSpec, page_texts: dict[str, str]) -> dict[str, tuple[bool, str]]:
    """Re-run the validator for every sourced field against stored page texts {url: text}."""
    out: dict[str, tuple[bool, str]] = {}
    for f, src in spec.fields_sources.items():
        text = page_texts.get(src.url)
        if text is None:
            out[f] = (False, "page text unavailable")
            continue
        if f == "footprint_polygon_m":
            if spec.footprint_polygon_m is None:
                out[f] = (False, "source without value")
                continue
            L, W = footprint_size(spec.footprint_polygon_m)
            out[f] = check_footprint(L, W, src.quote, text)
        elif f == "price":
            if spec.price.value is not None:
                ok, why, tok = check_price(spec.price.value, src.quote, text)
                if ok and tok["kind"] == "lease":
                    ok, why = False, "a lease rate is stored as a purchase price"
                out[f] = (ok, why)
            else:
                out[f] = check_field(True, src.quote, text)
        elif f == "in_place_rotation":
            out[f] = check_field(spec.in_place_rotation, src.quote, text, field=f)
        else:
            v = getattr(spec, f, None)
            out[f] = check_field(v, src.quote, text, field=f if f in catalog.NUMERIC_FIELDS or
                                 f == "min_turn_radius_m" else None)
    return out


def mark_quote_validation(spec: RobotSpec, page_texts: dict[str, str]) -> bool:
    res = revalidate(spec, page_texts)
    ok = bool(res) and all(r[0] for r in res.values()) and not catalog.unsourced_fields(spec)
    notes = catalog.read_notes(spec)
    notes["agent_quote_validated"] = ok
    failed = {f: why for f, (good, why) in res.items() if not good}
    if failed:
        notes["validation_failures"] = failed
    else:
        notes.pop("validation_failures", None)
    catalog.write_notes(spec, notes)
    return ok


# ---------------------------------------------------------------- pipeline
def build_spec(vendor: str, model: str, robot_class: str, *, tavily: TavilyClient | None = None, llm=None,
               urls: list[str] | None = None, max_urls: int = 4, direct_fetch: bool = False,
               save_pages: bool = True, llm_mode: str | None = None) -> RobotSpec:
    """Contract entry point. Returns an unreviewed RobotSpec (verified_by_human=False)."""
    tavily = tavily or TavilyClient()
    urls = list(urls) if urls else search_urls(tavily, vendor, model, max_urls=max_urls)
    retrieved_at = catalog.now_iso()
    texts: dict[str, str] = {}
    comments: list[str] = []
    if urls:
        ext = tavily.extract(urls)
        for r in ext.get("results", []):
            if r.get("raw_content"):
                texts[r["url"]] = r["raw_content"]
        for f in ext.get("failed_results", []):
            comments.append(f"extract failed for {f.get('url')}: {f.get('error')}")
    if direct_fetch:
        for u in urls:
            if u not in texts:
                try:
                    _, texts[u] = pages.fetch_text(u)
                except Exception as e:
                    comments.append(f"direct fetch failed for {u}: {type(e).__name__}")
    per_page = []
    for u in urls:
        if u not in texts:
            continue
        if save_pages:
            try:
                pages.cache_put(u, texts[u], retrieved_at)
            except OSError:
                pass
        raw = extract_raw(texts[u], vendor, model, u, llm=llm, mode=llm_mode)
        verified, rejected, price = verify_raw(raw, texts[u], u, retrieved_at)
        per_page.append({"url": u, "verified": verified, "rejected": rejected, "price": price,
                         "extractor": raw.get("_extractor", "")})
    if not per_page:
        comments.append("no page text available; every field is null")
    return assemble_spec(vendor, model, robot_class, per_page, comments=comments, page_texts=texts)


def _utf8_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.catalog_builder.build", description=__doc__.split("\n")[0])
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--class", dest="robot_class", required=True, choices=["A_service", "B_amr"])
    ap.add_argument("--urls", nargs="*", default=None, help="skip search and extract these URLs")
    ap.add_argument("--max-urls", type=int, default=4)
    ap.add_argument("--direct-fetch", action="store_true", help="HTTP GET pages Tavily could not extract")
    ap.add_argument("--out", default=None, help="output path (default catalog/robots/<id>.json)")
    ap.add_argument("--print", dest="print_only", action="store_true", help="print JSON, do not write")
    ap.add_argument("--force", action="store_true", help="overwrite an existing catalog file")
    a = ap.parse_args(argv)
    _utf8_stdout()

    spec = build_spec(a.vendor, a.model, a.robot_class, urls=a.urls, max_urls=a.max_urls, direct_fetch=a.direct_fetch)
    if a.print_only:
        print(json.dumps(spec.model_dump(mode="json", by_alias=True), indent=2, ensure_ascii=False))
        return 0
    from pathlib import Path

    target = Path(a.out) if a.out else catalog.spec_path(spec.robot_id)
    if target.exists() and not a.force:
        print(f"{target} exists; use --force to overwrite (or --out PATH)", file=sys.stderr)
        return 2
    p = catalog.save_spec(spec, a.out)
    n = sum(1 for f in catalog.NUMERIC_FIELDS + ["footprint_polygon_m"] if getattr(spec, f) is not None)
    print(f"wrote {p}  ({n} sourced fields, verified_by_human=false)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
