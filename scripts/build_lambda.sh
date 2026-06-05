#!/usr/bin/env bash
# Stage the Lambda deployment package: our code only (src/ + config/).
# pandas/pyarrow/awswrangler come from the AWS SDK for pandas layer, NOT the zip.
# Run before `terraform plan/apply` (or `tflocal`) — archive_file zips infra/build/lambda.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="$ROOT/infra/build/lambda"

rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -r "$ROOT/src" "$ROOT/config" "$STAGE/"

# Strip caches + placeholder files from the package.
find "$STAGE" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '.gitkeep' -delete 2>/dev/null || true

echo "Staged Lambda package -> $STAGE"
