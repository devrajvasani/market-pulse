# Personal Learning Plan
## What to study, in what order, to build "MarketPulse" with confidence

> This roadmap is **ordered to match the build** in `02_PROJECT_PLAN_IN_DEPTH.md`. Learn each track *just before* you need it ("just-in-time learning"), so concepts stick because you immediately use them. You are a beginner-to-medium data engineer, so each track starts from fundamentals. Time is short (~15 days), so the rule is: **learn the minimum to build the stage, then build, then deepen.**

---

## How to use this plan

- **Don't pre-study everything.** Learn a track right before its stage.
- **Hands-on beats reading.** For every concept, do one tiny example before using it in the project.
- **Use Claude Code as a tutor**, not just a code generator — ask it *why*, ask it to explain its choices, ask "what would break if…".
- **You create the AWS resources, guided.** Claude Code explains each service (concept + key settings + cost + local twin) and instructs you; *you* create/deploy it — a Console walkthrough the first time you meet a service, then `terraform apply` that *you* run. This hands-on approach is *how you actually learn AWS* (see Section N of `02`).
- **Spend ~1–2 hours/day learning, the rest building.** The project *is* the main teacher.

---

## Track 0 — Foundations & AWS cost safety *(before Stage 0)*

**Why first:** you must understand billing and safety before creating anything.

**Learn:**
- **Account types & credits:** the **new AWS Free Plan** ($100 + earn $100; account *closes* at 6 months or $0, so no surprise bill) **vs** a **legacy / promotional account** (overages *are billed*). You confirmed $100 of credit; the bonus +$100 and the safe auto-close exist **only on the new Free Plan** — so check **Billing → Credits** to see which you have. (Your June 18 expiry hints it may be legacy/promotional.)
- **Cloud Financial Management basics:** Budgets, Budget Actions, Cost Explorer, cost-allocation tags.
- **IAM basics:** root vs IAM user, MFA, users/roles/policies, least privilege.
- **The cost traps** to avoid: MWAA, Redshift provisioned, MSK, OpenSearch, NAT Gateway, idle EC2/RDS/EBS, unattached Elastic IPs.

**Do:** confirm your account type; set up budgets; create an IAM admin user with MFA. *If* you are on the new Free Plan, also complete the 5 onboarding tasks for +$100. If not, treat $99 as a hard ceiling.

**Free resources:** AWS Free Tier docs and FAQs; AWS Budgets docs; AWS IAM "getting started"; AWS Skill Builder (free tier) "Cloud Practitioner Essentials".

---

## Track 0.5 — Naming conventions & casing standards *(before Stage 1, used everywhere)*

**Why:** every resource and every line of code you write needs a name. Consistent, correct naming is one of the clearest signals of a professional engineer — and it's *required* (AWS rejects invalid names, and tools expect specific casing). You'll use this from your very first bucket onward, so learn it early.

**Learn — the casing styles (what they are, and where each is used):**

> "Case" = the rule for how you join words in a name (with capitals, underscores, or hyphens).

