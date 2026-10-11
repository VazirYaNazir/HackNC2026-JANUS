"""Shipped portable C worker: bounded Docker containers, never host compilation.

The daemon must be local and Linux with cgroup v2 enforcement. Docker Desktop
provides that worker VM on macOS and Windows. No home, project, credentials,
host device, socket, or writable host directory is mounted into a worker.
"""
import asyncio
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import ssl
import struct
import subprocess
import tempfile
import time
import uuid

from .compiler import CCompiler, CompileResult
from .isolation import BoundaryUnavailable, IsolationBoundary, REQUIRED_GUARANTEES, ResourceLimitExceeded, ResourceLimits
from .process import ProcessResult, finish_thread

WORKER_DIR = Path(__file__).resolve().parents[1] / "worker"
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
CREATE_ALLOWANCE_SECONDS = 20
WORKER_CAPABILITIES = frozenset({"files.read", "files.write", "process.spawn"})
BROKER_CAPABILITIES = frozenset({"audio.volume", "desktop.mouse", "desktop.screen", "process.inspect"})


def worker_identity(files=None):
    digest = hashlib.sha256()
    for name in ("Dockerfile", "runner.py"):
        digest.update(name.encode())
        digest.update(files[name] if files is not None else (WORKER_DIR / name).read_bytes())
    value = digest.hexdigest()
    return "janus-c-worker:" + value[:24], value


PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy")


def client_environment(*, provision=False):
    # Docker's local client gets neither provider keys nor laptop credentials.
    names = ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR")
    if provision:
        names += PROXY_NAMES
    return {name: value for name in names if (value := os.environ.get(name))}


def client_plugin_directories():
    """Discover locally installed CLI plugins without importing Docker secrets."""
    configured = os.environ.get("DOCKER_CONFIG")
    user_config = Path(configured).expanduser() if configured else Path.home() / ".docker"
    candidates = [user_config / "cli-plugins"]
    system = platform.system()
    if system == "Darwin":
        candidates.append(Path("/Applications/Docker.app/Contents/Resources/cli-plugins"))
    elif system == "Windows":
        candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "resources" / "cli-plugins")
    candidates.append(Path("/opt/homebrew/lib/docker/cli-plugins"))
    return list(dict.fromkeys(str(path) for path in candidates if path.is_dir()))


def provisioning_options():
    """Proxy and public CA configuration is scoped to trusted image building."""
    arguments = []
    for name in PROXY_NAMES:
        if os.environ.get(name):
            arguments += ["--build-arg", name]
    certificate = os.environ.get("SSL_CERT_FILE")
    if certificate:
        path = Path(certificate)
        if not path.is_file():
            raise ValueError("Invalid build trust bundle")
        public_bundle = path.read_bytes()
        if len(public_bundle) > 4 * 1024 * 1024 or b"PRIVATE KEY" in public_bundle:
            raise ValueError("Invalid build trust bundle")
        # Parse certificates as a trust store before exposing a public-only
        # BuildKit secret. The worker image never receives host private keys.
        ssl.create_default_context(cafile=str(path))
        arguments += ["--secret", "id=janus-build-ca,src=" + str(path)]
    return arguments


def sync_command(command, *, timeout=3, input_data=None, completion_barrier=False):
    started = time.monotonic() if completion_barrier else None
    result = subprocess.run(command, input=input_data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=None if completion_barrier else timeout,
                            env=client_environment(), check=False)
    if completion_barrier and time.monotonic() - started > timeout:
        # Killing a create client does not cancel its daemon transaction. Wait
        # for that trusted request to complete before cleanup can prove absence.
        raise subprocess.TimeoutExpired(command, timeout)
    return result


def container_options(name, image, limits):
    """One private writable disk; cgroup budgets include the trusted supervisor."""
    return ["create", "--name", name, "--interactive", "--read-only",
            "--network", "none", "--ipc", "private", "--cgroupns", "private",
            "--memory", str(limits.memory_bytes), "--memory-swap", str(limits.memory_bytes),
            "--pids-limit", str(limits.processes), "--cpu-period", "100000", "--cpu-quota", "100000",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--log-driver", "none", "--user", "65532:65532",
            "--tmpfs", f"/work:rw,exec,nosuid,nodev,size={limits.disk_bytes},mode=1777",
            "--tmpfs", "/dev/shm:rw,noexec,nosuid,nodev,size=4096,mode=000",
            "--workdir", "/work", "--env", "LANG=C.UTF-8",
            "--entrypoint", "/usr/bin/python3", image, "-I", "-B", "/opt/janus/runner.py"]


