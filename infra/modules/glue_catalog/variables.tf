variable "database_name" {
  description = "Glue Data Catalog database name (lowercase + underscores so it's Athena/SQL-friendly)."
  type        = string
}

variable "table_name" {
  description = "Glue table name, e.g. \"bronze_prices\"."
  type        = string
}

variable "data_location" {
  description = "S3 URI of the table's data prefix; MUST end with a trailing slash, e.g. \"s3://bucket/prices/\"."
  type        = string

  validation {
    condition     = can(regex("^s3://.+/$", var.data_location))
    error_message = "data_location must be an s3:// URI ending with a trailing slash."
  }
}

variable "columns" {
  description = "Ordered non-partition columns as {name, type} pairs in Athena/Glue types (string/double/bigint…)."
  type = list(object({
    name = string
    type = string
  }))
}

variable "projection_date_start" {
  description = "Earliest snapshot_date for partition projection (inclusive), e.g. \"2026-06-01\". Upper bound is NOW."
  type        = string
}
