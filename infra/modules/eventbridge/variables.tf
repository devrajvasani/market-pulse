variable "name" {
  description = "Rule name, e.g. \"marketpulse-dev-rule-ingest-schedule\"."
  type        = string
}

variable "schedule_expression" {
  description = "When to fire: rate(...) or cron(...)."
  type        = string
}

variable "target_lambda_arn" {
  description = "ARN of the Lambda to invoke on schedule."
  type        = string
}

variable "target_lambda_name" {
  description = "Name of the target Lambda (for the invoke permission)."
  type        = string
}
