# MarketPulse — Local Infrastructure & Dependencies Setup

> **Read and follow this once at Stage 0, before building.** It's the single, self-contained guide to getting your local environment ready. The main plan (`02_PROJECT_PLAN_IN_DEPTH.md`, Section F) points here so all setup steps live in one place — and so a *new* developer can get running with one document.

This file is written **generally**: the standards below apply to **any local service emulator** the project uses. Today that is **LocalStack** (emulates AWS). If the project ever adds another — e.g. **Azurite** (emulates Azure storage) — add it here following the same pattern (see Section 5).

---

## 1. What "local infrastructure" means here (and why)

> **Service emulator** = a tool (usually a Docker container) that runs cloud services *on your machine* so you can develop and test **for free**, then switch to the real cloud by changing configuration only.

**Why we do this:** the AWS credit is a hard **$99 ceiling** that expires mid-June. Building and debugging directly against real AWS would burn money and time. So you build locally against an emulator at **$0**, and only run on real AWS when you deliberately choose to. Your code switches **cloud ↔ local by config** (the backend-adapter pattern, plan Section B).

---

## 2. Standard practices (apply to every emulator)

These are not LocalStack-specific. Any emulator we adopt follows all of these:

1. **Project-owned, not global.** Each emulator's setup lives **in the repo** under `infrastructure/<name>/`, version-controlled. A teammate runs `docker compose up` — no manual install on their machine.
2. **Use the free edition; document the free/paid boundary.** Know exactly what the free tier does *not* cover, and how we work around it. *(LocalStack Community is free, but **Glue & Athena are Pro-only (~$45/mo)** → MarketPulse uses **DuckDB** and **PySpark** locally instead. Azurite is fully free.)*
3. **Dummy credentials, never real.** Emulators accept placeholder credentials. **Never point real cloud keys at an emulator.**
4. **Critical health check before use (fail fast).** "Container up" ≠ "services ready." Gate anything that depends on the emulator on a real readiness check, and fail with a clear message if it isn't ready.
5. **Bug-fixing the local env is first-class work.** When an emulator misbehaves, fix it at the root (no band-aids) and **record the issue + fix** in the runbook so the next person doesn't lose time.
6. **Gitignore runtime state.** Emulators write caches/certs/volumes — never commit them.

---

## 3. Prerequisites (install once on your machine)

| Tool | Why | Notes |
|---|---|---|
| **Docker** (Desktop or Engine) | runs the emulators | required for everything local |
| **AWS CLI v2** | talk to AWS *and* LocalStack | + `awscli-local` (`awslocal`) wrapper auto-targets LocalStack |
| **Terraform** + **`tflocal`** | infrastructure-as-code; `tflocal` applies it against LocalStack | the real cloud apply is run by **you** (plan Section N) |
| **Python** via **`uv`** | the app runtime + env manager | fast, reproducible envs |
| **DuckDB** | local query engine (stands in for Athena) | free; the Athena adapter target |
| **Ollama** | local LLM (stands in for Bedrock) | free; the Bedrock adapter target |

> Confirm each with a version check (e.g. `docker --version`, `aws --version`, `terraform -version`, `uv --version`). If something is a **core dependency** you're unsure about, confirm before proceeding — don't guess.

---

## 4. Current emulator — LocalStack (AWS)

LocalStack lives at `infrastructure/localstack/` and is started with `docker compose up`. Endpoint: **`http://localhost:4566`**.

### 4.1 — Run it
```bash
cd infrastructure/localstack
docker compose up -d        # start LocalStack + the dashboard UI
docker compose ps           # LocalStack should show "healthy"
docker compose down         # stop
```
- LocalStack gateway: `http://localhost:4566`
- Dashboard UI: `http://localhost:8080`

### 4.2 — Credentials (dummy — never real)
Set these in `config/environments/local.env` and your shell when using the CLI locally:
```bash
AWS_ENDPOINT_URL=http://localhost:4566
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
AWS_DEFAULT_REGION=us-east-1
```
> ⚠️ Never put **real** AWS keys here. Real keys live in `~/.aws/` and are used only against real AWS (plan Section O).

### 4.3 — What's included / excluded (the free/paid boundary)
- **Included (Community, free):** `s3`, `kinesis`, `lambda`, `sqs`, `sns`, `events` (EventBridge), `stepfunctions`, `secretsmanager`, `logs`, `iam`, `sts`.
- **NOT included:** **Glue** and **Athena** are LocalStack **Pro** (~$45/mo). MarketPulse keeps these free with **DuckDB** (query, in place of Athena) and **PySpark** (in place of Glue Spark). Code switches by config — plan Section B.

### 4.4 — Critical readiness check (do this before anything runs)
"Container up" is not "ready." Confirm the services you need are live:
```bash
curl -s http://localhost:4566/_localstack/health
```
Look for the needed services showing `running` / `available`. The compose `healthcheck` already gates the UI on this; **your setup/test scripts must do the same** and exit with a clear message ("LocalStack S3 not ready — run docker compose up in infrastructure/localstack") if not.

### 4.5 — Do not commit runtime state
Add to the repo `.gitignore`:
```gitignore
infrastructure/localstack/volume/
```

### 4.6 — Smoke test (proves it works)
```bash
docker compose -f infrastructure/localstack/docker-compose.yml up -d
curl -s http://localhost:4566/_localstack/health        # s3 should be running
awslocal s3 mb s3://marketpulse-dev-bucket-bronze       # create a bucket
awslocal s3 ls                                          # see it listed
```

---

## 5. Adding another emulator later (e.g. Azurite)

> **Azurite** emulates **Azure Blob/Queue/Table storage** locally — the Azure equivalent of LocalStack's S3. **It is not needed today** (MarketPulse is AWS-only), but **if** the project ever uses Azure storage, add it here following the **same checklist** — no new philosophy:

1. Create `infrastructure/azurite/docker-compose.yml` (project-owned, composable).
2. Use the free image; document any boundary.
3. Use **dummy credentials**; switch by config (Azure uses a connection string / account key, not real secrets locally).
4. Add a **healthcheck + readiness gate** (probe the blob endpoint) before dependents run.
5. **Gitignore** its runtime state.
6. Document it in this file (a new "Current emulators" entry) and in the runbook.

The point: the rules in Section 2 are the contract; each emulator is just a new instance of the same pattern.

---

## 6. Troubleshooting (and the habit)

Treat local-infra bugs as real project work — **root-cause fixes, documented in the runbook.** Common first checks:

- **Not healthy?** `docker compose ps`; `docker compose logs localstack`. Set `LOCALSTACK_DEBUG=1` temporarily for verbose logs.
- **Port already in use** (4566 / 8080)? Stop the conflicting process or remap the port in compose.
- **Calls fail / behave oddly?** Confirm you're hitting the emulator (`AWS_ENDPOINT_URL` set) with dummy creds — not real AWS. Re-check the service is in the included (free) list.
- **A Pro-only service "doesn't work"** (e.g. Glue)? Expected — use the local twin (DuckDB / PySpark) per Section 4.3.

> Whenever you solve one of these, add a one-line entry to the runbook ("symptom → cause → fix"). That's how the dev environment stays reliable for the next developer.
