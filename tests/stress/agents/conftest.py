import pytest

import agents_stress_helpers  # noqa: F401  (puts tests/unit/investigate on sys.path for inv_doubles)
from inv_doubles import data_dir, scene  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch, data_dir):  # every test gets its own storage dir and usage DB
    for k in ("CLEARANCE_LLM_MODE", "NEBIUS_API_KEY", "CLEARANCE_VISION_BASE_URL", "CLEARANCE_VISION_API_KEY",
              "CLEARANCE_MODELS_CONFIG"):
        monkeypatch.delenv(k, raising=False)
