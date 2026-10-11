"""Optional Tiger Data integration. Everything here uses fakes (no database, no compiler) except TigerIntegrationTests,
which only runs when TIGER_DATABASE_URL is set."""
import asyncio
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import panels
from app.agent_runtime.config import Identity
from app.agent_runtime.connections import PipelineError
from app.agent_runtime.events import PanelEvents
from app.agent_runtime.native import NativeManager
from app.routes import history as history_routes
from app.routes.agent_jobs import controller
from app.tiger import connection as tiger_connection
from app.tiger import lifecycle, schema
from app.tiger.migrate import main as migrate_main
from app.tiger.sink import TigerSink, top_processes
from app.tiger.store import TigerStore, TigerUnavailable, parse_range
from app.tiger.writer import INSERTS, MAX_BATCH, NullWriter, TigerWriter, get_writer

DEVICE = "laptop"


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeConnection:
    """Records executemany calls; optionally fails like an unreachable server."""

    def __init__(self, log):
        self.log = log
        self.closed = False
        self.commits = 0

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, rows):
        self.log.append((sql, list(rows)))

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class FakeServer:
    def __init__(self, *, down=False):
        self.down = down
        self.connects = 0
        self.log: list[tuple[str, list]] = []
        self.connections: list[FakeConnection] = []

    def __call__(self, url):
        self.connects += 1
        if self.down:
            raise OSError("connection refused")
        connection = FakeConnection(self.log)
        self.connections.append(connection)
        return connection

    def rows(self, table):
        return [row for sql, rows in self.log if sql == INSERTS[table] for row in rows]


