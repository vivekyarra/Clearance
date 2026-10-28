"""Re-run the verbatim-quote validator for catalog files against cached page text.

  python -m agents.catalog_builder.revalidate [robot_id ...] [--write]
Page text comes from the page cache (CLEARANCE_CATALOG_PAGES, default ~/.cache/pg/catalog_pages/by_url),
filled by build_spec or by research fetches. With --write, notes.agent_quote_validated is updated.
"""
from __future__ import annotations

import argparse
import sys

from agents.catalog_builder import catalog, pages
from agents.catalog_builder.build import mark_quote_validation, revalidate


def page_texts_for(spec) -> dict[str, str]:
    out = {}
    for src in spec.fields_sources.values():
        if src.url not in out:
            t = pages.cache_get(src.url)
            if t is not None:
                out[src.url] = t
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.catalog_builder.revalidate")
    ap.add_argument("robot_ids", nargs="*")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    specs = [catalog.load_spec(r) for r in a.robot_ids] or catalog.load_catalog(verified_only=False)
    bad = 0
    for spec in specs:
        texts = page_texts_for(spec)
        res = revalidate(spec, texts)
        fails = {f: why for f, (ok, why) in res.items() if not ok}
        bad += bool(fails)
        print(f"{spec.robot_id:28s} {len(res) - len(fails)}/{len(res)} sources pass" + (f"  {fails}" if fails else ""))
        if a.write:
            mark_quote_validation(spec, texts)
            catalog.save_spec(spec)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
