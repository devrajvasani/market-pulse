# Scheduled rule (classic EventBridge rule — LocalStack-friendly).
resource "aws_cloudwatch_event_rule" "this" {
  name                = var.name
  description         = "Scheduled trigger for ${var.target_lambda_name}"
  schedule_expression = var.schedule_expression
}

# Point the rule at the Lambda. Explicit target_id keeps the target stable/idempotent across plans.
resource "aws_cloudwatch_event_target" "this" {
  rule      = aws_cloudwatch_event_rule.this.name
  target_id = "${var.name}-target"
  arn       = var.target_lambda_arn
}

# Allow EventBridge to invoke the Lambda (scoped to this rule).
resource "aws_lambda_permission" "this" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = var.target_lambda_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.this.arn
}
