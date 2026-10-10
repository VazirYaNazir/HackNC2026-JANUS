import asyncio
import os
import secrets
from typing import Literal
from uuid import uuid4

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

router = APIRouter(tags=["Agent"])

AGENT_TOKEN = os.getenv("AGENT_TOKEN", "")
CONTROL_API_KEY = os.getenv("CONTROL_API_KEY", "")

# Single-agent prototype
connected_agent: WebSocket | None = None
pending_jobs: dict[str, asyncio.Future] = {}
send_lock = asyncio.Lock()


class JobRequest(BaseModel):
    action: Literal["ping", "add"]
    args: dict[str, float] = Field(default_factory=dict)


@router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    global connected_agent

    # Authenticate laptop before accepting
    supplied_token = websocket.headers.get("authorization", "")

    if not AGENT_TOKEN or not secrets.compare_digest(
        supplied_token, f"Bearer {AGENT_TOKEN}"
    ):
        await websocket.close(code=1008)
        return

    # Only one agent for now
    if connected_agent is not None:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    connected_agent = websocket

    print("Agent connected")

    try:
        while True:
            message = await websocket.receive_json()

            if message.get("type") == "job_result":
                job_id = message.get("job_id")
                future = pending_jobs.get(job_id)

                if future is not None and not future.done():
                    future.set_result(message)

    except WebSocketDisconnect:
        print("Agent disconnected")

    finally:
        if connected_agent is websocket:
            connected_agent = None

            # Fail jobs waiting on this connection
            for future in pending_jobs.values():
                if not future.done():
                    future.set_exception(
                        ConnectionError("Agent disconnected")
                    )


@router.post("/jobs")
async def submit_job(
    job: JobRequest,
    x_control_key: str | None = Header(default=None),
):
    if not CONTROL_API_KEY or not secrets.compare_digest(
        x_control_key or "", CONTROL_API_KEY
    ):
        raise HTTPException(401, "Unauthorized")

    websocket = connected_agent

    if websocket is None:
        raise HTTPException(503, "No agent connected")

    job_id = str(uuid4())

    future = asyncio.get_running_loop().create_future()
    pending_jobs[job_id] = future

    payload = {
        "type": "job",
        "job_id": job_id,
        "action": job.action,
        "args": job.args,
    }

    try:
        try:
            async with send_lock:
                await websocket.send_json(payload)
        except Exception:
            raise HTTPException(503, "Unable to send job to agent")

        try:
            return await asyncio.wait_for(future, timeout=15)

        except asyncio.TimeoutError:
            raise HTTPException(504, "Agent execution timed out")

        except ConnectionError:
            raise HTTPException(
                503, "Agent disconnected during execution"
            )

    finally:
        pending_jobs.pop(job_id, None)
