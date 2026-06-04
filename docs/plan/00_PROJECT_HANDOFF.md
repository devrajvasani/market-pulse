# MarketPulse — Project Handoff (READ ME FIRST)

> Paste this into the new chat (Claude Code in VS Code, or a new Claude chat) **before** you start building. It orients the assistant on what we're building, the rules, and how to use the plan files. **Provide this together with** `01_PROJECT_PLAN_HIGH_LEVEL.md`, `02_PROJECT_PLAN_IN_DEPTH.md`, and `03_PERSONAL_LEARNING_PLAN.md`.

---

## 1. What we're building (one paragraph)

**MarketPulse** — a production-shaped, real-time **market-intelligence lakehouse** on AWS. It ingests financial/crypto data in **three forms** — live streaming prices (Kinesis), scheduled historical batches, and text documents/news — organises them into a **medallion lakehouse** (Bronze → Silver → Gold; Apache Iceberg tables stored as Parquet on S3), and exposes a **hybrid RAG assistant** that answers plain-English questions using *both* documents *and* live SQL metrics. Built with infrastructure-as-code (Terraform), CI/CD (GitHub Actions), tests, security, observability, and strict cost control. It is designed to **switch to a free local environment by changing configuration only** (DuckDB / Ollama / LocalStack).

---

## 2. The plan files & how to use them

- **`01` High-Level** — the blueprint / overview.
- **`02` In-Depth** — the build playbook (Sections A–M), stage by stage. **This is the main file to follow.**
- **`03` Learning** — what to study, ordered to match the build (learn each topic just before its stage).

**Lean-context rule (saves tokens, keeps focus):** when working a stage, load only **Sections A, B, B2, L, M + the one stage** from `02` — do **not** paste the whole file every turn.

---

## 3. Hard constraints

- **Budget: $99 AWS maximum** (treat as a hard ceiling). $100 credit is confirmed, but the **account type is still to be confirmed** at *Billing → Credits*: the **new Free Plan** closes safely at $0 (no bill); a **legacy / promotional / paid** account **bills overages**. The June 18 credit expiry hints it may *not* be the new Free Plan → **assume a hard ceiling until confirmed.**
- **Deadline:** export everything (code, IaC, data samples, screenshots, cost report) **before June 18** — the account may close.
- **Level:** beginner-to-medium data engineer → keep explanations beginner-friendly and explain the *why*.
- **Portability:** cloud↔local by config only. Backend adapters: **Athena↔DuckDB**, **Bedrock↔Ollama**; everything else via `AWS_ENDPOINT_URL` → LocalStack.
- **Local environment:** built from a **project-owned service emulator** in `infrastructure/<name>/` (any dev runs `docker compose up`). Currently **LocalStack (Community)** for AWS — Community services only, so **Glue/Athena are Pro-only and run locally via DuckDB + local PySpark instead.** Dummy creds (`test`/`test`, `us-east-1`); a healthcheck gates anything that uses it; runtime state gitignored. **The same standards apply to any emulator added later (e.g. Azurite for Azure).** Full step-by-step in **`04_INFRASTRUCTURE_SETUP.md`** (do it at Stage 0). See also `02` Section F.
- **Cost traps to avoid:** MWAA (~$350/mo), Redshift provisioned, MSK, OpenSearch, NAT Gateway, idle EC2/RDS/EBS, unattached Elastic IPs. **Do NOT create an AWS Organization** (it voids the credits).

---

## 4. Architecture in brief (services)

- **Storage / lake:** S3 (Parquet + Apache Iceberg), medallion Bronze/Silver/Gold.
- **Catalog:** Glue Data Catalog (+ crawlers, or manual tables).
- **Transform:** **primary = SQL ELT** (Athena `CREATE TABLE AS` + dbt-athena, writes Iceberg, cheap); **learning path = Glue Spark (PySpark)** for the same step.
- **Query engine ("warehouse"):** Athena (cloud) / DuckDB (local).
- **Streaming:** Kinesis Data Streams + Lambda consumer (+ a DLQ); containerised producer.
- **Orchestration:** Step Functions + EventBridge (NOT MWAA).
- **RAG / AI:** Bedrock (LLM + embeddings) / Ollama (local); vectors in a FAISS index in S3 (avoid OpenSearch vector store).
- **Security:** IAM least-privilege, KMS, Secrets Manager, Lake Formation.
- **Observability:** CloudWatch (logs, metrics, dashboards, alarms).
- **IaC / CI/CD / containers:** Terraform (+ `tflocal`), GitHub Actions, Docker (+ ECR / Fargate).
- **Cost:** Budgets + Budget Actions + Cost Explorer + cost-allocation tags.

---

## 5. Naming, tags & branches (standards to follow)

- **Cloud slug = `marketpulse`** (one word); **Git repo = `market-pulse`** (hyphenated). Resource names follow `{project}-{env}-{service}-{purpose}`, e.g. `marketpulse-dev-bucket-bronze`, `marketpulse-dev-stream-trades`.
- **Tags** (via Terraform `default_tags`): `project=marketpulse`, `environment=dev`, `owner=<you>`, `managed-by=terraform`.
- **Branches:** `main` (production, tagged releases like `v1.0.0`, **protected**) / `develop` (day-to-day, mixed work + bug fixes) / `feature/<module>` (per module) / temporary `release/x.y.z`. **Conventional Commits** (`feat(scope): …`, `fix(scope): …`).

---

## 6. STRICT rules (do not violate)

