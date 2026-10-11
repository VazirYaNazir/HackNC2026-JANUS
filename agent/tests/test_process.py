"""Bounded pipe draining, exit handling, interrupts, and both OS cleanup branches."""
import asyncio
import io
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from agent.process import finish_thread, run_bounded


class ThreadOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_cancellation_propagates_without_reading_cancelled_task_exception(self):
        def cancel():
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await finish_thread(cancel)

    async def test_cancellation_waits_for_owned_file_reader_even_if_cancelled_twice(self):
        entered, release = threading.Event(), threading.Event()
        finished = threading.Event()
        def read():
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("Test reader was never released")
            finished.set()
            return "fixture"
        task = asyncio.create_task(finish_thread(read))
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        self.assertTrue(finished.is_set())

    async def test_cancelled_reader_failure_is_retrieved_before_propagating_cancellation(self):
        entered, release = threading.Event(), threading.Event()
        def read():
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("Test reader was never released")
            raise OSError("fixture file no longer available")
        task = asyncio.create_task(finish_thread(read))
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_python(self, source, **kwargs):
        return run_bounded([sys.executable, "-c", source], cwd=self.tmp.name, **kwargs)

    def test_stdout_stderr_nonzero_exit_and_utf8_replacement(self):
        result = self.run_python("import sys;sys.stdout.buffer.write(b'hello\\xff');sys.stderr.write('error');sys.exit(7)")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "hello�", "error"))
        self.assertFalse(result.timed_out or result.output_limited or result.unavailable)
        self.assertGreaterEqual(result.elapsed_ms, 0)

    def test_missing_command_is_an_unavailable_result_without_sensitive_os_message(self):
        result = run_bounded([str(Path(self.tmp.name) / "does-not-exist")], cwd=self.tmp.name)
        self.assertTrue(result.unavailable)
        self.assertIsNone(result.returncode)
        self.assertEqual((result.stdout, result.stderr), ("", ""))

    def test_cancelled_before_start_does_not_launch_subprocess(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch("agent.process.subprocess.Popen") as popen:
            result = run_bounded(["fixture"], cwd=self.tmp.name, cancel_event=cancelled)
        self.assertTrue(result.cancelled)
        popen.assert_not_called()

    def test_thread_cancellation_stops_and_reaps_running_process(self):
        cancelled, processes = threading.Event(), []
        real_popen = subprocess.Popen
        def popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            cancelled.set()
            return process
        with patch("agent.process.subprocess.Popen", side_effect=popen):
            result = self.run_python("import time;time.sleep(10)", cancel_event=cancelled)
        self.assertTrue(result.cancelled)
        self.assertIsNotNone(processes[0].poll())

    def test_simultaneous_large_stdout_and_stderr_are_drained_without_deadlock(self):
        source = "import sys,threading\na=threading.Thread(target=lambda:sys.stdout.buffer.write(b'a'*200000));a.start();sys.stderr.buffer.write(b'b'*200000);a.join()"
        result = self.run_python(source, stdout_limit=4096, stderr_limit=4096, timeout=2)
        self.assertTrue(result.output_limited)
        self.assertLessEqual(len(result.stdout), 4096)
        self.assertLessEqual(len(result.stderr), 4096)
        self.assertFalse(result.timed_out)

    def test_exact_output_budget_is_accepted(self):
        result = self.run_python("import sys;sys.stdout.write('x'*1024)", stdout_limit=1024)
        self.assertEqual(result.stdout, "x" * 1024)
        self.assertFalse(result.output_limited)

    @unittest.skipIf(os.name == "nt", "POSIX process group fixture")
    def test_exited_parent_with_inherited_child_pipes_is_cleaned_up(self):
        source = "import subprocess,sys\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)']);print(p.pid,flush=True)"
        result = self.run_python(source, timeout=1)
        self.assertEqual(result.returncode, 0)
        self.assertLess(result.elapsed_ms, 2000)
        child = int(result.stdout.strip())
        if sys.platform.startswith("linux"):
            status = Path(f"/proc/{child}/stat")
            # SIGKILL is asynchronous: pipe handles can close before the kernel
            # finishes transitioning the orphan to a zombie or reaping it.
            deadline = time.monotonic() + 2
            while True:
                try:
                    state = status.read_text().rsplit(")", 1)[1].split()[0]
                except FileNotFoundError:
                    break
                if state == "Z":
                    break
                self.assertLess(time.monotonic(), deadline, f"Child {child} remains alive in state {state}")
                time.sleep(0.01)

    @unittest.skipIf(os.name == "nt", "POSIX interruption fixture")
    def test_interrupt_kills_and_reaps_trusted_compiler_process(self):
        processes = []
        real_popen = subprocess.Popen
        def popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch("agent.process.subprocess.Popen", side_effect=popen), patch("agent.process.time.sleep", side_effect=KeyboardInterrupt()), self.assertRaises(KeyboardInterrupt):
            self.run_python("import time;time.sleep(10)")
        self.assertIsNotNone(processes[0].poll())

    def test_windows_taskkill_and_fallback_cleanup_paths_are_bounded(self):
        for failure in (None, OSError("taskkill unavailable"), subprocess.TimeoutExpired("taskkill", 3)):
            with self.subTest(failure=type(failure).__name__):
                process = MagicMock()
                process.pid, process.returncode = 123, None
                process.stdout, process.stderr = io.BytesIO(b"stdout"), io.BytesIO(b"stderr")
                process.poll.side_effect = lambda: process.returncode
                process.kill.side_effect = lambda: setattr(process, "returncode", -9)
                process.wait.return_value = -9
                with patch("agent.process.os.name", "nt"), patch("agent.process.Path", PureWindowsPath), patch("agent.process.subprocess.CREATE_NEW_PROCESS_GROUP", 512, create=True), patch("agent.process.subprocess.Popen", return_value=process) as popen, patch("agent.process.subprocess.run", side_effect=failure) as taskkill, patch("agent.process.time.monotonic", side_effect=[0, 1, 2]):
                    result = run_bounded(["fixture.exe"], cwd=self.tmp.name, timeout=0.01)
                self.assertTrue(result.timed_out)
                self.assertEqual((result.stdout, result.stderr), ("stdout", "stderr"))
                self.assertEqual(popen.call_args.kwargs["creationflags"], 512)
                self.assertEqual(taskkill.call_args.args[0][1:], ["/PID", "123", "/T", "/F"])
                self.assertEqual(taskkill.call_args.kwargs["timeout"], 3)
                process.kill.assert_called_once()
                process.wait.assert_called_once()

    def test_windows_cleanup_handles_taskkill_success_or_process_exit_race(self):
        for already_exited in (False, True):
            process = MagicMock()
            process.pid, process.returncode = 123, None
            process.stdout, process.stderr = io.BytesIO(), io.BytesIO()
            process.poll.side_effect = lambda: process.returncode
            process.wait.return_value = -9
            if not already_exited:
                process.kill.side_effect = OSError("process exited during kill")
            def taskkill(*args, **kwargs):
                if already_exited:
                    process.returncode = -9
            with patch("agent.process.os.name", "nt"), patch("agent.process.Path", PureWindowsPath), patch("agent.process.subprocess.CREATE_NEW_PROCESS_GROUP", 512, create=True), patch("agent.process.subprocess.Popen", return_value=process), patch("agent.process.subprocess.run", side_effect=taskkill), patch("agent.process.time.monotonic", side_effect=[0, 1, 2]):
                self.assertTrue(run_bounded(["fixture"], cwd=self.tmp.name, timeout=0.01).timed_out)
            self.assertEqual(process.kill.call_count, 0 if already_exited else 1)

    @unittest.skipIf(os.name == "nt", "POSIX cleanup branch")
    def test_missing_process_group_during_cleanup_is_harmless(self):
        process = MagicMock()
        process.stdout, process.stderr = io.BytesIO(), io.BytesIO()
        process.returncode = -9
        process.poll.return_value = None
        with patch("agent.process.subprocess.Popen", return_value=process), patch("agent.process.time.monotonic", side_effect=[0, 1, 2]), patch("agent.process.os.killpg", side_effect=ProcessLookupError()):
            result = run_bounded(["fixture"], cwd=self.tmp.name, timeout=0.01)
        self.assertTrue(result.timed_out)
        process.wait.assert_called_once()
