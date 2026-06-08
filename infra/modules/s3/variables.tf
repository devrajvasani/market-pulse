variable "bucket_name" {
  description = "Globally-unique bucket name, e.g. \"marketpulse-dev-bucket-bronze-1a2b3c4d\"."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the KMS CMK used for SSE-KMS encryption of objects."
  type        = string
}

variable "force_destroy" {
  description = "If true, `terraform destroy` empties the bucket (incl. versions) and deletes it. Dev convenience for clean teardown; keep false in prod."
  type        = bool
  default     = false
}

variable "noncurrent_version_expiration_days" {
  description = "Delete superseded (noncurrent) object versions this many days after they're replaced. Lake data is re-ingestible, so version history is for short-term recovery only."
  type        = number
  default     = 7

  validation {
    condition     = var.noncurrent_version_expiration_days >= 1
    error_message = "noncurrent_version_expiration_days must be >= 1 (AWS requires a positive day count)."
  }
}

variable "abort_incomplete_multipart_upload_days" {
  description = "Abort and purge incomplete multipart uploads this many days after they start, so orphaned parts don't accrue storage cost."
  type        = number
  default     = 7

  validation {
    condition     = var.abort_incomplete_multipart_upload_days >= 1
    error_message = "abort_incomplete_multipart_upload_days must be >= 1 (AWS requires a positive day count)."
  }
}
