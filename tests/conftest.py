"""Test-wide settings: the suite is hermetic. It ignores local settings (.env, config/*.local.yaml), runs in offline mode and sees no provider
keys, so no test run spends Token Factory or Tavily credits and results match CI."""
import os

os.environ["CLEARANCE_LLM_MODE"] = "offline"
os.environ["CLEARANCE_TAVILY_MODE"] = "offline"
os.environ["CLEARANCE_SKIP_DOTENV"] = "1"
for _k in [k for k in os.environ if k.endswith("_API_KEY")] + ["CLEARANCE_VISION_BASE_URL"]:
    os.environ.pop(_k, None)

# Keep test artifacts (usage DB, sites, analyses) out of the real data directory.
import tempfile  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="clearance-tests-")
os.environ.setdefault("CLEARANCE_DATA", _TMP)
os.environ["CLEARANCE_USAGE_DB"] = os.path.join(_TMP, "usage.db")
