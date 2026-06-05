# ── Stage 1: batch-ingest component — secret + IAM role (Lambda + schedule come next) ──

locals {
  ingest_function_name = "${local.name_prefix}-lambda-batch-ingest"
}

# The market-data (CoinGecko) API key — container only; VALUE set out-of-band, never in TF state.
module "secret" {
  source      = "./modules/secret"
  name        = "${local.name_prefix}-secret-marketdata-apikey"
  description = "CoinGecko API key for batch ingestion (value set out-of-band)."
  kms_key_arn = module.kms.key_arn
}

# Least-privilege execution role for the ingest Lambda.
module "iam_ingest" {
  source            = "./modules/iam_lambda"
  name              = "${local.name_prefix}-role-lambda-ingest"
  function_name     = local.ingest_function_name
  bronze_bucket_arn = module.s3["bronze"].bucket_arn
  kms_key_arn       = module.kms.key_arn
  secret_arn        = module.secret.secret_arn
}
