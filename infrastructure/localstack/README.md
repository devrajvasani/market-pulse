# LocalStack — MarketPulse local AWS emulator

Project-owned AWS emulator so the platform runs **offline and free**, then
switches to real AWS by config only (plan Section B / file `04_INFRASTRUCTURE_SETUP.md`).
A new developer needs only Docker — no global install.

## Run it
```bash
cd infrastructure/localstack
docker compose up -d     # start LocalStack + the console dashboard (first run builds ./ui)
docker compose ps        # localstack should report "healthy"
docker compose down      # stop
```
- Gateway: `http://localhost:4566`
- **Console:** `http://localhost:8080`

## Console (read-only dashboard)
A small AWS-console-like dashboard (`./ui` — a Flask + boto3 container) that queries LocalStack
**server-side** (so the browser never deals with SigV4/CORS) and shows live state: **service
health**, **S3 buckets + their objects**, **Lambda** functions, **EventBridge** rules, **KMS**
keys, **Secrets Manager** (names only — values are **never** read), plus SQS / SNS / Step Functions
/ Kinesis / CloudWatch Logs / IAM. Auto-refreshes every 10 s. **Read-only and dev-only.**

## Credentials (dummy — NEVER real)
Local code/CLI use placeholder creds. Never point real AWS keys at the emulator
(file 04 §4.2 / plan Section O). These live in `config/environments/local.env`:
```
AWS_ENDPOINT_URL=http://localhost:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=us-east-1
```

## LocalStack auth token (optional on the pinned image)
We pin **`localstack/localstack:3`** (the 3.x Community line), which runs **token-free** — no
account needed for the services MarketPulse uses. A token is required only if you upgrade to a
newer **4.x / 2026.x** image, which gates even Community behind a free token (plan `01` §5/§10)
and otherwise exits with code 55. If you upgrade, create a free account at
https://app.localstack.cloud, then put the token (a **secret**, never committed) in
`infrastructure/localstack/.env` (gitignored; Compose auto-reads it):
```
LOCALSTACK_AUTH_TOKEN=<your token>
```

## Readiness check (do this before anything runs against it)
"Container up" ≠ "services ready" (file 04 §4.4). The compose `healthcheck` gates
the UI on readiness; scripts/tests must gate too:
```bash
curl -s http://localhost:4566/_localstack/health      # the needed services show "running"/"available"
../../scripts/wait_for_localstack.sh                  # repo readiness gate — exits non-zero with a clear message
```

## What's included / excluded (the free/paid boundary)
- **Included (Community, free):** `s3`, `kinesis`, `lambda`, `sqs`, `sns`,
  `events` (EventBridge), `stepfunctions`, `secretsmanager`, `logs`, `iam`, `sts`.
- **NOT included:** **Glue** and **Athena** are LocalStack **Pro** (~$45/mo).
  MarketPulse keeps these free with **DuckDB** (query, in place of Athena) and
  **PySpark** (in place of Glue Spark). Code switches by config (plan Section B).

## Smoke test (proves it works)
```bash
docker compose up -d
curl -s http://localhost:4566/_localstack/health          # s3 should be running
awslocal s3 mb s3://marketpulse-dev-bucket-bronze         # create a bucket
awslocal s3 ls                                            # see it listed
```

## Runtime state
`./volume/` holds LocalStack runtime data and is **gitignored** (file 04 §4.5).

## Troubleshooting (root-cause, then log it in the runbook)
- **Not healthy?** `docker compose ps`; `docker compose logs localstack`; set `DEBUG=1` temporarily.
- **Port 4566/8080 in use?** Stop the conflicting process or remap the port in `docker-compose.yml`.
- **Calls behave oddly?** Confirm `AWS_ENDPOINT_URL` is set with dummy creds — you're hitting the
  emulator, not real AWS. Re-check the service is in the included (free) list above.
- **A Pro-only service "doesn't work" (e.g. Glue)?** Expected — use the local twin (DuckDB / PySpark).
