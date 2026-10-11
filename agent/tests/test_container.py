"""Local Docker readiness, bounded exchanges, and artifact admission.

These tests replace the Docker client, never execute fixture artifacts, and
verify enforcement failures fail closed before any native side effects.
"""
import asyncio
import base64
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from agent import container
from agent.broker import BrokerError
from agent.compiler import CCompiler
from agent.isolation import BoundaryUnavailable, REQUIRED_GUARANTEES, ResourceLimitExceeded, ResourceLimits
from agent.process import ProcessResult


def elf64(machine=62, kind=2):
    ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, kind, machine, 1,
                         0x400000, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    segment = struct.pack("<IIQQQQQQ", 1, 5, 0, 0x400000, 0x400000,
                          120, 120, 4096)
    return header + segment


def completed(code=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class BoundaryFixtures:
    def boundary(self, ready=True):
        with patch("agent.container.shutil.which", return_value=None):
            boundary = container.ContainerBoundary()
        self.addCleanup(boundary._config.cleanup)
        if ready:
            boundary.docker = "/trusted/docker"
            boundary.endpoint = "unix:///fixture/docker.sock"
            boundary.image = "sha256:" + "a" * 64
            boundary.available, boundary.reason = True, None
            boundary.build_guarantees = boundary.run_guarantees = REQUIRED_GUARANTEES
            boundary.execution_target = {"platform": "Linux", "architecture": "x86_64",
                                         "profile": "portable-c", "capabilities": sorted(container.WORKER_CAPABILITIES), "dependencies": []}
            boundary.compiler = CCompiler("worker-gcc", boundary.docker, "Linux")
        return boundary

    def files(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name)
        source, executable = path / "program.c", path / "program"
        source.write_text("int main(void){return 0;}", encoding="utf-8")
        executable.write_bytes(elf64())
        return source, executable


class ClientConfigurationTests(BoundaryFixtures, unittest.TestCase):
    def test_private_config_discovers_mac_bundled_homebrew_and_user_plugins_without_auth(self):
        existing = {"/user/.docker/cli-plugins", "/Applications/Docker.app/Contents/Resources/cli-plugins", "/opt/homebrew/lib/docker/cli-plugins"}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "private", "HOME": "/user"}, clear=True), \
             patch("agent.container.Path.home", return_value=Path("/user")), \
             patch("agent.container.platform.system", return_value="Darwin"), \
             patch("agent.container.Path.is_dir", lambda path: str(path) in existing):
            boundary = self.boundary(False)
            config = json.loads((Path(boundary._config.name) / "config.json").read_text())
            self.assertEqual(config, {"cliPluginsExtraDirs": ["/user/.docker/cli-plugins", "/Applications/Docker.app/Contents/Resources/cli-plugins", "/opt/homebrew/lib/docker/cli-plugins"]})
            self.assertNotIn("private", json.dumps(config))
            self.assertNotIn("GEMINI_API_KEY", container.client_environment())

    def test_windows_plugin_paths_respect_original_config_but_never_copy_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "original"
            (original / "cli-plugins").mkdir(parents=True)
            (original / "config.json").write_text('{"auths":{"registry":"private"},"credsStore":"desktop","currentContext":"remote"}')
            program_files = Path(folder) / "apps"
            bundled = program_files / "Docker" / "Docker" / "resources" / "cli-plugins"
            bundled.mkdir(parents=True)
            with patch.dict(os.environ, {"DOCKER_CONFIG": str(original), "ProgramFiles": str(program_files)}, clear=True), \
                 patch("agent.container.platform.system", return_value="Windows"):
                boundary = self.boundary(False)
                config = json.loads((Path(boundary._config.name) / "config.json").read_text())
                self.assertEqual(config, {"cliPluginsExtraDirs": [str(original / "cli-plugins"), str(bundled)]})
                self.assertNotEqual(Path(boundary._config.name), original)
                self.assertNotIn("DOCKER_CONFIG", container.client_environment())
                self.assertEqual(set(config), {"cliPluginsExtraDirs"})

    def test_missing_plugin_directories_produce_only_empty_discovery_config(self):
        with patch.dict(os.environ, {}, clear=True), patch("agent.container.platform.system", return_value="Linux"), \
             patch("agent.container.Path.home", return_value=Path("/missing")), patch("agent.container.Path.is_dir", return_value=False):
            boundary = self.boundary(False)
            self.assertEqual(json.loads((Path(boundary._config.name) / "config.json").read_text()), {"cliPluginsExtraDirs": []})

    def test_trusted_creation_allowance_is_separate_from_guest_limits(self):
        boundary = self.boundary()
        self.assertEqual(boundary.operation_timeout(ResourceLimits(), "compile"), 50)
        self.assertEqual(boundary.operation_timeout(ResourceLimits(), "run"), 23)
        self.assertEqual(boundary.operation_timeout(ResourceLimits(run_seconds=1), "run"), 21)

    def test_sync_creation_barrier_never_kills_a_pending_daemon_transaction(self):
        with patch("agent.container.time", SimpleNamespace(monotonic=Mock(side_effect=[0, 21]))), \
             patch("agent.container.subprocess.run", return_value=completed()) as run, self.assertRaises(subprocess.TimeoutExpired):
            container.sync_command(["docker", "create"], timeout=20, completion_barrier=True)
        self.assertIsNone(run.call_args.kwargs["timeout"])
        with patch("agent.container.time", SimpleNamespace(monotonic=Mock(side_effect=[0, 1]))), \
             patch("agent.container.subprocess.run", return_value=completed()) as run:
            self.assertEqual(container.sync_command(["docker", "create"], timeout=20, completion_barrier=True).returncode, 0)
        self.assertIsNone(run.call_args.kwargs["timeout"])

    def test_client_environment_strips_credentials_remote_overrides_and_empty_values(self):
        environ = {"PATH": "/trusted/bin", "SystemRoot": "C:\\Windows", "WINDIR": "C:\\Windows",
                   "TEMP": "/temporary", "TMP": "", "TMPDIR": "/tmp", "HOME": "/private/home",
                   "GEMINI_API_KEY": "fixture-secret", "JANUS_AGENT_TOKEN": "fixture-token",
                   "DOCKER_HOST": "tcp://remote:2375", "DOCKER_CONFIG": "/credentials", "DOCKER_TLS_VERIFY": "1"}
        with patch.dict(os.environ, environ, clear=True):
            self.assertEqual(container.client_environment(), {name: environ[name] for name in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMPDIR")})
            with patch("agent.container.subprocess.run", return_value=completed()) as run:
                container.sync_command(["docker", "info"], timeout=2, input_data=b"request")
                self.assertEqual(run.call_args.kwargs["env"], container.client_environment())
                self.assertEqual(run.call_args.kwargs["input"], b"request")
                self.assertEqual(run.call_args.kwargs["timeout"], 2)
                self.assertFalse(run.call_args.kwargs["check"])

    def test_worker_identity_changes_with_either_pinned_source(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(container, "WORKER_DIR", Path(folder)):
            path = Path(folder)
            (path / "Dockerfile").write_bytes(b"FROM pinned")
            (path / "runner.py").write_bytes(b"first runner")
            tag, digest = container.worker_identity()
            expected = hashlib.sha256(b"DockerfileFROM pinnedrunner.pyfirst runner").hexdigest()
            self.assertEqual(digest, expected)
            self.assertEqual(tag, "janus-c-worker:" + digest[:24])
            (path / "runner.py").write_bytes(b"second runner")
            self.assertNotEqual(container.worker_identity(), (tag, digest))

    def test_provision_environment_includes_only_proxy_settings_and_never_provider_credentials(self):
        environ = {"PATH": "/trusted/bin", "HTTP_PROXY": "http://proxy:80", "HTTPS_PROXY": "http://proxy:80",
                   "NO_PROXY": "localhost", "http_proxy": "http://proxy:80", "https_proxy": "http://proxy:80",
                   "no_proxy": "localhost", "GEMINI_API_KEY": "fixture", "SSL_CERT_FILE": "/public/ca.pem", "HOME": "/private"}
        with patch.dict(os.environ, environ, clear=True):
            self.assertEqual(container.client_environment(), {"PATH": "/trusted/bin"})
            self.assertEqual(container.client_environment(provision=True), {key: value for key, value in environ.items() if key == "PATH" or key in container.PROXY_NAMES})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(container.provisioning_options(), [])

    def test_build_proxy_arguments_contain_names_only_and_public_ca_is_a_buildkit_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            certificate = Path(folder) / "public.pem"
            certificate.write_bytes(b"-----BEGIN CERTIFICATE-----\npublic-fixture\n-----END CERTIFICATE-----")
            with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy:80", "SSL_CERT_FILE": str(certificate)}, clear=True), \
                 patch("agent.container.ssl.create_default_context") as parse:
                arguments = container.provisioning_options()
                self.assertEqual(arguments, ["--build-arg", "HTTP_PROXY", "--secret", "id=janus-build-ca,src=" + str(certificate)])
                self.assertNotIn("http://proxy:80", arguments)
                parse.assert_called_once_with(cafile=str(certificate))

    def test_invalid_missing_private_key_and_oversized_trust_bundles_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ca.pem"
            with patch.dict(os.environ, {"SSL_CERT_FILE": str(path)}, clear=True), self.assertRaises(ValueError):
                container.provisioning_options()
            for data in (b"-----BEGIN PRIVATE KEY-----\nprivate fixture", b"x" * (4 * 1024 * 1024 + 1), b"not a certificate"):
                path.write_bytes(data)
                with self.subTest(kind=data[:32]), patch.dict(os.environ, {"SSL_CERT_FILE": str(path)}, clear=True), self.assertRaises((ValueError, OSError)):
                    container.provisioning_options()

    def test_private_container_options_enforce_requested_budgets_without_host_access(self):
        limits = ResourceLimits(memory_bytes=128 * 1024 * 1024, disk_bytes=4 * 1024 * 1024, processes=4)
        options = container.container_options("job", "sha256:fixture", limits)
        for key, value in (("--memory", str(limits.memory_bytes)), ("--memory-swap", str(limits.memory_bytes)),
                           ("--pids-limit", "4"), ("--network", "none"), ("--cap-drop", "ALL"),
                           ("--security-opt", "no-new-privileges"), ("--log-driver", "none"), ("--user", "65532:65532")):
            self.assertEqual(options[options.index(key) + 1], value)
        self.assertIn("--read-only", options)
        self.assertIn("/work:rw,exec,nosuid,nodev,size=4194304,mode=1777", options)
        self.assertNotIn("--privileged", options)
        self.assertNotIn("--mount", options)
        self.assertNotIn("--volume", options)
        self.assertEqual(options.count("--env"), 1)
        self.assertIn("LANG=C.UTF-8", options)

    def test_no_docker_stays_unavailable_and_command_cannot_be_used(self):
        boundary = self.boundary(False)
        self.assertEqual(boundary.describe(), {"available": False, "reason": "DOCKER_NOT_INSTALLED", "execution_target": None})
        self.assertFalse(boundary.can_build())
        with self.assertRaises(BoundaryUnavailable):
            _ = boundary.command
        boundary.docker = "/trusted/docker"
        with self.assertRaises(BoundaryUnavailable):
            _ = boundary.command

    def test_dynamic_host_capabilities_are_described_without_mutating_target(self):
        boundary = self.boundary()
        self.assertEqual(boundary.run_capabilities, container.WORKER_CAPABILITIES)
        boundary.broker = SimpleNamespace(capabilities=frozenset({"audio.volume"}))
        described = boundary.describe()
        self.assertIn("audio.volume", described["execution_target"]["capabilities"])
        self.assertNotIn("audio.volume", boundary.execution_target["capabilities"])
        self.assertTrue(boundary.can_run(frozenset({"audio.volume"})))
        self.assertFalse(boundary.can_run(frozenset({"network"})))


class ReadinessProbeTests(BoundaryFixtures, unittest.TestCase):
    def probe(self, *, endpoint="unix:///fixture/docker.sock", engine=None, metadata=None,
              context_code=0, info_code=0, image_code=0, create_code=0,
              response=None, start_code=0, remove_code=0, remove_stderr=b"", exception=None):
        engine = engine if engine is not None else {"OSType": "linux", "CgroupVersion": "2", "MemoryLimit": True,
            "SwapLimit": True, "PidsLimit": True, "CpuCfsPeriod": True, "CpuCfsQuota": True,
            "SecurityOptions": ["name=seccomp,profile=builtin"], "Architecture": "amd64"}
        metadata = metadata if metadata is not None else [{"Config": {"Labels": {"org.janus.worker.digest": "fixture-digest"}}, "Id": "sha256:" + "a" * 64}]
        response = response if response is not None else {"ready": True, "guarantees": sorted(REQUIRED_GUARANTEES),
                                                         "architecture": "arm64" if isinstance(engine, dict) and engine.get("Architecture") in {"arm64", "aarch64"} else "x86_64"}
        calls = []
        def command(argv, **kwargs):
            calls.append((argv, kwargs))
            if exception:
                raise exception
            if "context" in argv:
                return completed(context_code, json.dumps([{"Endpoints": {"docker": {"Host": endpoint}}}]).encode())
            if "info" in argv:
                return completed(info_code, json.dumps(engine).encode())
            if "image" in argv:
                return completed(image_code, json.dumps(metadata).encode())
            if "create" in argv:
                return completed(create_code)
            if "start" in argv:
                return completed(start_code, json.dumps(response).encode())
            if "rm" in argv:
                return completed(remove_code, stderr=remove_stderr)
            self.fail(f"Unexpected Docker command {argv}")
        with patch.dict(os.environ, {"DOCKER_HOST": ""}), patch("agent.container.shutil.which", return_value="/trusted/docker"), \
             patch("agent.container.worker_identity", return_value=("janus-fixture", "fixture-digest")), \
             patch("agent.container.sync_command", side_effect=command):
            boundary = container.create_boundary()
        self.addCleanup(boundary._config.cleanup)
        return boundary, calls

    def test_successful_probe_requires_complete_enforcement_and_always_removes_probe(self):
        for architecture in ("amd64", "x86_64", "arm64", "aarch64"):
            engine = {"OSType": "linux", "CgroupVersion": 2, "MemoryLimit": True, "SwapLimit": True,
                      "PidsLimit": True, "CpuCfsPeriod": True, "CpuCfsQuota": True,
                      "SecurityOptions": ["name=seccomp"], "Architecture": architecture}
            with self.subTest(architecture=architecture):
                boundary, calls = self.probe(engine=engine)
                self.assertTrue(boundary.can_build())
                self.assertTrue(boundary.can_run(container.WORKER_CAPABILITIES))
                self.assertEqual(boundary.image, "sha256:" + "a" * 64)
                self.assertIsNone(boundary.reason)
                self.assertEqual(boundary.execution_target["architecture"], "arm64" if architecture in {"arm64", "aarch64"} else "x86_64")
                self.assertIn("rm", calls[-1][0])
                probe_request = json.loads(next(kwargs["input_data"] for args, kwargs in calls if "start" in args))
                self.assertEqual(probe_request, {"phase": "probe", "limits": asdict(ResourceLimits())})
                self.assertEqual(next(kwargs["timeout"] for args, kwargs in calls if "start" in args), 5)
                create = next(kwargs for args, kwargs in calls if "create" in args)
                self.assertEqual(create["timeout"], 20)
                self.assertTrue(create["completion_barrier"])

    def test_explicit_local_windows_pipe_bypasses_context_but_remote_endpoints_fail_closed(self):
        for endpoint, ready in (("npipe:////./pipe/docker_engine", True), ("tcp://localhost:2375", False),
                                ("ssh://host", False), ("unix://relative", False), (42, False)):
            with self.subTest(endpoint=endpoint):
                boundary, calls = self.probe(endpoint=endpoint)
                self.assertEqual(boundary.available, ready)
                if not ready:
                    self.assertEqual(boundary.reason, "DOCKER_LOCAL_ENGINE_REQUIRED")
                    self.assertFalse(any("create" in args for args, _ in calls))
        with patch.dict(os.environ, {"DOCKER_HOST": " tcp://remote:2375 "}), \
             patch("agent.container.shutil.which", return_value="docker"), patch("agent.container.sync_command") as command:
            boundary = container.ContainerBoundary()
            self.addCleanup(boundary._config.cleanup)
            self.assertEqual(boundary.reason, "DOCKER_LOCAL_ENGINE_REQUIRED")
            command.assert_not_called()

    def test_missing_context_engine_image_or_supported_architecture_never_claims_available(self):
        for arguments, expected in (({"context_code": 1}, "DOCKER_NOT_RUNNING"), ({"info_code": 1}, "DOCKER_NOT_RUNNING"),
                                    ({"image_code": 1}, "WORKER_IMAGE_MISSING"),
                                    ({"engine": {"OSType": "windows"}}, "DOCKER_LINUX_ENGINE_REQUIRED")):
            with self.subTest(arguments=arguments):
                boundary, _ = self.probe(**arguments)
                self.assertEqual(boundary.reason, expected)
                self.assertFalse(boundary.can_build())
        engine = {"OSType": "linux", "CgroupVersion": "2", "MemoryLimit": True, "SwapLimit": True,
                  "PidsLimit": True, "CpuCfsPeriod": True, "CpuCfsQuota": True, "SecurityOptions": ["seccomp"], "Architecture": "ppc64"}
        boundary, _ = self.probe(engine=engine)
        self.assertEqual(boundary.reason, "WORKER_ARCHITECTURE_UNSUPPORTED")

    def test_every_cgroup_limit_and_seccomp_are_required_before_container_creation(self):
        engine = {"OSType": "linux", "CgroupVersion": "2", "MemoryLimit": True, "SwapLimit": True,
                  "PidsLimit": True, "CpuCfsPeriod": True, "CpuCfsQuota": True, "SecurityOptions": ["seccomp"], "Architecture": "amd64"}
        for key, value in (("CgroupVersion", "1"), ("MemoryLimit", 1), ("SwapLimit", False),
                           ("PidsLimit", False), ("CpuCfsPeriod", False), ("CpuCfsQuota", False), ("SecurityOptions", [])):
            with self.subTest(key=key):
                boundary, calls = self.probe(engine={**engine, key: value})
                self.assertEqual(boundary.reason, "WORKER_ENFORCEMENT_UNAVAILABLE")
                self.assertFalse(any("create" in args for args, _ in calls))

    def test_mutable_tag_wrong_digest_and_invalid_image_ids_cannot_attest_worker(self):
        for metadata in ([{}], [{"Config": {"Labels": {"org.janus.worker.digest": "wrong"}}, "Id": "sha256:" + "a" * 64}],
                         *([{ "Config": {"Labels": {"org.janus.worker.digest": "fixture-digest"}}, "Id": value}] for value in (None, 12, "sha256:short", "prefix:" + "a" * 64))):
            with self.subTest(metadata=metadata):
                boundary, calls = self.probe(metadata=metadata)
                self.assertEqual(boundary.reason, "WORKER_IMAGE_MISSING")
                self.assertFalse(any("create" in args for args, _ in calls))

    def test_failed_attestation_or_creation_is_removed_and_remains_blocked(self):
        cases = ({"create_code": 1}, {"start_code": 1}, {"response": {"ready": False}},
                 {"response": {"ready": True, "guarantees": ["timeout"], "architecture": "x86_64"}},
                 {"response": {"ready": True, "guarantees": sorted(REQUIRED_GUARANTEES), "architecture": "arm64"}},
                 {"remove_code": 1, "remove_stderr": b"permission denied"})
        for case in cases:
            with self.subTest(case=case):
                boundary, calls = self.probe(**case)
                self.assertFalse(boundary.available)
                self.assertFalse(boundary.can_run())
                self.assertEqual(boundary.reason, "WORKER_ENFORCEMENT_UNAVAILABLE")
                self.assertIn("rm", calls[-1][0])
        boundary, _ = self.probe(remove_code=1, remove_stderr=b"No such container")
        self.assertTrue(boundary.available)

    def test_malformed_engine_metadata_and_client_errors_are_sanitized(self):
        for case in ({"metadata": []}, {"metadata": [12]}, {"metadata": [{"Config": []}]}, {"engine": []}, {"exception": OSError("private path")},
                     {"exception": subprocess.TimeoutExpired("docker", 3)}, {"response": []}):
            with self.subTest(case=case):
                # Invalid object types must not leak metadata/private diagnostics.
                boundary, _ = self.probe(**case)
                self.assertEqual(boundary.reason, "WORKER_ENFORCEMENT_UNAVAILABLE")
                self.assertFalse(boundary.available)


class ResultValidationTests(unittest.TestCase):
    def test_valid_success_failure_and_flags_preserve_result(self):
        limits = ResourceLimits()
        self.assertEqual(container.checked_result({"returncode": 0}, limits), ProcessResult(0))
        self.assertEqual(container.checked_result({"returncode": None, "timed_out": True, "stdout": "output", "stderr": "error", "elapsed_ms": 5}, limits),
                         ProcessResult(None, "output", "error", 5, timed_out=True))
        self.assertEqual(container.checked_result({"returncode": -2147483648, "cancelled": True}, limits).returncode, -2147483648)

    def test_invalid_field_types_ranges_and_missing_code_are_rejected(self):
        cases = [{}, {"returncode": True}, {"returncode": 2 ** 31}, {"returncode": -(2 ** 31) - 1},
                 {"returncode": "0"}, {"returncode": 0, "stdout": []}, {"returncode": 0, "stderr": None},
                 {"returncode": 0, "elapsed_ms": -1}, {"returncode": 0, "elapsed_ms": True},
                 *({"returncode": 0, key: 1} for key in ("timed_out", "output_limited", "unavailable", "cancelled", "resource_limited"))]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(BoundaryUnavailable):
                container.checked_result(response, ResourceLimits())

    def test_resource_and_utf8_output_limits_include_both_streams_and_diagnostics(self):
        limits = ResourceLimits(stdout_bytes=4, stderr_bytes=3, diagnostics_bytes=2)
        self.assertEqual(container.checked_result({"returncode": 0, "stdout": "éé", "stderr": "abc"}, limits).stdout, "éé")
        for response, diagnostics in (({"returncode": 0, "stdout": "ééa"}, False), ({"returncode": 0, "stderr": "éé"}, False),
                                      ({"returncode": 0, "stdout": "abc"}, True), ({"returncode": 0, "stderr": "abc"}, True),
                                      ({"returncode": 0, "resource_limited": True}, False)):
            with self.subTest(response=response), self.assertRaises(ResourceLimitExceeded):
                container.checked_result(response, limits, diagnostics=diagnostics)

    def test_only_portable_target_elf_is_admitted(self):
        self.assertTrue(container.elf_matches(elf64(), "x86_64"))
        self.assertTrue(container.elf_matches(elf64(183), "arm64"))
        for data, architecture in ((b"", "x86_64"), (b"MZ" + bytes(200), "x86_64"), (elf64(), "arm64"),
                                   (elf64(), "unknown"), (elf64().replace(b"\x7fELF", b"BAD!", 1), "x86_64")):
            with self.subTest(architecture=architecture):
                self.assertFalse(container.elf_matches(data, architecture))
        for offset, data in ((7, b"\xff"), (16, b"\x01\x00"), (20, bytes(4)), (52, b"\x01\x00")):
            binary = bytearray(elf64())
            binary[offset:offset + len(data)] = data
            with self.subTest(offset=offset):
                self.assertFalse(container.elf_matches(binary, "x86_64"))


class FakeInput:
    def __init__(self, error=None):
        self.data = b""
        self.closed = False
        self.error = error
    def write(self, data):
        self.data += data
    async def drain(self):
        if self.error:
            raise self.error
    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, stdout=b"output", stderr=b"error", *, finished=True, input_error=None):
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stderr.feed_data(stderr)
        if finished:
            self.stdout.feed_eof()
            self.stderr.feed_eof()
        self.stdin = FakeInput(input_error)
        self.returncode = 0 if finished else None
        self.kills = 0
        self.waits = 0
    def kill(self):
        self.kills += 1
        self.returncode = -9
    async def wait(self):
        self.waits += 1
        return self.returncode


class ClientProcessTests(BoundaryFixtures, unittest.IsolatedAsyncioTestCase):
    async def test_cli_receives_only_local_endpoint_empty_config_and_clean_environment(self):
        boundary, process = self.boundary(), FakeProcess()
        with patch("agent.container.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as launch:
            self.assertEqual(await boundary._call(["info"], input_data=b"request"), (0, b"output", b"error"))
        args, kwargs = launch.call_args
        self.assertEqual(args, (*boundary.command, "info"))
        self.assertEqual(kwargs["env"], container.client_environment())
        self.assertEqual(process.stdin.data, b"request")
        self.assertTrue(process.stdin.closed)
        self.assertEqual(process.kills, 0)
        self.assertGreaterEqual(process.waits, 1)

    async def test_proxy_configuration_is_forwarded_for_build_only(self):
        boundary = self.boundary()
        with patch.dict(os.environ, {"PATH": "/trusted", "HTTPS_PROXY": "http://proxy:80", "GEMINI_API_KEY": "fixture"}, clear=True):
            for arguments, expected_proxy in ((["build", "fixture"], True), (["start", "fixture"], False), ([], False)):
                process = FakeProcess()
                with self.subTest(arguments=arguments), patch("agent.container.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as launch:
                    await boundary._call(arguments)
                    environment = launch.call_args.kwargs["env"]
                    self.assertEqual("HTTPS_PROXY" in environment, expected_proxy)
                    self.assertNotIn("GEMINI_API_KEY", environment)

    async def test_exit_race_during_kill_still_reaps_the_process(self):
        process = FakeProcess(finished=False)
        def gone():
            process.returncode = -9
            raise ProcessLookupError
        process.kill = gone
        with patch("agent.container.asyncio.create_subprocess_exec", AsyncMock(return_value=process)), self.assertRaises(TimeoutError):
            await self.boundary()._call(["start"], timeout=.01)
        self.assertGreater(process.waits, 0)

    async def test_broken_stdin_is_closed_and_worker_is_reaped(self):
        for error in (BrokenPipeError(), ConnectionResetError()):
            process = FakeProcess(input_error=error)
            with self.subTest(error=type(error).__name__), patch("agent.container.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                self.assertEqual((await self.boundary()._call(["start"]))[0], 0)
                self.assertTrue(process.stdin.closed)
                self.assertGreater(process.waits, 0)

    async def test_each_output_stream_has_a_hard_bound_and_overflow_kills_and_reaps(self):
        for stdout, stderr in ((b"12345", b""), (b"", b"12345")):
            process = FakeProcess(stdout, stderr, finished=False)
            with self.subTest(stream="stdout" if stdout else "stderr"), patch("agent.container.asyncio.create_subprocess_exec", AsyncMock(return_value=process)), self.assertRaises(ResourceLimitExceeded):
                await self.boundary()._call(["start"], limit=4)
            self.assertEqual(process.kills, 1)
            self.assertTrue(process.stdin.closed)
            self.assertGreater(process.waits, 0)

    async def test_timeout_and_cancellation_kill_reap_and_leave_no_reader_tasks(self):
        for cancel in (False, True):
            process = FakeProcess(finished=False)
            with self.subTest(cancel=cancel), patch("agent.container.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                task = asyncio.create_task(self.boundary()._call(["start"], timeout=.01 if not cancel else 10))
                if cancel:
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError if cancel else TimeoutError):
                    await task
            self.assertEqual(process.kills, 1)
            self.assertTrue(process.stdin.closed)
            self.assertGreater(process.waits, 0)


class ExchangeTests(BoundaryFixtures, unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_during_create_waits_for_late_creation_before_removing(self):
        boundary = self.boundary()
        started, complete = asyncio.Event(), asyncio.Event()
        order = []
        async def call(arguments, **kwargs):
            self.assertEqual(arguments[0], "create")
            self.assertIsNone(kwargs["timeout"])
            started.set()
            await complete.wait()
            order.append("created")
            return 0, b"", b""
        async def remove(name):
            order.append("removed")
        boundary._call, boundary._remove = call, AsyncMock(side_effect=remove)
        task = asyncio.create_task(boundary._exchange({}, ResourceLimits(), "run"))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        boundary._remove.assert_not_called()
        complete.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(order, ["created", "removed"])
        self.assertTrue(boundary.can_build())

    async def test_lost_create_response_quarantines_boundary_and_never_starts_guest(self):
        boundary = self.boundary()
        boundary._call = AsyncMock(side_effect=OSError("connection lost"))
        boundary._remove = AsyncMock()
        with self.assertRaises(BoundaryUnavailable):
            await boundary._exchange({}, ResourceLimits(), "run")
        self.assertEqual(boundary.reason, "WORKER_CLEANUP_UNVERIFIED")
        self.assertFalse(boundary.can_build())
        self.assertFalse(boundary.can_run())
        self.assertEqual(boundary._call.await_count, 1)
        boundary._remove.assert_awaited_once()

    async def test_cancelled_create_error_is_consumed_and_quarantines_before_cleanup(self):
        boundary = self.boundary()
        started, finish = asyncio.Event(), asyncio.Event()
        async def call(arguments, **kwargs):
            started.set()
            await finish.wait()
            raise OSError("connection lost")
        boundary._call, boundary._remove = call, AsyncMock()
        task = asyncio.create_task(boundary._exchange({}, ResourceLimits(), "run"))
        await started.wait()
        task.cancel()
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(boundary.reason, "WORKER_CLEANUP_UNVERIFIED")
        boundary._remove.assert_awaited_once()

    async def test_success_sends_bounded_json_and_removes_container_after_response(self):
        boundary = self.boundary()
        boundary._call = AsyncMock(side_effect=[(0, b"created", b""), (0, b'{"returncode":0,"stdout":"ok"}', b"")])
        boundary._remove = AsyncMock()
        with patch("agent.container.time", SimpleNamespace(monotonic=Mock(side_effect=[10, 10.25]))):
            response = await boundary._exchange({"phase": "run", "stdin": "é"}, ResourceLimits(), "run")
        self.assertEqual(response["stdout"], "ok")
        start = boundary._call.call_args_list[1]
        self.assertEqual(start.kwargs["timeout"], 3)
        self.assertEqual(json.loads(start.kwargs["input_data"])["limits"], asdict(ResourceLimits()))
        self.assertIn(b"\\u00e9", start.kwargs["input_data"])
        boundary._remove.assert_awaited_once()
        self.assertEqual(boundary._remove.call_args.args[0], start.args[0][-1])

    async def test_unready_worker_and_oversized_budgets_do_not_create_any_container(self):
        boundary = self.boundary()
        boundary._call = AsyncMock()
        boundary.build_guarantees = frozenset()
        with self.assertRaises(BoundaryUnavailable):
            await boundary._exchange({}, ResourceLimits(), "run")
        boundary.build_guarantees = REQUIRED_GUARANTEES
        boundary.run_guarantees = frozenset()
        with self.assertRaises(BoundaryUnavailable):
            await boundary._exchange({}, ResourceLimits(), "run")
        boundary.run_guarantees = REQUIRED_GUARANTEES
        for key in ("memory_bytes", "disk_bytes", "stdout_bytes", "stderr_bytes", "diagnostics_bytes", "processes", "cpu_seconds"):
            with self.subTest(budget=key), self.assertRaises(ResourceLimitExceeded):
                await boundary._exchange({}, replace(ResourceLimits(), **{key: getattr(ResourceLimits(), key) + 1}), "run")
        boundary._call.assert_not_called()

    async def test_guest_compile_and_cpu_deadlines_exclude_unstarted_container_creation(self):
        boundary = self.boundary()
        boundary._remove = AsyncMock()
        for phase, limits, expected in (("compile", ResourceLimits(compile_seconds=2), 2),
                                        ("run", ResourceLimits(cpu_seconds=1), .8)):
            boundary._call = AsyncMock(side_effect=[(0, b"", b""), (0, b'{"returncode":0}', b"")])
            with self.subTest(phase=phase), patch("agent.container.time", SimpleNamespace(monotonic=Mock(side_effect=[0, .25]))):
                await boundary._exchange({"phase": phase}, limits, phase)
            self.assertAlmostEqual(boundary._call.call_args.kwargs["timeout"], expected)
        boundary._call = AsyncMock(return_value=(0, b"", b""))
        with patch("agent.container.time", SimpleNamespace(monotonic=Mock(side_effect=[0, 21]))), self.assertRaises(BoundaryUnavailable):
            await boundary._exchange({}, ResourceLimits(), "run")
        self.assertEqual(boundary._call.await_count, 1)

    async def test_failed_creation_supervisor_and_malformed_protocol_always_remove_container(self):
        for outcomes in ([(1, b"", b"denied")], [(0, b"", b""), (1, b"", b"error")],
                         *([(0, b"", b""), (0, data, b"")] for data in (b"not-json", b"\xff", b"[]", b'{"private":"field"}'))):
            boundary = self.boundary()
            boundary._call = AsyncMock(side_effect=outcomes)
            boundary._remove = AsyncMock()
            with self.subTest(outcomes=outcomes), self.assertRaises(BoundaryUnavailable):
                await boundary._exchange({}, ResourceLimits(), "run")
            boundary._remove.assert_awaited_once()

    async def test_cgroup_kill_exit_codes_are_resource_failures_and_cleaned_up(self):
        for code in (137, 143):
            boundary = self.boundary()
            boundary._call = AsyncMock(side_effect=[(0, b"", b""), (code, b"", b"cgroup killed")])
            boundary._remove = AsyncMock()
            with self.subTest(code=code), self.assertRaises(ResourceLimitExceeded):
                await boundary._exchange({}, ResourceLimits(), "run")
            boundary._remove.assert_awaited_once()

    async def test_cleanup_failure_invalidates_successful_result(self):
        boundary = self.boundary()
        boundary._call = AsyncMock(side_effect=[(0, b"", b""), (0, b'{"returncode":0}', b"")])
        boundary._remove = AsyncMock(side_effect=BoundaryUnavailable("cleanup failed"))
        with self.assertRaises(BoundaryUnavailable):
            await boundary._exchange({}, ResourceLimits(), "run")

    async def test_remove_requires_not_found_attestation_and_handles_already_missing_container(self):
        boundary = self.boundary()
        for outcomes, okay in (([(0, b"", b""), (1, b"", b"No such object")], True),
                               ([(1, b"", b"No such container"), (1, b"", b"No such container")], True),
                               ([(1, b"", b"permission denied")], False),
                               ([(0, b"", b""), (0, b"still exists", b"")], False),
                               ([(0, b"", b""), (1, b"", b"daemon unavailable")], False)):
            boundary._call = AsyncMock(side_effect=outcomes)
            with self.subTest(outcomes=outcomes):
                if okay:
                    await boundary._remove("job")
                else:
                    with self.assertRaises(BoundaryUnavailable):
                        await boundary._remove("job")

    async def test_cancellation_waits_for_cleanup_even_when_cancelled_repeatedly(self):
        boundary = self.boundary()
        started, removing, removed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def call(arguments, **kwargs):
            if arguments[0] == "create":
                return 0, b"", b""
            started.set()
            await asyncio.Event().wait()
        async def remove(name):
            removing.set()
            await removed.wait()
        boundary._call, boundary._remove = call, AsyncMock(side_effect=remove)
        task = asyncio.create_task(boundary._exchange({}, ResourceLimits(), "run"))
        await started.wait()
        task.cancel()
        await removing.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        removed.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        boundary._remove.assert_awaited_once()

    async def test_cancellation_during_success_cleanup_does_not_publish_result(self):
        boundary = self.boundary()
        removing, removed = asyncio.Event(), asyncio.Event()
        boundary._call = AsyncMock(side_effect=[(0, b"", b""), (0, b'{"returncode":0}', b"")])
        async def remove(name):
            removing.set()
            await removed.wait()
        boundary._remove = remove
        task = asyncio.create_task(boundary._exchange({}, ResourceLimits(), "run"))
        await removing.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        removed.set()
        with self.assertRaises(asyncio.CancelledError):
            await task


class ArtifactTests(BoundaryFixtures, unittest.IsolatedAsyncioTestCase):
    async def test_successful_compile_writes_only_target_artifact_and_forwards_diagnostics(self):
        boundary = self.boundary()
        source, output = self.files()
        boundary._exchange = AsyncMock(return_value={"returncode": 0, "stdout": "compile", "stderr": "warning", "elapsed_ms": 4,
                                                     "artifact_base64": base64.b64encode(elf64()).decode()})
        result = await boundary.compile(boundary.compiler, source, output, ResourceLimits(), ())
        self.assertTrue(result.success)
        self.assertEqual(output.read_bytes(), elf64())
        self.assertEqual((result.stdout, result.stderr, result.elapsed_ms), ("compile", "warning", 4))
        self.assertEqual(boundary._exchange.call_args.args[0]["source"], source.read_text())

    async def test_invalid_compiler_dependencies_paths_and_symlinks_never_reach_worker(self):
        boundary = self.boundary()
        source, output = self.files()
        boundary._exchange = AsyncMock()
        symlink = source.with_name("linked.c")
        symlink.symlink_to(source)
        for compiler, src, out, dependencies in ((CCompiler("clang", "clang", "Darwin"), source, output, ()),
                                                (boundary.compiler, source, output, ("-framework", "IOKit")),
                                                (boundary.compiler, symlink, output, ()),
                                                (boundary.compiler, source, output.parent / "nested" / "program", ())):
            with self.subTest(src=src, dependencies=dependencies), self.assertRaises(BoundaryUnavailable):
                await boundary.compile(compiler, src, out, ResourceLimits(), dependencies)
        boundary.build_guarantees = frozenset()
        with self.assertRaises(BoundaryUnavailable):
            await boundary.compile(boundary.compiler, source, output, ResourceLimits(), ())
        boundary._exchange.assert_not_called()

    async def test_empty_and_oversized_sources_delete_stale_output_and_do_not_compile(self):
        boundary = self.boundary()
        source, output = self.files()
        boundary._exchange = AsyncMock()
        for text in ("", "é" * (64 * 1024 + 1)):
            source.write_text(text, encoding="utf-8")
            output.write_bytes(elf64())
            with self.subTest(length=len(text)), self.assertRaises(ResourceLimitExceeded):
                await boundary.compile(boundary.compiler, source, output, ResourceLimits(), ())
            self.assertFalse(output.exists())
        boundary._exchange.assert_not_called()

    async def test_compile_timeout_failure_and_flags_never_materialize_artifact(self):
        boundary = self.boundary()
        source, output = self.files()
        cases = [(TimeoutError(), "COMPILE_TIMEOUT"), ({"returncode": 0, "timed_out": True}, "COMPILE_TIMEOUT"),
                 ({"returncode": 0, "output_limited": True}, "RESOURCE_LIMIT"), ({"returncode": 0, "unavailable": True}, "COMPILER_UNAVAILABLE"),
                 ({"returncode": 0, "cancelled": True}, "COMPILE_CANCELLED"), ({"returncode": 1, "stderr": "compile failed"}, "COMPILE_ERROR")]
        for response, reason in cases:
            boundary._exchange = AsyncMock(side_effect=response) if isinstance(response, Exception) else AsyncMock(return_value=response)
            with self.subTest(reason=reason):
                result = await boundary.compile(boundary.compiler, source, output, ResourceLimits(), ())
                self.assertEqual(result.reason, reason)
                self.assertFalse(result.executable_exists)
                self.assertFalse(output.exists())

    async def test_invalid_missing_wrong_target_or_oversized_compile_artifact_is_rejected(self):
        boundary = self.boundary()
        source, output = self.files()
        for response, limits, error in (({"returncode": 0}, ResourceLimits(), BoundaryUnavailable),
                                       ({"returncode": 0, "artifact_base64": "!!"}, ResourceLimits(), BoundaryUnavailable),
                                       ({"returncode": 0, "artifact_base64": 42}, ResourceLimits(), BoundaryUnavailable),
                                       ({"returncode": 0, "artifact_base64": base64.b64encode(elf64(183)).decode()}, ResourceLimits(), BoundaryUnavailable),
                                       ({"returncode": 0, "artifact_base64": base64.b64encode(elf64()).decode()}, ResourceLimits(disk_bytes=100), ResourceLimitExceeded)):
            boundary._exchange = AsyncMock(return_value=response)
            with self.subTest(response=str(response)[:80]), self.assertRaises(error):
                await boundary.compile(boundary.compiler, source, output, limits, ())
            self.assertFalse(output.exists())

    async def test_run_forwards_only_isolated_capabilities_and_exact_stdin(self):
        boundary = self.boundary()
        _, executable = self.files()
        boundary._exchange = AsyncMock(return_value={"returncode": 0, "stdout": "answer"})
        result = await boundary.run(executable, "typed input\n", ResourceLimits(), frozenset({"files.read", "process.spawn"}))
        self.assertEqual(result, ProcessResult(0, "answer"))
        payload = boundary._exchange.call_args.args[0]
        self.assertEqual(payload["stdin"], "typed input\n")
        self.assertEqual(payload["capabilities"], ["files.read", "process.spawn"])
        self.assertEqual(base64.b64decode(payload["artifact_base64"]), elf64())

    async def test_run_rejects_undeclared_host_caps_symlinks_wrong_target_and_large_artifacts(self):
        boundary = self.boundary()
        _, executable = self.files()
        boundary._exchange = AsyncMock()
        symlink = executable.with_name("linked")
        symlink.symlink_to(executable)
        for path, limits, caps, error in ((executable, ResourceLimits(), frozenset({"audio.volume"}), BoundaryUnavailable),
                                         (symlink, ResourceLimits(), frozenset(), BoundaryUnavailable),
                                         (executable, ResourceLimits(disk_bytes=100), frozenset(), ResourceLimitExceeded)):
            with self.subTest(path=path, caps=caps), self.assertRaises(error):
                await boundary.run(path, "", limits, caps)
        executable.write_bytes(elf64(183))
        with self.assertRaises(BoundaryUnavailable):
            await boundary.run(executable, "", ResourceLimits(), frozenset())
        boundary._exchange.assert_not_called()

    async def test_run_timeout_is_a_bounded_result_and_native_broker_is_not_invoked(self):
        boundary = self.boundary()
        _, executable = self.files()
        boundary._exchange = AsyncMock(side_effect=TimeoutError())
        boundary.broker = SimpleNamespace(capabilities=frozenset({"audio.volume"}), prepare_input=Mock(return_value="safe snapshot"), apply=AsyncMock())
        result = await boundary.run(executable, "typed", ResourceLimits(), frozenset({"audio.volume"}))
        self.assertTrue(result.timed_out)
        boundary.broker.apply.assert_not_called()

    async def test_broker_prepares_input_and_applies_after_success_with_granted_capabilities(self):
        boundary = self.boundary()
        _, executable = self.files()
        boundary._exchange = AsyncMock(return_value={"returncode": 0, "stdout": "original", "elapsed_ms": 2})
        boundary.broker = SimpleNamespace(capabilities=frozenset({"audio.volume"}), prepare_input=Mock(return_value="safe snapshot"),
                                          apply=AsyncMock(return_value=ProcessResult(0, "broker result")))
        capabilities = frozenset({"audio.volume", "files.read"})
        result = await boundary.run(executable, "typed", ResourceLimits(), capabilities)
        self.assertEqual(result.stdout, "broker result")
        boundary.broker.prepare_input.assert_called_once_with("typed", capabilities, max_bytes=65536)
        self.assertEqual(boundary._exchange.call_args.args[0]["capabilities"], ["files.read"])
        self.assertEqual(boundary._exchange.call_args.args[0]["stdin"], "safe snapshot")
        boundary.broker.apply.assert_awaited_once_with(ProcessResult(0, "original", elapsed_ms=2), capabilities, ResourceLimits())
        boundary.broker.apply = AsyncMock(side_effect=BrokerError("sensitive native message"))
        result = await boundary.run(executable, "", ResourceLimits(), capabilities)
        self.assertEqual(result, ProcessResult(1, stderr="Native request was not authorized.", elapsed_ms=2))

    async def test_broker_rejects_undeclared_native_commands_even_for_pure_compute(self):
        boundary = self.boundary()
        _, executable = self.files()
        boundary._exchange = AsyncMock(return_value={"returncode": 0, "stdout": '{"version":1,"janus":[{"tool":"volume","action":"get","args":{}}]}'})
        boundary.broker = SimpleNamespace(capabilities=frozenset({"audio.volume"}), prepare_input=Mock(return_value=""), apply=AsyncMock(side_effect=BrokerError("undeclared")))
        result = await boundary.run(executable, "", ResourceLimits(), frozenset())
        self.assertEqual(result.returncode, 1)
        boundary.broker.apply.assert_awaited_once()


class PreparationTests(unittest.IsolatedAsyncioTestCase):
    def state(self, reason, available=False):
        value = {"available": available, "reason": reason, "execution_target": {"platform": "Linux"} if available else None}
        return SimpleNamespace(available=available, reason=reason, describe=Mock(return_value=value), _call=AsyncMock(return_value=(0, b"", b"")))

    async def test_ready_or_missing_prerequisites_do_not_launch_or_build(self):
        for reason, available in ((None, True), ("DOCKER_NOT_INSTALLED", False), ("DOCKER_LOCAL_ENGINE_REQUIRED", False)):
            state = self.state(reason, available)
            with self.subTest(reason=reason), patch("agent.container.create_boundary", return_value=state), patch("agent.container.subprocess.Popen") as launch:
                self.assertEqual(await container.prepare_worker(), state.describe())
                launch.assert_not_called()
                state._call.assert_not_called()

    async def test_absent_docker_desktop_does_not_launch_arbitrary_app(self):
        state = self.state("DOCKER_NOT_RUNNING")
        with patch("agent.container.create_boundary", return_value=state), patch("agent.container.Path.exists", return_value=False), \
             patch("agent.container.subprocess.Popen") as launch:
            self.assertEqual(await container.prepare_worker(), state.describe())
            launch.assert_not_called()

    async def test_failed_desktop_launch_returns_unavailable_without_private_error(self):
        state = self.state("DOCKER_NOT_RUNNING")
        with patch("agent.container.create_boundary", return_value=state), patch("agent.container.Path.exists", return_value=True), \
             patch("agent.container.subprocess.Popen", side_effect=OSError("private path")):
            self.assertEqual(await container.prepare_worker(), state.describe())
            state._call.assert_not_called()

    async def test_docker_desktop_launch_on_mac_and_windows_then_returns_verified_state(self):
        for system in ("Darwin", "Windows"):
            stopped, ready = self.state("DOCKER_NOT_RUNNING"), self.state(None, True)
            with self.subTest(system=system), patch("agent.container.create_boundary", side_effect=[stopped, ready]), \
                 patch("agent.container.platform.system", return_value=system), patch("agent.container.Path.exists", return_value=True), \
                 patch("agent.container.subprocess.Popen") as launch, patch("agent.container.asyncio.sleep", AsyncMock()):
                self.assertEqual(await container.prepare_worker(), ready.describe())
                command = launch.call_args.args[0]
                if system == "Darwin":
                    self.assertEqual(command, ["/usr/bin/open", "-a", "/Applications/Docker.app"])
                else:
                    self.assertTrue(command[0].endswith("Docker Desktop.exe"))
                self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.DEVNULL)
                stopped._call.assert_not_called()

    async def test_docker_desktop_wait_is_bounded_and_does_not_build_while_daemon_is_down(self):
        state = self.state("DOCKER_NOT_RUNNING")
        with patch("agent.container.create_boundary", return_value=state) as create, \
             patch("agent.container.Path.exists", return_value=True), patch("agent.container.subprocess.Popen"), patch("agent.container.asyncio.sleep", AsyncMock()) as sleep, \
             patch("agent.container.time", SimpleNamespace(monotonic=Mock(side_effect=range(40)))):
            self.assertEqual(await container.prepare_worker(), state.describe())
            self.assertEqual(sleep.await_count, 29)
            self.assertEqual(create.call_count, 30)
            state._call.assert_not_called()

    async def test_missing_image_builds_only_pinned_worker_then_rechecks_readiness(self):
        missing, ready = self.state("WORKER_IMAGE_MISSING"), self.state(None, True)
        snapshots = []
        async def build(arguments, **kwargs):
            folder = Path(arguments[-1])
            snapshots.append((folder, {path.name: path.read_bytes() for path in folder.iterdir()}))
            return 0, b"", b""
        missing._call = AsyncMock(side_effect=build)
        with patch.dict(os.environ, {}, clear=True), patch("agent.container.create_boundary", side_effect=[missing, ready]):
            self.assertEqual(await container.prepare_worker(), ready.describe())
        arguments = missing._call.call_args.args[0]
        self.assertEqual(arguments[0], "build")
        self.assertIn("org.janus.worker.digest=" + container.worker_identity()[1], arguments)
        self.assertIn(container.worker_identity()[0], arguments)
        self.assertGreater(missing._call.call_args.kwargs["timeout"], 0)
        self.assertEqual(missing._call.call_args.kwargs["limit"], 2 * 1024 * 1024)
        self.assertEqual(snapshots[0][1], {name: (container.WORKER_DIR / name).read_bytes() for name in ("Dockerfile", "runner.py")})
        self.assertFalse(snapshots[0][0].exists())

    async def test_invalid_trust_bundle_blocks_build_and_returns_safe_status(self):
        missing = self.state("WORKER_IMAGE_MISSING")
        with patch("agent.container.create_boundary", return_value=missing), \
             patch("agent.container.provisioning_options", side_effect=ValueError("private key")):
            self.assertEqual(await container.prepare_worker(), {"available": False, "reason": "WORKER_BUILD_FAILED", "execution_target": None})
            missing._call.assert_not_called()

    async def test_desktop_start_can_proceed_to_missing_image_build(self):
        stopped, missing, ready = self.state("DOCKER_NOT_RUNNING"), self.state("WORKER_IMAGE_MISSING"), self.state(None, True)
        with patch("agent.container.create_boundary", side_effect=[stopped, missing, ready]), \
             patch("agent.container.Path.exists", return_value=True), patch("agent.container.subprocess.Popen"), patch("agent.container.asyncio.sleep", AsyncMock()):
            self.assertEqual(await container.prepare_worker(), ready.describe())
            missing._call.assert_awaited_once()

    async def test_build_failure_and_resource_errors_are_sanitized(self):
        for response in ((1, b"", b"private diagnostics"), OSError("private path"), TimeoutError(),
                         ResourceLimitExceeded("large log"), BoundaryUnavailable("daemon detail")):
            missing = self.state("WORKER_IMAGE_MISSING")
            missing._call = AsyncMock(side_effect=response) if isinstance(response, Exception) else AsyncMock(return_value=response)
            with self.subTest(response=type(response).__name__), patch("agent.container.create_boundary", return_value=missing):
                self.assertEqual(await container.prepare_worker(), {"available": False, "reason": "WORKER_BUILD_FAILED", "execution_target": None})
