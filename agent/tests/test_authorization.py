"""Local startup consent, scoped fresh grants, replay protection and framing."""
from contextlib import redirect_stderr
from dataclasses import replace
import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.authorization import APPROVAL_LINE, ApprovedSession, SessionScope, approve_session, default_session_policy, load_session_scope
from agent.bridge import Outbox
from agent.jobs import ValidationService
from agent.process import ProcessResult
from agent.validation import CAPABILITIES, DEPENDENCIES, ExecutionPolicy, Job, load_policies
from tests.support import COMPILER, LOCAL, SimulatedBoundary, job


class Terminal(io.StringIO):
    def isatty(self):
        return True


class StartupConsentTests(unittest.TestCase):
    def scope(self):
        return SessionScope.parse(default_session_policy("Darwin")["session"])

    def test_defaults_include_all_capabilities_only_local_dependencies_and_max_supported_limits(self):
        for system in ("Darwin", "Windows"):
            with self.subTest(system=system):
                data = default_session_policy(system)
                self.assertEqual(data["jobs"], [])
                scope = SessionScope.parse(data["session"])
                self.assertEqual(scope.capabilities, CAPABILITIES)
                self.assertEqual(scope.dependencies, {name for name, details in DEPENDENCIES.items() if details[0] == system})
                self.assertEqual((scope.max_attempts, scope.max_source_bytes), (3, 131072))
                self.assertEqual((scope.limits.compile_seconds, scope.limits.run_seconds, scope.limits.processes), (30, 3, 16))
                self.assertTrue(scope.execute_authorized and scope.share_outputs and scope.side_effects)
        with self.assertRaises(ValueError):
            default_session_policy("Linux")

    def test_configuration_is_inactive_without_local_approval_and_explicit_jobs_still_load(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            data = default_session_policy("Darwin")
            path.write_text(json.dumps(data))
            self.assertEqual(load_policies(str(path)), {})
            self.assertIsInstance(load_session_scope(str(path)), SessionScope)
            data["jobs"] = [{"job_id": "manual", "operation": "compute", "execute_authorized": False}]
            path.write_text(json.dumps(data))
            self.assertFalse(load_policies(str(path))["manual"].execute_authorized)
            path.write_text(json.dumps({"jobs": []}))
            with self.assertRaises(ValueError):
                load_session_scope(str(path))
        with self.assertRaises(ValueError):
            load_session_scope(None)

    def test_session_loader_rejects_oversized_files_before_parsing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(" " * (256 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "too large"):
                load_session_scope(str(path))

    def test_scope_rejects_remote_worker_overrides_unknown_capabilities_and_excessive_budgets(self):
        changes = [{"operations": []}, {"operations": ["*", "compute"]}, {"operations": ["compute", "compute"]}, {"operations": ["Bad"]}, {"operations": [1]}, {"capabilities": ["everything"]}, {"dependencies": ["shell"]}, {"output_protocols": []}, {"output_protocols": ["raw"]}, {"execute_authorized": 1}, {"share_outputs": "yes"}, {"side_effects": None}, {"max_attempts": 4}, {"max_source_bytes": 131073}, {"limits": {"memory_bytes": 536870913}}, {"limits": {"compile_seconds": 31}}, {"limits": {"processes": True}}, {"limits": {"worker": "shell"}}, {"limits": []}, {"approved": True}, {"isolation_adapter": "remote:worker"}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                SessionScope.parse({**default_session_policy("Darwin")["session"], **change})

    def test_local_terminal_discloses_scope_and_requires_exact_explicit_approval(self):
        scope, output = self.scope(), io.StringIO()
        with patch("agent.authorization.sys.stdin", Terminal("APPROVE\n")), redirect_stderr(output):
            approved = approve_session(scope)
        self.assertEqual(approved.scope, scope)
        for disclosure in ("desktop.screen", "files.write", "share outputs: True", "until this agent stops", "isolation adapter"):
            self.assertIn(disclosure, output.getvalue())
        for answer in ("", "yes\n", "approve\n", "DENY\n"):
            with self.subTest(answer=answer), patch("agent.authorization.sys.stdin", Terminal(answer)), redirect_stderr(io.StringIO()), self.assertRaises(ValueError):
                approve_session(scope)

    def test_noninteractive_prompt_denies_without_reading_input(self):
        with patch("agent.authorization.sys.stdin", io.StringIO("APPROVE\n")) as stdin, self.assertRaisesRegex(ValueError, "interactive"):
            approve_session(self.scope())
        self.assertEqual(stdin.tell(), 0)

    def test_invalid_local_objects_cannot_approve_or_activate_a_scope(self):
        for invalid in (True, {}, None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    ApprovedSession(invalid)
                with self.assertRaises(ValueError):
                    approve_session(invalid)

    def test_terminal_read_failure_denies_approval_without_starting_a_session(self):
        for failure in (EOFError(), OSError("terminal disconnected")):
            stdin = Terminal()
            with self.subTest(failure=type(failure).__name__), patch("agent.authorization.sys.stdin", stdin), patch.object(stdin, "readline", side_effect=failure), redirect_stderr(io.StringIO()), self.assertRaisesRegex(ValueError, "declined"):
                approve_session(self.scope())

    def test_launcher_pipe_accepts_only_explicit_local_approval_line(self):
        with patch("agent.authorization.sys.stdin", io.StringIO(APPROVAL_LINE)):
            self.assertIsInstance(approve_session(self.scope(), launcher_pipe=True), ApprovedSession)
        for stdin in (Terminal(APPROVAL_LINE), io.StringIO(""), io.StringIO("yes\n"), io.StringIO(APPROVAL_LINE.strip()), io.StringIO('{"approved":true}\n')):
            with self.subTest(stdin=type(stdin).__name__), patch("agent.authorization.sys.stdin", stdin), self.assertRaises(ValueError):
                approve_session(self.scope(), launcher_pipe=True)

    def test_session_grants_preserve_requested_operation_permissions_dependencies_and_protocol(self):
        approval = ApprovedSession(self.scope())
        request = Job.parse(job("new-ui-id", operation="audio.set", capabilities=["audio.volume"], dependencies=["coreaudio"], output_protocol="json-v1"))
        grant = approval.grant(request)
        self.assertEqual((grant.job_id, grant.operation, grant.output_protocol), ("new-ui-id", "audio.set", "json-v1"))
        self.assertEqual(grant.capabilities, {"audio.volume"})
        self.assertEqual(grant.dependencies, {"coreaudio"})
        self.assertTrue(grant.execute_authorized and grant.share_outputs)
        # Scope doesn't leak unrelated broad permissions into a job's grant.
        self.assertNotIn("admin", grant.capabilities)
        restricted = ApprovedSession(replace(self.scope(), operations=frozenset({"compute"}), capabilities=frozenset(), dependencies=frozenset(), output_protocols=frozenset({"text"})))
        for request in (request, Job.parse(job(capabilities=["network"])), Job.parse(job(dependencies=["mac-system"])), Job.parse(job(output_protocol="json-v1"))):
            self.assertIsNone(restricted.grant(request))
        self.assertIsNotNone(restricted.grant(Job.parse(job())))

    def test_job_output_protocol_optional_for_legacy_clients_and_strict_when_supplied(self):
        self.assertEqual(Job.parse(job()).output_protocol, "text")
        self.assertEqual(Job.parse(job(output_protocol="json-v1")).output_protocol, "json-v1")
        for protocol in (None, 1, True, [], "raw"):
            with self.subTest(protocol=protocol), self.assertRaises(ValueError):
                Job.parse(job(output_protocol=protocol))

    def test_service_requires_approved_object_not_configuration_or_boolean(self):
        for invalid in (True, {}, self.scope()):
            with self.subTest(invalid=type(invalid).__name__), self.assertRaises(ValueError):
                ValidationService(COMPILER, session_authorization=invalid)


class SessionJobTests(unittest.IsolatedAsyncioTestCase):
    def setup_session(self, *, approved=True, scope=None, policies=None, boundary=None):
        scope = scope or SessionScope.parse(default_session_policy("Darwin")["session"])
        boundary = boundary or SimulatedBoundary()
        service = ValidationService(COMPILER, policies, boundary, local_platform=LOCAL,
                                    session_authorization=ApprovedSession(scope) if approved else None)
        box = Outbox()
        session = service.connect(box)
        self.addAsyncCleanup(session.close)
        return service, session, box, boundary

    async def event(self, box):
        return await asyncio.wait_for(box.get(), 1)

    async def test_file_scope_without_approval_denies_and_never_compiles(self):
        service, session, box, boundary = self.setup_session(approved=False)
        await session.handle(job("fresh"))
        self.assertEqual((await self.event(box))["reason"], "AUTHORIZATION_REQUIRED")
        self.assertFalse(service.policies or boundary.sources)

    async def test_fresh_ui_jobs_get_local_grants_and_execute_json_outputs_once(self):
        boundary = SimulatedBoundary(executions=[ProcessResult(0, '{"version":1,"value":42}')])
        service, session, box, _ = self.setup_session(boundary=boundary)
        await session.handle(job("fresh-widget-request", output_protocol="json-v1"))
        ready = await self.event(box)
        self.assertEqual(ready["status"], "READY_FOR_EXECUTION")
        self.assertEqual(service.policies["fresh-widget-request"].output_protocol, "json-v1")
        execute = {"type": "execute_job", "job_id": "fresh-widget-request", "source_sha256": ready["source_sha256"]}
        await session.handle(execute)
        result = await self.event(box)
        self.assertEqual((result["status"], result["stdout"]), ("COMPLETED", '{"version":1,"value":42}'))
        await session.handle(execute)
        self.assertEqual((await self.event(box))["reason"], "VALIDATION_REQUIRED")
        self.assertEqual(len(boundary.runs), 1)

    async def test_approved_json_output_protocol_rejects_invalid_execution_framing(self):
        _, session, box, _ = self.setup_session(boundary=SimulatedBoundary(executions=[ProcessResult(0, "unframed output")]))
        await session.handle(job("json", output_protocol="json-v1"))
        ready = await self.event(box)
        await session.handle({"type": "execute_job", "job_id": "json", "source_sha256": ready["source_sha256"]})
        self.assertEqual((await self.event(box))["reason"], "RESOURCE_OR_OUTPUT_LIMIT")

    async def test_reconnect_keeps_replay_ledger_and_cannot_mint_replacement_grant(self):
        service, session, box, _ = self.setup_session()
        await session.handle(job("fresh"))
        await self.event(box)
        first = service.policies["fresh"]
        await session.close()
        other_box = Outbox()
        other = service.connect(other_box)
        self.addAsyncCleanup(other.close)
        await other.handle(job("fresh"))
        self.assertEqual((await self.event(other_box))["reason"], "DUPLICATE_JOB")
        self.assertIs(service.policies["fresh"], first)
        await other.handle(job("another-fresh"))
        self.assertEqual((await self.event(other_box))["status"], "READY_FOR_EXECUTION")

    async def test_scope_denial_and_remote_policy_injection_never_create_a_grant(self):
        scope = replace(SessionScope.parse(default_session_policy("Darwin")["session"]), capabilities=frozenset())
        service, session, box, boundary = self.setup_session(scope=scope)
        await session.handle(job("outside", capabilities=["admin"]))
        self.assertEqual((await self.event(box))["reason"], "CAPABILITY_DENIED")
        await session.handle(job("injection", execute_authorized=True))
        self.assertEqual((await self.event(box))["reason"], "SOURCE_INVALID")
        self.assertFalse(service.policies or boundary.sources)

    async def test_explicit_per_job_denial_takes_precedence_over_broad_session(self):
        existing = ExecutionPolicy("explicit", "compute", side_effects=True, execute_authorized=False)
        service, session, box, boundary = self.setup_session(policies={"explicit": existing})
        await session.handle(job("explicit"))
        ready = await self.event(box)
        self.assertIs(service.policies["explicit"], existing)
        await session.handle({"type": "execute_job", "job_id": "explicit", "source_sha256": ready["source_sha256"]})
        self.assertEqual((await self.event(box))["reason"], "AUTHORIZATION_REQUIRED")
        self.assertFalse(boundary.runs)

    async def test_approval_does_not_supply_or_bypass_missing_isolation(self):
        from agent.isolation import IsolationBoundary
        _, session, box, _ = self.setup_session(boundary=IsolationBoundary())
        await session.handle(job("fresh"))
        event = await self.event(box)
        self.assertEqual((event["status"], event["reason"]), ("VALIDATION_BLOCKED", "BUILD_BOUNDARY_UNAVAILABLE"))
