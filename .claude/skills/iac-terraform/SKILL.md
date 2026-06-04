---
name: iac-terraform
description: Terraform / infrastructure-as-code conventions for the MarketPulse project. Use when writing or editing any Terraform (.tf/.tfvars) files, IAM policies, or infrastructure configuration.
---

# IaC / Terraform conventions (MarketPulse)

## Structure
- Reusable modules under `infra/modules/<service>/`; environment composition under `infra/` (or `infra/envs/dev/`).
- One module per logical service (s3, kinesis, lambda, glue, stepfunctions, etc.).

## Naming
- Every resource name follows **`{project}-{env}-{service}-{purpose}`**.
- Examples: `marketpulse-dev-bucket-bronze`, `marketpulse-dev-stream-trades`, `marketpulse-dev-lambda-ingest`.
- Project slug is `marketpulse` (one word, no internal hyphen, so the 4 parts stay parseable).

## Tags
- Set `default_tags` on the AWS provider so every resource is tagged automatically: `project=marketpulse`, `environment=dev`, `owner=<user>`, `managed-by=terraform`. Do not hand-tag each resource.

## IAM — least privilege (strict)
- One role per component; grant **only** the specific actions on the specific resource ARNs needed.
- **Never** use wildcard actions (`"*"`, `s3:*`) or `"Resource": "*"`, and never attach broad managed policies (e.g. `AdministratorAccess`) to app roles. If a wildcard is truly unavoidable, call it out explicitly to the user.

## Encryption & exposure
- Enable **KMS** encryption on S3 buckets and secrets. **Block all public access** on S3. Default to least exposure.

## Secrets — the Terraform-state trap
- Terraform state can store secret **values in plaintext**. So: create the secret **container** (e.g. `aws_secretsmanager_secret`) in Terraform, but set the secret **value out-of-band** (Console/CLI) after apply.
- Never put real secret values in `.tf` or `.tfvars`. Mark sensitive variables `sensitive = true`.
- Never commit `*.tfstate` or secret-bearing `*.tfvars` (they belong in `.gitignore`).

## Local development
- Use **`tflocal`** (the LocalStack wrapper for Terraform) to apply against LocalStack locally and for free. The real cloud apply is run by the user.

## Provisioning rule (project)
- Write the Terraform and run **`terraform plan`** (read-only) to show what will change — but **never run `terraform apply`/`terraform destroy`**. Explain the change and **instruct the user to apply it** (see plan Section N).
- Before proposing any new resource, apply the **aws-cost-guard** cost check.
