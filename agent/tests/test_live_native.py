import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.bridge import Outbox
from agent.live_native import LiveNative
from agent.transport import connected_session, packet
from agent.jobs import ValidationService
from tests.support import COMPILER, LOCAL, SimulatedBoundary
from tests.test_bridge import process_fixture
from tests.test_transport import Socket


def options(screen=False):
    return SimpleNamespace(screen=screen, temperature_file="sensor.txt", min_cpu=2, min_ram_mb=50, screen_interval=500)


class LiveNativeTests(unittest.IsolatedAsyncioTestCase):
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
