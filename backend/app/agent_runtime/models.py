"""Strict wire and provider contracts, independent of laptop permissions."""
import hashlib
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = 2
MAX_FRAME_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 128 * 1024
IDENTIFIER = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
CAPABILITIES = frozenset({"files.read", "files.write", "network", "external.send", "desktop.mouse", "desktop.screen", "audio.volume", "process.inspect", "process.spawn", "admin", "persistent.write"})
DEPENDENCIES = {"coreaudio": "Darwin", "applicationservices": "Darwin", "iokit": "Darwin", "imageio": "Darwin", "mac-system": "Darwin", "win32": "Windows"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GenerationRequest(StrictModel):
    description: str = Field(min_length=1, max_length=8192)
    operation: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    platform: Literal["Darwin", "Windows"]
    architecture: Literal["arm64", "x86_64", "x86"]
    capabilities: list[str] = Field(default_factory=list, max_length=11)
    dependencies: list[str] = Field(default_factory=list, max_length=7)
    output_protocol: Literal["text", "json-v1"] = "text"

    @model_validator(mode="after")
    def declarations(self) -> "GenerationRequest":
        for values, allowed in ((self.capabilities, CAPABILITIES), (self.dependencies, DEPENDENCIES)):
            if len(set(values)) != len(values) or not set(values) <= set(allowed):
                raise ValueError("Invalid capability or dependency declaration")
        if any(DEPENDENCIES[name] != self.platform for name in self.dependencies):
            raise ValueError("Dependency does not support the requested platform")
        return self


class GeneratedProgram(StrictModel):
    language: Literal["c11"]
    source: str = Field(min_length=1, max_length=MAX_SOURCE_BYTES)
    operation: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    platform: Literal["Darwin", "Windows"]
    architecture: Literal["arm64", "x86_64", "x86"]
    capabilities: list[str] = Field(max_length=11)
    dependencies: list[str] = Field(max_length=7)
    output_protocol: Literal["text", "json-v1"]
    summary: str = Field(min_length=1, max_length=2048)

    @field_validator("source")
    @classmethod
    def source_bytes(cls, source: str) -> str:
        if len(source.encode("utf-8", "strict")) > MAX_SOURCE_BYTES or "\0" in source:
            raise ValueError("Invalid or oversize C source")
        return source

    def check_scope(self, request: GenerationRequest) -> None:
        for field in ("operation", "platform", "architecture", "output_protocol"):
            if getattr(self, field) != getattr(request, field):
                raise ValueError("Provider changed the requested job scope")
        for field in ("capabilities", "dependencies"):
            values = getattr(self, field)
            if len(values) != len(set(values)) or set(values) != set(getattr(request, field)):
                raise ValueError("Provider changed the requested job scope")

    def manifest(self, job_id: str) -> dict[str, Any]:
        return {"type": "validate_job", "job_id": job_id, "attempt": 1,
                **self.model_dump(exclude={"summary", "output_protocol"})}


class SubmitGeneration(StrictModel):
    device_id: IDENTIFIER
    job_id: IDENTIFIER
    request: GenerationRequest


class SubmitSource(SubmitGeneration):
    program: GeneratedProgram

    @model_validator(mode="after")
    def same_scope(self) -> "SubmitSource":
        self.program.check_scope(self.request)
        return self


class Envelope(StrictModel):
    version: Literal[2]
    type: str = Field(min_length=1, max_length=64)
    device_id: IDENTIFIER
    job_id: IDENTIFIER | None
    state: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]


def envelope(kind: str, device_id: str, job_id: str | None, state: str, payload: dict[str, Any]) -> dict[str, Any]:
    return Envelope(version=PROTOCOL_VERSION, type=kind, device_id=device_id, job_id=job_id, state=state, payload=payload).model_dump()


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def safe_diagnostics(value: Any) -> str:
    """Accept only the local validator's canonical categories, never raw output."""
    categories = ("undeclared identifier", "unknown type", "undefined symbol", "required header or library unavailable", "argument count mismatch", "incompatible types", "duplicate definition", "syntax error", "compiler error \\(detail withheld\\)")
    line = r"(?:compiler|program\.c:[0-9]{1,7}:[0-9]{1,7}): error: (?:" + "|".join(categories) + r")"
    if not isinstance(value, str) or len(value.encode("utf-8")) > 16384:
        raise ValueError("Invalid sanitized diagnostics")
    if value == "Compiler reported an error (details withheld).":
        return value
    if not value or any(not re.fullmatch(line, item) for item in value.splitlines()):
        raise ValueError("Invalid sanitized diagnostics")
    return value


class Hello(StrictModel):
    platform: Literal["Darwin", "Windows"]
    architecture: Literal["arm64", "x86_64", "x86"]
    max_workers: int = Field(ge=1, le=4)
    max_attempts: Literal[3]
    build_boundary_available: bool
    execution_boundary_available: bool
    native_streams: list[Literal["volume", "mouse", "metrics", "processes", "screen"]] = Field(default_factory=list, max_length=5)


HASH = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class RepairPayload(StrictModel):
    attempt: int = Field(ge=1, le=2)
    reason: Literal["COMPILE_ERROR"]
    platform: Literal["Darwin", "Windows"]
    architecture: Literal["arm64", "x86_64", "x86"]
    compiler: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    language_standard: Literal["c11"]
    source_sha256: HASH
    diagnostics: str

    @field_validator("diagnostics")
    @classmethod
    def canonical(cls, value: str) -> str:
        return safe_diagnostics(value)


class ValidationPayload(StrictModel):
    status: Literal["REJECTED", "VALIDATION_FAILED", "VALIDATION_BLOCKED", "READY_FOR_EXECUTION", "CANCELLED"]
    reason: Annotated[str, Field(pattern=r"^[A-Z_]{1,64}$")] | None
    attempts: int = Field(default=0, ge=0, le=3)
    compiler: str | None = Field(default=None, max_length=32)
    source_sha256: HASH | None = None
    diagnostics: str = ""
    preflight: Literal["PREFLIGHT_BLOCKED", "PREFLIGHT_PASSED", "PREFLIGHT_LIMITED", "PREFLIGHT_FAILED"] = "PREFLIGHT_BLOCKED"

    @field_validator("diagnostics")
    @classmethod
    def canonical(cls, value: str) -> str:
        return safe_diagnostics(value) if value else ""


class ExecutionPayload(StrictModel):
    status: Literal["COMPLETED", "EXECUTION_FAILED", "EXECUTION_BLOCKED"]
    reason: Annotated[str, Field(pattern=r"^[A-Z_]{1,64}$")] | None
    returncode: int | None = None
    stdout: str = Field(default="", max_length=65536)
    stderr: str = Field(default="", max_length=16384)


class AckPayload(StrictModel):
    accepted_type: Literal["validate_job", "code_revision", "execute_job", "cancel_job"]
