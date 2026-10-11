"""Orchestration failures and race branches without a native worker."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent_runtime.config import Identity, RuntimeSettings
from app.agent_runtime.connections import AgentConnection, ConnectionHub, PipelineError
from app.agent_runtime.jobs import JobManager
from app.agent_runtime.models import Envelope, Hello, envelope, source_hash
from tests.support import GOOD, program, registry, request


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = RuntimeSettings(require_tls=False, send_timeout=.01, job_timeout=.5)
        self.identity = Identity("device-1", "owner-1")
        self.sent = asyncio.Queue()
        self.auto_cancel = True
        async def send(frame):
            await self.sent.put(frame)
            if frame["type"] == "cancel_job" and self.auto_cancel:
                await self.manager.receive(self.connection, Envelope.model_validate(envelope("validation_result", "device-1", frame["job_id"], "CANCELLED", {"status": "CANCELLED", "reason": "CANCELLED"})))
        self.websocket = SimpleNamespace(send_json=AsyncMock(side_effect=send), close=AsyncMock())
        self.connection = AgentConnection(self.identity, self.websocket, Hello(platform="Darwin", architecture="x86_64", max_workers=4, max_attempts=3, build_boundary_available=True, execution_boundary_available=True), self.settings)
        self.hub = ConnectionHub(registry(), self.settings)
        self.hub.connections["device-1"] = self.connection
        self.provider = SimpleNamespace(generate=AsyncMock(return_value=program()), repair=AsyncMock(return_value=program(source=GOOD + "\n")))
        self.manager = JobManager(self.hub, self.provider, self.settings)

    async def asyncTearDown(self):
        await self.manager.close()

    async def submitted(self, job_id="job"):
        record = await self.manager.submit(self.identity, "device-1", job_id, request())
        async with asyncio.timeout(.5):
            while True:
                frame = await self.sent.get()
                if frame["type"] == "validate_job":
                    self.assertEqual(frame["job_id"], job_id)
                    break
        return record

    async def event(self, kind, state, payload, job_id="job"):
        await self.manager.receive(self.connection, Envelope.model_validate(envelope(kind, "device-1", job_id, state, payload)))

    async def result(self, job_id="job"):
        return await self.manager.wait(self.identity, job_id, .5)

    async def test_retained_result_eviction_does_not_allow_job_id_replay(self):
        self.manager.settings = RuntimeSettings(require_tls=False, max_jobs=1)
        for job_id in ("first", "second"):
            await self.submitted(job_id)
            await self.event("validation_result", "REJECTED", {"status": "REJECTED", "reason": "AUTHORIZATION_REQUIRED"}, job_id)
            await self.result(job_id)
        self.assertEqual(set(self.manager.records), {"second"})
        with self.assertRaisesRegex(PipelineError, "DUPLICATE_JOB"):
            await self.manager.submit(self.identity, "device-1", "first", request())
        with self.assertRaisesRegex(PipelineError, "JOB_NOT_FOUND"):
            self.manager.get(self.identity, "first")

    async def test_global_capacity_active_retention_and_lifetime_ledger_are_bounded(self):
        self.manager.settings = RuntimeSettings(require_tls=False, max_active_jobs=1, max_jobs=1)
        await self.submitted()
        with self.assertRaisesRegex(PipelineError, "JOB_CAPACITY"):
            await self.manager.submit(self.identity, "device-1", "other", request())
        self.manager.settings = RuntimeSettings(require_tls=False, max_active_jobs=4, max_jobs=1)
        with self.assertRaisesRegex(PipelineError, "JOB_CAPACITY"):
            await self.manager.submit(self.identity, "device-1", "other", request())
        self.manager.seen = {str(n): self.identity for n in range(4096)}
        with self.assertRaisesRegex(PipelineError, "SERVICE_CAPACITY"):
            await self.manager.submit(self.identity, "device-1", "other", request())

    async def test_invalid_stage_results_never_execute_or_trigger_unbounded_repair(self):
        cases = [
            ("execution_result", "COMPLETED", {"status": "COMPLETED", "reason": None}),
            ("validation_result", "READY_FOR_EXECUTION", {"status": "READY_FOR_EXECUTION", "reason": None, "source_sha256": "0" * 64, "preflight": "PREFLIGHT_PASSED"}),
            ("repair_requested", "REPAIRING", {"attempt": 2, "reason": "COMPILE_ERROR", "platform": "Darwin", "architecture": "x86_64", "compiler": "gcc", "language_standard": "c11", "source_sha256": source_hash(GOOD), "diagnostics": "compiler: error: syntax error"}),
        ]
        for n, (kind, state, payload) in enumerate(cases):
            job_id = f"invalid-{n}"
            await self.submitted(job_id)
            await self.event(kind, state, payload, job_id)
            result = await self.result(job_id)
            self.assertEqual(result["state"], "FAILED")
        self.provider.repair.assert_not_awaited()

    async def test_duplicate_repair_request_is_rejected_after_first_correction(self):
        await self.submitted()
        payload = {"attempt": 1, "reason": "COMPILE_ERROR", "platform": "Darwin", "architecture": "x86_64", "compiler": "gcc", "language_standard": "c11", "source_sha256": source_hash(GOOD), "diagnostics": "compiler: error: syntax error"}
        await self.event("repair_requested", "REPAIRING", payload)
        self.assertEqual((await self.sent.get())["type"], "code_revision")
        await self.event("repair_requested", "REPAIRING", payload)
        self.assertEqual((await self.result())["state"], "FAILED")
        self.provider.repair.assert_awaited_once()

    async def test_validation_event_after_execution_started_is_rejected(self):
        await self.submitted()
        payload = {"status": "READY_FOR_EXECUTION", "reason": None, "source_sha256": source_hash(GOOD), "preflight": "PREFLIGHT_PASSED"}
        await self.event("validation_result", "READY_FOR_EXECUTION", payload)
        self.assertEqual((await self.sent.get())["type"], "execute_job")
        await self.event("validation_result", "READY_FOR_EXECUTION", payload)
        self.assertEqual((await self.result())["state"], "FAILED")

    async def test_event_shape_state_and_queue_limits_disconnect_instead_of_misrouting(self):
        record = await self.submitted()
        for kind, state, payload in (("other", "ACCEPTED", {}), ("job_ack", "WRONG", {"accepted_type": "validate_job"}), ("validation_result", "WRONG", {"status": "CANCELLED", "reason": "CANCELLED"})):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                await self.event(kind, state, payload)
        for _ in range(16):
            await self.event("job_ack", "ACCEPTED", {"accepted_type": "validate_job"})
        with self.assertRaisesRegex(ValueError, "Too many job events"):
            await self.event("job_ack", "ACCEPTED", {"accepted_type": "validate_job"})
        self.assertEqual(record.events.qsize(), 16)

    async def test_cancel_delivery_failure_is_not_reported_as_confirmed_cleanup(self):
        record = await self.submitted()
        self.websocket.send_json.side_effect = OSError("private transport details")
        result = await self.manager.cancel(self.identity, "job")
        self.assertEqual(result["state"], "CANCELLED")
        self.assertFalse(result["cancellation_confirmed"])
        self.assertNotIn("private", str(result))
        self.assertEqual(await self.manager.cancel(self.identity, "job"), record.view())

    async def test_missing_cancel_reply_times_out_without_false_confirmation(self):
        await self.submitted()
        self.auto_cancel = False
        result = await self.manager.cancel(self.identity, "job")
        self.assertFalse(result["cancellation_confirmed"])

    async def test_send_failure_finishes_disconnected_and_hides_raw_details(self):
        self.websocket.send_json.side_effect = OSError("secret-transport")
        record = await self.manager.submit(self.identity, "device-1", "send-fail", request())
        await record.done.wait()
        self.assertEqual(record.state, "DISCONNECTED")
        self.assertNotIn("secret", str(record.view()))

    async def test_job_cleanup_waits_for_already_terminal_tasks_and_shutdown_blocks_new_work(self):
        await self.submitted()
        self.auto_cancel = False
        cancelling = asyncio.create_task(self.manager.cancel(self.identity, "job"))
        await asyncio.sleep(0)
        await self.manager.close()
        await cancelling
        self.assertTrue(self.manager.records["job"].task.done())
        with self.assertRaisesRegex(PipelineError, "SERVICE_CAPACITY"):
            await self.manager.submit(self.identity, "device-1", "new", request())

    async def test_same_device_connection_loss_affects_only_that_connection(self):
        record = await self.submitted()
        unrelated = AgentConnection(Identity("device-2", "owner-2"), self.websocket, self.connection.hello, self.settings)
        await self.manager.disconnected(unrelated)
        self.assertFalse(record.done.is_set())
        self.connection.closed = True
        await self.manager.disconnected(self.connection)
        self.assertEqual(record.state, "DISCONNECTED")

    async def test_evicted_result_tombstones_accept_only_the_original_device_scope(self):
        self.manager.settings = RuntimeSettings(require_tls=False, max_jobs=1)
        await self.submitted("old")
        await self.event("validation_result", "REJECTED", {"status": "REJECTED", "reason": "AUTHORIZATION_REQUIRED"}, "old")
        await self.result("old")
        await self.submitted("new")
        await self.event("validation_result", "CANCELLED", {"status": "CANCELLED", "reason": "CANCELLED"}, "old")
        self.assertFalse(self.manager.records["new"].done.is_set())
        frame = Envelope.model_validate(envelope("execution_result", "device-2", "old", "COMPLETED", {"status": "COMPLETED", "reason": None}))
        attacker = AgentConnection(Identity("device-2", "owner-2"), self.websocket, self.connection.hello, self.settings)
        with self.assertRaises(ValueError):
            await self.manager.receive(attacker, frame)

    async def test_jobs_still_cleaning_up_count_toward_capacity(self):
        self.manager.settings = RuntimeSettings(require_tls=False, max_active_jobs=1, send_timeout=.01)
        record = await self.submitted()
        self.auto_cancel = False
        record.finish("CANCELLED", "CANCELLED")
        record.task.cancel()
        await asyncio.sleep(0)
        with self.assertRaisesRegex(PipelineError, "JOB_CAPACITY"):
            await self.manager.submit(self.identity, "device-1", "new", request())

    async def test_connection_send_gate_and_deadline_are_bounded(self):
        self.connection.closed = True
        with self.assertRaisesRegex(PipelineError, "AGENT_DISCONNECTED"):
            await self.connection.send("cancel_job", "job", "CANCELLED", {})
        self.connection.closed = False
        async def stalled(_):
            await asyncio.sleep(1)
        self.websocket.send_json.side_effect = stalled
        with self.assertRaisesRegex(PipelineError, "AGENT_SEND_FAILED"):
            await self.connection.send("cancel_job", "job", "CANCELLED", {})

    async def test_cancelled_database_admission_is_completed_without_dispatching(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def create(*args):
            started.set(); await release.wait(); return True
        self.manager.store = SimpleNamespace(create_job=AsyncMock(side_effect=create), save_job=AsyncMock())
        submitting = asyncio.create_task(self.manager.submit(self.identity, "device-1", "admission", request()))
        await started.wait(); submitting.cancel(); await asyncio.sleep(0); submitting.cancel(); await asyncio.sleep(0)
        self.assertFalse(submitting.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError): await submitting
        self.assertNotIn("admission", self.manager.records)
        self.assertEqual(self.manager.store.save_job.await_args.args[0]["reason"], "SUBMISSION_CANCELLED")
        self.provider.generate.assert_not_awaited()

    async def test_shutdown_waits_for_database_admission_and_persists_cancellation(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def create(*args):
            started.set(); await release.wait(); return True
        self.manager.store = SimpleNamespace(create_job=AsyncMock(side_effect=create), save_job=AsyncMock())
        submitting = asyncio.create_task(self.manager.submit(self.identity, "device-1", "shutdown-admission", request()))
        await started.wait()
        closing = asyncio.create_task(self.manager.close()); await asyncio.sleep(0)
        self.assertFalse(closing.done())
        release.set(); await closing
        self.assertTrue(submitting.cancelled())
        self.assertFalse(self.manager.admissions)
        self.provider.generate.assert_not_awaited()

    async def test_terminal_database_publication_survives_repeated_cancellation(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def save(view, terminal):
            if terminal:
                started.set(); await release.wait()
        self.manager.store = SimpleNamespace(create_job=AsyncMock(return_value=True), save_job=AsyncMock(side_effect=save))
        record = await self.submitted()
        await self.event("validation_result", "REJECTED", {"status": "REJECTED", "reason": "AUTHORIZATION_REQUIRED"})
        await started.wait(); record.task.cancel(); await asyncio.sleep(0); record.task.cancel(); await asyncio.sleep(0)
        self.assertFalse(record.task.done())
        release.set(); await record.task
        self.assertEqual(record.state, "REJECTED")

    async def test_persistent_ledger_is_bounded_in_memory_and_late_events_use_scoped_database_lookup(self):
        self.manager.seen = {str(n): self.identity for n in range(4096)}
        self.manager.store = SimpleNamespace(create_job=AsyncMock(return_value=True), save_job=AsyncMock(), job=AsyncMock(return_value={"state": "COMPLETED"}))
        await self.submitted()
        self.assertEqual(len(self.manager.seen), 4096)
        await self.event("job_ack", "ACCEPTED", {"accepted_type": "validate_job"}, "old-database-job")
        self.manager.store.job.assert_awaited_once_with(self.identity, "old-database-job")
