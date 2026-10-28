#!/usr/bin/env bash
# Shared settings for infra/nebius/*.sh. Source it; do not run it.
set -euo pipefail
NEBIUS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$NEBIUS_DIR/../.." && pwd)"
if [[ -f "$NEBIUS_DIR/config.env" ]]; then
  set -a; source "$NEBIUS_DIR/config.env"; set +a
fi
NEBIUS_REGION="${NEBIUS_REGION:-eu-north1}"
CLEARANCE_IMAGE_TAG="${CLEARANCE_IMAGE_TAG:-$(cat "$REPO_ROOT/VERSION" 2>/dev/null || echo latest)}"
REGISTRY_HOST="cr.${NEBIUS_REGION}.nebius.cloud"

need() { command -v "$1" >/dev/null 2>&1 || { echo "error: '$1' is required ($2)" >&2; exit 1; }; }
require_var() { [[ -n "${!1:-}" ]] || { echo "error: set $1 in infra/nebius/config.env" >&2; exit 1; }; }

image_ref() {  # image_ref clearance-api -> cr.<region>.nebius.cloud/<registry>/clearance-api:<tag>
  require_var CLEARANCE_REGISTRY_PATH
  echo "${REGISTRY_HOST}/${CLEARANCE_REGISTRY_PATH}/$1:${CLEARANCE_IMAGE_TAG}"
}

subnet_id() {
  if [[ -n "${NEBIUS_SUBNET_ID:-}" ]]; then echo "$NEBIUS_SUBNET_ID"; return; fi
  nebius vpc subnet list --format jsonpath='{.items[0].metadata.id}'
}

endpoint_url() {  # managed https:// URL of an endpoint (docs: serverless/tutorials/deploy-model)
  nebius ai endpoint get-by-name --name "$1" --format json \
    | jq -r '.status.public_endpoints[] | select(startswith("https://"))' | head -1
}

new_token() { openssl rand -hex 32; }
