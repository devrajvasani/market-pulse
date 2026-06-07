# Athena workgroup — isolates query configuration and enforces a per-query bytes-scanned
# CAP (the cost guardrail: a runaway scan is killed before it bills). Results go to a
# dedicated, SSE-KMS-encrypted bucket. enforce_workgroup_configuration = clients can't
# override the cap or the results location.

resource "aws_athena_workgroup" "this" {
  name          = var.workgroup_name
  description   = "MarketPulse analytics workgroup (per-query bytes-scanned cap)."
  state         = "ENABLED"
  force_destroy = true # dev: allow `terraform destroy` even if it holds saved queries

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff

    result_configuration {
      output_location = var.results_location

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = var.kms_key_arn
      }
    }
  }
}
