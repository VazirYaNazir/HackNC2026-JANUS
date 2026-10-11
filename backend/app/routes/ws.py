import asyncio
import json
import logging
import subprocess
import sys
from typing import Dict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import psutil

from app.db import get_script_by_id, validate_device_token

logger = logging.getLogger("uvicorn.error")

router = APIRouter(tags=["WebSocket"])
ws_router = router

# Active Connection Pool: socket -> metadata dict
connected_clients: Dict[WebSocket, dict] = {}


def _execute_script(code: str) -> dict:
    try:
        process = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {
            "status": "success" if process.returncode == 0 else "error",
            "returncode": process.returncode,
            "stdout": process.stdout.strip(),
            "stderr": process.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "returncode": -1,
            "stdout": "",
            "stderr": "Script execution timed out (5s limit)",
        }
    except Exception as exc:
        return {
            "status": "error",
            "returncode": -1,
            "stdout": "",
            "stderr": f"Execution error: {str(exc)}",
        }


async def broadcast_system_stats_loop():
    """Broadcasts CPU and RAM stats to all connected clients every 2 seconds."""
    # Prime psutil measurement
    psutil.cpu_percent(interval=None)
    try:
        while True:
            await asyncio.sleep(2)
            if not connected_clients:
                continue

            cpu_usage = psutil.cpu_percent(interval=None)
            ram_usage = psutil.virtual_memory().percent

            stats_msg = {
                "type": "stats_update",
                "data": {
                    "cpu_percent": cpu_usage,
                    "ram_percent": ram_usage,
                    "cpu": cpu_usage,
                    "ram": ram_usage,
                },
            }

            disconnected = []
            for ws in list(connected_clients.keys()):
                try:
                    await ws.send_json(stats_msg)
                except Exception:
                    disconnected.append(ws)

            for ws in disconnected:
                connected_clients.pop(ws, None)
    except asyncio.CancelledError:
        pass


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # 1. Authentication Handshake (wait up to 10 seconds)
    try:
        auth_raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        auth_data = json.loads(auth_raw)
    except asyncio.TimeoutError:
        try:
            await websocket.send_json({"type": "error", "message": "Authentication timed out."})
            await websocket.close(code=4003)
        except Exception:
            pass
        return
    except (json.JSONDecodeError, WebSocketDisconnect):
        try:
            await websocket.send_json({"type": "error", "message": "Invalid authentication message."})
            await websocket.close(code=4003)
        except Exception:
            pass
        return

    if not isinstance(auth_data, dict) or auth_data.get("type") != "auth" or not auth_data.get("token"):
        try:
            await websocket.send_json({"type": "error", "message": "Missing or invalid authentication credentials."})
            await websocket.close(code=4003)
        except Exception:
            pass
        return

    token = auth_data["token"]
    device_info = validate_device_token(token)
    if not device_info:
        try:
            await websocket.send_json({"type": "error", "message": "Invalid or revoked device token."})
            await websocket.close(code=4003)
        except Exception:
            pass
        return

    # Add to pool & acknowledge
    connected_clients[websocket] = device_info
    await websocket.send_json({"type": "auth_ok"})

    # 2. Message Handling Loop
    try:
        while True:
            raw_msg = await websocket.receive_text()
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON format."})
                continue

            if not isinstance(msg, dict):
                await websocket.send_json({"type": "error", "message": "Expected JSON object."})
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})

            elif msg_type == "trigger":
                script_id = msg.get("script_id")
                if not script_id:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Missing script_id.",
                    })
                    continue

                script = get_script_by_id(script_id)
                if not script:
                    await websocket.send_json({
                        "type": "trigger_result",
                        "script_id": script_id,
                        "status": "error",
                        "message": "Script not found.",
                    })
                    continue

                execution_result = await asyncio.to_thread(_execute_script, script["code"])
                await websocket.send_json({
                    "type": "trigger_result",
                    "script_id": script_id,
                    **execution_result,
                })

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning(f"WebSocket client error: {exc}")
    finally:
        connected_clients.pop(websocket, None)
