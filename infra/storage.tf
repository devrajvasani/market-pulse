# ── Stage 1: storage foundation — one KMS key + the three medallion-lake buckets ──

locals {
  name_prefix = "marketpulse-${var.environment}"
  layers      = ["bronze", "silver", "gold"]
}

# Random suffix so the globally-unique bucket names won't collide with other AWS accounts.
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# One CMK encrypts the whole lake (and, later, the secret).
module "kms" {
  source = "./modules/kms"
  name   = "${local.name_prefix}-kms-lake"
}

# bronze / silver / gold buckets, all identical settings via one reusable module.
module "s3" {
  source   = "./modules/s3"
  for_each = toset(local.layers)

  bucket_name = "${local.name_prefix}-bucket-${each.key}-${random_id.bucket_suffix.hex}"
  kms_key_arn = module.kms.key_arn
}
