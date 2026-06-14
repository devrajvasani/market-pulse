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
  sqs_managed_sse_enabled   = true    # SSE (SQS-managed key)
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
        # Decrypt the SSE-KMS Kinesis records + generate a data key for the SSE-KMS S3 write.
        Sid      = "UseLakeCmk"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
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
  count                              = var.enable_streaming ? 1 : 0
  event_source_arn                   = aws_kinesis_stream.trades[0].arn
  function_name                      = module.lambda_trades_consumer[0].function_arn
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
