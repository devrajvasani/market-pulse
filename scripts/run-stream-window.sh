#!/usr/bin/env bash
# Run the streaming producer for a BOUNDED window, then stop -- cost control (Stage 4):
# Kinesis bills per shard-hour, so we never leave the producer (or stream) running idle.
#
#   Local (LocalStack):    MINUTES=2 ./scripts/run-stream-window.sh
#   Dry-run (no Kinesis):  DRY_RUN=1 MINUTES=1 ./scripts/run-stream-window.sh
#   Real AWS:              AWS_ENDPOINT_URL= AWS_PROFILE=marketpulse-admin MINUTES=2 ./scripts/run-stream-window.sh
#
# From INSIDE the container, LocalStack is reached at host.docker.internal:4566 (Docker
# Desktop) -- NOT localhost. For real AWS, set AWS_ENDPOINT_URL="" (and provide creds).
set -euo pipefail

MINUTES="${MINUTES:-1}"
IMAGE="marketpulse-producer:local"
ENDPOINT="${AWS_ENDPOINT_URL-http://host.docker.internal:4566}"

echo "Building producer image (${IMAGE})..."
docker build -f src/ingestion/streaming/Dockerfile -t "${IMAGE}" .

echo "Streaming ${MINUTES} min -> '${ENDPOINT:-<real AWS>}' (products: ${STREAM_PRODUCTS:-BTC-USD,ETH-USD}, dry_run=${DRY_RUN:-0})..."
docker run --rm \
  -e AWS_ENDPOINT_URL="${ENDPOINT}" \
  -e AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}" \
  -e AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}" \
  -e AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}" \
  -e STREAM_NAME="${STREAM_NAME:-}" \
  -e STREAM_PRODUCTS="${STREAM_PRODUCTS:-BTC-USD,ETH-USD}" \
  -e STREAM_WINDOW_SECONDS="$(( MINUTES * 60 ))" \
  -e DRY_RUN="${DRY_RUN:-0}" \
  "${IMAGE}"
echo "Window complete."
