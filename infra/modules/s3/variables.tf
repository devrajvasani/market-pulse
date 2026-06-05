variable "bucket_name" {
  description = "Globally-unique bucket name, e.g. \"marketpulse-dev-bucket-bronze-1a2b3c4d\"."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the KMS CMK used for SSE-KMS encryption of objects."
  type        = string
}
