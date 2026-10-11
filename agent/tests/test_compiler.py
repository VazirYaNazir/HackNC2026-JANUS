"""Compiler command, diagnostics, discovery, and verified-fallback contracts."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.compiler import CCompiler, CompileResult, CompilerError, detect_compiler
from agent.process import ProcessResult


class CompilerCommandTests(unittest.TestCase):
    def test_c11_commands_preserve_paths_and_never_invoke_a_shell(self):
        source, output = Path("directory with spaces/program.c"), Path("directory with spaces/program.exe")
        for name, system in (("clang", "Darwin"), ("gcc", "Windows"), ("zig", "Windows")):
            with self.subTest(compiler=name):
                command = CCompiler(name, "compiler with spaces", system).command(source, output, ("-luser32",))
                self.assertEqual(command[0], "compiler with spaces")
                self.assertIn("-std=c11", command)
                self.assertIn(str(source), command)
                self.assertEqual(command[command.index("-o") + 1], str(output))
                self.assertEqual(command[-1], "-luser32")
                self.assertEqual(command.count("cc"), int(name == "zig"))

    def test_msvc_uses_c_mode_c11_and_separate_object_file(self):
        compiler = CCompiler("msvc", "cl", "Windows")
        command = compiler.command(Path("program.c"), Path("program.exe"), ("user32.lib",))
        for option in ("/TC", "/std:c11", "/Fe:program.exe", "/Fo:program.obj"):
            self.assertIn(option, command)
        self.assertEqual(command[-2:], ["/link", "user32.lib"])
        self.assertNotIn("/link", compiler.command(Path("program.c"), Path("program.exe")))

    def test_success_requires_exit_zero_regular_artifact_and_no_failure_reason(self):
        for code, exists, reason, success in ((0, True, None, True), (1, True, None, False), (0, False, None, False), (None, True, None, False), (0, True, "RESOURCE_LIMIT", False)):
            with self.subTest(code=code, exists=exists, reason=reason):
                self.assertEqual(CompileResult("clang", code, "", "", 1, exists, reason).success, success)

    def test_detailed_failures_delete_stale_or_partial_executable(self):
        cases = [(ProcessResult(None, unavailable=True), "COMPILER_UNAVAILABLE"),
                 (ProcessResult(None, cancelled=True), "COMPILE_CANCELLED"),
                 (ProcessResult(-9, timed_out=True), "COMPILE_TIMEOUT"),
                 (ProcessResult(-9, output_limited=True), "RESOURCE_LIMIT"),
                 (ProcessResult(1, "stdout", "stderr", 7), "COMPILE_ERROR"),
                 (ProcessResult(0), "COMPILER_UNAVAILABLE")]
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "program.c", Path(tmp) / "program"
            source.write_text("int main(void) {return 0;}")
            compiler = CCompiler("clang", "not-run", "Darwin")
            for process, reason in cases:
                with self.subTest(reason=reason):
                    output.write_bytes(b"previous executable")
                    with patch("agent.compiler.run_bounded", return_value=process) as runner:
                        result = compiler.compile_detailed(source, output, 2, ("-lm",), 128)
                    self.assertEqual(result.reason, reason)
                    self.assertEqual((result.stdout, result.stderr, result.elapsed_ms), (process.stdout, process.stderr, process.elapsed_ms))
                    self.assertFalse(output.exists())
                    self.assertEqual(runner.call_args.kwargs["stdout_limit"], 128)
                    self.assertEqual(runner.call_args.kwargs["timeout"], 2)

    def test_legacy_compile_error_contains_structured_result(self):
        result = CompileResult("gcc", 1, "diagnostic on stdout", "warning on stderr", 10, False, "COMPILE_ERROR")
        compiler = CCompiler("gcc", "not-run", "Darwin")
        with patch.object(CCompiler, "compile_detailed", return_value=result), self.assertRaises(CompilerError) as caught:
            compiler.compile(Path("program.c"), Path("program"))
        self.assertIs(caught.exception.result, result)
        self.assertIn("diagnostic on stdout", str(caught.exception))
        self.assertIn("warning on stderr", str(caught.exception))
        with patch.object(CCompiler, "compile_detailed", return_value=CompileResult("gcc", 0, "", "", 1, True)):
            self.assertTrue(compiler.compile(Path("program.c"), Path("program")).success)


class CompilerDiscoveryTests(unittest.TestCase):
    def test_preferred_available_compiler_is_probed_on_each_platform(self):
        for system in ("Darwin", "Windows"):
            with self.subTest(system=system), patch("agent.compiler.platform.system", return_value=system), patch("agent.compiler.shutil.which", return_value="/fixture/clang"), patch.object(CCompiler, "compile") as compile, patch("agent.compiler.install_zig") as installer:
                self.assertEqual(detect_compiler().name, "clang")
                compile.assert_called_once()
                installer.assert_not_called()

    def test_windows_tries_msvc_when_clang_and_gcc_are_missing(self):
        with patch("agent.compiler.platform.system", return_value="Windows"), patch("agent.compiler.shutil.which", side_effect=lambda name: "cl.exe" if name == "cl" else None), patch.object(CCompiler, "compile"):
            self.assertEqual(detect_compiler(), CCompiler("msvc", "cl.exe", "Windows"))

    def test_broken_system_compiler_is_not_selected(self):
        seen = []
        def probe(compiler, source, executable, **kwargs):
            seen.append(compiler.name)
            self.assertIn("int main(void)", source.read_text())
            if compiler.name == "clang":
                raise OSError("fixture compiler cannot launch")
        with patch("agent.compiler.platform.system", return_value="Darwin"), patch("agent.compiler.shutil.which", side_effect=lambda name: name), patch.object(CCompiler, "compile", probe):
            self.assertEqual(detect_compiler().name, "gcc")
        self.assertEqual(seen, ["clang", "gcc"])

    def test_missing_or_nonworking_system_compilers_use_and_probe_fallback(self):
        for missing in (False, True):
            def probe(compiler, *args, **kwargs):
                if compiler.name != "zig":
                    raise CompilerError("fixture failure")
            with self.subTest(missing=missing), patch("agent.compiler.platform.system", return_value="Windows"), patch("agent.compiler.shutil.which", return_value=None if missing else "broken-compiler"), patch.object(CCompiler, "compile", probe), patch("agent.compiler.install_zig", return_value=Path("zig.exe")) as installer:
                self.assertEqual(detect_compiler(), CCompiler("zig", "zig.exe", "Windows"))
                installer.assert_called_once()

    def test_fallback_failure_is_reported_as_environment_failure(self):
        for failure in (RuntimeError("bad checksum"), OSError("download unavailable"), CompilerError("probe failed")):
            with self.subTest(failure=type(failure).__name__), patch("agent.compiler.platform.system", return_value="Darwin"), patch("agent.compiler.shutil.which", return_value=None), patch("agent.compiler.install_zig", side_effect=failure), self.assertRaisesRegex(CompilerError, "Automatic compiler installation failed"):
                detect_compiler()

    def test_unsupported_platform_never_probes_or_installs(self):
        with patch("agent.compiler.platform.system", return_value="Linux"), patch("agent.compiler.shutil.which") as which, patch("agent.compiler.install_zig") as installer, self.assertRaisesRegex(CompilerError, "Unsupported operating system"):
            detect_compiler()
        which.assert_not_called()
        installer.assert_not_called()
