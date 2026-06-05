# MarketPulse — root Terraform. STAGE 0: provider + default_tags only, NO resources.
# Resources are added per stage; YOU run `terraform apply` after review (plan Section N).
# Local mode runs this via `tflocal`, which overrides the AWS endpoints to LocalStack.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region

  # Every resource inherits these tags automatically — we never tag by hand (Section B2.1).
  default_tags {
    tags = {
      project     = "marketpulse"
      environment = var.environment
      owner       = var.owner
      managed-by  = "terraform"
    }
  }
}
