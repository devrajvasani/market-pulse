# MarketPulse runbook — one-word commands (plan Section J).
# Windows: run via Git Bash / WSL, or install `make` (e.g. choco install make).
.PHONY: help install lint format test precommit \
        localstack-up localstack-down wait-localstack deploy-local run-local \
        tf-fmt tf-validate tf-plan deploy stream destroy

help:  ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the uv env and install all deps
	uv sync

lint:  ## Lint with ruff
	uv run ruff check .

format:  ## Auto-format with ruff
	uv run ruff format .

test:  ## Lint + run critical tests (same gate as pre-commit)
	uv run ruff check .
	uv run pytest

precommit:  ## Run every pre-commit hook across the repo
	uv run pre-commit run --all-files

localstack-up:  ## Start the local AWS emulator (LocalStack)
	docker compose -f infrastructure/localstack/docker-compose.yml up -d

localstack-down:  ## Stop LocalStack
	docker compose -f infrastructure/localstack/docker-compose.yml down

wait-localstack:  ## Block until LocalStack is healthy (readiness gate)
	./scripts/wait_for_localstack.sh

deploy-local: localstack-up wait-localstack  ## Bring up the local stack and confirm it's ready
	@echo "Local stack ready at http://localhost:4566"

run-local:  ## Run the batch ingestion locally (needs APP_ENV=local + BRONZE_BUCKET from tflocal output)
	uv run python -m src.ingestion.batch.ingest

tf-fmt:  ## Check Terraform formatting
	terraform -chdir=infra fmt -check -recursive

tf-validate:  ## Validate Terraform (read-only; never applies)
	terraform -chdir=infra init -backend=false >/dev/null && terraform -chdir=infra validate

tf-plan:  ## Show the Terraform plan (read-only preview for review)
	terraform -chdir=infra plan

# deploy/destroy are HUMAN-run (plan Section N): Claude never runs terraform apply/destroy.
# These print the exact command so the action stays explicit and un-bypassable.
deploy:  ## (human) Print the apply command — review tf-plan first, then run it yourself
	@echo "Review 'make tf-plan', then deploy yourself:  terraform -chdir=infra apply"

stream:  ## Run the streaming producer for a window (Stage 4): make stream MINUTES=10
	@echo "stream: implemented in Stage 4 (run-stream-window)."

destroy:  ## (human) Print the teardown command — run it yourself before the credit expires
	@echo "Tear down yourself (Section I/N):  terraform -chdir=infra destroy"
