import json

import jsonschema

from clearance_core.schemas import LegFeasibility, Verdict, VerdictCheck
from scripts.export_schemas import OUT, main, render


def test_committed_schemas_match_models():
    assert main(["--check"]) == 0


def test_schemas_are_valid_and_use_gap_vocabulary():
    files = render()
    for text in files.values():
        jsonschema.Draft202012Validator.check_schema(json.loads(text))
    feas = files["feasibility.schema.json"]
    assert "narrowest_gap_m" in feas and "required_gap_m" in feas and "clearance_m" not in feas


def test_verdict_instance_validates():
    v = Verdict(robot_id="r", label="Cleared", main_reason="meets every rule as-is",
                as_is=[VerdictCheck(rule="x", passed=True)])
    jsonschema.validate(v.model_dump(mode="json"), json.loads((OUT / "verdict.schema.json").read_text()))
    assert LegFeasibility(from_wp="A", to_wp="B", reachable=True).narrowest_gap_m is None
