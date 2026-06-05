# Stage 0 — Foundation & Safety

**Status:** Done (2026-06-05) · Spec: `docs/plan/02_PROJECT_PLAN_IN_DEPTH.md` (Section C)

**Goal:** set up safety guardrails + the project skeleton before creating any AWS resource.

## Code & local setup (in repo)
- Repo structure: `config/ src/ infra/ infrastructure/ tests/ scripts/ docs/`.
- Backend adapters: `config/settings.py` picks Athena/DuckDB + Bedrock/Ollama by `APP_ENV`.
- `src/common`: custom errors + JSON logging.
- LocalStack (Docker, image `:3`, token-free) + `wait_for_localstack.sh` readiness check.
- Tools: `uv`, `ruff`, `pytest`, `pre-commit` (detect-secrets), `Makefile`.
- Terraform skeleton: provider + `default_tags`, no resources yet.
- CI: ruff + pytest on push/PR.
- Docs: developer guide, user guide, architecture, contributing.
- 16 tests passing.

## AWS done (Console — all free, no paid resources created)
- Account `724166961779`, region `us-east-1`, **Free Plan**, $100 credit, expires 2026-06-18.
- Root: MFA on, no access keys.
- IAM user `marketpulse-admin`: AdministratorAccess + own MFA (used instead of root).
- Enabled "IAM access to Billing".
- Cost Explorer on; billing alerts on (CloudWatch + Free Tier).
- 3 Budgets — `$20`, `$50`, `$80` — email at 80% and 100%.

## Git
- Git Flow: `feature/*` → PR → `develop`; `main` protected (PR + CI).
- `ask` rules: prompt before `git commit/push`, `terraform apply/destroy`.
- Merged PRs: #1 skeleton · #2 readiness fix · #3 ask-rules + tf lock.

## Verified
`make test` ✓ · `pre-commit` ✓ · LocalStack healthy (11 services + S3 test) ✓ · `terraform validate` ✓.

## Key choices
us-east-1 · secret scanner = detect-secrets · LocalStack `:3` (token-free) · minimal CI now.

## Left for Stage 1
Create the Resource Group + activate the `project` cost-allocation tag (both need a tagged resource to exist first).
