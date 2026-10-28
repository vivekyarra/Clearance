#!/usr/bin/env bash
# Deploy the Clearance API (+ web console) as a Nebius Serverless Endpoint.
#   bash infra/nebius/deploy_api_endpoint.sh [--dry-run]
# Needs: images pushed (build_push.sh), bucket + access key (create_bucket.sh).
# Flags: https://docs.nebius.com/cli/reference/ai/endpoint/create.md (docs/VERIFIED.md 2.11).
source "$(dirname "$0")/common.sh"
source "$NEBIUS_DIR/endpoints/api.env"
need nebius "curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash"
need jq "https://jqlang.org/"
require_var CLEARANCE_S3_BUCKET
require_var AWS_ACCESS_KEY_ID
require_var AWS_SECRET_ACCESS_KEY
DRY=()
[[ "${1:-}" == "--dry-run" ]] && DRY=(--dry-run)
SUBNET="$(subnet_id)"
# Operator token for every write route of the API (sites, twins, Clearance checks, catalog review, live).
# Generated when empty and printed once at the end; keep it in infra/nebius/config.env to reuse it.
OPERATOR_TOKEN="${CLEARANCE_OPERATOR_TOKEN:-$(new_token)}"
AUTH_ARGS=(--auth "$ENDPOINT_AUTH")
HEALTH_HDR=()
if [[ "$ENDPOINT_AUTH" == "token" ]]; then
  ENDPOINT_TOKEN="${CLEARANCE_API_TOKEN:-$(new_token)}"
  AUTH_ARGS+=(--token "$ENDPOINT_TOKEN")
  HEALTH_HDR=(-H "Authorization: Bearer $ENDPOINT_TOKEN")
fi

# Secrets travel as plain --env for a short-lived deployment. For anything long-lived use
# --env-secret KEY=<SecretStash selector> (docs: serverless/jobs/manage).
ENV_ARGS=(
  --env "CLEARANCE_DATA=/data"
  --env "CLEARANCE_DEPLOYED=1"
  --env "CLEARANCE_OPERATOR_TOKEN=$OPERATOR_TOKEN"
  --env "CLEARANCE_CATALOG_WRITEBACK=${CLEARANCE_CATALOG_WRITEBACK:-0}"
  --env "CLEARANCE_S3_BUCKET=$CLEARANCE_S3_BUCKET"
  --env "CLEARANCE_S3_ENDPOINT=${CLEARANCE_S3_ENDPOINT:-https://storage.${NEBIUS_REGION}.nebius.cloud}"
  --env "CLEARANCE_S3_REGION=${CLEARANCE_S3_REGION:-$NEBIUS_REGION}"
  --env "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID"
  --env "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
  --env "CLEARANCE_DISPATCHER=${CLEARANCE_DISPATCHER:-nebius}"
  --env "CLEARANCE_NEBIUS_SUBMIT=1"
  --env "CLEARANCE_IMAGE_JOBS_CPU=$(image_ref clearance-jobs-cpu)"
  --env "CLEARANCE_IMAGE_JOBS_GPU=$(image_ref clearance-jobs-gpu)"
  --env "NEBIUS_SUBNET_ID=$SUBNET"
  --env "CLEARANCE_LLM_MODE=${CLEARANCE_LLM_MODE:-live}"
  --env "CLEARANCE_TAVILY_MODE=${CLEARANCE_TAVILY_MODE:-live}"
)
# Upload URLs are built from CLEARANCE_PUBLIC_API_BASE when set; otherwise the Host header is checked
# against CLEARANCE_ALLOWED_HOSTS (the managed endpoint host). The API refuses to start with neither.
[[ -n "${CLEARANCE_PUBLIC_API_BASE:-}" ]] && ENV_ARGS+=(--env "CLEARANCE_PUBLIC_API_BASE=$CLEARANCE_PUBLIC_API_BASE")
ENV_ARGS+=(--env "CLEARANCE_ALLOWED_HOSTS=${CLEARANCE_ALLOWED_HOSTS:-*.nebius.cloud,127.0.0.1,localhost}")
[[ -n "${CLEARANCE_CORS_ORIGINS:-}" ]] && ENV_ARGS+=(--env "CLEARANCE_CORS_ORIGINS=$CLEARANCE_CORS_ORIGINS")
[[ -n "${NEBIUS_API_KEY:-}" ]] && ENV_ARGS+=(--env "NEBIUS_API_KEY=$NEBIUS_API_KEY")
[[ -n "${TAVILY_API_KEY:-}" ]] && ENV_ARGS+=(--env "TAVILY_API_KEY=$TAVILY_API_KEY")
[[ -n "${CLEARANCE_VISION_BASE_URL:-}" ]] && ENV_ARGS+=(--env "CLEARANCE_VISION_BASE_URL=$CLEARANCE_VISION_BASE_URL")
[[ -n "${CLEARANCE_VISION_API_KEY:-}" ]] && ENV_ARGS+=(--env "CLEARANCE_VISION_API_KEY=$CLEARANCE_VISION_API_KEY")
# With CLEARANCE_DISPATCHER=nebius the API container submits jobs with the Nebius CLI: build the image
# with --build-arg INSTALL_NEBIUS_CLI=1 and give it a service-account CLI profile
# (https://docs.nebius.com/cli/configure.md). Confirm with account. Use CLEARANCE_DISPATCHER=local to run
# jobs inside the API container instead.

nebius ai endpoint create \
  --name "$ENDPOINT_NAME" \
  --image "$(image_ref "$ENDPOINT_IMAGE_NAME")" \
  --platform "$ENDPOINT_PLATFORM" \
  --preset "$ENDPOINT_PRESET" \
  --disk-size "$ENDPOINT_DISK" \
  --container-port "$ENDPOINT_PORT" \
  "${AUTH_ARGS[@]}" \
  --volume "s3://$CLEARANCE_S3_BUCKET:/data:rw" \
  --subnet-id "$SUBNET" \
  "${ENV_ARGS[@]}" "${DRY[@]}"

if [[ ${#DRY[@]} -gt 0 ]]; then exit 0; fi
if [[ -z "${CLEARANCE_OPERATOR_TOKEN:-}" ]]; then
  echo "CLEARANCE_OPERATOR_TOKEN=$OPERATOR_TOKEN   (generated; enter it in the console's operator token dialog)"
fi
echo "Waiting for RUNNING and /health ..."
for _ in $(seq 1 60); do
  url="$(endpoint_url "$ENDPOINT_NAME" || true)"
  # with ENDPOINT_AUTH=token the managed URL needs the endpoint token as well
  if [[ -n "$url" ]] && curl -fsS "${HEALTH_HDR[@]}" "$url/health" >/dev/null 2>&1; then
    echo "Clearance API: $url"
    exit 0
  fi
  sleep 10
done
echo "Endpoint is not healthy yet; check: nebius ai endpoint get-by-name --name $ENDPOINT_NAME" >&2
exit 1
