# Project Plan — High Level
## "MarketPulse" : A Real-Time Market Intelligence Lakehouse

> This is the **bird's-eye view** of the project. It explains *what* we are building, *why* each piece exists, and *in what order* we build it. The companion file `02_PROJECT_PLAN_IN_DEPTH.md` has the step-by-step build details. The file `03_PERSONAL_LEARNING_PLAN.md` is your study roadmap.

---

## 1. The one-paragraph vision

We are building **one data platform** that takes in financial / crypto market data in **three forms** — *live streaming prices*, *historical batch data*, and *text documents (news)* — cleans and organises it into a **lakehouse**, and lets a person **ask questions in plain English** through an AI assistant. It will be built with **production habits**: infrastructure-as-code, automated testing, CI/CD, security, monitoring, and strict cost control. When the AWS credit runs out, the whole thing can be run **for free on your laptop** by changing configuration only.

**Plain-word definitions (used throughout):**

- **Lakehouse** = a *data lake* (cheap file storage like S3) combined with a *warehouse* (you can run analytics SQL on top). You get cheap storage **and** SQL power.
- **Medallion layers** = three quality levels of data: **Bronze** (raw, exactly as it arrived), **Silver** (cleaned, typed, joined), **Gold** (final business-ready tables).
- **ETL / ELT** = *Extract, Transform, Load* — the work of pulling data, reshaping it, and saving the clean result. (ELT just does the transform *after* loading.)
- **Streaming** = data that arrives continuously, live, second by second.
- **Batch** = data pulled on a schedule (e.g. once an hour), in chunks.
- **RAG** = *Retrieval-Augmented Generation* — an AI that answers using *your* data and documents, not its own memory. (You already know this well, so we keep it solid but not the main focus.)

---

## 2. Why this project (and not a basic one)

| Your goal | How this project delivers it |
|---|---|
| High interviewer impression | A *real-time* market platform with an AI analyst stands out far more than the common e-commerce dashboard. |
| Maximum learning + breadth | It naturally uses **~18–20 AWS services** across every layer of a real data platform. |
| "Multi-domain" feel, done right | Three data types (live numbers, historical numbers, text) inside **one coherent platform** — deep *and* broad. |
| Streaming + RAG | Both are core components, exactly as you chose. |
| Production standards | IaC, CI/CD, testing, security, governance, observability, cost-ops are all built in. |
| Strict $99 budget | Built on cheap, serverless services; the expensive traps (MWAA, Redshift, MSK) are deliberately avoided. |
| Portable to local (free) | Open file formats + a config-swappable backend design let it run on LocalStack / DuckDB / Ollama. |

---

## 3. The big picture (data flow)

```
            ┌─────────────────────── INGESTION ───────────────────────┐
            │                                                          │
  Live exchange feed ──► Kinesis ──► Lambda ──┐                        │
  (streaming numbers)                          │                       │
                                               ▼                       │
  Scheduled API pulls ──► EventBridge ──► Lambda/Glue ──►  S3 BRONZE (raw)
  (historical numbers)                                          │
                                                                ▼
  News / documents ──► Lambda ──► (text) ─────────►  S3 BRONZE (raw docs)
            │                                                   │
            └───────────────────────────────────────────────────┘
                                                                │
                          TRANSFORM (Glue jobs + dbt)           ▼
                          Bronze ──► S3 SILVER (clean) ──► S3 GOLD (marts)
                                          │  (Apache Iceberg tables)
                                          ▼
                          QUERY: Amazon Athena  (local twin: DuckDB)
                                          │
            ┌─────────────────────────────┼───────────────────────────┐
            ▼                             ▼                            ▼
   RAG assistant (Bedrock)        BI / dashboards            ad-hoc SQL analysis
   "ask in plain English"         (QuickSight, optional)

  Cross-cutting (everywhere):
  • Orchestration: Step Functions + EventBridge
  • Security: IAM + KMS + Secrets Manager + Lake Formation
  • Observability: CloudWatch
  • Infra-as-code: Terraform   • CI/CD: GitHub Actions   • Containers: Docker
  • Cost control: AWS Budgets + Budget Actions + Cost Explorer + tags
```

