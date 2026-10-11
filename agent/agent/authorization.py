"""Explicit, local startup consent for a bounded lifetime of generated jobs.

The JSON file describes scope; it never constitutes session approval. Approval
comes from this laptop's terminal or the already-consented local launcher's pipe.
Neither a cloud message nor a persisted JSON boolean can activate a session.
"""
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import platform
import sys

from .isolation import ResourceLimits
from .validation import CAPABILITIES, DEPENDENCIES, MAX_ATTEMPTS, MAX_SOURCE_BYTES, NAME, ExecutionPolicy, Job, string_set

APPROVAL_LINE = "JANUS_SESSION_APPROVED\n"
SESSION_FIELDS = frozenset({"operations", "capabilities", "dependencies", "execute_authorized", "share_outputs", "side_effects", "output_protocols", "max_attempts", "max_source_bytes", "limits"})


@dataclass(frozen=True)
class SessionScope:
    operations: frozenset[str]
    capabilities: frozenset[str]
    dependencies: frozenset[str]
    execute_authorized: bool = True
    share_outputs: bool = True
    side_effects: bool = True
    output_protocols: frozenset[str] = frozenset({"text", "json-v1"})
    max_attempts: int = MAX_ATTEMPTS
    max_source_bytes: int = MAX_SOURCE_BYTES
    limits: ResourceLimits = field(default_factory=ResourceLimits)

    def __post_init__(self):
        if not isinstance(self.operations, frozenset) or not self.operations or len(self.operations) > 256 or any(not isinstance(name, str) or (name != "*" and not NAME.fullmatch(name)) for name in self.operations) or ("*" in self.operations and len(self.operations) != 1):
            raise ValueError("Session operations must be declared names or a single '*' wildcard")
        if not isinstance(self.output_protocols, frozenset) or not self.output_protocols or not self.output_protocols <= {"text", "json-v1"}:
            raise ValueError("Invalid session output protocols")
        # Reuse the immutable per-job contract, including boolean and size limits.
        ExecutionPolicy("local-session-scope", "compute", capabilities=self.capabilities,
                        dependencies=self.dependencies, execute_authorized=self.execute_authorized,
                        share_outputs=self.share_outputs, side_effects=self.side_effects,
                        max_attempts=self.max_attempts, max_source_bytes=self.max_source_bytes,
                        limits=self.limits)
        if any(getattr(self.limits, key) > value for key, value in asdict(ResourceLimits()).items()):
            raise ValueError("Session resource limits cannot exceed supported default budgets")

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or not {"operations", "capabilities", "dependencies"} <= data.keys() or not data.keys() <= SESSION_FIELDS:
            raise ValueError("Malformed local session scope or unsupported policy field")
        args = dict(data)
        operations = args.get("operations")
        if not isinstance(operations, list) or any(not isinstance(x, str) for x in operations) or len(set(operations)) != len(operations):
            raise ValueError("Invalid session operation list")
        args["operations"] = frozenset(operations)
        args["capabilities"] = string_set(args["capabilities"], CAPABILITIES)
        args["dependencies"] = string_set(args["dependencies"], DEPENDENCIES)
        args["output_protocols"] = string_set(args.get("output_protocols", ["text", "json-v1"]), {"text", "json-v1"})
        limits = args.pop("limits", {})
        if not isinstance(limits, dict) or not limits.keys() <= asdict(ResourceLimits()).keys():
            raise ValueError("Invalid local session resource budgets")
        args["limits"] = ResourceLimits(**limits)
        return cls(**args)


@dataclass(frozen=True)
class ApprovedSession:
    """In-memory approval; configuration loaders never create this object."""
    scope: SessionScope

    def __post_init__(self):
        if not isinstance(self.scope, SessionScope):
            raise ValueError("A locally approved session requires a validated scope")

    def grant(self, job: Job) -> ExecutionPolicy | None:
        scope = self.scope
        if ("*" not in scope.operations and job.operation not in scope.operations) or not job.capabilities <= scope.capabilities or not job.dependencies <= scope.dependencies or job.output_protocol not in scope.output_protocols:
            return None
        return ExecutionPolicy(job.job_id, job.operation, capabilities=job.capabilities,
                               dependencies=job.dependencies, execute_authorized=scope.execute_authorized,
                               share_outputs=scope.share_outputs, side_effects=scope.side_effects,
                               output_protocol=job.output_protocol, max_attempts=scope.max_attempts,
                               max_source_bytes=scope.max_source_bytes, limits=scope.limits)


def default_session_policy(system: str | None = None) -> dict:
    """Broad supported local scope; still inactive until this startup's consent."""
    system = system or platform.system()
    if system not in ("Darwin", "Windows"):
        raise ValueError("Native session defaults require macOS or Windows")
    return {"jobs": [], "session": {"operations": ["*"],
            "capabilities": sorted(CAPABILITIES),
            "dependencies": [name for name, details in DEPENDENCIES.items() if details[0] == system],
            "execute_authorized": True, "share_outputs": True, "side_effects": True,
            "output_protocols": ["text", "json-v1"], "max_attempts": MAX_ATTEMPTS,
            "max_source_bytes": MAX_SOURCE_BYTES, "limits": asdict(ResourceLimits())}}


def load_session_scope(path: str | None) -> SessionScope:
    if path is None:
        raise ValueError("Session approval requires --validation-policy with a local session scope")
    file = Path(path)
    if file.stat().st_size > 256 * 1024:
        raise ValueError("Local policy file is too large")
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"jobs", "session"}:
        raise ValueError("Session approval requires a local jobs/session policy file")
    return SessionScope.parse(data["session"])


def approve_session(scope: SessionScope, *, launcher_pipe=False) -> ApprovedSession:
    """Fail closed without explicit local approval; never cache consent to disk."""
    if not isinstance(scope, SessionScope):
        raise ValueError("Invalid local session scope")
    if launcher_pipe:
        if sys.stdin.isatty() or sys.stdin.readline(64) != APPROVAL_LINE:
            raise ValueError("Local launcher session approval was not received")
    else:
        if not sys.stdin.isatty():
            raise ValueError("Session approval requires an interactive local terminal")
        print("JANUS session permissions (until this agent stops):", file=sys.stderr)
        print("  Operations: " + ", ".join(sorted(scope.operations)), file=sys.stderr)
        print("  Capabilities: " + (", ".join(sorted(scope.capabilities)) or "none"), file=sys.stderr)
        print("  Libraries: " + (", ".join(sorted(scope.dependencies)) or "none"), file=sys.stderr)
        print(f"  Execute generated jobs: {scope.execute_authorized}; share outputs: {scope.share_outputs}", file=sys.stderr)
        print("  Generated C uses the verified local Docker worker; unsupported access remains denied.", file=sys.stderr)
        print("Type APPROVE to authorize this local session: ", end="", file=sys.stderr, flush=True)
        try:
            response = sys.stdin.readline(64)
        except (EOFError, OSError) as error:
            raise ValueError("Local session approval was declined") from error
        if response.strip() != "APPROVE":
            raise ValueError("Local session approval was declined")
    return ApprovedSession(scope)
