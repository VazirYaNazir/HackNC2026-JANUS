"""Opt-in actual Gemini -> Docker worker execution; no simulated boundary.

Run JANUS_RUN_LIVE_WORKER=1 uv run --locked python -m unittest
tests.test_live_worker.LiveWorkerTests -v from backend/. The existing backend
.env is loaded without printing credentials or overriding process settings.
Docker must already be running with the repository worker image prepared.
An explicit opt-in fails when either required prerequisite is unavailable;
ordinary test runs skip this class without contacting Gemini or Docker.
JANUS_RUN_LIVE_WORKER_REPAIR=1 additionally spends a live provider call on a
real compile-error repair. No host desktop operations are requested.

The handshake reports the actual host and Docker worker platforms. Linux CI
checks headless transport/containment integration, not macOS/Windows native SDK
behavior or Docker Desktop installation.
"""
import asyncio
import json
import os
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from dotenv import load_dotenv

from app import db
from app.agent_runtime.config import Identity, RuntimeSettings
from app.agent_runtime.llm import GeminiProvider
from app.agent_runtime.models import GeneratedProgram, GenerationRequest
from app.agent_runtime.store import SQLiteStore
from tests.support import RunningServer, headers, registry
from agent.container import create_boundary
from agent.jobs import ValidationService
from agent.validation import ExecutionPolicy


@unittest.skipUnless(os.getenv('JANUS_RUN_LIVE_WORKER') == '1', 'Real Gemini + Docker worker is explicitly opt-in')
class LiveWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
        if not (os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')):
            self.fail('An existing environment-provided Gemini credential is required')
        self.boundary = await asyncio.to_thread(create_boundary)
        self.addCleanup(self.boundary._config.cleanup)
        if not self.boundary.can_build() or not self.boundary.can_run():
            self.fail('Verified Docker worker unavailable: ' + str(self.boundary.reason))
        self.folder = tempfile.TemporaryDirectory(prefix='janus-live-worker-')
        self.addCleanup(self.folder.cleanup)
        location = patch.object(db, 'DB_PATH', Path(self.folder.name) / 'live.db')
        location.start()
        self.addCleanup(location.stop)
        store = SQLiteStore()
        await store.initialize(registry())
        self.addAsyncCleanup(store.close)
        self.provider = GeminiProvider(timeout=20, max_concurrent=2, attempts=2)
        settings = RuntimeSettings(require_tls=False, job_timeout=120, heartbeat_interval=.5, heartbeat_timeout=3, send_timeout=1)
        self.backend = await RunningServer().start(provider=self.provider, settings=settings, store=store)
        self.addAsyncCleanup(self.backend.close)
        self.backend.http.timeout = 35
        self.identity = Identity('device-1', 'owner-1')
        target = self.boundary.execution_target
        host = platform.system()
        self.service = ValidationService(self.boundary.compiler, {}, self.boundary,
            local_platform=('Linux', target['architecture']), host_platform=(host, platform.machine()), max_parallel=2)
        await self.backend.agent(self.service)

    async def draft(self):
        target = self.boundary.execution_target
        description = 'Compute the integer 6 multiplied by 7. Print exactly one JSON object with integer version 1 and integer value 42 followed by a newline. Pure computation only: no files, network, native commands or other side effects.'
        response = await self.backend.http.post('/api/v1/programs/plan', headers=headers(), json={
            'description': description, 'platform': 'Linux', 'architecture': target['architecture'], 'allowed_capabilities': []})
        self.assertEqual(response.status_code, 200, response.text)
        request = GenerationRequest.model_validate(response.json())
        self.assertEqual(request.description, description)
        self.assertEqual(request.capabilities, [])
        self.assertEqual(request.dependencies, [])
        self.assertEqual(request.output_protocol, 'json-v1')
        response = await self.backend.http.post('/api/v1/programs/generate', headers=headers(), json=request.model_dump())
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        program = GeneratedProgram.model_validate(body['program'])
        program.check_scope(request)
        self.assertIn('main', program.source)
        self.assertFalse(self.backend.runtime.jobs.records or self.service.seen_jobs)
        self.assertEqual(await self.backend.runtime.store.program(self.identity, body['program_id'], request), program)
        return request, program

    async def execute_reviewed_source(self, request, program):
        # The test explicitly reviews the exact returned scope and supplies a
        # fresh LOCAL grant; neither /plan nor /generate creates this grant.
        job_id = 'live-' + uuid4().hex
        self.assertEqual(program.capabilities, [])
        self.assertEqual(program.dependencies, [])
        self.service.policies[job_id] = ExecutionPolicy(job_id, request.operation,
            execute_authorized=True, share_outputs=True, side_effects=False, output_protocol='json-v1')
        body = {'device_id': 'device-1', 'job_id': job_id, 'request': request.model_dump(), 'program': program.model_dump()}
        response = await self.backend.http.post('/api/v1/agent-jobs/source', headers=headers(), json=body)
        self.assertEqual(response.status_code, 202, response.text)
        response = await self.backend.http.get('/api/v1/agent-jobs/' + job_id + '/wait', headers=headers(), params={'timeout': 120}, timeout=125)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result['state'], 'COMPLETED', result)
        self.assertEqual(result['result']['returncode'], 0)
        output = json.loads(result['result']['stdout'])
        self.assertEqual(output['version'], 1)
        self.assertEqual(output['value'], 42)
        self.assertFalse(output.get('janus'))
        self.assertGreaterEqual(result['acknowledgments'], 2)
        self.assertEqual(self.backend.runtime.jobs.records[job_id].program.platform, 'Linux')
        self.assertIn(job_id, self.service.seen_jobs)
        replay = await self.backend.http.post('/api/v1/agent-jobs/source', headers=headers(), json=body)
        self.assertEqual((replay.status_code, replay.json()['detail']), (409, 'DUPLICATE_JOB'))
        return result

    async def test_authenticated_plan_generate_review_source_real_compile_preflight_and_execution(self):
        request, program = await self.draft()
        await self.execute_reviewed_source(request, program)

    @unittest.skipUnless(os.getenv('JANUS_RUN_LIVE_WORKER_REPAIR') == '1', 'Real Gemini compile repair is separately opt-in')
    async def test_real_compile_error_sanitization_gemini_repair_and_execution(self):
        request, program = await self.draft()
        failing = GeneratedProgram.model_validate({**program.model_dump(), 'source': 'int main(void) { return janus_missing_fixture; }\n'})
        result = await self.execute_reviewed_source(request, failing)
        self.assertEqual(result['repairs'], 1)
