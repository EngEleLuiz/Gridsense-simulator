{#
    Converts the model's table into a TimescaleDB hypertable, partitioned
    on `time_column`, for efficient time-range queries and (eventually)
    native compression/retention policies.

    Safe to run against plain PostgreSQL (e.g. in local dev/CI without
    the TimescaleDB extension installed): the DO block checks for the
    extension first and silently no-ops if it isn't present, so model
    builds never fail for lack of Timescale.
#}
{% macro make_hypertable(time_column) %}
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable(
            '{{ this }}', '{{ time_column }}',
            if_not_exists => TRUE,
            migrate_data => TRUE
        );
    END IF;
END $$;
{% endmacro %}