---

## 4. Service map — AWS, free local twin, and cost

This single table is the heart of the plan. Every component has a **free local equivalent** (for when the credit ends) and a **cost note** (to protect your $99). Verified prices are US-East reference rates; your region may differ slightly.

| Layer (plain meaning) | AWS service | Free local twin | Cost note (verified) |
|---|---|---|---|
| **Streaming ingest** — live data | Kinesis Data Streams + Lambda | LocalStack Kinesis | ~**$0.015 / shard-hour** (provisioned). Run only during test windows → a few dollars total. |
| **Batch ingest + scheduling** | EventBridge + Lambda | LocalStack / cron | Pennies. |
| **Lake storage** — raw→clean→ready | S3 (Parquet + Apache Iceberg) | MinIO / LocalStack S3 | Cents for a few GB. |
| **Table catalog** — lists your tables | Glue Data Catalog + Crawlers | local Iceberg/Hive catalog | **First 1M objects + 1M requests free**; crawlers $0.44/DPU-hr but run for minutes. |
| **Transform (ETL/ELT)** | Glue jobs (Spark / **Python Shell**) + dbt | local Spark / DuckDB + dbt | **Main cost lever.** Spark = $0.44/DPU-hr, min 2 DPU ≈ $0.88/hr. **Python Shell ≈ $0.003/hr** — use it for light jobs. |
| **SQL engine (the "warehouse")** | Amazon Athena | **DuckDB** (swap by config) | **$5 per TB scanned.** With partitioning + Parquet your whole project scans well under 1 TB → a few dollars. |
| **Orchestration** — run steps in order | Step Functions + EventBridge | LocalStack / local Dagster | Cheap. **We avoid MWAA (~$350/month).** |
| **RAG AI assistant** | Bedrock (LLM + embeddings) | **Ollama** + local index | Pay-per-token; small text usage ≈ a few dollars. **Avoid OpenSearch vector store** (costly). |
| **Vector store** (for RAG) | FAISS index file in S3, loaded by Lambda | local FAISS file | Near-zero. (pgvector-in-Docker is the optional "managed" upgrade.) |
| **Secrets & security** | Secrets Manager + IAM + KMS | LocalStack Secrets Manager | Minimal. |
| **Data governance** | Lake Formation (permissions on lake) | (concept only locally) | No extra charge for basic use. |
| **Observability** | CloudWatch (logs, metrics, alarms) | local Grafana | Minimal. |
| **Containers** | ECR (image registry) + ECS Fargate (run containers) | Docker + docker-compose | Run containers only in short windows; Fargate billed per second. |
| **Infra-as-code** | Terraform (`tflocal` wrapper for local) | same, endpoint override | Free. |
| **CI/CD** | GitHub Actions (+ optional CodeBuild/CodePipeline) | runs in GitHub cloud | Free for public repos. |
| **Cost control** | Budgets + Budget Actions + Cost Explorer + tags | (AWS-only concept) | This *protects* you, costs nothing. |

**Service count:** ~18–20 AWS services. That gives you broad, resume-worthy coverage for the credit.

---

## 5. How "switch to local by config only" actually works

This is your hard requirement, so here is the honest mechanism. We split services into two groups.

**Group A — services that have a local twin (just point your code at it):**
S3, Lambda, DynamoDB, Kinesis, SQS, SNS, EventBridge, Step Functions, Secrets Manager.
→ Set **one environment variable** `AWS_ENDPOINT_URL=http://localhost:4566` and your Python (`boto3`) code talks to LocalStack instead of AWS. **No code change.**

**Group B — services with no free local twin (we use a backend adapter):**
Athena, Glue, Bedrock.
→ We write our code against a small **interface** (a "port"). Example: a `QueryEngine` interface with two implementations — `AthenaBackend` (cloud) and `DuckDBBackend` (local). The active one is chosen in a config file. Same idea for an `LLMClient` interface: `BedrockBackend` vs `OllamaBackend`.
→ This adapter design (called *ports and adapters* / *hexagonal architecture*) is itself a senior-level skill that impresses reviewers.

