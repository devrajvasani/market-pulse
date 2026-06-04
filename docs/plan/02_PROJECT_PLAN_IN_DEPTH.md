# Project Plan — In Depth
## "MarketPulse" : Build-Ready Implementation Guide

> Read `01_PROJECT_PLAN_HIGH_LEVEL.md` first for the overview. This file is the **detailed playbook** to hand to Claude Code, stage by stage. Each stage lists **ordered tasks** ("first do this, then this"), the **definition of done**, and **what to ask Claude Code to build**. Cross-cutting deep-dives (engineering standards, security, CI/CD, containers, cost) are at the end.

> **⚠️ Before you start (Day 0) — two things first:**
> 1. **Confirm your AWS account type** at *Billing → Credits*, and **treat $99 as a hard ceiling for all AWS spend.** You have $100 credit; the new Free Plan closes safely at $0, a legacy/paid account bills overages — **either way, $99 is the cap.** Set Budgets + Budget Actions accordingly (Section I).
> 2. **Stand up the safety layer before writing any project code:** the repo with `main`/`develop`, `CLAUDE.md`, and the `ask` permission rules for git + `terraform apply`/AWS commands (Sections H, M.7, N). This makes the human-in-the-loop guardrails live from the very first action.

---

## A. Repository structure

Set this up in Stage 0. A clean structure is what reviewers notice first.

```
market-pulse/                  # Git repo name stays kebab-case (the cloud slug is "marketpulse")
├── README.md                  # short intro + quickstart (points to docs/)
├── Makefile                   # one-word commands: make test / deploy-local / stream / destroy
├── .pre-commit-config.yaml    # runs ruff + critical tests before EVERY commit
├── infrastructure/            # project-owned local environment (any dev just `docker compose up`)
│   └── localstack/
│       ├── docker-compose.yml # LocalStack (S3 + Community services) + a small UI
│       ├── ui/                # LocalStack dashboard (static, served by nginx)
│       ├── volume/            # LocalStack runtime state — GITIGNORED (not committed)
│       └── README.md          # how to run it + dummy credentials
├── .env.example               # all config keys, no secrets
├── pyproject.toml             # Python deps (managed with uv) + ruff config
│
├── config/
│   ├── settings.py            # loads env vars; picks cloud vs local backends
│   └── environments/
│       ├── aws.env            # AWS endpoints + region
│       └── local.env          # AWS_ENDPOINT_URL=http://localhost:4566 etc.
│
├── src/
│   ├── ingestion/
│   │   ├── batch/             # scheduled pulls (historical prices, news)
│   │   └── streaming/         # live feed producer (containerized)
│   ├── transform/
│   │   ├── sql/               # Athena CTAS / INSERT (primary ELT path)
│   │   ├── glue_spark/        # PySpark Glue job (the Spark learning path)
│   │   └── dbt/               # dbt project (Silver/Gold SQL models + tests)
│   ├── rag/                   # embeddings, vector index, assistant
│   ├── backends/              # the adapter layer (see Section B)
│   │   ├── query_engine.py    # QueryEngine interface
│   │   ├── athena_backend.py
│   │   ├── duckdb_backend.py
│   │   ├── llm_client.py      # LLMClient interface
│   │   ├── bedrock_backend.py
│   │   └── ollama_backend.py
│   └── common/                # logging, errors.py (custom exceptions), schemas, data-quality helpers
│
├── infra/                     # Terraform (all AWS resources)
│   ├── modules/               # reusable modules: s3, glue, kinesis, lambda, iam...
│   └── main.tf, variables.tf, outputs.tf
│
├── .github/workflows/         # CI/CD pipelines (GitHub Actions)
│   ├── ci.yml                 # lint + test + terraform plan (on every push); BLOCKS on failure
│   └── deploy.yml             # terraform apply (on main, manual approval)
│
├── tests/                     # unit (moto-mocked) + integration tests (run vs LocalStack)
├── scripts/                   # helper scripts (run-stream-window.sh, teardown.sh)
└── docs/
    ├── DEVELOPER_GUIDE.md
    ├── USER_GUIDE.md
    └── architecture.md        # diagram + design decisions + idempotency design
```

---

## B. Config & backend-adapter design (the portability core)

This is *the* design that makes "switch to local by config only" real. Build it in Stage 0 so every later stage uses it.

**Idea:** code never imports `boto3` for Athena/Bedrock directly. It asks a factory for the active backend.

```python
# config/settings.py  (simplified)
import os
ENV = os.getenv("APP_ENV", "local")          # "aws" or "local"
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL")  # set only in local.env

def get_query_engine():
    if ENV == "aws":
        from src.backends.athena_backend import AthenaBackend
        return AthenaBackend()
    from src.backends.duckdb_backend import DuckDBBackend
    return DuckDBBackend()

def get_llm_client():
    if ENV == "aws":
        from src.backends.bedrock_backend import BedrockBackend
        return BedrockBackend()
    from src.backends.ollama_backend import OllamaBackend
    return OllamaBackend()
```

**Both backends implement the same interface**, e.g. `QueryEngine.run_sql(sql) -> rows`. Switching environments = changing `APP_ENV` and loading a different `.env`. **No logic rewrite.**

For **Group A** services (S3, Lambda, Kinesis, SQS, SNS, DynamoDB, EventBridge, Step Functions, Secrets Manager), `boto3` automatically respects `AWS_ENDPOINT_URL`. So local mode just points them at LocalStack — no adapter needed.

---

## B2. Project organization — naming, tags & resource groups

> **Apply this to EVERY resource you create in the stages below, and to your code.** This is what lets you (1) recognise a MarketPulse resource at a glance, and (2) see this project's resources and *cost* on one page — separate from any future project. The "hand to Claude Code" lines in each stage assume these rules.

**Why this matters (the *why*):** AWS is **flat** — by default every bucket, Lambda, and stream lands in one big pile with no "project folders". Two mechanisms fix that: **names** (so *your eyes* recognise a resource) and **tags** (so *AWS tools* can group and bill by project). You use both, together.

> **Tag** = a `key = value` label stuck on a resource (e.g. `project = marketpulse`). It doesn't change how the resource works; it's a field AWS can filter, group, and total costs by. **Resource Group** = a saved page that auto-collects every resource matching a tag.

> **One slug, two spellings — on purpose:** the **cloud slug is `marketpulse`** (one word, no internal hyphen) so the naming pattern `{project}-{env}-{service}` stays unambiguous. The **Git repo stays `market-pulse`** (repo names read better hyphenated and are never parsed by position). Use `marketpulse` for *all* tags and AWS resource names.

### B2.1 — The tag standard (every resource gets these)

```
project      = marketpulse
environment  = dev          # later: dev / staging / prod
owner        = your-name
managed-by   = terraform
```

Set these **once** in Terraform with `default_tags`, so every resource inherits them automatically — you never tag by hand:

```hcl
# infra/main.tf
provider "aws" {
  region = var.region
  default_tags {
    tags = {
      project    = "marketpulse"
      environment = var.environment
      owner      = "your-name"
      managed-by = "terraform"
    }
  }
}
```

### B2.2 — AWS resource naming pattern

```
{project}-{environment}-{service}-{purpose}

marketpulse-dev-bucket-bronze
marketpulse-dev-lambda-batch-ingest
marketpulse-dev-stream-trades
marketpulse-dev-glue-bronze-to-silver
marketpulse-dev-sfn-batch-pipeline
marketpulse-dev-secret-marketdata-apikey
```

**Critical AWS naming rules to respect (the *why*: AWS rejects invalid names):**
- **S3 buckets:** lowercase only, **no underscores**, 3–63 chars, and **globally unique across all of AWS** — so add a short suffix if taken (e.g. `marketpulse-dev-bucket-bronze-9f2`). Use **hyphens**, never underscores or spaces.
- **Lambda / Glue / Step Functions:** hyphens are safe; keep within length limits; no spaces.
- Be **consistent** — pick the pattern above and never deviate. Inconsistency is what makes accounts unreadable.

### B2.3 — Code-level naming (your point: "use standard naming during coding")

Different languages/tools expect different **casing**. Match each tool's convention so your code looks native and professional:

