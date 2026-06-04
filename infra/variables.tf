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