**Important honesty note:** LocalStack's free **Community** edition now requires a free account/token, and the heavy data services (Glue, Athena, Redshift) are in its paid Pro tier. That is exactly *why* Group B uses open-source twins (DuckDB, Ollama, local Spark) instead of relying on LocalStack for everything. The result: switching environments = **editing config + setting one env var**, never rewriting logic.

**IaC portability:** Terraform runs against LocalStack through the `tflocal` wrapper (it just overrides the service endpoints). So your infrastructure definitions are reused locally too.

---

## 6. Build stages (ordered by dependency, NOT by calendar days)

Each stage produces something working before the next begins. **Core** = must-do, already impressive. **Stretch** = polish if time remains.

**Stage 0 — Foundation & safety (do this first).**
Set up AWS account safety (budgets, alarms, billing alerts, IAM admin user, cost tags). Complete the **5 onboarding tasks to earn +$100**. Create the GitHub repo, the folder structure, the config/backend-adapter skeleton, Docker + docker-compose for local, and the Claude Code skills/subagents. *Why first: nothing else is safe to build until cost guardrails and the skeleton exist.*

**Stage 1 — Storage + batch ingest (Bronze).**
Create the S3 lake buckets. Build the scheduled batch ingestion (EventBridge → Lambda) that pulls historical market data and lands it raw in **Bronze**. *Why now: you cannot transform data you have not landed.*

**Stage 2 — Catalog + first SQL.**
Register Bronze data in the Glue Data Catalog (crawler or manual table). Run your first Athena query. *Why now: this proves the lakehouse "sees" your data before you invest in transforms.*

**Stage 3 — Transform to Silver + Gold.**
Build **Bronze → Silver → Gold** as Apache Iceberg tables. Primary path is **SQL-based ELT** (Athena `CREATE TABLE AS` + **dbt** — cheapest, serverless), and you *also* build a **Glue PySpark** job for the same step to learn Spark. Add **data quality tests**. *Why now: clean Gold tables are what everything downstream (AI, dashboards) depends on.*

**Stage 4 — Streaming path.**
Add the live feed: a containerized producer → Kinesis → Lambda → Bronze, then fold the stream into the lakehouse. *Why now: streaming is layered on after the batch backbone works, so you debug one thing at a time.*

**Stage 5 — RAG assistant (moderate-to-high).**
Ingest news/documents, create Bedrock embeddings, store vectors (FAISS-in-S3), and build the assistant that answers using **both** documents **and** live Gold metrics (it can run an Athena query when needed). *Why now: the assistant needs Gold data + documents to exist first.*

**Stage 6 — Orchestration + observability.**
Wire the batch pipeline together with Step Functions + EventBridge (retries, ordering). Build CloudWatch dashboards and alarms. *Why now: orchestration ties finished pieces into one reliable flow.*

**Stage 7 — CI/CD + containerization polish.**
GitHub Actions pipelines: lint → test → `terraform plan` → deploy. Containerize the producer (and optionally a dashboard) → ECR → Fargate. *Why now: CI/CD is most useful once there is real code and infra to protect.*

**Stage 8 — Documentation + handover (do this last, but start notes early).**
Write the **Developer Guide** and the **User Guide**, the architecture diagram, the cost report, and the resume write-up. **Export everything (code, IaC, sample data, screenshots) before the credit expires on June 18.** *Why last: the account may close when credits end, so capture proof of work.*

---

## 7. Tech stack & tooling

- **Language:** Python (ingestion, Lambda, glue scripts), SQL (dbt, Athena).
- **Transforms:** AWS Glue (Spark + Python Shell), **dbt** (SQL models + tests).
- **Table format:** Apache Iceberg (open, portable).
- **Query:** Athena (cloud) / DuckDB (local).
- **AI:** Amazon Bedrock (cloud) / Ollama (local); FAISS for vectors.
- **Orchestration:** Step Functions + EventBridge (cloud) / Dagster (optional local).
- **Infra-as-code:** Terraform + `tflocal`.
- **CI/CD:** GitHub Actions.
- **Containers:** Docker + docker-compose; ECR + ECS Fargate on AWS.
- **Local cloud:** LocalStack (Community) + MinIO.
- **Cost:** AWS Budgets, Budget Actions, Cost Explorer, cost-allocation tags.
- **Version control:** Git + GitHub — `main` (production, tagged releases) / `develop` (day-to-day) / `feature/<module>` / temporary `release/<version>` branches, with **Conventional Commits** and branch protection.

