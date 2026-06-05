variable "region" {
  description = "AWS region for the state bucket + lock table."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment slug used in the state-bucket + lock-table names + tags."
  type        = string
  default     = "dev"
}

variable "owner" {
  description = "Owner identifier for the `owner` tag. Pass via -var or TF_VAR_owner (never hardcoded)."
  type        = string
}
