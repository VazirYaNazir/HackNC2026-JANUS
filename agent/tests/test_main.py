"""CLI parsing, startup authorization, native invocation, and error reporting."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agent.compiler import CCompiler, CompilerError
from agent.main import main, parser
from agent.isolation import IsolationBoundary, REQUIRED_GUARANTEES


class ReadyWorker(IsolationBoundary):
    build_guarantees = run_guarantees = REQUIRED_GUARANTEES
    compiler = CCompiler("worker-gcc", "docker", "Linux")
    execution_target = {"platform": "Linux", "architecture": "x86_64", "profile": "portable-c", "capabilities": [], "dependencies": []}

    def describe(self):
        return {"available": True, "reason": None, "execution_target": self.execution_target}


class MainTests(unittest.TestCase):
    def setUp(self):
        self.boundary = IsolationBoundary()
        loader = patch("agent.isolation.load_boundary", return_value=self.boundary)
        self.loader = loader.start()
        self.addCleanup(loader.stop)

    def call(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_build_prints_named_outputs(self):
        with patch("agent.main.build_tools", return_value={"volume": Path("volume"), "mouse": Path("mouse")}) as build:
            code, stdout, stderr = self.call(["build"])
        self.assertEqual(code, 0)
        self.assertIn("volume: volume", stdout)
        self.assertIn("mouse: mouse", stdout)
        self.assertEqual(stderr, "")
        build.assert_called_once_with()

    def test_run_forwards_arguments_without_shell_and_returns_native_exit(self):
        for arguments in (["--", "--once"], ["--once"]):
            with self.subTest(arguments=arguments), patch("agent.main.build_tools", return_value={"metrics": Path("folder with spaces/metrics")}) as build, patch("agent.main.subprocess.call", return_value=7) as run:
                self.assertEqual(self.call(["run", "metrics", *arguments])[0], 7)
                build.assert_called_once_with(("metrics",))
                run.assert_called_once_with([str(Path("folder with spaces/metrics")), "--once"])

    def test_stream_initializes_one_compiler_local_policy_and_fail_closed_service(self):
        compiler = CCompiler("clang", "fixture", "Darwin")
        binaries = {"volume": Path("volume")}
        bridge = AsyncMock()
        with patch.dict("os.environ", {"AGENT_TOKEN": "private-fixture-token", "AGENT_BACKEND_URL": "wss://fixture/ws/agent"}, clear=True), patch("agent.main.detect_compiler", return_value=compiler) as detect, patch("agent.main.build_tools", return_value=binaries) as build, patch("agent.validation.load_policies", return_value={}) as policies, patch("agent.bridge.run_bridge", bridge):
            code, stdout, stderr = self.call(["stream", "--validation-policy", "local.json", "--screen", "--temperature-file", "sensor.txt"])
        self.assertEqual(code, 0)
        detect.assert_called_once()
        build.assert_called_once_with(compiler=compiler)
        policies.assert_called_once_with("local.json")
        bridge.assert_awaited_once()
        options, actual_binaries, token, service = bridge.call_args.args
        self.assertEqual((actual_binaries, token), (binaries, "private-fixture-token"))
        self.assertTrue(options.screen)
        self.assertEqual(options.temperature_file, "sensor.txt")
        self.assertFalse(service.boundary.can_build())
        self.assertNotIn(token, stdout + stderr)

    def test_missing_token_stops_before_compiler_or_policy_access(self):
        with patch.dict("os.environ", {}, clear=True), patch("agent.main.detect_compiler") as detect, patch("agent.validation.load_policies") as policies, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["stream", "--url", "wss://fixture/ws/agent"])
        self.assertEqual(caught.exception.code, 2)
        detect.assert_not_called()
        policies.assert_not_called()

    def test_registered_connection_uses_local_grants_workers_and_no_telemetry_builds(self):
        compiler = CCompiler("clang", "fixture", "Darwin")
        connection = AsyncMock()
        with patch.dict("os.environ", {"AGENT_TOKEN": "private-fixture-token", "AGENT_DEVICE_ID": "laptop", "AGENT_BACKEND_URL": "wss://fixture/ws/agents/laptop"}, clear=True), patch("agent.main.detect_compiler", return_value=compiler) as detect, patch("agent.main.build_tools") as build, patch("agent.validation.load_policies", return_value={}) as policies, patch("agent.transport.run_connection", connection):
            code, stdout, stderr = self.call(["connect", "--validation-policy", "local.json", "--workers", "2"])
        self.assertEqual(code, 0)
        connection.assert_awaited_once()
        options, token, service = connection.call_args.args
        self.assertEqual(options.device_id, "laptop")
        self.assertEqual(service.max_parallel, 2)
        self.assertFalse(service.boundary.can_build())
        detect.assert_called_once()
        build.assert_not_called()
        policies.assert_called_once_with("local.json")
        self.assertNotIn(token, stdout + stderr)

    def test_registered_connection_missing_token_stops_before_compiler(self):
        with patch.dict("os.environ", {}, clear=True), patch("agent.main.detect_compiler") as detect, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["connect", "--url", "wss://fixture/ws/agents/laptop", "--device-id", "laptop"])
        self.assertEqual(caught.exception.code, 2)
        detect.assert_not_called()

    def test_ready_portable_worker_keeps_host_compiler_for_native_builds(self):
        self.loader.return_value = ReadyWorker()
        host = CCompiler("clang", "host-compiler", "Darwin")
        connection = AsyncMock()
        with patch.dict("os.environ", {"AGENT_TOKEN": "fixture"}, clear=True), patch("agent.main.detect_compiler", return_value=host) as detect, \
                patch("agent.main.platform.system", return_value="Darwin"), patch("agent.main.platform.machine", return_value="arm64"), \
                patch("agent.main.build_tools", return_value={"volume": "fixture"}) as build, patch("agent.transport.run_connection", connection):
            code, _, _ = self.call(["connect", "--device-id", "laptop", "--url", "ws://127.0.0.1/ws/agents/laptop", "--native-tools"])
        self.assertEqual(code, 0)
        detect.assert_called_once()
        self.assertIs(build.call_args.kwargs["compiler"], host)
        service = connection.call_args.args[2]
        self.assertIs(service.compiler, ReadyWorker.compiler)
        self.assertEqual(service.local_platform, ("Linux", "x86_64"))
        self.assertEqual(service.host_platform, ("Darwin", "arm64"))

    def test_ready_non_native_linux_worker_needs_no_unsupported_host_compiler(self):
        self.loader.return_value = ReadyWorker()
        connection = AsyncMock()
        with patch.dict("os.environ", {"AGENT_TOKEN": "fixture"}, clear=True), patch("agent.main.detect_compiler") as detect, \
                patch("agent.main.platform.system", return_value="Linux"), patch("agent.main.build_tools") as build, \
                patch("agent.transport.run_connection", connection):
            code, _, _ = self.call(["connect", "--device-id", "laptop", "--url", "ws://127.0.0.1/ws/agents/laptop"])
        self.assertEqual(code, 0)
        detect.assert_not_called()
        build.assert_not_called()
        self.assertIs(connection.call_args.args[2].compiler, ReadyWorker.compiler)

    def test_worker_status_reports_actual_boundary_without_a_compiler_or_approval(self):
        self.loader.return_value = ReadyWorker()
        with patch("agent.main.detect_compiler") as compiler, patch("agent.authorization.approve_session") as approval:
            code, stdout, stderr = self.call(["worker", "status"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), ReadyWorker().describe())
        self.assertEqual(stderr, "")
        compiler.assert_not_called()
        approval.assert_not_called()

    def test_worker_diagnose_prints_one_safe_collected_report_without_provisioning(self):
        boundary = ReadyWorker()
        report = {"available": False, "reason": "WORKER_ENFORCEMENT_UNAVAILABLE", "execution_target": None,
                  "diagnostics": [{"stage": "runtime_probe", "outcome": "failed", "code": "CGROUP_REQUIRED", "checks": {"cpu": False}}]}
        def diagnose():
            print("trusted readiness diagnostic progress")
            return report
        boundary.diagnose = Mock(side_effect=diagnose)
        self.loader.return_value = boundary
        with patch.dict("os.environ", {}, clear=True), patch("agent.main.detect_compiler") as compiler, \
             patch("agent.main.build_tools") as build, patch("agent.authorization.approve_session") as approval:
            code, stdout, stderr = self.call(["worker", "diagnose"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), report)
        self.assertIn("trusted readiness diagnostic progress", stderr)
        boundary.diagnose.assert_called_once_with()
        self.loader.assert_called_once_with(None)
        compiler.assert_not_called()
        build.assert_not_called()
        approval.assert_not_called()

    def test_worker_diagnose_preserves_explicit_local_adapter_and_describe_fallback(self):
        self.loader.return_value = ReadyWorker()
        with patch.dict("os.environ", {}, clear=True):
            code, stdout, stderr = self.call(["worker", "diagnose", "--isolation-adapter", "local.worker:factory"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), ReadyWorker().describe())
        self.assertEqual(stderr, "")
        self.loader.assert_called_once_with("local.worker:factory")
        self.loader.reset_mock()
        self.loader.return_value = IsolationBoundary()
        code, stdout, stderr = self.call(["worker", "diagnose"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), {"available": False, "reason": "WORKER_UNAVAILABLE", "execution_target": None})
        self.assertEqual(stderr, "")

    def test_worker_status_never_invokes_optional_diagnostics(self):
        boundary = ReadyWorker()
        boundary.diagnose = Mock()
        self.loader.return_value = boundary
        code, stdout, _ = self.call(["worker", "status"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), boundary.describe())
        boundary.diagnose.assert_not_called()

    def test_worker_diagnostic_failure_uses_sanitized_worker_error_report(self):
        boundary = ReadyWorker()
        boundary.diagnose = Mock(side_effect=OSError("private-credential-file"))
        self.loader.return_value = boundary
        code, stdout, stderr = self.call(["worker", "diagnose"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout), {"available": False, "reason": "WORKER_PREPARATION_FAILED", "execution_target": None})
        self.assertNotIn("private-credential-file", stdout + stderr)

    def test_worker_prepare_outputs_one_json_report_and_routes_progress_to_stderr(self):
        expected = ReadyWorker().describe()

        async def prepare():
            print("trusted image build progress")
            return expected

        with patch.dict(sys.modules, {"agent.container": SimpleNamespace(prepare_worker=prepare)}), \
                patch.dict("os.environ", {}, clear=True):
            code, stdout, stderr = self.call(["worker", "prepare"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout), expected)
        self.assertIn("trusted image build progress", stderr)
        self.loader.assert_not_called()

    def test_worker_prepare_preserves_custom_adapter_without_provisioning_default_image(self):
        self.loader.return_value = ReadyWorker()
        prepare = AsyncMock()
        with patch.dict(sys.modules, {"agent.container": SimpleNamespace(prepare_worker=prepare)}), \
                patch.dict("os.environ", {"AGENT_ISOLATION_ADAPTER": "my_local_app.worker:create_boundary"}, clear=True):
            code, stdout, _ = self.call(["worker", "prepare"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(stdout)["available"])
        self.loader.assert_called_once_with("my_local_app.worker:create_boundary")
        prepare.assert_not_awaited()

    def test_worker_prepare_reports_missing_prerequisites_without_native_build_or_approval(self):
        expected = {"available": False, "reason": "DOCKER_NOT_INSTALLED", "execution_target": None}
        prepare = AsyncMock(return_value=expected)
        with patch.dict(sys.modules, {"agent.container": SimpleNamespace(prepare_worker=prepare)}), \
                patch.dict("os.environ", {}, clear=True), patch("agent.main.detect_compiler") as detect, \
                patch("agent.main.build_tools") as build, patch("agent.authorization.approve_session") as approval:
            code, stdout, stderr = self.call(["worker", "prepare"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout), expected)
        self.assertEqual(stderr, "")
        prepare.assert_awaited_once()
        detect.assert_not_called()
        build.assert_not_called()
        approval.assert_not_called()

    def test_failed_worker_prepare_is_structured_and_never_echoes_exception_secrets(self):
        self.loader.side_effect = ValueError("private-credential")
        code, stdout, stderr = self.call(["worker", "status"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout), {"available": False, "reason": "WORKER_PREPARATION_FAILED", "execution_target": None})
        self.assertNotIn("private-credential", stdout + stderr)

    def test_session_approval_pipe_creates_scoped_service_without_second_prompt(self):
        from agent.authorization import APPROVAL_LINE, default_session_policy
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(default_session_policy("Darwin")))
            compiler = CCompiler("clang", "fixture", "Darwin")
            connection = AsyncMock()
            with patch.dict("os.environ", {"AGENT_TOKEN": "fixture"}, clear=True), patch("agent.main.detect_compiler", return_value=compiler), patch("agent.authorization.sys.stdin", io.StringIO(APPROVAL_LINE)), patch("agent.transport.run_connection", connection):
                code, _, stderr = self.call(["connect", "--device-id", "laptop", "--url", "wss://fixture/ws/agents/laptop", "--validation-policy", str(path), "--session-approval-stdin"])
            self.assertEqual(code, 0)
            self.assertIsNotNone(connection.call_args.args[2].session_authorization)
            self.assertNotIn("Type APPROVE", stderr)

    def test_session_decline_or_noninteractive_prompt_stops_before_compiler(self):
        from agent.authorization import default_session_policy
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(default_session_policy("Darwin")))
            for flag, answer in (("--approve-session", "APPROVE\n"), ("--session-approval-stdin", "DENY\n")):
                with self.subTest(flag=flag), patch.dict("os.environ", {"AGENT_TOKEN": "fixture"}, clear=True), patch("agent.main.detect_compiler") as compiler, patch("agent.authorization.sys.stdin", io.StringIO(answer)), patch("agent.transport.run_connection", AsyncMock()) as connection:
                    code, _, _ = self.call(["connect", "--device-id", "laptop", "--url", "wss://fixture/ws/agents/laptop", "--validation-policy", str(path), flag])
                self.assertEqual(code, 1)
                compiler.assert_not_called()
                connection.assert_not_awaited()

    def test_session_config_alone_remains_unapproved_and_flags_are_mutually_exclusive(self):
        from agent.authorization import default_session_policy
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text(json.dumps(default_session_policy("Darwin")))
            with patch.dict("os.environ", {"AGENT_TOKEN": "fixture"}, clear=True), patch("agent.main.detect_compiler", return_value=CCompiler("clang", "fixture", "Darwin")), patch("agent.transport.run_connection", AsyncMock()) as connection:
                code, _, _ = self.call(["connect", "--device-id", "laptop", "--url", "wss://fixture/ws/agents/laptop", "--validation-policy", str(path)])
            self.assertEqual(code, 0)
            self.assertIsNone(connection.call_args.args[2].session_authorization)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser().parse_args(["connect", "--approve-session", "--session-approval-stdin"])

    def test_registered_native_tools_and_screen_opt_in_use_the_same_connection(self):
        compiler = CCompiler("clang", "fixture", "Darwin")
        for screen in (False, True):
            with patch.dict("os.environ", {"AGENT_TOKEN": "private-fixture-token"}, clear=True), patch("agent.main.detect_compiler", return_value=compiler), patch("agent.main.build_tools", return_value={"volume": "fixture"}) as build, patch("agent.transport.run_connection", AsyncMock()) as connection:
                args = ["connect", "--device-id", "laptop", "--url", "wss://fixture/ws/agents/laptop", "--native-tools"] + (["--screen"] if screen else [])
                code, _, _ = self.call(args)
                self.assertEqual(code, 0); self.assertEqual(connection.call_args.args[3], {"volume": "fixture"})
                self.assertEqual("screen" in build.call_args.args[0], screen)

    def test_screen_without_local_native_enablement_is_rejected(self):
        with patch("agent.main.detect_compiler") as compiler:
            code, _, stderr = self.call(["connect", "--device-id", "laptop", "--url", "wss://fixture/ws/agents/laptop", "--screen"])
        self.assertEqual(code, 1); self.assertIn("requires --native-tools", stderr); compiler.assert_not_called()

    def test_invalid_stream_options_are_reported_without_starting_build(self):
        for args in (["--url", "ws://remote/ws/agent"], ["--url", "wss://fixture/ws/agent", "--screen-interval", "0"]):
            with self.subTest(args=args), patch("agent.main.build_tools") as build:
                code, _, stderr = self.call(["stream", *args])
                self.assertEqual(code, 1)
                self.assertIn("Agent error:", stderr)
                build.assert_not_called()

    def test_invalid_local_policy_stops_before_compiler_initialization(self):
        with patch.dict("os.environ", {"AGENT_TOKEN": "fixture"}, clear=True), patch("agent.validation.load_policies", side_effect=ValueError("invalid local policy")), patch("agent.main.detect_compiler") as detect:
            self.assertEqual(self.call(["stream", "--url", "wss://fixture/ws/agent"])[0], 1)
        detect.assert_not_called()

    def test_build_errors_and_interrupt_have_stable_exit_codes(self):
        for error, expected in ((CompilerError("compiler unavailable"), 1), (OSError("fixture missing"), 1), (ValueError("fixture invalid"), 1), (KeyboardInterrupt(), 130)):
            with self.subTest(error=type(error).__name__), patch("agent.main.build_tools", side_effect=error):
                self.assertEqual(self.call(["build"])[0], expected)

    def test_parser_rejects_unknown_tool_and_requires_command(self):
        for argv in ([], ["run", "unknown"], ["stream", "--bogus"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser().parse_args(argv)
        with patch.dict("os.environ", {"AGENT_BACKEND_URL": "wss://fixture/ws/agent"}):
            options = parser().parse_args(["stream"])
            self.assertEqual(options.url, "wss://fixture/ws/agent")
            self.assertFalse(options.screen)

    def test_module_entrypoint_invokes_cli_and_exits_with_returned_status(self):
        stdout = io.StringIO()
        # Execute the real module entrypoint without starting native programs.
        # Remove the imported module temporarily to avoid runpy's duplicate-load warning.
        with patch.dict(sys.modules), patch.object(sys, "argv", ["agent.main", "build"]), patch("agent.native.build_tools", return_value={"volume": Path("volume")}), redirect_stdout(stdout), self.assertRaises(SystemExit) as caught:
            sys.modules.pop("agent.main", None)
            runpy.run_module("agent.main", run_name="__main__", alter_sys=True)
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("volume: volume", stdout.getvalue())