async def until(condition, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        await asyncio.sleep(0.01)
    return condition()


class NoopWhenUnsetTests(unittest.IsolatedAsyncioTestCase):
    def test_writer_factory_is_a_noop_without_a_url(self):
        with patch.dict(os.environ, {}, clear=True):
            writer = get_writer()
        self.assertIsInstance(writer, NullWriter)
        self.assertFalse(writer.enabled)
        writer.metric("d", "cpu.percent", 1.0)
        writer.process_samples("d", [{"name": "x", "pid": 1}])
        writer.action_event("d", "widget", "a", {}, "ok")
        self.assertEqual(writer.stats(), {"enabled": False})

    async def test_lifecycle_installs_nothing_when_unset(self):
        app = SimpleNamespace(state=SimpleNamespace())
        runtime = SimpleNamespace(native=SimpleNamespace(sink=None))
        with patch.dict(os.environ, {}, clear=True):
            await lifecycle.start_tiger(app, runtime)
            await lifecycle.stop_tiger(app)
        self.assertIsNone(app.state.tiger_store)
        self.assertIsNone(runtime.native.sink)

    async def test_lifecycle_installs_the_sink_when_configured_and_tolerates_a_bare_runtime(self):
        with patch.dict(os.environ, {"TIGER_DATABASE_URL": "postgres://example/db"}), \
                patch.object(lifecycle, "get_writer", side_effect=lambda: TigerWriter("postgres://example/db", connect=FakeServer())):
            app = SimpleNamespace(state=SimpleNamespace())
            runtime = SimpleNamespace(native=SimpleNamespace(sink=None))
            await lifecycle.start_tiger(app, runtime)
            self.assertIsInstance(runtime.native.sink, TigerSink)
            self.assertIsInstance(app.state.tiger_store, TigerStore)
            await lifecycle.stop_tiger(app)
            bare = SimpleNamespace(state=SimpleNamespace())
            await lifecycle.start_tiger(bare, SimpleNamespace())  # a runtime without .native (as in some tests) must not break
            await lifecycle.stop_tiger(bare)

    def test_native_manager_without_a_sink_behaves_as_before(self):
        self.assertIsNone(NativeManager(SimpleNamespace(), PanelEvents()).sink)


class WriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_flushes_when_a_batch_reaches_500_rows(self):
        server = FakeServer()
        writer = TigerWriter("u", flush_seconds=60, connect=server)
        await writer.start()
        for n in range(MAX_BATCH):
            writer.metric(DEVICE, "cpu.percent", n)
        self.assertTrue(await until(lambda: writer.written == MAX_BATCH))
        self.assertEqual(len(server.rows("metrics")), MAX_BATCH)
        self.assertEqual(server.connections[0].commits, 1)
        await writer.stop()

    async def test_flushes_on_the_interval_even_for_a_small_batch(self):
        server = FakeServer()
        writer = TigerWriter("u", flush_seconds=0.05, connect=server)
        await writer.start()
        writer.metric(DEVICE, "mem.percent", 41.5)
        writer.metric(DEVICE, "cpu.percent", 7)
        self.assertTrue(await until(lambda: writer.written == 2))
        values = {row[2]: row[3] for row in server.rows("metrics")}
        self.assertEqual(values, {"mem.percent": 41.5, "cpu.percent": 7.0})
        await writer.stop()

    async def test_each_table_gets_its_own_insert_with_the_right_columns(self):
        server = FakeServer()
        writer = TigerWriter("u", flush_seconds=0.05, connect=server)
        await writer.start()
        moment = datetime(2026, 10, 11, 16, 31, tzinfo=timezone.utc)
        writer.process_samples(DEVICE, [{"name": "chrome.exe", "pid": 42, "cpu": 31.5, "ram_mb": 512.0}], at=moment)
        writer.action_event(DEVICE, "imessage", "lock", {"x": 1}, "ok", "msg-1", at=moment)
        self.assertTrue(await until(lambda: writer.written == 2))
        self.assertEqual(server.rows("process_samples"), [(moment, DEVICE, "chrome.exe", 42, 31.5, 512.0)])
        self.assertEqual(server.rows("action_events"), [(moment, DEVICE, "imessage", "lock", '{"x":1}', "ok", "msg-1")])
        await writer.stop()

    async def test_unknown_action_sources_are_rejected(self):
        writer = TigerWriter("u", connect=FakeServer())
        for source in ("device", "", "Widget"):
            with self.assertRaises(ValueError):
                writer.action_event(DEVICE, source, "a", {}, "ok")
        for source in ("widget", "imessage", "generated"):
            writer.action_event(DEVICE, source, "a", {}, "ok")

    async def test_the_queue_is_bounded_and_producers_never_block_or_raise(self):
        writer = TigerWriter("u", queue_size=10, connect=FakeServer())  # not started: nothing drains
        started = time.perf_counter()
        for n in range(10_000):
            writer.metric(DEVICE, "cpu.percent", n)
        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertEqual((writer.queue.qsize(), writer.dropped), (10, 9_990))

    async def test_an_unreachable_database_drops_samples_without_slowing_producers(self):
        server = FakeServer(down=True)
        writer = TigerWriter("u", flush_seconds=0.02, connect=server)
        await writer.start()
        started = time.perf_counter()
        for n in range(2000):
            writer.metric(DEVICE, "cpu.percent", n)
        self.assertLess(time.perf_counter() - started, 0.5)
        with self.assertLogs("uvicorn.error", level="WARNING"):
            self.assertTrue(await until(lambda: writer.failures >= 1))
        await asyncio.sleep(0.1)
        self.assertEqual(writer.written, 0)
        self.assertGreater(writer.dropped, 0)
        await writer.stop()
        await writer.stop()  # idempotent
        writer.metric(DEVICE, "cpu.percent", 1)  # and producers after stop are simply ignored
        self.assertEqual(writer.queue.qsize(), 0)

    async def test_failures_are_logged_once_a_minute_and_retries_back_off(self):
        clock = Clock()
        server = FakeServer(down=True)
        writer = TigerWriter("u", connect=server, clock=clock)
        batch = [("metrics", (datetime.now(timezone.utc), DEVICE, "cpu.percent", 1.0))]
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            await writer._flush(list(batch))                 # t=0: fails, logs
            self.assertEqual(server.connects, 1)
            clock.now += 3
            await writer._flush(list(batch))                 # inside the back-off window: dropped without even connecting
            self.assertEqual(server.connects, 1)
            clock.now += 10
            await writer._flush(list(batch))                 # back-off over: tries again, fails, but the log is rate-limited
            self.assertEqual(server.connects, 2)
            self.assertEqual(len(logs.records), 1)
            clock.now += 61
            await writer._flush(list(batch))                 # a minute later the next failure is logged again
            self.assertEqual(len(logs.records), 2)
        self.assertNotIn("postgres", "".join(logs.output))   # no connection details in the log
        self.assertEqual(writer.written, 0)

    async def test_recovers_when_the_database_comes_back_and_reconnects_after_an_error(self):
        clock = Clock()
        server = FakeServer(down=True)
        writer = TigerWriter("u", connect=server, clock=clock)
        batch = [("metrics", (datetime.now(timezone.utc), DEVICE, "cpu.percent", 1.0))]
        with self.assertLogs("uvicorn.error", level="WARNING"):
            await writer._flush(list(batch))
        server.down = False
        clock.now += 30
        await writer._flush(list(batch))
        self.assertEqual(writer.written, 1)
        server.connections[0].executemany = MagicMock(side_effect=psycopg.OperationalError("server closed the connection"))
        clock.now += 120
        with self.assertLogs("uvicorn.error", level="WARNING"):
            await writer._flush(list(batch))
        self.assertTrue(server.connections[0].closed)         # the broken connection is discarded...
        clock.now += 120
        await writer._flush(list(batch))
        self.assertEqual(len(server.connections), 2)           # ...and a fresh one is opened next time
        self.assertEqual(writer.written, 2)

    async def test_stop_returns_even_when_the_worker_thread_never_runs(self):
        # Regression: tests that patch threading.Thread leave the executor without threads. stop() must not wait on it.
        writer = TigerWriter("u", connect=FakeServer())
        writer._pool = MagicMock()
        writer._pool.submit.side_effect = AssertionError("no connection was opened, so the executor must not be used")
        await asyncio.wait_for(writer.stop(), 2)

    async def test_stop_gives_up_on_a_wedged_worker_after_a_deadline(self):
        server = FakeServer()
        writer = TigerWriter("u", flush_seconds=0.02, connect=server)
        await writer.start()
        writer.metric(DEVICE, "cpu.percent", 1)
        self.assertTrue(await until(lambda: writer.written == 1))
        self.assertIsNotNone(writer._conn)
        real_pool = writer._pool
        wedged = asyncio.get_running_loop().create_future()
        writer._pool = MagicMock()
        with patch("app.tiger.writer.CLOSE_DEADLINE_SECONDS", 0.1), patch.object(asyncio.get_running_loop(), "run_in_executor", return_value=wedged):
            started = time.perf_counter()
            await writer.stop()
            self.assertLess(time.perf_counter() - started, 1.0)
        real_pool.shutdown(wait=False)

    async def test_stop_flushes_what_is_still_queued(self):
        server = FakeServer()
        writer = TigerWriter("u", flush_seconds=60, connect=server)
        await writer.start()
        writer.metric(DEVICE, "cpu.percent", 5)
        await writer.stop()
        self.assertEqual(len(server.rows("metrics")), 1)


class SinkTests(unittest.TestCase):
    def sink(self, **kwargs):
        self.writer = MagicMock(enabled=True)
        self.clock = Clock(0)
        return TigerSink(self.writer, clock=self.clock, **kwargs)

    def test_stats_become_metrics_and_bad_values_are_skipped(self):
        sink = self.sink()
        sink.readings(DEVICE, {"cpu.percent": 12.5, "mem.percent": 40, "mem.used_mb": 8000.0, "cpu.temperature": None, "volume.percent": 30})
        names = [call.args[1] for call in self.writer.metric.call_args_list]
        self.assertEqual(names, ["cpu.percent", "mem.percent", "mem.used_mb"])
        self.writer.reset_mock()
        sink.readings(DEVICE, {"cpu.percent": True, "mem.percent": "n/a", "cpu.temperature": 61.0})
        self.assertEqual([c.args[1:] for c in self.writer.metric.call_args_list], [("cpu.temperature", 61.0)])

    def test_metrics_are_thinned_to_one_sample_per_interval_per_metric(self):
        sink = self.sink()
        for second in range(0, 12):
            self.clock.now = second
            sink.readings(DEVICE, {"cpu.percent": second})
        self.assertEqual([c.args[2] for c in self.writer.metric.call_args_list], [0, 5, 10])

    def test_process_samples_every_10_seconds_top_10_by_cpu_without_context_processes(self):
        sink = self.sink()
        tree = json.dumps([{"pid": n, "name": f"app{n}", "cpu_percent": n, "ram_bytes": n * 1048576, "context": False} for n in range(1, 16)]
                          + [{"pid": 99, "name": "parent", "cpu_percent": 100.0, "ram_bytes": 1, "context": True}])
        sink.readings(DEVICE, {"process.tree": tree})
        rows = self.writer.process_samples.call_args.args[1]
        self.assertEqual([r["name"] for r in rows], [f"app{n}" for n in range(15, 5, -1)])
        self.assertEqual(rows[0], {"name": "app15", "pid": 15, "cpu": 15, "ram_mb": 15.0})
        self.writer.reset_mock()
        self.clock.now = 9
        sink.readings(DEVICE, {"process.tree": tree})
        self.writer.process_samples.assert_not_called()
        self.clock.now = 10
        sink.readings(DEVICE, {"process.tree": tree})
        self.writer.process_samples.assert_called_once()

    def test_malformed_process_trees_are_ignored(self):
        self.assertEqual(top_processes("not json"), [])
        self.assertEqual(top_processes("[1, {}, {\"cpu_percent\": \"x\"}]"), [])
        self.assertEqual(top_processes("[{\"pid\": 1, \"name\": \"a\", \"cpu_percent\": 3}]"), [{"name": "a", "pid": 1, "cpu": 3, "ram_mb": None}])

    def test_a_failing_writer_never_propagates(self):
        sink = self.sink()
        self.writer.metric.side_effect = RuntimeError("boom")
        self.writer.action_event.side_effect = RuntimeError("boom")
        sink.readings(DEVICE, {"cpu.percent": 1})
        sink.action(DEVICE, "widget", "native_left_click", {}, "ok")

    def test_a_disabled_writer_makes_the_sink_inert(self):
        sink = TigerSink(NullWriter())
        sink.readings(DEVICE, {"cpu.percent": 1})
        sink.action(DEVICE, "widget", "lock", {}, "ok")

    def test_joystick_moves_are_never_logged_and_slider_drags_are_thinned(self):
        sink = self.sink()
        for _ in range(50):
            sink.action(DEVICE, "widget", "native_mouse", {}, "ok")
        self.writer.action_event.assert_not_called()
        for step in range(5):
            self.clock.now = step * 0.3
            sink.action(DEVICE, "widget", "native_volume", {"value": step}, "ok")
        self.assertEqual(self.writer.action_event.call_count, 2)       # t=0 and t=1.2
        sink.action(DEVICE, "widget", "native_left_click", {}, "ok")
        sink.action(DEVICE, "widget", "native_left_click", {}, "ok")
        self.assertEqual(self.writer.action_event.call_count, 4)       # discrete actions are always recorded


class NativeHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class Connection:
            identity = Identity(DEVICE, "owner")
            hello = SimpleNamespace(native_streams=["volume", "mouse", "metrics", "processes"])
        self.connection = Connection()
        self.sink = MagicMock()
        self.manager = NativeManager(SimpleNamespace(get=lambda device: self.connection), PanelEvents())
        self.manager.sink = self.sink

        async def send(kind, job, state, payload):
            await self.manager.receive(self.connection, SimpleNamespace(job_id=None, state="CONNECTED", payload={
                "type": "command_result", "stream": payload["target"], "request_id": payload["request_id"], "ok": self.ok,
                "data": {"value": payload["args"].get("value", 0)} if payload["target"] == "volume" else {}}))
        self.ok = True
        self.connection.send = AsyncMock(side_effect=send)

    def frame(self, payload):
        return SimpleNamespace(job_id=None, state="CONNECTED", payload=payload)

    async def test_readings_reach_the_sink_with_the_device_id(self):
        await self.manager.receive(self.connection, self.frame({"type": "telemetry", "stream": "metrics", "data": {
            "cpu_percent": 12.0, "ram": {"percent": 44.0, "used_bytes": 8 * 1048576}, "cpu_temperature_c": 60.0}}))
        device, values = self.sink.readings.call_args.args
        self.assertEqual(device, DEVICE)
        self.assertEqual((values["cpu.percent"], values["mem.percent"], values["cpu.temperature"]), (12.0, 44.0, 60.0))

    async def test_widget_actions_are_recorded_with_the_widget_source_by_default(self):
        await self.manager.command(self.connection.identity, "native_volume", 35)
        self.sink.action.assert_called_once_with(DEVICE, "widget", "native_volume", {"value": 35}, "ok", None)

    async def test_imessage_actions_carry_their_source_and_request_id(self):
        await self.manager.command(self.connection.identity, "native_volume", 20, source="imessage", request_id="msg-9")
        self.sink.action.assert_called_once_with(DEVICE, "imessage", "native_volume", {"value": 20}, "ok", "msg-9")
        self.sink.reset_mock()
        await self.manager.lock(self.connection.identity, source="imessage", request_id="msg-10")
        self.sink.action.assert_called_once_with(DEVICE, "imessage", "lock", {}, "ok", "msg-10")

    async def test_clicks_record_without_values_and_failures_record_the_reason(self):
        await self.manager.command(self.connection.identity, "native_left_click", None)
        self.sink.action.assert_called_once_with(DEVICE, "widget", "native_left_click", {}, "ok", None)
        self.sink.reset_mock()
        self.ok = False
        with self.assertRaisesRegex(PipelineError, "NATIVE_COMMAND_FAILED"):
            await self.manager.command(self.connection.identity, "native_volume", 5, source="imessage", request_id="m")
        self.sink.action.assert_called_once_with(DEVICE, "imessage", "native_volume", {"value": 5}, "error:NATIVE_COMMAND_FAILED", "m")
        self.sink.reset_mock()
        with self.assertRaises(PipelineError):
            await self.manager.lock(self.connection.identity)
        self.assertEqual(self.sink.action.call_args.args[4], "error:NATIVE_COMMAND_FAILED")

    async def test_invalid_actions_are_not_logged(self):
        with self.assertRaisesRegex(PipelineError, "NATIVE_ACTION_NOT_FOUND"):
            await self.manager.command(self.connection.identity, "shell", None)
        self.sink.action.assert_not_called()

    async def test_generated_program_dispatch_is_recorded(self):
        sink = MagicMock()
        runtime = SimpleNamespace(native=SimpleNamespace(sink=sink), jobs=SimpleNamespace(submit=AsyncMock(return_value="record")), store=None)
        payload = SimpleNamespace(request_sha256=None, program_id=None, job_id="job-1")
        widget = {"id": "w1", "action": "compute"}
        request = SimpleNamespace(operation="compute")
        with patch.object(panels, "action_request", AsyncMock(return_value=(widget, request))):
            self.assertEqual(await panels.dispatch(runtime, Identity(DEVICE, "owner"), payload), "record")
            sink.action.assert_called_once_with(DEVICE, "generated", "compute", {"widget_id": "w1", "operation": "compute"}, "submitted", "job-1")
            sink.reset_mock()
            runtime.jobs.submit = AsyncMock(side_effect=PipelineError("AGENT_OFFLINE", 503))
            with self.assertRaises(PipelineError):
                await panels.dispatch(runtime, Identity(DEVICE, "owner"), payload)
            self.assertEqual(sink.action.call_args.args[4], "error:AGENT_OFFLINE")

    async def test_dispatch_works_without_a_sink(self):
        runtime = SimpleNamespace(native=SimpleNamespace(sink=None), jobs=SimpleNamespace(submit=AsyncMock(return_value="record")), store=None)
        payload = SimpleNamespace(request_sha256=None, program_id=None, job_id="j")
        with patch.object(panels, "action_request", AsyncMock(return_value=({"id": "w", "action": "a"}, SimpleNamespace(operation="o")))):
            self.assertEqual(await panels.dispatch(runtime, Identity(DEVICE, "owner"), payload), "record")


class SchemaTests(unittest.TestCase):
    sql = "\n".join(statement for _, statement, _ in schema.STATEMENTS)

    def test_the_three_hypertables_have_the_specified_columns(self):
        for fragment in ("time timestamptz NOT NULL, device_id text NOT NULL, metric text NOT NULL, value double precision NOT NULL",
                         "name text NOT NULL, pid integer NOT NULL", "cpu double precision, ram_mb double precision",
                         "source text NOT NULL CHECK (source IN ('widget', 'imessage', 'generated'))",
                         "args jsonb NOT NULL", "outcome text NOT NULL, request_id text"):
            self.assertIn(fragment, self.sql)
        for table in ("metrics", "process_samples", "action_events"):
            self.assertIn(f"create_hypertable('{table}', 'time', if_not_exists => TRUE)", self.sql)

    def test_continuous_aggregates_and_their_refresh_policies(self):
        for view, bucket in (("metrics_1m", "1 minute"), ("metrics_1h", "1 hour")):
            self.assertIn(f"CREATE MATERIALIZED VIEW IF NOT EXISTS {view} WITH (timescaledb.continuous)", self.sql)
            self.assertIn(f"time_bucket('{bucket}', time)", self.sql)
            self.assertIn(f"add_continuous_aggregate_policy('{view}'", self.sql)
        for aggregate in ("avg(value)", "max(value)", "min(value)"):
            self.assertEqual(self.sql.count(aggregate), 2)

    def test_compression_after_one_day_and_30_day_retention(self):
        for table in ("metrics", "process_samples"):
            self.assertIn(f"add_compression_policy('{table}', INTERVAL '1 day', if_not_exists => TRUE)", self.sql)
        for table in ("metrics", "process_samples", "action_events"):
            self.assertIn(f"add_retention_policy('{table}', INTERVAL '30 days', if_not_exists => TRUE)", self.sql)

    def test_every_statement_is_written_to_be_re_runnable(self):
        for label, statement, _ in schema.STATEMENTS:
            self.assertTrue("IF NOT EXISTS" in statement or "if_not_exists => TRUE" in statement or "SET (timescaledb.compress" in statement, label)

    def test_migrate_runs_every_statement_in_order_and_reports(self):
        connection = MagicMock()
        lines = []
        skipped = schema.migrate(connection, lines.append)
        self.assertEqual([call.args[0] for call in connection.execute.call_args_list], [s for _, s, _ in schema.STATEMENTS])
        self.assertEqual((skipped, len(lines)), ([], len(schema.STATEMENTS)))

    def test_only_optional_steps_may_fail(self):
        connection = MagicMock()
        connection.execute.side_effect = lambda sql: (_ for _ in ()).throw(psycopg.errors.InsufficientPrivilege("no")) if "CREATE EXTENSION" in sql else None
        self.assertEqual(schema.migrate(connection), ["timescaledb extension"])
        connection.execute.side_effect = lambda sql: (_ for _ in ()).throw(psycopg.errors.SyntaxError("bad")) if "metrics_1m WITH" in sql else None
        with self.assertRaisesRegex(RuntimeError, "continuous aggregate metrics_1m"):
            schema.migrate(connection)

    def test_the_cli_does_nothing_without_a_url_and_uses_autocommit_with_one(self):
        with patch.dict(os.environ, {}, clear=True), patch("builtins.print"):
            self.assertEqual(migrate_main(), 0)
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch.dict(os.environ, {"TIGER_DATABASE_URL": "postgres://u:p@h/db"}), patch("app.tiger.migrate.connect", return_value=connection) as connect, patch("builtins.print"):
            self.assertEqual(migrate_main(), 0)
        self.assertEqual(connect.call_args.kwargs, {"autocommit": True})
        with patch.dict(os.environ, {"TIGER_DATABASE_URL": "postgres://u:p@h/db"}), patch("app.tiger.migrate.connect", side_effect=OSError("down")), patch("builtins.print"):
            self.assertEqual(migrate_main(), 1)


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    def test_conninfo_keeps_the_host_name_but_adds_the_ipv4_address(self):
        info = tiger_connection.conninfo_for("postgres://tsdbadmin:secret@abc.tsdb.cloud.timescale.com:35432/tsdb?sslmode=require", "203.0.113.7")
        for part in ("host=abc.tsdb.cloud.timescale.com", "hostaddr=203.0.113.7", "port=35432", "sslmode=require", "connect_timeout=5"):
            self.assertIn(part, info)

    def test_connect_prefers_ipv4_and_skips_resolution_for_addresses_and_sockets(self):
        with patch.object(tiger_connection, "resolve_ipv4_blocking", return_value="203.0.113.7") as resolve, patch("psycopg.connect") as connect:
            tiger_connection.connect("postgres://u:p@db.example.com:5433/tsdb")
            self.assertEqual(resolve.call_args.args, ("db.example.com", 5433))
            self.assertIn("hostaddr=203.0.113.7", connect.call_args.args[0])
            resolve.reset_mock()
            for url in ("postgres://u:p@127.0.0.1/tsdb", "postgres://u:p@[::1]/tsdb"):
                tiger_connection.connect(url)
            resolve.assert_not_called()

    def test_connect_still_works_when_ipv4_resolution_fails(self):
        with patch.object(tiger_connection, "resolve_ipv4_blocking", return_value=None), patch("psycopg.connect") as connect:
            tiger_connection.connect("postgres://u:p@db.example.com/tsdb", autocommit=True)
        self.assertNotIn("hostaddr", connect.call_args.args[0])
        self.assertEqual(connect.call_args.kwargs, {"autocommit": True})

    def test_blocking_resolver_asks_for_ipv4_only(self):
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("198.51.100.4", 5432))]) as lookup:
            self.assertEqual(tiger_connection.resolve_ipv4_blocking("h", 5432), "198.51.100.4")
        import socket
        self.assertEqual(lookup.call_args.kwargs["family"], socket.AF_INET)
        with patch("socket.getaddrinfo", side_effect=OSError):
            self.assertIsNone(tiger_connection.resolve_ipv4_blocking("h", 5432))

    def test_a_missing_psycopg_only_disables_tiger_it_does_not_break_the_backend(self):
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location("tiger_connection_without_psycopg", tiger_connection.__file__)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"psycopg": None, "psycopg.conninfo": None}):  # `import psycopg` now raises ImportError
            spec.loader.exec_module(module)
        self.assertIsNone(module.psycopg)
        self.assertEqual(module.DB_ERRORS, ())
        self.assertIsNone(module.tiger_url() if not os.getenv("TIGER_DATABASE_URL") else None)
        with self.assertRaisesRegex(RuntimeError, "psycopg"):
            module.connect("postgres://u:p@h/db")

    async def test_the_store_and_writer_treat_that_error_as_unavailable_not_a_crash(self):
        store = TigerStore("postgres://x", connect=MagicMock(side_effect=RuntimeError("psycopg is missing")))
        with self.assertLogs("uvicorn.error", level="WARNING"), self.assertRaises(TigerUnavailable):
            await store.recent_activity(DEVICE)
        writer = TigerWriter("postgres://x", connect=MagicMock(side_effect=RuntimeError("psycopg is missing")))
        with self.assertLogs("uvicorn.error", level="WARNING"):
            await writer._flush([("metrics", (datetime.now(timezone.utc), DEVICE, "cpu.percent", 1.0))])
        self.assertEqual((writer.written, writer.failures), (0, 1))
        await writer.stop()

    def test_url_helper(self):
        with patch.dict(os.environ, {"TIGER_DATABASE_URL": "  postgres://x  "}):
            self.assertEqual(tiger_connection.tiger_url(), "postgres://x")
        with patch.dict(os.environ, {"TIGER_DATABASE_URL": "   "}):
            self.assertIsNone(tiger_connection.tiger_url())


