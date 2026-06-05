# Customer-managed KMS key (CMK) used to encrypt the S3 lake + Secrets Manager at rest.
resource "aws_kms_key" "this" {
  description             = "MarketPulse CMK: ${var.name}"
  deletion_window_in_days = 7    # safety window before a scheduled-deleted key is destroyed
  enable_key_rotation     = true # AWS rotates the key material automatically (~yearly)
}

# Friendly alias (alias/marketpulse-dev-kms-lake) so we reference a name, not a raw key ID.
resource "aws_kms_alias" "this" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.this.key_id
}
