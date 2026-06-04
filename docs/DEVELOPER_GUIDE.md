# MarketPulse — Developer Guide

> Seeded in Stage 0; expanded each stage and finalised in Stage 8. The full spec
> lives in `docs/plan/` (`02_PROJECT_PLAN_IN_DEPTH.md` is the playbook).

## What this is
A market-intelligence **lakehouse** (medallion S3 + Apache Iceberg, streaming +
batch + documents, a hybrid RAG assistant) that runs on AWS or **free locally by
config swap**. See `docs/architecture.md` for the design.

## Prerequisites (install once)
| Tool | Why | Check |
|---|---|---|
| Docker (Desktop/Engine) | runs LocalStack | `docker --version` |
| AWS CLI v2 + `awslocal` | talk to AWS and LocalStack | `aws --version` |
| Terraform + `tflocal` | infrastructure-as-code | `terraform -version` |
| `uv` | Python env + deps | `uv --version` |
| DuckDB | local query engine (Athena stand-in) | `duckdb --version` |
| Ollama | local LLM (Bedrock stand-in) — **installed separately** | `ollama --version` |

If a core tool is missing, install it before proceeding — don't guess.

**LocalStack auth token (optional).** We pin `localstack/localstack:3` (Community), which runs
token-free. Only if you upgrade to a newer 4.x/2026.x image do you need a free token: create an
account at https://app.localstack.cloud and put it in `infrastructure/localstack/.env` (gitignored)
as `LOCALSTACK_AUTH_TOKEN=<token>` — never commit it.

## Local setup (free, offline)
```bash
uv sync                                              # create the env + install deps
uv run pre-commit install                            # enable the commit gate (once)
set -a && . config/environments/local.env && set +a  # APP_ENV=local + dummy creds + endpoint
make localstack-up && make wait-localstack           # start + wait for a healthy LocalStack
make test                                            # ruff + critical tests
```
`make help` lists every command. Local mode uses **dummy** creds (`test`/`test`) and
the LocalStack endpoint — never real keys (Section O).

## The backend-adapter design (cloud ↔ local by config)
Code never imports `boto3` for Athena/Bedrock directly — it asks
`config/settings.py` for the active backend. `APP_ENV=aws` → `AthenaBackend` /
`BedrockBackend`; `APP_ENV=local` → `DuckDBBackend` / `OllamaBackend`. Group-A
services (S3, Kinesis, Lambda, SQS, SNS, EventBridge, Step Functions, Secrets
Manager) need no adapter — `boto3` respects `AWS_ENDPOINT_URL`. Switching
environments is changing `APP_ENV` and loading a different `.env` — no logic rewrite.

## Repo layout
`config/` settings + env files · `src/` (ingestion, transform, rag, backends, common)
· `infra/` Terraform · `infrastructure/localstack/` the local emulator ·
`tests/` · `scripts/` · `.github/workflows/` CI/CD · `docs/`.

## Coding standards
PEP 8 + type hints + docstrings; root-cause fixes; idempotency + check-before-create;
custom exceptions + retries/backoff + DLQ; JSON critical-event logging; **critical tests
written with the code**. Enforced by `CLAUDE.md` + the pre-commit/CI gate (plan Section L).

## Tests & the quality gate
- `make test` — ruff + pytest (the same gate as pre-commit and CI).
- `uv run pre-commit run --all-files` — all hooks (lint, format, secret scan, tests).
- Unit tests mock AWS with `moto`; integration tests (later stages) run against LocalStack.

## Runbook (Makefile)
`install` · `lint` · `format` · `test` · `precommit` · `localstack-up` / `localstack-down`
· `wait-localstack` · `deploy-local` · `run-local` · `tf-fmt` · `tf-validate` · `tf-plan`
· `deploy` / `destroy` (**human-run** — they print the exact Terraform command; Section N) ·
`stream` (Stage 4).

## AWS mode
Configure real creds in `~/.aws` (profile or SSO — never in the repo). Set
`APP_ENV=aws`. Claude writes Terraform and runs `terraform plan`; **you** run
`terraform apply` after reviewing the plan (Section N). Cost guardrails + the
teardown procedure: plan Section I.

## Idempotency design
How re-runs stay safe (deterministic keys, date-partition overwrite, Iceberg
`MERGE`, Terraform's own idempotency) is documented in `docs/architecture.md`.
