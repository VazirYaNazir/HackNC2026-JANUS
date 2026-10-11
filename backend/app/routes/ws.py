"""Authenticated panel updates and laptop dispatch; never execute on the server."""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.agent_runtime.connections import PipelineError
from app.panels import DispatchRequest, action_request, dispatch

router = APIRouter(tags=["WebSocket"])
ws_router = router


async def broadcast_system_stats_loop():
    # Kept for lifecycle compatibility. Cloud CPU/RAM is not laptop telemetry.
    await asyncio.Future()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    runtime = websocket.app.state.agent_runtime
    if runtime.hub.settings.require_tls and websocket.url.scheme != "wss":
        await websocket.close(code=1008)
        return
    await websocket.accept()
    subscriber, tasks, commands = None, [], set()
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), 10)
        if len(raw.encode()) > 4096:
            raise ValueError("Invalid authentication")
        auth = json.loads(raw)
        if not isinstance(auth, dict) or set(auth) != {"type", "token"} or auth["type"] != "auth" or not isinstance(auth["token"], str):
            raise ValueError("Invalid authentication")
        token = auth["token"]
        identity = await runtime.authenticate(token)
        if identity is None:
            raise ValueError("Unauthorized")
        subscriber = runtime.events.subscribe(identity)
        await websocket.send_json({"type": "auth_ok"})
        for source, value in runtime.native.latest.get(identity, {}).items():
            runtime.events.send(subscriber, {"type": "data", "source": source, "value": value})

        async def writer():
            while True:
                event = await subscriber.queue.get()
                if event["type"] == "disconnect":
                    await websocket.close(code=1013)
                    return
                if await runtime.authenticate(token) != identity:
                    raise ValueError("Revoked credentials")
                async with asyncio.timeout(5):
                    await websocket.send_json(event)

        def error(message):
            runtime.events.send(subscriber, {"type": "error", "message": message})

        async def trigger(message):
            try:
                payload = DispatchRequest(widget_id=message["widget"], job_id=message["job_id"],
                    value=message.get("value"), program_id=message.get("program_id"), request_sha256=message.get("request_sha256"))
                widget, _ = await action_request(runtime, identity, payload)
                if message.get("action") != widget["action"]:
                    raise PipelineError("ACTION_MISMATCH", 422)
                await dispatch(runtime, identity, payload)
            except (ValueError, KeyError):
                error("INVALID_TRIGGER")
            except PipelineError as failure:
                error(failure.reason)
            except Exception:
                error("REQUEST_FAILED")

        def finished(task):
            commands.discard(task)
            if not task.cancelled():
                task.exception()

        async def reader():
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode()) > 16384:
                    raise ValueError("Oversize control frame")
                if await runtime.authenticate(token) != identity:
                    raise ValueError("Revoked credentials")
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise ValueError("Invalid message")
                except ValueError:
                    error("INVALID_MESSAGE")
                    continue
                if message.get("type") == "ping":
                    runtime.events.send(subscriber, {"type": "pong"})
                elif message.get("type") == "trigger" and len(commands) < 4:
                    task = asyncio.create_task(trigger(message))
                    commands.add(task)
                    task.add_done_callback(finished)
                else:
                    error("INVALID_MESSAGE_OR_CAPACITY")

        tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (ValueError, KeyError, TimeoutError, WebSocketDisconnect, RuntimeError):
        try:
            await websocket.close(code=4003)
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        if subscriber is not None:
            runtime.events.unsubscribe(subscriber)
        for task in tasks + list(commands):
            task.cancel()
        await asyncio.gather(*tasks, *commands, return_exceptions=True)
