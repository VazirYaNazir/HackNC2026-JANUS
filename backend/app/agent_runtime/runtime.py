"""All connection/job state belongs to the public server's event loop."""
import os
from dataclasses import dataclass

from .config import DeviceRegistry, Identity, RuntimeSettings
from .connections import ConnectionHub
from .jobs import JobManager
from .llm import GeminiProvider, LLMProvider
from .events import PanelEvents
from .store import SQLiteStore
from .native import NativeManager


def generation_available(provider) -> bool:
    """False only for a provider that reports no credentials (Gemini without a key); others are assumed ready."""
    return getattr(provider, "client", True) is not None


@dataclass
class AgentRuntime:
    registry: DeviceRegistry
    hub: ConnectionHub
    jobs: JobManager
    provider: LLMProvider
    store: SQLiteStore | None = None
    events: PanelEvents | None = None
    native: NativeManager | None = None

    @classmethod
    def create(cls, registry: DeviceRegistry | None = None, provider: LLMProvider | None = None,
               settings: RuntimeSettings | None = None, store: SQLiteStore | None = None) -> "AgentRuntime":
        registry = registry or DeviceRegistry.from_env()
        settings = settings or RuntimeSettings.from_env()
        provider = provider or GeminiProvider(max_concurrent=int(os.getenv("GEMINI_MAX_CONCURRENT", "8")),
                                             timeout=float(os.getenv("GEMINI_TIMEOUT", "20")),
                                             attempts=int(os.getenv("GEMINI_API_ATTEMPTS", "2")))
        hub = ConnectionHub(registry, settings)
        events = PanelEvents()
        hub.on_presence = lambda connection, online: events.publish(connection.identity, {
            "type": "agent_presence", "session": {"device_id": connection.identity.device_id,
                "online": online, "target": connection.hello.model_dump() if online else None,
                "generation_available": generation_available(provider)}})
        jobs = JobManager(hub, provider, settings, store=store, notify=events.publish)
        native = NativeManager(hub, events)
        async def receive(connection, frame):
            if frame.type == "native_event":
                await native.receive(connection, frame)
            else:
                await jobs.receive(connection, frame)
        async def disconnected(connection):
            native.disconnected(connection)
            await jobs.disconnected(connection)
        hub.on_event, hub.on_disconnect = receive, disconnected
        return cls(registry, hub, jobs, provider, store, events, native)

    @classmethod
    async def persisted(cls) -> "AgentRuntime":
        store = SQLiteStore()
        try:
            registry = await store.initialize(DeviceRegistry.from_env())
            return cls.create(registry=registry, store=store)
        except BaseException:
            await store.close()
            raise

    async def authenticate(self, token: str, *, phones: bool = True) -> Identity | None:
        identity = self.registry.controller(token)
        if identity is None and phones and self.store is not None:
            identity = await self.store.phone(token)
        return identity

    async def close(self) -> None:
        await self.hub.close()
        await self.jobs.close()
        await self.provider.close()
        if self.store is not None:
            await self.store.close()
