"""All connection/job state belongs to the public server's event loop."""
import os
from dataclasses import dataclass

from .config import DeviceRegistry, RuntimeSettings
from .connections import ConnectionHub
from .jobs import JobManager
from .llm import GeminiProvider, LLMProvider


@dataclass
class AgentRuntime:
    registry: DeviceRegistry
    hub: ConnectionHub
    jobs: JobManager
    provider: LLMProvider

    @classmethod
    def create(cls, registry: DeviceRegistry | None = None, provider: LLMProvider | None = None,
               settings: RuntimeSettings | None = None) -> "AgentRuntime":
        registry = registry or DeviceRegistry.from_env()
        settings = settings or RuntimeSettings.from_env()
        provider = provider or GeminiProvider(max_concurrent=int(os.getenv("GEMINI_MAX_CONCURRENT", "8")),
                                             timeout=float(os.getenv("GEMINI_TIMEOUT", "20")),
                                             attempts=int(os.getenv("GEMINI_API_ATTEMPTS", "2")))
        hub = ConnectionHub(registry, settings)
        return cls(registry, hub, JobManager(hub, provider, settings), provider)

    async def close(self) -> None:
        await self.hub.close()
        await self.jobs.close()
        await self.provider.close()
