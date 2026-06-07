output "workgroup_name" {
  description = "Athena workgroup name (enforces the bytes-scanned cap + results location)."
  value       = aws_athena_workgroup.this.name
}
