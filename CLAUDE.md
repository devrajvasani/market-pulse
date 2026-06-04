# CLAUDE.md — MarketPulse (project rules for Claude Code)

> **Project memory. These rules apply to the *whole* development, every session.** The plan files hold the detail; this file is the standing contract. Keep it loaded; don't restate the plan, follow it.

## Project (one line)
**MarketPulse** — a real-time market-intelligence **lakehouse** on AWS (medallion S3 + Apache Iceberg, streaming ingest, hybrid RAG assistant), built to switch **cloud ↔ local by config**. Strict **$99 AWS budget** (hard ceiling; credits expire ~June 18).

## Source of truth — follow the plan
- The plan files **are the spec**: `00` handoff, `01` high-level, **`02` in-depth (Sections A–O — the main playbook)**, `03` learning, `04` infrastructure setup.
- **Follow the plan faithfully: focus, no over-engineering, no skipping.** Build exactly what each stage specifies — **don't add** services/abstractions/features beyond it, and **don't skip** necessary steps (critical tests, least-privilege IAM, error handling, idempotency, logging, teardown). Apply it intelligently to specifics. If the plan seems wrong or missing something, **raise it and ask — never silently deviate.**
- Work **stage by stage** (`02` Stage 0 → 8); load only the relevant section per stage.

## How to work
- **Plan-first.** Before any non-trivial task, propose a **short plan** (approach + files + questions) and **wait for approval**. Use Plan Mode — lightweight, not a heavy spec (the plan already is the spec). Direct implementation only for small, obvious changes.
- **Ask, don't assume.** When a requirement is ambiguous, a choice has **cost/security/architecture** trade-offs, you're blocked, or about to assume something / do something irreversible → **stop and ask** (one focused question at a time); state assumptions explicitly. Ask for consequential decisions; proceed autonomously on routine ones.

## Human-in-the-loop (hard rules — never break)
- **Never `git commit` or `git push` without my explicit approval.** Stage changes, propose a Conventional-Commit message, then wait.
- **Never create AWS resources yourself.** For each one: explain the concept + the exact steps, then **I** create/deploy it (Console for a new service, or `terraform apply` that **I** run). You may run `terraform plan` (read-only); **never** `terraform apply`/`destroy` or mutating `aws` CLI commands.
- **Never** use `--dangerously-skip-permissions`.

## Skills & agents (you drive them; I don't invoke manually)
- **Auto-use the relevant skill:** `aws-cost-guard` (any infra/cost), `iac-terraform` (Terraform), `data-quality` (transforms/tests).
- **Run `cost-reviewer` + `infra-reviewer` before any deploy** / before handing me a `terraform apply`.
- When I must act (create an agent via `/agents`, reload the window, approve a commit, run `terraform apply`), **explain exactly what to do, step by step.**

## Cost — $99 hard ceiling
- Total AWS spend **< $99**, regardless of account type. **Price-check before creating anything.**
- **Avoid traps:** MWAA, Redshift provisioned, MSK, OpenSearch, NAT Gateway, idle EC2/RDS/EBS.
- **Prefer:** Athena / SQL ELT (CTAS, dbt) over Spark; **Glue Spark** only when PySpark is truly needed; on-demand **Kinesis** (stop when idle); **Lambda** over EC2; **FAISS-in-S3** over OpenSearch; S3 + Parquet + partitioning.
- Budgets at **$20 / $50 / $80** + Budget Actions. Track every hand-created resource; `terraform destroy` test resources; **export everything before the credit expiry**.

## Engineering standards
- PEP 8, type hints, docstrings. **Root-cause fixes — no band-aids** (no bare `except: pass`).
- **Idempotency + check-before-create** (re-runs are safe; no duplicate files/rows).
- **Error handling:** custom exceptions + clear messages + retries/backoff + a **DLQ**.
- **Critical-event logging** (detailed, not noisy).
- **Critical tests written *with* the code**, passing before any run/deploy (pre-commit + CI gate that blocks on failure).

## Secrets
- **Never hardcode, print, or commit a secret value.** App secrets → **Secrets Manager** or free **SSM Parameter Store** (least-privilege read). Real AWS creds in `~/.aws/`, short-lived/OIDC preferred.
- **Never put a secret *value* in Terraform** (create the container; value set out-of-band — the state trap). Secret-scanning **pre-commit hook**. A committed secret = compromised → **rotate it**.

## Naming, tags, branches
- **Resources:** `{project}-{env}-{service}-{purpose}` (slug `marketpulse`), e.g. `marketpulse-dev-bucket-bronze`.
- **Tags:** `default_tags` = project / environment / owner / managed-by.
- **Branches:** `main` (protected, releases) / `develop` (daily) / `feature/<module>`. **Conventional Commits** (`feat:`, `fix:`, …).

## Local-first (switch by config)
- Build against the **project-owned emulator** at $0 (currently **LocalStack** in `infrastructure/localstack/`); use real AWS only when I choose to.
- **Adapters:** Athena↔**DuckDB**, Bedrock↔**Ollama**; everything else via `AWS_ENDPOINT_URL`. **Glue/Athena are LocalStack Pro → use DuckDB + PySpark locally.**
- **Health-check the emulator before use** (fail fast). Dummy creds locally (`test`/`test`), never real keys. **Same standards apply to any emulator added later (e.g. Azurite).**
- Setup steps: **`04_INFRASTRUCTURE_SETUP.md`**.
