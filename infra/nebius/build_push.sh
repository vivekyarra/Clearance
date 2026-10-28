#!/usr/bin/env bash
# Build the three Clearance images (linux/amd64) and push them to Nebius Container Registry.
#   bash infra/nebius/build_push.sh [api|jobs-cpu|jobs-gpu ...]     (default: all three)
# First time: nebius registry create --name clearance --format json | jq -r .metadata.id | cut -d- -f 2
#   -> put the result in CLEARANCE_REGISTRY_PATH; then `nebius registry configure-helper`.
# Docs: https://docs.nebius.com/container-registry/quickstart.md
source "$(dirname "$0")/common.sh"
need docker "https://docs.docker.com/get-docker/"
targets=("$@"); [[ ${#targets[@]} -eq 0 ]] && targets=(api jobs-cpu jobs-gpu)
for t in "${targets[@]}"; do
  ref="$(image_ref "clearance-$t")"
  echo ">> building $ref"
  docker build --platform linux/amd64 -f "$REPO_ROOT/infra/Dockerfile.$t" -t "$ref" "$REPO_ROOT"
  docker push "$ref"
done
