import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.bridge import Outbox
from agent.live_native import LiveNative, SampleOutbox
from agent.transport import connected_session, packet
from agent.jobs import ValidationService
from tests.support import COMPILER, LOCAL, SimulatedBoundary
from tests.test_bridge import process_fixture
from tests.test_transport import Socket


def options(screen=False):
    return SimpleNamespace(screen=screen, temperature_file="sensor.txt", min_cpu=2, min_ram_mb=50, screen_interval=500)


class LiveNativeTests(unittest.IsolatedAsyncioTestCase):
    def controls(self, outbox=None):
        native, outbox = LiveNative({"volume": "volume", "mouse": "mouse"}, options()), outbox or Outbox()
        for name in ("volume", "mouse"):
            native.queues[name] = asyncio.Queue(32)
            native.tools[name] = SimpleNamespace(command=AsyncMock(return_value={"type": "command_result", "stream": name, "ok": True}), close=AsyncMock())
            native.tasks.append(asyncio.create_task(native.worker(name, outbox)))
        return native, outbox

    async def test_all_tools_start_concurrently_and_close_without_orphans(self):
        native = LiveNative({name: name for name in ("volume", "mouse", "metrics", "processes", "screen")}, options(True))
        started, gate, processes = [], asyncio.Event(), {}
        async def spawn(binary, *args, **kwargs):
            started.append((binary, args))
            if len(started) == 5: gate.set()
            await asyncio.wait_for(gate.wait(), 1)
            processes[binary] = process_fixture()
            return processes[binary]
        with patch("agent.live_native.asyncio.create_subprocess_exec", side_effect=spawn): await native.start(Outbox())
        self.assertEqual(len(started), 5)
        self.assertIn(("screen", ("--interval", "500", "--max-width", "960", "--request-permission")), started)
        self.assertIn(("metrics", ("--temperature-file", "sensor.txt")), started)
        await native.close(); self.assertTrue(all(task.done() for task in native.tasks))
        self.assertTrue(all(process.returncode == 0 for process in processes.values()))

    async def test_screen_requires_local_opt_in_and_temperature_file_is_optional(self):
        native = LiveNative({"metrics": "metrics", "screen": "screen"}, options())
        native.options.temperature_file = None
        self.assertEqual(native.streams, ["metrics"])
        with patch("agent.live_native.asyncio.create_subprocess_exec", return_value=process_fixture()) as spawn: await native.start(Outbox())
        self.assertEqual(spawn.call_args.args, ("metrics",)); await native.close()

    async def test_ordered_control_workers_correlate_results_and_other_targets_run_in_parallel(self):
        native, outbox = LiveNative({}, options()), Outbox()
        for name in ("volume", "mouse"):
            native.queues[name] = asyncio.Queue(32)
            native.tools[name] = SimpleNamespace(command=AsyncMock(return_value={"type": "command_result", "stream": name, "ok": True}), close=AsyncMock())
            native.tasks.append(asyncio.create_task(native.worker(name, outbox)))
        native.command({"request_id": "v1", "target": "volume", "action": "set", "args": {"value": 50}})
        native.command({"request_id": "v2", "target": "volume", "action": "set", "args": {"value": 60}})
        native.command({"request_id": "m1", "target": "mouse", "action": "move", "args": {"x": 0, "y": 0}})
        results = [await asyncio.wait_for(outbox.get(), 1) for _ in range(3)]
        self.assertEqual({item["request_id"] for item in results}, {"v1", "v2", "m1"})
        self.assertEqual([call.args[0] for call in native.tools["volume"].command.await_args_list], ["set 50.000000\n", "set 60.000000\n"])
        await native.close()

    async def test_generated_and_frontend_commands_share_one_ordered_tool_queue(self):
        native, outbox = self.controls()
        started, release = asyncio.Event(), asyncio.Event()
        async def command(line):
            if line == "set 10.000000\n":
                started.set()
                await release.wait()
            return {"type": "command_result", "stream": "volume", "ok": True, "data": {"value": float(line.split()[1])}}
        native.tools["volume"].command.side_effect = command
        native.command({"request_id": "frontend", "target": "volume", "action": "set", "args": {"value": 10}})
        await started.wait()
        generated = asyncio.create_task(native.execute({"target": "volume", "action": "set", "args": {"value": 30}}))
        await asyncio.sleep(0)
        self.assertFalse(generated.done())
        release.set()
        result = await asyncio.wait_for(generated, 1)
        self.assertEqual(result["data"], {"value": 30})
        self.assertEqual((await outbox.get())["request_id"], "frontend")
        self.assertEqual([call.args[0] for call in native.tools["volume"].command.await_args_list], ["set 10.000000\n", "set 30.000000\n"])
        self.assertTrue(outbox.results.empty())
        self.assertEqual(native.snapshot("volume"), {"value": 30})
        await native.close()

    async def test_generated_mouse_runs_while_a_volume_acknowledgement_waits(self):
        native, _ = self.controls()
        waiting = asyncio.Event()
        async def volume(_):
            waiting.set()
            await asyncio.Event().wait()
        native.tools["volume"].command.side_effect = volume
        first = asyncio.create_task(native.execute({"target": "volume", "action": "set", "args": {"value": 30}}))
        await waiting.wait()
        result = await asyncio.wait_for(native.execute({"target": "mouse", "action": "scroll", "args": {"amount": 1}}), 1)
        self.assertTrue(result["ok"])
        self.assertFalse(first.done())
        await native.close()
        with self.assertRaises(asyncio.CancelledError):
            await first

    async def test_cancelled_queued_generated_command_never_reaches_the_tool(self):
        native = LiveNative({"volume": "volume"}, options())
        native.queues["volume"] = asyncio.Queue(32)
        native.tools["volume"] = SimpleNamespace(command=AsyncMock(return_value={"type": "command_result", "stream": "volume", "ok": True}), close=AsyncMock())
        pending = asyncio.create_task(native.execute({"target": "volume", "action": "set", "args": {"value": 70}}))
        await asyncio.sleep(0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        outbox = Outbox()
        native.tasks.append(asyncio.create_task(native.worker("volume", outbox)))
        native.command({"request_id": "next", "target": "volume", "action": "set", "args": {"value": 20}})
        self.assertEqual((await asyncio.wait_for(outbox.get(), 1))["request_id"], "next")
        native.tools["volume"].command.assert_awaited_once_with("set 20.000000\n")
        await native.close()

    async def test_inflight_cancellation_drains_the_ack_before_next_frontend_command(self):
        native, outbox = self.controls()
        started, release = asyncio.Event(), asyncio.Event()
        async def command(line):
            if line == "set 70.000000\n":
                started.set()
                await release.wait()
            return {"type": "command_result", "stream": "volume", "ok": True}
        native.tools["volume"].command.side_effect = command
        pending = asyncio.create_task(native.execute({"target": "volume", "action": "set", "args": {"value": 70}}))
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        native.command({"request_id": "next", "target": "volume", "action": "set", "args": {"value": 20}})
        await asyncio.sleep(0)
        self.assertEqual(native.tools["volume"].command.await_count, 1)
        release.set()
        self.assertEqual((await asyncio.wait_for(outbox.get(), 1))["request_id"], "next")
        self.assertEqual(native.tools["volume"].command.await_count, 2)
        await native.close()

    async def test_close_cancels_active_and_queued_generated_acks_without_late_actions(self):
        native, _ = self.controls()
        started = asyncio.Event()
        async def blocked(_):
            started.set()
            await asyncio.Event().wait()
        native.tools["volume"].command.side_effect = blocked
        first = asyncio.create_task(native.execute({"target": "volume", "action": "set", "args": {"value": 70}}))
        await started.wait()
        queued = asyncio.create_task(native.execute({"target": "volume", "action": "set", "args": {"value": 20}}))
        await asyncio.sleep(0)
        await native.close()
        for pending in (first, queued):
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertEqual(native.tools["volume"].command.await_count, 1)
        self.assertTrue(native.queues["volume"].empty())
        self.assertFalse(native.control_available("volume"))

    async def test_generated_ack_failure_is_owned_and_closes_the_worker(self):
        native, _ = self.controls()
        native.tools["volume"].command.side_effect = RuntimeError("missing acknowledgement")
        with self.assertRaisesRegex(RuntimeError, "missing acknowledgement"):
            await native.execute({"target": "volume", "action": "set", "args": {"value": 20}})
        self.assertIsInstance(native.tasks[0].exception(), RuntimeError)
        await native.close()

    async def test_frontend_worker_failure_is_observed_and_other_tools_close(self):
        native, _ = self.controls()
        native.tools["volume"].command.side_effect = RuntimeError("missing acknowledgement")
        native.command({"request_id": "frontend", "target": "volume", "action": "set", "args": {"value": 20}})
        with self.assertRaisesRegex(RuntimeError, "missing acknowledgement"):
            await native.tasks[0]
        await native.close()
        self.assertTrue(all(task.done() for task in native.tasks))

    async def test_generated_commands_reject_extra_fields_disabled_tools_and_full_queues(self):
        native = LiveNative({"volume": "volume"}, options())
        valid = {"target": "volume", "action": "set", "args": {"value": 20}}
        with self.assertRaises(ValueError):
            await native.execute({**valid, "request_id": "remote"})
        with self.assertRaises(ValueError):
            await native.execute(valid)
        native.queues["volume"] = asyncio.Queue(1)
        native.tools["volume"] = SimpleNamespace(close=AsyncMock())
        native.queues["volume"].put_nowait(("frontend", "set 20\n"))
        with self.assertRaisesRegex(ValueError, "full"):
            await native.execute(valid)
        await native.close()

    def test_telemetry_tee_retains_copied_snapshots_and_keeps_live_events(self):
        native, outbox = LiveNative({}, options()), Outbox()
        tee = SampleOutbox(native, outbox)
        event = {"type": "telemetry", "stream": "volume", "data": {"value": 20}}
        tee.sample(event)
        self.assertIs(outbox.samples["volume"], event)
        event["data"]["value"] = 30
        snapshot = native.snapshot("volume")
        self.assertEqual(snapshot, {"value": 20})
        snapshot["value"] = 40
        self.assertEqual(native.snapshot("volume"), {"value": 20})
        tee.sample({"type": "error", "stream": "volume", "error": "permission denied"})
        self.assertIsNone(native.snapshot("volume"))
        self.assertEqual(outbox.samples["volume"]["type"], "error")

    async def test_command_validation_disabled_targets_and_full_queues(self):
        native = LiveNative({}, options()); native.queues["volume"] = asyncio.Queue(1)
        valid = {"request_id": "r", "target": "volume", "action": "set", "args": {"value": 20}}
        for payload in ({**valid, "extra": 1}, {**valid, "request_id": ""}, {**valid, "request_id": None}, {**valid, "args": {"value": True}}, {**valid, "target": "mouse", "action": "move", "args": {"x": 0, "y": 0}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError): native.command(payload)
        native.command(valid)
        with self.assertRaisesRegex(ValueError, "full"): native.command(valid)

    async def test_partial_start_failure_still_closes_successful_processes(self):
        native = LiveNative({"volume": "volume", "mouse": "mouse"}, options())
        process = process_fixture()
        async def spawn(binary, *args, **kwargs):
            if binary == "mouse": raise OSError("spawn failed")
            return process
        with patch("agent.live_native.asyncio.create_subprocess_exec", side_effect=spawn), self.assertRaises(OSError): await native.start(Outbox())
        await native.close(); self.assertEqual(process.returncode, 0)

    async def test_cancellation_waits_for_all_in_flight_spawns_before_closing(self):
        native = LiveNative({"metrics": "metrics"}, options()); begun, release = asyncio.Event(), asyncio.Event(); process = process_fixture()
        async def spawn(*args, **kwargs): begun.set(); await release.wait(); return process
        with patch("agent.live_native.asyncio.create_subprocess_exec", side_effect=spawn):
            task = asyncio.create_task(native.start(Outbox())); await begun.wait(); task.cancel(); release.set()
            with self.assertRaises(asyncio.CancelledError): await task
        await native.close(); self.assertEqual(process.returncode, 0)

    async def test_protocol_carries_native_telemetry_commands_and_heartbeats_without_generated_jobs(self):
        class Fixture:
            streams, tasks = ["volume"], []
            async def start(self, outbox): self.outbox = outbox; outbox.sample({"type": "telemetry", "stream": "volume", "data": {"value": 25}})
            def command(self, payload): self.received = payload
            async def close(self): self.closed = True
        native, socket = Fixture(), Socket()
        socket.incoming.put_nowait(json.dumps(packet("laptop", "connection_ready", None, "CONNECTED", {"heartbeat_interval": 1})))
        ready = asyncio.Event(); service = ValidationService(COMPILER, {}, SimulatedBoundary(), local_platform=LOCAL)
        task = asyncio.create_task(connected_session(socket, "laptop", service, native=native, on_ready=ready.set)); await ready.wait()
        hello = await socket.outgoing.get(); self.assertEqual(hello["payload"]["native_streams"], ["volume"])
        frame = await socket.outgoing.get(); self.assertEqual(frame["type"], "native_event")
        command = {"request_id": "r", "target": "volume", "action": "set", "args": {"value": 20}}
        socket.put("native_command", None, "CONNECTED", command); socket.put("ping", None, "CONNECTED", {"nonce": "n"})
        pong = await asyncio.wait_for(socket.outgoing.get(), 1); self.assertEqual(pong["type"], "pong"); self.assertEqual(native.received, command)
        socket.incoming.put_nowait(None); await task; self.assertTrue(native.closed)

    async def test_invalid_native_command_is_rejected_with_correlated_error(self):
        class Fixture:
            streams, tasks = ["volume"], []
            async def start(self, outbox): pass
            def command(self, payload): raise ValueError("disabled")
            async def close(self): pass
        socket = Socket(); socket.incoming.put_nowait(json.dumps(packet("laptop", "connection_ready", None, "CONNECTED", {"heartbeat_interval": 1})))
        service = ValidationService(COMPILER, {}, SimulatedBoundary(), local_platform=LOCAL); ready = asyncio.Event()
        task = asyncio.create_task(connected_session(socket, "laptop", service, native=Fixture(), on_ready=ready.set)); await ready.wait(); await socket.outgoing.get()
        socket.put("native_command", None, "CONNECTED", {"request_id": "r", "target": "volume"})
        result = await asyncio.wait_for(socket.outgoing.get(), 1); self.assertEqual(result["payload"]["request_id"], "r"); self.assertFalse(result["payload"]["ok"])
        socket.incoming.put_nowait(None); await task
