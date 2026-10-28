"""Export JSON Schemas for the frozen data contracts (clearance_core/schemas.py) into schemas/.

    python scripts/export_schemas.py          # write schemas/*.schema.json
    python scripts/export_schemas.py --check  # exit 1 if schemas/ is out of date
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from clearance_core import schemas as S

OUT = Path(__file__).resolve().parent.parent / "schemas"
MODELS = {
    "capture": S.Capture, "pose_record": S.PoseRecord, "twin": S.Twin, "robot_spec": S.RobotSpec,
    "sim_batch": S.SimBatch, "feasibility": S.Feasibility, "fact": S.Fact, "investigation": S.Investigation,
    "verdict": S.Verdict, "traffic_model": S.TrafficModel,
}


def render() -> dict[str, str]:
    out = {}
    for name, model in MODELS.items():
        schema = model.model_json_schema(by_alias=True)
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"clearance/{name}", **schema}
        out[f"{name}.schema.json"] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    files = render()
    if args.check:
        stale = [n for n, t in files.items() if not (OUT / n).is_file() or (OUT / n).read_text(encoding="utf-8") != t]
        if stale:
            print("schemas out of date:", ", ".join(stale), "- run python scripts/export_schemas.py")
            return 1
        return 0
    OUT.mkdir(exist_ok=True)
    for n, t in files.items():
        (OUT / n).write_text(t, encoding="utf-8", newline="\n")
    print(f"wrote {len(files)} schemas to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
