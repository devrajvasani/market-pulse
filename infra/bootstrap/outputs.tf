output "state_bucket" {
  description = "S3 bucket holding infra/'s remote state. Copy into infra/backend.hcl."
  value       = aws_s3_bucket.tfstate.id
}

output "lock_table" {
  description = "DynamoDB state-lock table. Copy into infra/backend.hcl."
  value       = aws_dynamodb_table.tflock.name
}

output "region" {
  description = "Region of the backend resources."
  value       = var.region
}
