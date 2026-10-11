import asyncio
import json
import statistics
import time
import unittest
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from agent.compiler import CCompiler
from agent.isolation import ResourceLimits
from agent.jobs import ValidationService
from agent.validation import ExecutionPolicy, Fixture
from agent.transport import packet

from app.agent_runtime.config import RuntimeSettings
from tests.support import BAD, BAD_TWO, BAD_THREE, FakeSDK, GOOD, RunningServer, TrustedFixtureBoundary, headers, program, request


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = await RunningServer().start()
        self.boundaries = []

    async def asyncTearDown(self):
        await self.backend.close()
        for boundary in self.boundaries:
            self.assertEqual(boundary.active, 0)
            self.assertTrue(all(not directory.exists() for directory in boundary.directories))

    async def laptop(self, job_ids, *, device=1, share=False, compile_delay=0, run_delay=0, policies=None, workers=4):
        boundary = TrustedFixtureBoundary(compile_delay=compile_delay, run_delay=run_delay)
        self.boundaries.append(boundary)
        policies = policies if policies is not None else {
            job_id: ExecutionPolicy(job_id, "compute", execute_authorized=True, share_outputs=share,
                                    side_effects=False, output_protocol="json-v1",
                                    fixtures=(Fixture(stdout='{"version":1,"value":42}\n'),)) for job_id in job_ids}
        service = ValidationService(boundary.compiler, policies, boundary, local_platform=("Darwin", "x86_64"), max_parallel=workers)
        websocket, task = await self.backend.agent(service, device)
        return boundary, service, websocket, task

    async def test_complete_request_gemini_validation_real_fixture_execution_result(self):
        await self.laptop(["e2e"], share=True)
        submitted = await self.backend.generate("e2e")
        self.assertEqual(submitted.status_code, 202, submitted.text)
        result = await self.backend.result("e2e")
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(json.loads(result["result"]["stdout"]), {"version": 1, "value": 42})
        self.assertEqual(result["result"]["returncode"], 0)
        self.assertEqual(result["device_id"], "device-1")
        self.assertGreaterEqual(result["acknowledgments"], 2)
        self.assertEqual(len(self.backend.sdk.calls), 1)

    async def test_private_outputs_remain_local_and_no_source_or_paths_leak_in_result(self):
        await self.laptop(["private"])
        self.assertEqual((await self.backend.generate("private")).status_code, 202)
        result = await self.backend.result("private")
        self.assertEqual(result["state"], "COMPLETED")
        self.assertEqual(set(result["result"]), {"status", "reason"})
        self.assertNotIn(GOOD, json.dumps(result))
        self.assertNotIn("janus-build-", json.dumps(result))

    async def test_compile_failure_repair_preserves_scope_and_sanitizes_diagnostics(self):
        self.backend.sdk.script["repair"] = [BAD, GOOD]
        await self.laptop(["repair-job"], share=True)
        self.assertEqual((await self.backend.generate("repair-job", description="repair")).status_code, 202)
        result = await self.backend.result("repair-job")
        self.assertEqual(result["state"], "COMPLETED", result)
        self.assertEqual(result["repairs"], 1)
        self.assertEqual(len(self.backend.sdk.calls), 2)
        diagnostic = self.backend.sdk.calls[1]["diagnostics"]
        self.assertIn("undeclared identifier", diagnostic)
        self.assertNotIn("fixture_missing", diagnostic)
        self.assertNotIn("janus-build-", diagnostic)

    async def test_compilation_attempts_and_gemini_repairs_are_bounded(self):
        self.backend.sdk.script["exhaust"] = [BAD, BAD_TWO, BAD_THREE, GOOD]
        await self.laptop(["exhaust-job"])
        self.assertEqual((await self.backend.generate("exhaust-job", description="exhaust")).status_code, 202)
        result = await self.backend.result("exhaust-job")
        self.assertEqual(result["state"], "VALIDATION_FAILED", result)
        self.assertEqual(result["reason"], "ATTEMPTS_EXHAUSTED")
        self.assertEqual(result["result"]["attempts"], 3)
        self.assertEqual(result["repairs"], 2)
        self.assertEqual(len(self.backend.sdk.calls), 3)

    async def test_authentication_role_device_and_owner_scope(self):
        await self.laptop(["owned"])
        with self.assertRaises(InvalidStatus):
            await connect(f"ws://127.0.0.1:{self.backend.port}/ws/agents/device-1",
                          additional_headers={"Authorization": "Bearer wrong"})
        with self.assertRaises(InvalidStatus):
            await connect(f"ws://127.0.0.1:{self.backend.port}/ws/agents/device-2",
                          additional_headers={"Authorization": "Bearer agent-1-" + "a" * 32})
        response = await self.backend.http.post("/api/v1/agent-jobs/generate", json={"device_id": "device-1", "job_id": "bad", "request": request().model_dump()})
        self.assertEqual(response.status_code, 401)
        response = await self.backend.http.post("/api/v1/agent-jobs/generate", headers=headers(1),
                                               json={"device_id": "device-2", "job_id": "bad", "request": request().model_dump()})
        self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.backend.generate("owned")).status_code, 202)
        foreign = await self.backend.http.get("/api/v1/agent-jobs/owned", headers=headers(2))
        self.assertEqual(foreign.status_code, 404)
        cancelled = await self.backend.http.post("/api/v1/agent-jobs/owned/cancel", headers=headers(2))
        self.assertEqual(cancelled.status_code, 404)
        self.assertEqual((await self.backend.result("owned"))["state"], "COMPLETED")

    async def test_multiple_agents_and_eight_concurrent_jobs_report_latency(self):
        ids = [f"parallel-{n}" for n in range(8)]
        first, *_ = await self.laptop(ids[:4], share=True, compile_delay=.05)
        second, *_ = await self.laptop(ids[4:], share=True, device=2, compile_delay=.05)
        started = time.perf_counter()
        submissions = await asyncio.gather(*(self.backend.generate(job, device=1 if n < 4 else 2) for n, job in enumerate(ids)))
        self.assertTrue(all(response.status_code == 202 for response in submissions), [response.text for response in submissions])
        results = await asyncio.gather(*(self.backend.result(job, device=1 if n < 4 else 2) for n, job in enumerate(ids)))
        self.assertEqual({result["job_id"] for result in results}, set(ids))
        self.assertTrue(all(result["state"] == "COMPLETED" for result in results))
        self.assertTrue(all(result["device_id"] == ("device-1" if n < 4 else "device-2") for n, result in enumerate(results)))
        self.assertGreaterEqual(first.peak, 2)
        self.assertGreaterEqual(second.peak, 2)
        self.assertLessEqual(first.peak, 4)
        self.assertLessEqual(second.peak, 4)
        durations = sorted(result["elapsed_ms"] for result in results)
        metrics = {"jobs": 8, "devices": 2, "wall_ms": round(1000 * (time.perf_counter() - started), 3),
                   "median_job_ms": statistics.median(durations), "max_job_ms": max(durations),
                   "peak_compile_workers_per_device": [first.peak, second.peak], "gemini_requests": len(self.backend.sdk.calls)}
        Path("/tmp/janus-pipeline-benchmark.json").write_text(json.dumps(metrics, indent=2) + "\n")
        self.assertEqual(metrics["gemini_requests"], 8)

    async def test_threaded_compilation_does_not_block_heartbeats(self):
        boundary, *_ = await self.laptop(["slow"], compile_delay=.2)
        self.assertEqual((await self.backend.generate("slow")).status_code, 202)
        await asyncio.wait_for(boundary.started.wait(), 1)
        connection = self.backend.runtime.hub.get("device-1")
        await asyncio.wait_for(connection.pong.wait(), .5)
        self.assertFalse(self.backend.runtime.jobs.records["slow"].done.is_set())
        self.assertEqual((await self.backend.result("slow"))["state"], "COMPLETED")

    async def test_active_and_queued_cancellation_confirm_local_cleanup(self):
        boundary, *_ = await self.laptop(["active", "queued"], compile_delay=.3, workers=1)
        self.assertEqual((await self.backend.generate("active")).status_code, 202)
        await asyncio.wait_for(boundary.started.wait(), 1)
        self.assertEqual((await self.backend.generate("queued")).status_code, 202)
        queued = await self.backend.http.post("/api/v1/agent-jobs/queued/cancel", headers=headers())
        self.assertEqual(queued.status_code, 200)
        self.assertEqual(queued.json()["state"], "CANCELLED")
        active = await self.backend.http.post("/api/v1/agent-jobs/active/cancel", headers=headers())
        self.assertEqual(active.status_code, 200)
        self.assertTrue(active.json()["cancellation_confirmed"])
        self.assertEqual(boundary.active, 0)

    async def test_local_permissions_and_default_boundary_cannot_be_bypassed(self):
        boundary, *_ = await self.laptop([], policies={})
        self.assertEqual((await self.backend.generate("unapproved")).status_code, 202)
        result = await self.backend.result("unapproved")
        self.assertEqual((result["state"], result["reason"]), ("REJECTED", "AUTHORIZATION_REQUIRED"))
        self.assertEqual(boundary.directories, [])
        service = ValidationService(CCompiler("clang", "/not-executed", "Darwin"),
                                    {"blocked": ExecutionPolicy("blocked", "compute", output_protocol="json-v1")},
                                    local_platform=("Darwin", "x86_64"))
        await self.backend.agent(service, device=2)
        self.assertEqual((await self.backend.generate("blocked", device=2)).status_code, 202)
        result = await self.backend.result("blocked", device=2)
        self.assertEqual((result["state"], result["reason"]), ("VALIDATION_BLOCKED", "BUILD_BOUNDARY_UNAVAILABLE"))

    async def test_execution_timeout_does_not_trigger_gemini_repair(self):
        policy = ExecutionPolicy("run-timeout", "compute", execute_authorized=True, output_protocol="json-v1",
                                 limits=ResourceLimits(run_seconds=.03))
        await self.laptop(["run-timeout"], policies={"run-timeout": policy}, run_delay=.2)
        self.assertEqual((await self.backend.generate("run-timeout")).status_code, 202)
        result = await self.backend.result("run-timeout")
        self.assertEqual((result["state"], result["reason"]), ("EXECUTION_FAILED", "EXECUTION_TIMEOUT"))
        self.assertEqual(len(self.backend.sdk.calls), 1)

    async def test_source_submission_uses_same_validation_without_gemini(self):
        await self.laptop(["source"])
        response = await self.backend.http.post("/api/v1/agent-jobs/source", headers=headers(),
                                               json={"device_id": "device-1", "job_id": "source", "request": request().model_dump(), "program": program().model_dump()})
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["state"], "QUEUED")
        self.assertEqual((await self.backend.result("source"))["state"], "COMPLETED")
        self.assertEqual(len(self.backend.sdk.calls), 0)
        offline = await self.backend.http.post("/api/v1/agent-jobs/source", headers=headers(2),
                                               json={"device_id": "device-2", "job_id": "offline-source", "request": request().model_dump(), "program": program().model_dump()})
        self.assertEqual(offline.status_code, 503)

    async def test_disconnect_fails_affected_jobs_and_reconnect_never_replays_them(self):
        boundary, service, websocket, task = await self.laptop(["disconnect", "new"], compile_delay=.2)
        self.assertEqual((await self.backend.generate("disconnect")).status_code, 202)
        await asyncio.wait_for(boundary.started.wait(), 1)
        await websocket.close()
        result = await self.backend.result("disconnect")
        self.assertEqual(result["state"], "DISCONNECTED")
        await asyncio.wait_for(task, 2)
        await self.backend.agent(service)
        self.assertEqual((await self.backend.generate("disconnect")).status_code, 409)
        self.assertEqual((await self.backend.generate("new")).status_code, 202)
        self.assertEqual((await self.backend.result("new"))["state"], "COMPLETED")
        self.assertEqual(len(self.backend.sdk.calls), 2)

    async def test_provider_failure_and_backend_deadline_return_structured_status(self):
        self.backend.sdk.script["repeat"] = [BAD, BAD]
        await self.laptop(["repeat", "deadline"])
        self.assertEqual((await self.backend.generate("repeat", description="repeat")).status_code, 202)
        result = await self.backend.result("repeat")
        self.assertEqual((result["state"], result["reason"]), ("PROVIDER_ERROR", "REPEATED_SOURCE"))
        self.backend.runtime.jobs.settings = RuntimeSettings(require_tls=False, job_timeout=.02, send_timeout=.5)
        self.backend.sdk.delay = .2
        self.assertEqual((await self.backend.generate("deadline")).status_code, 202)
        result = await self.backend.result("deadline")
        self.assertEqual((result["state"], result["reason"]), ("TIMED_OUT", "JOB_TIMEOUT"))

    async def test_offline_target_mismatch_capacity_duplicate_and_invalid_requests(self):
        self.assertEqual((await self.backend.generate("offline")).status_code, 503)
        _, _, websocket, _ = await self.laptop([f"cap-{n}" for n in range(4)], compile_delay=.3)
        for n in range(4):
            self.assertEqual((await self.backend.generate(f"cap-{n}")).status_code, 202)
        self.assertEqual((await self.backend.generate("over-capacity")).status_code, 429)
        self.assertEqual((await self.backend.generate("cap-0")).status_code, 409)
        req = request().model_dump()
        for change, expected in (({"platform": "Windows"}, 422), ({"capabilities": ["unknown"]}, 422), ({"execute_authorized": True}, 422)):
            response = await self.backend.http.post("/api/v1/agent-jobs/generate", headers=headers(),
                                                   json={"device_id": "device-1", "job_id": "invalid", "request": {**req, **change}})
            self.assertEqual(response.status_code, expected, response.text)
        await websocket.close()

    async def raw_agent(self, device=1):
        websocket = await connect(f"ws://127.0.0.1:{self.backend.port}/ws/agents/device-{device}",
                                  additional_headers={"Authorization": f"Bearer agent-{device}-" + "a" * 32})
        self.backend.sockets.append(websocket)
        await websocket.send(json.dumps(packet(f"device-{device}", "agent_hello", None, "CONNECTED", {
            "platform": "Darwin", "architecture": "x86_64", "max_workers": 4, "max_attempts": 3,
            "build_boundary_available": False, "execution_boundary_available": False})))
        return websocket

    async def test_duplicate_connections_do_not_replace_the_registered_agent(self):
        await self.laptop(["original"])
        original = self.backend.runtime.hub.get("device-1")
        duplicate = await self.raw_agent()
        with self.assertRaises(ConnectionClosed):
            await duplicate.recv()
        self.assertIs(self.backend.runtime.hub.get("device-1"), original)
        self.assertEqual((await self.backend.generate("original")).status_code, 202)
        self.assertEqual((await self.backend.result("original"))["state"], "COMPLETED")

    async def test_malformed_binary_and_wrong_device_messages_close_connection(self):
        for raw in ("not-json", b"binary", json.dumps(packet("wrong-device", "pong", None, "CONNECTED", {"nonce": "bad"}))):
            websocket = await self.raw_agent()
            self.assertEqual(json.loads(await websocket.recv())["type"], "connection_ready")
            await websocket.send(raw)
            with self.assertRaises(ConnectionClosed):
                await asyncio.wait_for(websocket.recv(), 1)

    async def test_result_cannot_be_routed_by_another_authenticated_device(self):
        await self.laptop(["victim"], compile_delay=.15)
        attacker = await self.raw_agent(device=2)
        await attacker.recv()
        self.assertEqual((await self.backend.generate("victim")).status_code, 202)
        await attacker.send(json.dumps(packet("device-2", "execution_result", "victim", "COMPLETED", {"status": "COMPLETED", "reason": None})))
        with self.assertRaises(ConnectionClosed) as closed:
            async with asyncio.timeout(2):
                while True:
                    # A heartbeat can already be queued while the HTTP request runs.
                    # Answer it so heartbeat expiry cannot satisfy this assertion.
                    message = json.loads(await attacker.recv())
                    self.assertEqual(message["type"], "ping")
                    await attacker.send(json.dumps(packet("device-2", "pong", None, "CONNECTED", message["payload"])))
        self.assertEqual(closed.exception.rcvd.code, 1008)
        self.assertEqual((await self.backend.result("victim"))["state"], "COMPLETED")

    async def test_application_heartbeat_detects_an_unresponsive_agent(self):
        self.backend.runtime.hub.settings = RuntimeSettings(require_tls=False, heartbeat_interval=.01, heartbeat_timeout=.03)
        websocket = await self.raw_agent()
        await websocket.recv()
        self.assertEqual(json.loads(await websocket.recv())["type"], "ping")
        with self.assertRaises(ConnectionClosed):
            await asyncio.wait_for(websocket.recv(), 1)

    async def test_tls_is_required_when_deployed_and_wait_timeout_does_not_cancel_job(self):
        self.backend.runtime.hub.settings = RuntimeSettings(require_tls=True)
        with self.assertRaises(InvalidStatus):
            await self.raw_agent()
        self.backend.runtime.hub.settings = RuntimeSettings(require_tls=False, heartbeat_interval=.04, heartbeat_timeout=.5)
        await self.laptop(["wait"], compile_delay=.15)
        self.assertEqual((await self.backend.generate("wait")).status_code, 202)
        response = await self.backend.http.get("/api/v1/agent-jobs/wait/wait", headers=headers(), params={"timeout": .001})
        self.assertEqual(response.status_code, 504)
        self.assertEqual((await self.backend.result("wait"))["state"], "COMPLETED")
