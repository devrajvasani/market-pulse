# Glue Data Catalog: a database + one EXTERNAL table.
#
# The table is METADATA ONLY (schema-on-read) — it points at Parquet already in S3 and
# stores zero rows. Partition projection computes snapshot_date / snapshot_hour from the
# object path, so there is NO crawler and NO per-partition bookkeeping (cheaper + zero
# maintenance). Glue/Athena are AWS-only (LocalStack Pro); the local twin is DuckDB.

resource "aws_glue_catalog_database" "this" {
  name = var.database_name
}

resource "aws_glue_catalog_table" "this" {
  name          = var.table_name
  database_name = aws_glue_catalog_database.this.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    classification                           = "parquet"
    EXTERNAL                                 = "TRUE"
    "projection.enabled"                     = "true"
    "projection.snapshot_date.type"          = "date"
    "projection.snapshot_date.format"        = "yyyy-MM-dd"
    "projection.snapshot_date.range"         = "${var.projection_date_start},NOW"
    "projection.snapshot_date.interval"      = "1"
    "projection.snapshot_date.interval.unit" = "DAYS"
    "projection.snapshot_hour.type"          = "integer"
    "projection.snapshot_hour.range"         = "0,23"
    "projection.snapshot_hour.digits"        = "2"
    # $${...} escapes Terraform interpolation -> Athena receives the literal ${snapshot_date}.
    "storage.location.template" = "${var.data_location}snapshot_date=$${snapshot_date}/snapshot_hour=$${snapshot_hour}"
  }

  storage_descriptor {
    location      = var.data_location
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = var.columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  # Order matters — must match the S3 path layout: prices/snapshot_date=…/snapshot_hour=…/.
  partition_keys {
    name = "snapshot_date"
    type = "string"
  }
  partition_keys {
    name = "snapshot_hour"
    type = "string"
  }
}
