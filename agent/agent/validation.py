"""Local C11 contracts, bounded repair, and fail-closed native validation."""
import asyncio
import hashlib
import json
import platform
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .compiler import CCompiler, CompileResult
from .isolation import BoundaryUnavailable, IsolationBoundary, ResourceLimitExceeded, ResourceLimits
from .process import finish_thread

MAX_SOURCE_BYTES = 128 * 1024
MAX_ATTEMPTS = 3
ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
NAME = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
STANDARD_HEADERS = frozenset("assert.h complex.h ctype.h errno.h fenv.h float.h inttypes.h iso646.h limits.h locale.h math.h setjmp.h signal.h stdalign.h stdarg.h stdatomic.h stdbool.h stddef.h stdint.h stdio.h stdlib.h stdnoreturn.h string.h tgmath.h threads.h time.h uchar.h wchar.h wctype.h".split())
CAPABILITIES = frozenset({"files.read", "files.write", "network", "external.send", "desktop.mouse", "desktop.screen", "audio.volume", "process.inspect", "process.spawn", "admin", "persistent.write"})
DEPENDENCIES = {
    "coreaudio": ("Darwin", ("CoreAudio/", "CoreFoundation/"), ("CoreAudio", "CoreFoundation")),
    "applicationservices": ("Darwin", ("ApplicationServices/", "CoreGraphics/"), ("ApplicationServices",)),
    "iokit": ("Darwin", ("IOKit/", "CoreFoundation/"), ("IOKit", "CoreFoundation")),
    "imageio": ("Darwin", ("ImageIO/", "CoreGraphics/", "CoreFoundation/"), ("ImageIO", "CoreGraphics", "CoreFoundation")),
    "mac-system": ("Darwin", ("sys/", "mach/", "libproc.h", "unistd.h", "poll.h", "spawn.h"), ()),
    "win32": ("Windows", ("windows.h", "initguid.h", "mmdeviceapi.h", "endpointvolume.h", "wbemidl.h", "tlhelp32.h", "psapi.h", "wincodec.h"), ("ole32", "oleaut32", "uuid", "user32", "gdi32", "psapi", "wbemuuid", "windowscodecs")),
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def architecture(value: str) -> str:
    return {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64", "x86": "x86", "i386": "x86"}.get(value.lower(), value.lower())


def string_set(value, allowed):
    if not isinstance(value, list) or any(not isinstance(x, str) or x not in allowed for x in value) or len(set(value)) != len(value):
        raise ValueError("Unknown or malformed capability/dependency declaration")
    return frozenset(value)


@dataclass(frozen=True)
class Fixture:
    stdin: str = ""
    stdout: str = ""
    returncode: int = 0


@dataclass(frozen=True)
class ExecutionPolicy:
    """A LOCAL app grant for one job ID; never constructed from a cloud message."""
    job_id: str
    operation: str
    capabilities: frozenset[str] = frozenset()
    dependencies: frozenset[str] = frozenset()
    execute_authorized: bool = False
    share_outputs: bool = False
    side_effects: bool = True
    output_protocol: str = "text"
    fixtures: tuple[Fixture, ...] = ()
    execution_stdin: str = field(default="", repr=False)
    max_attempts: int = MAX_ATTEMPTS
    max_source_bytes: int = MAX_SOURCE_BYTES
    limits: ResourceLimits = field(default_factory=ResourceLimits)

    def __post_init__(self):
        if not isinstance(self.job_id, str) or not isinstance(self.operation, str) or not ID.fullmatch(self.job_id) or not NAME.fullmatch(self.operation):
            raise ValueError("Invalid local job grant")
        if any(type(value) is not bool for value in (self.execute_authorized, self.share_outputs, self.side_effects)):
            raise ValueError("Local authorization flags must be boolean")
        if not isinstance(self.capabilities, frozenset) or not isinstance(self.dependencies, frozenset) or not isinstance(self.fixtures, tuple) or not isinstance(self.limits, ResourceLimits):
            raise ValueError("Local grants must use immutable capability/fixture/resource values")
        if not self.capabilities <= CAPABILITIES or not self.dependencies <= DEPENDENCIES.keys():
            raise ValueError("Unknown local grant capability/dependency")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= MAX_ATTEMPTS:
            raise ValueError("Local attempt limit must be between 1 and 3")
        if type(self.max_source_bytes) is not int or not 1 <= self.max_source_bytes <= MAX_SOURCE_BYTES:
            raise ValueError("Local source limit must be at most 128 KiB")
        if self.output_protocol not in ("text", "json-v1") or len(self.fixtures) > 8:
            raise ValueError("Invalid local fixture/protocol policy")
        if not isinstance(self.execution_stdin, str) or len(self.execution_stdin.encode()) > 65536:
            raise ValueError("Invalid or oversize local runtime input")
        for fixture in self.fixtures:
            if not isinstance(fixture, Fixture) or not isinstance(fixture.stdin, str) or not isinstance(fixture.stdout, str) or len(fixture.stdin.encode()) > 65536 or len(fixture.stdout.encode()) > 65536 or type(fixture.returncode) is not int:
                raise ValueError("Invalid or oversize local synthetic fixture")


def load_policies(path: str | None) -> dict[str, ExecutionPolicy]:
    if path is None:
        return {}
    file = Path(path)
    if file.stat().st_size > 256 * 1024:
        raise ValueError("Local policy file is too large")
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "jobs" not in data or not data.keys() <= {"jobs", "session"} or not isinstance(data["jobs"], list) or len(data["jobs"]) > 256:
        raise ValueError("Local policy must contain a jobs list (at most 256)")
    if "session" in data:
        from .authorization import SessionScope
        SessionScope.parse(data["session"])
    policies = {}
    fields = {"job_id", "operation", "capabilities", "dependencies", "execute_authorized", "share_outputs", "side_effects", "output_protocol", "fixtures", "execution_stdin", "max_attempts", "max_source_bytes"}
    for item in data["jobs"]:
        if not isinstance(item, dict) or not {"job_id", "operation"} <= item.keys() or not item.keys() <= fields:
            raise ValueError("Malformed local grant or unsupported policy field")
        args = dict(item)
        args["capabilities"] = string_set(args.get("capabilities", []), CAPABILITIES)
        args["dependencies"] = string_set(args.get("dependencies", []), DEPENDENCIES)
        for name in ("execute_authorized", "share_outputs", "side_effects"):
            if name in args and type(args[name]) is not bool:
                raise ValueError("Local authorization flags must be boolean")
        fixtures = args.pop("fixtures", [])
        if not isinstance(fixtures, list) or any(not isinstance(x, dict) or not x.keys() <= {"stdin", "stdout", "returncode"} for x in fixtures):
            raise ValueError("Invalid local synthetic fixtures")
        args["fixtures"] = tuple(Fixture(**x) for x in fixtures)
        grant = ExecutionPolicy(**args)
        if grant.job_id in policies:
            raise ValueError("Duplicate local job grant")
        policies[grant.job_id] = grant
    return policies


@dataclass(frozen=True)
class Job:
    job_id: str
    attempt: int
    language: str
    source: str
    operation: str
    platform: str
    architecture: str
    capabilities: frozenset[str]
    dependencies: frozenset[str]
    output_protocol: str = "text"

    def __post_init__(self):
        if not isinstance(self.job_id, str) or not ID.fullmatch(self.job_id) or type(self.attempt) is not int or self.attempt != 1 or self.language != "c11":
            raise ValueError("Invalid initial C11 job metadata")
        if not isinstance(self.operation, str) or not NAME.fullmatch(self.operation) or not isinstance(self.source, str):
            raise ValueError("Invalid source or operation")
        if not isinstance(self.platform, str) or not isinstance(self.architecture, str) or len(self.architecture) > 32:
            raise ValueError("Invalid platform metadata")
        if not isinstance(self.capabilities, frozenset) or not isinstance(self.dependencies, frozenset) or not self.capabilities <= CAPABILITIES or not self.dependencies <= DEPENDENCIES.keys():
            raise ValueError("Invalid capability/dependency declaration")
        if self.output_protocol not in ("text", "json-v1"):
            raise ValueError("Invalid output protocol")

    @classmethod
    def parse(cls, data):
        fields = {"type", "job_id", "attempt", "language", "source", "operation", "platform", "architecture", "capabilities", "dependencies"}
        if not isinstance(data, dict) or not fields <= data.keys() or not data.keys() <= fields | {"output_protocol"} or data["type"] != "validate_job":
            raise ValueError("Malformed job manifest; cloud policy/paths are not accepted")
        if not isinstance(data["job_id"], str) or not ID.fullmatch(data["job_id"]):
            raise ValueError("Invalid job ID")
        if type(data["attempt"]) is not int or data["attempt"] != 1 or data["language"] != "c11":
            raise ValueError("Initial job must declare attempt 1 and C11")
        if not isinstance(data["operation"], str) or not NAME.fullmatch(data["operation"]):
            raise ValueError("Invalid operation")
        if not isinstance(data["platform"], str) or not isinstance(data["architecture"], str) or len(data["architecture"]) > 32:
            raise ValueError("Invalid platform metadata")
        if not isinstance(data["source"], str):
            raise ValueError("Source must be a UTF-8 string")
        args = {k: v for k, v in data.items() if k != "type"}
        args["capabilities"] = string_set(args["capabilities"], CAPABILITIES)
        args["dependencies"] = string_set(args["dependencies"], DEPENDENCIES)
        return cls(**args)


@dataclass(frozen=True)
class CodeRevision:
    job_id: str
    attempt: int
    language: str
    source: str

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) != {"type", "job_id", "attempt", "language", "source"} or data["type"] != "code_revision":
            raise ValueError("Revision cannot change task, capabilities, target or policy")
        if type(data["attempt"]) is not int or not isinstance(data["source"], str):
            raise ValueError("Malformed revision")
        return cls(**{k: v for k, v in data.items() if k != "type"})


def check_source(source, job, policy):
    try:
        raw = source.encode("utf-8", "strict")
    except (AttributeError, UnicodeError) as error:
        raise ValueError("Source is not valid UTF-8") from error
    if not raw or len(raw) > policy.max_source_bytes or b"\0" in raw:
        raise ValueError("Empty, malformed or oversize C source")
    comments = re.sub(r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                      lambda match: " " if match[0].startswith(("//", "/*")) else match[0], source)
    if re.search(r"^\s*#\s*(?:include_next|embed|import)\b", comments, re.M):
        raise ValueError("Unsupported external dependency directive")
    for directive in re.findall(r"^\s*#\s*include\b([^\n]*)", comments, re.M):
        include = re.fullmatch(r"\s*<([A-Za-z0-9_./-]+)>\s*", directive)
        if not include:
            raise ValueError("Only declared system headers are accepted")
        header = include[1]
        if ".." in header.split("/") or header.startswith("/"):
            raise ValueError("Private or relative include paths are not permitted")
        if header not in STANDARD_HEADERS and not any(header.startswith(prefix) for dep in job.dependencies for prefix in DEPENDENCIES[dep][1]):
            raise ValueError("Header requires a declared available dependency")
    for literal in re.findall(r'"(?:\\.|[^"\\])*"', comments):
        text = literal[1:-1]
        if text.startswith(("/", "~/")) or re.match(r"[A-Za-z]:[/\\]", text):
            raise ValueError("Pass granted paths separately at runtime, not in source")
    clean = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', '""', comments)
    entry = r"\bint\s+main\s*\(\s*(?:void|int\s+\w+\s*,\s*char\s*\*\s*(?:\*\s*\w+|\w+\s*\[\s*\]))\s*\)\s*\{"
    if len(re.findall(entry, clean)) != 1:
        raise ValueError("Standalone jobs require a normal int main entry point")
    checks = {
        "process.spawn": r"\b(?:system|popen|_popen|exec\w*|fork|posix_spawn\w*|CreateProcess\w*|ShellExecute\w*|WinExec)\s*\(",
        "files.write": r"\b(?:remove|rename|unlink|DeleteFile\w*|MoveFile\w*)\s*\(",
        "network": r"\b(?:socket|connect|WinHttp\w*|InternetOpen\w*)\s*\(",
    }
    for capability, pattern in checks.items():
        if capability not in job.capabilities and re.search(pattern, clean):
            raise ValueError("Obvious undeclared capability use")
    return raw


def sanitize_diagnostics(raw, limit=16384):
    """Transmit canonical categories/locations, never raw lines, tokens or snippets."""
    categories = (
        (r"undeclared|C2065", "undeclared identifier"),
        (r"unknown type|C2146", "unknown type"),
        (r"undefined reference|undefined symbol|unresolved external|LNK2019", "undefined symbol"),
        (r"file not found|No such file|cannot open.*(?:include|file)|C1083", "required header or library unavailable"),
        (r"too (?:few|many) arguments|C2198|C2197", "argument count mismatch"),
        (r"incompatible|conflicting types|C2440", "incompatible types"),
        (r"redefinition|multiple definition|duplicate symbol|C2084", "duplicate definition"),
        (r"expected|syntax error|C2059", "syntax error"),
    )
    result = []
    for line in raw[:131072].splitlines():
        if not re.search(r"\b(?:error|fatal error)\b|LNK\d{4}", line, re.I):
            continue
        location = re.search(r"\bprogram\.c(?::(\d{1,7})(?::(\d{1,7}))?|\((\d{1,7})(?:,(\d{1,7}))?\))", line)
        label = "compiler"
        if location:
            label = f"program.c:{location[1] or location[3]}:{location[2] or location[4] or '0'}"
        category = next((safe for pattern, safe in categories if re.search(pattern, line, re.I)), "compiler error (detail withheld)")
        item = f"{label}: error: {category}"
        if item not in result:
            result.append(item)
    text = "\n".join(result) or "Compiler reported an error (details withheld)."
    return text[:limit]


def dependency_args(job, compiler):
    libraries = sorted({lib for dep in job.dependencies for lib in DEPENDENCIES[dep][2]})
    if compiler.system == "Darwin":
        return tuple(arg for lib in libraries for arg in ("-framework", lib))
    return tuple(f"{lib}.lib" if compiler.name == "msvc" else f"-l{lib}" for lib in libraries)


@dataclass
class CompiledArtifact:
    executable: Path
    binary_sha256: str
    source_sha256: str
    job: Job
    policy: ExecutionPolicy
    valid: bool = True
    consumed: bool = False

    def verify(self):
        try:
            return (self.valid and not self.consumed and self.executable.is_file()
                    and not self.executable.is_symlink()
                    and self.executable.stat().st_size <= self.policy.limits.disk_bytes
                    and digest(self.executable.read_bytes()) == self.binary_sha256)
        except OSError:
            return False


@dataclass
class ValidationResult:
    job_id: str | None
    status: str
    reason: str | None = None
    attempts: int = 0
    compiler: str | None = None
    source_sha256: str | None = None
    diagnostics: str = ""
    preflight: str = "PREFLIGHT_BLOCKED"
    artifact: CompiledArtifact | None = field(default=None, repr=False)
    build_directory: object | None = field(default=None, repr=False)

    def event(self):
        return {"type": "validation_result", "job_id": self.job_id, "status": self.status, "reason": self.reason,
                "attempts": self.attempts, "compiler": self.compiler, "source_sha256": self.source_sha256,
                "diagnostics": self.diagnostics, "preflight": self.preflight}

    def close(self):
        if self.artifact:
            self.artifact.valid = False
        if self.build_directory:
            self.build_directory.cleanup()
            self.build_directory = None


async def validate_job(job, compiler, request_revision, *, policy=None, boundary=None, revision_timeout=30, local_platform=None):
    """Contract -> constrained compile -> bounded backend repair -> safe preflight.

    The local app supplies policy/boundary objects. Neither is accepted remotely.
    An absent boundary blocks before the generated source reaches any compiler.
    """
    job_id = job.job_id if isinstance(job, Job) else job.get("job_id") if isinstance(job, dict) else None
    if not isinstance(job_id, str) or not ID.fullmatch(job_id):
        job_id = None
    result = ValidationResult(job_id, "REJECTED", "SOURCE_INVALID")
    boundary = boundary or IsolationBoundary()
    try:
        job = job if isinstance(job, Job) else Job.parse(job)
        if not isinstance(policy, ExecutionPolicy) or policy.job_id != job.job_id:
            result.reason = "AUTHORIZATION_REQUIRED"
            return result
        if job.operation != policy.operation or not job.capabilities <= policy.capabilities or not job.dependencies <= policy.dependencies:
            result.reason = "CAPABILITY_DENIED"
            return result
        target, arch = local_platform or (platform.system(), platform.machine())
        if target not in ("Darwin", "Windows", "Linux") or job.platform != target or architecture(job.architecture) != architecture(arch) or any(DEPENDENCIES[dep][0] != target for dep in job.dependencies):
            result.reason = "PLATFORM_UNSUPPORTED"
            return result
        result.source_sha256 = digest(check_source(job.source, job, policy))
    except (ValueError, TypeError, UnicodeError):
        return result
    result.compiler = compiler.name if compiler else None
    if compiler is None or compiler.system != job.platform:
        result.status, result.reason = "VALIDATION_FAILED", "COMPILER_UNAVAILABLE"
        return result
    if not boundary.can_build():
        result.status, result.reason = "VALIDATION_BLOCKED", "BUILD_BOUNDARY_UNAVAILABLE"
        return result
    seen_sources, seen_errors = set(), set()
    source = job.source
    directory = None
    try:
        for attempt in range(1, policy.max_attempts + 1):
            try:
                raw = check_source(source, job, policy)
            except ValueError:
                result.status, result.reason = "VALIDATION_FAILED", "SOURCE_INVALID"
                return result
            source_hash = digest(raw)
            if source_hash in seen_sources:
                result.status, result.reason = "VALIDATION_FAILED", "REPEATED_SOURCE"
                return result
            seen_sources.add(source_hash)
            result.source_sha256 = source_hash
            if directory:
                directory.cleanup()
            directory = tempfile.TemporaryDirectory(prefix="janus-build-")
            stage = Path(directory.name)
            src, exe = stage / "program.c", stage / ("program.exe" if target == "Windows" else "program")
            src.write_bytes(raw)
            result.attempts = attempt
            try:
                compiled = await asyncio.wait_for(boundary.compile(compiler, src, exe, policy.limits, dependency_args(job, compiler)), boundary.operation_timeout(policy.limits, "compile"))
            except TimeoutError:
                result.status, result.reason = "VALIDATION_FAILED", "COMPILE_TIMEOUT"
                return result
            except BoundaryUnavailable:
                result.status, result.reason = "VALIDATION_BLOCKED", "BUILD_BOUNDARY_UNAVAILABLE"
                return result
            except ResourceLimitExceeded:
                result.status, result.reason = "VALIDATION_FAILED", "RESOURCE_LIMIT"
                return result
            except OSError:
                result.status, result.reason = "VALIDATION_FAILED", "COMPILER_UNAVAILABLE"
                return result
            if not isinstance(compiled, CompileResult) or any(not isinstance(value, str) for value in (compiled.stdout, compiled.stderr)):
                result.status, result.reason = "VALIDATION_FAILED", "COMPILER_UNAVAILABLE"
                return result
            if any(len(value.encode("utf-8", "replace")) > policy.limits.diagnostics_bytes for value in (compiled.stdout, compiled.stderr)):
                result.status, result.reason = "VALIDATION_FAILED", "RESOURCE_LIMIT"
                return result
            if compiled.reason not in (None, "COMPILE_ERROR", "COMPILER_UNAVAILABLE", "COMPILE_TIMEOUT", "COMPILE_CANCELLED", "RESOURCE_LIMIT"):
                result.status, result.reason = "VALIDATION_FAILED", "COMPILER_UNAVAILABLE"
                return result
            if compiled.reason is None and (type(compiled.returncode) is not int or (compiled.returncode == 0 and not compiled.executable_exists)):
                result.status, result.reason = "VALIDATION_FAILED", "COMPILER_UNAVAILABLE"
                return result
            if compiled.success:
                if not exe.is_file() or exe.is_symlink():
                    result.status, result.reason = "VALIDATION_FAILED", "COMPILER_UNAVAILABLE"
                    return result
                if exe.stat().st_size > policy.limits.disk_bytes:
                    result.status, result.reason = "VALIDATION_FAILED", "RESOURCE_LIMIT"
                    return result
                binary_hash = await finish_thread(lambda: digest(exe.read_bytes()))
                artifact = CompiledArtifact(exe, binary_hash, source_hash, job, policy)
                result.artifact, result.build_directory = artifact, directory
                directory = None
                await preflight(result, boundary)
                return result
            result.diagnostics = sanitize_diagnostics(compiled.stderr + "\n" + compiled.stdout)
            result.status, result.reason = "VALIDATION_FAILED", compiled.reason or "COMPILE_ERROR"
            if result.reason != "COMPILE_ERROR":
                return result
            error_hash = digest(result.diagnostics.encode())
            if error_hash in seen_errors:
                result.reason = "REPEATED_DIAGNOSTIC"
                return result
            seen_errors.add(error_hash)
            if attempt >= policy.max_attempts:
                result.reason = "ATTEMPTS_EXHAUSTED"
                return result
            request = {"type": "repair_requested", "job_id": job.job_id, "attempt": attempt, "reason": "COMPILE_ERROR",
                       "platform": target, "architecture": architecture(arch), "compiler": compiler.name,
                       "language_standard": "c11", "source_sha256": source_hash, "diagnostics": result.diagnostics}
            try:
                revision = await asyncio.wait_for(request_revision(job.job_id, attempt, request), revision_timeout)
                revision = revision if isinstance(revision, CodeRevision) else CodeRevision.parse(revision)
                if revision.job_id != job.job_id or revision.attempt != attempt + 1 or revision.language != "c11":
                    raise ValueError("Stale or mismatched revision")
            except TimeoutError:
                result.reason = "REPAIR_TIMEOUT"
                return result
            except (ValueError, TypeError):
                result.reason = "REVISION_INVALID"
                return result
            source = revision.source
    except BaseException:
        result.close()
        raise
    finally:
        if directory:
            directory.cleanup()
    return result


def valid_output(result, policy):
    if result.timed_out or result.output_limited or result.unavailable or result.cancelled:
        return False
    if len(result.stdout.encode()) > policy.limits.stdout_bytes or len(result.stderr.encode()) > policy.limits.stderr_bytes:
        return False
    if policy.output_protocol == "json-v1":
        try:
            output = json.loads(result.stdout)
            return isinstance(output, dict) and type(output.get("version")) is int and output["version"] == 1
        except (ValueError, TypeError):
            return False
    return True


async def preflight(result, boundary):
    artifact = result.artifact
    policy = artifact.policy
    result.reason = None
    if not boundary.can_run():
        result.status, result.reason = "VALIDATION_BLOCKED", "EXECUTION_BOUNDARY_UNAVAILABLE"
        result.close()
        return
    if policy.side_effects or artifact.job.capabilities:
        result.status, result.preflight = "READY_FOR_EXECUTION", "PREFLIGHT_LIMITED"
        return
    fixtures = policy.fixtures or (Fixture(),)
    try:
        for fixture in fixtures:
            if not await finish_thread(artifact.verify) or not artifact.valid:
                raise BoundaryUnavailable("Artifact invalidated")
            outcome = await asyncio.wait_for(boundary.run(artifact.executable, fixture.stdin, policy.limits, frozenset()), boundary.operation_timeout(policy.limits, "run"))
            if not valid_output(outcome, policy) or outcome.returncode != fixture.returncode or (policy.fixtures and outcome.stdout != fixture.stdout):
                result.status, result.reason, result.preflight = "VALIDATION_FAILED", "PREFLIGHT_FAILED", "PREFLIGHT_FAILED"
                result.close()
                return
    except BoundaryUnavailable:
        result.status, result.reason = "VALIDATION_BLOCKED", "EXECUTION_BOUNDARY_UNAVAILABLE"
        result.close()
        return
    except (TimeoutError, ResourceLimitExceeded, OSError):
        result.status, result.reason, result.preflight = "VALIDATION_FAILED", "PREFLIGHT_FAILED", "PREFLIGHT_FAILED"
        result.close()
        return
    result.status, result.preflight = "READY_FOR_EXECUTION", "PREFLIGHT_PASSED"
