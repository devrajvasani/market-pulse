variable "function_name" {
  description = "Lambda function name, e.g. \"marketpulse-dev-lambda-batch-ingest\"."
  type        = string
}

variable "role_arn" {
  description = "ARN of the execution role the function assumes."
  type        = string
}

variable "source_zip" {
  description = "Path to the deployment .zip (from archive_file)."
  type        = string
}

variable "source_hash" {
  description = "base64-sha256 of the zip — changes trigger a redeploy."
  type        = string
}

variable "handler" {
  description = "Entry point: <module>.<function>."
  type        = string
  default     = "src.ingestion.batch.handler.handler"
}

variable "runtime" {
  description = "Lambda runtime."
  type        = string
  default     = "python3.12"
}

variable "timeout_seconds" {
  description = "Function timeout."
  type        = number
  default     = 60
}

variable "memory_mb" {
  description = "Function memory."
  type        = number
  default     = 256
}

variable "layers" {
  description = "Layer ARNs (e.g. AWS SDK for pandas). Empty on LocalStack."
  type        = list(string)
  default     = []
}

variable "environment" {
  description = "Environment variables for the function."
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the function's log group."
  type        = number
  default     = 14
}
