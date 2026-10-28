#!/usr/bin/env bash
# Deploy Cosmos Reason 2 with vLLM as a GPU Nebius Serverless Endpoint and print the env the API needs.
#   bash infra/nebius/deploy_vision_endpoint.sh [--dry-run]
# Then set CLEARANCE_VISION_BASE_URL=<url>/v1 and CLEARANCE_VISION_API_KEY=<token> for the API.
# Stopped endpoints are not billed (docs: serverless/pricing-quotas); stop it between sessions.
source "$(dirname "$0")/common.sh"
source "$NEBIUS_DIR/endpoints/cosmos_reason2.env"
need nebius "curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash"
need jq "https://jqlang.org/"
require_var HF_TOKEN
DRY=()
[[ "${1:-}" == "--dry-run" ]] && DRY=(--dry-run)
TOKEN="${CLEARANCE_VISION_TOKEN:-$(new_token)}"
VLLM_ARGS="--model $VISION_MODEL_ID --served-model-name $VISION_MODEL_ID --host 0.0.0.0 --port $ENDPOINT_PORT"
VLLM_ARGS+=" --max-model-len $VLLM_MAX_MODEL_LEN --reasoning-parser qwen3"

nebius ai endpoint create \
  --name "$ENDPOINT_NAME" \
  --image "$VLLM_IMAGE" \
  --container-command "python3 -m vllm.entrypoints.openai.api_server" \
  --args "$VLLM_ARGS" \
  --platform "$ENDPOINT_PLATFORM" \
  --preset "$ENDPOINT_PRESET" \
  --disk-size "$ENDPOINT_DISK" \
  --shm-size "$ENDPOINT_SHM" \
  --container-port "$ENDPOINT_PORT" \
  --auth token \
  --token "$TOKEN" \
  --env "HF_TOKEN=$HF_TOKEN" \
  --subnet-id "$(subnet_id)" \
  "${DRY[@]}"

if [[ ${#DRY[@]} -gt 0 ]]; then exit 0; fi
echo "Waiting for the model (first start downloads weights and compiles CUDA graphs) ..."
for _ in $(seq 1 90); do
  url="$(endpoint_url "$ENDPOINT_NAME" || true)"
  if [[ -n "$url" ]] && curl -fsS "$url/v1/models" -H "Authorization: Bearer $TOKEN" >/dev/null 2>&1; then
    echo "CLEARANCE_VISION_BASE_URL=$url/v1"
    echo "CLEARANCE_VISION_API_KEY=$TOKEN"
    exit 0
  fi
  sleep 20
done
echo "Vision endpoint is not ready yet; check: nebius ai endpoint get-by-name --name $ENDPOINT_NAME" >&2
exit 1
