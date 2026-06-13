# ── Stage 3d: Glue PySpark learning job (Bronze -> Silver, parallel to the dbt path) ──
#
# The SQL/dbt path is primary; this Glue Spark job exists to LEARN Spark and is run once
# or twice. It writes a SEPARATE Iceberg table (silver_prices_spark) so it sits alongside
# the dbt `silver_prices`, not on top of it. Gated by `enable_glue_spark` (default false):
# flip it on to deploy + run, then off + apply to remove. An idle Glue job costs $0; a run
# is ~2 DPU x a few minutes (cents). AWS-only — assumes enable_catalog = true (the Glue DB,
# Athena-results scratch bucket, and silver bucket already exist).

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Upload the job script to the (already-encrypted) Athena-results scratch bucket. source_hash
# re-uploads it whenever the local file changes (etag is unreliable for SSE-KMS objects).
resource "aws_s3_object" "glue_bronze_to_silver_script" {
  count       = var.enable_glue_spark ? 1 : 0
  bucket      = module.athena_results[0].bucket_id
  key         = "glue/scripts/bronze_to_silver.py"
  source      = "${path.root}/../src/transform/glue_spark/bronze_to_silver.py"
  source_hash = filesha256("${path.root}/../src/transform/glue_spark/bronze_to_silver.py")

  # Fail fast with a clear message if enabled without enable_catalog — the Glue DB and
  # athena-results bucket this job depends on only exist when enable_catalog = true.
  lifecycle {
    precondition {
      condition     = !var.enable_glue_spark || var.enable_catalog
      error_message = "enable_glue_spark requires enable_catalog = true."
    }
  }
}

# Trust policy: ONLY the Glue service may assume this role.
resource "aws_iam_role" "glue_bronze_to_silver" {
  count = var.enable_glue_spark ? 1 : 0
  name  = "${local.name_prefix}-role-glue-bronze-to-silver"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
      # Confused-deputy guard: only Glue acting for THIS account may assume the role.
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

# Least-privilege: read Bronze, read/write Silver (Iceberg data + metadata), read the script
# and read/write the temp dir, table ops on the one Glue database, the lake CMK, Glue logs.
# No wildcard actions, no "Resource": "*".
resource "aws_iam_role_policy" "glue_bronze_to_silver" {
  count = var.enable_glue_spark ? 1 : 0
  name  = "${local.name_prefix}-role-glue-bronze-to-silver-policy"
  role  = aws_iam_role.glue_bronze_to_silver[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadBronzeObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${module.s3["bronze"].bucket_arn}/*"
      },
      {
        # Iceberg writes data + metadata and reads back during commits.
        Sid      = "ReadWriteSilverObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${module.s3["silver"].bucket_arn}/*"
      },
      {
        # Read the job script + read/write the Glue TempDir (scoped to the glue/ prefix).
        Sid      = "ReadScriptReadWriteTemp"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${module.athena_results[0].bucket_arn}/glue/*"
      },
      {
        Sid    = "ListLakeBuckets"
        Effect = "Allow"
        Action = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = [
          module.s3["bronze"].bucket_arn,
          module.s3["silver"].bucket_arn,
          module.athena_results[0].bucket_arn,
        ]
      },
      {
        # Register/update the Iceberg table in the one Glue database (table + DB + catalog
        # ARNs). No DeleteTable: createOrReplace only needs Create/Update, and omitting it
        # means this job can never drop the sibling dbt-managed tables in the same database.
        Sid    = "GlueCatalogTableOps"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetTables",
          "glue:CreateTable",
          "glue:UpdateTable",
        ]
        Resource = [
          "arn:aws:glue:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:catalog",
          "arn:aws:glue:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:database/${local.glue_database}",
          "arn:aws:glue:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:table/${local.glue_database}/*",
        ]
      },
      {
        # SSE-KMS read (Bronze) + write (Silver/temp) under the lake CMK.
        Sid      = "UseLakeCmk"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:Encrypt", "kms:DescribeKey"]
        Resource = module.kms.key_arn
      },
      {
        # Scoped to the Glue JOB log groups only (not all of /aws-glue/, which would also
        # cover crawlers/sessions). Covers default output/error + continuous logs-v2.
        Sid    = "WriteGlueJobLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = [
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/jobs/output:*",
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/jobs/error:*",
          "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws-glue/jobs/logs-v2:*",
        ]
      },
    ]
  })
}

# The Glue Spark job. 2 x G.1X for a couple of minutes on tiny data; no retries; 10-min cap.
resource "aws_glue_job" "bronze_to_silver" {
  count             = var.enable_glue_spark ? 1 : 0
  name              = "${local.name_prefix}-glue-bronze-to-silver"
  role_arn          = aws_iam_role.glue_bronze_to_silver[0].arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  max_retries       = 0
  timeout           = 10 # minutes

  command {
    name            = "glueetl"
    script_location = "s3://${module.athena_results[0].bucket_id}/glue/scripts/bronze_to_silver.py"
    python_version  = "3"
  }

  default_arguments = {
    "--job-language"     = "python"
    "--datalake-formats" = "iceberg" # puts the Iceberg runtime on the classpath
    "--TempDir"          = "s3://${module.athena_results[0].bucket_id}/glue/tmp/"
    "--bronze_path"      = "s3://${module.s3["bronze"].bucket_id}/prices/"
    "--silver_database"  = local.glue_database
    "--silver_table"     = "silver_prices_spark"
    "--warehouse"        = "s3://${module.s3["silver"].bucket_id}/"
  }

  execution_property {
    max_concurrent_runs = 1
  }
}
