"""Opt-in enforcement checks against the shipped image and a real local engine.

JANUS_TEST_CONTAINER=1 requires the image to be built and its real enforcement
probe to succeed. Fixtures execute only through ContainerBoundary, never on the
host. All tracked containers must be absent before each test completes.
"""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.container import ContainerBoundary, elf_matches, sync_command
from agent.isolation import ResourceLimitExceeded, ResourceLimits


ENABLED = os.environ.get("JANUS_TEST_CONTAINER") == "1"
HEADER = """#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <time.h>
"""


class TrackedBoundary(ContainerBoundary):
    def __init__(self):
        self.owned = set()
        self.create_started = None
        self.attach_durations = []
        super().__init__()

    async def _call(self, arguments, **kwargs):
        if arguments and arguments[0] == "create":
            self.owned.add(arguments[arguments.index("--name") + 1])
            if self.create_started is not None:
                self.create_started.set()
        started = time.monotonic()
        try:
            return await super()._call(arguments, **kwargs)
        finally:
            if arguments and arguments[0] == "start":
                self.attach_durations.append(time.monotonic() - started)


@unittest.skipUnless(ENABLED, "Set JANUS_TEST_CONTAINER=1 to verify the real container boundary")
class RealContainerEnforcementTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.boundary = TrackedBoundary()
        if not cls.boundary.available:
            raise AssertionError(f"Opt-in container boundary unavailable: {cls.boundary.reason}")

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="janus-container-integration-")
        self.root = Path(self.tmp.name)
        self.before = set(self.boundary.owned)
        self.boundary.create_started = None
        self.sequence = 0

    async def asyncTearDown(self):
        try:
            for name in self.boundary.owned - self.before:
                result = await asyncio.to_thread(sync_command, [*self.boundary.command, "inspect", "--type", "container", name])
                self.assertNotEqual(result.returncode, 0, f"Owned container survived: {name}")
                self.assertTrue(any(message in result.stderr for message in (b"No such object", b"No such container")), result.stderr.decode("utf-8", "replace"))
        finally:
            self.tmp.cleanup()

    async def compile_source(self, body, *, limits=None):
        self.sequence += 1
        directory = self.root / str(self.sequence)
        directory.mkdir()
        source, executable = directory / "program.c", directory / "program"
        source.write_text(HEADER + body, encoding="utf-8")
        result = await self.boundary.compile(self.boundary.compiler, source, executable, limits or ResourceLimits(), ())
        self.assertTrue(result.success, f"Real compiler failed: {result.reason}: {result.stderr}")
        self.assertTrue(elf_matches(executable.read_bytes(), self.boundary.execution_target["architecture"]))
        return executable

    async def run_source(self, body, *, stdin="", limits=None, capabilities=frozenset()):
        executable = await self.compile_source(body)
        return await self.boundary.run(executable, stdin, limits or ResourceLimits(), capabilities)

    async def running_owned(self):
        result = await asyncio.to_thread(sync_command, [*self.boundary.command, "ps", "--format", "{{.Names}}"])
        self.assertEqual(result.returncode, 0)
        return set(result.stdout.decode().splitlines()) & (self.boundary.owned - self.before)

    async def wait_running(self, count=1, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            running = await self.running_owned()
            if len(running) >= count:
                return running
            await asyncio.sleep(.02)
        self.fail(f"Did not observe {count} owned running containers")

    async def test_real_compile_run_stdin_stderr_and_nonzero_exit(self):
        result = await self.run_source('int main(void) { char value[64]; if (!fgets(value, sizeof(value), stdin)) return 2; printf("received:%s", value); fprintf(stderr, "diagnostic\\n"); return 7; }', stdin="hello\n")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "received:hello\n", "diagnostic\n"))
        self.assertFalse(result.timed_out or result.output_limited or result.unavailable)

    async def test_compile_error_returns_diagnostics_without_exporting_an_artifact(self):
        source, output = self.root / "invalid.c", self.root / "invalid"
        source.write_text("int main(void) { this is invalid; }", encoding="utf-8")
        result = await self.boundary.compile(self.boundary.compiler, source, output, ResourceLimits(), ())
        self.assertFalse(result.success)
        self.assertEqual(result.reason, "COMPILE_ERROR")
        self.assertTrue(result.stderr)
        self.assertFalse(output.exists())

    async def test_private_host_paths_and_environment_credentials_are_absent(self):
        secret = self.root / "credential-fixture.txt"
        secret.write_text("integration-only-private-fixture", encoding="utf-8")
        body = f'''int main(void) {{
            FILE *f = fopen({json.dumps(str(secret))}, "r");
            if (f) {{ fclose(f); return 10; }}
            if (getenv("JANUS_TEST_SECRET") != NULL) return 11;
            f = fopen("/root/.ssh/id_rsa", "r"); if (f) {{ fclose(f); return 12; }}
            puts("host paths and credentials absent"); return 0;
        }}'''
        with patch.dict(os.environ, {"JANUS_TEST_SECRET": "integration-only-secret"}):
            result = await self.run_source(body, capabilities=frozenset({"files.read"}))
        self.assertEqual((result.returncode, result.stdout), (0, "host paths and credentials absent\n"))
        self.assertEqual(secret.read_text(), "integration-only-private-fixture")

    async def test_compiler_cannot_include_private_host_headers(self):
        secret = self.root / "private-header.h"
        secret.write_text('#define JANUS_PRIVATE "integration-only-credential"\n', encoding="utf-8")
        source, output = self.root / "include.c", self.root / "include"
        source.write_text(f'#include {json.dumps(str(secret))}\nint main(void) {{ return JANUS_PRIVATE[0]; }}\n', encoding="utf-8")
        result = await self.boundary.compile(self.boundary.compiler, source, output, ResourceLimits(), ())
        self.assertEqual(result.reason, "COMPILE_ERROR")
        self.assertFalse(output.exists())
        self.assertNotIn("integration-only-credential", result.stderr + result.stdout)

    async def test_scratch_write_requires_permission_and_each_run_is_fresh(self):
        executable = await self.compile_source('''int main(void) {
            if (access("/work/note", F_OK) == 0) return 10;
            int expected = getchar() == '1';
            int fd = open("/work/note", O_WRONLY|O_CREAT|O_TRUNC, 0600);
            if (!expected) { if (fd >= 0) return 11; if (errno != EPERM) return 12; puts("write denied"); return 0; }
            if (fd < 0 || write(fd, "private", 7) != 7) return 13;
            close(fd); FILE *f = fopen("/work/note", "r"); if (!f) return 14;
            char text[16] = {0}; if (!fgets(text, sizeof(text), f)) return 15; fclose(f);
            if (strcmp(text, "private")) return 16; puts("scratch written"); return 0;
        }''')
        denied = await self.boundary.run(executable, "0", ResourceLimits(), frozenset())
        granted = await self.boundary.run(executable, "1", ResourceLimits(), frozenset({"files.read", "files.write"}))
        again = await self.boundary.run(executable, "0", ResourceLimits(), frozenset())
        self.assertEqual((denied.returncode, denied.stdout), (0, "write denied\n"))
        self.assertEqual((granted.returncode, granted.stdout), (0, "scratch written\n"))
        self.assertEqual((again.returncode, again.stdout), (0, "write denied\n"))

    async def test_network_is_denied_even_with_all_supported_worker_permissions(self):
        result = await self.run_source('int main(void) { int fd=socket(AF_INET, SOCK_STREAM, 0); if (fd>=0 || errno!=EPERM) return 10; puts("network denied"); return 0; }', capabilities=frozenset({"files.read", "files.write", "process.spawn"}))
        self.assertEqual((result.returncode, result.stdout), (0, "network denied\n"))

    async def test_process_creation_requires_permission(self):
        executable = await self.compile_source('''int main(void) {
            int expected = getchar() == '1'; pid_t pid=fork();
            if (!expected) { if (pid>=0 || errno!=EPERM) return 10; puts("spawn denied"); return 0; }
            if (pid<0) return 11; if (pid==0) _exit(0);
            int status=0; if (waitpid(pid, &status, 0)!=pid || !WIFEXITED(status) || WEXITSTATUS(status)) return 12;
            puts("spawn allowed"); return 0;
        }''')
        denied = await self.boundary.run(executable, "0", ResourceLimits(), frozenset())
        granted = await self.boundary.run(executable, "1", ResourceLimits(), frozenset({"process.spawn"}))
        self.assertEqual((denied.returncode, denied.stdout), (0, "spawn denied\n"))
        self.assertEqual((granted.returncode, granted.stdout), (0, "spawn allowed\n"))

    async def test_process_cap_stops_a_granted_fork_tree(self):
        executable = await self.compile_source('''int main(void) {
            for (int n=0;n<100;n++) { pid_t pid=fork(); if(pid<0) { if(errno!=EAGAIN) return 10; sleep(5); return 11; } if(pid==0) { sleep(20); _exit(0); } }
            return 12;
        }''')
        started = time.monotonic()
        with self.assertRaises(ResourceLimitExceeded):
            await self.boundary.run(executable, "", replace(ResourceLimits(), processes=4), frozenset({"process.spawn"}))
        self.assertLess(self.boundary.attach_durations[-1], 3.5)
        self.assertLess(time.monotonic() - started, 25)

    async def test_exited_parent_cannot_leave_a_child_in_a_new_session(self):
        result = await self.run_source('''int main(void) {
            int ready[2]; if(pipe(ready)<0) return 10;
            pid_t pid=fork(); if(pid<0) return 11;
            if(pid==0) { close(ready[0]); if(setsid()<0) _exit(12); if(write(ready[1], "1", 1)!=1) _exit(13); close(ready[1]); sleep(20); _exit(0); }
            close(ready[1]); char marker; if(read(ready[0], &marker, 1)!=1 || marker!='1') return 14; close(ready[0]);
            printf("child:%ld\\n", (long)pid); fflush(stdout); return 0;
        }''', capabilities=frozenset({"process.spawn"}))
        self.assertEqual(result.returncode, 0)
        self.assertRegex(result.stdout, r"^child:\d+\n$")
        self.assertLess(result.elapsed_ms, 3000)
        # asyncTearDown verifies the whole owning namespace has been removed.

    async def test_stdout_and_stderr_overflow_stop_execution_at_independent_byte_caps(self):
        executable = await self.compile_source('''int main(void) {
            int fd=getchar()=='e' ? 2 : 1; char output[4096]; memset(output, 'x', sizeof(output));
            for (;;) if(write(fd, output, sizeof(output))<0) return 0;
        }''')
        limits = replace(ResourceLimits(), stdout_bytes=1024, stderr_bytes=512)
        for channel in ("o", "e"):
            with self.subTest(channel=channel):
                result = await self.boundary.run(executable, channel, limits, frozenset())
                self.assertTrue(result.output_limited)
                self.assertLessEqual(len(result.stdout.encode()), 1024)
                self.assertLessEqual(len(result.stderr.encode()), 512)
                self.assertFalse(result.timed_out)

    async def test_memory_cgroup_kills_an_allocation_without_killing_the_supervisor(self):
        executable = await self.compile_source('''int main(void) {
            FILE *score=fopen("/proc/self/oom_score_adj", "w"); if(!score) return 10;
            fputs("1000", score); fclose(score);
            size_t size=128UL*1024*1024; volatile unsigned char *memory=malloc(size);
            if(!memory) return 11;
            for(size_t n=0;n<size;n+=4096) memory[n]=1;
            puts("memory escaped"); return 12;
        }''')
        started = time.monotonic()
        with self.assertRaises(ResourceLimitExceeded):
            await self.boundary.run(executable, "", replace(ResourceLimits(), memory_bytes=64 * 1024 * 1024), frozenset({"files.write"}))
        self.assertLess(self.boundary.attach_durations[-1], 3.5)
        self.assertLess(time.monotonic() - started, 25)

    async def test_disk_quota_and_other_writable_mounts_cannot_be_bypassed(self):
        result = await self.run_source('''int main(void) {
            int outside=open("/tmp/janus-fill", O_WRONLY|O_CREAT, 0600); if(outside>=0) return 10;
            outside=open("/dev/shm/janus-fill", O_WRONLY|O_CREAT, 0600); if(outside>=0) return 11;
            int fd=open("/work/fill", O_WRONLY|O_CREAT|O_TRUNC, 0600); if(fd<0) return 12;
            char bytes[4096]; memset(bytes, 1, sizeof(bytes)); size_t total=0;
            for(;;) { ssize_t n=write(fd, bytes, sizeof(bytes)); if(n<0) { if(errno!=ENOSPC) return 13; break; } total+=(size_t)n; if(total>1024*1024) return 14; }
            close(fd); puts("disk budget enforced"); return 0;
        }''', limits=replace(ResourceLimits(), disk_bytes=1024 * 1024, memory_bytes=128 * 1024 * 1024), capabilities=frozenset({"files.write"}))
        self.assertEqual((result.returncode, result.stdout), (0, "disk budget enforced\n"))

    async def test_aggregate_cpu_budget_ends_a_busy_loop(self):
        executable = await self.compile_source('int main(void) { volatile unsigned long value=0; for(;;) value++; }')
        started = time.monotonic()
        try:
            result = await self.boundary.run(executable, "", replace(ResourceLimits(), cpu_seconds=1), frozenset())
        except ResourceLimitExceeded:
            pass
        else:
            self.assertTrue(result.timed_out)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(result.unavailable)
        # Trusted Docker CREATE may copy image layers. The guest deadline
        # starts at START/attach and remains independent of that allowance.
        self.assertLess(self.boundary.attach_durations[-1], 1.2)
        self.assertLess(time.monotonic() - started, 25)

    async def test_wall_deadline_stops_sleep_without_using_the_cpu_budget(self):
        executable = await self.compile_source('int main(void) { sleep(20); return 0; }')
        started = time.monotonic()
        result = await self.boundary.run(executable, "", replace(ResourceLimits(), run_seconds=.8), frozenset())
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(result.unavailable)
        self.assertLess(self.boundary.attach_durations[-1], 1.2)
        self.assertLess(time.monotonic() - started, 25)

    async def test_cancellation_finishes_namespace_removal_before_propagating(self):
        executable = await self.compile_source('int main(void) { sleep(20); return 0; }')
        task = asyncio.create_task(self.boundary.run(executable, "", ResourceLimits(), frozenset()))
        try:
            names = await self.wait_running()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            for name in names:
                inspection = await asyncio.to_thread(sync_command, [*self.boundary.command, "inspect", "--type", "container", name])
                self.assertNotEqual(inspection.returncode, 0)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_cancellation_during_create_cannot_leave_a_late_created_container(self):
        executable = await self.compile_source('int main(void) { sleep(20); return 0; }')
        before_run = set(self.boundary.owned)
        entered = asyncio.Event()
        self.boundary.create_started = entered
        task = asyncio.create_task(self.boundary.run(executable, "", ResourceLimits(), frozenset()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            names = self.boundary.owned - before_run
            self.assertEqual(len(names), 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 25)
            # A Docker daemon may finish CREATE after the client is interrupted.
            # Cancellation must await that completion and verified removal.
            for _ in range(2):
                for name in names:
                    inspection = await asyncio.to_thread(sync_command, [*self.boundary.command, "inspect", "--type", "container", name])
                    self.assertNotEqual(inspection.returncode, 0, f"Late creation survived cancellation: {name}")
                await asyncio.sleep(.1)
        finally:
            self.boundary.create_started = None
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_concurrent_compilation_and_execution_keep_artifacts_and_scratch_separate(self):
        def body(letter):
            return f'''int main(void) {{
                if(access("/work/state", F_OK)==0) return 10;
                FILE *f=fopen("/work/state", "w"); if(!f) return 11; fputs("{letter}", f); fclose(f);
                struct timespec delay={{2,0}}; nanosleep(&delay, NULL);
                f=fopen("/work/state", "r"); if(!f) return 12; int value=fgetc(f); fclose(f);
                if(value!='{letter}') return 13; puts("{letter}"); return 0;
            }}'''
        first, second = await asyncio.gather(self.compile_source(body("A")), self.compile_source(body("B")))
        tasks = [asyncio.create_task(self.boundary.run(executable, "", ResourceLimits(), frozenset({"files.read", "files.write"}))) for executable in (first, second)]
        try:
            await self.wait_running(count=2)
            results = await asyncio.gather(*tasks)
            self.assertEqual([(result.returncode, result.stdout) for result in results], [(0, "A\n"), (0, "B\n")])
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
