# ── Bootstrap: the remote-state backend for the MAIN infra/ stack ─────────────
# Creates the S3 bucket + DynamoDB lock table that hold infra/'s Terraform state.
# Chicken-and-egg: the backend infra cannot store its own state in itself, so this
# small config uses LOCAL state (its own terraform.tfstate stays on disk, gitignored).
# Run ONCE, by hand, with real AWS creds. Then infra/ points its `backend "s3"` here.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project     = "marketpulse"
      environment = var.environment
      owner       = var.owner
      managed-by  = "terraform"
    }
  }
}

# Account ID makes the state-bucket name globally unique AND deterministic (no random suffix),
# so the name is stable enough to reference from infra/'s backend config.
data "aws_caller_identity" "current" {}

locals {
  state_bucket = "marketpulse-${var.environment}-tfstate-${data.aws_caller_identity.current.account_id}"
  lock_table   = "marketpulse-${var.environment}-tflock"
}

# State bucket: versioned (recover prior state), encrypted (SSE-S3 — no CMK dependency), private.
resource "aws_s3_bucket" "tfstate" {
  bucket = local.state_bucket
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # SSE-S3 keeps the backend independent of the lake CMK
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lock table: stops concurrent applies from corrupting state. PAY_PER_REQUEST ~= $0 at our usage.
resource "aws_dynamodb_table" "tflock" {
  name         = local.lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}
