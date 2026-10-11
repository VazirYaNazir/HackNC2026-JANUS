"""Backend-only control API; no mobile or Cloudflare integration here."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket

from app.agent_runtime.config import Identity
from app.agent_runtime.connections import PipelineError
from app.agent_runtime.models import SubmitGeneration, SubmitSource
from app.agent_runtime.runtime import AgentRuntime

router = APIRouter(tags=["Agent jobs v2"])


async def runtime(request: Request) -> AgentRuntime:
    return request.app.state.agent_runtime


async def controller(request: Request, service: Annotated[AgentRuntime, Depends(runtime)]) -> Identity:
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    identity = await service.authenticate(token)
    if identity is None:
        raise HTTPException(401, "UNAUTHORIZED", headers={"WWW-Authenticate": "Bearer"})
    return identity


SERVICE = Annotated[AgentRuntime, Depends(runtime)]
CONTROLLER = Annotated[Identity, Depends(controller)]


@router.websocket("/ws/agents/{device_id}")
async def agent_connection(websocket: WebSocket, device_id: str) -> None:
    await websocket.app.state.agent_runtime.hub.serve(websocket, device_id)


@router.post("/api/v1/agent-jobs/generate", status_code=202)
async def generate_job(payload: SubmitGeneration, identity: CONTROLLER, service: SERVICE) -> dict:
    try:
        record = await service.jobs.submit(identity, payload.device_id, payload.job_id, payload.request)
        return record.view()
    except PipelineError as error:
        raise HTTPException(error.status, error.reason) from None


@router.post("/api/v1/agent-jobs/source", status_code=202)
async def source_job(payload: SubmitSource, identity: CONTROLLER, service: SERVICE) -> dict:
    try:
        record = await service.jobs.submit(identity, payload.device_id, payload.job_id, payload.request, payload.program)
        return record.view()
    except PipelineError as error:
        raise HTTPException(error.status, error.reason) from None


@router.get("/api/v1/agent-jobs/{job_id}")
async def get_job(job_id: str, identity: CONTROLLER, service: SERVICE) -> dict:
    try:
        return await service.jobs.status(identity, job_id)
    except PipelineError as error:
        raise HTTPException(error.status, error.reason) from None


@router.get("/api/v1/agent-jobs/{job_id}/wait")
async def wait_job(job_id: str, identity: CONTROLLER, service: SERVICE,
                   timeout: Annotated[float, Query(gt=0, le=180)] = 30) -> dict:
    try:
        return await service.jobs.wait(identity, job_id, timeout)
    except PipelineError as error:
        raise HTTPException(error.status, error.reason) from None


@router.post("/api/v1/agent-jobs/{job_id}/cancel")
async def cancel_job(job_id: str, identity: CONTROLLER, service: SERVICE) -> dict:
    try:
        return await service.jobs.cancel(identity, job_id)
    except PipelineError as error:
        raise HTTPException(error.status, error.reason) from None
