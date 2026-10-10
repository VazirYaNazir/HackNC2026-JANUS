"""Every execution gate, native worker failure, private input, and single-use race."""
import asyncio
from dataclasses import replace
import unittest
import threading
from unittest.mock import AsyncMock, patch

from agent.executor import execute_artifact
from agent.isolation import BoundaryUnavailable, ResourceLimitExceeded, ResourceLimits
from agent.process import ProcessResult
from agent.validation import CompiledArtifact, ExecutionPolicy, ValidationResult, validate_job
from tests.support import COMPILER, LOCAL, SimulatedBoundary, job


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def ready(self, *, capabilities=frozenset()):
        boundary = SimulatedBoundary()
        policy = ExecutionPolicy("job-123", "compute", execute_authorized=True, side_effects=False, capabilities=capabilities)
        result = await validate_job(job(capabilities=list(capabilities)), COMPILER, AsyncMock(), policy=policy, boundary=boundary, local_platform=LOCAL)
        self.addCleanup(result.close)
        boundary.runs.clear()
        return result, boundary, policy

    async def test_failed_or_incomplete_validation_never_launches(self):
        for status in ("REJECTED", "VALIDATION_FAILED", "VALIDATION_BLOCKED", "READY_FOR_EXECUTION"):
            boundary = SimulatedBoundary()
            result = await execute_artifact(ValidationResult("job-123", status), boundary, ExecutionPolicy("job-123", "compute"))
            self.assertEqual(result.reason, "VALIDATION_REQUIRED")
            self.assertFalse(boundary.runs)

    async def test_policy_revocation_and_artifact_scope_change_deny_execution(self):
        result, boundary, policy = await self.ready()
        self.assertEqual((await execute_artifact(result, boundary, replace(policy, execute_authorized=False))).reason, "AUTHORIZATION_REQUIRED")
        result.artifact.job = replace(result.artifact.job, operation="other")
        self.assertEqual((await execute_artifact(result, boundary, policy)).reason, "CAPABILITY_DENIED")
        self.assertFalse(boundary.runs)

    async def test_runtime_boundary_requires_every_requested_capability(self):
        result, boundary, policy = await self.ready(capabilities=frozenset({"files.write"}))
        boundary.run_capabilities = frozenset()
        outcome = await execute_artifact(result, boundary, policy)
        self.assertEqual(outcome.reason, "EXECUTION_BOUNDARY_UNAVAILABLE")
        self.assertFalse(boundary.runs)

    async def test_invalid_private_input_is_rejected_before_consuming_authorization(self):
        result, boundary, policy = await self.ready()
        for stdin in (None, b"private", "x" * 65537, "\ud800"):
            with self.subTest(stdin=repr(stdin)[:40]):
                self.assertEqual((await execute_artifact(result, boundary, policy, stdin)).reason, "INPUT_INVALID")
                self.assertFalse(result.artifact.consumed)
        self.assertFalse(boundary.runs)

    async def test_worker_failures_have_distinct_status_and_never_trigger_repair(self):
        cases = [(BoundaryUnavailable("private detail"), "EXECUTION_BLOCKED", "EXECUTION_BOUNDARY_UNAVAILABLE"),
                 (TimeoutError("private detail"), "EXECUTION_FAILED", "EXECUTION_TIMEOUT"),
                 (ResourceLimitExceeded("private detail"), "EXECUTION_FAILED", "RESOURCE_LIMIT"),
                 (OSError("private path"), "EXECUTION_FAILED", "LOAD_FAILED")]
        for error, status, reason in cases:
            with self.subTest(reason=reason):
                result, boundary, policy = await self.ready()
                boundary.executions = [error]
                outcome = await execute_artifact(result, boundary, policy)
                self.assertEqual((outcome.status, outcome.reason), (status, reason))
                self.assertNotIn("private", str(outcome.event("job-123", True)))
                self.assertTrue(result.artifact.consumed)

    async def test_nonzero_exit_and_oversize_output_do_not_report_success(self):
        for process, reason in ((ProcessResult(7, "result", "stderr"), "NONZERO_EXIT"), (ProcessResult(0, "x" * 65537), "RESOURCE_OR_OUTPUT_LIMIT"), (ProcessResult(0, output_limited=True), "RESOURCE_OR_OUTPUT_LIMIT")):
            result, boundary, policy = await self.ready()
            boundary.executions = [process]
            outcome = await execute_artifact(result, boundary, policy)
            self.assertEqual((outcome.status, outcome.reason), ("EXECUTION_FAILED", reason))
            if reason == "NONZERO_EXIT":
                self.assertEqual((outcome.returncode, outcome.stdout, outcome.stderr), (7, "result", "stderr"))

    async def test_execution_deadline_cancels_worker_and_consumes_authorization(self):
        result, boundary, policy = await self.ready()
        policy = replace(policy, limits=ResourceLimits(run_seconds=0.01))
        result.artifact.policy = policy
        cancelled = asyncio.Event()
        async def run(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        boundary.run = run
        self.assertEqual((await execute_artifact(result, boundary, policy)).reason, "EXECUTION_TIMEOUT")
        self.assertTrue(cancelled.is_set())
        self.assertTrue(result.artifact.consumed)

    async def test_concurrent_execution_requests_launch_artifact_only_once(self):
        result, boundary, policy = await self.ready()
        started, release = asyncio.Event(), asyncio.Event()
        async def run(*args):
            started.set()
            await release.wait()
            return ProcessResult(0)
        boundary.run = AsyncMock(side_effect=run)
        first = asyncio.create_task(execute_artifact(result, boundary, policy))
        await asyncio.wait_for(started.wait(), 1)
        second = await execute_artifact(result, boundary, policy)
        self.assertEqual(second.reason, "ARTIFACT_INVALID_OR_CONSUMED")
        release.set()
        self.assertEqual((await first).status, "COMPLETED")
        boundary.run.assert_awaited_once()

    async def test_simultaneous_threaded_hash_checks_cannot_double_claim_authorization(self):
        result, boundary, policy = await self.ready()
        barrier = threading.Barrier(2)
        def verify():
            barrier.wait(timeout=2)
            return True
        boundary.run = AsyncMock(return_value=ProcessResult(0))
        with patch.object(CompiledArtifact, "verify", side_effect=verify):
            outcomes = await asyncio.wait_for(asyncio.gather(execute_artifact(result, boundary, policy), execute_artifact(result, boundary, policy)), 3)
        self.assertEqual({outcome.status for outcome in outcomes}, {"COMPLETED", "EXECUTION_BLOCKED"})
        boundary.run.assert_awaited_once()

    async def test_slow_integrity_read_does_not_block_event_loop(self):
        result, boundary, policy = await self.ready()
        entered, release = threading.Event(), threading.Event()
        def verify():
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("Event loop could not release the hash worker")
            return True
        with patch.object(CompiledArtifact, "verify", side_effect=verify):
            task = asyncio.create_task(execute_artifact(result, boundary, policy))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            self.assertFalse(task.done())
            release.set()
            self.assertEqual((await asyncio.wait_for(task, 1)).status, "COMPLETED")

    async def test_disappearing_or_growing_binary_fails_integrity_check(self):
        result, boundary, policy = await self.ready()
        with patch.object(type(result.artifact.executable), "read_bytes", side_effect=OSError("fixture disappeared")):
            self.assertEqual((await execute_artifact(result, boundary, policy)).reason, "ARTIFACT_INVALID_OR_CONSUMED")
        with result.artifact.executable.open("wb") as file:
            file.truncate(policy.limits.disk_bytes + 1)
        self.assertEqual((await execute_artifact(result, boundary, policy)).reason, "ARTIFACT_INVALID_OR_CONSUMED")
        self.assertFalse(boundary.runs)
