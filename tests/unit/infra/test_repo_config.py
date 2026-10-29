"""Repository-level configuration owned by infra: Dockerfiles, requirements, CI, compose, wording rule."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]

INFRA_FILES = [
    "LICENSE", "THIRD_PARTY_LICENSES.md", "FEEDBACK.md", "CHANGELOG.md", "VERSION", ".gitignore",
    "pyproject.toml", "requirements.txt", "requirements-cloud.txt", "requirements-gpu.txt",
    "docs/VERIFIED.md", "docs/PRIVACY.md", "docs/ARCHITECTURE.md",
    "scripts/dev.ps1", "scripts/dev.sh", ".github/workflows/ci.yml",
]


def infra_paths() -> list[Path]:
    paths = [REPO / p for p in INFRA_FILES]
    paths += [p for p in (REPO / "infra").rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    paths += list((REPO / "tests" / "unit" / "infra").glob("*.py"))
    return paths


def test_license_is_apache():
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in text and "Version 2.0, January 2004" in text
    assert "END OF TERMS AND CONDITIONS" in text


def test_version_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", (REPO / "VERSION").read_text().strip())


def test_pyproject():
    d = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert d["project"]["name"] == "clearance"
    assert d["project"]["license"] == "Apache-2.0"
    assert d["tool"]["setuptools"]["dynamic"]["version"] == {"file": "VERSION"}


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-r"):
            name, sep, ver = line.partition("==")
            assert sep, f"unpinned requirement in {path.name}: {line}"
            pins[re.sub(r"\[.*\]", "", name).lower().replace("_", "-")] = ver
    return pins


def test_requirements_lean_and_pinned():
    base = _pins(REPO / "requirements.txt")
    for heavy in ("torch", "open3d", "gsplat", "transformers", "torchvision", "usd-core"):
        assert heavy not in base, f"{heavy} belongs in requirements-gpu.txt"
    for need in ("numpy", "fastapi", "pytest", "pydantic", "sqlalchemy", "opencv-python-headless", "pyyaml"):
        assert need in base
    gpu = _pins(REPO / "requirements-gpu.txt")
    assert "torch" in gpu and "gsplat" in gpu
    cloud = _pins(REPO / "requirements-cloud.txt")
    assert "boto3" in cloud and "psycopg" in cloud


def test_pyproject_pins_match_requirements():
    base = _pins(REPO / "requirements.txt")
    deps = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    for dep in deps:
        name, _, ver = dep.partition("==")
        assert base[name.lower().replace("_", "-")] == ver, dep


@pytest.mark.parametrize("dockerfile", ["Dockerfile.api", "Dockerfile.jobs-cpu", "Dockerfile.jobs-gpu"])
def test_dockerfile_copy_sources_exist(dockerfile):
    text = (REPO / "infra" / dockerfile).read_text(encoding="utf-8")
    for line in text.splitlines():
        m = re.match(r"\s*COPY\s+(?!--from)(.+)\s+\S+\s*$", line)
        if not m:
            continue
        for src in m.group(1).split():
            assert (REPO / src.rstrip("/")).exists(), f"{dockerfile}: COPY source {src} missing"
    assert (REPO / "infra" / f"{dockerfile}.dockerignore").is_file()
    assert "ENTRYPOINT" in text or "CMD" in text


def test_ci_workflow():
    wf = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as True
    assert "pull_request" in on and on["push"]["branches"] == ["main"]
    assert wf["concurrency"]["cancel-in-progress"] is True
    py, web = wf["jobs"]["python"], wf["jobs"]["web"]
    assert py["runs-on"] == web["runs-on"] == "ubuntu-latest"
    assert py["env"]["CLEARANCE_LLM_MODE"] == "offline" and py["env"]["CLEARANCE_TAVILY_MODE"] == "offline"
    steps = " ".join(str(s.get("run", "")) for s in py["steps"])
    assert "pip install -r requirements.txt" in steps and "pytest -q tests" in steps
    setup_py = [s for s in py["steps"] if "setup-python" in str(s.get("uses", ""))][0]
    assert setup_py["with"]["python-version"] == "3.11" and setup_py["with"]["cache"] == "pip"
    wsteps = " ".join(str(s.get("run", "")) for s in web["steps"])
    for cmd in ("npm ci", "npx tsc --noEmit", "npm test --if-present", "npm run build"):
        assert cmd in wsteps
    setup_node = [s for s in web["steps"] if "setup-node" in str(s.get("uses", ""))][0]
    assert str(setup_node["with"]["node-version"]) == "24"
    assert py["timeout-minutes"] <= 15 and web["timeout-minutes"] <= 15


def test_compose_parses():
    c = yaml.safe_load((REPO / "infra" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert c["services"]["api"]["build"]["dockerfile"] == "infra/Dockerfile.api"
    assert c["services"]["api"]["ports"] == ["8000:8000"]
    assert c["services"]["postgres"]["profiles"] == ["postgres"]
    assert c["services"]["minio"]["profiles"] == ["s3"]


def test_gitignore_covers_data_and_secrets():
    lines = set((REPO / ".gitignore").read_text(encoding="utf-8").splitlines())
    for entry in ("data/", "*.db", ".env", "node_modules/", "dist/", "__pycache__/", "*.safetensors",
                  "infra/nebius/config.env"):
        assert entry in lines, entry


def test_verified_md_keeps_live_section_marker():
    """scripts/verify_token_factory.py replaces the section that starts with '## Token Factory'."""
    md = (REPO / "docs" / "VERIFIED.md").read_text(encoding="utf-8")
    assert md.count("## Token Factory") == 1
    assert "\n## Token Factory\n" in md


# Words the project wording rule keeps out of everything we publish (assembled so this file passes too).
_BANNED = ["fa" + "ke", "mo" + "ck", "dum" + "my", "st" + "ub", "place" + "holder", "TO" + "DO", "FIX" + "ME",
           "ha" + "ck", "synthe" + "tic", "to" + "y", "half-" + "done", "not " + "implemented", "de" + "mo",
           "proto" + "type", "work" + "around", "experi" + "mental", "bro" + "ken", "tempo" + "rary", "see" + "ded",
           "be" + "ta", "limit" + "ation"]
_OLD = "pro" + "ving"


@pytest.mark.parametrize("path", infra_paths(), ids=lambda p: p.relative_to(REPO).as_posix())
def test_wording_rule(path):
    if path.name == "LICENSE":
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    low = text.lower()
    assert _OLD not in low, f"{path}: old product name"
    for w in _BANNED:
        if w == "ha" + "ck":   # the event name ("...athon") is allowed
            pat = r"\bha" + r"ck(?!athon)\w*"
        elif w == "to" + "y":
            pat = r"\bto" + r"ys?\b"
        elif w == "de" + "mo":  # the phrase "... video" is allowed
            pat = r"\bde" + r"mo(?! video)\w*"
        elif w == "be" + "ta":
            pat = r"\bbe" + r"tas?\b"
        else:
            pat = rf"\b{re.escape(w.lower())}\w*"
        hits = [m.group(0) for m in re.finditer(pat, low)]
        assert not hits, f"{path}: contains {hits[:3]}"
    assert not re.search(r"^#+\s*limit" + r"ations", low, re.M), f"{path}: name that section 'Scope'"
