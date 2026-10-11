"""Connection routing, bounded parallel workers, cancellation, and replay limits."""
import asyncio
import threading
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from agent.bridge import Outbox
from agent.jobs import ValidationService
from agent.process import ProcessResult
from agent.validation import CompiledArtifact, ExecutionPolicy, ValidationResult
from tests.support import COMPILER, LOCAL, SimulatedBoundary, job


class ParallelBoundary(SimulatedBoundary):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Queue()
        self.release = asyncio.Event()
        self.active = self.peak = 0

    async def compile(self, *args):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started.put_nowait(args[1])
        try:
            await self.release.wait()
            return await super().compile(*args)
        finally:
            self.active -= 1


class JobRoutingTests(unittest.IsolatedAsyncioTestCase):
    def session(self, names=("a", "b", "c", "d", "e"), boundary=None, **kwargs):
        boundary = boundary or SimulatedBoundary()
        policies = {name: ExecutionPolicy(name, "compute", side_effects=False) for name in names}
        service = ValidationService(COMPILER, policies, boundary, local_platform=LOCAL, **kwargs)
        box = Outbox()
        session = service.connect(box)
        self.addAsyncCleanup(session.close)
        return service, session, box, boundary

    async def event(self, box):
        return await asyncio.wait_for(box.get(), 2)

    async def test_four_independent_validations_start_before_any_finishes(self):
        service, session, box, boundary = self.session(boundary=ParallelBoundary())
        for name in ("a", "b", "c", "d"):
            await session.handle(job(name))
        for _ in range(4):
            await asyncio.wait_for(boundary.started.get(), 1)
        self.assertEqual(boundary.active, 4)
        await session.handle(job("e"))
        self.assertEqual((await self.event(box))["reason"], "RESOURCE_LIMIT")
        self.assertNotIn("e", service.seen_jobs)
        boundary.release.set()
        outcomes = [await self.event(box) for _ in range(4)]
        self.assertEqual({event["job_id"] for event in outcomes}, {"a", "b", "c", "d"})
        self.assertTrue(all(event["status"] == "READY_FOR_EXECUTION" for event in outcomes))

    async def test_local_parallel_limit_queues_work_and_releases_slots(self):
        _, session, box, boundary = self.session(boundary=ParallelBoundary(), max_parallel=2)
        for name in ("a", "b", "c", "d"):
            await session.handle(job(name))
        for _ in range(2):
            await asyncio.wait_for(boundary.started.get(), 1)
        await asyncio.sleep(0)
        self.assertEqual(boundary.active, 2)
        self.assertTrue(boundary.started.empty())
        boundary.release.set()
        for _ in range(4):
            self.assertEqual((await self.event(box))["status"], "READY_FOR_EXECUTION")
        self.assertEqual(boundary.peak, 2)

    async def test_pending_backend_repair_does_not_occupy_worker_slot(self):
        boundary = SimulatedBoundary(["program.c:1:1: error: undeclared", None])
        _, session, box, _ = self.session(boundary=boundary, max_parallel=1)
        await session.handle(job("a"))
        self.assertEqual((await self.event(box))["type"], "repair_requested")
        await session.handle(job("b"))
        self.assertEqual((await self.event(box))["job_id"], "b")
        self.assertIn("a", session.revisions)

    async def test_cancel_active_and_queued_jobs_cleans_workers_and_allows_new_work(self):
        _, session, box, boundary = self.session(boundary=ParallelBoundary(), max_parallel=1)
        await session.handle(job("a"))
        await session.handle(job("b"))
        await asyncio.wait_for(boundary.started.get(), 1)
        await session.handle({"type": "cancel_job", "job_id": "b"})
        self.assertEqual((await self.event(box))["status"], "CANCELLED")
        await session.handle({"type": "cancel_job", "job_id": "a"})
        self.assertEqual((await self.event(box))["status"], "CANCELLED")
        self.assertEqual(boundary.active, 0)
        boundary.release.set()
        await session.handle(job("c"))
        self.assertEqual((await self.event(box))["status"], "READY_FOR_EXECUTION")

    async def test_ready_artifact_cancellation_invalidates_and_deletes_it(self):
        _, session, box, _ = self.session()
        await session.handle(job("a"))
        await self.event(box)
        artifact = session.results["a"].artifact
        await session.handle({"type": "cancel_job", "job_id": "a"})
        self.assertEqual((await self.event(box))["status"], "CANCELLED")
        self.assertFalse(artifact.valid)
        self.assertFalse(artifact.executable.exists())
        self.assertNotIn("a", session.results)

    async def test_malformed_routes_stale_revisions_and_duplicate_ids_are_rejected(self):
        service, session, box, _ = self.session()
        for message, reason in ((job(job_id="invalid/id"), "SOURCE_INVALID"), ({"type": "unknown", "job_id": "a"}, "SOURCE_INVALID"), ({"type": "cancel_job", "job_id": "a", "path": "bad"}, "SOURCE_INVALID"), ({"type": "code_revision", "job_id": "a"}, "STALE_REVISION")):
            await session.handle(message)
            self.assertEqual((await self.event(box))["reason"], reason)
        await session.handle(job("a"))
        await self.event(box)
        await session.handle(job("a"))
        self.assertEqual((await self.event(box))["reason"], "DUPLICATE_JOB")
        service.seen_jobs.update(f"old-{i}" for i in range(256))
        await session.handle(job("b"))
        self.assertEqual((await self.event(box))["reason"], "RESOURCE_LIMIT")

    async def test_internal_validation_failure_is_sanitized_and_next_job_still_works(self):
        _, session, box, _ = self.session()
        with patch("agent.jobs.validate_job", side_effect=RuntimeError("private path and secret")):
            await session.handle(job("a"))
            event = await self.event(box)
            self.assertEqual(event["reason"], "INTERNAL_ERROR")
            self.assertNotIn("private", str(event))
        await session.handle(job("b"))
        self.assertEqual((await self.event(box))["status"], "READY_FOR_EXECUTION")

    async def test_execution_busy_disconnect_and_worker_exception_discard_artifacts(self):
        service, session, box, boundary = self.session(names=("a",))
        service.policies["a"] = replace(service.policies["a"], execute_authorized=True)
        await session.handle(job("a"))
        ready = await self.event(box)
        started = asyncio.Event()
        async def run(*args):
            started.set()
            await asyncio.Event().wait()
        boundary.run = run
        execute = {"type": "execute_job", "job_id": "a", "source_sha256": ready["source_sha256"]}
        await session.handle(execute)
        await asyncio.wait_for(started.wait(), 1)
        await session.handle(execute)
        self.assertEqual((await self.event(box))["reason"], "JOB_BUSY")
        artifact = session.results["a"].artifact
        await session.close()
        self.assertFalse(artifact.valid)
        self.assertFalse(artifact.executable.exists())

        service, session, box, boundary = self.session(names=("a",))
        service.policies["a"] = replace(service.policies["a"], execute_authorized=True)
        await session.handle(job("a"))
        ready = await self.event(box)
        boundary.run = AsyncMock(side_effect=RuntimeError("private worker details"))
        await session.handle({**execute, "source_sha256": ready["source_sha256"]})
        self.assertEqual((await self.event(box))["reason"], "INTERNAL_ERROR")
        self.assertFalse(session.results)

    async def test_executing_ready_job_counts_once_toward_retention_limit(self):
        service, session, box, boundary = self.session()
        ready = {}
        for name in ("a", "b", "c"):
            service.policies[name] = replace(service.policies[name], execute_authorized=True)
            await session.handle(job(name))
            ready[name] = await self.event(box)
        started = asyncio.Queue()
        original_run = boundary.run
        async def run(exe, stdin, limits, capabilities):
            if exe == session.results["a"].artifact.executable or exe == session.results["b"].artifact.executable:
                started.put_nowait(exe)
                await asyncio.Event().wait()
            return await original_run(exe, stdin, limits, capabilities)
        boundary.run = run
        for name in ("a", "b"):
            await session.handle({"type": "execute_job", "job_id": name, "source_sha256": ready[name]["source_sha256"]})
        for _ in range(2):
            await asyncio.wait_for(started.get(), 1)
        await session.handle(job("d"))
        self.assertEqual((await self.event(box))["status"], "READY_FOR_EXECUTION")

    def test_parallel_worker_configuration_is_local_and_bounded(self):
        for count in (0, 5, True, 1.5, "2"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                ValidationService(COMPILER, max_parallel=count)

    async def test_closed_session_cannot_emit_request_repairs_or_execute_stored_result(self):
        _, session, box, _ = self.session()
        session.closed = True
        await session.emit({"private": "must not be sent"})
        self.assertTrue(box.results.empty())

        with self.assertRaises(asyncio.CancelledError):
            await session.request_revision("a", 1, {})
        result = ValidationResult("a", "READY_FOR_EXECUTION")
        await session.execute("a", result)
        self.assertTrue(box.results.empty())

    async def test_close_tolerates_incomplete_result_without_an_artifact(self):
        _, session, _, _ = self.session()
        session.results["a"] = ValidationResult("a", "VALIDATION_FAILED")
        await session.close()
        self.assertFalse(session.results)

    async def test_result_finishing_after_session_invalidation_is_closed(self):
        _, session, box, _ = self.session()
        result = ValidationResult("a", "READY_FOR_EXECUTION")
        async def complete(*args, **kwargs):
            session.closed = True
            return result
        with patch("agent.jobs.validate_job", side_effect=complete), patch.object(result, "close") as close:
            await session.validate("a", job("a"))
        close.assert_called_once()
        self.assertFalse(session.results)
        self.assertTrue(box.results.empty())

    async def test_cancel_when_ready_event_is_backpressured_discards_artifact(self):
        _, session, box, _ = self.session()
        for index in range(64):
            await box.result({"index": index})
        await session.handle(job("a"))
        async def ready():
            while "a" not in session.results:
                await asyncio.sleep(0)
        await asyncio.wait_for(ready(), 1)
        artifact = session.results["a"].artifact
        await session.close()
        self.assertFalse(artifact.valid)
        self.assertFalse(artifact.executable.exists())
        self.assertFalse(session.results)

    async def test_failed_result_publication_does_not_retain_an_invalid_artifact(self):
        _, session, box, _ = self.session()
        with patch.object(box, "result", AsyncMock(side_effect=[RuntimeError("fixture outbox failure"), None])) as emit:
            await session.validate("a", job("a"))
        self.assertFalse(session.results)
        self.assertEqual(emit.call_args.args[0]["reason"], "INTERNAL_ERROR")

    async def test_local_grant_revocation_returns_blocked_and_discards_artifact(self):
        service, session, box, _ = self.session()
        await session.handle(job("a"))
        ready = await self.event(box)
        artifact = session.results["a"].artifact
        del service.policies["a"]
        await session.handle({"type": "execute_job", "job_id": "a", "source_sha256": ready["source_sha256"]})
        self.assertEqual((await self.event(box))["reason"], "AUTHORIZATION_REQUIRED")
        self.assertFalse(artifact.valid)
        self.assertFalse(session.results)

    async def test_disconnect_invalidates_artifact_before_waiting_for_hash_thread_cleanup(self):
        service, session, box, _ = self.session()
        service.policies["a"] = replace(service.policies["a"], execute_authorized=True)
        await session.handle(job("a"))
        ready = await self.event(box)
        artifact = session.results["a"].artifact
        entered, release = threading.Event(), threading.Event()
        def verify():
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("Test reader was never released")
            return True
        with patch.object(CompiledArtifact, "verify", side_effect=verify):
            await session.handle({"type": "execute_job", "job_id": "a", "source_sha256": ready["source_sha256"]})
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            close = asyncio.create_task(session.close())
            await asyncio.sleep(0)
            self.assertFalse(artifact.valid)
            self.assertFalse(close.done())
            release.set()
            await asyncio.wait_for(close, 1)
        self.assertFalse(artifact.executable.exists())
