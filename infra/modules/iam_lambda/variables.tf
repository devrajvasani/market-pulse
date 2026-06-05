variable "name" {
  description = "Role name, e.g. \"marketpulse-dev-role-lambda-ingest\"."
  type        = string
}

variable "function_name" {
  description = "Lambda function name this role serves (used to scope the CloudWatch Logs ARN)."
  type        = string
}

variable "bronze_bucket_arn" {
  description = "ARN of the Bronze bucket the Lambda may write to."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the CMK the Lambda uses to write SSE-KMS objects."
  type        = string
}

variable "secret_arn" {
  description = "ARN of the one secret the Lambda may read."
  type        = string
}
