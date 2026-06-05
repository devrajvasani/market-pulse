data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Trust policy: ONLY the Lambda service may assume this role.
resource "aws_iam_role" "this" {
  name = var.name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Least-privilege permissions: read/write Bronze, use the CMK (Decrypt covers the secret read +
# SSE-KMS read-back), read the one secret, write its own logs.
# No wildcard actions, no "Resource": "*" — every statement is scoped to a specific ARN.
resource "aws_iam_role_policy" "this" {
  name = "${var.name}-policy"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # awswrangler writes Parquet, and reads back for overwrite_partitions / existence checks.
        Sid      = "ReadWriteBronzeObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
        Resource = "${var.bronze_bucket_arn}/*"
      },
      {
        # ListBucket enumerates partitions; GetBucketLocation is needed by awswrangler.
        Sid      = "ListBronzeBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = var.bronze_bucket_arn
      },
      {
        # Decrypt is required to read the KMS-encrypted secret and to read back SSE-KMS objects.
        Sid      = "UseCmkForLakeIO"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Encrypt", "kms:Decrypt", "kms:DescribeKey"]
        Resource = var.kms_key_arn
      },
      {
        Sid      = "ReadApiKeySecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.secret_arn
      },
      {
        Sid      = "WriteOwnLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.function_name}:*"
      }
    ]
  })
}
