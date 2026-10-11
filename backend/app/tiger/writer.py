"""Batched, bounded, never-blocking writer for Tiger Data.

Producers call `metric()`, `process_samples()` and `action_event()` from the event loop; each only does a
`put_nowait`, so the agent/stats path is never slowed. A background task drains the queue every 5 s or 500 rows and
writes the batch on a worker thread (psycopg's blocking driver, which also works on Windows' default event loop).
If Tiger is unreachable the batch is dropped, never retried or queued without bound, and the problem is logged at most
once a minute.
"""
import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from .connection import connect as default_connect
from .connection import tiger_url

logger = logging.getLogger("uvicorn.error")

FLUSH_SECONDS = 5.0
MAX_BATCH = 500
QUEUE_SIZE = 5000
LOG_EVERY_SECONDS = 60.0
FLUSH_DEADLINE_SECONDS = 25.0
CLOSE_DEADLINE_SECONDS = 5.0

INSERTS = {
    "metrics": "INSERT INTO metrics (time, device_id, metric, value) VALUES (%s, %s, %s, %s)",
    "process_samples": "INSERT INTO process_samples (time, device_id, name, pid, cpu, ram_mb) VALUES (%s, %s, %s, %s, %s, %s)",
    "action_events": "INSERT INTO action_events (time, device_id, source, action_id, args, outcome, request_id) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)",
}
ACTION_SOURCES = ("widget", "imessage", "generated")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NullWriter:
    """Used when TIGER_DATABASE_URL is unset: every call is a no-op."""

    enabled = False

    def metric(self, *args: Any, **kwargs: Any) -> None: ...
    def process_samples(self, *args: Any, **kwargs: Any) -> None: ...
    def action_event(self, *args: Any, **kwargs: Any) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def stats(self) -> dict:
        return {"enabled": False}


class TigerWriter:
    enabled = True

    def __init__(self, url: str, *, flush_seconds: float = FLUSH_SECONDS, max_batch: int = MAX_BATCH, queue_size: int = QUEUE_SIZE,
                 connect: Callable[[str], Any] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.url = url
        self.flush_seconds, self.max_batch = flush_seconds, max_batch
        self.queue: asyncio.Queue[tuple[str, tuple]] = asyncio.Queue(maxsize=queue_size)
        self._connect = connect or default_connect
        self._clock = clock
        self._conn: Any = None
        self._task: asyncio.Task | None = None
        # One thread owns the connection, so psycopg never sees concurrent use.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tiger-writer")
        self.written = self.dropped = self.failures = 0
        self._last_log: float | None = None
        self._retry_at = 0.0
        self._backoff = 5.0
        self._stopped = False

    # -- producers (event loop, non-blocking) ------------------------------------------------------------------

    def _enqueue(self, table: str, row: tuple) -> None:
        if self._stopped:
            return
        try:
            self.queue.put_nowait((table, row))
        except asyncio.QueueFull:
            self.dropped += 1

    def metric(self, device_id: str, metric: str, value: float, at: datetime | None = None) -> None:
        self._enqueue("metrics", (at or _now(), device_id, metric, float(value)))

    def process_samples(self, device_id: str, rows: list[dict], at: datetime | None = None) -> None:
        moment = at or _now()
        for row in rows:
            self._enqueue("process_samples", (moment, device_id, str(row["name"])[:256], int(row["pid"]), _number(row.get("cpu")), _number(row.get("ram_mb"))))

    def action_event(self, device_id: str, source: str, action_id: str, args: dict | None, outcome: str, request_id: str | None = None,
                     at: datetime | None = None) -> None:
        if source not in ACTION_SOURCES:
            raise ValueError("source must be widget, imessage or generated")
        self._enqueue("action_events", (at or _now(), device_id, source, str(action_id)[:128],
                                        json.dumps(args or {}, separators=(",", ":"), default=str), str(outcome)[:256], request_id and str(request_id)[:256]))

    # -- lifecycle ---------------------------------------------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="tiger-writer")

    async def stop(self) -> None:
        if self._stopped:  # safe to call more than once
            return
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        pending = []
        while not self.queue.empty():
            pending.append(self.queue.get_nowait())
        if pending:
            await self._flush(pending)
        if self._conn is not None:  # nothing to close (and no worker thread to wait for) if a connection was never opened
            try:
                await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._pool, self._close), CLOSE_DEADLINE_SECONDS)
            except (TimeoutError, RuntimeError):
                pass  # shutting down: a wedged worker must not hold up the backend's exit
        self._pool.shutdown(wait=False)

    def stats(self) -> dict:
        return {"enabled": True, "queued": self.queue.qsize(), "written": self.written, "dropped": self.dropped, "failures": self.failures}

    # -- flushing ----------------------------------------------------------------------------------------------

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            batch: list[tuple[str, tuple]] = []
            deadline = loop.time() + self.flush_seconds
            while len(batch) < self.max_batch:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self.queue.get(), remaining))
                except TimeoutError:
                    break
            if batch:
                await self._flush(batch)

    async def _flush(self, batch: list[tuple[str, tuple]]) -> None:
        now = self._clock()
        if now < self._retry_at:  # recently unreachable: drop instead of paying the connect timeout again
            self.dropped += len(batch)
            return
        try:
            await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(self._pool, self._write, batch), FLUSH_DEADLINE_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # unreachable, auth, timeout, bad row...: all mean "drop this batch"
            self.failures += 1
            self.dropped += len(batch)
            self._retry_at = now + self._backoff
            self._backoff = min(60.0, self._backoff * 2)
            if self._last_log is None or now - self._last_log >= LOG_EVERY_SECONDS:
                self._last_log = now
                logger.warning("Tiger Data write failed (%s); dropping samples (%d dropped so far)", type(error).__name__, self.dropped)
            return
        self._backoff = 5.0
        self.written += len(batch)

    def _write(self, batch: list[tuple[str, tuple]]) -> None:
        """Runs on the writer thread."""
        try:
            if self._conn is None:
                self._conn = self._connect(self.url)
            grouped: dict[str, list[tuple]] = {}
            for table, row in batch:
                grouped.setdefault(table, []).append(row)
            with self._conn.cursor() as cursor:
                for table, rows in grouped.items():
                    cursor.executemany(INSERTS[table], rows)
            self._conn.commit()
        except Exception:
            self._close()
            raise

    def _close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def get_writer() -> TigerWriter | NullWriter:
    url = tiger_url()
    return TigerWriter(url) if url else NullWriter()
