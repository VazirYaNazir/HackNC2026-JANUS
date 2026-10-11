"""Versioned outbound job connection; existing local grants remain authoritative."""
import asyncio
import json
import platform
import random
import sys
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, InvalidHandshake

from .bridge import Outbox
from .jobs import JOB_MESSAGES
from .validation import ID, architecture

VERSION = 2
MAX_FRAME_BYTES = 1024 * 1024
COMMAND_STATES = {"validate_job": "VALIDATING", "code_revision": "REPAIRING", "execute_job": "EXECUTING", "cancel_job": "CANCELLED"}


def validate_options(options) -> None:
    if not isinstance(options.device_id, str) or not ID.fullmatch(options.device_id):
        raise ValueError("Set AGENT_DEVICE_ID or provide --device-id")
    url = urlsplit(options.url or "")
    if url.scheme not in ("ws", "wss") or not url.hostname or url.username or url.password or url.fragment or url.query:
        raise ValueError("Provide a backend WebSocket URL without credentials or query parameters")
    if url.scheme == "ws" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Remote backends require wss://")
    if url.path != f"/ws/agents/{options.device_id}":
        raise ValueError("Backend path must be /ws/agents/<device-id>")
    if type(options.workers) is not int or not 1 <= options.workers <= 4:
        raise ValueError("Job workers must be between 1 and 4")


def packet(device_id, kind, job_id, state, payload) -> dict:
    return {"version": VERSION, "type": kind, "device_id": device_id,
            "job_id": job_id, "state": state, "payload": payload}


def decode(raw, device_id) -> dict:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError("Invalid protocol frame")
    def reject_constant(value):
        raise ValueError("Nonfinite JSON value")
    message = json.loads(raw, parse_constant=reject_constant)
    if not isinstance(message, dict) or set(message) != {"version", "type", "device_id", "job_id", "state", "payload"} or type(message["version"]) is not int or message["version"] != VERSION or message["device_id"] != device_id:
        raise ValueError("Protocol version or device identity mismatch")
    if not isinstance(message["payload"], dict) or not isinstance(message["type"], str) or not isinstance(message["state"], str):
        raise ValueError("Invalid message structure")
    if message["job_id"] is not None and (not isinstance(message["job_id"], str) or not ID.fullmatch(message["job_id"])):
        raise ValueError("Invalid job identifier")
    return message


async def send(websocket, message) -> None:
    await websocket.send(json.dumps(message, separators=(",", ":"), ensure_ascii=True, allow_nan=False))


async def connected_session(websocket, device_id, service, *, on_ready=None) -> None:
    outbox = Outbox()
    session = service.connect(outbox)
    tasks, cancellations = [], set()
    target, arch = service.local_platform or (platform.system(), platform.machine())
    try:
        await send(websocket, packet(device_id, "agent_hello", None, "CONNECTED", {
            "platform": target, "architecture": architecture(arch), "max_workers": service.max_parallel, "max_attempts": 3,
            "build_boundary_available": service.boundary.can_build(), "execution_boundary_available": service.boundary.can_run()}))
        ready = decode(await asyncio.wait_for(websocket.recv(), 10), device_id)
        if ready["type"] != "connection_ready" or ready["job_id"] is not None or ready["state"] != "CONNECTED" or set(ready["payload"]) != {"heartbeat_interval"}:
            raise ValueError("Expected connection acknowledgement")
        if on_ready is not None:
            on_ready()

        async def writer():
            while True:
                event = await outbox.get()
                kind, job_id = event["type"], event["job_id"]
                payload = {key: value for key, value in event.items() if key not in ("type", "job_id")}
                state = "ACCEPTED" if kind == "job_ack" else "CONNECTED" if kind == "pong" else "REPAIRING" if kind == "repair_requested" else event["status"]
                await send(websocket, packet(device_id, kind, job_id, state, payload))

        async def cancel(message):
            await session.handle(message)

        def cancellation_finished(task):
            cancellations.discard(task)
            if not task.cancelled():
                task.exception()

        async def reader():
            async for raw in websocket:
                frame = decode(raw, device_id)
                kind, job_id, payload = frame["type"], frame["job_id"], frame["payload"]
                if kind == "ping":
                    if job_id is not None or frame["state"] != "CONNECTED" or set(payload) != {"nonce"} or not isinstance(payload["nonce"], str) or len(payload["nonce"]) > 128:
                        raise ValueError("Invalid heartbeat")
                    await outbox.result({"type": "pong", "job_id": None, **payload})
                    continue
                if kind not in JOB_MESSAGES or job_id is None or frame["state"] != COMMAND_STATES[kind] or "type" in payload or "job_id" in payload:
                    raise ValueError("Invalid job command")
                message = {"type": kind, "job_id": job_id, **payload}
                await outbox.result({"type": "job_ack", "job_id": job_id, "accepted_type": kind})
                if kind == "cancel_job":
                    if len(cancellations) >= 4:
                        raise ValueError("Too many pending cancellations")
                    task = asyncio.create_task(cancel(message))
                    cancellations.add(task)
                    task.add_done_callback(cancellation_finished)
                else:
                    await session.handle(message)

        tasks = [asyncio.create_task(writer()), asyncio.create_task(reader())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except ConnectionClosedOK:
                # A final acknowledgement can race a normal peer close.
                pass
    finally:
        async def cleanup():
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for task in cancellations:
                task.cancel()
            await asyncio.gather(*cancellations, return_exceptions=True)
            await session.close()
        closing = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            # Reconnection/shutdown must not abandon local workers or artifacts
            # if another cancellation arrives while the owned cleanup is running.
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    continue
            closing.result()
            raise


async def run_connection(options, token, service) -> None:
    retry = 1
    def ready():
        nonlocal retry
        retry = 1
        print("Backend connected; device job protocol v2 active", file=sys.stderr)
    while True:
        try:
            async with connect(options.url, additional_headers={"Authorization": f"Bearer {token}"},
                               max_size=MAX_FRAME_BYTES, ping_interval=20, ping_timeout=20, close_timeout=3) as websocket:
                await connected_session(websocket, options.device_id, service, on_ready=ready)
        except (ConnectionClosed, InvalidHandshake, OSError, RuntimeError, ValueError) as error:
            print(f"Agent connection interrupted ({type(error).__name__}); retrying", file=sys.stderr)
        await asyncio.sleep(random.uniform(retry / 2, retry))
        retry = min(30, retry * 2)
