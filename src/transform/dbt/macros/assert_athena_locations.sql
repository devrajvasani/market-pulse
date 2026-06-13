{#
  Fail-fast guard for the `athena` target (an on-run-start hook). Iceberg table DATA
  must land in the durable silver/gold buckets (DBT_SILVER_DATA / DBT_GOLD_DATA) and
  query RESULTS in the athena-results bucket (DBT_ATHENA_STAGING). If any is unset,
  dbt-athena would error -- or worse, silently write Iceberg files into the transient,
  lifecycle-expiring results bucket. So abort with a clear message BEFORE any model
  runs. No-op on every non-athena target (e.g. the local duckdb twin), so the same
  project still builds locally with these env vars unset.
#}
{% macro assert_athena_locations() %}
    {% if execute and target.name == 'athena' %}
        {% if env_var('DBT_ATHENA_STAGING', '') == ''
              or env_var('DBT_SILVER_DATA', '') == ''
              or env_var('DBT_GOLD_DATA', '') == '' %}
            {% do exceptions.raise_compiler_error(
                "athena target requires DBT_ATHENA_STAGING, DBT_SILVER_DATA and DBT_GOLD_DATA "
                ~ "(S3 URIs from `terraform output`). Iceberg data must land in the durable "
                ~ "silver/gold buckets, never the transient athena-results bucket. "
                ~ "Set all three and re-run."
            ) %}
        {% endif %}
    {% endif %}
{% endmacro %}
