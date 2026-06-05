variable "name" {
  description = "Secret name, e.g. \"marketpulse-dev-secret-marketdata-apikey\"."
  type        = string
}

variable "description" {
  description = "Human description of the secret."
  type        = string
  default     = ""
}

variable "kms_key_arn" {
  description = "ARN of the CMK used to encrypt the secret value."
  type        = string
}
