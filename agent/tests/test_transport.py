"""Protocol v2 framing, heartbeat, dispatch, teardown, and reconnect unit tests."""
import asyncio
import io
import json
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
from websockets.frames import Close

from agent.jobs import ValidationService
from agent.transport import connected_session, decode, packet, run_connection, send, validate_options
from agent.validation import ExecutionPolicy
from tests.support import COMPILER, LOCAL, SimulatedBoundary, job


class Socket:
    def __init__(self):
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()

    async def send(self, raw):
        await self.outgoing.put(json.loads(raw))

    async def recv(self):
        return await self.incoming.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.recv()
        if value is None:
            raise StopAsyncIteration
        return value

    def put(self, kind, job_id, state, payload):
        self.incoming.put_nowait(json.dumps(packet("laptop", kind, job_id, state, payload)))


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def test_connection_options_require_registered_device_tls_path_and_local_worker_limit(self):
        valid = {"url": "wss://backend/ws/agents/laptop", "device_id": "laptop", "workers": 4}
        validate_options(SimpleNamespace(**valid))
        validate_options(SimpleNamespace(**{**valid, "url": "ws://127.0.0.1/ws/agents/laptop"}))
        for change in ({"device_id": None}, {"device_id": "../bad"}, {"url": None}, {"url": "https://host/ws/agents/laptop"},
                       {"url": "ws://remote/ws/agents/laptop"}, {"url": "wss://u:p@host/ws/agents/laptop"},
                       {"url": "wss://host/ws/agents/laptop?token=private"}, {"url": "wss://host/ws/agents/other"},
                       {"url": "wss://host/ws/agents/laptop#x"}, {"workers": 0}, {"workers": 5}, {"workers": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_options(SimpleNamespace(**{**valid, **change}))

    def test_frames_reject_binary_oversize_nonfinite_wrong_version_identity_and_shape(self):
        valid = packet("laptop", "ping", None, "CONNECTED", {"nonce": "abc"})
        self.assertEqual(decode(json.dumps(valid), "laptop"), valid)
        for raw in (b"binary", "x" * (1024 * 1024 + 1), "[]", "not-json", json.dumps({**valid, "version": 1}),
                    json.dumps({**valid, "version": True}), json.dumps({**valid, "device_id": "other"}),
                    json.dumps({**valid, "payload": []}), json.dumps({**valid, "type": 3}), json.dumps({**valid, "state": 2}),
                    json.dumps({**valid, "job_id": "../bad"}), json.dumps({**valid, "extra": 1}),
                    json.dumps({**valid, "payload": {"x": float("nan")}})):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                decode(raw, "laptop")

    async def test_json_sends_are_standard_text_and_reject_nonfinite_values(self):
        socket = Socket()
        await send(socket, packet("laptop", "test", None, "CONNECTED", {"text": "é"}))
        self.assertEqual((await socket.outgoing.get())["payload"], {"text": "é"})
        with self.assertRaises(ValueError):
            await send(socket, {"x": float("inf")})

    async def start(self, *, service=None):
        socket = Socket()
        socket.put("connection_ready", None, "CONNECTED", {"heartbeat_interval": 10})
        service = service or ValidationService(COMPILER, {"job-123": ExecutionPolicy("job-123", "compute", execute_authorized=True)},
                                               SimulatedBoundary(), local_platform=LOCAL)
        ready = asyncio.Event()
        task = asyncio.create_task(connected_session(socket, "laptop", service, on_ready=ready.set))
        await asyncio.wait_for(ready.wait(), 1)
        hello = await socket.outgoing.get()
        return socket, service, task, hello

    async def next_type(self, socket, kind):
        async with asyncio.timeout(1):
            while True:
                frame = await socket.outgoing.get()
                if frame["type"] == kind:
                    return frame

    async def test_handshake_job_ack_validation_and_execution_reuse_existing_interfaces(self):
        socket, service, task, hello = await self.start()
        self.assertEqual(hello["payload"]["architecture"], "arm64")
        self.assertTrue(hello["payload"]["build_boundary_available"])
        payload = {key: value for key, value in job().items() if key not in ("type", "job_id")}
        socket.put("validate_job", "job-123", "VALIDATING", payload)
        ack = await self.next_type(socket, "job_ack")
        self.assertEqual(ack["payload"], {"accepted_type": "validate_job"})
        result = await self.next_type(socket, "validation_result")
        self.assertEqual(result["state"], "READY_FOR_EXECUTION")
        socket.put("execute_job", "job-123", "EXECUTING", {"source_sha256": result["payload"]["source_sha256"]})
        execution = await self.next_type(socket, "execution_result")
        self.assertEqual(execution["state"], "COMPLETED")
        self.assertEqual(set(execution["payload"]), {"status", "reason"})
        socket.incoming.put_nowait(None)
        await task

    async def test_heartbeats_and_repair_are_processed_while_job_waits(self):
        boundary = SimulatedBoundary(["program.c:1:2: error: undeclared fixture"])
        service = ValidationService(COMPILER, {"job-123": ExecutionPolicy("job-123", "compute")}, boundary, local_platform=LOCAL)
        socket, _, task, _ = await self.start(service=service)
        socket.put("validate_job", "job-123", "VALIDATING", {key: value for key, value in job().items() if key not in ("type", "job_id")})
        repair = await self.next_type(socket, "repair_requested")
        self.assertEqual(repair["state"], "REPAIRING")
        socket.put("ping", None, "CONNECTED", {"nonce": "abc"})
        self.assertEqual((await self.next_type(socket, "pong"))["payload"], {"nonce": "abc"})
        socket.put("code_revision", "job-123", "REPAIRING", {"attempt": 2, "language": "c11", "source": "int main(void) { return 0; }\n"})
        result = await self.next_type(socket, "validation_result")
        self.assertEqual(result["payload"]["attempts"], 2)
        directory = boundary.directories[-1]
        socket.incoming.put_nowait(None)
        await task
        self.assertFalse(directory.exists())

    async def test_cancellation_is_acknowledged_and_does_not_block_heartbeats(self):
        class Session:
            def __init__(self, outbox):
                self.outbox = outbox
                self.started, self.release = asyncio.Event(), asyncio.Event()
                self.closed = False
            async def handle(self, message):
                self.started.set()
                await self.release.wait()
                await self.outbox.result({"type": "validation_result", "job_id": message["job_id"], "status": "CANCELLED", "reason": "CANCELLED"})
            async def close(self):
                self.closed = True
        class Service:
            local_platform, max_parallel, boundary = LOCAL, 2, SimulatedBoundary()
            def connect(self, outbox):
                self.session = Session(outbox)
                return self.session
        service = Service()
        socket, _, task, hello = await self.start(service=service)
        self.assertEqual(hello["payload"]["max_workers"], 2)
        socket.put("cancel_job", "job-123", "CANCELLED", {})
        await service.session.started.wait()
        socket.put("ping", None, "CONNECTED", {"nonce": "abc"})
        await self.next_type(socket, "pong")
        service.session.release.set()
        self.assertEqual((await self.next_type(socket, "validation_result"))["state"], "CANCELLED")
        socket.incoming.put_nowait(None)
        await task
        self.assertTrue(service.session.closed)

    async def test_malformed_commands_and_heartbeat_force_cleanup(self):
        for kind, job_id, state, payload in (("ping", "bad", "CONNECTED", {"nonce": "a"}), ("ping", None, "CONNECTED", {"nonce": 1}),
                                          ("ping", None, "CONNECTED", {"nonce": "x" * 129}), ("unknown", "job-123", "VALIDATING", {}),
                                          ("execute_job", None, "EXECUTING", {}), ("validate_job", "job-123", "WRONG", {}),
                                          ("validate_job", "job-123", "VALIDATING", {"type": "other"})):
            with self.subTest(kind=kind, state=state):
                socket, service, task, _ = await self.start()
                socket.put(kind, job_id, state, payload)
                with self.assertRaises(ValueError):
                    await task

    async def test_cancellation_queue_is_bounded_and_pending_handlers_are_reaped(self):
        class Service:
            local_platform, max_parallel, boundary = LOCAL, 4, SimulatedBoundary()
            def connect(self, outbox):
                self.session = SimpleNamespace(handle=AsyncMock(side_effect=self.hold), close=AsyncMock())
                return self.session
            async def hold(self, message):
                await asyncio.Future()
        service = Service()
        socket, _, task, _ = await self.start(service=service)
        for n in range(5):
            socket.put("cancel_job", f"job-{n}", "CANCELLED", {})
        with self.assertRaisesRegex(ValueError, "Too many pending cancellations"):
            await task
        service.session.close.assert_awaited_once()

    async def test_handshake_failure_send_failure_and_cancellation_always_close_session(self):
        for phase in ("hello", "ready", "cancel"):
            with self.subTest(phase=phase):
                session = SimpleNamespace(close=AsyncMock())
                service = SimpleNamespace(local_platform=None, max_parallel=4, boundary=SimulatedBoundary(), connect=lambda _: session)
                socket = Socket()
                if phase == "hello":
                    socket.send = AsyncMock(side_effect=OSError("fixture"))
                    with self.assertRaises(OSError):
                        await connected_session(socket, "laptop", service)
                elif phase == "ready":
                    socket.put("wrong", None, "CONNECTED", {})
                    with self.assertRaises(ValueError):
                        await connected_session(socket, "laptop", service)
                else:
                    task = asyncio.create_task(connected_session(socket, "laptop", service))
                    await socket.outgoing.get()
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                session.close.assert_awaited_once()

    async def test_outgoing_write_error_cancels_receiver_and_invalidates_session(self):
        socket, service, task, _ = await self.start()
        socket.send = AsyncMock(side_effect=OSError("fixture broken pipe"))
        socket.put("ping", None, "CONNECTED", {"nonce": "abc"})
        with self.assertRaises(OSError):
            await task

    async def test_final_ack_racing_normal_socket_close_finishes_cleanup(self):
        socket, service, task, _ = await self.start()
        socket.send = AsyncMock(side_effect=ConnectionClosedOK(Close(1000, "fixture"), Close(1000, "fixture"), True))
        socket.put("ping", None, "CONNECTED", {"nonce": "abc"})
        await task

    async def test_repeated_disconnect_cancellation_waits_for_owned_cleanup(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def close():
            started.set()
            await release.wait()
        session = SimpleNamespace(close=AsyncMock(side_effect=close))
        service = SimpleNamespace(local_platform=LOCAL, max_parallel=4, boundary=SimulatedBoundary(), connect=lambda _: session)
        socket = Socket()
        socket.put("connection_ready", None, "CONNECTED", {"heartbeat_interval": 10})
        task = asyncio.create_task(connected_session(socket, "laptop", service))
        await socket.outgoing.get()
        socket.incoming.put_nowait(None)
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        session.close.assert_awaited_once()

    async def test_reconnection_has_bounded_jitter_and_authentication_heartbeat_limits(self):
        options = SimpleNamespace(url="wss://backend/ws/agents/laptop", device_id="laptop", workers=4)
        delays, bounds = [], []
        async def sleep(value):
            delays.append(value)
            if len(delays) == 7:
                raise asyncio.CancelledError
        def jitter(low, high):
            bounds.append((low, high))
            return high
        with patch("agent.transport.connect", side_effect=OSError("private-fixture")) as connection, patch("agent.transport.asyncio.sleep", side_effect=sleep), patch("agent.transport.random.uniform", side_effect=jitter), redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(asyncio.CancelledError):
                await run_connection(options, "secret-fixture-token", object())
        self.assertEqual(delays, [1, 2, 4, 8, 16, 30, 30])
        self.assertEqual(bounds[-1], (15, 30))
        kwargs = connection.call_args.kwargs
        self.assertEqual(kwargs["additional_headers"]["Authorization"], "Bearer secret-fixture-token")
        self.assertEqual((kwargs["ping_interval"], kwargs["ping_timeout"], kwargs["max_size"]), (20, 20, 1048576))
        self.assertNotIn("secret-fixture-token", stderr.getvalue())
        self.assertNotIn("private-fixture", stderr.getvalue())

    async def test_a_duplicate_device_rejection_ends_the_agent_instead_of_retrying_forever(self):
        options = SimpleNamespace(url="wss://backend/ws/agents/laptop", device_id="laptop", workers=4)
        refused = ConnectionClosed(Close(4409, "DEVICE_ALREADY_CONNECTED"), None)
        sleeps = []
        async def sleep(delay):
            sleeps.append(delay)
        with patch("agent.transport.connect", side_effect=refused) as connection, patch("agent.transport.asyncio.sleep", side_effect=sleep), redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "already connected"):
                await run_connection(options, "fixture", object())
        self.assertEqual((connection.call_count, sleeps), (1, []))

    async def test_other_close_codes_still_reconnect(self):
        options = SimpleNamespace(url="wss://backend/ws/agents/laptop", device_id="laptop", workers=4)
        async def sleep(delay):
            raise asyncio.CancelledError
        with patch("agent.transport.connect", side_effect=ConnectionClosed(Close(1008, ""), None)), patch("agent.transport.asyncio.sleep", side_effect=sleep), redirect_stderr(io.StringIO()):
            with self.assertRaises(asyncio.CancelledError):
                await run_connection(options, "fixture", object())

    async def test_successful_handshake_resets_backoff_and_normal_close_reconnects(self):
        class Context:
            async def __aenter__(self):
                return object()
            async def __aexit__(self, *args):
                return False
        options = SimpleNamespace(url="wss://backend/ws/agents/laptop", device_id="laptop", workers=4)
        seen = []
        async def session(*args, on_ready, native=None):
            on_ready()
        async def sleep(delay):
            seen.append(delay)
            if len(seen) == 3:
                raise asyncio.CancelledError
        with patch("agent.transport.connect", side_effect=[OSError(), Context(), Context()]), patch("agent.transport.connected_session", side_effect=session), patch("agent.transport.asyncio.sleep", side_effect=sleep), patch("agent.transport.random.uniform", side_effect=lambda low, high: high), redirect_stderr(io.StringIO()):
            with self.assertRaises(asyncio.CancelledError):
                await run_connection(options, "fixture", object())
        self.assertEqual(seen, [1, 1, 1])