| Where | Convention (case) | Example |
|---|---|---|
| Python variables / functions | `snake_case` | `bronze_bucket`, `def load_prices()` |
| Python classes | `PascalCase` | `class AthenaBackend:` |
| Python constants | `SCREAMING_SNAKE_CASE` | `MAX_RETRIES = 3` |
| Environment variables | `SCREAMING_SNAKE_CASE` | `AWS_ENDPOINT_URL`, `APP_ENV` |
| Terraform resource *local* names | `snake_case` | `resource "aws_s3_bucket" "bronze"` |
| AWS resource *actual* names (the string) | `kebab-case` | `marketpulse-dev-bucket-bronze` |
| Files / folders | `snake_case` or `kebab-case` (be consistent) | `batch_ingest.py` |
| Git repo | `kebab-case` | `market-pulse` |
| dbt models / SQL columns | `snake_case` | `gold_ohlc_hourly`, `close_price` |

*(Full definitions of each case are in the learning plan, Track 0.5.)*

### B2.4 — Where credentials/config reference these names

When you set connection details in code (Section B), reference resources **by their convention-named identifiers** — never hard-code random names:
- Secret names follow the pattern: `marketpulse-dev-secret-marketdata-apikey`.
- Config keys read the project/env: `APP_ENV`, `PROJECT="marketpulse"`, bucket names built from `f"{PROJECT}-{ENV}-bucket-bronze"`.
- **Never** put real credentials in code or in committed `.env` files — only in **Secrets Manager** (cloud) or untracked local env files. `.env.example` shows keys with empty values.

### B2.5 — The "one page per project" payoff

1. **Resource Group:** in the AWS console, create a group filtered to `project = marketpulse` → one auto-updating page listing every MarketPulse resource and its status.
2. **Cost-allocation tag:** in the Billing console, activate `project` as a **cost-allocation tag**. Then **Cost Explorer** shows *"MarketPulse has cost $X"* as a separate number.
3. **Tag-filtered budget:** create a budget scoped to `project = marketpulse` — alerts on *this project's* spend, not the whole account. (Great for your $99 limit.)

**LocalStack note:** LocalStack supports tags and the tagging APIs, so your Terraform/code run identically offline. Resource Groups and Cost Explorer are AWS-console features (no local view) — but local mode is free anyway, so there's nothing to track.

---

## C. Stage-by-stage build guide

Each stage: **objective → why → ordered tasks → definition of done → hand to Claude Code.**

> **Standing rule for every "Hand to Claude Code" instruction below:** it implicitly carries the **Section L engineering standards** — type hints, docstrings, critical tests written *with* the code, global error handling with clear messages, idempotency / check-before-create, critical-event logging, and **root-cause fixes (never temporary hacks)**. **Claude Code also must never run `git commit`/`git push` without your explicit approval (Section M.7), and must never create AWS resources itself — for each one it explains the concept and instructs you, and *you* run any `terraform apply` (Section N).** **And whenever a requirement is ambiguous, or a choice has real cost / security / architecture trade-offs, Claude Code asks you rather than guessing, and states any assumption explicitly (Section H — "ask, don't assume").** Each hand-off ends with a short "Apply Section L standards" reminder so this is never forgotten.

### Stage 0 — Foundation & safety

**Objective:** make the account safe and the project skeleton ready.
**Why:** never create billable resources before guardrails exist.

**Ordered tasks:**
1. **First**, create an IAM admin user (do not use the root account daily); enable MFA on root.
2. Turn on **billing alerts** and the **Cost Explorer**.
3. Create **AWS Budgets**: a $20, $50, and $80 monthly budget with email alerts. (Budget *Actions* come in the Cost-Governance deep-dive.)
4. **If you are on the new AWS Free Plan,** complete the **5 onboarding tasks** to earn **+$100** (launch+terminate an EC2, configure a tiny RDS, deploy a Lambda, test a Bedrock prompt, create a Budget), then **delete** the EC2/RDS. *(You confirmed $100 of base credit. The bonus credits and the safe "account closes at $0" behaviour exist **only on the new Free Plan** — and your June 18 expiry hints you may be on a legacy/promotional account instead. Check **Billing → Credits**. If you are NOT on the new Free Plan, skip this step and treat $99 as a hard ceiling, because overages would be billed.)*
5. Decide and record your **AWS region** (e.g. `ap-south-1`).
6. Create the **GitHub repo** (`market-pulse`) and the folder structure from Section A. **Set up the Git workflow (Section M):** create `main` (release) + `develop` (your working branch), **protect `main`** (require a PR + passing CI), add a `.gitignore`, and adopt **Conventional Commits**.
7. Build the **config + backend-adapter skeleton** (Section B) with placeholder backends.
8. **Set up the local environment — follow `04_INFRASTRUCTURE_SETUP.md`.** This is an explicit Stage 0 deliverable: create the **project-owned emulator** (currently `infrastructure/localstack/` — LocalStack Community: S3 + the Community services we use), with a **healthcheck/readiness gate**, **dummy creds** in `local.env`, and a **gitignored** `volume/`. (Ollama for the local LLM is set up separately.) **Definition of done for this step:** any developer runs `docker compose up` and gets a *healthy* local stack reachable at `http://localhost:4566`. The same setup standards apply to any emulator added later (e.g. Azurite).
9. Set up **Claude Code skills + subagents** (see Section H); the `uv` Python env; and the **quality tooling**: `ruff`, `pytest`, a `pre-commit` hook (Section L.6), and a `Makefile` of common commands (Section J runbook).
10. Adopt the **naming convention + tag standard from Section B2** as your rule for the whole project. Set Terraform `default_tags` now; create the **Resource Group** filtered to `project = marketpulse`; and **activate `project` as a cost-allocation tag** in the Billing console so Cost Explorer can track this project separately.

**Definition of done:** budgets + alerts live; account type confirmed; `main` + `develop` branches exist and `main` is protected; repo + skeleton committed; `pre-commit` + `make test` run; `docker compose up` in `infrastructure/localstack/` starts a **healthy** local stack (S3 reachable at `http://localhost:4566`).

**Hand to Claude Code:** "Scaffold the repo per Section A; implement `config/settings.py` and the empty backend classes per Section B; set up the project-owned `infrastructure/localstack/` LocalStack (Community services + healthcheck + dummy creds, `volume/` gitignored) per Section F; set up ruff, pytest, pre-commit, and a Makefile. Apply Section L standards."

---

### Stage 1 — Storage + batch ingest (Bronze)

**Objective:** land raw historical market data in S3 Bronze on a schedule.
**Why:** you cannot transform or query data you have not stored.

**Ordered tasks:**
1. **First**, define S3 buckets in Terraform: `bronze`, `silver`, `gold` (or one bucket with `bronze/ silver/ gold/` prefixes). Enable encryption (KMS) and block public access. **Name them per Section B2** (e.g. `marketpulse-dev-bucket-bronze`, lowercase + hyphens, add a short suffix if the global name is taken); tags come automatically from `default_tags`.
2. Write a **batch ingestion Lambda** (Python) that calls a free market-data API and writes raw data to `bronze/` as **Parquet** (columnar + compressed = cheap to scan), partitioned by date (`bronze/prices/dt=YYYY-MM-DD/`).
3. Store the API key in **Secrets Manager** (or SSM Parameter Store); the Lambda reads it at runtime with **least-privilege** access. **Follow Section O** — never put the secret value in code or in Terraform state.
4. Add an **EventBridge schedule** (e.g. hourly) to trigger the Lambda.
5. Make ingestion **idempotent** (Section L.3): re-running for the same date must not create duplicates — overwrite that date's partition rather than appending blindly.
6. Test locally first against LocalStack (`APP_ENV=local`), then deploy to AWS.

**Definition of done:** raw Parquet files appear in `bronze/`, partitioned by date, triggered on schedule, with the key in Secrets Manager; re-running the same day produces no duplicates.

**Cost note:** Lambda + EventBridge + S3 here cost pennies.
**Security note:** the Lambda's IAM role gets **only** `s3:PutObject` on the bronze prefix and `secretsmanager:GetSecretValue` on that one secret — least privilege.

**Hand to Claude Code:** "Write the idempotent batch ingestion Lambda (writes Parquet, overwrites date partition) + its Terraform (IAM role least-privilege, EventBridge schedule, Secrets Manager read). Make it run locally against LocalStack via `AWS_ENDPOINT_URL`. Apply Section L standards."

---

### Stage 2 — Catalog + first SQL

**Objective:** make the lakehouse "see" Bronze and run your first Athena query.
**Why:** proves the data is queryable before you invest in transforms.

**Ordered tasks:**
1. **First**, create a **Glue Database** (a logical group of tables) in Terraform.
2. Register the Bronze prices table — either run a **Glue Crawler** once, or define the table manually (cheaper, and fine for a stable schema).
3. Run a test query in **Athena**: `SELECT count(*) FROM bronze_prices`.
4. Set the **Athena query-results S3 location** and a workgroup (lets you cap bytes scanned).

