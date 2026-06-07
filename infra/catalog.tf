# ── Stage 2: catalog + first SQL — Athena-results bucket, Glue catalog, Athena workgroup ──
#
# Makes the lakehouse "see" Bronze: a Glue table (metadata only) over the Parquet already in
# S3, queried by Athena. The table uses partition projection (no crawler, $0). Every query is
# capped at 100 MB scanned. Glue/Athena are AWS-only (LocalStack Pro) — the free local twin is
# DuckDB (src/backends/duckdb_backend.py), reading the same Parquet.

locals {
  # Glue/Athena identifiers use underscores (hyphens need quoting in SQL): "marketpulse_dev".
  glue_database = replace(local.name_prefix, "-", "_")

  # bronze_prices schema — MUST match the Parquet types pinned in
  # src/ingestion/batch/ingest.py (_BRONZE_DTYPES). 24 data cols; 2 partition keys are
  # declared in the glue_catalog module (snapshot_date, snapshot_hour).
  bronze_prices_columns = [
    { name = "coin_id", type = "string" },
    { name = "coin_symbol", type = "string" },
    { name = "coin_name", type = "string" },
    { name = "quote_currency", type = "string" },
    { name = "current_price", type = "double" },
    { name = "market_cap", type = "double" },
    { name = "market_cap_rank", type = "bigint" },
    { name = "fully_diluted_valuation", type = "double" },
    { name = "total_volume", type = "double" },
    { name = "high_24h", type = "double" },
    { name = "low_24h", type = "double" },
    { name = "price_change_24h", type = "double" },
    { name = "price_change_pct_24h", type = "double" },
    { name = "price_change_pct_1h", type = "double" },
    { name = "price_change_pct_7d", type = "double" },
    { name = "market_cap_change_24h", type = "double" },
    { name = "market_cap_change_pct_24h", type = "double" },
    { name = "circulating_supply", type = "double" },
    { name = "total_supply", type = "double" },
    { name = "max_supply", type = "double" },
    { name = "ath", type = "double" },
    { name = "ath_change_pct", type = "double" },
    { name = "source_updated_at", type = "string" },
    { name = "ingested_at", type = "string" },
  ]
}

# Dedicated bucket for Athena query results — kept separate from lake data (encrypted, blocked).
module "athena_results" {
  source        = "./modules/s3"
  count         = var.enable_catalog ? 1 : 0
  bucket_name   = "${local.name_prefix}-bucket-athena-results-${random_id.bucket_suffix.hex}"
  kms_key_arn   = module.kms.key_arn
  force_destroy = true # dev: clean teardown (results are ephemeral + re-creatable)
}

# Glue database + the manually-defined Bronze prices table (partition projection, no crawler).
module "glue_catalog" {
  source                = "./modules/glue_catalog"
  count                 = var.enable_catalog ? 1 : 0
  database_name         = local.glue_database
  table_name            = "bronze_prices"
  data_location         = "s3://${module.s3["bronze"].bucket_id}/prices/"
  columns               = local.bronze_prices_columns
  projection_date_start = "2026-06-01"
}

# Athena workgroup with a 100 MB per-query scan cap (the cost guardrail).
module "athena" {
  source               = "./modules/athena"
  count                = var.enable_catalog ? 1 : 0
  workgroup_name       = "${local.name_prefix}-athena-analytics"
  results_location     = "s3://${module.athena_results[0].bucket_id}/query-results/"
  kms_key_arn          = module.kms.key_arn
  bytes_scanned_cutoff = 100 * 1024 * 1024 # 100 MB
}

# `enable_catalog` gates Stage 2: AWS = true (build Glue/Athena), LocalStack = false (Glue/Athena
# are LocalStack Pro; the local query twin is DuckDB). The `moved` blocks migrate the pre-gate
# (un-indexed) state to the counted address, so adding the gate is a no-op on AWS — no recreate.
moved {
  from = module.athena_results
  to   = module.athena_results[0]
}

moved {
  from = module.glue_catalog
  to   = module.glue_catalog[0]
}

moved {
  from = module.athena
  to   = module.athena[0]
}
