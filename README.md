# market-pulse
Production-grade market-intelligence lakehouse on AWS — real-time + batch + document pipelines (Kinesis · Glue · Iceberg · Athena) with a hybrid RAG assistant. Fully infrastructure-as-code (Terraform) and CI/CD, cost-governed under a strict budget, and runnable for free locally via config swap (DuckDB / Ollama / LocalStack).

## Quickstart (local, free)
```bash
uv sync                                              # env + deps
uv run pre-commit install                            # commit gate (once)
set -a && . config/environments/local.env && set +a  # local config + dummy creds
make localstack-up && make wait-localstack           # healthy LocalStack at :4566
make test                                            # ruff + critical tests
```
`make help` lists every command.

## Docs
- [Developer Guide](docs/DEVELOPER_GUIDE.md) — setup, the cloud↔local design, runbook, tests.
- [User Guide](docs/USER_GUIDE.md) — using the platform (assistant, queries, dashboard).
- [Architecture](docs/architecture.md) — data flow, portability, idempotency, key decisions.
- [Plan](docs/plan/) — the full build spec (`02_PROJECT_PLAN_IN_DEPTH.md` is the playbook).
- Contributing & branch model: [CONTRIBUTING.md](CONTRIBUTING.md).

Switch to AWS by setting `APP_ENV=aws` with real creds in `~/.aws`; infrastructure is
human-applied (`terraform apply`) per the plan's human-in-the-loop rules.