| Case | Looks like | Read it as | Where you use it |
|---|---|---|---|
| **snake_case** | `bronze_bucket` | lowercase, words joined by `_` | Python variables/functions, SQL columns, dbt models, file names |
| **SCREAMING_SNAKE_CASE** | `MAX_RETRIES` | uppercase + `_` | Constants, **environment variables** (`APP_ENV`) |
| **kebab-case** | `marketpulse-dev-bucket-bronze` | lowercase, words joined by `-` | **AWS resource names**, **S3 buckets**, Git repos, Docker images, URLs |
| **PascalCase** | `AthenaBackend` | each word capitalised, no spaces | Python **class** names |
| **camelCase** | `bucketName` | first word lowercase, rest capitalised | JSON keys, JavaScript (you'll meet it, less used here) |
| **dot.case** | `service.module.name` | lowercase joined by `.` | logger names, some config keys |

**Learn — the golden rules of *which* to use:**
- **Match the tool's native convention.** Python code → `snake_case`. A cloud resource string → `kebab-case`. Using the "wrong" case (e.g. `BronzeBucket` for a Python variable) instantly looks amateur.
- **Why hyphens for cloud, underscores for code?** Cloud names often become parts of **URLs/hostnames**, where underscores are illegal or discouraged — so the safe choice is hyphens. Code identifiers can't contain hyphens (a hyphen means "minus"), so code uses underscores.

**Learn — AWS naming *rules* you must obey (the *why*: AWS will reject bad names):**
- **S3 buckets:** lowercase only; **no underscores**; 3–63 characters; **globally unique across all AWS customers** (so add a short random suffix if a name is taken); may not look like an IP address.
- **General AWS resources** (Lambda, Glue, Step Functions): hyphens are safe; respect each service's length limit; no spaces; avoid special characters.
- **Consistency beats cleverness:** pick one pattern and never break it.

**Learn — the project naming pattern (used in your plan):**
```
{project}-{environment}-{service}-{purpose}
marketpulse-dev-bucket-bronze
marketpulse-dev-lambda-batch-ingest
```
This makes any resource self-explanatory: *whose, which stage, what kind, what for.*

**Learn — naming *best practices*:**
- Be **descriptive**, not cryptic (`batch-ingest`, not `bi`).
- Include the **environment** (`dev`/`staging`/`prod`) so you never confuse test and real resources.
- **No spaces, no uppercase in cloud names, no special characters.**
- Prefix with the **project** so future projects never collide.

**Learn — tagging best practices (the partner of naming):**
- AWS recommends a consistent tag set; a good minimum: `project`, `environment`, `owner`, `managed-by`.
- Decide which tags are **mandatory** (every resource must have them).
- **Enforce automatically:** set Terraform `default_tags` once so you never tag by hand. (Manual tagging always drifts.)
- Activate `project` as a **cost-allocation tag** so Cost Explorer can total it.

**Do:** before creating anything in Stage 1, write your naming pattern and tag set at the top of your repo's `DEVELOPER_GUIDE.md`, and set `default_tags` in Terraform. Then every resource follows the rule automatically.

**Free resources:** AWS "Tagging best practices" / "Tagging Your AWS Resources" whitepaper; AWS S3 "Bucket naming rules" docs; the "AWS resource naming conventions" community guides; PEP 8 (Python naming) for the code side.

---

## Track 0.6 — Git workflow: branches & commit messages *(set up in Stage 0)* — your development practice

**Why:** clean version control is a core professional habit and a clear resume signal. Branches let you work safely in parallel, CI tests changes in isolation, and tidy commits make your history readable. **Cost: $0** (Git + GitHub public repos are free; no AWS involved).

**Learn:**
- **Git basics:** `commit` (a saved snapshot + message), `branch` (a parallel line of work), `merge`, and the **Pull Request (PR)** (propose a merge; CI runs; you review the diff).
- **The branching model (Section M of `02`):**
  - **`main`** = production / release-target branch, always deployable, **protected**; the **deploy pipeline** ships it, and each release is **tagged** (`v1.0.0`).
  - **`release/<version>`** = a **temporary** branch (e.g. `release/1.0.0`) cut from `develop` to finalise a release (bug fixes + version bump only), then merged into `main` and tagged.
  - **`develop`** = your day-to-day branch (mixed module work + bug fixes).
  - **`feature/<module>`** = one branch per module, branched off `develop`.
- **Tags vs branch names:** you **never rename `main`** (e.g. `release/main` is non-standard); you mark releases with **tags** (`git tag v1.0.0`). The `release/*` slash is a *namespace* for temporary versioned branches.
- **Branch protection:** require a PR + passing CI before merging to `main` — this is what keeps `main` always deployable.
- **Conventional Commits:** `type(scope): summary` — e.g. `feat(ingestion): add idempotent loader`, `fix(streaming): handle reconnect`. Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`.
- **`.gitignore`:** never commit secrets, `.env`, large data, or Terraform state.

**Do:** in Stage 0, create `main` + `develop`, protect `main`, add `.gitignore` + a `CONTRIBUTING.md` describing the model. Then work on `feature/<module>` branches and merge via PRs.

**Free resources:** the **Pro Git** book (free online — chapter 3, "Git Branching"); GitHub Docs "About protected branches" + "About pull requests"; the **Conventional Commits** spec; "Git Flow" / "GitHub Flow" overviews.

---

## Track 1 — Data engineering core concepts *(before Stages 1–3)*

**Why:** this is the backbone of the whole project.

**Learn:**
- **ETL vs ELT** and when each is used.
- **Data lake vs data warehouse vs lakehouse** — and why lakehouse wins for cost + flexibility.
- **Medallion architecture** (Bronze/Silver/Gold) and *why* raw data is kept untouched.
- **File formats:** CSV vs **Parquet** (columnar, compressed) — why Parquet is far cheaper to query.
- **Partitioning** and **file sizing** — the #1 lever on query cost.
- **Apache Iceberg** basics — what a "table format" adds (schema evolution, time-travel, updates).

**Do:** sketch your Bronze/Silver/Gold tables on paper before writing any transform.

**Free resources:** "Fundamentals of Data Engineering" concepts (summaries/talks); Apache Iceberg docs "Getting Started"; AWS "What is a data lake / lakehouse" pages.

---

## Track 2 — AWS storage, catalog & query *(during Stages 1–3)*

**Why:** these are the lakehouse's storage and SQL engine.

**Learn:**
- **Amazon S3:** buckets, prefixes, partitions, encryption, lifecycle, blocking public access.
- **Parquet (hands-on):** *why* a columnar, compressed format makes queries cheap — row groups, column pruning (Athena reads only the columns you select), and compression. Write and read a Parquet file locally to *see* the size/scan difference vs CSV.
- **AWS Glue Data Catalog & Crawlers:** what a catalog/table is; when to use a crawler vs define tables manually (cost).
- **Two transform styles (you'll learn both):**
  - **SQL ELT on Athena (primary):** `CREATE TABLE AS SELECT` (CTAS) and `INSERT INTO` to build **Iceberg** tables serverlessly; plus **dbt-athena** models and tests. Cheapest path ($5/TB scanned).
  - **PySpark on Glue (the Spark path):** **PySpark** = the Python API for Apache Spark, a distributed engine. Learn Spark **DataFrames**, transformations vs actions, and writing Iceberg from Spark. Pricing: $0.44/DPU-hr (min 2 DPU ≈ $0.88/hr) — run a couple of learning jobs, don't make it your daily path.
- **Amazon Athena:** serverless SQL on S3; **$5 per TB scanned**; workgroups + per-query scan limits; how partitioning slashes cost.
- **dbt basics:** models, refs, materializations, and **tests**.

**Local twin to practice free:** **DuckDB** runs the SQL/dbt path on the same Parquet files; **local Spark (PySpark)** runs the Spark path — both free and offline.

**Free resources:** AWS S3/Glue/Athena docs "Getting Started"; Apache Parquet docs; the **PySpark "Quickstart: DataFrame"** guide + "Spark by Examples"; dbt "dbt Fundamentals" free course; DuckDB docs.

---

## Track 3 — Streaming *(during Stage 4)*

**Why:** the "real-time" half of the platform.

**Learn:**
- **Batch vs streaming** mental model; "transport layer, not a data lake" (move data out of the stream fast).
- **Amazon Kinesis Data Streams:** shards, producers/consumers, retention; pricing (~$0.015/shard-hour); why you run the stream only in test windows.
- **Lambda as a Kinesis consumer:** event-source mapping, batching.
- **WebSockets basics** (how the live producer connects to a feed).

**Local twin to practice free:** **LocalStack Kinesis** (Community) — develop the producer/consumer offline.

**Free resources:** AWS Kinesis Data Streams docs + FAQs; LocalStack Kinesis "Getting Started".

---

## Track 4 — Orchestration *(during Stage 6)*

**Why:** turns separate steps into one reliable pipeline.

**Learn:**
- **What orchestration is** (ordering, dependencies, retries, scheduling).
- **AWS Step Functions:** states, transitions, error handling, parallel/choice states.
- **EventBridge:** schedules (cron) and event rules.
- **Why we avoid MWAA** here (~$350/month) and what Airflow/Dagster are (so you can speak to them in interviews).

**Local twin to practice free:** Step Functions in LocalStack; or try **Dagster** locally (optional) to learn a real orchestrator.

**Free resources:** AWS Step Functions + EventBridge docs "Getting Started"; Dagster "Tutorial" (optional).

---

## Track 5 — AI / RAG (light touch — you already know this) *(during Stage 5)*

**Why:** you understand RAG deeply, so just map your knowledge onto AWS tools.

**Learn (only the AWS-specific bits):**
- **Amazon Bedrock:** how to call an LLM and an embeddings model; token-based pricing.
- **Vector store choice on a budget:** **FAISS-in-S3** (cheap) vs why **OpenSearch Serverless** is avoided (cost).
- **Hybrid retrieval idea:** combining document chunks with a live SQL result from Gold.

**Local twin to practice free:** **Ollama** (local LLM + embeddings) behind the same adapter.

**Skip for now / do later:** re-ranking, query rewriting, evaluation harness, multi-hop, memory — your post-project upgrades.

**Free resources:** Amazon Bedrock docs "Getting Started"; Ollama docs; FAISS quickstart.

---

## Track 6 — Infrastructure as Code (Terraform) *(starts Stage 0, used throughout)*

**Why:** "one command rebuilds everything" is a top resume signal and makes cloud↔local reuse possible.

**Learn:**
- **What IaC is** and why (repeatable, reviewable, no click-ops).
- **Terraform basics:** providers, resources, variables, outputs, state, `plan`/`apply`/`destroy`.
- **Modules** (reusable building blocks) and good **naming + tagging**.
- **`tflocal`** — running the same Terraform against LocalStack.

**Free resources:** HashiCorp "Terraform Getting Started — AWS" tutorials; LocalStack `tflocal` docs.

---

## Track 7 — Containerization (Docker) *(used in Stages 4 & 7)* — your point #4

**Why:** packages the producer/dashboard and powers the offline local stack.

**Learn (basics → applied):**
- **Images vs containers**, `Dockerfile`, layers & caching, `.dockerignore`.
- **docker-compose** for multi-service local setups (LocalStack + MinIO + Ollama).
- **Volumes** and **environment variables**.
- **ECR** (image registry) and **ECS Fargate** (run containers without managing servers).

**Free resources:** Docker "Get Started" guide; AWS ECR/ECS Fargate docs "Getting Started".

---

## Track 8 — CI/CD with GitHub Actions *(during Stage 7)* — your point #3, from basics

**Why:** automated testing + safe, repeatable deploys.

**Learn (in this order):**
1. **What CI/CD is** — Continuous Integration (auto build+test every change) + Continuous Delivery/Deployment (auto release what passes).
2. **GitHub Actions building blocks:** workflow → event/trigger → job → runner → step/action.
3. **Secrets** in GitHub; never hard-code credentials.
4. **A CI pipeline:** lint (ruff) → unit tests (pytest) → LocalStack integration tests → `terraform plan`.
5. **A CD pipeline:** `terraform apply` on `main` with manual approval.
6. **Security upgrade — OIDC:** short-lived AWS tokens instead of stored keys (great talking point).

**Free resources:** GitHub Actions "Quickstart" + "Understanding GitHub Actions"; AWS "Configure AWS Credentials" action + OIDC guide.

---

## Track 8.5 — Code quality, testing & error handling *(starts Stage 0, used everywhere)* — your coding-quality requirements

**Why:** these are the habits that separate a "student project" from a "production-shaped" one — and exactly what you asked to build in. They're enforced by the `engineering-standards` Claude Code skill and by `02`'s Section L, but you should *understand* what they enforce.

**Learn — best coding practices:**
- **PEP 8** (Python's style guide); auto-format with `ruff format`; lint with `ruff` (a fast linter that catches bugs and style issues).
- **Type hints** and **docstrings** — what they are and why they make code self-documenting and safer.
- **DRY** (*Don't Repeat Yourself*): small, single-purpose functions; no copy-paste.
- **No hard-coded values / no secrets in code** — everything from config or Secrets Manager.

**Learn — root-cause fixing (not band-aids):**
- The mindset: when something breaks, find *why* and fix it at the source (a **core/global fix**), so it can't recur.
- The anti-patterns to avoid: bare `except: pass`, broad exception-swallowing, `sleep()` timing hacks, commenting out failing tests.

**Learn — idempotency & check-before-create:**
- **Idempotent** = running twice = same result, no duplicates. Why ingestion must be safe to re-run (overwrite a date partition, deterministic keys, Iceberg `MERGE`).
- The habit of **checking whether a file/folder/object exists before creating or editing it**.

**Learn — logging:**
- Python `logging` vs `print`; **JSON logs** in Lambdas; log **levels** (INFO/WARNING/ERROR).
- *What* to log: critical milestones with counts, external-call outcomes, retries, errors with context — **not** secrets or per-record spam.

**Learn — error handling:**
- **Custom exceptions** and clear, contextful error messages.
- **Retries with exponential backoff** (e.g. the `tenacity` library) for flaky network calls.
- **Dead-letter queues (DLQ)** so failing records are parked, not lost.
- **Fail fast vs degrade gracefully** — when to crash vs route a bad record aside.

**Learn — testing:**
- **pytest basics:** test functions, `assert`, **fixtures** (reusable setup), parametrized tests.
- **Mocking AWS with `moto`** so unit tests run free and offline.
- The **testing pyramid** (many fast unit tests, fewer integration tests).
- **Test gates:** critical tests must pass before run/deploy — run by a **pre-commit hook** and by CI.

**Do:** in Stage 0, set up `ruff`, `pytest`, `pre-commit`, and a `Makefile`. From then on, write a test *with* every piece of code, and never mark a stage done until its critical tests pass.

**Free resources:** the official **pytest** docs ("Get Started"); **moto** docs; **ruff** docs; "PEP 8" and the "Google Python Style Guide"; **pre-commit** docs; the `tenacity` README for retries.

---

## Track 9 — Security, governance & licensing *(a thread through every stage)* — your point #1

**Why:** you specifically want to learn what makes a project *secure and production-grade*. This is exactly what senior interviewers test.

**Learn:**
- **IAM least privilege** — per-component roles with only the permissions needed; reading and writing **policies**; roles vs users.
- **Resource policies** — S3 bucket policies, KMS key policies.
- **KMS encryption** — at rest vs in transit; encrypting S3 and secrets.
- **Secrets & credentials (standard practices — see Section O of `02`):** AWS account creds (IAM user/SSO, **MFA**, **prefer short-lived over long-lived keys**, `~/.aws` kept outside the repo); **Secrets Manager vs SSM Parameter Store** (free) for app secrets, with least-privilege read; the **Terraform-state secret trap** (never put a secret value in state); **secret scanning** (`gitleaks` / `detect-secrets`) + GitHub push protection; **rotate** keys and treat "committed = compromised".
- **AWS Lake Formation** — table/column-level **data governance** on the lakehouse (e.g. an analyst role that sees Gold only).
- **Tagging policy** — for cost *and* governance.
- **Organizations & SCPs (concept only)** — account-wide guardrails; *do not enable* (it voids Free Tier credits).
- **Licensing literacy:** Apache-2.0 (Iceberg, Spark, Airflow, dbt-core), MIT (DuckDB, FAISS), **BSL** (LocalStack — not fully open source), and the **AWS Promotional Credit / Free Tier Terms** that govern your $100.

**Free resources:** AWS IAM, KMS, Secrets Manager, **SSM Parameter Store**, Lake Formation docs "Getting Started"; "IAM policy" examples; AWS "security best practices for access keys"; `gitleaks` / `detect-secrets` docs; the AWS Free Tier + Promotional Credit Terms pages.

---

## Track 10 — Observability *(during Stage 6)*

**Why:** you must *see* whether pipelines run and where they fail.

**Learn:**
- **CloudWatch:** logs, metrics, dashboards, alarms.
- **Structured logging** (JSON logs) from Lambdas.
- Basic **SLOs/alerts** thinking (alert on failures and over-long runs).

**Free resources:** AWS CloudWatch docs "Getting Started"; "structured logging in Lambda" guides.

---

## Suggested daily rhythm (not fixed dates — adapt freely)

1. **Morning (1–2 h):** learn the track for today's stage; do one tiny standalone example.
2. **Midday–evening:** build the stage locally (`APP_ENV=local`), write tests.
3. **Before deploy:** run cost-reviewer + infra-reviewer (Claude Code subagents).
4. **Deploy to AWS**, verify, then **check Cost Explorer**.
5. **End of day:** jot notes for the Developer/User guides while it's fresh.

---

## Interview talking points you'll earn (keep a running list)

- "I built a **multi-modal lakehouse** (streaming + batch + documents) on AWS."
- "I made it **portable**: it runs free locally via a backend-adapter (Athena↔DuckDB, Bedrock↔Ollama) by changing config only."
- "I enforced **least-privilege IAM**, KMS encryption, secrets management, and **Lake Formation** governance."
- "I built **CI/CD** with GitHub Actions (LocalStack integration tests, Terraform plan/apply, **OIDC** auth)."
- "I kept it under budget with **Budgets + Budget Actions**, partitioning, and **SQL-based ELT on Athena** (with **PySpark on Glue** for the heavier path) — here's the cost report."
- "I wrote it **production-shaped**: type hints, **moto-mocked tests** gated by **pre-commit + CI**, custom exceptions with clear messages, **retries + a dead-letter queue**, and **idempotent** re-runnable pipelines."
- "I used a **Git Flow-style** branching model — `main` (release, protected) / `develop` / `feature/<module>` — with **Conventional Commits** and **branch protection** (PR + green CI required to merge to `main`)."
- "I built a **hybrid RAG** assistant that combines news chunks with live SQL metrics."

---

## What to deliberately *not* learn now (avoid scope creep)

- Kubernetes/EKS (Fargate is enough here).
- Redshift / EMR / MSK deep dives (too costly to run; learn concepts only).
- Advanced RAG (re-ranking, evals) — your post-project upgrade.
- Multi-account / Organizations setup (voids credits).

Master the tracks above and you'll have a coherent, deep, **production-shaped** project — far beyond a basic API-to-warehouse pipeline.
