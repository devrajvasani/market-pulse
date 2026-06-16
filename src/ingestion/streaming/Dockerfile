# Streaming producer container: Coinbase trades WebSocket -> Kinesis (Stage 4).
# Build from the REPO ROOT so it can copy src/ + config/:
#   docker build -f src/ingestion/streaming/Dockerfile -t marketpulse-producer:local .
FROM python:3.12-slim

WORKDIR /app

# Only the producer's runtime deps (no dbt/awswrangler) -> a small, fast image.
RUN pip install --no-cache-dir "boto3>=1.34" "websocket-client>=1.7" "python-dotenv>=1.0"

COPY src ./src
COPY config ./config

# The window length, endpoint, products, creds, and DRY_RUN are passed via env at run
# time (see scripts/run-stream-window.sh). The producer self-terminates at the deadline.
CMD ["python", "-m", "src.ingestion.streaming.producer"]
