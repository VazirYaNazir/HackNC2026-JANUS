"""Every stage failure must discard binaries and avoid inappropriate AI retries."""
import asyncio
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent.compiler import CCompiler, CompileResult
from agent.isolation import BoundaryUnavailable, ResourceLimitExceeded
from agent.process import ProcessResult
from agent.validation import CompiledArtifact, ExecutionPolicy, Fixture, validate_job
from tests.support import COMPILER, LOCAL, SimulatedBoundary, job


class ValidationFailureTests(unittest.IsolatedAsyncioTestCase):
    async def validate(self, *, message=None, policy=None, boundary=None, compiler=COMPILER, local=LOCAL):
        repair = AsyncMock()
        boundary = boundary or SimulatedBoundary()
        policy = policy or ExecutionPolicy("job-123", "compute", side_effects=False)
        result = await validate_job(message or job(), compiler, repair, policy=policy, boundary=boundary, local_platform=local)
        self.addCleanup(result.close)
        return result, boundary, repair

    async def test_missing_local_grant_and_scope_expansion_never_reach_worker(self):
        boundary, repair = SimulatedBoundary(), AsyncMock()
        result = await validate_job(job(), COMPILER, repair, boundary=boundary, local_platform=LOCAL)
        self.assertEqual(result.reason, "AUTHORIZATION_REQUIRED")
        for message in (job(operation="other"), job(capabilities=["admin"]), job(dependencies=["mac-system"])):
            result, boundary, repair = await self.validate(message=message)
            self.assertEqual(result.reason, "CAPABILITY_DENIED")
            self.assertFalse(boundary.sources)
            repair.assert_not_called()

    async def test_nonobject_and_invalid_job_ids_produce_non_sensitive_rejection(self):
        for message in (None, [], "source", job(job_id=None), job(job_id="private/path"), job(job_id="x" * 129)):
            with self.subTest(message=str(message)[:40]):
                boundary, repair = SimulatedBoundary(), AsyncMock()
                result = await validate_job(message, COMPILER, repair, boundary=boundary, local_platform=LOCAL)
                self.assertEqual((result.status, result.reason, result.job_id), ("REJECTED", "SOURCE_INVALID", None))
                self.assertFalse(boundary.sources)
                repair.assert_not_called()

    async def test_local_platform_compiler_and_dependency_mismatch_are_not_repaired(self):
        cases = [(job(), COMPILER, ("Linux", "arm64"), "PLATFORM_UNSUPPORTED"),
                 (job(), CCompiler("gcc", "fixture", "Windows"), LOCAL, "COMPILER_UNAVAILABLE"),
                 (job(dependencies=["win32"]), COMPILER, LOCAL, "PLATFORM_UNSUPPORTED")]
        for message, compiler, local, reason in cases:
            with self.subTest(reason=reason):
                result, boundary, repair = await self.validate(message=message, compiler=compiler, local=local, policy=ExecutionPolicy("job-123", "compute", dependencies=frozenset({"win32"})))
                self.assertEqual(result.reason, reason)
                self.assertFalse(boundary.sources)
                repair.assert_not_called()

    async def test_build_boundary_disappearance_and_os_failure_stop_without_repair(self):
        for error, reason in ((BoundaryUnavailable("private detail"), "BUILD_BOUNDARY_UNAVAILABLE"), (OSError("private path"), "COMPILER_UNAVAILABLE")):
            result, boundary, repair = await self.validate(boundary=SimulatedBoundary([error]))
            self.assertEqual(result.reason, reason)
            self.assertNotIn("private", str(result.event()))
            self.assertFalse(boundary.runs)
            repair.assert_not_called()

    async def test_malformed_worker_reply_and_unclassified_failure_never_reach_cloud(self):
        replies = [None, {}, CompileResult("clang", 1, None, "private", 1, False), CompileResult("clang", 1, "", "private", 1, False, "private reason"), CompileResult("clang", 0, "", "", 1, False), CompileResult("clang", None, "", "", 1, False)]
        for reply in replies:
            with self.subTest(reply=str(reply)[:80]):
                boundary = SimulatedBoundary()
                boundary.compile = AsyncMock(return_value=reply)
                result, _, repair = await self.validate(boundary=boundary)
                self.assertEqual(result.reason, "COMPILER_UNAVAILABLE")
                self.assertNotIn("private", str(result.event()))
                repair.assert_not_called()

    async def test_compiler_success_without_expected_executable_is_environment_error(self):
        reply = CompileResult("clang", 0, "", "", 1, True)
        boundary = SimulatedBoundary([reply])
        result, _, repair = await self.validate(boundary=boundary)
        self.assertEqual(result.reason, "COMPILER_UNAVAILABLE")
        self.assertFalse(boundary.runs)
        repair.assert_not_called()

    async def test_oversize_executable_is_discarded_before_preflight(self):
        boundary = SimulatedBoundary()
        async def compile(compiler, source, output, limits, link_args):
            with output.open("wb") as file:
                file.truncate(limits.disk_bytes + 1)
            return CompileResult("clang", 0, "", "", 1, True)
        boundary.compile = compile
        result, _, repair = await self.validate(boundary=boundary)
        self.assertEqual(result.reason, "RESOURCE_LIMIT")
        self.assertFalse(boundary.runs)
        repair.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture")
    async def test_symlink_executable_cannot_become_validated_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.write_bytes(b"fixture")
            boundary = SimulatedBoundary()
            async def compile(compiler, source, output, limits, link_args):
                output.symlink_to(outside)
                return CompileResult("clang", 0, "", "", 1, True)
            boundary.compile = compile
            result, _, _ = await self.validate(boundary=boundary)
            self.assertEqual(result.reason, "COMPILER_UNAVAILABLE")
            self.assertFalse(boundary.runs)

    async def test_preflight_loading_boundary_timeout_and_resource_failure_discard_artifact(self):
        for error in (BoundaryUnavailable("private"), TimeoutError("private"), ResourceLimitExceeded("private"), OSError("private")):
            with self.subTest(error=type(error).__name__):
                result, boundary, repair = await self.validate(boundary=SimulatedBoundary(executions=[error]))
                self.assertEqual(result.status, "VALIDATION_BLOCKED" if isinstance(error, BoundaryUnavailable) else "VALIDATION_FAILED")
                self.assertFalse(result.artifact.valid)
                self.assertFalse(result.artifact.executable.exists())
                self.assertNotIn("private", str(result.event()))
                repair.assert_not_called()

    async def test_invalidated_binary_is_never_launched_for_preflight(self):
        with patch.object(CompiledArtifact, "verify", return_value=False):
            result, boundary, _ = await self.validate()
        self.assertEqual(result.preflight, "PREFLIGHT_BLOCKED")
        self.assertFalse(boundary.runs)

    async def test_all_deterministic_fixtures_use_only_synthetic_input_and_exact_expected_output(self):
        policy = ExecutionPolicy("job-123", "compute", side_effects=False, fixtures=(Fixture("2\n", "4\n"), Fixture("3\n", "6\n", 7)))
        boundary = SimulatedBoundary(executions=[ProcessResult(0, "4\n"), ProcessResult(7, "6\n")])
        result, _, repair = await self.validate(policy=policy, boundary=boundary)
        self.assertEqual(result.preflight, "PREFLIGHT_PASSED")
        self.assertEqual([(stdin, caps) for _, stdin, caps in boundary.runs], [("2\n", frozenset()), ("3\n", frozenset())])
        repair.assert_not_called()

    async def test_windows_target_and_architecture_alias_work_through_validation(self):
        result, _, _ = await self.validate(message=job(platform="Windows", architecture="AMD64"), compiler=CCompiler("msvc", "fixture", "Windows"), local=("Windows", "x86_64"))
        self.assertEqual(result.status, "READY_FOR_EXECUTION")
        self.assertEqual(result.artifact.executable.suffix, ".exe")
