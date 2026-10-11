"""Idempotent schema for the optional Tiger Data store: hypertables, continuous aggregates and policies.

Run with `python -m app.tiger.migrate` (npm run setup does this when TIGER_DATABASE_URL is set). Every statement is safe
to run repeatedly. Continuous aggregates cannot be created inside a transaction, so the connection is autocommit.
"""
from typing import Any, Callable

RAW_RETENTION = "30 days"
COMPRESS_AFTER = "1 day"

# (label, sql). `optional` statements are allowed to fail (for example the extension on a role that cannot create it).
STATEMENTS: list[tuple[str, str, bool]] = [
    ("timescaledb extension", "CREATE EXTENSION IF NOT EXISTS timescaledb", True),

    ("table metrics", """CREATE TABLE IF NOT EXISTS metrics (
        time timestamptz NOT NULL, device_id text NOT NULL, metric text NOT NULL, value double precision NOT NULL)""", False),
    ("table process_samples", """CREATE TABLE IF NOT EXISTS process_samples (
        time timestamptz NOT NULL, device_id text NOT NULL, name text NOT NULL, pid integer NOT NULL,
        cpu double precision, ram_mb double precision)""", False),
    ("table action_events", """CREATE TABLE IF NOT EXISTS action_events (
        time timestamptz NOT NULL, device_id text NOT NULL,
        source text NOT NULL CHECK (source IN ('widget', 'imessage', 'generated')),
        action_id text NOT NULL, args jsonb NOT NULL DEFAULT '{}'::jsonb, outcome text NOT NULL, request_id text)""", False),

    ("hypertable metrics", "SELECT create_hypertable('metrics', 'time', if_not_exists => TRUE)", False),
    ("hypertable process_samples", "SELECT create_hypertable('process_samples', 'time', if_not_exists => TRUE)", False),
    ("hypertable action_events", "SELECT create_hypertable('action_events', 'time', if_not_exists => TRUE)", False),

    ("index metrics lookup", "CREATE INDEX IF NOT EXISTS metrics_device_metric_time ON metrics (device_id, metric, time DESC)", False),
    ("index process_samples lookup", "CREATE INDEX IF NOT EXISTS process_samples_device_time ON process_samples (device_id, time DESC)", False),
    ("index action_events lookup", "CREATE INDEX IF NOT EXISTS action_events_device_time ON action_events (device_id, time DESC)", False),

    ("continuous aggregate metrics_1m", """CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_1m WITH (timescaledb.continuous) AS
        SELECT time_bucket('1 minute', time) AS bucket, device_id, metric,
               avg(value) AS avg_value, max(value) AS max_value, min(value) AS min_value
        FROM metrics GROUP BY bucket, device_id, metric WITH NO DATA""", False),
    ("continuous aggregate metrics_1h", """CREATE MATERIALIZED VIEW IF NOT EXISTS metrics_1h WITH (timescaledb.continuous) AS
        SELECT time_bucket('1 hour', time) AS bucket, device_id, metric,
               avg(value) AS avg_value, max(value) AS max_value, min(value) AS min_value
        FROM metrics GROUP BY bucket, device_id, metric WITH NO DATA""", False),
    ("refresh policy metrics_1m", """SELECT add_continuous_aggregate_policy('metrics_1m',
        start_offset => INTERVAL '2 hours', end_offset => INTERVAL '1 minute', schedule_interval => INTERVAL '1 minute', if_not_exists => TRUE)""", False),
    ("refresh policy metrics_1h", """SELECT add_continuous_aggregate_policy('metrics_1h',
        start_offset => INTERVAL '3 days', end_offset => INTERVAL '1 hour', schedule_interval => INTERVAL '1 hour', if_not_exists => TRUE)""", False),

    ("compression settings metrics", """ALTER TABLE metrics SET (timescaledb.compress,
        timescaledb.compress_segmentby = 'device_id, metric', timescaledb.compress_orderby = 'time DESC')""", False),
    ("compression settings process_samples", """ALTER TABLE process_samples SET (timescaledb.compress,
        timescaledb.compress_segmentby = 'device_id', timescaledb.compress_orderby = 'time DESC')""", False),
    ("compression policy metrics", f"SELECT add_compression_policy('metrics', INTERVAL '{COMPRESS_AFTER}', if_not_exists => TRUE)", False),
    ("compression policy process_samples", f"SELECT add_compression_policy('process_samples', INTERVAL '{COMPRESS_AFTER}', if_not_exists => TRUE)", False),

    ("retention metrics", f"SELECT add_retention_policy('metrics', INTERVAL '{RAW_RETENTION}', if_not_exists => TRUE)", False),
    ("retention process_samples", f"SELECT add_retention_policy('process_samples', INTERVAL '{RAW_RETENTION}', if_not_exists => TRUE)", False),
    ("retention action_events", f"SELECT add_retention_policy('action_events', INTERVAL '{RAW_RETENTION}', if_not_exists => TRUE)", False),
]

# What `check` verifies: hypertables and continuous aggregates that must exist.
REQUIRED_HYPERTABLES = ("metrics", "process_samples", "action_events")
REQUIRED_AGGREGATES = ("metrics_1m", "metrics_1h")


def migrate(connection: Any, report: Callable[[str], None] = lambda line: None) -> list[str]:
    """Runs every statement on an autocommit connection. Returns the labels of optional steps that were skipped."""
    skipped = []
    for label, sql, optional in STATEMENTS:
        try:
            connection.execute(sql)
            report(f"ok    {label}")
        except Exception as error:
            if not optional:
                raise RuntimeError(f"Tiger migration failed at '{label}': {error}") from error
            skipped.append(label)
            report(f"skip  {label} ({type(error).__name__})")
    return skipped
