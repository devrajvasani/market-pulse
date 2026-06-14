"""Glue PySpark job: Bronze -> Silver, the parallel *learning* path (Stage 3d).

This reproduces the dbt ``silver_prices`` transform in Spark DataFrames purely to
learn Spark hands-on -- the SQL/dbt path stays the primary, cheaper one. It reads
the same Bronze Parquet, type-casts the raw ISO-string timestamps to UTC, keeps one
row per natural key (``coin_id`` + ``snapshot_date`` + ``snapshot_hour``, latest
capture wins), and writes a *separate* Iceberg table ``silver_prices_spark`` -- kept
distinct from the dbt-managed ``silver_prices`` so the two paths can be compared,
not clobbered.

Designed for AWS Glue (launch with the job parameter ``--datalake-formats=iceberg``
so the Iceberg runtime is on the classpath). The ``transform()`` core is a pure
function, isolated so a future local-Spark unit test can exercise it without Glue.
"""

from __future__ import annotations

import logging
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

logger = logging.getLogger("bronze_to_silver")

# The 24 Bronze data columns carried into Silver, in order (the two timestamp columns
# are cast below; the two partition columns are derived separately).
_DATA_COLUMNS = [
    "coin_id",
    "coin_symbol",
    "coin_name",
    "quote_currency",
    "current_price",
    "market_cap",
    "market_cap_rank",
    "fully_diluted_valuation",
    "total_volume",
    "high_24h",
    "low_24h",
    "price_change_24h",
    "price_change_pct_24h",
    "price_change_pct_1h",
    "price_change_pct_7d",
    "market_cap_change_24h",
    "market_cap_change_pct_24h",
    "circulating_supply",
    "total_supply",
    "max_supply",
    "ath",
    "ath_change_pct",
]

_NATURAL_KEY = ["coin_id", "snapshot_date", "snapshot_hour"]


def transform(bronze: DataFrame) -> DataFrame:
    """Bronze -> Silver transform (pure: DataFrame in, DataFrame out).

    Mirrors dbt ``silver_prices``: cast the raw ISO-string timestamps to real
    timestamps and keep one row per natural key (latest ``ingested_at``). Assumes the
    Spark session timezone is UTC so the timestamps are stored as UTC instants.

    Args:
        bronze: raw Bronze rows, including the ``snapshot_date`` / ``snapshot_hour``
            partition columns discovered from the Parquet directory layout.

    Returns:
        The deduplicated, typed Silver DataFrame.
    """
    typed = bronze.select(
        *_DATA_COLUMNS,
        F.to_timestamp("source_updated_at").alias("source_updated_at"),
        F.to_timestamp("ingested_at").alias("ingested_at"),
        F.to_date(F.col("snapshot_date")).alias("snapshot_date"),
        F.col("snapshot_hour").cast("int").alias("snapshot_hour"),
    )
    # Latest capture per natural key (row_number == 1) -- the same rule as the SQL dedup,
    # incl. the source_updated_at tiebreaker for equal ingested_at (kept in lockstep).
    latest = Window.partitionBy(*_NATURAL_KEY).orderBy(
        F.col("ingested_at").desc(), F.col("source_updated_at").desc()
    )
    return (
        typed.withColumn("_row_num", F.row_number().over(latest))
        .where(F.col("_row_num") == 1)
        .drop("_row_num")
    )


def build_spark(glue: GlueContext, warehouse: str) -> SparkSession:
    """Configure the Spark session: UTC, plus an Iceberg catalog on the Glue catalog.

    Args:
        glue: the Glue context wrapping the Spark session.
        warehouse: S3 URI Iceberg writes table data under (the silver bucket).

    Returns:
        The configured Spark session.
    """
    spark = glue.spark_session
    # UTC everywhere so ISO timestamps land as UTC instants (matches the SQL path).
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    # Iceberg tables registered in the SAME Glue Data Catalog dbt writes to.
    spark.conf.set(
        "spark.sql.extensions",
        "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
    )
    spark.conf.set("spark.sql.catalog.glue_catalog", "org.apache.iceberg.spark.SparkCatalog")
    spark.conf.set(
        "spark.sql.catalog.glue_catalog.catalog-impl",
        "org.apache.iceberg.aws.glue.GlueCatalog",
    )
    spark.conf.set("spark.sql.catalog.glue_catalog.warehouse", warehouse)
    spark.conf.set("spark.sql.catalog.glue_catalog.io-impl", "org.apache.iceberg.aws.s3.S3FileIO")
    return spark


def main() -> None:
    """Glue entry point: read Bronze, transform, write the Silver Iceberg table."""
    args = getResolvedOptions(
        sys.argv,
        ["JOB_NAME", "bronze_path", "silver_database", "silver_table", "warehouse"],
    )

    glue = GlueContext(SparkContext.getOrCreate())
    spark = build_spark(glue, args["warehouse"])
    job = Job(glue)
    job.init(args["JOB_NAME"], args)

    bronze = spark.read.parquet(args["bronze_path"]).cache()
    silver = transform(bronze).cache()
    # Materialise into cache once so the row counts (logged below) don't trigger extra
    # full scans and the write reuses the cached frame rather than recomputing it.
    rows_in, rows_out = bronze.count(), silver.count()

    target = f"glue_catalog.{args['silver_database']}.{args['silver_table']}"
    # createOrReplace = full idempotent rebuild (the learning job's equivalent of the
    # dbt `table` materialization); partitioned by date like the SQL Iceberg tables.
    silver.writeTo(target).using("iceberg").partitionedBy("snapshot_date").createOrReplace()
    logger.info("Bronze rows in: %d, Silver rows out: %d -> %s", rows_in, rows_out, target)

    job.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
