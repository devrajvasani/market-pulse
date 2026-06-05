# No resources yet (Stage 0). Resource outputs are added as infrastructure lands.
output "project" {
  description = "Project slug used across resource names + tags."
  value       = "marketpulse"
}

output "region" {
  description = "Configured AWS region."
  value       = var.region
}

output "kms_key_arn" {
  description = "ARN of the lake CMK."
  value       = module.kms.key_arn
}

output "bucket_names" {
  description = "Medallion-lake bucket names by layer."
  value       = { for layer, m in module.s3 : layer => m.bucket_id }
}

output "secret_arn" {
  description = "ARN of the market-data API-key secret (value set out-of-band)."
  value       = module.secret.secret_arn
}

output "ingest_role_arn" {
  description = "ARN of the ingest Lambda's least-privilege execution role."
  value       = module.iam_ingest.role_arn
}

output "ingest_function_name" {
  description = "Name of the batch-ingest Lambda."
  value       = module.lambda_ingest.function_name
}

output "ingest_schedule_rule" {
  description = "Name of the EventBridge schedule rule."
  value       = module.eventbridge_ingest.rule_name
}