class FakeStoreConnection:
    def __init__(self, responses, seen):
        self.responses, self.seen = responses, seen

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, row_factory=None):
        return self

    def execute(self, sql, params):
        self.seen.append((sql, params))
        self.rows = next(rows for needle, rows in self.responses.items() if needle in sql)
        if isinstance(self.rows, Exception):
            raise self.rows

    def fetchall(self):
        return self.rows


def store_with(responses, seen=None):
    seen = [] if seen is None else seen
    return TigerStore("postgres://x", connect=lambda url: FakeStoreConnection(responses, seen), clock=Clock()), seen


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_metric_history_reads_the_one_minute_aggregate(self):
        bucket = datetime(2026, 10, 11, 16, 31, tzinfo=timezone.utc)
        store, seen = store_with({"FROM metrics_1m": [{"bucket": bucket, "avg_value": 40.0, "max_value": 55.0, "min_value": 30.0}]})
        points = await store.metric_history(DEVICE, "cpu.percent", 60)
        self.assertEqual(points, [{"time": bucket.isoformat(), "avg": 40.0, "max": 55.0, "min": 30.0}])
        self.assertEqual(seen[0][1], (DEVICE, "cpu.percent", 60))
        self.assertIn("ORDER BY bucket", seen[0][0])
        self.assertNotIn("cpu.percent", seen[0][0])  # values travel as parameters, never inside the SQL text

    async def test_invalid_metric_names_never_reach_the_database(self):
        store, seen = store_with({"FROM metrics_1m": []})
        for name in ("", "CPU", "cpu percent", "cpu'; drop table metrics;--", "a" * 65):
            with self.assertRaises(ValueError):
                await store.metric_history(DEVICE, name, 60)
        self.assertEqual(seen, [])

    def test_only_whitelisted_ranges_parse(self):
        self.assertEqual([parse_range(r) for r in ("15m", "1h", "24h")], [15, 60, 1440])
        for bad in ("", "2h", "1d", "60", "-1h", "1H", "1h; select 1"):
            with self.assertRaises(ValueError):
                parse_range(bad)

    async def test_recent_activity_maps_rows_and_caps_the_limit(self):
        moment = datetime(2026, 10, 11, 16, 31, tzinfo=timezone.utc)
        row = {"time": moment, "source": "imessage", "action_id": "lock", "args": {}, "outcome": "ok", "request_id": "m1"}
        store, seen = store_with({"FROM action_events": [row]})
        events = await store.recent_activity(DEVICE, 50)
        self.assertEqual(events, [{"time": moment.isoformat(), "source": "imessage", "action_id": "lock", "args": {}, "outcome": "ok", "request_id": "m1"}])
        await store.recent_activity(DEVICE, 10_000)
        self.assertEqual(seen[-1][1], (DEVICE, 200))

    async def test_worst_minute_and_top_apps(self):
        bucket = datetime(2026, 10, 11, 16, 31, tzinfo=timezone.utc)
        store, seen = store_with({"ORDER BY max_value DESC": [{"bucket": bucket, "max_value": 94.2}],
                                  "FROM process_samples": [{"name": "chrome.exe", "peak": 80}, {"name": "Code.exe", "peak": 40}]})
        self.assertEqual(await store.worst_minute(DEVICE, 60), {"bucket": bucket, "cpu": 94.2})
        self.assertEqual(await store.top_apps(DEVICE, bucket, 3), ["chrome.exe", "Code.exe"])
        self.assertEqual(seen[-1][1], (DEVICE, bucket, bucket, 3))
        empty, _ = store_with({"ORDER BY max_value DESC": []})
        self.assertIsNone(await empty.worst_minute(DEVICE, 60))

    async def test_connection_errors_become_tigerunavailable_and_are_logged_once_a_minute(self):
        clock = Clock()
        store = TigerStore("postgres://x", connect=MagicMock(side_effect=psycopg.OperationalError("secret-host unreachable")), clock=clock)
        with self.assertLogs("uvicorn.error", level="WARNING") as logs:
            for _ in range(3):
                with self.assertRaises(TigerUnavailable):
                    await store.metric_history(DEVICE, "cpu.percent", 60)
            self.assertEqual(len(logs.records), 1)
            clock.now += 61
            with self.assertRaises(TigerUnavailable):
                await store.recent_activity(DEVICE)
            self.assertEqual(len(logs.records), 2)
        self.assertNotIn("secret-host", "".join(logs.output))

    async def test_check_reports_missing_schema_pieces(self):
        store, _ = store_with({"hypertables": [{"name": "metrics"}], "continuous_aggregates": [{"name": "metrics_1m"}]})
        report = await store.check()
        self.assertEqual((report["reachable"], report["schema"]), (True, False))
        self.assertEqual(set(report["missing"]), {"process_samples", "action_events", "metrics_1h"})
        full, _ = store_with({"hypertables": [{"name": n} for n in ("metrics", "process_samples", "action_events")],
                              "continuous_aggregates": [{"name": "metrics_1m"}, {"name": "metrics_1h"}]})
        self.assertEqual(await full.check(), {"reachable": True, "schema": True, "missing": []})
        down = TigerStore("postgres://x", connect=MagicMock(side_effect=OSError("no route")))
        with self.assertLogs("uvicorn.error", level="WARNING"):
            self.assertEqual((await down.check())["reachable"], False)


