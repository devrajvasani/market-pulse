# Pre-create the log group with retention (the IAM policy grants logs to exactly this group;
# otherwise Lambda would auto-create it with infinite retention).
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "this" {
  function_name    = var.function_name
  role             = var.role_arn
  handler          = var.handler
  runtime          = var.runtime
  filename         = var.source_zip
  source_code_hash = var.source_hash
  timeout          = var.timeout_seconds
  memory_size      = var.memory_mb
  layers           = var.layers

  environment {
    variables = var.environment
  }

  depends_on = [aws_cloudwatch_log_group.this]
}
