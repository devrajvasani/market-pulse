output "role_arn" {
  description = "ARN of the Lambda execution role (assigned to the function)."
  value       = aws_iam_role.this.arn
}

output "role_name" {
  description = "Name of the Lambda execution role."
  value       = aws_iam_role.this.name
}
