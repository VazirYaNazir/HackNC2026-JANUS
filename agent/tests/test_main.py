"""CLI parsing, startup authorization, native invocation, and error reporting."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import AsyncMock, patch

from agent.compiler import CCompiler, CompilerError
from agent.main import main, parser


class MainTests(unittest.TestCase):
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
