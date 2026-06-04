#!/usr/bin/env bash
# Readiness gate for LocalStack — "container up" is NOT "services ready" (file 04 section 4.4).
# Polls the health endpoint and confirms the required services report running/available.
# Exits 0 when ready; non-zero with a clear, actionable message otherwise.
#
# Pure curl + grep: no Python/jq dependency, so it behaves identically on Git Bash
# (Windows), macOS, and Linux CI. (An earlier version trusted any `python` on PATH and
# was fooled by the Windows Store python stub — hence grep-only here.)
set -euo pipefail

ENDPOINT="${AWS_ENDPOINT_URL:-http://localhost:4566}"
HEALTH_URL="${ENDPOINT%/}/_localstack/health"
REQUIRED_SERVICES=("s3")   # the subset that must be live before anything runs against it
MAX_ATTEMPTS="${LOCALSTACK_WAIT_ATTEMPTS:-30}"
SLEEP_SECONDS="${LOCALSTACK_WAIT_SLEEP:-2}"

fail() {
  echo "ERROR: $*" >&2
  echo "Hint: run 'docker compose up -d' in infrastructure/localstack/ then retry." >&2
  exit 1
}

command -v curl >/dev/null 2>&1 || fail "curl is required but not installed."

# is_ready <health-json> <service-name> — true when the service status is running/available.
is_ready() {
  printf '%s' "$1" | grep -Eq "\"$2\"[[:space:]]*:[[:space:]]*\"(running|available)\""
}

echo "Waiting for LocalStack at ${HEALTH_URL} (need: ${REQUIRED_SERVICES[*]})..."
for _ in $(seq 1 "${MAX_ATTEMPTS}"); do
  if body="$(curl -sf "${HEALTH_URL}" 2>/dev/null)"; then
    ready=1
    for svc in "${REQUIRED_SERVICES[@]}"; do
      is_ready "${body}" "${svc}" || { ready=0; break; }
    done
    if [ "${ready}" -eq 1 ]; then
      echo "LocalStack ready — ${REQUIRED_SERVICES[*]} running at ${ENDPOINT}."
      exit 0
    fi
  fi
  sleep "${SLEEP_SECONDS}"
done

fail "LocalStack not ready after ~$((MAX_ATTEMPTS * SLEEP_SECONDS))s; ${REQUIRED_SERVICES[*]} not running."
