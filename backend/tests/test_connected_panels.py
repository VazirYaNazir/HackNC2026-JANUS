import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from tests.support import RunningServer, TrustedFixtureBoundary, headers, program, registry, request
from agent.compiler import CCompiler
from agent.jobs import ValidationService
from agent.transport import packet
from agent.validation import ExecutionPolicy, Fixture
from app import db
from app.agent_runtime.config import Identity, RuntimeSettings
from app.agent_runtime.connections import PipelineError
from app.agent_runtime.events import PanelEvents
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.store import SQLiteStore
from app.panels import PanelLayout, checked_value


def layout(kind="button"):
    return {"schemaVersion": 2, "coordinateSpace": "logical", "canvas": {"width": 360, "height": 780}, "name": "Live desk",
            "widgets": [{"id": "control", "type": kind, "label": "Compute", "action": "compute", "x": 0, "y": 0, "w": 168, "h": 62}]}


class ConnectedPanelsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="janus-panel-db-")
        self.path = patch.object(db, "DB_PATH", Path(self.folder.name) / "app.db")
        self.path.start()
        store = SQLiteStore()
        await store.initialize(registry())
        self.backend = await RunningServer().start(store=store)
        self.browsers, self.boundaries = [], []
        self.identity = Identity("device-1", "owner-1")

    async def asyncTearDown(self):
        for socket in self.browsers:
            await socket.close()
        await self.backend.close()
        for boundary in self.boundaries:
            self.assertTrue(all(not path.exists() for path in boundary.directories))
        self.path.stop(); self.folder.cleanup()

    async def configured(self, kind="button"):
        saved = await self.backend.http.put("/api/v1/studio/layout", headers=headers(), json={"layout": layout(kind), "revision": 0})
        self.assertEqual(saved.status_code, 200, saved.text)
        configured = await self.backend.http.put("/api/v1/studio/actions/compute", headers=headers(), json=request().model_dump())
        self.assertEqual(configured.status_code, 200)

    async def phone(self):
        code = (await self.backend.http.post("/api/v1/studio/pairing", headers=headers())).json()["code"]
        response = await self.backend.http.post("/api/pair", json={"code": code, "name": "Test phone"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), {"Authorization": "Bearer " + response.json()["token"]}

    async def test_pairing_advertises_tunnel_public_origin_and_optional_phone_app(self):
        with patch.dict("os.environ", {"PUBLIC_BACKEND_URL": "", "PWA_PUBLIC_URL": ""}), patch("app.routes.studio.get_tunnel_url", return_value="https://active.trycloudflare.com"):
            response = await self.backend.http.post("/api/v1/studio/pairing", headers=headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["host"], "https://active.trycloudflare.com")
        self.assertEqual(len(response.json()["code"]), 12)
        self.assertEqual(response.json()["expires_in_seconds"], 60)
        self.assertIsNone(response.json()["pwa_url"])
        with patch.dict("os.environ", {"PUBLIC_BACKEND_URL": "https://backend.example/", "PWA_PUBLIC_URL": "https://phone.example/"}):
            configured = (await self.backend.http.post("/api/v1/studio/pairing", headers=headers())).json()
        self.assertEqual(configured["host"], "https://backend.example")
        self.assertEqual(configured["pwa_url"], "https://phone.example")
        self.assertNotIn("token", configured)

    async def test_pairing_keeps_https_request_origin_and_has_a_local_development_fallback(self):
        # Exercise the route through ASGI so its scheme is really HTTPS, without a TLS fixture server.
        import httpx
        with patch.dict("os.environ", {"PUBLIC_BACKEND_URL": "", "PWA_PUBLIC_URL": ""}), patch("app.routes.studio.get_tunnel_url", return_value="https://other.trycloudflare.com"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.backend.app), base_url="https://connected.example") as client:
                response = await client.post("/api/v1/studio/pairing", headers=headers())
            self.assertEqual(response.json()["host"], "https://connected.example")
        with patch.dict("os.environ", {"PUBLIC_BACKEND_URL": "", "PWA_PUBLIC_URL": ""}), patch("app.routes.studio.get_tunnel_url", return_value=None):
            fallback = (await self.backend.http.post("/api/v1/studio/pairing", headers=headers())).json()
        self.assertEqual(fallback["host"], f"http://127.0.0.1:{self.backend.port}")

    async def test_pairing_rejects_invalid_public_configuration_before_minting_a_code(self):
        for name in ("PUBLIC_BACKEND_URL", "PWA_PUBLIC_URL"):
            for value in ("http://remote.example", "https://user:secret@host.example", "https://host.example/path", "https://host.example?token=secret", "https://host.example#fragment", "https://host.example:99999", "https://bad host.example", "https://[bad"):
                with self.subTest(name=name, value=value), patch.dict("os.environ", {"PUBLIC_BACKEND_URL": "", "PWA_PUBLIC_URL": "", name: value}):
                    response = await self.backend.http.post("/api/v1/studio/pairing", headers=headers())
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["detail"], f"INVALID_{name}")
        with db.get_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM panel_pairing").fetchone()[0], 0)

    async def test_launcher_origins_are_dynamic_and_unavailable_backend_does_not_mint_code(self):
        origins = Path(self.folder.name) / "public-urls.json"
        environment = {"PUBLIC_BACKEND_URL": "", "PWA_PUBLIC_URL": "", "JANUS_PUBLIC_URLS_FILE": str(origins)}
        with patch.dict("os.environ", environment):
            for content in (None, {"backend": None, "pwa": None}):
                if content is not None:
                    origins.write_text(json.dumps(content))
                response = await self.backend.http.post("/api/v1/studio/pairing", headers=headers())
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["detail"], "PUBLIC_BACKEND_UNAVAILABLE")
            with db.get_db() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM panel_pairing").fetchone()[0], 0)
            for prefix in ("initial", "replacement"):
                document = {"backend": f"https://{prefix}.trycloudflare.com", "pwa": f"https://{prefix}-phone.trycloudflare.com"}
                temporary = origins.with_suffix(".tmp")
                temporary.write_text(json.dumps(document))
                temporary.replace(origins)
                response = await self.backend.http.post("/api/v1/studio/pairing", headers=headers())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["host"], document["backend"])
                self.assertEqual(response.json()["pwa_url"], document["pwa"])
                self.assertEqual(response.json()["status"], "ready")
            with patch.dict("os.environ", {"PUBLIC_BACKEND_URL": "https://stable.example", "PWA_PUBLIC_URL": "https://stable-phone.example"}):
                response = await self.backend.http.post("/api/v1/studio/pairing", headers=headers())
                self.assertEqual(response.json()["host"], "https://stable.example")
                self.assertEqual(response.json()["pwa_url"], "https://stable-phone.example")

    async def test_launcher_origin_file_rejects_unsafe_values_without_echoing_them(self):
        origins = Path(self.folder.name) / "public-urls.json"
        environment = {"PUBLIC_BACKEND_URL": "", "PWA_PUBLIC_URL": "", "JANUS_PUBLIC_URLS_FILE": str(origins)}
        for document in ('private secret', json.dumps({"backend": "http://remote.example", "pwa": None}), json.dumps({"backend": "https://user:private@host.example", "pwa": None}), json.dumps({"backend": None, "pwa": 42}), json.dumps({"backend": None}), 'x' * 4097):
            origins.write_text(document)
            with self.subTest(document_type=type(document)), patch.dict("os.environ", environment):
                response = await self.backend.http.post("/api/v1/studio/pairing", headers=headers())
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["detail"], "INVALID_JANUS_PUBLIC_URLS_FILE")
        with db.get_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM panel_pairing").fetchone()[0], 0)

    async def browser(self, token):
        socket = await connect(f"ws://127.0.0.1:{self.backend.port}/ws")
        await socket.send(json.dumps({"type": "auth", "token": token}))
        self.assertEqual(json.loads(await socket.recv())["type"], "auth_ok")
        self.browsers.append(socket)
        return socket

    async def laptop(self, ids, *, share=True):
        boundary = TrustedFixtureBoundary()
        self.boundaries.append(boundary)
        policies = {job_id: ExecutionPolicy(job_id, "compute", execute_authorized=True, share_outputs=share,
                    side_effects=False, output_protocol="json-v1", fixtures=(Fixture(stdout='{"version":1,"value":42}\n'),)) for job_id in ids}
        await self.backend.agent(ValidationService(boundary.compiler, policies, boundary, local_platform=("Darwin", "x86_64")))

    async def message(self, socket, kind, state=None):
        async with asyncio.timeout(3):
            while True:
                message = json.loads(await socket.recv())
                if message["type"] == kind and (state is None or message.get("job", {}).get("state") == state):
                    return message

    async def test_studio_save_pair_pwa_dispatch_live_result_and_database_history(self):
        desktop = await self.browser(headers()["Authorization"].removeprefix("Bearer "))
        foreign = await self.browser(headers(2)["Authorization"].removeprefix("Bearer "))
        await self.configured()
        self.assertEqual((await self.message(desktop, "layout_updated"))["type"], "layout_updated")
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(foreign.recv(), .05)
        paired, phone_headers = await self.phone()
        document = (await self.backend.http.get("/api/layout", headers=phone_headers)).json()
        self.assertEqual(document["layout"]["name"], "Live desk")
        self.assertNotIn("source", json.dumps(document))
        phone = await self.browser(paired["token"])
        await self.laptop(["phone-job"])
        self.assertTrue((await self.message(phone, "agent_presence"))["session"]["online"])
        submitted = await self.backend.http.post("/api/v1/panel-actions/dispatch", headers=phone_headers, json={"widget_id": "control", "job_id": "phone-job"})
        self.assertEqual(submitted.status_code, 202, submitted.text)
        result = (await self.message(phone, "job_update", "COMPLETED"))["job"]
        self.assertEqual(json.loads(result["result"]["stdout"])["value"], 42)
        self.assertEqual(result["widget_id"], "control")
        self.assertTrue(result["program_id"])
        history = (await self.backend.http.get("/api/v1/agent-jobs", headers=phone_headers)).json()["jobs"]
        self.assertEqual(history[0]["state"], "COMPLETED")
        self.assertEqual((await self.backend.http.get("/api/v1/agent-jobs/phone-job", headers=headers(2))).status_code, 404)
        for endpoint, body in (("/api/v1/studio/layout", {"layout": layout(), "revision": 1}), ("/api/v1/studio/actions/compute", request().model_dump())):
            self.assertEqual((await self.backend.http.put(endpoint, headers=phone_headers, json=body)).status_code, 401)

    async def test_reuse_is_explicit_fresh_validated_execution_without_another_generation(self):
        await self.configured(); await self.laptop(["first", "reuse"])
        payload = {"widget_id": "control", "job_id": "first"}
        self.assertEqual((await self.backend.http.post("/api/v1/panel-actions/dispatch", headers=headers(), json=payload)).status_code, 202)
        first = await self.backend.result("first")
        prepared = (await self.backend.http.post("/api/v1/panel-actions/prepare", headers=headers(), json={**payload, "job_id": "reuse"})).json()
        self.assertEqual(prepared["matches"][0]["match"], "exact")
        self.assertNotIn("reuse", self.backend.runtime.jobs.records)
        self.assertEqual(len(self.backend.sdk.calls), 1)
        reuse = await self.backend.http.post("/api/v1/panel-actions/dispatch", headers=headers(), json={**payload, "job_id": "reuse", "program_id": first["program_id"]})
        self.assertEqual(reuse.status_code, 202, reuse.text)
        self.assertEqual((await self.backend.result("reuse"))["state"], "COMPLETED")
        self.assertEqual(len(self.backend.sdk.calls), 1)
        self.assertEqual(len(self.boundaries[0].directories), 2)
        duplicate = await self.backend.http.post("/api/v1/panel-actions/dispatch", headers=headers(), json=payload)
        self.assertEqual(duplicate.status_code, 409)

    async def test_offline_source_draft_reuse_still_requires_local_grant_and_isolation(self):
        await self.configured()
        draft = await self.backend.http.post("/api/v1/programs/generate", headers=headers(), json=request().model_dump())
        self.assertEqual(draft.status_code, 201, draft.text)
        self.assertFalse(self.backend.runtime.hub.connections)
        self.assertFalse(self.backend.runtime.jobs.records)
        identifier = draft.json()["program_id"]
        matches = (await self.backend.http.post("/api/v1/programs/similar", headers=headers(), json=request().model_dump())).json()["matches"]
        self.assertEqual(matches[0]["id"], identifier)
        service = ValidationService(CCompiler("clang", "/never-invoked", "Darwin"),
                                    {"draft-blocked": ExecutionPolicy("draft-blocked", "compute", execute_authorized=True,
                                                                     output_protocol="json-v1")},
                                    local_platform=("Darwin", "x86_64"))
        await self.backend.agent(service)
        with patch.object(CCompiler, "compile_detailed", side_effect=AssertionError("No local boundary")) as compile_source, patch("agent.jobs.execute_artifact", AsyncMock(side_effect=AssertionError("Draft cannot execute"))) as execute:
            for job_id, expected in (("draft-unapproved", ("REJECTED", "AUTHORIZATION_REQUIRED")),
                                     ("draft-blocked", ("VALIDATION_BLOCKED", "BUILD_BOUNDARY_UNAVAILABLE"))):
                submitted = await self.backend.http.post("/api/v1/panel-actions/dispatch", headers=headers(),
                    json={"widget_id": "control", "job_id": job_id, "program_id": identifier})
                self.assertEqual(submitted.status_code, 202, submitted.text)
                result = await self.backend.result(job_id)
                self.assertEqual((result["state"], result["reason"]), expected)
            compile_source.assert_not_called()
            execute.assert_not_awaited()
        self.assertEqual(len(self.backend.sdk.calls), 1)

    async def test_persisted_jobs_and_devices_remain_queryable_after_backend_restart(self):
        await self.configured(); await self.laptop(["survives"])
        await self.backend.generate("survives")
        await self.backend.result("survives")
        await self.backend.close()
        store = SQLiteStore(); loaded = await store.initialize(registry())
        self.assertEqual(loaded.controller(headers()["Authorization"].removeprefix("Bearer ")), self.identity)
        self.backend = await RunningServer().start(store=store)
        view = await self.backend.result("survives")
        self.assertEqual(view["state"], "COMPLETED")
        self.assertEqual(len(self.backend.sdk.calls), 0)
        self.assertEqual((await self.backend.http.post("/api/v1/agent-jobs/survives/cancel", headers=headers())).json()["state"], "COMPLETED")
        await self.laptop(["survives"])
        duplicate = await self.backend.generate("survives")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(len(self.backend.sdk.calls), 0)

    async def test_phone_revocation_expired_pairing_and_compatibility_aliases(self):
        await self.configured()
        paired, phone_headers = await self.phone()
        socket = await self.browser(paired["token"])
        self.assertEqual((await self.backend.http.get("/api/v1/panels/widgets", headers=phone_headers)).status_code, 200)
        self.assertFalse((await self.backend.http.get("/api/v1/panels/session", headers=phone_headers)).json()["online"])
        bad = await self.backend.http.post("/api/v1/panels/run/wrong", headers=phone_headers, json={"widget_id": "control", "job_id": "x"})
        self.assertEqual(bad.status_code, 422)
        missing = await self.backend.http.post("/api/pair", json={"code": "bad", "name": "Phone"})
        self.assertEqual(missing.status_code, 400)
        code = (await self.backend.http.post("/api/v1/studio/pairing", headers=headers())).json()["code"]
        alias = await self.backend.http.post("/api/v1/panels/pair", json={"pairing_code": code})
        self.assertEqual(alias.status_code, 200)
        await self.backend.runtime.store.call(db.revoke_device_by_id, paired["deviceId"])
        self.assertEqual((await self.backend.http.get("/api/layout", headers=phone_headers)).status_code, 401)
        await socket.send('{"type":"ping"}')
        with self.assertRaises(ConnectionClosed):
            await socket.recv()

    async def test_revision_conflicts_invalid_actions_and_malformed_control_requests(self):
        await self.configured()
        collision = await self.backend.http.put("/api/v1/studio/layout", headers=headers(), json={"layout": layout(), "revision": 0})
        self.assertEqual(collision.status_code, 409)
        invalid = await self.backend.http.put("/api/v1/studio/actions/not.allowed", headers=headers(), json=request().model_dump())
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual((await self.backend.http.get("/api/v1/studio/actions/compute", headers=headers())).status_code, 200)
        self.assertEqual((await self.backend.http.get("/api/v1/studio/actions/missing", headers=headers())).status_code, 404)
        self.assertEqual((await self.backend.http.get("/api/v1/studio/session", headers=headers())).json()["device_id"], "device-1")
        for payload in ({"widget_id": "missing", "job_id": "m"}, {"widget_id": "control", "job_id": "v", "value": 1}, {"widget_id": "control", "job_id": "u", "program_id": "unknown"}):
            response = await self.backend.http.post("/api/v1/panel-actions/prepare" if payload.get("value") else "/api/v1/panel-actions/dispatch", headers=headers(), json=payload)
            self.assertIn(response.status_code, (404, 422))
        socket = await self.browser(headers()["Authorization"].removeprefix("Bearer "))
        await socket.send("invalid json")
        self.assertEqual((await self.message(socket, "error"))["message"], "INVALID_MESSAGE")
        await socket.send('{"type":"ping"}')
        await self.message(socket, "pong")
        await socket.send('{"type":"unknown"}')
        await self.message(socket, "error")
        await socket.send(json.dumps({"type": "trigger", "widget": "control", "job_id": "bad-action", "action": "wrong"}))
        self.assertEqual((await self.message(socket, "error"))["message"], "ACTION_MISMATCH")

    async def test_revoked_phone_cannot_read_updates_without_sending_another_frame(self):
        paired, _ = await self.phone()
        socket = await self.browser(paired["token"])
        await self.backend.runtime.store.call(db.revoke_device_by_id, paired["deviceId"])
        self.backend.runtime.events.publish(self.identity, {"type": "error", "message": "private next update"})
        with self.assertRaises(ConnectionClosed):
            await asyncio.wait_for(socket.recv(), 2)

    async def test_websocket_trigger_runs_on_laptop_and_plaintext_deployment_is_rejected(self):
        await self.configured(); await self.laptop(["ws-job"])
        socket = await self.browser(headers()["Authorization"].removeprefix("Bearer "))
        await socket.send(json.dumps({"type": "trigger", "widget": "control", "action": "compute", "job_id": "ws-job"}))
        self.assertEqual((await self.message(socket, "job_update", "COMPLETED"))["job"]["job_id"], "ws-job")
        self.backend.runtime.hub.settings = RuntimeSettings(require_tls=True)
        with self.assertRaises(InvalidStatus):
            await connect(f"ws://127.0.0.1:{self.backend.port}/ws")

    async def test_persisted_runtime_factory_and_authentication(self):
        with patch.dict("os.environ", {"AGENT_DEVICES_JSON": "[]", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}, clear=True):
            runtime = await AgentRuntime.persisted()
        self.assertEqual(len(runtime.registry.devices), 2)
        self.assertEqual(await runtime.authenticate(headers()["Authorization"].removeprefix("Bearer ")), self.identity)
        await runtime.close()

    async def test_prepared_request_change_is_rejected_and_widget_values_reach_generation(self):
        await self.configured("slider")
        payload = {"widget_id": "control", "job_id": "value", "value": 25}
        prepared = (await self.backend.http.post("/api/v1/panel-actions/prepare", headers=headers(), json=payload)).json()
        self.assertIn('25', prepared["request"]["description"])
        changed = await self.backend.http.post("/api/v1/panel-actions/dispatch", headers=headers(), json={**payload, "value": 26, "request_sha256": prepared["request_sha256"]})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["detail"], "REQUEST_CHANGED")
        self.assertEqual(self.backend.sdk.calls, [])
        await self.backend.runtime.store.action(self.identity, "compute", request("x" * 8192))
        oversized = await self.backend.http.post("/api/v1/panel-actions/prepare", headers=headers(), json=payload)
        self.assertEqual(oversized.status_code, 422)
        self.assertEqual(oversized.json()["detail"], "INVALID_CONTROL_REQUEST")
        await self.backend.runtime.store.action(self.identity, "compute", request())
        huge = await self.backend.http.post("/api/v1/panel-actions/prepare", headers=headers(), json={**payload, "value": 10 ** 1000})
        self.assertEqual(huge.status_code, 422)
        document = layout("agent")
        await self.backend.http.put("/api/v1/studio/layout", headers=headers(), json={"layout": document, "revision": 1})
        prompt = (await self.backend.http.post("/api/v1/panel-actions/prepare", headers=headers(), json={"widget_id": "control", "job_id": "prompt", "value": "Print the temperature"})).json()
        self.assertEqual(prompt["request"]["description"], "Print the temperature")
        await self.backend.http.put("/api/v1/studio/layout", headers=headers(), json={"layout": {**document, "widgets": [{**document["widgets"][0], "action": "unconfigured"}]}, "revision": 2})
        missing = await self.backend.http.post("/api/v1/panel-actions/prepare", headers=headers(), json={"widget_id": "control", "job_id": "missing", "value": "prompt"})
        self.assertEqual(missing.status_code, 409)

    async def test_websocket_malformed_authentication_binary_and_oversize_frames_close_cleanly(self):
        for message in ('{}', '{"type":"auth","token":"wrong"}', 'x' * 5000, b'binary'):
            socket = await connect(f"ws://127.0.0.1:{self.backend.port}/ws")
            await socket.send(message)
            with self.assertRaises(ConnectionClosed): await socket.recv()
            await socket.close()
        socket = await self.browser(headers()["Authorization"].removeprefix("Bearer "))
        await socket.send('x' * 17000)
        with self.assertRaises(ConnectionClosed): await socket.recv()

    async def test_slow_browser_disconnects_without_blocking_other_clients(self):
        socket = await self.browser(headers()["Authorization"].removeprefix("Bearer "))
        for _ in range(65): self.backend.runtime.events.publish(self.identity, {"type": "layout_updated"})
        with self.assertRaises(ConnectionClosed): await socket.recv()

    async def test_websocket_trigger_errors_are_sanitized_and_heartbeat_is_responsive(self):
        await self.configured()
        socket = await self.browser(headers()["Authorization"].removeprefix("Bearer "))
        await socket.send('{"type":"trigger"}')
        self.assertEqual((await self.message(socket, "error"))["message"], "INVALID_TRIGGER")
        await socket.send(json.dumps({"type": "trigger", "widget": "control", "action": "compute", "job_id": "offline"}))
        self.assertEqual((await self.message(socket, "error"))["message"], "AGENT_OFFLINE")
        with patch("app.routes.ws.dispatch", AsyncMock(side_effect=OSError("private failure"))):
            await socket.send(json.dumps({"type": "trigger", "widget": "control", "action": "compute", "job_id": "error"}))
            self.assertEqual((await self.message(socket, "error"))["message"], "REQUEST_FAILED")
        await socket.send('42')
        self.assertEqual((await self.message(socket, "error"))["message"], "INVALID_MESSAGE")
        await socket.send('{"type":"ping"}')
        await self.message(socket, "pong")


class GenerationAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    """Needs no C compiler: no agent connects, so only the session endpoints are exercised."""

    async def test_sessions_report_whether_the_backend_has_a_gemini_key(self):
        with tempfile.TemporaryDirectory(prefix="janus-session-db-") as folder, patch.object(db, "DB_PATH", Path(folder) / "app.db"):
            store = SQLiteStore()
            await store.initialize(registry())
            backend = await RunningServer().start(store=store)
            try:
                for path in ("/api/v1/studio/session", "/api/v1/panels/session"):
                    body = (await backend.http.get(path, headers=headers())).json()
                    self.assertIs(body["generation_available"], True, path)
                    self.assertFalse(body["online"])
                backend.runtime.provider.client = None  # what GeminiProvider holds without a credential
                for path in ("/api/v1/studio/session", "/api/v1/panels/session"):
                    self.assertIs((await backend.http.get(path, headers=headers())).json()["generation_available"], False, path)
            finally:
                await backend.close()


class DuplicateAgentTests(unittest.IsolatedAsyncioTestCase):
    """Needs no C compiler: raw WebSocket agents only exchange the hello/ready handshake."""

    async def agent(self, backend):
        socket = await connect(f"ws://127.0.0.1:{backend.port}/ws/agents/device-1", additional_headers={"Authorization": "Bearer agent-1-" + "a" * 32})
        backend.sockets.append(socket)
        await socket.send(json.dumps(packet("device-1", "agent_hello", None, "CONNECTED", {
            "platform": "Windows", "architecture": "x86_64", "max_workers": 1, "max_attempts": 3,
            "build_boundary_available": False, "execution_boundary_available": False})))
        return socket

    async def test_a_second_agent_for_the_same_device_is_refused_with_a_final_close_code(self):
        with tempfile.TemporaryDirectory(prefix="janus-dup-db-") as folder, patch.object(db, "DB_PATH", Path(folder) / "app.db"):
            store = SQLiteStore()
            await store.initialize(registry())
            backend = await RunningServer().start(store=store)
            try:
                first = await self.agent(backend)
                self.assertEqual(json.loads(await asyncio.wait_for(first.recv(), 2))["type"], "connection_ready")
                original = backend.runtime.hub.get("device-1")
                second = await self.agent(backend)
                with self.assertRaises(ConnectionClosed) as closed:
                    await asyncio.wait_for(second.recv(), 2)
                self.assertEqual((closed.exception.rcvd.code, closed.exception.rcvd.reason), (4409, "DEVICE_ALREADY_CONNECTED"))
                self.assertIs(backend.runtime.hub.get("device-1"), original)  # the original stays registered and open
                await first.close()
                await asyncio.sleep(0.2)
                third = await self.agent(backend)  # once the first is gone the device can connect again
                self.assertEqual(json.loads(await asyncio.wait_for(third.recv(), 2))["type"], "connection_ready")
            finally:
                await backend.close()


