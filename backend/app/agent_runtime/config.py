"""Server-side device registration; credentials never come from job bodies."""
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from .models import IDENTIFIER, StrictModel


class Registration(StrictModel):
    device_id: IDENTIFIER
    owner_id: IDENTIFIER
    agent_token: str = Field(min_length=32, max_length=256, repr=False)
    controller_token: str = Field(min_length=32, max_length=256, repr=False)


@dataclass(frozen=True)
class Identity:
    device_id: str
    owner_id: str


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class DeviceRegistry:
    def __init__(self, registrations: list[Registration]):
        self.devices: dict[str, Identity] = {}
        self.agent_hashes: dict[str, bytes] = {}
        self.controller_hashes: dict[str, bytes] = {}
        used: set[bytes] = set()
        if len(registrations) > 128:
            raise ValueError("At most 128 agents can be registered")
        for item in registrations:
            agent, controller = token_hash(item.agent_token), token_hash(item.controller_token)
            if item.device_id in self.devices or agent in used or controller in used or agent == controller:
                raise ValueError("Device IDs and credentials must be unique")
            used.update((agent, controller))
            self.devices[item.device_id] = Identity(item.device_id, item.owner_id)
            self.agent_hashes[item.device_id] = agent
            self.controller_hashes[item.device_id] = controller

    @classmethod
    def from_env(cls) -> "DeviceRegistry":
        path, raw = os.getenv("AGENT_DEVICES_FILE"), os.getenv("AGENT_DEVICES_JSON", "[]")
        try:
            if path:
                file = Path(path)
                if file.stat().st_size > 65536:
                    raise ValueError("Oversize registration file")
                raw = file.read_text(encoding="utf-8")
            if len(raw.encode("utf-8")) > 65536:
                raise ValueError("Oversize registrations")
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError("Registrations must be a list")
            return cls([Registration.model_validate(item) for item in data])
        except (OSError, ValueError, TypeError):
            # Pydantic error strings can contain secret input values.
            raise ValueError("Invalid agent registration configuration") from None

    def agent(self, device_id: str, token: str) -> Identity | None:
        expected = self.agent_hashes.get(device_id)
        return self.devices.get(device_id) if expected and secrets.compare_digest(expected, token_hash(token)) else None

    def controller(self, token: str) -> Identity | None:
        supplied = token_hash(token)
        return next((self.devices[device] for device, expected in self.controller_hashes.items()
                     if secrets.compare_digest(expected, supplied)), None)


@dataclass(frozen=True)
class RuntimeSettings:
    max_jobs: int = 256
    max_active_jobs: int = 16
    max_jobs_per_device: int = 4
    job_timeout: float = 150
    send_timeout: float = 5
    heartbeat_interval: float = 10
    heartbeat_timeout: float = 20
    handshake_timeout: float = 10
    require_tls: bool = True

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        return cls(max_active_jobs=int(os.getenv("AGENT_MAX_ACTIVE_JOBS", "16")),
                   max_jobs_per_device=int(os.getenv("AGENT_MAX_JOBS_PER_DEVICE", "4")),
                   job_timeout=float(os.getenv("AGENT_JOB_TIMEOUT", "150")),
                   require_tls=os.getenv("APP_ENV", "development") != "development")

    def __post_init__(self):
        import math
        if any(type(n) is not int or n < 1 for n in (self.max_jobs, self.max_active_jobs, self.max_jobs_per_device)) or self.max_jobs_per_device > 4:
            raise ValueError("Invalid agent job capacity")
        for value in (self.job_timeout, self.send_timeout, self.heartbeat_interval, self.heartbeat_timeout, self.handshake_timeout):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid agent runtime deadline")
