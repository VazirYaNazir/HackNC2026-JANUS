"""Protocol tests use a simulated boundary; no generated native code is run."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from agent.bridge import Outbox, receive_commands, send_events
from agent.compiler import CCompiler, CompileResult
from agent.executor import execute_artifact, execute_c, ExecutionError
from agent.isolation import IsolationBoundary, REQUIRED_GUARANTEES, ResourceLimitExceeded, ResourceLimits
from agent.jobs import ValidationService
from agent.installer import CACHE_RECEIPT, VERSION, cache_verified, toolchain_digest
from agent.process import ProcessResult, run_bounded
from agent.validation import ExecutionPolicy, Fixture, Job, MAX_SOURCE_BYTES, load_policies, sanitize_diagnostics, validate_job

SOURCE = "int main(void) { return 0; }"
COMPILER = CCompiler("clang", "/not-executed/clang", "Darwin")
LOCAL = ("Darwin", "arm64")


def job(job_id="job-123", source=SOURCE, **fields):
    return {"type": "validate_job", "job_id": job_id, "attempt": 1, "language": "c11", "source": source,
            "operation": "compute", "platform": "Darwin", "architecture": "arm64", "capabilities": [], "dependencies": [], **fields}


class SimulatedBoundary(IsolationBoundary):
    """Tests only: simulate worker replies, never invoke a compiler or native executable."""
    build_guarantees = REQUIRED_GUARANTEES
    run_guarantees = REQUIRED_GUARANTEES
    run_capabilities = frozenset({"files.write", "desktop.mouse"})

    def __init__(self, compilations=(), executions=()):
        self.compilations = list(compilations)
        self.executions = list(executions)
        self.sources, self.runs, self.directories = [], [], []

    async def compile(self, compiler, source, output, limits, link_args):
        self.sources.append(source.read_text())
        self.directories.append(source.parent)
        outcome = self.compilations.pop(0) if self.compilations else None
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome:
            if isinstance(outcome, CompileResult):
                return outcome
            return CompileResult("clang", 1, "", outcome, 1, False, "COMPILE_ERROR")
        output.write_bytes(b"simulated-not-executable")
        return CompileResult("clang", 0, "", "", 1, True)

    async def run(self, executable, stdin, limits, capabilities):
        self.runs.append((executable, stdin, capabilities))
        outcome = self.executions.pop(0) if self.executions else ProcessResult(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    async def validate(self, message=None, policy=None, boundary=None, revision=None, compiler=COMPILER, **kwargs):
        message = message or job()
        policy = policy or ExecutionPolicy(message["job_id"], "compute", side_effects=False)
        boundary = boundary or SimulatedBoundary()
        repair = revision or AsyncMock()
        result = await validate_job(message, compiler, repair, policy=policy, boundary=boundary, local_platform=LOCAL, **kwargs)
        self.addCleanup(result.close)
        return result, boundary, repair

    async def test_valid_source_compiles_and_preflight_passes(self):
        result, boundary, repair = await self.validate()
        self.assertEqual(result.status, "READY_FOR_EXECUTION")
        self.assertEqual(result.preflight, "PREFLIGHT_PASSED")
        self.assertEqual(result.attempts, 1)
        self.assertTrue(result.artifact.verify())
        self.assertEqual(len(boundary.runs), 1)
        repair.assert_not_called()

    async def test_missing_symbol_requests_exactly_one_revision(self):
        boundary = SimulatedBoundary(["/Users/alice/private/program.c:9:5: error: use of undeclared identifier 'ACCESS_TOKEN'\n9 | private contents", None])
        revision = AsyncMock(return_value={"type": "code_revision", "job_id": "job-123", "attempt": 2, "language": "c11", "source": "int main(void) { return 1-1; }"})
        result, _, repair = await self.validate(boundary=boundary, revision=revision)
        self.assertEqual((result.status, result.attempts), ("READY_FOR_EXECUTION", 2))
        repair.assert_awaited_once()
        event = repair.call_args.args[2]
        self.assertEqual(event["diagnostics"], "program.c:9:5: error: undeclared identifier")
        self.assertNotIn("source", event)
        self.assertNotIn("alice", json.dumps(event))
        self.assertNotIn("TOKEN", json.dumps(event))
        self.assertEqual(boundary.sources[1], revision.return_value["source"])
        self.assertNotEqual(boundary.directories[0], boundary.directories[1])
        self.assertFalse(boundary.directories[0].exists())

    async def test_identical_revision_stops_before_second_compile(self):
        revision = AsyncMock(return_value={"type": "code_revision", "job_id": "job-123", "attempt": 2, "language": "c11", "source": SOURCE})
        result, boundary, _ = await self.validate(boundary=SimulatedBoundary(["program.c:1:1: error: undeclared identifier"]), revision=revision)
        self.assertEqual(result.reason, "REPEATED_SOURCE")
        self.assertEqual(len(boundary.sources), 1)
        self.assertEqual(boundary.runs, [])

    async def test_same_diagnostic_without_progress_stops(self):
        error = "program.c:1:2: error: unknown type name 'private'"
        revision = AsyncMock(return_value={"type": "code_revision", "job_id": "job-123", "attempt": 2, "language": "c11", "source": SOURCE + "\n/* changed */"})
        result, boundary, repair = await self.validate(boundary=SimulatedBoundary([error, error]), revision=revision)
        self.assertEqual(result.reason, "REPEATED_DIAGNOSTIC")
        self.assertEqual(len(boundary.sources), 2)
        repair.assert_awaited_once()

    async def test_three_compilations_maximum_and_no_execution(self):
        boundary = SimulatedBoundary(["program.c:1:1: error: undeclared", "program.c:2:2: error: syntax error", "program.c:3:3: error: unknown type"])
        async def revision(job_id, attempt, diagnostics):
            return {"type": "code_revision", "job_id": job_id, "attempt": attempt + 1, "language": "c11", "source": SOURCE + f"\n/* attempt {attempt + 1} */"}
        repair = AsyncMock(side_effect=revision)
        result, _, _ = await self.validate(boundary=boundary, revision=repair)
        self.assertEqual((result.status, result.reason, result.attempts), ("VALIDATION_FAILED", "ATTEMPTS_EXHAUSTED", 3))
        self.assertEqual(len(boundary.sources), 3)
        self.assertEqual(repair.await_count, 2)
        self.assertEqual(boundary.runs, [])
        self.assertTrue(all(not directory.exists() for directory in boundary.directories))

    async def test_malformed_and_scope_changing_revisions_are_rejected(self):
        base = {"type": "code_revision", "job_id": "job-123", "attempt": 2, "language": "c11", "source": SOURCE + "\n/* new */"}
        for change in ({"attempt": 3}, {"attempt": True}, {"job_id": "another-job"}, {"language": "c++"}, {"capabilities": ["admin"]}, {"source": "x" * (MAX_SOURCE_BYTES + 1)}):
            with self.subTest(change=list(change)):
                result, boundary, _ = await self.validate(boundary=SimulatedBoundary(["program.c:1:1: error: undeclared"]), revision=AsyncMock(return_value={**base, **change}))
                self.assertEqual(result.status, "VALIDATION_FAILED")
                self.assertIn(result.reason, ("REVISION_INVALID", "SOURCE_INVALID"))
                self.assertEqual(len(boundary.sources), 1)

    async def test_invalid_contract_never_reaches_compiler(self):
        for change in ({"attempt": True}, {"source": "void main(void) {}"}, {"source": SOURCE + "\0"}, {"source": SOURCE + "\ud800"}, {"source": "x" * (MAX_SOURCE_BYTES + 1)}, {"execution_policy": {"authorized": True}}, {"source": '#include "/Users/alice/private.h"\n' + SOURCE}, {"source": "int main(void) {system(\"bad\");return 0;}"}):
            with self.subTest(change=list(change)):
                result, boundary, repair = await self.validate(message=job(**change))
                self.assertEqual(result.status, "REJECTED")
                self.assertEqual(boundary.sources, [])
                repair.assert_not_called()

    async def test_platform_mismatch_and_missing_compiler_do_not_repair(self):
        for message, compiler, reason in ((job(platform="Windows"), COMPILER, "PLATFORM_UNSUPPORTED"), (job(architecture="x86_64"), COMPILER, "PLATFORM_UNSUPPORTED"), (job(), None, "COMPILER_UNAVAILABLE")):
            result, boundary, repair = await self.validate(message=message, compiler=compiler)
            self.assertEqual(result.reason, reason)
            self.assertEqual(boundary.sources, [])
            repair.assert_not_called()

    async def test_compile_environment_failures_do_not_repair(self):
        for reason in ("COMPILER_UNAVAILABLE", "COMPILE_TIMEOUT", "RESOURCE_LIMIT"):
            with self.subTest(reason=reason):
                result, boundary, repair = await self.validate(boundary=SimulatedBoundary([CompileResult("clang", None, "", "sensitive", 1, False, reason)]))
                self.assertEqual(result.reason, reason)
                self.assertEqual(boundary.runs, [])
                repair.assert_not_called()

    async def test_oversize_worker_diagnostics_stop_without_repair_or_transfer(self):
        boundary = SimulatedBoundary([CompileResult("clang", 1, "", "private" * 20000, 1, False, "COMPILE_ERROR")])
        result, _, repair = await self.validate(boundary=boundary)
        self.assertEqual(result.reason, "RESOURCE_LIMIT")
        self.assertEqual(result.diagnostics, "")
        self.assertEqual(boundary.runs, [])
        repair.assert_not_called()

    async def test_unexpected_worker_failure_cleans_compiled_artifact(self):
        boundary = SimulatedBoundary(executions=[RuntimeError("local worker failure")])
        with self.assertRaises(RuntimeError):
            await validate_job(job(), COMPILER, AsyncMock(),
                               policy=ExecutionPolicy("job-123", "compute", side_effects=False),
                               boundary=boundary, local_platform=LOCAL)
        self.assertTrue(all(not path.exists() for path in boundary.directories))

    async def test_wall_timeout_and_resource_exception_stop(self):
        boundary = SimulatedBoundary([ResourceLimitExceeded("private location")])
        result, _, repair = await self.validate(boundary=boundary)
        self.assertEqual(result.reason, "RESOURCE_LIMIT")
        repair.assert_not_called()
        boundary = SimulatedBoundary()
        async def wait(*args):
            await asyncio.sleep(1)
        boundary.compile = wait
        policy = ExecutionPolicy("job-123", "compute", limits=ResourceLimits(compile_seconds=0.01))
        result, _, repair = await self.validate(boundary=boundary, policy=policy)
        self.assertEqual(result.reason, "COMPILE_TIMEOUT")
        repair.assert_not_called()

    async def test_repair_timeout_stops(self):
        async def revision(*args):
            await asyncio.sleep(1)
        result, boundary, _ = await self.validate(boundary=SimulatedBoundary(["program.c:1:1: error: undeclared"]), revision=revision, revision_timeout=0.01)
        self.assertEqual(result.reason, "REPAIR_TIMEOUT")
        self.assertEqual(len(boundary.sources), 1)

    async def test_no_build_boundary_means_no_untrusted_compiler_launch(self):
        with patch.object(CCompiler, "compile_detailed", side_effect=AssertionError("unsafe launch")):
            result, _, repair = await self.validate(boundary=IsolationBoundary())
        self.assertEqual((result.status, result.reason, result.attempts), ("VALIDATION_BLOCKED", "BUILD_BOUNDARY_UNAVAILABLE", 0))
        repair.assert_not_called()

    async def test_missing_run_boundary_blocks_preflight(self):
        boundary = SimulatedBoundary()
        boundary.run_guarantees = frozenset()
        result, _, _ = await self.validate(boundary=boundary)
        self.assertEqual((result.status, result.preflight), ("VALIDATION_BLOCKED", "PREFLIGHT_BLOCKED"))
        self.assertFalse(result.artifact.valid)
        self.assertEqual(boundary.runs, [])

    async def test_real_side_effect_permissions_are_not_used_in_preflight(self):
        policy = ExecutionPolicy("job-123", "compute", capabilities=frozenset({"files.write"}), side_effects=True)
        result, boundary, _ = await self.validate(message=job(capabilities=["files.write"]), policy=policy)
        self.assertEqual(result.preflight, "PREFLIGHT_LIMITED")
        self.assertEqual(boundary.runs, [])

    async def test_fixture_failure_and_invalid_framing_do_not_execute(self):
        policy = ExecutionPolicy("job-123", "compute", side_effects=False, fixtures=(Fixture("2\n", "4\n"),))
        boundary = SimulatedBoundary(executions=[ProcessResult(0, "wrong\n")])
        result, _, _ = await self.validate(policy=policy, boundary=boundary)
        self.assertEqual(result.preflight, "PREFLIGHT_FAILED")
        self.assertFalse(result.artifact.valid)
        policy = replace(policy, fixtures=(), output_protocol="json-v1")
        result, _, _ = await self.validate(policy=policy, boundary=SimulatedBoundary(executions=[ProcessResult(0, "not json")]))
        self.assertEqual(result.preflight, "PREFLIGHT_FAILED")

    async def test_execution_needs_local_authorization_and_unchanged_artifact(self):
        result, boundary, _ = await self.validate()
        denied = await execute_artifact(result, boundary, result.artifact.policy)
        self.assertEqual(denied.reason, "AUTHORIZATION_REQUIRED")
        self.assertEqual(len(boundary.runs), 1)  # Only synthetic preflight.
        policy = ExecutionPolicy("job-123", "compute", side_effects=False, execute_authorized=True)
        result, boundary, _ = await self.validate(policy=policy)
        result.artifact.executable.write_bytes(b"tampered")
        denied = await execute_artifact(result, boundary, policy)
        self.assertEqual(denied.reason, "ARTIFACT_INVALID_OR_CONSUMED")
        result.artifact.executable.unlink()
        denied = await execute_artifact(result, boundary, policy)
        self.assertEqual(denied.reason, "ARTIFACT_INVALID_OR_CONSUMED")

    async def test_execution_single_use_and_private_output_not_forwarded(self):
        policy = ExecutionPolicy("job-123", "compute", side_effects=False, execute_authorized=True)
        boundary = SimulatedBoundary(executions=[ProcessResult(0), ProcessResult(0, "private stdout", "private stderr")])
        result, _, _ = await self.validate(policy=policy, boundary=boundary)
        outcome = await execute_artifact(result, boundary, policy, "private stdin")
        self.assertEqual(outcome.status, "COMPLETED")
        self.assertNotIn("stdout", outcome.event("job-123"))
        self.assertNotIn("private", json.dumps(outcome.event("job-123")))
        self.assertEqual(outcome.event("job-123", True)["stdout"], "private stdout")
        denied = await execute_artifact(result, boundary, policy)
        self.assertEqual(denied.reason, "ARTIFACT_INVALID_OR_CONSUMED")
        with self.assertRaises(ExecutionError):
            execute_c(SOURCE, COMPILER)


class DiagnosticsTests(unittest.TestCase):
    def test_cached_fallback_checks_support_files_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "toolchain"
            root.mkdir()
            (root / "zig").write_bytes(b"verified fixture compiler")
            (root / "lib").mkdir()
            library = root / "lib" / "runtime.h"
            library.write_text("verified fixture header")
            release = "test-release-checksum"
            (root / CACHE_RECEIPT).write_text(json.dumps({"version": VERSION, "release_sha256": release, "toolchain_sha256": toolchain_digest(root)}))
            self.assertTrue(cache_verified(root, release))
            library.write_text("tampered")
            self.assertFalse(cache_verified(root, release))
            self.assertFalse(cache_verified(root, "different-release"))
            if os.name != "nt":
                (root / "escape").symlink_to(Path(tmp))
                with self.assertRaises(RuntimeError):
                    toolchain_digest(root)

    def test_diagnostics_remove_paths_tokens_snippets_and_environment(self):
        raw = "\n".join([
            '/Users/alice/private/program.c:9:5: error: use of undeclared identifier "SECRET_TOKEN_abcdef"',
            '9 | secret file contents; Bearer secret; PATH=/Users/alice',
            r'C:\Users\alice\program.c(2,3): error C2065: private secret',
            '/private/directory/user.h:1:1: error: arbitrary private output',
            '\x1b[31mcompiler error: custom token hidden\x1b[0m',
        ])
        safe = sanitize_diagnostics(raw)
        for secret in ("alice", "Users", "TOKEN", "secret", "PATH", "user.h", "private", "Bearer", "\x1b"):
            self.assertNotIn(secret, safe)
        self.assertIn("program.c:9:5", safe)
        self.assertIn("program.c:2:3", safe)
        self.assertLessEqual(len(sanitize_diagnostics(raw * 10000, limit=100)), 100)

    def test_policy_file_is_local_strict_and_cannot_enable_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps({"jobs": [{"job_id": "j1", "operation": "compute", "side_effects": False}]}))
            self.assertFalse(load_policies(str(path))["j1"].execute_authorized)
            path.write_text(json.dumps({"jobs": [{"job_id": "j1", "operation": "compute", "sandbox_verified": True}]}))
            with self.assertRaises(ValueError):
                load_policies(str(path))
            path.write_text(json.dumps({"jobs": [{"job_id": "j1", "operation": "compute", "execute_authorized": "true"}]}))
            with self.assertRaises(ValueError):
                load_policies(str(path))

    @unittest.skipUnless(shutil.which("cc"), "Requires a trusted host C compiler")
    def test_structured_compiler_result_and_missing_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, executable = Path(tmp) / "program.c", Path(tmp) / "program"
            source.write_text(SOURCE)
            compiler = CCompiler("gcc", shutil.which("cc"), "Darwin")
            compiled = compiler.compile_detailed(source, executable)
            self.assertTrue(compiled.success)
            source.write_text("int main(void) { return unknown; }")
            compiled = compiler.compile_detailed(source, executable)
            self.assertEqual(compiled.reason, "COMPILE_ERROR")
            self.assertFalse(executable.exists())
            with patch("agent.compiler.run_bounded", return_value=ProcessResult(0)):
                compiled = compiler.compile_detailed(source, executable)
            self.assertEqual(compiled.reason, "COMPILER_UNAVAILABLE")

    def test_trusted_process_output_and_time_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_bounded([sys.executable, "-c", "import sys;sys.stderr.write('x'*1000000)"], cwd=tmp, stderr_limit=1024)
            self.assertTrue(result.output_limited)
            self.assertLessEqual(len(result.stderr), 1024)
            result = run_bounded([sys.executable, "-c", "import time;time.sleep(1)"], cwd=tmp, timeout=0.02)
            self.assertTrue(result.timed_out)


class JobSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_does_not_prevent_later_job_or_existing_ping(self):
        boundary = SimulatedBoundary()
        grants = {name: ExecutionPolicy(name, "compute", side_effects=False) for name in ("bad", "good")}
        service = ValidationService(COMPILER, grants, boundary, local_platform=LOCAL)
        outbox = Outbox()
        session = service.connect(outbox)
        self.addAsyncCleanup(session.close)
        await session.handle(job("bad", source="void main(void) {}"))
        first = await asyncio.wait_for(outbox.get(), 1)
        self.assertEqual(first["status"], "REJECTED")
        await session.handle(job("good"))
        second = await asyncio.wait_for(outbox.get(), 1)
        self.assertEqual(second["status"], "READY_FOR_EXECUTION")

    async def test_disconnect_cancels_repairs_and_stale_replay(self):
        boundary = SimulatedBoundary(["program.c:1:1: error: undeclared"])
        service = ValidationService(COMPILER, {"job-123": ExecutionPolicy("job-123", "compute")}, boundary, local_platform=LOCAL)
        box = Outbox()
        session = service.connect(box)
        await session.handle(job())
        event = await asyncio.wait_for(box.get(), 1)
        self.assertEqual(event["type"], "repair_requested")
        await session.close()
        self.assertEqual(boundary.runs, [])
        self.assertTrue(all(not p.exists() for p in boundary.directories))
        session = service.connect(box)
        self.addAsyncCleanup(session.close)
        await session.handle({"type": "code_revision", "job_id": "job-123", "attempt": 2, "language": "c11", "source": SOURCE})
        self.assertEqual((await box.get())["reason"], "STALE_REVISION")
        await session.handle(job())
        self.assertEqual((await box.get())["reason"], "DUPLICATE_JOB")

    async def test_closed_connection_cannot_start_a_new_validation(self):
        boundary = SimulatedBoundary()
        grant = ExecutionPolicy("job-123", "compute", side_effects=False)
        service = ValidationService(COMPILER, {grant.job_id: grant}, boundary, local_platform=LOCAL)
        session = service.connect(Outbox())
        await session.close()
        await session.handle(job())
        await asyncio.sleep(0)
        self.assertFalse(session.tasks)
        self.assertFalse(service.seen_jobs)
        self.assertFalse(boundary.sources)

    async def test_disconnect_during_preflight_invalidates_artifact(self):
        boundary = SimulatedBoundary()
        running, cancelled = asyncio.Event(), asyncio.Event()
        async def run(executable, *args):
            running.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        boundary.run = run
        grant = ExecutionPolicy("job-123", "compute", side_effects=False)
        service = ValidationService(COMPILER, {grant.job_id: grant}, boundary, local_platform=LOCAL)
        session = service.connect(Outbox())
        await session.handle(job())
        await asyncio.wait_for(running.wait(), 1)
        await session.close()
        self.assertTrue(cancelled.is_set())
        self.assertTrue(all(not path.exists() for path in boundary.directories))
        self.assertFalse(session.results)

    async def test_output_transfer_requires_local_opt_in(self):
        for share in (False, True):
            with self.subTest(share=share):
                boundary = SimulatedBoundary(executions=[ProcessResult(0), ProcessResult(0, "private result")])
                grant = ExecutionPolicy("job-123", "compute", side_effects=False, execute_authorized=True, share_outputs=share)
                service = ValidationService(COMPILER, {grant.job_id: grant}, boundary, local_platform=LOCAL)
                box = Outbox()
                session = service.connect(box)
                await session.handle(job())
                ready = await asyncio.wait_for(box.get(), 1)
                self.assertEqual(ready["status"], "READY_FOR_EXECUTION")
                await session.handle({"type": "execute_job", "job_id": "job-123", "source_sha256": ready["source_sha256"]})
                complete = await asyncio.wait_for(box.get(), 1)
                self.assertEqual(complete["status"], "COMPLETED")
                self.assertEqual("stdout" in complete, share)
                await session.close()

    async def test_cloud_execution_policy_or_unknown_hash_never_authorizes_execution(self):
        service = ValidationService(COMPILER, local_platform=LOCAL)
        box = Outbox()
        session = service.connect(box)
        self.addAsyncCleanup(session.close)
        await session.handle({"type": "execute_job", "job_id": "job-123", "source_sha256": "bogus", "authorized": True})
        self.assertEqual((await box.get())["reason"], "SOURCE_INVALID")
        await session.handle({"type": "execute_job", "job_id": "job-123", "source_sha256": "bogus"})
        self.assertEqual((await box.get())["status"], "EXECUTION_BLOCKED")

    async def test_websocket_repair_route_keeps_receiving_commands(self):
        boundary = SimulatedBoundary(["program.c:1:1: error: undeclared", None])
        service = ValidationService(COMPILER, {"job-123": ExecutionPolicy("job-123", "compute", side_effects=False)}, boundary, local_platform=LOCAL)
        finished = asyncio.get_running_loop().create_future()

        async def handler(ws):
            try:
                await ws.send(json.dumps(job()))
                repair = json.loads(await asyncio.wait_for(ws.recv(), 2))
                self.assertEqual(repair["type"], "repair_requested")
                # Receiver must keep reading while validation awaits a revision.
                await ws.send(json.dumps({"type": "job", "job_id": "ping", "action": "ping"}))
                ping = json.loads(await asyncio.wait_for(ws.recv(), 2))
                self.assertEqual(ping["result"], {"pong": True})
                await ws.send(json.dumps({"type": "code_revision", "job_id": "job-123", "attempt": 2, "language": "c11", "source": SOURCE + "\n/* revised */"}))
                result = json.loads(await asyncio.wait_for(ws.recv(), 2))
                self.assertEqual((result["status"], result["attempts"]), ("READY_FOR_EXECUTION", 2))
                finished.set_result(None)
            except BaseException as error:
                finished.set_exception(error)
            finally:
                await ws.close()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as ws:
                box = Outbox()
                session = service.connect(box)
                sender = asyncio.create_task(send_events(ws, box))
                try:
                    await receive_commands(ws, {}, box, session)
                    await finished
                finally:
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)
                    await session.close()
        self.assertEqual(len(boundary.sources), 2)


if __name__ == "__main__":
    unittest.main()
