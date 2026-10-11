"""Persisted AI source drafts never require or command a laptop worker."""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import db
from app.agent_runtime.config import Identity, RuntimeSettings
from app.agent_runtime.connections import AgentConnection
from app.agent_runtime.llm import GeminiProvider, ProviderError
from app.agent_runtime.models import GeneratedProgram, Hello
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.store import SQLiteStore
from app.routes.agent_jobs import router
from tests.support import FakeSDK, GOOD, headers, program, registry, request


class ProgramDraftTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="janus-draft-")
        self.path = patch.object(db, "DB_PATH", Path(self.folder.name) / "drafts.db")
        self.path.start()
        self.store = SQLiteStore()
        await self.store.initialize(registry())
        self.sdk = FakeSDK()
        self.provider = GeminiProvider(client=self.sdk, timeout=1)
        self.runtime = AgentRuntime.create(registry(), self.provider, RuntimeSettings(require_tls=False), self.store)
        self.identity = Identity("device-1", "owner-1")
        app = FastAPI()
        app.state.agent_runtime = self.runtime
        app.include_router(router)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.runtime.close()
        self.path.stop()
        self.folder.cleanup()

    async def generate(self, payload=None, credential=None):
        return await self.http.post("/api/v1/programs/generate", json=(payload or request()).model_dump(),
                                    headers=headers() if credential is None else credential)

    async def test_offline_generation_persists_exact_source_and_scope_without_creating_jobs(self):
        self.assertFalse(self.runtime.hub.connections)
        draft_request = request("Report CPU details").model_copy(update={"operation": "process.inspect", "capabilities": ["process.inspect"], "dependencies": ["mac-system"]})
        with patch.object(self.runtime.jobs, "submit", AsyncMock(side_effect=AssertionError("Draft must not submit a job"))), patch.object(self.runtime.hub, "get", side_effect=AssertionError("Draft must not require a laptop")):
            response = await self.generate(draft_request)
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(set(body), {"program_id", "program"})
        self.assertEqual(body["program"], program(draft_request).model_dump())
        self.assertEqual(await self.store.program(self.identity, body["program_id"], draft_request), program(draft_request))
        self.assertFalse(self.runtime.jobs.records or self.runtime.jobs.seen)
        self.assertEqual(await self.store.history(self.identity), [])
        self.assertEqual(self.sdk.calls[0]["request"], draft_request.model_dump())

    async def test_connected_laptop_without_adapter_receives_no_commands(self):
        websocket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
        connection = AgentConnection(self.identity, websocket,
            Hello(platform="Darwin", architecture="x86_64", max_workers=4, max_attempts=3,
                  build_boundary_available=False, execution_boundary_available=False), self.runtime.hub.settings)
        self.runtime.hub.connections[self.identity.device_id] = connection
        response = await self.generate()
        self.assertEqual(response.status_code, 201, response.text)
        websocket.send_json.assert_not_awaited()
        self.assertFalse(self.runtime.jobs.records)

    async def test_controller_and_bound_phone_share_only_their_own_program_scope(self):
        code = await self.store.pairing(self.identity)
        phone = await self.store.pair(code, "Draft phone")
        credential = {"Authorization": "Bearer " + phone["token"]}
        response = await self.generate(credential=credential)
        self.assertEqual(response.status_code, 201)
        identifier = response.json()["program_id"]
        self.assertEqual(await self.store.program(self.identity, identifier, request()), program())
        for outside in (Identity("device-2", "owner-2"), Identity("device-1", "owner-2")):
            self.assertIsNone(await self.store.program(outside, identifier, request()))
            self.assertEqual(await self.store.programs(outside, request()), [])
        changed = request().model_copy(update={"capabilities": ["network"]})
        self.assertIsNone(await self.store.program(self.identity, identifier, changed))
        await self.store.call(db.revoke_device_by_id, phone["deviceId"])
        self.assertEqual((await self.generate(credential=credential)).status_code, 401)
        self.assertEqual(len(self.sdk.calls), 1)

    async def test_missing_invalid_and_agent_credentials_never_call_generation(self):
        for credential in ({}, {"Authorization": "Bearer unknown"}, {"Authorization": "Bearer agent-1-" + "a" * 32}):
            with self.subTest(credential=credential):
                response = await self.generate(credential=credential)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()["detail"], "UNAUTHORIZED")
        self.assertFalse(self.sdk.calls)

    async def test_remote_policy_fields_and_invalid_target_dependencies_are_rejected(self):
        changes = [{"device_id": "device-2"}, {"execute_authorized": True}, {"isolation_adapter": "remote:worker"}, {"dependencies": ["win32"]}, {"capabilities": ["unknown"]}, {"description": ""}, {"output_protocol": "raw"}]
        for change in changes:
            with self.subTest(change=change):
                response = await self.http.post("/api/v1/programs/generate", headers=headers(), json={**request().model_dump(), **change})
                self.assertEqual(response.status_code, 422)
        self.assertFalse(self.sdk.calls)

    async def test_provider_cannot_change_any_requested_scope_field(self):
        changes = [{"operation": "files.write"}, {"platform": "Windows"}, {"architecture": "arm64"}, {"output_protocol": "text"}, {"capabilities": ["admin"]}, {"dependencies": ["mac-system"]}]
        for change in changes:
            with self.subTest(change=change), patch.object(self.provider, "generate", AsyncMock(return_value=program().model_copy(update=change))):
                response = await self.generate()
                self.assertEqual((response.status_code, response.json()["detail"]), (502, "MALFORMED_RESPONSE"))
        self.assertEqual(await self.store.programs(self.identity, request()), [])

    async def test_generated_model_is_revalidated_even_for_alternative_provider_instances(self):
        invalid = [None, [], {}, program().model_copy(update={"source": GOOD + "\0"}), program().model_copy(update={"source": "x" * 131073}), program().model_copy(update={"summary": ""}), program().model_copy(update={"language": "C11"})]
        for generated in invalid:
            with self.subTest(type=type(generated).__name__), patch.object(self.provider, "generate", AsyncMock(return_value=generated)):
                response = await self.generate()
                self.assertEqual((response.status_code, response.json()["detail"]), (502, "MALFORMED_RESPONSE"))
        self.assertEqual(await self.store.programs(self.identity, request()), [])

    async def test_provider_errors_have_bounded_codes_and_never_expose_private_details(self):
        for reason, status in (("PROVIDER_NOT_CONFIGURED", 503), ("PROVIDER_UNAVAILABLE", 503), ("RATE_LIMITED", 429), ("PROVIDER_TIMEOUT", 504), ("MALFORMED_RESPONSE", 502), ("PROVIDER_ERROR", 502), ("private credential and path", 502)):
            with self.subTest(reason=reason), patch.object(self.provider, "generate", AsyncMock(side_effect=ProviderError(reason))):
                response = await self.generate()
                expected = reason if reason != "private credential and path" else "PROVIDER_ERROR"
                self.assertEqual((response.status_code, response.json()["detail"]), (status, expected))
                self.assertNotIn("private credential", response.text)
        for failure, status, reason in ((TimeoutError("private timeout"), 504, "PROVIDER_TIMEOUT"), (OSError("private connection"), 503, "PROVIDER_UNAVAILABLE"), (ValueError("private invalid model"), 502, "MALFORMED_RESPONSE")):
            with self.subTest(failure=type(failure).__name__), patch.object(self.provider, "generate", AsyncMock(side_effect=failure)):
                response = await self.generate()
                self.assertEqual((response.status_code, response.json()["detail"]), (status, reason))
                self.assertNotIn("private", response.text)
        self.assertEqual(await self.store.programs(self.identity, request()), [])

    async def test_unconfigured_gemini_returns_actionable_error_while_offline(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
            missing = GeminiProvider()
        self.runtime.provider = missing
        response = await self.generate()
        self.assertEqual((response.status_code, response.json()["detail"]), (503, "PROVIDER_NOT_CONFIGURED"))
        self.assertFalse(self.runtime.jobs.records)
        self.runtime.provider = self.provider

    async def test_generation_waits_for_the_existing_provider_pool_and_obeys_its_deadline(self):
        provider = GeminiProvider(client=self.sdk, max_concurrent=1, timeout=.02)
        self.runtime.provider = provider
        await provider.slots.acquire()
        try:
            response = await asyncio.wait_for(self.generate(), .5)
            self.assertEqual((response.status_code, response.json()["detail"]), (504, "PROVIDER_TIMEOUT"))
            self.assertFalse(self.sdk.calls)
        finally:
            provider.slots.release()
        self.assertEqual((await self.generate()).status_code, 201)
        self.assertEqual(len(self.sdk.calls), 1)

    async def test_parallel_drafts_use_the_shared_provider_limit_and_distinct_program_ids(self):
        sdk = FakeSDK(delay=.01)
        self.runtime.provider = GeminiProvider(client=sdk, max_concurrent=2, timeout=1)
        responses = await asyncio.gather(*(self.generate() for _ in range(6)))
        self.assertEqual([response.status_code for response in responses], [201] * 6)
        self.assertEqual(len({response.json()["program_id"] for response in responses}), 6)
        self.assertEqual(sdk.peak, 2)
        self.assertEqual(len(await self.store.programs(self.identity, request())), 6)
        self.assertFalse(self.runtime.jobs.records)

    async def test_alternative_provider_also_has_an_outer_deadline_and_saves_nothing_on_timeout(self):
        async def blocked(_):
            await asyncio.Event().wait()
        real_timeout = asyncio.timeout
        with patch.object(self.provider, "generate", AsyncMock(side_effect=blocked)), patch("app.routes.agent_jobs.asyncio.timeout", side_effect=lambda seconds: real_timeout(.01)) as deadline:
            response = await self.generate()
        deadline.assert_called_once_with(25)
        self.assertEqual((response.status_code, response.json()["detail"]), (504, "PROVIDER_TIMEOUT"))
        self.assertEqual(await self.store.programs(self.identity, request()), [])
        self.assertFalse(self.runtime.jobs.records)

    async def test_missing_or_closing_storage_stops_before_provider_and_closed_pool_fails_cleanly(self):
        self.runtime.store = None
        response = await self.generate()
        self.assertEqual((response.status_code, response.json()["detail"]), (503, "PROGRAM_STORE_UNAVAILABLE"))
        self.runtime.store = self.store
        self.runtime.jobs.closing = True
        response = await self.generate()
        self.assertEqual((response.status_code, response.json()["detail"]), (503, "SERVICE_CLOSING"))
        self.assertFalse(self.sdk.calls)
        self.runtime.jobs.closing = False
        for failure in (sqlite3.OperationalError("private path"), RuntimeError("private closed executor")):
            with self.subTest(failure=type(failure).__name__), patch.object(self.store, "save_program", AsyncMock(side_effect=failure)):
                response = await self.generate()
                self.assertEqual((response.status_code, response.json()["detail"]), (503, "PROGRAM_STORE_UNAVAILABLE"))
                self.assertNotIn("private", response.text)

    async def test_shutdown_during_generation_does_not_write_an_orphan_draft(self):
        async def closing(_):
            self.runtime.jobs.closing = True
            return program()
        with patch.object(self.provider, "generate", AsyncMock(side_effect=closing)), patch.object(self.store, "save_program", AsyncMock()) as save:
            response = await self.generate()
        self.assertEqual((response.status_code, response.json()["detail"]), (503, "SERVICE_CLOSING"))
        save.assert_not_awaited()

    async def test_cancelled_generation_releases_provider_slot_and_does_not_persist_or_execute(self):
        started = asyncio.Event()
        async def blocked(**_):
            started.set()
            await asyncio.Event().wait()
        self.sdk.aio.models.generate_content.side_effect = blocked
        task = asyncio.create_task(self.generate())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.provider.slots._value, 8)
        self.assertEqual(await self.store.programs(self.identity, request()), [])
        self.assertFalse(self.runtime.jobs.records)

    async def test_draft_remains_scoped_and_available_after_restarting_the_sqlite_store(self):
        draft = (await self.generate()).json()
        replacement = SQLiteStore()
        try:
            await replacement.initialize(registry())
            self.assertEqual(await replacement.program(self.identity, draft["program_id"], request()), GeneratedProgram.model_validate(draft["program"]))
            self.assertIsNone(await replacement.program(Identity("device-2", "owner-2"), draft["program_id"], request()))
        finally:
            await replacement.close()
