"""Parallel trusted builds, cache invalidation, and atomic artifact publication."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import queue
import unittest
from unittest.mock import patch

from agent.compiler import CompilerError
from agent.native import BUILD_LOCKS, TOOLS, build_tools, link_arguments


class FixtureCompiler:
    def __init__(self, path, system="Darwin", name="clang", barrier=None):
        self.path, self.system, self.name, self.barrier = str(path), system, name, barrier
        self.calls, self.lock = [], threading.Lock()

    def compile(self, source, output, **kwargs):
        with self.lock:
            self.calls.append((source, output, kwargs))
        if self.barrier:
            self.barrier.wait(timeout=3)
        output.write_bytes(b"trusted fixture executable")


class NativeBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "native").mkdir()
        for name in TOOLS:
            (self.root / "native" / f"{name}.c").write_text("int main(void) {return 0;}")
        compiler_path = self.root / "compiler"
        compiler_path.write_bytes(b"fixture compiler")
        self.compiler = FixtureCompiler(compiler_path)
        self.patcher = patch("agent.native.ROOT", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_all_five_builds_overlap_and_result_order_is_stable(self):
        self.compiler.barrier = threading.Barrier(5)
        result = build_tools(compiler=self.compiler)
        self.assertEqual(tuple(result), TOOLS)
        self.assertEqual(len(self.compiler.calls), 5)
        self.assertTrue(all(path.is_file() for path in result.values()))
        self.assertTrue(all(call[2]["timeout"] == 120 for call in self.compiler.calls))

    def test_warm_cache_does_not_compile_and_source_change_rebuilds_one_tool(self):
        first = build_tools(compiler=self.compiler)
        self.compiler.calls.clear()
        self.assertEqual(build_tools(compiler=self.compiler), first)
        self.assertEqual(self.compiler.calls, [])
        (self.root / "native" / "mouse.c").write_text("int main(void) {return 1;}")
        build_tools(compiler=self.compiler)
        self.assertEqual([call[0].stem for call in self.compiler.calls], ["mouse"])

    def test_changed_compiler_corrupt_metadata_and_missing_binary_rebuild(self):
        result = build_tools(("volume",), self.compiler)
        self.compiler.calls.clear()
        Path(self.compiler.path).write_bytes(b"updated compiler")
        build_tools(("volume",), self.compiler)
        binary = result["volume"]
        binary.with_suffix(".build.json").write_text("{bad json")
        build_tools(("volume",), self.compiler)
        binary.unlink()
        build_tools(("volume",), self.compiler)
        self.assertEqual(len(self.compiler.calls), 3)

    def test_concurrent_callers_share_one_build_for_the_same_cache_entry(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: build_tools(("volume",), self.compiler), range(4)))
        self.assertEqual(len(self.compiler.calls), 1)
        self.assertTrue(all(result == results[0] for result in results))

    def test_failed_rebuild_preserves_previous_artifact_and_metadata(self):
        binary = build_tools(("volume",), self.compiler)["volume"]
        metadata = binary.with_suffix(".build.json")
        before = (binary.read_bytes(), metadata.read_bytes())
        (self.root / "native" / "volume.c").write_text("changed fixture")
        def fail(source, output, **kwargs):
            output.write_bytes(b"partial compiler output")
            raise CompilerError("fixture compile error")
        with patch.object(self.compiler, "compile", side_effect=fail), self.assertRaises(CompilerError):
            build_tools(("volume",), self.compiler)
        self.assertEqual((binary.read_bytes(), metadata.read_bytes()), before)
        self.assertEqual(list(binary.parent.glob("volume-build-*")), [])

    def test_interrupted_build_cancels_other_active_compilers_and_cleans_staging(self):
        barrier = threading.Barrier(2)
        acknowledged = threading.Event()
        def compile(source, output, **kwargs):
            output.write_bytes(b"partial output")
            barrier.wait(timeout=2)
            if source.stem == "volume":
                raise KeyboardInterrupt()
            if not kwargs["cancel_event"].wait(timeout=2):
                raise AssertionError("Sibling compiler did not receive cancellation")
            acknowledged.set()
        with patch.object(self.compiler, "compile", side_effect=compile), self.assertRaises(KeyboardInterrupt):
            build_tools(("volume", "mouse"), self.compiler)
        self.assertTrue(acknowledged.is_set())
        self.assertEqual(list((self.root / ".native-bin").rglob("*.build.json")), [])
        self.assertEqual(list((self.root / ".native-bin").rglob("*-build-*")), [])

    def test_later_tool_failure_cancels_slow_earlier_tool_without_hiding_diagnostic(self):
        started, acknowledged = threading.Event(), threading.Event()
        def compile(source, output, **kwargs):
            if source.stem == "volume":
                started.set()
                if not kwargs["cancel_event"].wait(timeout=2):
                    raise AssertionError("Slow earlier compiler never received cancellation")
                acknowledged.set()
                raise CompilerError("cancelled slow compiler")
            if not started.wait(timeout=1):
                raise AssertionError("Earlier compiler never started")
            raise CompilerError("later compiler diagnostic")
        with patch.object(self.compiler, "compile", side_effect=compile), self.assertRaisesRegex(CompilerError, "later compiler diagnostic"):
            build_tools(("volume", "mouse"), self.compiler)
        self.assertTrue(acknowledged.is_set())

    def test_cancelled_build_waiting_for_same_tool_lock_never_launches_compiler(self):
        flags = queue.Queue()
        calls = []
        def compile(source, output, **kwargs):
            calls.append(source.stem)
            flags.put(kwargs["cancel_event"])
            raise CompilerError("fixture failure")
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            with BUILD_LOCKS["mouse"], patch.object(self.compiler, "compile", side_effect=compile):
                future = pool.submit(build_tools, ("volume", "mouse"), self.compiler)
                cancelled = flags.get(timeout=1)
                self.assertTrue(cancelled.wait(timeout=1))
            with self.assertRaises(CompilerError):
                future.result(timeout=1)
        finally:
            pool.shutdown(wait=True)
        self.assertEqual(calls, ["volume"])

    def test_missing_output_fails_without_cache_publication(self):
        with patch.object(self.compiler, "compile"), self.assertRaisesRegex(OSError, "regular executable"):
            build_tools(("volume",), self.compiler)
        self.assertEqual(list((self.root / ".native-bin").rglob("*.build.json")), [])

    def test_windows_output_and_framework_library_choices(self):
        for system, name in (("Windows", "msvc"), ("Windows", "gcc"), ("Darwin", "clang")):
            with self.subTest(system=system, compiler=name):
                self.compiler.system, self.compiler.name = system, name
                binaries = build_tools(("volume",), self.compiler)
                self.assertEqual(binaries["volume"].suffix, ".exe" if system == "Windows" else "")
                for tool in TOOLS:
                    args = link_arguments(tool, self.compiler)
                    if system == "Windows":
                        self.assertTrue(all(arg.endswith(".lib") if name == "msvc" else arg.startswith("-l") for arg in args))
                    else:
                        self.assertEqual(args.count("-framework"), len(args) // 2)

    def test_invalid_requests_fail_before_compiler_detection_and_empty_request_is_idle(self):
        with patch("agent.native.detect_compiler") as detect:
            self.assertEqual(build_tools(()), {})
            for workers in (0, 6, True, 1.5):
                with self.subTest(workers=workers), self.assertRaises(ValueError):
                    build_tools(workers=workers)
            with self.assertRaises(ValueError):
                build_tools(("unknown",))
        detect.assert_not_called()

    def test_duplicates_are_built_once_and_implicit_compiler_is_detected_once(self):
        with patch("agent.native.detect_compiler", return_value=self.compiler) as detect:
            self.assertEqual(tuple(build_tools(("volume", "volume"))), ("volume",))
        detect.assert_called_once()
        self.assertEqual(len(self.compiler.calls), 1)