1. **NEVER `git commit` or `git push` without the developer's explicit approval.** Stage changes, propose a Conventional-Commit message, then **wait**. Enforce via: a line in `CLAUDE.md`, an **`ask` permission rule** on `Bash(git commit:*)` and `Bash(git push:*)`, and (optionally) a **PreToolUse hook**. **Never** use `--dangerously-skip-permissions` / `bypassPermissions`.
2. **NEVER create AWS resources yourself.** For every AWS resource, give a plain-words concept brief (what / why / key settings / cost / local twin / exact steps), then **instruct the developer** — a Console walkthrough the first time a service is used, then **Terraform that the developer runs**. Claude Code may run `terraform plan` (read-only) but **never `terraform apply`/`terraform destroy` or mutating `aws` CLI commands** — the developer runs those. Enforce via `CLAUDE.md` + `ask` rules on `Bash(terraform apply:*)`, `Bash(terraform destroy:*)`, mutating `Bash(aws:*)`. (This is deliberate: the developer is learning AWS hands-on. See `02` Section N.)
3. **Engineering standards (Section L of `02`):** PEP 8 + type hints + docstrings; **root-cause fixes, never band-aids** (no bare `except: pass`, no hacks); **idempotency + check-before-create/edit** (no duplicate files/rows; re-runs are safe); **critical-event logging** (detailed but not noisy); **error handling** with custom exceptions + clear messages + retries/backoff + a **DLQ**; **critical tests written with the code**, passing before any run/deploy (pre-commit + CI gate that blocks on failure).
4. **Secrets & credentials (Section O of `02`):** NEVER hardcode, print, or commit a secret value. Store app secrets in **Secrets Manager** (or free **SSM Parameter Store**) with least-privilege read; keep AWS creds in `~/.aws` (outside the repo), prefer short-lived/OIDC over long-lived keys; **never put a secret value in Terraform state**; add a **secret-scanning pre-commit hook**; treat any committed secret as compromised (rotate it). Instruct the developer on each step — they apply it.
5. **Cost discipline:** check the price + budget before creating any resource; confirm the AWS account type before creating anything; **track every hand-created resource** (forgotten ones cost money — Terraform is the source of truth for clean teardown); stop the Kinesis stream when idle; `terraform destroy` test resources.
6. **Ask, don't assume (keep me in the loop):** when a requirement is ambiguous, a choice has real cost/security/architecture trade-offs, you're blocked/unsure, or you're about to make an assumption I might disagree with or do something hard to reverse — **stop and ask me** (one focused question at a time), and **state assumptions explicitly**. Ask for the consequential decisions; proceed autonomously on the routine ones under these rules. (See `02` Section H.)

---

## 7. Claude Code setup (do in Stage 0)

- **Model:** default **Sonnet 4.6** for routine coding; use **Opus 4.8** (`/model opus`) for architecture and tricky debugging — or use the **`opusplan`** alias (Opus plans, Sonnet executes). *Reminder: Claude Code usage is billed via your Claude plan/API — separate from the AWS $99.*
- **`CLAUDE.md`** (always-on, keep short): project summary; the naming/tag standard; the **no-commit-without-approval rule**; the **no-AWS-provisioning rule** ("explain + instruct me; I run `terraform apply` and Console steps"); "use Conventional Commits"; "tests with code, root-cause fixes only"; "switch cloud↔local by config"; "read the relevant `02` section before building".
- **Skills** (`.claude/skills/<name>/SKILL.md`, load on demand): `aws-cost-guard` (price/budget check + cost-trap warnings before deploys), `iac-terraform` (Terraform conventions, least-privilege IAM, tagging), `data-quality` (pipeline test patterns, dbt tests).
- **Subagents** (`.claude/agents/`, set them to **Sonnet**): `cost-reviewer` (cost impact before deploy), `infra-reviewer` (IAM/security + Terraform review).
- **Permissions:** add **`ask` rules** for `Bash(git commit:*)`, `Bash(git push:*)`, `Bash(terraform apply:*)`, `Bash(terraform destroy:*)`, and mutating `Bash(aws:*)` — so commits *and* AWS changes always prompt you. (`terraform plan` + read-only `aws` are fine.)
- **Drive the skills/agents for the user:** the user does **not** invoke skills/agents manually. Automatically use the relevant skill per task (`aws-cost-guard` / `iac-terraform` / `data-quality`) and proactively run the `cost-reviewer` + `infra-reviewer` subagents before deploys. When the user must act (create an agent via `/agents`, reload the window, approve a commit, run `terraform apply`), explain exactly what to do.
- **Hook (optional, strong):** a `PreToolUse` hook that blocks `git commit`/`git push` (and optionally `terraform apply`) unless confirmed.

---

## 8. Current status & first three moves

**Status:** planning is complete; about to start Stage 0 (Foundation & safety).

1. **Confirm AWS account type** at Billing → Credits — and **treat $99 as a hard ceiling for all AWS spend** (new Free Plan closes safely at $0; legacy/paid bills overages — either way, $99 is the cap).
2. **Stand up the safety layer before any code:** create the repo + `main`/`develop`; write `CLAUDE.md`; add the git `ask` permission rule. (This makes the no-commit guardrail live from the first action.)
3. **Begin Stage 0**, then proceed stage by stage per `02` (Stage 0 → 8), feeding only the relevant sections each time.

---

*Definition of "done" for any component (Section K of `02`): runs from IaC, has passing critical tests, least-privilege IAM, proper error handling + critical-event logging, is idempotent, is documented (with its command in the runbook), switches cloud↔local by config, and has a known cost.*
