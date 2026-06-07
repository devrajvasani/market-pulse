variable "region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment slug used in resource names + tags (dev/staging/prod)."
  type        = string
  default     = "dev"
}

variable "owner" {
  description = "Owner identifier for the `owner` cost-allocation tag. Set in terraform.tfvars (gitignored) or via TF_VAR_owner — never hardcoded."
  type        = string
}

variable "awswrangler_layer_arn" {
  description = "ARN of the AWS SDK for pandas (awswrangler) Lambda layer for the real deploy. Leave empty for LocalStack (the Lambda isn't invoked there)."
  type        = string
  default     = ""
}

variable "ingest_schedule" {
  description = "EventBridge schedule for batch ingestion — rate(...) or cron(...)."
  type        = string
  default     = "rate(1 hour)"
}

variable "enable_catalog" {
  description = "Build the Stage 2 Glue catalog + Athena workgroup + results bucket. Set false for LocalStack (Glue/Athena are LocalStack Pro; the local query twin is DuckDB)."
  type        = bool
  default     = true
}
