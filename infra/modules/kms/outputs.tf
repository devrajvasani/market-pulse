output "key_arn" {
  description = "ARN of the CMK (referenced by S3 encryption, the secret, and IAM policies)."
  value       = aws_kms_key.this.arn
}

output "key_id" {
  description = "Key ID of the CMK."
  value       = aws_kms_key.this.key_id
}

output "alias_name" {
  description = "Alias of the CMK."
  value       = aws_kms_alias.this.name
}
