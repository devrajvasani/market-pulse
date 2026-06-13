{#
  Parse an ISO-8601 string column into a real timestamp using each engine's
  native, standards-compliant parser. This is the STANDARD dbt cross-engine
  pattern (adapter.dispatch) — not a per-target {% if %} branch and not a
  lossy/temporary cast. The dispatch namespace ('marketpulse') matches the
  project name in dbt_project.yml.

  Both Bronze timestamp strings are UTC but in different ISO forms:
    - ingested_at        ends with "+00:00" (Python datetime.isoformat)
    - source_updated_at  ends with "Z"      (CoinGecko last_updated)
  Each implementation parses both forms natively, then NORMALISES to a tz-naive
  UTC `timestamp` so the persisted column type is identical AND storable on both
  engines -- Athena CTAS cannot persist `timestamp with time zone`. Inputs are
  already UTC, so dropping the zone is lossless:
    - DuckDB : cast -> timestamptz (ISO/Z aware), then AT TIME ZONE 'UTC' -> timestamp.
    - Athena : from_iso8601_timestamp(...) (tz-aware), then cast as timestamp (Athena
               sessions run in UTC, so the wall-clock is UTC).
#}

{% macro parse_iso_timestamp(col) -%}
    {{ return(adapter.dispatch('parse_iso_timestamp', 'marketpulse')(col)) }}
{%- endmacro %}


{% macro default__parse_iso_timestamp(col) -%}
    cast({{ col }} as timestamp)
{%- endmacro %}


{% macro duckdb__parse_iso_timestamp(col) -%}
    cast({{ col }} as timestamptz) at time zone 'UTC'
{%- endmacro %}


{% macro athena__parse_iso_timestamp(col) -%}
    cast(from_iso8601_timestamp({{ col }}) as timestamp)
{%- endmacro %}
