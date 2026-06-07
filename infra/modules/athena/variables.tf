variable "workgroup_name" {
  description = "Athena workgroup name, e.g. \"marketpulse-dev-athena-analytics\"."
  type        = string
}

variable "results_location" {
  description = "S3 URI for query results, e.g. \"s3://bucket/query-results/\"."
  type        = string
}

variable "kms_key_arn" {
  description = "CMK ARN used to SSE-KMS encrypt Athena query results."
  type        = string
}

variable "bytes_scanned_cutoff" {
  description = "Per-query bytes-scanned cap (the cost guardrail). AWS minimum is 10 MB (10485760)."
  type        = number

  validation {
    condition     = var.bytes_scanned_cutoff >= 10485760
    error_message = "bytes_scanned_cutoff must be at least 10 MB (10485760 bytes)."
  }
}