class RouteTests(unittest.TestCase):
    def client(self, store, device=DEVICE):
        app = FastAPI()
        app.state.tiger_store = store
        app.dependency_overrides[controller] = lambda: Identity(device, "owner")
        app.include_router(history_routes.router)
        return TestClient(app)

    def test_history_returns_points_for_the_callers_own_device(self):
        store = MagicMock()
        store.metric_history = AsyncMock(return_value=[{"time": "t", "avg": 1, "max": 2, "min": 0}])
        body = self.client(store, "my-laptop").get("/api/metrics/history", params={"metric": "cpu.percent", "range": "1h"}).json()
        self.assertEqual(body, {"available": True, "metric": "cpu.percent", "range": "1h", "points": [{"time": "t", "avg": 1, "max": 2, "min": 0}]})
        store.metric_history.assert_awaited_once_with("my-laptop", "cpu.percent", 60)

    def test_history_defaults_to_one_hour(self):
        store = MagicMock()
        store.metric_history = AsyncMock(return_value=[])
        self.client(store).get("/api/metrics/history", params={"metric": "mem.percent"})
        store.metric_history.assert_awaited_once_with(DEVICE, "mem.percent", 60)

    def test_unconfigured_or_unreachable_means_available_false_not_an_error(self):
        for store in (None,):
            response = self.client(store).get("/api/metrics/history", params={"metric": "cpu.percent"})
            self.assertEqual((response.status_code, response.json()["available"], response.json()["points"]), (200, False, []))
            self.assertEqual(self.client(store).get("/api/activity").json(), {"available": False, "events": []})
        down = MagicMock()
        down.metric_history = AsyncMock(side_effect=TigerUnavailable("OperationalError"))
        down.recent_activity = AsyncMock(side_effect=TigerUnavailable("OperationalError"))
        self.assertFalse(self.client(down).get("/api/metrics/history", params={"metric": "cpu.percent"}).json()["available"])
        self.assertEqual(self.client(down).get("/api/activity").json(), {"available": False, "events": []})

    def test_bad_input_is_rejected_before_any_query(self):
        store = MagicMock()
        store.metric_history = AsyncMock()
        client = self.client(store)
        for params in ({"metric": "cpu.percent", "range": "99h"}, {"metric": "CPU"}, {"metric": "a;b"}, {}):
            self.assertEqual(client.get("/api/metrics/history", params=params).status_code, 422, params)
        store.metric_history.assert_not_awaited()

    def test_activity_returns_the_last_50(self):
        store = MagicMock()
        store.recent_activity = AsyncMock(return_value=[{"action_id": "lock"}])
        self.assertEqual(self.client(store).get("/api/activity").json(), {"available": True, "events": [{"action_id": "lock"}]})
        store.recent_activity.assert_awaited_once_with(DEVICE, 50)


