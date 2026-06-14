# ── Stage 4: streaming — Kinesis trades stream + consumer Lambda + DLQ ──
#
# producer (container) -> Kinesis -> consumer Lambda -> bronze/trades/ (NDJSON), with an
# SQS dead-letter queue for batches that keep failing. Gated by `enable_streaming`
# (default false): Kinesis bills per shard-hour, so apply for a test window then destroy.
# The consumer writes NDJSON with boto3 ONLY (no awswrangler layer) -> runs identically on
# LocalStack and AWS. The producer runs as a container under the developer's creds (no role
# here). data.aws_caller_identity/aws_region are declared in transform.tf (same root module).

locals {
  trades_stream_name   = "${local.name_prefix}-stream-trades"
  trades_consumer_name = "${local.name_prefix}-lambda-trades-consumer"
}

# 1-shard provisioned stream, SSE-KMS with the lake CMK, 24h retention (the minimum).
resource "aws_kinesis_stream" "trades" {
  count            = var.enable_streaming ? 1 : 0
  name             = local.trades_stream_name
  shard_count      = 1
  retention_period = 24
  encryption_type  = "KMS"
  kms_key_id       = module.kms.key_arn

  stream_mode_details {
    stream_mode = "PROVISIONED"
  }
}

# Dead-letter queue: parks whole batches the consumer fails to process after retries.
resource "aws_sqs_queue" "trades_dlq" {
  count                     = var.enable_streaming ? 1 : 0
  name                      = "${local.name_prefix}-dlq-trades"
  message_retention_seconds = 1209600 # 14 days
  # CMK-everywhere posture (matches S3/Kinesis/Secret). The consumer's UseLakeCmk grant
  # already covers GenerateDataKey/Decrypt on this key for the DLQ writes.
  kms_master_key_id                 = module.kms.key_arn
  kms_data_key_reuse_period_seconds = 300
}

# Least-privilege execution role for the consumer Lambda.
resource "aws_iam_role" "trades_consumer" {
  count = var.enable_streaming ? 1 : 0
  name  = "${local.name_prefix}-role-lambda-trades-consumer"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "trades_consumer" {
  count = var.enable_streaming ? 1 : 0
  name  = "${local.name_prefix}-role-lambda-trades-consumer-policy"
  role  = aws_iam_role.trades_consumer[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # The event-source mapping polls Kinesis using this role.
        Sid    = "ReadTradesStream"
        Effect = "Allow"
        Action = [
          "kinesis:GetRecords",
          "kinesis:GetShardIterator",
          "kinesis:DescribeStream",
          "kinesis:DescribeStreamSummary",
          "kinesis:ListShards",
        ]
        Resource = aws_kinesis_stream.trades[0].arn
      },
      {
        Sid      = "WriteBronzeTrades"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${module.s3["bronze"].bucket_arn}/trades/*"
      },
      {
        # Decrypt SSE-KMS Kinesis records, generate data keys for the SSE-KMS S3 write + the
        # CMK-encrypted DLQ. DescribeKey is needed by the SDK to validate/resolve the CMK.
        Sid      = "UseLakeCmk"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
        Resource = module.kms.key_arn
      },
      {
        Sid      = "SendToDlq"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.trades_dlq[0].arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.trades_consumer_name}:*"
      },
    ]
  })
}

# Consumer Lambda — same code zip as ingest, different handler. NO awswrangler layer:
# it only needs boto3 (NDJSON write), so it runs unchanged on LocalStack.
module "lambda_trades_consumer" {
  source        = "./modules/lambda"
  count         = var.enable_streaming ? 1 : 0
  function_name = local.trades_consumer_name
  role_arn      = aws_iam_role.trades_consumer[0].arn
  source_zip    = data.archive_file.ingest.output_path
  source_hash   = data.archive_file.ingest.output_base64sha256
  handler       = "src.ingestion.streaming.consumer.handler"
  environment   = { BRONZE_BUCKET = module.s3["bronze"].bucket_id }
}

# Trigger: Kinesis -> consumer, in small batches, with the DLQ as the on-failure sink.
resource "aws_lambda_event_source_mapping" "trades" {
  count            = var.enable_streaming ? 1 : 0
  event_source_arn = aws_kinesis_stream.trades[0].arn
  function_name    = module.lambda_trades_consumer[0].function_arn
  # LATEST: process only records arriving after the mapping is enabled. Fine here -- the
  # stream is created in the same apply, so there are no pre-existing records to miss.
  starting_position                  = "LATEST"
  batch_size                         = 100
  maximum_batching_window_in_seconds = 5
  maximum_retry_attempts             = 2
  bisect_batch_on_function_error     = true

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.trades_dlq[0].arn
    }
  }
}

# Athena read-side for the streaming Bronze: a JSON-SerDe Glue table over bronze/trades/.
# (DuckDB reads the NDJSON directly via read_json; Athena needs this catalog table.)
# Metadata only, partition projection (no crawler), like bronze_prices but JSON. Gated on
# enable_streaming AND enable_catalog -> created only on the AWS path (LocalStack uses DuckDB).
# price/size are JSON strings from the exchange (cast to double in silver_trades); trade_id
# is a JSON number.
resource "aws_glue_catalog_table" "bronze_trades" {
  count         = var.enable_streaming && var.enable_catalog ? 1 : 0
  name          = "bronze_trades"
  database_name = module.glue_catalog[0].database_name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification                           = "json"
    EXTERNAL                                 = "TRUE"
    "projection.enabled"                     = "true"
    "projection.snapshot_date.type"          = "date"
    "projection.snapshot_date.format"        = "yyyy-MM-dd"
    "projection.snapshot_date.range"         = "2026-06-01,NOW"
    "projection.snapshot_date.interval"      = "1"
    "projection.snapshot_date.interval.unit" = "DAYS"
    "projection.snapshot_hour.type"          = "integer"
    "projection.snapshot_hour.range"         = "0,23"
    "projection.snapshot_hour.digits"        = "2"
    "storage.location.template"              = "s3://${module.s3["bronze"].bucket_id}/trades/snapshot_date=$${snapshot_date}/snapshot_hour=$${snapshot_hour}"
  }

  storage_descriptor {
    location      = "s3://${module.s3["bronze"].bucket_id}/trades/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters            = { "ignore.malformed.json" = "true" }
    }

    columns { # JSON value types: trade_id is a number; price/size are strings
      name = "trade_id"
      type = "bigint"
    }
    columns {
      name = "product_id"
      type = "string"
    }
    columns {
      name = "price"
      type = "string"
    }
    columns {
      name = "size"
      type = "string"
    }
    columns {
      name = "side"
      type = "string"
    }
    columns {
      name = "event_time"
      type = "string"
    }
    columns {
      name = "ingested_at"
      type = "string"
    }
    columns {
      name = "source"
      type = "string"
    }
  }

  # Order matches the S3 path: trades/snapshot_date=…/snapshot_hour=…/.
  partition_keys {
    name = "snapshot_date"
    type = "string"
  }
  partition_keys {
    name = "snapshot_hour"
    type = "string"
  }
}
