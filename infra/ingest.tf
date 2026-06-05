# ── Stage 1: batch-ingest component — secret, IAM role, Lambda, and EventBridge schedule ──

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

# Deployment package = our code only (src/ + config/). Run scripts/build_lambda.sh first to
# stage infra/build/lambda; pandas/pyarrow/awswrangler come from the layer (var below).
data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.root}/build/lambda"
  output_path = "${path.root}/build/ingest.zip"
}

# The ingest Lambda — assumes the least-privilege role, reads the secret, writes Parquet to bronze.
module "lambda_ingest" {
  source        = "./modules/lambda"
  function_name = local.ingest_function_name
  role_arn      = module.iam_ingest.role_arn
  source_zip    = data.archive_file.ingest.output_path
  source_hash   = data.archive_file.ingest.output_base64sha256
  layers        = var.awswrangler_layer_arn != "" ? [var.awswrangler_layer_arn] : []

  environment = {
    BRONZE_BUCKET          = module.s3["bronze"].bucket_id
    MARKETDATA_SECRET_NAME = module.secret.secret_name
  }
}

# Fire the Lambda on a schedule.
module "eventbridge_ingest" {
  source              = "./modules/eventbridge"
  name                = "${local.name_prefix}-rule-ingest-schedule"
  schedule_expression = var.ingest_schedule
  target_lambda_arn   = module.lambda_ingest.function_arn
  target_lambda_name  = module.lambda_ingest.function_name
}
