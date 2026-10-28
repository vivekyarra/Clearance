#!/usr/bin/env bash
# Create the Clearance bucket, a service account with an S3 access key, and the CORS rule that lets
# phones upload with presigned PUT URLs. Commands follow https://docs.nebius.com/object-storage/quickstart.md
#   bash infra/nebius/create_bucket.sh
source "$(dirname "$0")/common.sh"
need nebius "curl -sSL https://artifacts.nebius.cloud/cli/install.sh | bash"
need aws "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
need jq "https://jqlang.org/"
require_var CLEARANCE_S3_BUCKET
ENDPOINT="https://storage.${NEBIUS_REGION}.nebius.cloud"

if [[ -z "${AWS_ACCESS_KEY_ID:-}" ]]; then
  SA_ID=$(nebius iam service-account create --name clearance-storage-sa --format jsonpath='{.metadata.id}')
  PROJECT_ID=$(nebius config get parent-id)
  TENANT_ID=$(nebius iam project get "$PROJECT_ID" --format jsonpath='{.metadata.parent_id}')
  EDITORS_GROUP_ID=$(nebius iam group get-by-name --name editors --parent-id "$TENANT_ID" --format jsonpath='{.metadata.id}')
  nebius iam group-membership create --parent-id "$EDITORS_GROUP_ID" --member-id "$SA_ID" >/dev/null
  KEY_ID=$(nebius iam v2 access-key create --account-service-account-id "$SA_ID" --description clearance --format json | jq -r '.metadata.id')
  AWS_ACCESS_KEY_ID=$(nebius iam v2 access-key get --id "$KEY_ID" --format json | jq -r '.status.aws_access_key_id')
  AWS_SECRET_ACCESS_KEY=$(nebius iam v2 access-key get --id "$KEY_ID" --format json | jq -r '.status.secret')
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
  echo "Add these to infra/nebius/config.env (shown once):"
  echo "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID"
  echo "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
fi
export AWS_DEFAULT_REGION="$NEBIUS_REGION"
aws --endpoint-url "$ENDPOINT" s3 mb "s3://$CLEARANCE_S3_BUCKET" || echo "(bucket may already exist)"
aws --endpoint-url "$ENDPOINT" s3api put-bucket-cors --bucket "$CLEARANCE_S3_BUCKET" \
  --cors-configuration "file://$NEBIUS_DIR/bucket_cors.json"
# Presigned URL check (docs/VERIFIED.md 3.4: Confirm with account).
echo ok > /tmp/clearance_presign_check.txt
aws --endpoint-url "$ENDPOINT" s3 cp /tmp/clearance_presign_check.txt "s3://$CLEARANCE_S3_BUCKET/checks/presign.txt"
url=$(aws --endpoint-url "$ENDPOINT" s3 presign "s3://$CLEARANCE_S3_BUCKET/checks/presign.txt" --expires-in 300)
if curl -fsS "$url" | grep -q ok; then echo "presigned GET: works"; else echo "presigned GET: FAILED" >&2; exit 1; fi
