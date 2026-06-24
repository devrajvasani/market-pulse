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

variable "enable_glue_spark" {
  description = "Build the Stage 3d Glue PySpark learning job + its least-privilege role (AWS-only; requires enable_catalog=true). Opt-in: set true to deploy + run once or twice, then false + apply to remove. An idle Glue job costs $0; a run is a few cents."
  type        = bool
  default     = false
}

variable "enable_streaming" {
  description = "Build the Stage 4 streaming path (Kinesis trades stream + consumer Lambda + SQS DLQ + event-source mapping). Opt-in: Kinesis bills ~$0.015/shard-hour, so set true for a test window then false + apply to remove. LocalStack runs it free via tflocal."
  type        = bool
  default     = false
}

variable "enable_rag" {
  description = "Build the Stage 5 RAG assistant (ingest + index + assistant Lambdas + least-privilege roles). Requires enable_catalog=true (the assistant queries Gold via Athena/Glue). Opt-in + AWS-deferred: the assistant runs locally via CLI/API (Ollama+DuckDB); a real Bedrock deploy also needs a faiss/numpy Lambda layer or container image (see docs/stages/stage-5-rag.md)."
  type        = bool
  default     = false
}

variable "bedrock_embed_model" {
  description = "Bedrock embeddings model id used by the RAG index Lambda (scopes its bedrock:InvokeModel ARN)."
  type        = string
  default     = "amazon.titan-embed-text-v2:0"
}

variable "bedrock_gen_model" {
  description = "Bedrock generation model id used by the RAG assistant Lambda (scopes its bedrock:InvokeModel ARN)."
  type        = string
  default     = "anthropic.claude-3-5-haiku-20241022-v1:0"
}
