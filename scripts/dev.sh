#!/usr/bin/env bash
# Clearance developer tasks (Makefile equivalent) for macOS / Linux / Git Bash.
#   bash scripts/dev.sh <task>
# Tasks: setup | test | test-web | build-web | api | web | dev | verify-models | docker-build | compose-up | ci
# Environment: PYTHON (default: python3, or the venv at $CLEARANCE_VENV), offline mode unless keys are set.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -n "${CLEARANCE_VENV:-}" ]]; then
  if [[ -x "$CLEARANCE_VENV/bin/python" ]]; then PYTHON="$CLEARANCE_VENV/bin/python"; else PYTHON="$CLEARANCE_VENV/Scripts/python.exe"; fi
fi
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python
export PYTHONPATH="$ROOT"
export CLEARANCE_LLM_MODE="${CLEARANCE_LLM_MODE:-offline}"
export CLEARANCE_TAVILY_MODE="${CLEARANCE_TAVILY_MODE:-offline}"

task="${1:-help}"; shift || true
case "$task" in
  setup)
    "$PYTHON" -m pip install -r requirements.txt
    (cd apps/web && npm ci --no-audit --no-fund) ;;
  test)
    "$PYTHON" -m pytest -q tests "$@" ;;
  test-web)
    (cd apps/web && npx tsc --noEmit && npm test --if-present -- --passWithNoTests) ;;
  build-web)
    (cd apps/web && npm run build) ;;
  api)
    "$PYTHON" -m uvicorn services.api.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload --ws-max-size 131072 "$@" ;;
  web)
    (cd apps/web && npm run dev) ;;
  dev)
    "$PYTHON" -m uvicorn services.api.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload --ws-max-size 131072 &
    api_pid=$!
    trap 'kill $api_pid 2>/dev/null || true' EXIT
    (cd apps/web && npm run dev) ;;
  verify-models)
    "$PYTHON" scripts/verify_token_factory.py "$@" ;;
  docker-build)
    for t in api jobs-cpu jobs-gpu; do
      docker build --platform linux/amd64 -f "infra/Dockerfile.$t" -t "clearance-$t:local" .
    done ;;
  compose-up)
    docker compose -f infra/docker-compose.yml up --build "$@" ;;
  ci)
    "$PYTHON" -m pytest -q tests
    (cd apps/web && npm ci --no-audit --no-fund && npx tsc --noEmit \
      && npm test --if-present -- --passWithNoTests && npm run build) ;;
  help|*)
    sed -n '2,5p' "$0" ;;
esac
