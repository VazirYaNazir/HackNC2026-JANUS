"""Read side of the Tiger Data store: metric history, recent activity, and the "why was it slow?" query.

Each query opens a short-lived connection on a worker thread (they are rare and human-triggered), with a hard deadline,
so a slow or unreachable database can never hold up the event loop."""
import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Any, Callable

from .connection import DB_ERRORS
from .connection import connect as default_connect
from .schema import REQUIRED_AGGREGATES, REQUIRED_HYPERTABLES

logger = logging.getLogger("uvicorn.error")

QUERY_DEADLINE_SECONDS = 8.0
LOG_EVERY_SECONDS = 60.0
RANGES = {"15m": 15, "30m": 30, "1h": 60, "3h": 180, "6h": 360, "12h": 720, "24h": 1440}
METRIC_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")


class TigerUnavailable(RuntimeError):
    """Not configured, unreachable, or the schema is missing."""


def parse_range(value: str) -> int:
    """Minutes for a range such as '1h' or '30m'; only the whitelisted ranges are accepted."""
    if value not in RANGES:
        raise ValueError(f"range must be one of {', '.join(RANGES)}")
    return RANGES[value]


class TigerStore:
    def __init__(self, url: str, *, connect: Callable[..., Any] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.url = url
        self._connect = connect or default_connect
        self._clock = clock
        self._last_log: float | None = None

    # -- plumbing ----------------------------------------------------------------------------------------------

    def _run(self, sql: str, params: tuple) -> list[dict]:
        from psycopg.rows import dict_row  # imported here so the module loads without psycopg

        with self._connect(self.url) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())

    async def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._run, sql, params), QUERY_DEADLINE_SECONDS)
        except (*DB_ERRORS, OSError, TimeoutError, RuntimeError) as error:
            now = self._clock()
            if self._last_log is None or now - self._last_log >= LOG_EVERY_SECONDS:
                self._last_log = now
                logger.warning("Tiger Data query failed (%s)", type(error).__name__)
            raise TigerUnavailable(type(error).__name__) from None

    # -- queries -----------------------------------------------------------------------------------------------

    async def metric_history(self, device_id: str, metric: str, minutes: int) -> list[dict]:
        """One point per minute from the metrics_1m continuous aggregate, oldest first."""
        if not METRIC_NAME.fullmatch(metric):
            raise ValueError("invalid metric name")
        rows = await self._query(
            """SELECT bucket, avg_value, max_value, min_value FROM metrics_1m
               WHERE device_id = %s AND metric = %s AND bucket >= now() - make_interval(mins => %s)
               ORDER BY bucket""", (device_id, metric, minutes))
        return [{"time": row["bucket"].isoformat(), "avg": row["avg_value"], "max": row["max_value"], "min": row["min_value"]} for row in rows]

    async def recent_activity(self, device_id: str, limit: int = 50) -> list[dict]:
        rows = await self._query(
            """SELECT time, source, action_id, args, outcome, request_id FROM action_events
               WHERE device_id = %s ORDER BY time DESC LIMIT %s""", (device_id, max(1, min(limit, 200))))
        return [{"time": row["time"].isoformat(), "source": row["source"], "action_id": row["action_id"], "args": row["args"],
                 "outcome": row["outcome"], "request_id": row["request_id"]} for row in rows]

    async def worst_minute(self, device_id: str, minutes: int = 60) -> dict | None:
        """The minute with the highest CPU peak in the window, from metrics_1m."""
        rows = await self._query(
            """SELECT bucket, max_value FROM metrics_1m
               WHERE device_id = %s AND metric = 'cpu.percent' AND bucket >= now() - make_interval(mins => %s)
               ORDER BY max_value DESC, bucket DESC LIMIT 1""", (device_id, minutes))
        return {"bucket": rows[0]["bucket"], "cpu": rows[0]["max_value"]} if rows else None

    async def top_apps(self, device_id: str, start: datetime, limit: int = 3) -> list[str]:
        """The busiest processes during the minute that starts at `start`, from process_samples."""
        rows = await self._query(
            """SELECT name, max(cpu) AS peak FROM process_samples
               WHERE device_id = %s AND time >= %s AND time < %s + interval '1 minute' AND cpu IS NOT NULL
               GROUP BY name ORDER BY peak DESC, name LIMIT %s""", (device_id, start, start, max(1, min(limit, 10))))
        return [row["name"] for row in rows]

    async def check(self) -> dict:
        """Reachability and whether the schema exists, for `npm run doctor`."""
        try:
            tables = await self._query(
                "SELECT hypertable_name AS name FROM timescaledb_information.hypertables")
            aggregates = await self._query(
                "SELECT view_name AS name FROM timescaledb_information.continuous_aggregates")
        except TigerUnavailable as error:
            return {"reachable": False, "error": str(error), "missing": []}
        missing = [name for name in REQUIRED_HYPERTABLES if name not in {r["name"] for r in tables}]
        missing += [name for name in REQUIRED_AGGREGATES if name not in {r["name"] for r in aggregates}]
        return {"reachable": True, "schema": not missing, "missing": missing}
