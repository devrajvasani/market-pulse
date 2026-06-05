# Secret CONTAINER only — the VALUE is set out-of-band (Console/CLI) so it never lands in
# Terraform state in plaintext (the state trap, plan Section O.4).
resource "aws_secretsmanager_secret" "this" {
  name                    = var.name
  description             = var.description
  kms_key_id              = var.kms_key_arn # encrypt the secret with our CMK
  recovery_window_in_days = 7               # safety window before a deleted secret is purged
}
