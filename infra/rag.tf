# ── Stage 5: RAG assistant — ingest + index + assistant Lambdas (gated, AWS-deferred) ──
#
# The hybrid RAG runs locally via the CLI/API (Ollama + DuckDB). This is the AWS deploy shape:
# three least-privilege Lambdas behind `enable_rag` (default false). Topology:
#   rag-ingest    : RSS -> bronze/docs/      (scheduled in Stage 6)
#   rag-index     : bronze/docs -> embed -> gold/rag/<env>/ FAISS index
#   rag-assistant : question -> retrieve (gold/rag) + templated Gold query (Athena) -> Bedrock
#
# NOTE (deferred): a real deploy needs a Lambda LAYER or CONTAINER IMAGE carrying faiss + numpy
# (binary deps) — see docs/stages/stage-5-rag.md. Gated off + not applied this stage (Bedrock
# access uncertain + the free period is ending). Requires enable_catalog=true (Athena/Glue Gold).
# data.aws_caller_identity/aws_region are declared in transform.tf (same root module).

locals {
  rag_ingest_name    = "${local.name_prefix}-lambda-rag-ingest"
  rag_index_name     = "${local.name_prefix}-lambda-rag-index"
  rag_assistant_name = "${local.name_prefix}-lambda-rag-assistant"
  rag_index_prefix   = "rag/${var.environment}" # env-namespaced S3 prefix in the gold bucket
  rag_workgroup_name = "${local.name_prefix}-athena-analytics"

  # Specific Bedrock foundation-model ARNs the Lambdas may invoke (no wildcards).
  rag_embed_model_arn = "arn:aws:bedrock:${data.aws_region.current.name}::foundation-model/${var.bedrock_embed_model}"
  rag_gen_model_arn   = "arn:aws:bedrock:${data.aws_region.current.name}::foundation-model/${var.bedrock_gen_model}"

  # Trust policy shared by the three RAG roles: only Lambda may assume them.
  rag_lambda_assume_role = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Deployment package = the rag/ project + config/ + src/ (the Gold seam imports config.settings).
# Build into build/rag-lambda first; faiss+numpy must come from a layer/container (deferred).
data "archive_file" "rag" {
  count       = var.enable_rag ? 1 : 0
  type        = "zip"
  source_dir  = "${path.root}/build/rag-lambda"
  output_path = "${path.root}/build/rag.zip"
}

# Guard: the assistant queries Gold via Athena/Glue, which only exist when enable_catalog = true.
resource "terraform_data" "rag_requires_catalog" {
  count = var.enable_rag ? 1 : 0
  lifecycle {
    precondition {
      condition     = var.enable_catalog
      error_message = "enable_rag requires enable_catalog = true (the assistant queries Gold via Athena/Glue)."
    }
  }
}

# ── rag-ingest: write cleaned docs to bronze/docs/ ──────────────────────────────
resource "aws_iam_role" "rag_ingest" {
  count              = var.enable_rag ? 1 : 0
  name               = "${local.name_prefix}-role-lambda-rag-ingest"
  assume_role_policy = local.rag_lambda_assume_role
}

resource "aws_iam_role_policy" "rag_ingest" {
  count = var.enable_rag ? 1 : 0
  name  = "${local.name_prefix}-role-lambda-rag-ingest-policy"
  role  = aws_iam_role.rag_ingest[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Idempotent write: HeadObject (GetObject) to detect existing + PutObject to upsert.
        Sid      = "WriteReadBronzeDocs"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${module.s3["bronze"].bucket_arn}/docs/*"
      },
      {
        Sid      = "UseLakeCmk"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = module.kms.key_arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.rag_ingest_name}:*"
      },
    ]
  })
}

module "lambda_rag_ingest" {
  source        = "./modules/lambda"
  count         = var.enable_rag ? 1 : 0
  function_name = local.rag_ingest_name
  role_arn      = aws_iam_role.rag_ingest[0].arn
  source_zip    = data.archive_file.rag[0].output_path
  source_hash   = data.archive_file.rag[0].output_base64sha256
  handler       = "rag.lambda_handler.ingest_handler"
  environment = {
    APP_ENV       = "aws"
    BRONZE_BUCKET = module.s3["bronze"].bucket_id
  }
}

# ── rag-index: embed bronze/docs -> FAISS in gold/rag/<env>/ ─────────────────────
resource "aws_iam_role" "rag_index" {
  count              = var.enable_rag ? 1 : 0
  name               = "${local.name_prefix}-role-lambda-rag-index"
  assume_role_policy = local.rag_lambda_assume_role
}

resource "aws_iam_role_policy" "rag_index" {
  count = var.enable_rag ? 1 : 0
  name  = "${local.name_prefix}-role-lambda-rag-index-policy"
  role  = aws_iam_role.rag_index[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadBronzeDocs"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${module.s3["bronze"].bucket_arn}/docs/*"
      },
      {
        Sid       = "ListBronzeDocs"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = module.s3["bronze"].bucket_arn
        Condition = { StringLike = { "s3:prefix" = ["docs/*"] } }
      },
      {
        # The indexer only writes the FAISS artifacts; it never reads them back.
        Sid      = "WriteRagIndex"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${module.s3["gold"].bucket_arn}/${local.rag_index_prefix}/*"
      },
      {
        Sid      = "InvokeEmbeddings"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = local.rag_embed_model_arn
      },
      {
        Sid      = "UseLakeCmk"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = module.kms.key_arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.rag_index_name}:*"
      },
    ]
  })
}

module "lambda_rag_index" {
  source        = "./modules/lambda"
  count         = var.enable_rag ? 1 : 0
  function_name = local.rag_index_name
  role_arn      = aws_iam_role.rag_index[0].arn
  source_zip    = data.archive_file.rag[0].output_path
  source_hash   = data.archive_file.rag[0].output_base64sha256
  handler       = "rag.lambda_handler.index_handler"
  memory_mb     = 1024 # faiss build over many chunks
  environment = {
    APP_ENV             = "aws"
    BRONZE_BUCKET       = module.s3["bronze"].bucket_id
    GOLD_BUCKET         = module.s3["gold"].bucket_id
    RAG_INDEX_PREFIX    = local.rag_index_prefix
    BEDROCK_EMBED_MODEL = var.bedrock_embed_model
  }
}

# ── rag-assistant: retrieve + templated Gold query + Bedrock answer ──────────────
resource "aws_iam_role" "rag_assistant" {
  count              = var.enable_rag ? 1 : 0
  name               = "${local.name_prefix}-role-lambda-rag-assistant"
  assume_role_policy = local.rag_lambda_assume_role
}

resource "aws_iam_role_policy" "rag_assistant" {
  count = var.enable_rag ? 1 : 0
  name  = "${local.name_prefix}-role-lambda-rag-assistant-policy"
  role  = aws_iam_role.rag_assistant[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadRagIndex"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${module.s3["gold"].bucket_arn}/${local.rag_index_prefix}/*"
      },
      {
        # List only the Gold Iceberg data Athena scans (not the whole bucket / RAG index).
        Sid       = "ListGoldIceberg"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = module.s3["gold"].bucket_arn
        Condition = { StringLike = { "s3:prefix" = ["gold/*"] } }
      },
      {
        Sid      = "GetGoldIceberg"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${module.s3["gold"].bucket_arn}/gold/*"
      },
      {
        # Athena query lifecycle, scoped to the project workgroup.
        Sid    = "RunAthenaQueries"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
        ]
        Resource = "arn:aws:athena:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:workgroup/${local.rag_workgroup_name}"
      },
      {
        # Read-only Glue catalog access for the Gold tables (no create/update/delete).
        Sid    = "ReadGoldCatalog"
        Effect = "Allow"
        Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartitions"]
        Resource = [
          "arn:aws:glue:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:database/${local.glue_database}",
          "arn:aws:glue:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:table/${local.glue_database}/*",
        ]
      },
      {
        # Athena writes/reads query results in the dedicated results bucket.
        Sid      = "AthenaResults"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [module.athena_results[0].bucket_arn, "${module.athena_results[0].bucket_arn}/*"]
      },
      {
        # Embed the query (Titan) + compose the answer (Converse), streaming included.
        Sid      = "InvokeBedrock"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [local.rag_embed_model_arn, local.rag_gen_model_arn]
      },
      {
        Sid      = "UseLakeCmk"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = module.kms.key_arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.rag_assistant_name}:*"
      },
    ]
  })
}

module "lambda_rag_assistant" {
  source        = "./modules/lambda"
  count         = var.enable_rag ? 1 : 0
  function_name = local.rag_assistant_name
  role_arn      = aws_iam_role.rag_assistant[0].arn
  source_zip    = data.archive_file.rag[0].output_path
  source_hash   = data.archive_file.rag[0].output_base64sha256
  handler       = "rag.lambda_handler.assistant_handler"
  memory_mb     = 1024 # faiss load + search
  environment = {
    APP_ENV             = "aws"
    GOLD_BUCKET         = module.s3["gold"].bucket_id
    RAG_INDEX_PREFIX    = local.rag_index_prefix
    ATHENA_DATABASE     = local.glue_database
    ATHENA_WORKGROUP    = local.rag_workgroup_name
    BEDROCK_EMBED_MODEL = var.bedrock_embed_model
    BEDROCK_GEN_MODEL   = var.bedrock_gen_model
  }
}

output "rag_assistant_function_name" {
  description = "Name of the RAG assistant Lambda (null unless enable_rag)."
  value       = var.enable_rag ? module.lambda_rag_assistant[0].function_name : null
}