def elf_matches(data, architecture):
    return (len(data) >= 64 and data[:7] == b"\x7fELF\x02\x01\x01"
            and data[7] in {0, 3} and struct.unpack_from("<H", data, 16)[0] in {2, 3}
            and struct.unpack_from("<H", data, 18)[0] == {"x86_64": 62, "arm64": 183}.get(architecture)
            and struct.unpack_from("<I", data, 20)[0] == 1
            and struct.unpack_from("<H", data, 52)[0] == 64)


class ContainerBoundary(IsolationBoundary):
    """Availability follows a real worker enforcement probe, not configuration."""
    broker = None

    def __init__(self):
        self.reason = "DOCKER_NOT_INSTALLED"
        self.available = False
        self.execution_target = None
        self.compiler = None
        self.docker = shutil.which("docker")
        self.image = None
        self.endpoint = None
        self._config = tempfile.TemporaryDirectory(prefix="janus-docker-client-")
        # Desktop often keeps Buildx outside Docker's system plugin search.
        # Preserve only local plugin discovery, never auths, credential helpers,
        # contexts, or arbitrary settings from the user's Docker config file.
        (Path(self._config.name) / "config.json").write_text(
            json.dumps({"cliPluginsExtraDirs": client_plugin_directories()}), encoding="utf-8")
        self.build_guarantees = self.run_guarantees = frozenset()
        if self.docker:
            self._probe()

    @property
    def run_capabilities(self):
        return WORKER_CAPABILITIES | (self.broker.capabilities if self.broker else frozenset())

    @property
    def command(self):
        if not self.docker or not self.endpoint:
            raise BoundaryUnavailable("Local Docker worker unavailable")
        return [self.docker, "--config", self._config.name, "--host", self.endpoint]

    def describe(self):
        target = {**self.execution_target, "capabilities": sorted(self.run_capabilities)} if self.execution_target else None
        return {"available": self.available, "reason": self.reason, "execution_target": target}

    def operation_timeout(self, limits, phase):
        return super().operation_timeout(limits, phase) + CREATE_ALLOWANCE_SECONDS

    def _probe(self):
        try:
            # Context metadata is read only by the trusted client. All subsequent
            # commands use an explicit local endpoint and an empty client config.
            explicit = os.environ.get("DOCKER_HOST", "").strip()
            if explicit:
                endpoint = explicit
            else:
                context = sync_command([self.docker, "context", "inspect"])
                if context.returncode:
                    self.reason = "DOCKER_NOT_RUNNING"
                    return
                endpoint = json.loads(context.stdout)[0]["Endpoints"]["docker"]["Host"]
            if not isinstance(endpoint, str) or not endpoint.startswith(("unix:///", "npipe:////./pipe/")):
                self.reason = "DOCKER_LOCAL_ENGINE_REQUIRED"
                return
            self.endpoint = endpoint
            info = sync_command([*self.command, "info", "--format", "{{json .}}"])
            if info.returncode:
                self.reason = "DOCKER_NOT_RUNNING"
                return
            engine = json.loads(info.stdout)
            if not isinstance(engine, dict):
                raise ValueError("Invalid engine metadata")
            if engine.get("OSType") != "linux":
                self.reason = "DOCKER_LINUX_ENGINE_REQUIRED"
                return
            if (str(engine.get("CgroupVersion")) != "2"
                    or not all(engine.get(key) is True for key in ("MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsPeriod", "CpuCfsQuota"))
                    or not any("seccomp" in item for item in engine.get("SecurityOptions", []))):
                self.reason = "WORKER_ENFORCEMENT_UNAVAILABLE"
                return
            architecture = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}.get(engine.get("Architecture"))
            if architecture is None:
                self.reason = "WORKER_ARCHITECTURE_UNSUPPORTED"
                return
            image, digest = worker_identity()
            found = sync_command([*self.command, "image", "inspect", image])
            if found.returncode:
                self.reason = "WORKER_IMAGE_MISSING"
                return
            metadata = json.loads(found.stdout)[0]
            if not isinstance(metadata, dict):
                raise ValueError("Invalid worker metadata")
            if metadata.get("Config", {}).get("Labels", {}).get("org.janus.worker.digest") != digest:
                self.reason = "WORKER_IMAGE_MISSING"
                return
            image_id = metadata.get("Id")
            if not isinstance(image_id, str) or len(image_id) != 71 or not image_id.startswith("sha256:"):
                self.reason = "WORKER_IMAGE_MISSING"
                return
            self.image = image_id
            name = "janus-probe-" + uuid.uuid4().hex
            limits = ResourceLimits()
            try:
                created = sync_command([*self.command, *container_options(name, self.image, limits)],
                                       timeout=CREATE_ALLOWANCE_SECONDS, completion_barrier=True)
                if created.returncode:
                    self.reason = "WORKER_ENFORCEMENT_UNAVAILABLE"
                    return
                probe = sync_command([*self.command, "start", "--attach", "--interactive", name],
                                     input_data=json.dumps({"phase": "probe", "limits": asdict(limits)}).encode(), timeout=5)
                result = json.loads(probe.stdout)
                if (probe.returncode or not isinstance(result, dict) or result.get("ready") is not True
                        or frozenset(result.get("guarantees", [])) != REQUIRED_GUARANTEES
                        or result.get("architecture") != architecture):
                    self.reason = "WORKER_ENFORCEMENT_UNAVAILABLE"
                    return
            finally:
                removed = sync_command([*self.command, "rm", "--force", name])
                if removed.returncode and b"No such container" not in removed.stderr:
                    raise BoundaryUnavailable("Worker cleanup unavailable")
            self.execution_target = {"platform": "Linux", "architecture": architecture,
                                     "profile": "portable-c", "capabilities": sorted(WORKER_CAPABILITIES), "dependencies": []}
            self.compiler = CCompiler("worker-gcc", self.docker, "Linux")
            self.build_guarantees = self.run_guarantees = REQUIRED_GUARANTEES
            self.available, self.reason = True, None
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError, AttributeError, BoundaryUnavailable):
            self.reason = "WORKER_ENFORCEMENT_UNAVAILABLE"

    async def _call(self, arguments, *, input_data=b"", timeout=10, limit=65536):
        process = await asyncio.create_subprocess_exec(*self.command, *arguments,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=client_environment(provision=bool(arguments) and arguments[0] == "build"))
        async def read(pipe):
            parts, size = [], 0
            while data := await pipe.read(8192):
                size += len(data)
                if size > limit:
                    raise ResourceLimitExceeded("Worker output limit")
                parts.append(data)
            return b"".join(parts)
        async def write():
            try:
                process.stdin.write(input_data)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()
        tasks = [asyncio.create_task(read(process.stdout)), asyncio.create_task(read(process.stderr)), asyncio.create_task(write())]
        try:
            async with asyncio.timeout(timeout):
                stdout, stderr, _ = await asyncio.gather(*tasks)
                await process.wait()
            return process.returncode, stdout, stderr
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()

    async def _remove(self, name):
        code, _, stderr = await self._call(["rm", "--force", name])
        if code and b"No such container" not in stderr:
            raise BoundaryUnavailable("Worker cleanup unavailable")
        code, _, stderr = await self._call(["inspect", "--type", "container", name])
        if code == 0 or not any(text in stderr for text in (b"No such object", b"No such container")):
            raise BoundaryUnavailable("Worker cleanup could not be verified")

    async def _create(self, name, limits):
        """Resolve an owned daemon transaction before cancellation or cleanup.

        Container creation cannot execute generated code. Its local allowance
        is separate from the guest deadline. If a slow request exceeds that
        allowance, we still wait for its result, then refuse to start the guest.
        """
        started = time.monotonic()
        creating = asyncio.create_task(self._call(container_options(name, self.image, limits), timeout=None))
        cancelled = False
        while not creating.done():
            try:
                await asyncio.shield(creating)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        try:
            code, _, _ = creating.result()
            if code:
                raise BoundaryUnavailable("Worker creation could not be confirmed")
        except Exception:
            # A lost response cannot attest that the daemon has no pending
            # transaction, even if an early inspect says the name is absent.
            self.available = False
            self.reason = "WORKER_CLEANUP_UNVERIFIED"
            self.build_guarantees = self.run_guarantees = frozenset()
            if cancelled:
                raise asyncio.CancelledError from None
            raise BoundaryUnavailable("Worker creation could not be confirmed") from None
        if cancelled:
            raise asyncio.CancelledError
        if time.monotonic() - started > CREATE_ALLOWANCE_SECONDS:
            raise BoundaryUnavailable("Worker creation allowance exceeded")

    async def _exchange(self, payload, limits, phase):
        if not self.can_build() or not self.can_run():
            raise BoundaryUnavailable("Worker unavailable")
        defaults = ResourceLimits()
        if any(getattr(limits, field) > getattr(defaults, field) for field in asdict(defaults)):
            raise ResourceLimitExceeded("Worker budget exceeds supported maximum")
        name = "janus-job-" + uuid.uuid4().hex
        timeout = min(limits.compile_seconds if phase == "compile" else limits.run_seconds,
                      max(0.01, limits.cpu_seconds - 0.2))
        try:
            await self._create(name, limits)
            payload = {**payload, "limits": asdict(limits)}
            code, stdout, _ = await self._call(["start", "--attach", "--interactive", name],
                input_data=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(),
                timeout=timeout, limit=MAX_ARTIFACT_BYTES * 4 // 3 + 256 * 1024)
            if code in {137, 143}:
                raise ResourceLimitExceeded("Worker resource limit")
            if code:
                raise BoundaryUnavailable("Worker supervisor unavailable")
            result = json.loads(stdout)
            if not isinstance(result, dict) or set(result) - {"returncode", "stdout", "stderr", "elapsed_ms", "timed_out", "output_limited", "unavailable", "cancelled", "artifact_base64", "reason", "resource_limited"}:
                raise BoundaryUnavailable("Invalid worker response")
            return result
        except (json.JSONDecodeError, UnicodeError):
            raise BoundaryUnavailable("Invalid worker response") from None
        finally:
            # External cancellation cannot leave a container running or holding
            # an artifact open. Cleanup is finished before returning/cancelling.
            closing = asyncio.create_task(self._remove(name))
            cancelled = False
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    cancelled = True
            closing.result()
            if cancelled:
                raise asyncio.CancelledError

    async def compile(self, compiler, source, output, limits, link_args):
        if (not self.can_build() or compiler.system != "Linux" or link_args
                or source.is_symlink() or source.parent.resolve() != output.parent.resolve()):
            raise BoundaryUnavailable("Unsupported worker compiler input")
        output.unlink(missing_ok=True)
        text = await finish_thread(lambda: source.read_text(encoding="utf-8"))
        if not text or len(text.encode()) > 128 * 1024:
            raise ResourceLimitExceeded("Source limit")
        try:
            response = await self._exchange({"phase": "compile", "source": text, "link_args": []}, limits, "compile")
        except TimeoutError:
            return CompileResult("worker-gcc", None, "", "", 0, False, "COMPILE_TIMEOUT")
        result = checked_result(response, limits, diagnostics=True)
        reason = ("COMPILE_TIMEOUT" if result.timed_out else "RESOURCE_LIMIT" if result.output_limited
                  else "COMPILER_UNAVAILABLE" if result.unavailable else "COMPILE_CANCELLED" if result.cancelled
                  else "COMPILE_ERROR" if result.returncode != 0 else None)
        if reason is None:
            try:
                binary = base64.b64decode(response["artifact_base64"], validate=True)
            except (KeyError, ValueError, TypeError):
                raise BoundaryUnavailable("Invalid worker artifact") from None
            if len(binary) > min(limits.disk_bytes, MAX_ARTIFACT_BYTES):
                raise ResourceLimitExceeded("Artifact limit")
            if not elf_matches(binary, self.execution_target["architecture"]):
                raise BoundaryUnavailable("Invalid worker artifact target")
            await finish_thread(output.write_bytes, binary)
        return CompileResult("worker-gcc", result.returncode, result.stdout, result.stderr,
                             result.elapsed_ms, reason is None, reason)

    async def run(self, executable, stdin, limits, capabilities):
        if not self.can_run(capabilities) or executable.is_symlink():
            raise BoundaryUnavailable("Unsupported worker capability")
        binary = await finish_thread(executable.read_bytes)
        if len(binary) > min(limits.disk_bytes, MAX_ARTIFACT_BYTES):
            raise ResourceLimitExceeded("Artifact limit")
        if not elf_matches(binary, self.execution_target["architecture"]):
            raise BoundaryUnavailable("Invalid worker artifact target")
        if self.broker:
            stdin = self.broker.prepare_input(stdin, capabilities, max_bytes=limits.stdout_bytes)
        payload = {"phase": "run", "artifact_base64": base64.b64encode(binary).decode(),
                   "stdin": stdin, "capabilities": sorted(capabilities & WORKER_CAPABILITIES)}
        try:
            response = await self._exchange(payload, limits, "run")
        except TimeoutError:
            return ProcessResult(None, timed_out=True)
        result = checked_result(response, limits)
        if self.broker:
            from .broker import BrokerError
            try:
                result = await self.broker.apply(result, capabilities, limits)
            except BrokerError:
                return ProcessResult(1, stderr="Native request was not authorized.", elapsed_ms=result.elapsed_ms)
        return result


def checked_result(response, limits, *, diagnostics=False):
    if "returncode" not in response:
        raise BoundaryUnavailable("Invalid worker process result")
    if type(response.get("resource_limited", False)) is not bool:
        raise BoundaryUnavailable("Invalid worker resource result")
    if response.get("resource_limited", False):
        raise ResourceLimitExceeded("Worker resource limit")
    fields = {key: response[key] for key in ("returncode", "stdout", "stderr", "elapsed_ms", "timed_out", "output_limited", "unavailable", "cancelled") if key in response}
    code = fields.get("returncode")
    if (code is not None and (type(code) is not int or not -2147483648 <= code <= 2147483647)
            or not isinstance(fields.get("stdout", ""), str) or not isinstance(fields.get("stderr", ""), str)
            or type(fields.get("elapsed_ms", 0)) is not int or fields.get("elapsed_ms", 0) < 0
            or any(type(fields.get(name, False)) is not bool for name in ("timed_out", "output_limited", "unavailable", "cancelled"))):
        raise BoundaryUnavailable("Invalid worker process result")
    result = ProcessResult(**fields)
    first, second = (limits.diagnostics_bytes, limits.diagnostics_bytes) if diagnostics else (limits.stdout_bytes, limits.stderr_bytes)
    if len(result.stdout.encode()) > first or len(result.stderr.encode()) > second:
        raise ResourceLimitExceeded("Worker output limit")
    return result


def create_boundary():
    return ContainerBoundary()


async def prepare_worker():
    """Build only the repository's pinned worker image, then prove readiness."""
    boundary = await asyncio.to_thread(create_boundary)
    if boundary.available or boundary.reason not in {"WORKER_IMAGE_MISSING", "DOCKER_NOT_RUNNING"}:
        return boundary.describe()
    if boundary.reason == "DOCKER_NOT_RUNNING":
        system = platform.system()
        app = Path("/Applications/Docker.app") if system == "Darwin" else Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"
        if not app.exists():
            return boundary.describe()
        launch = ["/usr/bin/open", "-a", str(app)] if system == "Darwin" else [str(app)]
        try:
            await asyncio.to_thread(subprocess.Popen, launch, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return boundary.describe()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            boundary = await asyncio.to_thread(create_boundary)
            if boundary.reason != "DOCKER_NOT_RUNNING":
                break
        if boundary.reason != "WORKER_IMAGE_MISSING":
            return boundary.describe()
    try:
        # Freeze both image identity and build context; concurrent source edits
        # cannot label a different worker with the original content digest.
        files = await finish_thread(lambda: {name: (WORKER_DIR / name).read_bytes() for name in ("Dockerfile", "runner.py")})
        image, digest = worker_identity(files)
        with tempfile.TemporaryDirectory(prefix="janus-worker-build-") as folder:
            for name, data in files.items():
                await finish_thread((Path(folder) / name).write_bytes, data)
            code, _, _ = await boundary._call(["build", *provisioning_options(), "--label", "org.janus.worker.digest=" + digest,
                "--tag", image, folder], timeout=120, limit=2 * 1024 * 1024)
        if code:
            return {"available": False, "reason": "WORKER_BUILD_FAILED", "execution_target": None}
        return (await asyncio.to_thread(create_boundary)).describe()
    except (OSError, TimeoutError, ValueError, ResourceLimitExceeded, BoundaryUnavailable):
        return {"available": False, "reason": "WORKER_BUILD_FAILED", "execution_target": None}
