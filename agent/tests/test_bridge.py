"""Transport queues, per-device ordering, concurrent startup, and all shutdown paths."""
import asyncio
from contextlib import redirect_stderr
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

from agent.bridge import MAX_LINE, NativeProcess, Outbox, command_line, connected_session, receive_commands, run_bridge, scalar, validate_options
from agent.jobs import ValidationService
from tests.support import COMPILER, job


class SocketFixture:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.messages.get()
        if value is None:
            raise StopAsyncIteration
        return value

    def feed(self, value):
        self.messages.put_nowait(json.dumps(value) if not isinstance(value, (str, bytes)) else value)

    def end(self):
        self.messages.put_nowait(None)

    async def send(self, message):
        self.sent.append(json.loads(message))


def process_fixture():
    process = SimpleNamespace(stdout=asyncio.StreamReader(), stdin=SimpleNamespace(write=MagicMock(), drain=AsyncMock()), returncode=None)
    def stop():
        process.returncode = 0
    async def wait():
        stop()
        return 0
    process.terminate, process.kill, process.wait = MagicMock(side_effect=stop), MagicMock(side_effect=stop), AsyncMock(side_effect=wait)
    return process


class BridgeInputTests(unittest.TestCase):
    def test_scalar_boundaries_reject_nonnumbers_and_overflow(self):
        for value in (-1, 0, 1):
            self.assertEqual(scalar(value, -1, 1), value)
        for value in (True, None, "1", [], float("nan"), float("inf"), 10**1000, 2):
            with self.subTest(value=repr(value)[:40]), self.assertRaises(ValueError):
                scalar(value, -1, 1)

    def test_all_allowed_button_states_and_scroll_boundaries(self):
        for button in ("left", "right", "middle"):
            for state in ("up", "down"):
                self.assertEqual(command_line({"target": "mouse", "action": "button", "args": {"button": button, "state": state}}), ("mouse", f"button {button} {state}\n"))
        for amount in (-100, 0, 100, 1.0):
            self.assertEqual(command_line({"target": "mouse", "action": "scroll", "args": {"amount": amount}}), ("mouse", f"scroll {int(amount)}\n"))
        for message in ({"args": []}, {"target": "mouse", "action": "button", "args": {"button": "bad", "state": "down"}}, {"target": "mouse", "action": "scroll", "args": {"amount": 0.5}}, {"target": "mouse", "action": "unknown"}):
            with self.subTest(message=message), self.assertRaises(ValueError):
                command_line(message)

    def test_url_and_each_stream_limit_reject_invalid_values(self):
        options = SimpleNamespace(url="wss://fixture/ws/agent", screen_interval=1000, min_cpu=1, min_ram_mb=100)
        for url in (None, "", "https://fixture/ws/agent", "wss:///ws/agent", "wss://fixture", "wss://fixture/ws/agent#fragment", "wss://user@fixture/ws/agent", "ws://remote/ws/agent"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_options(SimpleNamespace(**{**vars(options), "url": url}))
        for name, value in (("screen_interval", 199), ("screen_interval", 60001), ("min_cpu", -1), ("min_ram_mb", 1000001)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_options(SimpleNamespace(**{**vars(options), name: value}))
        for url in ("ws://localhost/ws/agent", "ws://[::1]/ws/agent", "wss://fixture/ws/agent"):
            validate_options(SimpleNamespace(**{**vars(options), "url": url}))


class OutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_consumer_wakes_for_samples_and_results(self):
        box = Outbox()
        pending = asyncio.create_task(box.get())
        await asyncio.sleep(0)
        box.sample({"stream": "metrics", "seq": 1})
        self.assertEqual(await asyncio.wait_for(pending, 1), {"stream": "metrics", "seq": 1})
        pending = asyncio.create_task(box.get())
        await asyncio.sleep(0)
        await box.result({"type": "result"})
        self.assertEqual(await asyncio.wait_for(pending, 1), {"type": "result"})

    async def test_backpressure_is_bounded_and_pending_result_resumes_when_drained(self):
        box = Outbox()
        for i in range(64):
            await box.result({"seq": i})
        producer = asyncio.create_task(box.result({"seq": 64}))
        await asyncio.sleep(0)
        self.assertFalse(producer.done())
        self.assertEqual((await box.get())["seq"], 0)
        await asyncio.wait_for(producer, 1)
        self.assertEqual(box.results.qsize(), 64)


class NativeProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_samples_errors_and_pending_ack_are_routed_separately(self):
        process, box = process_fixture(), Outbox()
        native = NativeProcess("volume", process, box)
        pending = asyncio.get_running_loop().create_future()
        native.pending = pending
        events = [{"type": "telemetry", "stream": "volume", "seq": 1}, {"type": "command_result", "stream": "volume", "ok": False}, {"type": "error", "stream": "volume", "seq": 2}]
        for event in events:
            process.stdout.feed_data((json.dumps(event) + "\n").encode())
        process.stdout.feed_eof()
        with self.assertRaisesRegex(RuntimeError, "native process exited"):
            await native.read()
        self.assertEqual(await pending, events[1])
        self.assertEqual(await box.get(), events[2])

    async def test_invalid_native_json_stream_and_event_type_fail_connection(self):
        for line in (b"bad\n", b"\xff\n", b"[]\n", b'{"stream":"mouse","type":"telemetry"}\n', b'{"stream":"volume","type":"unexpected"}\n'):
            with self.subTest(line=line):
                process = process_fixture()
                process.stdout.feed_data(line)
                process.stdout.feed_eof()
                with self.assertRaisesRegex(RuntimeError, "invalid native JSON"):
                    await NativeProcess("volume", process, Outbox()).read()

    async def test_oversize_line_is_a_reconnectable_native_failure(self):
        process = process_fixture()
        process.stdout = asyncio.StreamReader(limit=16)
        process.stdout.feed_data(b"x" * 32 + b"\n")
        with self.assertRaisesRegex(RuntimeError, "framing limit"):
            await NativeProcess("volume", process, Outbox()).read()

    async def test_stray_and_already_completed_acks_cannot_replace_pending_result(self):
        for completed in (False, True):
            process = process_fixture()
            native = NativeProcess("volume", process, Outbox())
            if completed:
                native.pending = asyncio.get_running_loop().create_future()
                native.pending.set_result({"original": True})
            process.stdout.feed_data(b'{"stream":"volume","type":"command_result"}\n')
            process.stdout.feed_eof()
            with self.assertRaises(RuntimeError):
                await native.read()
            if completed:
                self.assertEqual(native.pending.result(), {"original": True})

    async def test_concurrent_commands_on_one_device_keep_ack_order(self):
        process = process_fixture()
        native = NativeProcess("volume", process, Outbox())
        first = asyncio.create_task(native.command("set 1\n"))
        await asyncio.sleep(0)
        second = asyncio.create_task(native.command("set 2\n"))
        await asyncio.sleep(0)
        self.assertEqual(process.stdin.write.call_args_list[0].args, (b"set 1\n",))
        self.assertEqual(process.stdin.write.call_count, 1)
        native.pending.set_result({"value": 1})
        self.assertEqual(await asyncio.wait_for(first, 1), {"value": 1})
        async def second_started():
            while process.stdin.write.call_count < 2:
                await asyncio.sleep(0)
        await asyncio.wait_for(second_started(), 1)
        native.pending.set_result({"value": 2})
        self.assertEqual(await asyncio.wait_for(second, 1), {"value": 2})
        self.assertIsNone(native.pending)

    async def test_ack_timeout_clears_pending_and_requires_reconnect(self):
        process = process_fixture()
        native = NativeProcess("volume", process, Outbox())
        real_wait = asyncio.wait_for
        with patch("agent.bridge.asyncio.wait_for", new=lambda future, timeout: real_wait(future, 0.01)), self.assertRaisesRegex(RuntimeError, "acknowledgement timed out"):
            await native.command("set 1\n")
        self.assertIsNone(native.pending)

    async def test_close_handles_already_exited_graceful_terminate_kill_and_missing_process(self):
        process = process_fixture()
        process.returncode = 0
        await NativeProcess("mouse", process, Outbox()).close()
        process.terminate.assert_not_called()
        process = process_fixture()
        await NativeProcess("volume", process, Outbox()).close()
        process.stdin.write.assert_called_once_with(b"stop\n")
        process.terminate.assert_not_called()
        process = process_fixture()
        process.wait = AsyncMock(side_effect=[TimeoutError(), TimeoutError(), 0])
        await NativeProcess("mouse", process, Outbox()).close()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        process = process_fixture()
        process.terminate.side_effect = ProcessLookupError()
        await NativeProcess("metrics", process, Outbox()).close()
        process = process_fixture()
        process.stdin.drain.side_effect = BrokenPipeError()
        await NativeProcess("volume", process, Outbox()).close()
        process.terminate.assert_called_once()


class CommandRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def setup_receiver(self, tools=None, validation=None):
        socket, box = SocketFixture(), Outbox()
        task = asyncio.create_task(receive_commands(socket, tools or {}, box, validation))
        async def cleanup():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.addAsyncCleanup(cleanup)
        return socket, box, task

    async def test_slow_volume_does_not_block_mouse_ping_or_revisions(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def volume(line):
            started.set()
            await release.wait()
            return {"type": "command_result", "stream": "volume", "ok": True}
        tools = {"volume": SimpleNamespace(command=AsyncMock(side_effect=volume)), "mouse": SimpleNamespace(command=AsyncMock(return_value={"type": "command_result", "stream": "mouse", "ok": True}))}
        validation = SimpleNamespace(handle=AsyncMock())
        socket, box, _ = await self.setup_receiver(tools, validation)
        socket.feed({"type": "command", "request_id": "v", "target": "volume", "action": "set", "args": {"value": 1}})
        await asyncio.wait_for(started.wait(), 1)
        socket.feed({"type": "command", "request_id": "m", "target": "mouse", "action": "scroll", "args": {"amount": 1}})
        socket.feed({"type": "job", "job_id": "ping", "action": "ping"})
        socket.feed({"type": "code_revision", "job_id": "revision"})
        events = [await asyncio.wait_for(box.get(), 1) for _ in range(2)]
        self.assertEqual({event.get("request_id", event.get("job_id")) for event in events}, {"m", "ping"})
        validation.handle.assert_awaited_once()
        release.set()
        self.assertEqual((await asyncio.wait_for(box.get(), 1))["request_id"], "v")

    async def test_queue_overflow_rejects_extra_commands_and_receiver_still_handles_ping(self):
        started = asyncio.Event()
        async def blocked(line):
            started.set()
            await asyncio.Event().wait()
        socket, box, _ = await self.setup_receiver({"volume": SimpleNamespace(command=AsyncMock(side_effect=blocked))})
        command = {"type": "command", "target": "volume", "action": "set", "args": {"value": 1}}
        socket.feed({**command, "request_id": "active"})
        await asyncio.wait_for(started.wait(), 1)
        for i in range(33):
            socket.feed({**command, "request_id": str(i)})
        socket.feed({"type": "job", "job_id": "ping", "action": "ping"})
        rejected = await asyncio.wait_for(box.get(), 1)
        self.assertEqual((rejected["request_id"], rejected["ok"]), ("32", False))
        self.assertIn("Too many pending", rejected["error"])
        self.assertEqual((await asyncio.wait_for(box.get(), 1))["job_id"], "ping")

    async def test_worker_failure_cancels_other_device_workers(self):
        mouse_started, mouse_cancelled = asyncio.Event(), asyncio.Event()
        async def mouse(line):
            mouse_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                mouse_cancelled.set()
        tools = {"mouse": SimpleNamespace(command=AsyncMock(side_effect=mouse)), "volume": SimpleNamespace(command=AsyncMock(side_effect=RuntimeError("missing ack")))}
        socket, _, task = await self.setup_receiver(tools)
        socket.feed({"type": "command", "request_id": "m", "target": "mouse", "action": "scroll", "args": {"amount": 1}})
        await asyncio.wait_for(mouse_started.wait(), 1)
        socket.feed({"type": "command", "request_id": "v", "target": "volume", "action": "set", "args": {"value": 1}})
        with self.assertRaisesRegex(RuntimeError, "missing ack"):
            await asyncio.wait_for(task, 1)
        self.assertTrue(mouse_cancelled.is_set())

    async def test_invalid_messages_return_typed_errors_without_stopping_receiver(self):
        socket, box, _ = await self.setup_receiver()
        bad = ["{", [], {"type": "unexpected"}, {"type": "job", "job_id": 1}, {"type": "job", "job_id": "j", "args": []}, {"type": "job", "job_id": "j", "action": "unsupported"}, {"type": "command", "request_id": ""}, {"type": "command", "request_id": "x" * 129}, {"type": "command", "request_id": "c", "target": "volume", "action": "set", "args": {"value": 1}}]
        for message in bad:
            with self.subTest(message=str(message)[:50]):
                socket.feed(message)
                event = await asyncio.wait_for(box.get(), 1)
                self.assertFalse(event["ok"])
                self.assertEqual(event["type"], "job_result" if isinstance(message, dict) and message.get("type") == "job" else "command_result")
        socket.feed(job())
        self.assertEqual((await asyncio.wait_for(box.get(), 1))["reason"], "VALIDATION_UNAVAILABLE")
        socket.feed({"type": "job", "job_id": "add", "action": "add", "args": {"a": -2, "b": 5}})
        self.assertEqual((await asyncio.wait_for(box.get(), 1))["result"], {"sum": 3})


class ConnectionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def options(self, screen=True):
        return SimpleNamespace(url="wss://fixture/ws/agent", screen=screen, screen_interval=500, min_cpu=2, min_ram_mb=50, temperature_file="local sensor.txt")

    async def test_all_native_processes_start_concurrently_and_are_closed_on_disconnect(self):
        options = self.options()
        socket = SocketFixture()
        socket.end()
        processes, calls = {}, []
        ready = asyncio.Event()
        async def spawn(name, *args, **kwargs):
            calls.append((name, args, kwargs))
            if len(calls) == 5:
                ready.set()
            await ready.wait()
            processes[name] = process_fixture()
            return processes[name]
        service = ValidationService(COMPILER)
        with patch("agent.bridge.asyncio.create_subprocess_exec", side_effect=spawn):
            await asyncio.wait_for(connected_session(socket, options, {name: name for name in ("volume", "mouse", "metrics", "processes", "screen")}, service), 2)
        self.assertEqual(len(processes), 5)
        self.assertTrue(all(process.returncode == 0 for process in processes.values()))
        arguments = {name: argv for name, argv, _ in calls}
        self.assertEqual(arguments["metrics"], ("--temperature-file", "local sensor.txt"))
        self.assertEqual(arguments["screen"], ("--interval", "500", "--request-permission"))
        self.assertTrue(all(kwargs["limit"] == MAX_LINE for _, _, kwargs in calls))
        hello = next(event for event in socket.sent if event["type"] == "agent_hello")
        self.assertFalse(hello["validation"]["build_boundary_available"])

    async def test_one_failed_spawn_reaps_other_concurrently_started_processes(self):
        processes, calls = [], []
        ready = asyncio.Event()
        async def spawn(name, *args, **kwargs):
            calls.append(name)
            if len(calls) == 4:
                ready.set()
            await ready.wait()
            if name == "volume":
                raise OSError("fixture spawn failure")
            process = process_fixture()
            processes.append(process)
            return process
        with patch("agent.bridge.asyncio.create_subprocess_exec", side_effect=spawn), self.assertRaises(OSError):
            await asyncio.wait_for(connected_session(SocketFixture(), self.options(False), {name: name for name in ("volume", "mouse", "metrics", "processes")}), 2)
        self.assertEqual(len(processes), 3)
        self.assertTrue(all(process.returncode == 0 for process in processes))

    async def test_normal_websocket_close_while_sending_is_not_a_native_failure(self):
        socket = SocketFixture()
        socket.send = AsyncMock(side_effect=ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True))
        processes = []
        async def spawn(*args, **kwargs):
            process = process_fixture()
            processes.append(process)
            return process
        with patch("agent.bridge.asyncio.create_subprocess_exec", side_effect=spawn):
            await asyncio.wait_for(connected_session(socket, self.options(False), {name: name for name in ("volume", "mouse", "metrics", "processes")}), 1)
        self.assertTrue(all(process.returncode == 0 for process in processes))

    async def test_cancellation_during_parallel_startup_reaps_every_spawn(self):
        calls, processes = [], []
        started, release = asyncio.Event(), asyncio.Event()
        async def spawn(name, *args, **kwargs):
            calls.append(name)
            if len(calls) == 4:
                started.set()
            await release.wait()
            process = process_fixture()
            processes.append(process)
            return process
        with patch("agent.bridge.asyncio.create_subprocess_exec", side_effect=spawn):
            task = asyncio.create_task(connected_session(SocketFixture(), self.options(False), {name: name for name in ("volume", "mouse", "metrics", "processes")}))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self.assertEqual(len(processes), 4)
        self.assertTrue(all(process.returncode == 0 for process in processes))

    async def test_reconnect_backoff_caps_and_resets_after_success(self):
        sleeps = []
        async def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 7:
                raise asyncio.CancelledError
        with patch("agent.bridge.connect", side_effect=OSError("offline")), patch("agent.bridge.asyncio.sleep", side_effect=sleep), redirect_stderr(io.StringIO()), self.assertRaises(asyncio.CancelledError):
            await run_bridge(self.options(), {}, "private-token")
        self.assertEqual(sleeps, [1, 2, 4, 8, 16, 30, 30])

        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=SocketFixture())
        context.__aexit__ = AsyncMock(return_value=False)
        sleeps.clear()
        async def four_sleeps(delay):
            sleeps.append(delay)
            if len(sleeps) == 4:
                raise asyncio.CancelledError
        with patch("agent.bridge.connect", side_effect=[OSError("offline"), OSError("offline"), context, OSError("offline")]) as connect, patch("agent.bridge.connected_session", AsyncMock()), patch("agent.bridge.asyncio.sleep", side_effect=four_sleeps), redirect_stderr(io.StringIO()) as stderr, self.assertRaises(asyncio.CancelledError):
            await run_bridge(self.options(), {}, "private-token")
        self.assertEqual(sleeps, [1, 2, 1, 2])
        self.assertEqual(connect.call_args.kwargs["additional_headers"], {"Authorization": "Bearer private-token"})
        self.assertNotIn("private-token", stderr.getvalue())