@unittest.skipUnless(os.getenv("TIGER_DATABASE_URL") and os.getenv("JANUS_TIGER_INTEGRATION") == "1",
                     "set TIGER_DATABASE_URL and JANUS_TIGER_INTEGRATION=1 to run the Tiger Data integration test")
class TigerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Runs against a real TimescaleDB (Tiger Cloud or a local container) and writes to it: it applies the schema, inserts
    rows under a unique 'itest-<time>' device id, and removes them (including their aggregate buckets) afterwards.

    Needs BOTH variables, on purpose. backend/.env is loaded by app.config, so TIGER_DATABASE_URL alone can be present
    without anyone having asked for this test to touch the database; the explicit JANUS_TIGER_INTEGRATION=1 prevents that."""

    async def test_migrate_write_aggregate_and_read_back(self):
        url = os.environ["TIGER_DATABASE_URL"]
        device = f"itest-{int(time.time())}"
        with tiger_connection.connect(url, autocommit=True) as connection:
            schema.migrate(connection)
            schema.migrate(connection)  # idempotent: a second run changes nothing and does not fail
        writer = TigerWriter(url, flush_seconds=0.2)
        await writer.start()
        base = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=3)
        for minute, cpu in enumerate((20.0, 94.0, 30.0)):
            at = base + timedelta(minutes=minute, seconds=5)
            writer.metric(device, "cpu.percent", cpu, at=at)
            writer.metric(device, "cpu.percent", cpu - 4, at=at + timedelta(seconds=20))
        peak = base + timedelta(minutes=1, seconds=10)
        writer.process_samples(device, [{"name": "chrome.exe", "pid": 1, "cpu": 70.0, "ram_mb": 900.0}, {"name": "Code.exe", "pid": 2, "cpu": 20.0, "ram_mb": 400.0}], at=peak)
        writer.action_event(device, "imessage", "lock", {"k": "v"}, "ok", "req-1")
        self.assertTrue(await until(lambda: writer.written >= 9, 10))
        await writer.stop()
        try:
            with tiger_connection.connect(url, autocommit=True) as connection:
                connection.execute("CALL refresh_continuous_aggregate('metrics_1m', NULL, NULL)")
            store = TigerStore(url)
            points = await store.metric_history(device, "cpu.percent", 60)
            self.assertEqual(len(points), 3)
            self.assertEqual(max(p["max"] for p in points), 94.0)
            worst = await store.worst_minute(device, 60)
            self.assertEqual(round(worst["cpu"]), 94)
            self.assertEqual(await store.top_apps(device, worst["bucket"], 3), ["chrome.exe", "Code.exe"])
            events = await store.recent_activity(device, 50)
            self.assertEqual((events[0]["action_id"], events[0]["args"]), ("lock", {"k": "v"}))
            report = await store.check()
            self.assertEqual((report["reachable"], report["schema"]), (True, True))
        finally:
            with tiger_connection.connect(url, autocommit=True) as connection:
                for table in ("metrics", "process_samples", "action_events"):
                    connection.execute(f"DELETE FROM {table} WHERE device_id = %s", (device,))
                # Deleting raw rows does not remove buckets already materialised: refreshing the window recomputes it
                # from the remaining raw data, which drops this device's buckets and leaves real devices untouched.
                connection.execute("CALL refresh_continuous_aggregate('metrics_1m', now() - interval '3 hours', now() + interval '1 minute')")
                connection.execute("CALL refresh_continuous_aggregate('metrics_1h', now() - interval '3 hours', now() + interval '1 hour')")


if __name__ == "__main__":
    unittest.main()
