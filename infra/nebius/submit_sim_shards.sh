#!/usr/bin/env bash
# Fan one simulation batch out to N CPU Serverless Jobs.
#   bash infra/nebius/submit_sim_shards.sh <batch_spec.json> <n_shards> [--dry-run]
# See infra/nebius/submit_sim_shards.py for the shard layout.
source "$(dirname "$0")/common.sh"
need nebius "curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash"
need python "Python 3.11 with PyYAML"
require_var CLEARANCE_S3_BUCKET
export CLEARANCE_IMAGE_JOBS_CPU="${CLEARANCE_IMAGE_JOBS_CPU:-$(image_ref clearance-jobs-cpu)}"
export NEBIUS_SUBNET_ID="$(subnet_id)"
cd "$REPO_ROOT"
python infra/nebius/submit_sim_shards.py "$@"
