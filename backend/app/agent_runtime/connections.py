"""One asynchronous writer lock and heartbeat per authenticated laptop."""
import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

from .config import DeviceRegistry, Identity, RuntimeSettings
from .models import Envelope, Hello, MAX_FRAME_BYTES, envelope


class PipelineError(RuntimeError):
    def __init__(self, reason: str, status: int = 409):
        self.reason, self.status = reason, status
        super().__init__(reason)


@dataclass(eq=False)
class AgentConnection:
    identity: Identity
    websocket: WebSocket
    hello: Hello
    settings: RuntimeSettings
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pong: asyncio.Event = field(default_factory=asyncio.Event)
    nonce: str | None = None
    closed: bool = False

    async def send(self, kind: str, job_id: str | None, state: str, payload: dict[str, Any]) -> None:
        if self.closed:
            raise PipelineError("AGENT_DISCONNECTED", 503)
        try:
            async with asyncio.timeout(self.settings.send_timeout):
                async with self.send_lock:
                    await self.websocket.send_json(envelope(kind, self.identity.device_id, job_id, state, payload))
        except (OSError, RuntimeError, TimeoutError, WebSocketDisconnect):
            raise PipelineError("AGENT_SEND_FAILED", 503) from None

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.settings.heartbeat_interval)
            self.nonce = uuid4().hex
            self.pong.clear()
            await self.send("ping", None, "CONNECTED", {"nonce": self.nonce})
            await asyncio.wait_for(self.pong.wait(), self.settings.heartbeat_timeout)
            self.nonce = None


async def receive_frame(websocket: WebSocket, device_id: str) -> Envelope:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    raw = message.get("text")
    if not isinstance(raw, str):
        raise ValueError("Only JSON text frames are supported")
    if len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError("Oversize protocol frame")
    frame = Envelope.model_validate_json(raw)
    if frame.device_id != device_id:
        raise ValueError("Device identity mismatch")
    return frame


class ConnectionHub:
    def __init__(self, registry: DeviceRegistry, settings: RuntimeSettings):
        self.registry, self.settings = registry, settings
        self.connections: dict[str, AgentConnection] = {}
        self.lock = asyncio.Lock()
        self.on_event: Callable[[AgentConnection, Envelope], Awaitable[None]] | None = None
        self.on_disconnect: Callable[[AgentConnection], Awaitable[None]] | None = None
        self.on_presence = None
        self.tasks: set[asyncio.Task] = set()

    def get(self, device_id: str) -> AgentConnection:
        connection = self.connections.get(device_id)
        if connection is None or connection.closed:
            raise PipelineError("AGENT_OFFLINE", 503)
        return connection

    async def serve(self, websocket: WebSocket, device_id: str) -> None:
        authorization = websocket.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        identity = self.registry.agent(device_id, token)
        secure = websocket.url.scheme == "wss"
        local = websocket.client is not None and websocket.client.host in ("127.0.0.1", "::1", "testclient")
        if identity is None or (not secure and (self.settings.require_tls or not local)):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        connection = None
        tasks: list[asyncio.Task] = []
        try:
            first = await asyncio.wait_for(receive_frame(websocket, device_id), self.settings.handshake_timeout)
            if first.type != "agent_hello" or first.job_id is not None or first.state != "CONNECTED":
                raise ValueError("Expected agent hello")
            hello = Hello.model_validate(first.payload)
            connection = AgentConnection(identity, websocket, hello, self.settings)
            async with self.lock:
                if device_id in self.connections:
                    raise PipelineError("DEVICE_ALREADY_CONNECTED")
                self.connections[device_id] = connection
            await connection.send("connection_ready", None, "CONNECTED", {"heartbeat_interval": self.settings.heartbeat_interval})
            if self.on_presence is not None:
                self.on_presence(connection, True)
            tasks = [asyncio.create_task(self._read(connection)), asyncio.create_task(connection.heartbeat())]
            self.tasks.update(tasks)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except WebSocketDisconnect:
            pass
        except (ValueError, TypeError, UnicodeError, PipelineError, TimeoutError):
            await self._close_socket(websocket, 1008)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.tasks.difference_update(tasks)
            if connection is not None:
                connection.closed = True
                async with self.lock:
                    if self.connections.get(device_id) is connection:
                        self.connections.pop(device_id)
                        if self.on_presence is not None:
                            self.on_presence(connection, False)
                if self.on_disconnect is not None:
                    await self.on_disconnect(connection)
            await self._close_socket(websocket, 1000)

    async def _read(self, connection: AgentConnection) -> None:
        while True:
            frame = await receive_frame(connection.websocket, connection.identity.device_id)
            if frame.type == "pong":
                if frame.job_id is not None or frame.state != "CONNECTED" or frame.payload != {"nonce": connection.nonce} or connection.nonce is None:
                    raise ValueError("Unexpected heartbeat reply")
                connection.pong.set()
            elif self.on_event is not None:
                await self.on_event(connection, frame)
            else:
                raise ValueError("Unexpected protocol message")

    @staticmethod
    async def _close_socket(websocket: WebSocket, code: int) -> None:
        try:
            await websocket.close(code=code)
        except (RuntimeError, OSError, WebSocketDisconnect):
            pass

    async def close(self) -> None:
        for connection in list(self.connections.values()):
            connection.closed = True
            await self._close_socket(connection.websocket, 1001)
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