class PanelContracts(unittest.TestCase):
    def test_server_validates_geometry_ids_binding_ranges_and_overlap(self):
        self.assertEqual(PanelLayout.model_validate(layout()).widgets[0].id, "control")
        for change in ({"x": 300}, {"action": None}, {"w": float("nan")}, {"min": 5, "max": 1}):
            bad = layout(); bad["widgets"][0].update(change)
            with self.assertRaises(ValueError): PanelLayout.model_validate(bad)
        for identifier in ("control", "other"):
            bad = layout(); bad["widgets"].append({**bad["widgets"][0], "id": identifier})
            with self.assertRaises(ValueError): PanelLayout.model_validate(bad)
        dial = layout(); dial["widgets"][0].update(type="gauge", source="cpu.percent")
        self.assertEqual(PanelLayout.model_validate(dial).widgets[0].source, "cpu.percent")

    def test_input_values_are_typed_and_bounded(self):
        cases = [("button", None, "text"), ("toggle", True, 1), ("slider", 50, 101), ("knob", 0, float("nan")), ("joystick", {"x": .5, "y": -.5}, {"x": 2, "y": 0}), ("dpad", "left", "wrong"), ("agent", "Compute", ""), ("command", "run", "x" * 4097)]
        for kind, valid, invalid in cases:
            checked_value({"type": kind}, valid)
            with self.assertRaises(PipelineError): checked_value({"type": kind}, invalid)

    def test_event_fanout_capacity_is_scoped_and_slow_consumers_disconnect(self):
        events = PanelEvents(); owner = Identity("d", "o"); other = Identity("e", "p")
        slow = events.subscribe(owner); foreign = events.subscribe(other)
        for n in range(65): events.publish(owner, {"type": "job_update", "n": n})
        self.assertEqual(slow.queue.get_nowait()["type"], "disconnect")
        self.assertTrue(foreign.queue.empty())
        for _ in range(8): events.subscribe(owner)
        with self.assertRaises(ValueError): events.subscribe(owner)
        events.unsubscribe(foreign)