**Definition of done:** Athena returns a row count from Bronze.

**Cost note:** crawlers are $0.44/DPU-hr but finish in a minute or two; the Data Catalog's first 1M objects/requests are free. Define tables manually where the schema is fixed to skip crawler cost.
**Local twin:** DuckDB reads the same Parquet directly — `SELECT count(*) FROM read_parquet('s3://.../bronze/prices/**')`.

**Hand to Claude Code:** "Add Terraform for a Glue database + a manually-defined Bronze table + an Athena workgroup with a bytes-scanned limit. Provide a test query. Apply Section L standards."

---

### Stage 3 — Transform to Silver + Gold (learn BOTH SQL ELT and PySpark)

**Objective:** clean Bronze → Silver, then build business marts → Gold, as Iceberg tables, with data-quality tests. **You'll learn both transform styles: SQL-based ELT (primary) and PySpark on Glue (also fully learned).**
**Why:** clean Gold tables are what the AI assistant and dashboards depend on.

> **Why two styles? (your request to learn both Athena and Glue Spark.)** We use the cheaper, more reliable one as the *primary* path and build the other as a *parallel learning* path:
> - **Primary (cheap, reliable):** ELT in SQL. **Athena `CREATE TABLE AS SELECT` (CTAS)** and `INSERT INTO`, plus **dbt-athena**, write **Apache Iceberg** tables natively and serverlessly — no Spark cluster needed. *(Why not a Glue Python Shell job for Iceberg? A plain Python Shell job can't reliably write Iceberg — that needs Spark or a SQL engine. SQL is the right cheap tool here. This corrects an earlier draft of the plan.)*
> - **Learning path (also build it):** a **Glue Spark job written in PySpark** that does the same Bronze→Silver transform, so you learn Spark and the DataFrame API hands-on.

> **Jargon:** **PySpark** = the Python API for Apache Spark, a distributed engine for processing large datasets in parallel. **Parquet** = the columnar, compressed file format your Iceberg tables are stored in — it's why Athena scans only the columns you ask for, keeping cost tiny.

**Ordered tasks:**
1. **First (primary path)**, build **Bronze→Silver** as SQL: an Athena CTAS / `INSERT` (or a dbt-athena model) that fixes types, removes duplicates, and writes **Silver** as an **Iceberg** table (stored as Parquet).
2. Add **data-quality checks** on Silver (no null timestamps, prices > 0) using **dbt tests** (or Great Expectations). **Fail the pipeline if checks fail.**
3. Build **Silver→Gold** marts with **dbt** (hourly OHLC, rolling averages, volatility); dbt runs its tests here too.
4. **(Learning path)** Write a **Glue Spark (PySpark) job** that reproduces Bronze→Silver using Spark DataFrames + the Iceberg connector. Run it **once or twice** to learn Spark, then rely on the SQL path to save cost. Document the cost difference.
5. Decide a **partitioning strategy** (partition by date) and keep file sizes reasonable (Iceberg **compaction** — merging many small files into fewer big ones) — this is what keeps Athena scans, and cost, tiny.

**Definition of done:** Gold Iceberg tables exist (built by the SQL/dbt path), dbt tests pass, an Athena query on a Gold mart returns sensible numbers; the PySpark job runs and produces an equivalent Silver table.

**Cost note:** **this stage is your main cost lever.** The **SQL/Athena path is cheapest** ($5/TB scanned — pennies with partitioning). **Glue Spark** is ~$0.44/DPU-hr (min 2 DPU ≈ $0.88/hr) — fine for a few learning runs, but don't make it your everyday path.
**Local twin:** the dbt models run against **DuckDB** locally (free); the PySpark job runs on **local Spark** (free) reading the same Parquet.

**Hand to Claude Code:** "Build Bronze→Silver as an Athena CTAS / dbt-athena Iceberg model (primary), dbt Gold marts with tests, and partitioning; AND a parallel Glue PySpark job that reproduces Bronze→Silver for learning. Make dbt switch between Athena and DuckDB by profile. Apply Section L standards."

---

### Stage 4 — Streaming path

**Objective:** add a live feed → Kinesis → Lambda → Bronze, then fold it into the lakehouse.
**Why:** layered on after the batch backbone works, so you debug one thing at a time.

**Ordered tasks:**
1. **First**, write the **producer**: a small Python program that connects to a free public exchange WebSocket and pushes trade records into **Kinesis**. **Containerize it with Docker** (this is a natural Docker fit).
2. Create the **Kinesis stream** in Terraform (start with **1 shard**, on-demand or provisioned). **Name it per Section B2** (e.g. `marketpulse-dev-stream-trades`); tags inherit automatically.
3. Write a **consumer Lambda** triggered by Kinesis that batches records and writes them to `bronze/trades/`. Add a **dead-letter queue (DLQ)** so repeatedly-failing records are parked, not lost (Section L.5).
4. Extend the Silver/Gold transforms to include streaming trades (e.g. a near-real-time "latest price" Gold table).
5. **Cost control:** run the producer **only in short test windows** (a script `run-stream-window.sh` that starts the container, runs N minutes, stops it). Never leave the stream running idle.

**Definition of done:** running the producer for a few minutes produces trade files in Bronze that flow into a Gold "latest price" table; failing records land in the DLQ, not lost.

**Cost note:** ~$0.015/shard-hour. One shard for a few hours of testing ≈ a couple of dollars. **Stop the stream when not testing.**
**Local twin:** Kinesis runs in **free LocalStack**; the producer container points at LocalStack via `AWS_ENDPOINT_URL`.

**Hand to Claude Code:** "Write the Dockerized Kinesis producer (reads a public WebSocket), the consumer Lambda with a DLQ, the Terraform (1-shard stream, IAM, event-source mapping), and a `run-stream-window.sh` that runs the producer for a fixed number of minutes. Apply Section L standards."

---

### Stage 5 — RAG assistant (moderate-to-high)

**Objective:** answer plain-English questions using **both** documents **and** live Gold metrics.
**Why:** the assistant needs Gold data and documents to already exist.

> You know RAG deeply, so this is kept concise. We build a **solid, moderate-to-high** RAG — good chunking, real embeddings, and a "hybrid" twist where the assistant can also pull live numbers. Advanced upgrades (re-ranking, query rewriting, evaluation harness, multi-hop) are noted as your *future* work, not built now.

**Ordered tasks:**
1. **First**, build a **document ingestion** step: pull market news/filings to `bronze/docs/`, clean text, and **chunk** it. Make it **idempotent** (re-ingesting the same document doesn't duplicate chunks).
2. Create **embeddings** with **Bedrock** (e.g. a Titan/embeddings model); store vectors in a **FAISS index file in S3** (cheap, serverless). The `LLMClient`/embeddings calls go through the adapter (Bedrock vs Ollama).
3. Build the **assistant Lambda**: on a question, retrieve top-k chunks from FAISS, **and** (the hybrid twist) if the question needs numbers, call the `QueryEngine` to run a templated Athena query on Gold, then pass both to the **Bedrock LLM** to compose the answer.
4. Expose it simply — a CLI or a tiny API endpoint (optionally a small Streamlit UI as Stretch).

**Definition of done:** asking e.g. "What moved Bitcoin this week and by how much?" returns an answer that cites a news chunk *and* a real number from Gold.

**Cost note:** Bedrock is pay-per-token; your text volume is small → a few dollars at most. **Do not use OpenSearch Serverless** as the vector store (it can cost a lot) — FAISS-in-S3 is near-free.
**Local twin:** **Ollama** replaces Bedrock for both embeddings and the LLM; FAISS runs locally. Swap by `APP_ENV`.

**Future upgrades (you, later):** add a re-ranker, query rewriting, an evaluation set, and conversation memory.

**Hand to Claude Code:** "Build idempotent doc ingestion + chunking, Bedrock embeddings → FAISS-in-S3, and a hybrid assistant Lambda that retrieves chunks and optionally runs a templated Athena query, then answers via the LLMClient adapter. Apply Section L standards."

---

### Stage 6 — Orchestration + observability

**Objective:** tie the batch pipeline into one reliable flow; see what's happening.
**Why:** orchestration connects finished pieces with ordering and retries.

**Ordered tasks:**
1. **First**, model the batch flow as a **Step Functions** state machine: ingest → Bronze→Silver → dbt Gold → data-quality gate → (notify on failure via SNS).
2. Trigger the state machine on a schedule with **EventBridge**.
3. Add **CloudWatch**: a dashboard (records ingested, job durations, failures) and **alarms** (e.g. alert if a job fails or runs too long).
4. Add **critical-event logging** across Lambdas (JSON logs to CloudWatch Logs) per Section L.4 — milestones, external-call outcomes, retries, and errors with context; not per-record spam.

**Definition of done:** one EventBridge trigger runs the whole batch pipeline end-to-end with retries; a CloudWatch dashboard shows it; a failure sends an alert.

**Cost note:** Step Functions + EventBridge + CloudWatch are cheap. **We deliberately avoid MWAA (~$350/month).**
**Local twin:** Step Functions runs in LocalStack; or use **Dagster** locally to learn a real orchestrator (Stretch).

**Hand to Claude Code:** "Write the Step Functions definition (ingest→silver→gold→quality-gate→SNS-on-fail), its EventBridge schedule, a CloudWatch dashboard, failure alarms, and JSON critical-event logging — all in Terraform. Apply Section L standards."

---

### Stage 7 — CI/CD + containerization polish

**Objective:** automate testing and deployment; finish containerization.
**Why:** CI/CD is most valuable once there's real code and infra to protect.

**Ordered tasks:**
1. **First**, the **CI** workflow (`ci.yml`, runs on every push): set up Python, install deps, run **linter** (ruff) + **unit tests** (moto-mocked) + spin up **LocalStack** for **integration tests**, then `terraform fmt -check` and `terraform plan`. **The job must fail and block the merge/deploy if lint or any critical test fails** (Section L.6).
2. The **CD** workflow (`deploy.yml`, runs on `main` with manual approval): `terraform apply`. Store AWS credentials as **GitHub Secrets** (or use OIDC — see CI/CD deep-dive).
3. **Containerize** the producer (already done in Stage 4) and push the image to **ECR**; optionally run it on **ECS Fargate** in short windows. Containerize the optional dashboard.
4. Add a **status badge** to the README.

**Definition of done:** pushing code runs the full CI; a failing lint/test blocks deploy; merging to `main` deploys after approval; the producer image is in ECR.

**Cost note:** GitHub Actions is free for public repos; ECR storage is cents; Fargate billed per second — run briefly.
**Local twin:** the same tests run locally via docker-compose + LocalStack (and via the pre-commit hook before each commit).

**Hand to Claude Code:** "Write `ci.yml` (ruff + moto unit tests + LocalStack integration tests + terraform plan, failing the build on any lint/critical-test failure) and `deploy.yml` (terraform apply with manual approval). Add a Dockerfile for the producer and an ECR push step. Apply Section L standards."

---

### Stage 8 — Documentation + handover

**Objective:** produce the guides, diagram, cost report, resume write-up; **export everything before June 18**.
**Why:** the account may close when credits end — capture proof of work.

**Ordered tasks (start the notes early, finish here):**
1. Write the **Developer Guide** and **User Guide** (outlines in Section J), including the **runbook (commands reference)** and the **idempotency design** explanation.
2. Create the **architecture diagram** (e.g. with a diagram tool or Mermaid) and `docs/architecture.md` with key design decisions.
3. Produce the **cost report** from Cost Explorer (what each service cost, how you stayed under budget).
4. Take **screenshots** (console, dashboards, a sample assistant answer) for your portfolio.
5. **Export**: push all code/IaC to GitHub, download sample data, save the cost report and screenshots locally.
6. **Tear down** billable resources with `terraform destroy` (or `make destroy`) before the deadline.

**Definition of done:** both guides + diagram + cost report committed; screenshots saved; everything reproducible from GitHub after the account closes.

**Hand to Claude Code:** "Generate the Developer Guide and User Guide per Section J outlines from the repo (including the runbook and idempotency design), plus a Mermaid architecture diagram. Apply Section L standards."

---

## D. Data model (Bronze / Silver / Gold)

- **Bronze:** raw, append-only, partitioned by ingest date, stored as **Parquet**. Never edited. (Lets you re-process if logic changes.)
- **Silver:** typed, de-duplicated, validated. One row per event/price, clean columns, Iceberg table.
- **Gold:** business marts, e.g.
  - `gold_ohlc_hourly` (open/high/low/close + volume per symbol per hour),
  - `gold_volatility` (rolling std-dev),
  - `gold_latest_price` (near-real-time, fed by the stream),
  - `gold_news_sentiment` (optional, links to RAG docs).

Keep Gold **partitioned by date** and files reasonably sized; this is the single biggest control on Athena cost.

---

## E. Security & governance (your point #1 — learn it here)

This is where you build production security habits. Treat it as a *track running through every stage*, not a one-off.

- **IAM (Identity and Access Management) — least privilege.** Every component (each Lambda, Glue job, the producer) gets its **own role** with **only** the permissions it needs (e.g. write to *one* prefix, read *one* secret). Never reuse a broad admin role for app components. *This is the #1 thing interviewers probe.*
- **Resource policies.** S3 bucket policies block public access; KMS key policies limit who can decrypt; least-privilege everywhere.
- **KMS (Key Management Service) — encryption.** Encrypt S3 data and secrets with a KMS key. Understand the difference between encryption *at rest* (stored) and *in transit* (TLS).
- **Secrets Manager.** All API keys/credentials live here, never in code or env files committed to git. `.env.example` shows keys with **no values**. *(Full standard practices — AWS auth credentials, the Terraform-state secret trap, and secret scanning — are in **Section O**.)*
- **Lake Formation — data governance.** Use it to grant **table/column-level permissions** on your lakehouse (e.g. a "read-only analyst" role can see Gold but not Bronze). This teaches real data-governance.
- **Tagging policy.** Every resource tagged `project = marketpulse`, plus `environment`, `owner`, `managed-by`. Tags drive cost reports *and* governance.
- **Organizations / SCPs (concept).** You won't set up an Org (it would expire your Free Tier credits immediately — avoid), but understand what **Service Control Policies** are: account-wide guardrails. Learn the concept; don't enable it here.

**Licensing (your point #1).** Know the licenses of the open-source tools you ship and the AWS terms you rely on:
- Apache Iceberg, Apache Spark (PySpark), Airflow, dbt-core → **Apache-2.0** (permissive).
- DuckDB, FAISS → **MIT** (permissive).
- **LocalStack** → moved to **BSL** (Business Source License) — *not* fully open source anymore; the free Community tier needs an account. This is exactly why we use open-source twins for the heavy services.
- **AWS credits/Free Plan** → governed by the **AWS Promotional Credit Terms** and **Free Tier Terms** (e.g. joining an Organization voids credits). Read these once; they affect your budget.

---

## F. Local infrastructure & containerization (your point #4 — where Docker fits)

Docker shows up naturally in several places:

1. **Local environment (project-owned)** — a Docker-based **service emulator** runs the cloud services locally so the whole platform works offline and for free. For MarketPulse that's **LocalStack** (emulates AWS), kept in `infrastructure/localstack/` *inside the repo* — a new developer just runs `docker compose up`, no global install. *(Ollama for the local LLM runs separately.)*
2. **Streaming producer** — packaged as a Docker image (Stage 4), pushed to **ECR**, optionally run on **ECS Fargate** (Stage 7).
3. **Optional dashboard** (Streamlit) — a small container.
4. **Glue / Lambda custom images (optional, advanced)** — both can run from container images if you need special libraries.
5. **CI** — GitHub Actions spins up the emulator container to run integration tests.

### F.1 — Standard practices for any local service emulator

> **Service emulator** = a tool (usually a Docker container) that runs cloud services on your machine so you develop and test for free, then switch to the real cloud by config. **LocalStack** emulates **AWS** (our case, endpoint `http://localhost:4566`); **Azurite** emulates **Azure** storage; others exist for other clouds.

**These rules apply to *every* emulator the project adopts — LocalStack now, and Azurite or any similar tool if a future need arises.** They are not LocalStack-specific:

- **Project-owned, not global.** Keep each emulator's setup *in the repo* (e.g. `infrastructure/<emulator>/`) so it's version-controlled and reproducible. Onboarding is one command (`docker compose up`) — no teammate has to install it manually.
- **Use the free edition; know the free/paid boundary.** Pick the free tier and document what it does *not* cover. *(Example: LocalStack Community is free but **Glue and Athena are Pro-only ~$45/mo**, so MarketPulse runs those locally via **DuckDB** and **PySpark** instead — the backend-adapter pattern, Section B. Azurite, by contrast, is fully free.)*
- **Dummy credentials, never real; switch by config.** Emulators accept placeholder creds — set them explicitly (for LocalStack: `AWS_ENDPOINT_URL=http://localhost:4566`, `AWS_ACCESS_KEY_ID=test`, `AWS_SECRET_ACCESS_KEY=test`, `AWS_DEFAULT_REGION=us-east-1`). **Never point real cloud keys at an emulator.**
- **Critical check before use (fail fast).** "Container up" ≠ "services ready." Gate dependents on the emulator's health: a compose `healthcheck` + `depends_on: condition: service_healthy`, and a readiness poll in scripts/tests that exits with a clear message if it isn't ready. *(LocalStack: `GET /_localstack/health`; Azurite: a probe to its blob endpoint.)*
- **Treat local-infra reliability + bug-fixing as a first-class project feature.** When an emulator misbehaves (a service won't start, behaves differently from the real cloud, a port clashes), **fix it at the root** (Section L: no band-aids) and **record the issue + fix in the runbook** (Section J) so the next developer doesn't lose time.
- **Gitignore runtime state.** Each emulator writes runtime data (caches, certs, volumes) — never commit it.

**Adding another emulator later (e.g. Azurite):** follow the *same* pattern — a project-owned compose under `infrastructure/<name>/`, dummy creds, a health gate, gitignored state, and a section in the setup file + runbook. No new philosophy, just the same checklist applied.

> 📄 **Full step-by-step setup (prerequisites, run commands, credentials, health checks, troubleshooting) lives in its own file: `04_INFRASTRUCTURE_SETUP.md`.** The main plan points here so a developer sets up the whole local environment in one place. **Do this at Stage 0, before building.**

**Hand to Claude Code:** "Set up local infrastructure per `04_INFRASTRUCTURE_SETUP.md` and the F.1 standards: project-owned compose, free edition (document the free/paid boundary), dummy creds, a healthcheck + readiness gate before anything runs against it, gitignored runtime state, and any issue + fix logged in the runbook. The same standards apply to any emulator added later (e.g. Azurite). Apply Section L standards."


**Learn (basics → applied):** images vs containers, `Dockerfile`, layers/caching, `docker compose`, environment variables, volumes, and `.dockerignore`. Then ECR (registry) and Fargate (run without managing servers).

---

## G. CI/CD from basics (your point #3 — GitHub Actions)

> Start from zero. CI/CD = **Continuous Integration** (every code change is automatically built and tested) + **Continuous Delivery/Deployment** (changes that pass are automatically released). The goal: catch mistakes early and deploy safely and repeatably.

**GitHub Actions concepts (learn in this order):**
1. **Workflow** — a YAML file in `.github/workflows/` that runs on an **event**.
2. **Event/trigger** — e.g. `on: push`, `on: pull_request`, or manual `workflow_dispatch`.
3. **Job** — a unit that runs on a **runner** (a fresh virtual machine GitHub gives you).
4. **Step** — a single command or a reusable **action** (e.g. `actions/checkout`).
5. **Secrets** — store credentials in repo settings; never in the YAML.
6. **Caching & matrix** — speed up (cache deps) and test across versions (matrix).

**Two pipelines you will build (mapped to the branches in Section M):**
- **`ci.yml` (on push/PR to `feature/*` and `develop`):** checkout → set up Python → install deps → **ruff** (lint) → **pytest** (moto-mocked unit) → start **LocalStack** → integration tests → `terraform fmt -check` + `terraform plan`. *This proves the change is safe before it reaches `develop`.*
- **`deploy.yml` (on merge to `main`, with manual approval):** checkout → configure AWS creds → `terraform apply`. *`main` is the release branch, so merging to it is what ships to AWS.*

**Quality gates (your rule — tests must pass before runs):**
- CI must **fail and block the merge/deploy** if `ruff` or any **critical test** fails. A green check is **required** to deploy.
- Add a local **pre-commit hook** (`.pre-commit-config.yaml`) so `ruff` + critical tests also run **before every commit** — catching problems before they even reach GitHub. (Defined in Section L.6.)

**Security upgrade (learn it):** instead of long-lived AWS keys in GitHub Secrets, use **OIDC** — GitHub gets a short-lived token from AWS for each run via an IAM role. More secure; great resume point.

---

## H. Claude Code setup (lean, per your token concern)

- **Skills (markdown instruction files Claude Code loads only when relevant — cheap):**
  - `engineering-standards`: **enforce Section L on all code** — PEP 8 + type hints + docstrings; **root-cause fixes, never band-aids** (no bare `except: pass`); **idempotency + check-before-create/edit**; **critical-event logging** (detailed but not noisy); **error handling with clear messages + retries + DLQ**; and **critical tests written with the code, passing before any run/deploy**.
  - `aws-cost-guard`: before any `terraform apply` or new resource, check the price and confirm it fits the budget; warn about cost traps (MWAA, Redshift, NAT, idle resources).
  - `iac-terraform`: your Terraform conventions (module layout, naming, tagging, least-privilege IAM).
  - `data-quality`: rules for pipeline tests (null checks, ranges, row-count deltas) and dbt test patterns.
- **MCP servers:** keep to **zero or one**. If any, a single AWS docs/pricing lookup. (You asked to minimise MCP to save tokens — agreed.)
- **`CLAUDE.md` (project memory — set up first):** Claude Code auto-loads `./CLAUDE.md` at the start of every session, so put your standing rules there: the **naming + tag standard (B2)**, the **engineering standards (L)**, the **branch model + commit convention (M)**, and — explicitly — **"never run `git commit`/`git push` without my approval (M.7)"**. This is also a token saver (you don't re-type rules each session). Keep it short; it can `@import` longer files (e.g. `@docs/git-instructions.md`).
- **Permissions (enforce the commit + AWS-provisioning rules):** Claude Code is **human-in-the-loop by default** (it shows you each file diff and bash command before running). Add **`ask` rules** for `Bash(git commit:*)`, `Bash(git push:*)`, `Bash(terraform apply:*)`, `Bash(terraform destroy:*)`, and mutating `Bash(aws:*)` commands, so commits *and* AWS changes always prompt you. Never put these on the `allow` list, and never use `--dangerously-skip-permissions` on your real machine. (`terraform plan` and read-only `aws` commands are fine for Claude to run.)
- **Subagents:**
  - `cost-reviewer`: reviews any infra change for cost impact *before* deploy.
  - `infra-reviewer`: reviews IAM/security and Terraform for least-privilege and mistakes.
- **Collaboration — ask, don't assume (keep me in the loop):** When a requirement is **ambiguous**, when there are **multiple valid approaches with real trade-offs**, when a choice affects **cost, security, or architecture**, when Claude Code is **blocked/unsure**, or when it's about to **make an assumption you might disagree with** or do something **hard to reverse** → it **stops and asks you**, rather than guessing. It **states assumptions explicitly** ("I'm assuming X — confirm?") and asks **one focused question at a time**. *Balance:* ask for the **consequential** decisions; proceed autonomously on the routine ones (under the standing rules). Put this rule in `CLAUDE.md` so it's always on.
- **Proactive skill & agent usage (Claude Code drives this — the user does not invoke them manually):** Claude Code should **automatically use the relevant skill** for each task (`aws-cost-guard` whenever infra/cost is involved, `iac-terraform` when writing Terraform, `data-quality` when building transforms/tests), and **proactively run the `cost-reviewer` and `infra-reviewer` subagents before any deploy / before handing over a `terraform apply`**. The user may not know the mechanics, so whenever an action is needed from them — create an agent via `/agents`, reload the window, approve a commit, run `terraform apply` — **Claude Code explains exactly what to click/type, step by step.**
- **Follow the plan faithfully — focus, no over-engineering, no skipping.** The plan files (`00`–`04`, especially `02`) are the spec. Build exactly what each stage specifies: **don't add** services, abstractions, or features beyond it (no over-engineering), and **don't skip** necessary steps (critical tests, least-privilege IAM, error handling, idempotency, logging, teardown). Read the plan with focus and apply it intelligently to the specifics; if something in the plan seems wrong or missing, **raise it and ask** — never silently deviate. Put this rule in `CLAUDE.md`.
- **Plan-first per stage (default for anything new, costly, or AWS-touching):** Before writing code for a stage or a non-trivial task, Claude Code first proposes a **short plan** — its approach, the files it will create/change, and any open questions — using the relevant section of `02` as the source spec, and **waits for the user's approval** before implementing. This is a *lightweight* plan (use Claude Code's **Plan Mode**), **not** a heavy spec document — the plan files already *are* the spec, so don't re-derive them. **Direct implementation (skip the plan step) is acceptable only for small, obvious, low-risk changes** (rename, add a test, tweak config). *Why:* a wrong approach caught in a plan is free; the same mistake caught after `terraform apply` can cost real credit — and reviewing the approach first is how the user learns. Put this rule in `CLAUDE.md`.
- **Day-0 Claude Code setup (one-time):** (1) install the official Anthropic **Claude Code VS Code extension**; (2) copy the provided **`.claude/skills/`** folder into the repo root; (3) create the two subagents with the **`/agents`** command (project scope) using the provided agent files — directly-placed agent files otherwise need a **session restart** to load; (4) create **`CLAUDE.md`** at the repo root; (5) add the **`ask` permission rules** (git + `terraform apply`/`destroy` + mutating `aws`); (6) verify with `/agents`.
- **Workflow habit:** build locally (`APP_ENV=local`) → pre-commit + tests pass → cost-reviewer + infra-reviewer → **you review the diff and approve the commit** → **you run `terraform apply` (Claude explains it first; see Section N)** — and Claude **asks you at any ambiguous or consequential decision** along the way. This catches expensive errors before they hit AWS.

---

## I. Cost governance (deep-dive — protect the $99)

**Layer 1 — Budgets (alerting):** create budgets at **$20, $50, $80** with email alerts at 80% and 100% of each.

**Layer 2 — Budget Actions (automatic protection):** a **Budget Action** can *automatically* respond when a threshold is crossed — e.g. apply a restrictive IAM policy that blocks creating new expensive resources, or stop targeted resources. Set one at ~$80 as a safety net.

**Layer 3 — Cost Explorer + tags:** group costs by your `project`/`environment` tags; check the dashboard **daily** during the build.

**Layer 4 — habits:** stop the Kinesis stream when idle; delete the Stage-0 EC2/RDS after onboarding; no NAT Gateway (use public subnets or VPC endpoints for a learning project); `terraform destroy` anything you're done testing.

**Account-type rule ($99 is your hard ceiling either way):**
- **Treat $99 as a hard cap on all AWS spend, regardless of account type.** You have $100 credit; we cap at $99 to keep a safety margin.
- **New Free Plan** → if you exceed credits, AWS **shuts the account down** (no bill), so the cap is a safety habit.
- **Legacy / Paid Plan** → you **will be billed** at standard rates after $100, so the cap is **mandatory** — keep Budget Actions + daily checks on. (Your June 18 expiry suggests this may be your case — confirm on Billing → Credits.)

**Teardown checklist (before June 18):** export code + data + cost report + screenshots → `terraform destroy` → confirm in Cost Explorer that nothing is still running.

---

## J. Documentation deliverables (your point #6)

**Developer Guide (`docs/DEVELOPER_GUIDE.md`) — for engineers:**
1. Architecture overview + the diagram.
2. Prerequisites (AWS CLI, Terraform, Docker, uv, Ollama).
3. Local setup: `docker compose up`, `APP_ENV=local`, run a pipeline end-to-end offline.
4. AWS setup: configure creds, `terraform apply`, run on cloud.
5. The backend-adapter design (how/why to switch cloud↔local).
6. Repo layout, **coding standards (Section L)**, how to run tests, how CI/CD works.
7. **Runbook — commands reference (your point D1):** every common task and the **exact command**, ideally via the `Makefile`. For example:
   - `make install` — set up the Python env.
   - `make test` — run lint + critical tests (same as the pre-commit gate).
   - `make deploy-local` / `make run-local` — start the local stack and run a pipeline offline.
   - `make deploy` — `terraform apply` to AWS (after approval).
   - `make stream MINUTES=10` — run the streaming producer for a fixed window.
   - `make destroy` — tear everything down.
8. **Idempotency design (your point D2):** explain *how* re-running is safe — deterministic keys, date-partition overwrite, Iceberg `MERGE`, Terraform's own idempotency — so a reviewer sees you designed for safe re-runs.
9. How to add a new data source / a new Gold mart.
10. Cost guardrails and the teardown procedure.

**User Guide (`docs/USER_GUIDE.md`) — for users of the platform:**
1. What the platform does, in plain words.
2. How to ask the **RAG assistant** questions (with example questions + answers).
3. How to run a sample **SQL query** on Gold (and read the result).
4. How to read the **dashboard** (what each chart means).
5. Limits (data freshness, supported symbols) and where to get help.

> A short **idempotency + key-decisions** note also lives in `docs/architecture.md`, and a one-paragraph summary in the README, so the design intent is visible at a glance.

---

## K. Definition of "production-shaped" (so you know when to stop)

A component is done when it: runs from **IaC**, has **critical tests that pass**, uses **least-privilege IAM**, has **proper error handling + critical-event logging**, is **idempotent (safe to re-run)**, is **documented (incl. its command in the runbook)**, switches **cloud↔local** by config, and has a **known cost**. That bar — not "enterprise-perfect" — is the right target for this 12-day portfolio build.

---

## L. Engineering standards (apply to every stage)

> **Standing rules for all coding**, enforced by the `engineering-standards` Claude Code skill (Section H) and referenced by every "Hand to Claude Code" line. The goal: code that is correct, debuggable, and safe to re-run — production-shaped, not student-grade.

### L.1 — Coding best practices
- **Style:** follow **PEP 8**; auto-format with `ruff format`; lint with `ruff`.
- **Type hints** on every function signature; **docstrings** on every module, class, and public function (what it does, args, returns, raises).
- **Small, single-purpose functions**; no copy-paste duplication (the **DRY** principle — *Don't Repeat Yourself*).
- **No hard-coded values** (bucket names, regions, model IDs) — read them from `config/settings.py` / env; build names from the project slug (Section B2.4).
- **No secrets in code** — Secrets Manager (cloud) or untracked local env only. *(Full secrets handling: **Section O**.)*

### L.2 — Root-cause fixes, never band-aids (your rule)
- When something breaks, **diagnose the underlying cause** and fix it at the source so it cannot recur — a **global/systemic fix**, not a local patch.
- **Forbidden:** silencing errors with bare `except: pass`; broad `except Exception` that hides bugs; hard-coded "magic" workarounds; commenting out failing tests; `sleep()` hacks to mask timing bugs.
- If a proper fix is bigger than the moment allows, write a `# TODO:` stating the **root cause** and open an issue — never ship a hidden hack.
- *Instruction to Claude Code:* "State the root cause before fixing; prefer a core/global solution over a temporary one."

### L.3 — Idempotency & check-before-create (your rule)
> **Idempotent** = running the same operation twice gives the same result — no duplicates, no damage.
- **Before creating or editing a file / folder / S3 object, check whether it already exists**, then create, skip, or overwrite *deliberately* — never blindly.
- Ingestion must be **safe to re-run**: re-loading the same day must not create duplicate rows (deterministic keys / overwrite the date partition / Iceberg `MERGE`).
- Never create the same resource by hand *and* in Terraform (Terraform is already idempotent).
- **Document the idempotency design** in `docs/architecture.md` (Section J, point 8).

### L.4 — Critical-event logging (your rule: "critical things, with detail, not too much")
- Use Python's `logging` (JSON format in Lambdas) — **never `print`**.
- **Log the critical events in every module, with useful detail, and stay quiet otherwise:** start/finish of a key operation **with counts** (e.g. "wrote 12,304 rows to silver_prices"); the outcome of every external call (API, S3, Athena, Bedrock); every retry; and every error (with **context + the cause**).
- **Do not** log secrets, full payloads, or per-record spam.
- Levels: `INFO` for milestones, `WARNING` for recoverable issues, `ERROR` for failures.

### L.5 — Error handling with clear messages
- A shared `src/common/errors.py` defines **custom exceptions** (e.g. `IngestionError`, `ConfigError`, `TransformError`).
- Wrap external calls; on failure, raise with a **clear, contextful message** — e.g. `"Failed to read secret 'marketpulse-dev-secret-marketdata-apikey': <cause>"` — never a bare stack trace with no context.
- **Retries with exponential backoff** for network/throttling errors (e.g. the `tenacity` library).
- **Dead-letter queue (DLQ)** on Lambda/Kinesis consumers so repeatedly-failing records are parked for inspection, not lost.
- **Fail fast** on programmer errors (bad config); **degrade gracefully** on expected external hiccups (one bad record routes to the DLQ instead of killing the whole batch).

> **DLQ (dead-letter queue)** = a holding queue for messages/records that keep failing, so you can inspect them later instead of losing them silently.

### L.6 — Testing discipline ("critical tests before runs")
- Every component ships with **unit tests**; AWS calls are **mocked with `moto`** (a library that fakes AWS in memory) so tests run free and offline.
- **Critical tests must pass before you run or deploy** — enforced **locally by the pre-commit hook** and **in CI** (Section G). A failing critical test **blocks** the deploy.
- **Critical tests include:** config loads correctly; each backend adapter returns the expected shape; a transform produces the expected rows; ingestion is **idempotent** (re-run = no duplicates).
- **Integration tests** run against **LocalStack** (free).
- *Instruction to Claude Code:* "Write tests *with* the code, not after; do not mark a stage 'done' until its critical tests pass."

---

## M. Git workflow — branches & commit messages (your development practice)

> **Why a branching strategy?** A *branch* is a parallel copy of your code where you work without breaking the main code. Used well, branches keep your history clean, let CI test changes in isolation, and show reviewers you work like a team engineer. **Cost: $0** — Git and GitHub (public repos) are free; nothing here touches AWS.

> **Honest note (solo trade-off):** this model is slightly more than a solo developer strictly needs. We use it on purpose — to *learn* the professional workflow and to impress reviewers. Keep it lightweight: you may review and merge your own Pull Requests.

> **Jargon:** **commit** = a saved snapshot of changes with a message. **merge** = combine one branch's changes into another. **Pull Request (PR)** = a request to merge a branch, where CI runs and you review the diff first. **Branch protection** = rules on a branch (e.g. "require a PR and passing CI before merge").

### M.1 — The branches

| Branch | Purpose | Who / when | Triggers |
|---|---|---|---|
| **`main`** | **Production / release-target branch** — always deployable; the **deploy (CD) pipeline** ships it to AWS; each release is **tagged** here (e.g. `v1.0.0`). **Protected.** | Updated only by merging a `release/*` (or `develop`) branch when stable. | **`deploy.yml`** on merge to `main`. |
| **`release/<version>`** | **Release-preparation branch** (e.g. `release/1.0.0`) — a quiet place to finalise a release: last bug fixes, version bump, changelog. **Temporary** (deleted after merge). | Cut from `develop` when a milestone is ready to ship. | **`ci.yml`** on push/PR. |
| **`develop`** | **Integration branch** — your day-to-day branch; a mix of module work and bug-fix commits. | **You, for normal changes.** | **`ci.yml`** runs on push/PR. |
| **`feature/<module>`** | **Module-specific** work, branched off `develop`. | One per module: `feature/ingestion`, `feature/streaming`, `feature/transform`, `feature/rag`, `feature/orchestration`, `feature/cicd`. | **`ci.yml`** runs on push/PR. |
| **`fix/<short-name>`** | A focused bug fix (optional; small fixes may go straight on `develop`). | When a bug needs isolation. | **`ci.yml`**. |

### M.2 — The flow (how a change travels)

```
feature/<module> ─(PR,CI)─► develop ─(cut)─► release/1.0.0 ─(PR + tag v1.0.0)─► main ─► deploy.yml ─► AWS
       ▲                        ▲                   │
  module work             normal changes      final fixes only ──(merge back)──► develop
  + commits               + bug fixes (you)
```

1. Start a module → `git checkout -b feature/streaming` (off `develop`).
2. Commit as you go (Conventional Commits, below). CI runs on the branch.
3. Open a **PR into `develop`**; merge when CI is green.
4. `develop` accumulates modules + fixes (your day-to-day working branch).
5. When a milestone is ready to ship → **cut `release/1.0.0` from `develop`**; here do **only** final bug fixes + version bump + changelog (no new features).
6. **PR `release/1.0.0` into `main`**, then **tag** it: `git tag v1.0.0`. Merge the release branch **back into `develop`** too, then delete it.
7. Merge to `main` → **`deploy.yml`** deploys to AWS (after the manual approval from Section G).

### M.3 — Release branches & tags (and why not `release/main`)

> **You asked about a release branch — here's the standard practice, plus a correction worth knowing.**

- **`main` is already your release target — you do *not* rename it.** A name like `release/main` is **non-standard** because (1) the **slash marks a *namespace* of many branches** (`feature/*`, `release/*`), so `release/...` implies several versioned branches, not one permanent branch; and (2) `main` is the expected default that GitHub, tooling, and other engineers assume is production — renaming it adds friction.
- **The standard release branch is temporary and *versioned*:** `release/1.0.0`, `release/1.1.0`, … Cut one from `develop` when a release is near, stabilise it (only bug fixes + version bump + changelog — **no new features**), then merge into `main`, **tag** the release, and merge back into `develop`. This is the **Git Flow** release branch. *(For this project you'll likely create just one — `release/1.0.0`.)*
- **Tags record what was released.** `git tag v1.0.0` on `main` is an **immutable snapshot** marking exactly what shipped. **Tags — not branch renames — are how releases are recorded.** Later you can deploy or roll back to a specific tag.

> **Honest trade-off (solo / 12-day project):** full Git Flow has three layers (`feature` → `develop` → `release` → `main`) — more ceremony than a solo project strictly needs. The lighter, equally-standard alternative is **GitHub Flow: just `main` + tags** (no separate release branch). Since you asked to learn the release-branch practice, the plan now includes `release/*`; if you'd rather keep it simple, skip it and tag `main` directly — both are legitimate.

> **Optional upgrade:** trigger `deploy.yml` on a **version tag** (`v*`) instead of on every merge to `main`. That makes "a release" an explicit, deliberate action.

### M.4 — Branch protection on `main` (ties to your CI gate)
- Require a **Pull Request** to merge into `main` (no direct pushes).
- Require **CI to pass** (the lint + critical-tests gate from Section G / L.6) before merge.
- This is what makes `main` *always deployable* — the core promise of a release branch.

### M.5 — Commit messages (Conventional Commits)

> Use a small, standard format so history is readable and scannable — and so reviewers see professional discipline.

**Format:** `type(scope): short summary in present tense`
- **type:** `feat` (new feature), `fix` (bug fix), `docs`, `test`, `refactor`, `chore`, `ci`, `perf`, `build`, `style`.
- **scope:** the module — `ingestion`, `streaming`, `transform`, `rag`, `infra`, `cicd`, `docs`.

**Examples:**
- `feat(ingestion): add idempotent daily price loader`
- `fix(streaming): reconnect websocket on dropped connection`
- `test(transform): add silver de-duplication test`
- `ci(github): block deploy when critical tests fail`
- `docs(readme): add idempotency design summary`

**Rules of thumb:** one logical change per commit; summary under ~72 characters; explain *why* in the body if not obvious; never commit secrets or large data files (add them to `.gitignore`).

### M.6 — Setup (do in Stage 0)
1. `git init`; create the repo on GitHub (`market-pulse`).
2. Create `main` and `develop`; set `develop` as your working default.
3. Turn on **branch protection** for `main` (require PR + passing CI).
4. Add a `.gitignore` (Python, Terraform state, `.env`, data files) and a short commit-convention note in `CONTRIBUTING.md` (or the Developer Guide).
5. *(Optional, nice touch)* add `commitlint` via a `pre-commit` hook to auto-check commit-message format.
6. **Release branches come later, not now:** cut `release/X.Y.Z` from `develop` only **at release time** (typically once, near Stage 8). After merging it into `main`, **tag** the commit `git tag v1.0.0 && git push --tags`.

### M.7 — Commit & push approval (STRICT — your rule)

> **Claude Code must NEVER run `git commit` or `git push` on its own. You approve every commit.**

- Claude Code may **write code, stage changes, run tests, and *propose* a Conventional-Commit message** — but it **stops and waits for your explicit "yes"** before it actually commits or pushes.
- **How this is enforced (not just requested):** permission rules in your prompt/`CLAUDE.md` *shape* behaviour but don't *enforce* it — Claude Code's **permission system** does. So we set it up two ways:
  1. **Permission rule:** add an **`ask` rule** for git writes — `Bash(git commit:*)` and `Bash(git push:*)` — so Claude Code always prompts you (configured via `/permissions` or `settings.json`). Do **not** add these to the `allow` list.
  2. **Belt-and-braces hook (optional, stronger):** a **`PreToolUse` hook** that blocks `git commit`/`git push` unless you confirm — hooks are enforced by Claude Code regardless of what the model "decides".
- Also state the rule in **`CLAUDE.md`** so the model itself doesn't try: *"Never run git commit or git push without the developer's explicit approval; stage changes and propose a commit message instead."*
- **Never** use `--dangerously-skip-permissions` (or `bypassPermissions` mode) on your real machine — that removes exactly this safety.

**Hand to Claude Code:** "Set up the Git workflow per Section M: `.gitignore`, a `CONTRIBUTING.md` documenting the branch model + Conventional Commits, and (optional) a commit-message lint hook. **Do not commit or push anything yourself — stage changes, propose the commit message, and wait for my approval.** Apply Section L standards."

---

## N. AWS provisioning workflow — you create the resources, with guided learning (your rule)

> **Your rule:** Claude Code must **not create AWS resources on its own**. Whenever a stage needs AWS resources, Claude Code **explains the concept first**, then **instructs you**, and **you** create/deploy them — so you learn each service hands-on (the concept *and* the skill of using it).

### N.1 — The rule
- Claude Code may **write Terraform, write code, run `terraform plan`** (a read-only preview of changes), and **explain** — but it **never runs `terraform apply` / `terraform destroy` or any AWS-mutating CLI command itself.** *You* run those, after Claude explains what they will do.
- For every AWS resource, **before** it is created, Claude Code gives you a short **concept brief** (see N.3).

### N.2 — Two modes (how you actually create things)
This balances your **learning** goal with the plan's **production-grade IaC** goal (honest trade-off below).

- **Learn-it mode** — *the first time you meet a new service* (S3, Glue, Kinesis, Athena, Step Functions, Bedrock, …): Claude Code walks you through creating **one example in the AWS Console** so you *see and understand* it. Then you **delete that example**, and the real project resource is created via Terraform (build-it mode). *(Why delete it? So it doesn't keep costing money or clash with the Terraform-managed one.)*
- **Build-it mode** — *the default for the actual project resources*: Claude Code writes the **Terraform** and explains exactly what it creates and why; **you run `terraform apply` yourself** (after reading `terraform plan`). You stay in control, learn IaC, and the project stays reproducible.

> **Honest trade-off:** doing the Console walkthrough for *every* new service is slower than letting Terraform do everything silently — but that hands-on time is exactly the AWS skill you want, and it's what interviewers probe. To respect the ~12-day clock: do the Console walkthrough **once per new service** (to learn it), and let **Terraform (run by you)** handle repeats and the full rebuild. Don't hand-create the same kind of resource ten times.

### N.3 — The concept brief (what Claude explains before each resource)
For each resource, in plain words:
1. **What** the service/resource is.
2. **Why** we use it here (its job in MarketPulse).
3. **Key settings** you're choosing, and what they mean.
4. **Cost**, and how to stay safe.
5. **Local twin** (how it maps to LocalStack / DuckDB / Ollama for free practice).
6. **The exact steps** — Console clicks (learn-it mode) or the Terraform you'll `apply` (build-it mode).

### N.4 — Cost & teardown safety (important)
- **Track every resource you create.** A forgotten Console-created resource keeps costing money; Terraform-managed resources are removed cleanly with `terraform destroy`.
- Keep **Terraform as the source of truth** so the **teardown checklist (Section I)** reliably removes everything before June 18. For anything you created by hand and want to keep, ask Claude Code how to **`terraform import`** it so Terraform tracks it.
- Always run the **`aws-cost-guard`** check + confirm the price *before* creating anything (Section H).

### N.5 — Enforce it (like the commit rule)
- State it in **`CLAUDE.md`**: *"Never run `terraform apply`/`terraform destroy` or AWS-mutating CLI commands. Explain the concept and the exact steps; I will create/deploy AWS resources myself."*
- Add **`ask` permission rules** for `Bash(terraform apply:*)`, `Bash(terraform destroy:*)`, and mutating `Bash(aws:*)` commands, so Claude Code always prompts you. (`terraform plan` and read-only `aws` commands are fine for Claude to run.)

> **Note on CI/CD (reconciling with Section G):** the deploy pipeline (`deploy.yml`) also runs `terraform apply` — but it is **gated by your manual approval** in GitHub, so a human is still in the loop. **During the learning build, run `terraform apply` locally yourself** (that's how you learn). Treat the CI auto-deploy as the *production pattern you showcase*: build it in Stage 7, keep the approval gate, and only let it apply once you're comfortable. So there's no contradiction — `apply` is always behind a human (you), whether you run it locally or approve it in the pipeline.

**Hand to Claude Code:** "For every AWS resource: give me the N.3 concept brief, then either walk me through the Console (a new service) or write the Terraform for me to apply. Never run `terraform apply`/`destroy` or AWS-mutating commands yourself. Apply Section L standards."

---

## O. Secrets & credentials handling (standard practices — I instruct, you apply)

> **Your rule (consistent with Section N):** Claude Code **explains** each secrets step and **instructs you**; **you** create/configure the credentials. Secrets are the #1 thing leaked in real projects, so we follow standard practice from day one. **Claude Code must never print, hardcode, or commit a real secret value.**

> **Jargon:** a **secret** = any value that grants access and must stay private (API keys, passwords, access keys, tokens). **Credentials** = the secrets that prove *who you are* to AWS.

### O.1 — Two kinds of secrets in this project
1. **AWS account credentials** — how *you* (and Terraform / the CLI) authenticate to AWS.
2. **Application secrets** — third-party keys the app uses at runtime (e.g. the market-data API key, news API key).

### O.2 — AWS account credentials (authentication)
- **Never use the root account for daily work.** Use an **IAM user** (or **IAM Identity Center / SSO**). Enable **MFA**.
- **Prefer short-lived credentials over long-lived access keys.** Leaked long-lived keys are the #1 cause of AWS breaches. If you can, use **IAM Identity Center (SSO)** with `aws sso login` (temporary creds); otherwise an IAM user access key is acceptable for a learning project.
- **Store CLI creds outside the repo:** `aws configure` writes them to `~/.aws/credentials` (a named profile) — **never** in your project folder, **never** hardcoded in code, **never** committed.
- **Least privilege** on whatever identity you use; **rotate** keys; **delete** unused keys; never paste keys into chat, Slack, or screenshots.
- **CI/CD:** prefer **OIDC** (Section G) — GitHub gets a short-lived token, so there are *no* stored AWS keys. If you must use keys, put them in **GitHub Secrets**, never in the workflow YAML.

### O.3 — Application secrets (runtime)
- Store them in **AWS Secrets Manager** (the plan's default — you learn the standard service) **or**, to save money, **SSM Parameter Store (SecureString)**.
  - **Cost flag:** Secrets Manager is **~$0.40 per secret per month + ~$0.05 per 10,000 API calls** (confirm current rates). **SSM Parameter Store standard parameters are free.** For a couple of secrets over ~2 weeks, Secrets Manager is ~$1 total (fine) — but Parameter Store is the **$0 alternative**. Both encrypt with **KMS** and your code reads them the same way.
- The Lambda/job reads the secret **at runtime** via the SDK; its IAM role gets **only** `secretsmanager:GetSecretValue` (or `ssm:GetParameter`) on **that one secret** — least privilege.
- **Never** put a secret in code, in a committed `.env`, in logs, or in plaintext anywhere.

### O.4 — The Terraform-state trap (important, often missed)
- **Terraform state can store secret values in plaintext.** If you pass a secret *value* into a Terraform resource/variable, it lands in `terraform.tfstate`.
- **Standard practice:** create the **secret container** in Terraform (e.g. the Secrets Manager secret *resource*), but **set the value out-of-band** — you add the actual value via the Console/CLI after `apply`. Mark sensitive variables `sensitive = true`, keep state in an **encrypted backend** (or local + `.gitignore`), and **never commit `*.tfstate` or secret-bearing `*.tfvars`**.

### O.5 — Repo hygiene & scanning
- **`.gitignore`** must exclude: `.env`, `*.tfstate*`, secret-bearing `*.tfvars`, and anything under `.aws/`.
- **`.env.example`** (committed) lists keys with **empty values**; the real `.env` (untracked) holds local values.
- Add a **secret scanner to your pre-commit hook** (e.g. `gitleaks` or `detect-secrets`) so an accidental secret is caught **before** it's committed. Enable **GitHub secret scanning / push protection** on the repo too.
- **If a secret is ever committed: treat it as compromised — rotate it immediately.** Deleting it from history is *not* enough.

### O.6 — Local development
- Locally, secrets live only in your untracked `.env`; **LocalStack Secrets Manager / SSM** can mock retrieval, so your code path is identical to AWS (swap by `AWS_ENDPOINT_URL`). No real secret leaves your machine.

**Hand to Claude Code:** "When a secret is needed: explain the concept, then instruct me to store it in Secrets Manager (or SSM Parameter Store), grant the component least-privilege read access, and wire the code to fetch it at runtime. Never hardcode, print, or commit a secret value; never put a secret *value* in Terraform (create the container — I'll set the value). Add a secret-scanning pre-commit hook. Apply Section L standards."

---

*Next: open `03_PERSONAL_LEARNING_PLAN.md` for what to study, in what order, with free resources.*
