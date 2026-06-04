# No resources yet (Stage 0). Resource outputs are added as infrastructure lands.
output "project" {
  description = "Project slug used across resource names + tags."
  value       = "marketpulse"
}

output "region" {
  description = "Configured AWS region."
  value       = var.region
}
