"""Portable worker targets and permission proposals never authorize execution."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
import httpx

from app import db
from app.agent_runtime.config import Identity, RuntimeSettings
from app.agent_runtime.connections import AgentConnection, PipelineError
from app.agent_runtime.llm import GeminiProvider, ProviderError
from app.agent_runtime.models import ExecutionTarget, GeneratedProgram, GenerationRequest, Hello, PlanRequest, RepairPayload, PORTABLE_CAPABILITIES
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.store import SQLiteStore
from app.routes.agent_jobs import router
from app.routes.panels import router as panel_router
from app.routes.studio import router as studio_router
from tests.support import FakeSDK, GOOD, headers, program, registry


TARGET = {"platform": "Linux", "architecture": "x86_64", "profile": "portable-c", "capabilities": sorted(PORTABLE_CAPABILITIES), "dependencies": []}


def proposal(description="Set the volume to 35 percent", capabilities=None):
    return PlanRequest(description=description, platform="Linux", architecture="x86_64", allowed_capabilities=sorted(PORTABLE_CAPABILITIES) if capabilities is None else capabilities)


def planned(payload=None, **changes):
    payload = payload or proposal()
    return GenerationRequest(description=payload.description, operation="audio.volume", platform=payload.platform,
                             architecture=payload.architecture, capabilities=["audio.volume"], dependencies=[],
                             output_protocol="json-v1").model_copy(update=changes)


def hello(**changes):
    return Hello.model_validate({"platform": "Darwin", "architecture": "arm64", "max_workers": 4,
        "max_attempts": 3, "build_boundary_available": True, "execution_boundary_available": True,
        "execution_target": TARGET, "boundary_reason": None, **changes})


class PortableTargetModelsTests(unittest.TestCase):
    def test_physical_host_and_execution_target_are_distinct_and_legacy_hello_is_valid(self):
        current = hello()
        self.assertEqual((current.platform, current.architecture), ("Darwin", "arm64"))
        self.assertEqual((current.execution_target.platform, current.execution_target.architecture), ("Linux", "x86_64"))
        legacy = Hello.model_validate(current.model_dump(exclude={"execution_target", "boundary_reason"}))
        self.assertIsNone(legacy.execution_target)
        self.assertIsNone(legacy.boundary_reason)
        self.assertEqual(hello(boundary_reason="DOCKER_UNAVAILABLE").boundary_reason, "DOCKER_UNAVAILABLE")

    def test_execution_target_rejects_unknown_fields_native_libraries_and_unsupported_rights(self):
        for change in ({"platform": "Darwin"}, {"architecture": "x86"}, {"profile": "native"},
                       {"capabilities": ["network"]}, {"capabilities": ["admin"]}, {"capabilities": ["external.send"]},
                       {"capabilities": ["persistent.write"]}, {"capabilities": ["files.read", "files.read"]},
                       {"dependencies": ["win32"]}, {"worker_command": "remote"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                ExecutionTarget.model_validate({**TARGET, **change})
        with self.assertRaises(ValueError):
            hello(boundary_reason="private /Users/person/credential")
        self.assertEqual(hello(platform="Linux").platform, "Linux")
        with self.assertRaises(ValueError):
            hello(platform="Linux", native_streams=["volume"])

    def test_linux_requires_supported_architecture_scope_and_broker_framing(self):
        good = planned()
        self.assertEqual(GenerationRequest.model_validate(good.model_dump()).platform, "Linux")
        self.assertEqual(GenerationRequest.model_validate({**good.model_dump(), "capabilities": [], "output_protocol": "text"}).output_protocol, "text")
        self.assertEqual(GenerationRequest.model_validate({**good.model_dump(), "capabilities": ["files.write"], "output_protocol": "text"}).capabilities, ["files.write"])
        for change in ({"architecture": "x86"}, {"dependencies": ["mac-system"]}, {"dependencies": ["win32"]},
                       {"capabilities": ["network"]}, {"capabilities": ["audio.volume", "audio.volume"]}, {"output_protocol": "text"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                GenerationRequest.model_validate({**good.model_dump(), **change})
            with self.subTest(program=change), self.assertRaises(ValueError):
                GeneratedProgram.model_validate({**program(good).model_dump(), **change})

    def test_linux_repairs_keep_canonical_diagnostics_and_manifest_output_protocol(self):
        payload = {"attempt": 1, "reason": "COMPILE_ERROR", "platform": "Linux", "architecture": "x86_64", "compiler": "clang",
            "language_standard": "c11", "source_sha256": "a" * 64, "diagnostics": "program.c:1:2: error: undeclared identifier"}
        self.assertEqual(RepairPayload.model_validate(payload).platform, "Linux")
        self.assertEqual(program(planned()).manifest("fresh-job")["output_protocol"], "json-v1")

    def test_planning_input_and_output_scope_are_strict(self):
        payload = proposal()
        for change in ({"allowed_capabilities": ["everything"]}, {"allowed_capabilities": ["audio.volume", "audio.volume"]},
                       {"description": "   "}, {"execute_authorized": True}, {"device_id": "other"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                PlanRequest.model_validate({**payload.model_dump(), **change})
        for change in ({"description": "Changed request"}, {"platform": "Darwin"}, {"architecture": "arm64"},
                       {"capabilities": ["network"]}, {"dependencies": ["mac-system"]}, {"output_protocol": "text"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                payload.check_scope(planned(payload, **change))


class PlanningProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_portable_generation_and_repair_use_native_broker_instruction_without_changing_scope(self):
        sdk = FakeSDK()
        provider = GeminiProvider(client=sdk)
        payload = planned()
        generated = await provider.generate(payload)
        generated.check_scope(payload)
        await provider.repair(payload, GOOD, "program.c:1:2: error: undeclared identifier")
        instruction = sdk.aio.models.generate_content.call_args.kwargs["config"].system_instruction
        for detail in ('portable Linux C11', '"janus"', 'at most eight commands', 'host filesystem', 'screen get {}', 'process.inspect'):
            self.assertIn(detail, instruction)
        self.assertEqual(sdk.calls[0]["request"], payload.model_dump())
        await provider.close()

    async def test_planning_uses_async_structured_output_and_minimal_capability_contract(self):
        sdk = FakeSDK()
        sdk.aio.models.generate_content.side_effect = None
        sdk.aio.models.generate_content.return_value = SimpleNamespace(text=json.dumps({"request": planned().model_dump()}))
        provider = GeminiProvider(client=sdk)
        result = await provider.plan(proposal())
        self.assertEqual(result.capabilities, ["audio.volume"])
        kwargs = sdk.aio.models.generate_content.call_args.kwargs
        self.assertEqual(json.loads(kwargs["contents"]), {"request": proposal().model_dump()})
        self.assertEqual(kwargs["config"].response_mime_type, "application/json")
        self.assertIn('smallest capabilities', kwargs["config"].system_instruction)
        self.assertIn('request:null', kwargs["config"].system_instruction)
        await provider.close()

    async def test_planning_refuses_unsupported_tasks_and_rejects_expansion_or_malformed_output(self):
        sdk = FakeSDK()
        sdk.aio.models.generate_content.side_effect = None
        provider = GeminiProvider(client=sdk)
        sdk.aio.models.generate_content.return_value = SimpleNamespace(text='{"request":null}')
        with self.assertRaisesRegex(ProviderError, '^CAPABILITY_UNAVAILABLE$'):
            await provider.plan(proposal())
        for response in ('not-json', '{}', json.dumps({"request": planned(description="Other task").model_dump()}),
                         json.dumps({"request": planned(capabilities=["network"]).model_dump()}),
                         json.dumps({"request": planned(output_protocol="text").model_dump()})):
            sdk.aio.models.generate_content.return_value = SimpleNamespace(text=response)
            with self.subTest(response=response), self.assertRaisesRegex(ProviderError, '^MALFORMED_RESPONSE$'):
                await provider.plan(proposal())
        await provider.close()

    async def test_planning_shares_pool_deadline_and_cancellation_cleanup(self):
        sdk = FakeSDK()
        entered = asyncio.Event()
        async def blocked(**_):
            entered.set()
            await asyncio.Event().wait()
        sdk.aio.models.generate_content.side_effect = blocked
        provider = GeminiProvider(client=sdk, max_concurrent=1, timeout=.02)
        await provider.slots.acquire()
        with self.assertRaisesRegex(ProviderError, '^PROVIDER_TIMEOUT$'):
            await provider.plan(proposal())
        provider.slots.release()
        task = asyncio.create_task(provider.plan(proposal()))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(provider.slots._value, 1)
        sdk.aio.models.generate_content.side_effect = None
        sdk.aio.models.generate_content.return_value = SimpleNamespace(text=json.dumps({"request": planned().model_dump()}))
        self.assertEqual((await provider.plan(proposal())).capabilities, ["audio.volume"])
        await provider.close()


class PlanningAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="janus-plan-")
        self.path = patch.object(db, "DB_PATH", Path(self.folder.name) / "plans.db")
        self.path.start()
        self.store = SQLiteStore()
        await self.store.initialize(registry())
        self.provider = SimpleNamespace(plan=AsyncMock(return_value=planned()), generate=AsyncMock(), repair=AsyncMock(), close=AsyncMock())
        self.runtime = AgentRuntime.create(registry(), self.provider, RuntimeSettings(require_tls=False), self.store)
        self.identity = Identity("device-1", "owner-1")
        self.websocket = SimpleNamespace(send_json=AsyncMock(), close=AsyncMock())
        self.connection = AgentConnection(self.identity, self.websocket, hello(), self.runtime.hub.settings)
        self.runtime.hub.connections[self.identity.device_id] = self.connection
        app = FastAPI()
        app.state.agent_runtime = self.runtime
        app.include_router(router)
        app.include_router(studio_router)
        app.include_router(panel_router)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.runtime.close()
        self.path.stop()
        self.folder.cleanup()

    async def plan(self, payload=None, credential=None):
        return await self.http.post('/api/v1/programs/plan', headers=headers() if credential is None else credential,
                                    json=(payload or proposal()).model_dump())

    async def test_plan_returns_minimal_scope_without_program_storage_job_or_agent_command(self):
        response = await self.plan()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), planned().model_dump())
        self.provider.plan.assert_awaited_once_with(proposal())
        self.provider.generate.assert_not_awaited()
        self.websocket.send_json.assert_not_awaited()
        self.assertFalse(self.runtime.jobs.records or self.runtime.jobs.seen)
        self.assertEqual(await self.store.programs(self.identity, planned()), [])
        self.assertEqual(await self.store.history(self.identity), [])

    async def test_linux_source_review_remains_available_offline_and_persists_exact_scope(self):
        self.runtime.hub.connections.clear()
        self.provider.generate.return_value = program(planned())
        response = await self.http.post('/api/v1/programs/generate', headers=headers(), json=planned().model_dump())
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body['program'], program(planned()).model_dump())
        self.assertEqual(await self.store.program(self.identity, body['program_id'], planned()), program(planned()))
        self.provider.plan.assert_not_awaited()
        self.assertFalse(self.runtime.jobs.records)
        self.websocket.send_json.assert_not_awaited()

    async def test_bound_phone_plans_only_for_its_laptop_and_revoked_credentials_stop_before_provider(self):
        code = await self.store.pairing(self.identity)
        phone = await self.store.pair(code, 'Planning phone')
        credential = {'Authorization': 'Bearer ' + phone['token']}
        self.assertEqual((await self.plan(credential=credential)).status_code, 200)
        await self.store.call(db.revoke_device_by_id, phone['deviceId'])
        self.assertEqual((await self.plan(credential=credential)).status_code, 401)
        self.provider.plan.assert_awaited_once()
        self.assertEqual((await self.plan(credential=headers(2))).json()['detail'], 'AGENT_OFFLINE')
        self.provider.plan.assert_awaited_once()

    async def test_unauthorized_and_agent_credentials_never_call_planning(self):
        for credential in ({}, {'Authorization': 'Bearer unknown'}, {'Authorization': 'Bearer agent-1-' + 'a' * 32}):
            with self.subTest(credential=credential):
                self.assertEqual((await self.plan(credential=credential)).status_code, 401)
        self.provider.plan.assert_not_awaited()

    async def test_missing_agent_target_and_forbidden_target_or_rights_stop_before_provider(self):
        self.runtime.hub.connections.clear()
        self.assertEqual((await self.plan()).json()['detail'], 'AGENT_OFFLINE')
        self.runtime.hub.connections[self.identity.device_id] = self.connection
        self.connection.hello.execution_target = None
        self.assertEqual((await self.plan()).json()['detail'], 'EXECUTION_TARGET_UNAVAILABLE')
        self.connection.hello = hello()
        self.assertEqual((await self.plan(proposal().model_copy(update={'platform': 'Darwin'}))).json()['detail'], 'TARGET_MISMATCH')
        response = await self.plan(proposal(capabilities=['network']))
        self.assertEqual((response.status_code, response.json()['detail']), (403, 'CAPABILITY_UNAVAILABLE'))
        self.provider.plan.assert_not_awaited()

    async def test_remote_permission_and_device_fields_are_rejected(self):
        for change in ({'device_id': 'device-2'}, {'execute_authorized': True}, {'allowed_capabilities': ['unknown']}, {'description': ' '}):
            with self.subTest(change=change):
                response = await self.http.post('/api/v1/programs/plan', headers=headers(), json={**proposal().model_dump(), **change})
                self.assertEqual(response.status_code, 422)
        self.provider.plan.assert_not_awaited()

    async def test_provider_response_is_revalidated_and_cannot_change_target_scope_or_protocol(self):
        for response in (None, {}, planned(description='other'), planned(platform='Darwin'), planned(architecture='arm64'),
                         planned(capabilities=['network']), planned(dependencies=['mac-system']), planned(output_protocol='text')):
            self.provider.plan.return_value = response
            with self.subTest(response=response):
                result = await self.plan()
                self.assertEqual((result.status_code, result.json()['detail']), (502, 'MALFORMED_RESPONSE'))
        self.provider.plan.return_value = planned(capabilities=['desktop.mouse'])
        result = await self.plan(proposal(capabilities=['audio.volume']))
        self.assertEqual((result.status_code, result.json()['detail']), (502, 'MALFORMED_RESPONSE'))
        self.assertFalse(self.runtime.jobs.records)

    async def test_provider_failures_return_bounded_safe_codes(self):
        for reason, status in (('PROVIDER_NOT_CONFIGURED', 503), ('PROVIDER_UNAVAILABLE', 503), ('RATE_LIMITED', 429),
                               ('PROVIDER_TIMEOUT', 504), ('MALFORMED_RESPONSE', 502), ('CAPABILITY_UNAVAILABLE', 403), ('private secret', 502)):
            self.provider.plan.side_effect = ProviderError(reason)
            with self.subTest(reason=reason):
                result = await self.plan()
                self.assertEqual((result.status_code, result.json()['detail']), (status, reason if reason != 'private secret' else 'PROVIDER_ERROR'))
                self.assertNotIn('private', result.text)
        for error, status, reason in ((TimeoutError('private'), 504, 'PROVIDER_TIMEOUT'), (OSError('private'), 503, 'PROVIDER_UNAVAILABLE'), (ValueError('private'), 502, 'MALFORMED_RESPONSE')):
            self.provider.plan.side_effect = error
            result = await self.plan()
            self.assertEqual((result.status_code, result.json()['detail']), (status, reason))

    async def test_plan_rejects_connection_changes_or_shutdown_during_provider_wait(self):
        async def disconnected(_):
            self.connection.closed = True
            return planned()
        self.provider.plan.side_effect = disconnected
        response = await self.plan()
        self.assertEqual((response.status_code, response.json()['detail']), (409, 'EXECUTION_TARGET_CHANGED'))
        self.connection.closed = False
        async def changed(_):
            self.connection.hello.execution_target.capabilities = []
            return planned()
        self.provider.plan.side_effect = changed
        self.assertEqual((await self.plan()).json()['detail'], 'EXECUTION_TARGET_CHANGED')
        self.connection.hello = hello()
        async def closing(_):
            self.runtime.jobs.closing = True
            return planned()
        self.provider.plan.side_effect = closing
        self.assertEqual((await self.plan()).json()['detail'], 'SERVICE_CLOSING')
        self.provider.plan.reset_mock()
        self.assertEqual((await self.plan()).json()['detail'], 'SERVICE_CLOSING')
        self.provider.plan.assert_not_awaited()

    async def test_plan_alternative_provider_has_outer_deadline_and_cancellation_propagates(self):
        started = asyncio.Event()
        async def blocked(_):
            started.set()
            await asyncio.Event().wait()
        self.provider.plan.side_effect = blocked
        real_timeout = asyncio.timeout
        with patch('app.routes.agent_jobs.asyncio.timeout', side_effect=lambda _: real_timeout(.01)) as deadline:
            result = await self.plan()
        deadline.assert_called_once_with(25)
        self.assertEqual((result.status_code, result.json()['detail']), (504, 'PROVIDER_TIMEOUT'))
        started.clear()
        task = asyncio.create_task(self.plan())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.runtime.jobs.records)

    async def test_session_and_presence_preserve_physical_host_worker_target_and_safe_failure_reason(self):
        self.connection.hello = hello(build_boundary_available=False, execution_boundary_available=False, boundary_reason='DOCKER_UNAVAILABLE')
        for path in ('/api/v1/studio/session', '/api/v1/panels/session'):
            response = await self.http.get(path, headers=headers())
            target = response.json()['target']
            self.assertEqual((target['platform'], target['architecture']), ('Darwin', 'arm64'))
            self.assertEqual(target['execution_target'], TARGET)
            self.assertEqual(target['boundary_reason'], 'DOCKER_UNAVAILABLE')
        subscriber = self.runtime.events.subscribe(self.identity)
        self.runtime.hub.on_presence(self.connection, True)
        event = subscriber.queue.get_nowait()
        self.assertEqual(event['session']['target']['execution_target'], TARGET)
        self.assertEqual(event['session']['target']['boundary_reason'], 'DOCKER_UNAVAILABLE')
        self.runtime.events.unsubscribe(subscriber)

    async def test_source_and_generation_admission_check_authoritative_worker_target_and_rights(self):
        wrong_target = GenerationRequest(description='Native request', operation='compute', platform='Darwin', architecture='arm64')
        outside = planned(capabilities=['desktop.screen'])
        self.connection.hello.execution_target.capabilities = ['audio.volume']
        for payload, reason in ((wrong_target, 'TARGET_MISMATCH'), (outside, 'CAPABILITY_UNAVAILABLE')):
            with self.subTest(payload=payload):
                result = await self.http.post('/api/v1/agent-jobs/generate', headers=headers(), json={'device_id': 'device-1', 'job_id': 'not-admitted', 'request': payload.model_dump()})
                self.assertEqual(result.json()['detail'], reason)
                result = await self.http.post('/api/v1/agent-jobs/source', headers=headers(), json={'device_id': 'device-1', 'job_id': 'not-admitted', 'request': payload.model_dump(), 'program': program(payload).model_dump()})
                self.assertEqual(result.json()['detail'], reason)
        self.assertFalse(self.runtime.jobs.records or self.runtime.jobs.seen)
        self.provider.generate.assert_not_awaited()
        self.websocket.send_json.assert_not_awaited()
        # The internal gate also rejects a forged model that bypassed request
        # parsing; no native library reaches the portable worker or provider.
        with self.assertRaisesRegex(PipelineError, 'CAPABILITY_UNAVAILABLE'):
            await self.runtime.jobs.submit(self.identity, 'device-1', 'not-admitted', planned(dependencies=['mac-system']))

    async def test_correct_linux_source_enters_existing_fresh_job_pipeline_and_not_physical_host_gate(self):
        async def finish(record):
            record.finish('REJECTED', 'AUTHORIZATION_REQUIRED')
        with patch.object(self.runtime.jobs, '_run', side_effect=finish):
            response = await self.http.post('/api/v1/agent-jobs/source', headers=headers(), json={
                'device_id': 'device-1', 'job_id': 'fresh-linux', 'request': planned().model_dump(), 'program': program(planned()).model_dump()})
            self.assertEqual(response.status_code, 202, response.text)
            await self.runtime.jobs.records['fresh-linux'].task
        record = self.runtime.jobs.records['fresh-linux']
        self.assertEqual(record.request.platform, 'Linux')
        self.assertEqual(record.state, 'REJECTED')
        self.assertEqual(record.reason, 'AUTHORIZATION_REQUIRED')
        self.provider.generate.assert_not_awaited()
        self.websocket.send_json.assert_not_awaited()
