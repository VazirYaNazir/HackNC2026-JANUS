import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from websockets.asyncio.client import connect

from app import db
from app.agent_runtime.config import Identity
from app.agent_runtime.connections import PipelineError
from app.agent_runtime.events import PanelEvents
from app.agent_runtime.native import NativeManager, number, readings
from app.agent_runtime.store import SQLiteStore
from agent.jobs import ValidationService
from tests.native_fixture import NativeFixture
from tests.support import RunningServer, TrustedFixtureBoundary, headers, registry


def event(stream, data=None, **extra):
    return {"type": "telemetry", "stream": stream, "data": data or {}, **extra}


class NativeReadingTests(unittest.TestCase):
    def test_metrics_volume_and_unavailable_sensors(self):
        self.assertEqual(readings(event("volume", {"value": 75}))["volume.percent"], 75)
        values = readings(event("metrics", {"cpu_percent": 30, "ram": {"percent": 20, "used_bytes": 1048576}, "cpu_temperature_c": 55}))
        self.assertEqual([values[key] for key in ("cpu.percent", "mem.percent", "mem.used_mb", "cpu.temperature")], [30, 20, 1, 55])
        values = readings(event("metrics"))
        self.assertTrue(all(values[key] is None for key in ("cpu.percent", "mem.percent", "mem.used_mb", "cpu.temperature")))
        self.assertEqual(readings(event("mouse")), {"native.mouse.status": "Live"})

    def test_error_clears_old_readings_and_validates_bounded_messages(self):
        for stream, source in (("screen", "screen.frame"), ("processes", "process.tree"), ("metrics", "cpu.temperature"), ("volume", "volume.percent")):
            values = readings(event(stream, type="error", error="Permission denied"))
            self.assertIsNone(values[source]); self.assertEqual(values[f"native.{stream}.status"], "Permission denied")
        self.assertEqual(readings(event("mouse", type="error", error="No accessibility permission")), {"native.mouse.status": "No accessibility permission"})
        for payload in (event("unknown"), event("volume", type="unexpected"), {"stream": "volume", "type": "telemetry", "data": []}, event("mouse", type="error", error=2), event("mouse", type="error", error="x"*2049)):
            with self.subTest(payload=payload), self.assertRaises(ValueError): readings(payload)

    def test_rejects_nonfinite_boolean_out_of_range_and_incomplete_metrics(self):
        for value in (True, None, [], "1", float("nan"), float("inf"), -1, 101, 10**1000):
            with self.subTest(value=str(value)[:40]), self.assertRaises(ValueError): number(value, 0, 100)
        for data in ({"ram": {}}, {"ram": {"percent": 101, "used_bytes": 0}}, {"cpu_temperature_c": 1000}, {"cpu_percent": -1}):
            with self.subTest(data=data), self.assertRaises(ValueError): readings(event("metrics", data))

    def test_screen_accepts_only_bounded_jpeg_frames(self):
        encoded = base64.b64encode(b"\xff\xd8fixture\xff\xd9").decode()
        self.assertEqual(readings(event("screen", {"mime": "image/jpeg", "base64": encoded}))["screen.frame"], "data:image/jpeg;base64," + encoded)
        for data in ({"mime": "text/html", "base64": encoded}, {"mime": "image/jpeg", "base64": "bad"}, {"mime": "image/jpeg", "base64": base64.b64encode(b"bad").decode()}, {"mime": "image/jpeg", "base64": "x"*800001}):
            with self.subTest(data=str(data)[:60]), self.assertRaises(ValueError): readings(event("screen", data))

    def test_tree_keeps_relevant_apps_and_ancestors_and_bounds_cycles(self):
        root = {"pid": 1, "parent_pid": None, "name": "Parent", "cpu_percent": None, "ram_bytes": None, "context": True}
        child = {**root, "pid": 2, "parent_pid": 1, "name": "Busy", "cpu_percent": 45, "context": False}
        ignored = {**root, "pid": 3}
        rows = json.loads(readings(event("processes", {"processes": [root, child, ignored]}))["process.tree"])
        self.assertEqual({p["pid"] for p in rows}, {1, 2})
        rows = json.loads(readings(event("processes", {"processes": [{**root, "parent_pid": 2}, child]}))["process.tree"])
        self.assertEqual(len(rows), 2)
        many = [{**child, "pid": n+10, "parent_pid": None} for n in range(1000)]
        self.assertEqual(len(json.loads(readings(event("processes", {"processes": many}))["process.tree"])), 128)
        for entries in (None, [{}], [root, root], [{**root, "pid": 1.5}], [{**root, "parent_pid": "2"}], [{**root, "name": None}], [{**root, "context": None}], [{**root, "cpu_percent": -1}], [False], [root]*16385):
            with self.subTest(entries=str(entries)[:100]), self.assertRaises(ValueError): readings(event("processes", {"processes": entries}))


class NativeManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class Connection:
            identity = Identity("device", "owner")
            hello = SimpleNamespace(native_streams=["volume", "mouse"])
        self.connection = Connection()
        self.events = PanelEvents()
        self.hub = SimpleNamespace(get=lambda device: self.connection)
        self.manager = NativeManager(self.hub, self.events)

    def frame(self, payload, **extra):
        return SimpleNamespace(job_id=None, state="CONNECTED", payload=payload, **extra)

    async def test_dispatches_all_fixed_commands_with_correlated_acknowledgements(self):
        async def send(kind, job, state, payload):
            data = {"value": payload["args"]["value"]} if payload["target"] == "volume" else {}
            await self.manager.receive(self.connection, self.frame({"type": "command_result", "stream": payload["target"], "request_id": payload["request_id"], "ok": True, "data": data}))
        self.connection.send = AsyncMock(side_effect=send)
        for action, value in (("native_volume", 35), ("native_mouse", {"x": 0, "y": 0}), ("native_scroll", 2), ("native_left_click", None), ("native_right_click", None)):
            result = await self.manager.command(self.connection.identity, action, value); self.assertTrue(result["ok"])
        self.assertEqual(self.connection.send.await_count, 7)
        self.assertEqual(self.manager.pending, {})
        self.assertEqual(self.manager.latest[self.connection.identity]["volume.percent"], 35)

    async def test_errors_timeout_capacity_and_disabled_tools_cleanup_pending(self):
        async def fail(kind, job, state, payload):
            await self.manager.receive(self.connection, self.frame({"type": "command_result", "stream": "volume", "request_id": payload["request_id"], "ok": False}))
        self.connection.send = AsyncMock(side_effect=fail)
        with self.assertRaisesRegex(PipelineError, "NATIVE_COMMAND_FAILED"): await self.manager.command(self.connection.identity, "native_volume", 1)
        self.connection.send = AsyncMock()
        with patch("app.agent_runtime.native.asyncio.wait_for", side_effect=TimeoutError), self.assertRaisesRegex(PipelineError, "TIMEOUT"):
            await self.manager.command(self.connection.identity, "native_volume", 1)
        self.assertEqual(self.manager.pending, {})
        self.manager.pending = {(self.connection, str(n)): asyncio.get_running_loop().create_future() for n in range(32)}
        with self.assertRaisesRegex(PipelineError, "CAPACITY"): await self.manager.command(self.connection.identity, "native_volume", 1)
        self.manager.pending.clear(); self.connection.hello = SimpleNamespace(native_streams=[])
        with self.assertRaisesRegex(PipelineError, "NOT_ENABLED"): await self.manager.command(self.connection.identity, "native_volume", 1)
        with self.assertRaisesRegex(PipelineError, "NOT_FOUND"): await self.manager.command(self.connection.identity, "shell", None)

    async def test_enforces_scope_clears_cache_and_fails_pending_on_disconnect(self):
        subscriber = self.events.subscribe(self.connection.identity)
        other = self.events.subscribe(Identity("other", "owner"))
        await self.manager.receive(self.connection, self.frame(event("volume", {"value": 30})))
        self.assertEqual(subscriber.queue.qsize(), 2); self.assertTrue(other.queue.empty())
        for frame in (SimpleNamespace(job_id="job", state="CONNECTED", payload={}), SimpleNamespace(job_id=None, state="WRONG", payload={}), self.frame(event("screen")), self.frame(event("volume", type="command_result", ok="yes"))):
            with self.assertRaises(ValueError): await self.manager.receive(self.connection, frame)
        future = asyncio.get_running_loop().create_future(); self.manager.pending[(self.connection, "r")] = future
        self.manager.disconnected(self.connection)
        with self.assertRaisesRegex(PipelineError, "DISCONNECTED"): await future
        self.assertEqual(self.manager.latest, {})


class NativeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.patch = patch.object(db, "DB_PATH", Path(self.folder.name) / "app.db"); self.patch.start()
        store = SQLiteStore(); await store.initialize(registry()); self.backend = await RunningServer().start(store=store)
        self.native = NativeFixture(); boundary = TrustedFixtureBoundary()
        self.socket, _ = await self.backend.agent(ValidationService(boundary.compiler, {}, boundary, local_platform=("Darwin", "x86_64")), native=self.native)
        self.layout = {"schemaVersion": 2, "coordinateSpace": "logical", "canvas": {"width": 360, "height": 780}, "name": "Native", "widgets": [
            {"id": "volume", "type": "slider", "label": "Volume", "action": "native_volume", "x": 0, "y": 0, "w": 170, "h": 70},
            {"id": "mouse", "type": "joystick", "label": "Mouse", "action": "native_mouse", "x": 0, "y": 82, "w": 170, "h": 170}]}
        response = await self.backend.http.put("/api/v1/studio/layout", headers=headers(), json={"layout": self.layout, "revision": 0}); self.assertEqual(response.status_code, 200)

    async def asyncTearDown(self):
        await self.backend.close(); self.patch.stop(); self.folder.cleanup()

    async def test_phone_and_studio_receive_same_telemetry_and_native_commands_bypass_gemini(self):
        code = (await self.backend.http.post("/api/v1/studio/pairing", headers=headers())).json()["code"]
        phone = (await self.backend.http.post("/api/pair", json={"code": code, "name": "Phone"})).json()
        for token in (phone["token"], headers()["Authorization"][7:]):
            async with connect(f"ws://127.0.0.1:{self.backend.port}/ws") as browser:
                await browser.send(json.dumps({"type": "auth", "token": token})); self.assertEqual(json.loads(await browser.recv())["type"], "auth_ok")
                values = {}
                async with asyncio.timeout(2):
                    while "process.tree" not in values:
                        message = json.loads(await browser.recv()); values[message["source"]] = message["value"]
                self.assertEqual(values["mem.percent"], 42); self.assertEqual(values["cpu.temperature"], 55)
        result = await self.backend.http.post("/api/v1/native/commands", headers={"Authorization": "Bearer " + phone["token"]}, json={"widget_id": "volume", "job_id": "native", "value": 67})
        self.assertEqual(result.status_code, 200); self.assertTrue(result.json()["ok"])
        self.assertEqual(self.native.commands[-1]["args"], {"value": 67}); self.assertEqual(self.backend.sdk.calls, [])

    async def test_rejects_other_laptop_bad_values_bad_bindings_and_offline_agent(self):
        for payload, auth, expected in (({"widget_id": "volume", "job_id": "r", "value": 50}, headers(2), 404), ({"widget_id": "volume", "job_id": "r", "value": 101}, headers(), 422), ({"widget_id": "volume", "job_id": "r", "value": True}, headers(), 422), ({"widget_id": "mouse", "job_id": "r", "value": {"x": 2, "y": 0}}, headers(), 422), ({"widget_id": "absent", "job_id": "r"}, headers(), 404)):
            response = await self.backend.http.post("/api/v1/native/commands", headers=auth, json=payload); self.assertEqual(response.status_code, expected)
        self.assertEqual(self.native.commands, [])
        await self.socket.close()
        for _ in range(100):
            if not self.backend.runtime.hub.connections: break
            await asyncio.sleep(.01)
        response = await self.backend.http.post("/api/v1/native/commands", headers=headers(), json={"widget_id": "volume", "job_id": "r", "value": 30})
        self.assertEqual(response.status_code, 503); self.assertEqual(self.backend.runtime.native.latest, {})
