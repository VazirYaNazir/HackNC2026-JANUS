"""Local boundary interface. No native untrusted-code sandbox is supplied yet.

An adapter must be implemented and audited in the local app. Flags in a cloud
manifest or policy JSON cannot install or enable an isolation implementation.
"""
from dataclasses import dataclass
import math
from pathlib import Path
import importlib
import inspect
import re

from .compiler import CCompiler, CompileResult
from .process import ProcessResult

REQUIRED_GUARANTEES = frozenset({"filesystem", "credentials", "network", "cpu", "memory", "processes", "disk", "timeout", "output"})


class BoundaryUnavailable(RuntimeError):
    pass


class ResourceLimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ResourceLimits:
    compile_seconds: float = 30
    run_seconds: float = 3
    diagnostics_bytes: int = 65536
    stdout_bytes: int = 65536
    stderr_bytes: int = 16384
    memory_bytes: int = 512 * 1024 * 1024
    disk_bytes: int = 64 * 1024 * 1024
    processes: int = 16
    cpu_seconds: int = 30

    def __post_init__(self):
        if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n <= 0 for n in (self.compile_seconds, self.run_seconds)):
            raise ValueError("Deadlines must be positive finite numbers")
        if self.compile_seconds > 30 or self.run_seconds > 3:
            raise ValueError("Prototype deadlines cannot exceed 30s compile / 3s run")
        for name in ("diagnostics_bytes", "stdout_bytes", "stderr_bytes", "memory_bytes", "disk_bytes", "processes", "cpu_seconds"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError("Resource budgets must be positive integers")


class IsolationBoundary:
    """Override only after verifying OS enforcement of every listed guarantee.

    Implementations must kill/reap the complete worker tree on timeout or
    cancellation, deny user-home/credential/network access by default, use a
    clean environment, expose only granted paths/APIs, and cap worker resources.
    A temporary directory, process timeout, or compiler success is insufficient.
    """
    build_guarantees: frozenset[str] = frozenset()
    run_guarantees: frozenset[str] = frozenset()
    run_capabilities: frozenset[str] = frozenset()

    def can_build(self) -> bool:
        return REQUIRED_GUARANTEES <= self.build_guarantees

    def can_run(self, capabilities=frozenset()) -> bool:
        return REQUIRED_GUARANTEES <= self.run_guarantees and capabilities <= self.run_capabilities

    async def compile(self, compiler: CCompiler, source: Path, output: Path,
                      limits: ResourceLimits, link_args: tuple[str, ...]) -> CompileResult:
        raise BoundaryUnavailable("No verified build boundary installed")

    async def run(self, executable: Path, stdin: str, limits: ResourceLimits,
                  capabilities: frozenset[str]) -> ProcessResult:
        raise BoundaryUnavailable("No verified execution boundary installed")


def load_boundary(spec: str | None) -> IsolationBoundary:
    """Load an explicitly configured LOCAL, audited adapter, never a cloud value.

    Interface/guarantee checks establish conformance, not an OS security audit.
    Deployment remains responsible for verifying the selected implementation.
    """
    if not spec:
        return IsolationBoundary()
    if not isinstance(spec, str) or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", spec):
        raise ValueError("Local isolation adapter must be module:factory")
    module, name = spec.split(":")
    try:
        boundary = getattr(importlib.import_module(module), name)()
        if not isinstance(boundary, IsolationBoundary) or not boundary.can_build() or not boundary.can_run() or not all(inspect.iscoroutinefunction(method) for method in (boundary.compile, boundary.run)):
            raise ValueError("Invalid adapter interface or missing guarantees")
    except Exception:
        raise ValueError("Cannot load a conforming local isolation adapter") from None
    return boundary
