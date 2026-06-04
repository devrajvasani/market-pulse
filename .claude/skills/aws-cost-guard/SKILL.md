---
name: aws-cost-guard
description: Cost-safety rules for the MarketPulse AWS project. Use whenever creating, changing, or deploying any AWS resource, writing Terraform, estimating cost, or before any `terraform apply`. Enforces the $99 hard ceiling and warns about expensive services.
---

# AWS Cost Guard (MarketPulse — $99 hard ceiling)

Total AWS spend on this project MUST stay under **$99** (hard ceiling, either account type). Credits expire ~June 18 — export all work before then.

## Before proposing ANY resource
- State its **estimated cost** (per hour and per month) and whether it is **always-on** or **on-demand**.
- Confirm the change keeps cumulative spend **safely under $99**.
- If a change could risk exceeding the budget, **STOP and flag it to the user** before proceeding.

## Never suggest these cost traps
- **MWAA** (managed Airflow, ~$350/mo) — use Step Functions + EventBridge instead.
- **Redshift provisioned clusters** — use Athena (pay-per-query).
- **MSK** (managed Kafka) — use Kinesis Data Streams.
- **OpenSearch** (incl. as a vector store) — use a FAISS index stored in S3.
- **NAT Gateway**, always-on **EC2/RDS**, unattached **EBS volumes / Elastic IPs**.

## Prefer cheap-by-default choices
- **Athena** (pay per TB scanned) over Redshift.
- For transforms: **Athena SQL ELT** (CTAS / dbt-athena) is the default — cheap and writes Iceberg. Use **Glue Spark** only when PySpark is genuinely needed (mind the 2-DPU minimum, ~$0.88/hr). **Glue Python-shell** (~$0.003/hr, cheapest job type) is for *light Python tasks*, **not** Iceberg writes.
- **Kinesis** running only during test windows — **stop it when idle**.
- **Lambda** over EC2 for compute.
- **FAISS-in-S3** over OpenSearch for vectors.
- **Bedrock** on-demand with small token volumes.
- **S3 + Parquet + partitioning** so Athena scans little data (less data scanned = less cost).

## Budgets & guardrails
- Ensure **AWS Budgets** exist with alerts at **$20 / $50 / $80**, plus **Budget Actions** that can stop/restrict resources at a threshold. Remind the user to keep them on.

## Provisioning & teardown (project rule)
- Do **NOT** run `terraform apply`/`terraform destroy` or mutating `aws` CLI commands. Explain the cost and **instruct the user to run them**. `terraform plan` (read-only) is fine.
- Every resource created must be **tracked and removable**. Remind the user to `terraform destroy` test resources, stop Kinesis when not in use, and export work before the credit expiry.