**Claude Code setup (kept lean to save tokens):**
- **Skills (cheap, load only when needed):** `engineering-standards` (PEP 8 + type hints + tests-with-code, root-cause fixes not band-aids, idempotency + check-before-create, critical-event logging, error handling with retries + DLQ), `aws-cost-guard` (always check price + budget before any deploy), `iac-terraform` (your Terraform conventions), `data-quality` (testing rules for pipelines).
- **MCP servers:** use **very few**. At most one docs/pricing lookup. (You asked to minimise MCP — agreed.)
- **Subagents:** a **cost-reviewer** and a **code/infra-reviewer** that check work *before* any deploy.
- **Human-in-the-loop:** Claude Code never commits or creates AWS resources on its own — it explains the concept and instructs you; *you* approve commits and run `terraform apply`. It also **asks you (rather than guessing) whenever a requirement is ambiguous or a choice has real cost/security/architecture trade-offs**, and states assumptions explicitly. (This is how you learn AWS hands-on and stay in control; see `02` Sections H, M.7 + N.)
- **Local tools:** AWS CLI, Terraform + `tflocal`, Docker, DuckDB, `uv` (Python env), Ollama.

---

## 8. Deliverables (what "done" looks like)

1. A working **multi-modal lakehouse** on AWS (streaming + batch + documents).
2. A **RAG assistant** answering plain-English questions over your data.
3. **Infrastructure-as-code** (one command rebuilds everything).
4. **CI/CD pipelines** in GitHub Actions.
5. **Containerized** components (Docker) with a local docker-compose stack.
6. **Local mode** that runs free via config swap (DuckDB / Ollama / LocalStack).
7. **Documentation:** Developer Guide + User Guide + architecture diagram.
8. **Cost report** proving you stayed under budget (a great interview talking point).
9. **Resume write-up** summarising impact, scale, and skills.

---

## 9. Cost strategy (summary)

- **Realistic estimate:** a careful build runs well under **$30–40** over 15 days, leaving large buffer under $99.
- **Two safety layers:** (a) Budgets with alerts at **$20 / $50 / $80**; (b) **Budget Actions** that can automatically stop or restrict resources at a threshold.
- **$99 is a hard ceiling (either way):** treat **$99 as the maximum AWS spend, regardless of account type** ($100 credit, capped at $99 for margin). Only the new Free Plan auto-closes safely at $0; on a legacy/Paid Plan overages **are billed**, so the Budget Actions guardrails are **mandatory**. Confirm your plan at Billing → Credits. Details in `02`, Section I.
- **Before any project code:** stand up the **safety layer** first — repo + `main`/`develop`, `CLAUDE.md`, and the `ask` permission rules for git + `terraform apply` — so the human-in-the-loop guardrails are live from the start.

---

## 10. Honest trade-offs & risks

- **Ambition vs time.** Streaming + RAG + full stack is a lot for ~12 days. We manage it with the staged Core/Stretch split and heavy reuse via IaC + Claude Code.
- **Each component is *production-shaped*, not *enterprise-complete*.** Good practices, tested, documented — but not infinitely scaled. This is the right level for a portfolio piece, and interviewers respect it.
- **LocalStack is no longer fully free.** Handled by the backend-adapter design (Group B uses open-source twins).
- **Streaming is the main cost risk.** Handled by running the stream only in short test windows and using on-demand/small shard counts.
- **Data-source reliability.** Free public market feeds can change; we isolate the feed behind the ingestion layer so a source swap is easy.

---

*Next: open `02_PROJECT_PLAN_IN_DEPTH.md` for the detailed, build-ready steps to hand to Claude Code.*
