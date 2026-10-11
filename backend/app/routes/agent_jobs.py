"""Authenticated source generation and the separate laptop execution pipeline."""
import asyncio
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket

from app.agent_runtime.config import Identity
from app.agent_runtime.connections import PipelineError
from app.agent_runtime.llm import ProviderError
from app.agent_runtime.models import GeneratedProgram, GenerationRequest, PlanRequest, SubmitGeneration, SubmitSource
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


def provider_failure(error: ProviderError) -> HTTPException:
    statuses = {"PROVIDER_NOT_CONFIGURED": 503, "PROVIDER_UNAVAILABLE": 503,
                "RATE_LIMITED": 429, "PROVIDER_TIMEOUT": 504,
                "MALFORMED_RESPONSE": 502, "PROVIDER_ERROR": 502,
                "CAPABILITY_UNAVAILABLE": 403}
    reason = error.reason if error.reason in statuses else "PROVIDER_ERROR"
    return HTTPException(statuses[reason], reason)


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


@router.post("/api/v1/programs/generate", status_code=201)
async def generate_program(payload: GenerationRequest, identity: CONTROLLER, service: SERVICE) -> dict:
    """Create a scoped source draft without dispatching, validating, or executing it.

    Gemini retains its existing concurrency/retry budget. Source review needs no
    connected laptop; reusing this draft still enters the unchanged fresh-job
    local authorization and isolation checks before any generated program runs.
    """
    if service.jobs.closing:
        raise HTTPException(503, "SERVICE_CLOSING")
    if service.store is None:
        raise HTTPException(503, "PROGRAM_STORE_UNAVAILABLE")
    try:
        # Also bound alternative provider implementations and the complete slot
        # wait. Gemini's own shorter deadline and pool remain authoritative.
        async with asyncio.timeout(25):
            generated = await service.provider.generate(payload)
        program = GeneratedProgram.model_validate(generated.model_dump() if isinstance(generated, GeneratedProgram) else generated)
        program.check_scope(payload)
    except ProviderError as error:
        raise provider_failure(error) from None
    except TimeoutError:
        raise HTTPException(504, "PROVIDER_TIMEOUT") from None
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(502, "MALFORMED_RESPONSE") from None
    except OSError:
        raise HTTPException(503, "PROVIDER_UNAVAILABLE") from None
    if service.jobs.closing:
        raise HTTPException(503, "SERVICE_CLOSING")
    try:
        program_id = await service.store.save_program(identity, payload, program)
    except (sqlite3.Error, OSError, RuntimeError):
        raise HTTPException(503, "PROGRAM_STORE_UNAVAILABLE") from None
    return {"program_id": program_id, "program": program.model_dump()}


@router.post("/api/v1/programs/plan")
async def plan_program(payload: PlanRequest, identity: CONTROLLER, service: SERVICE) -> dict:
    """Propose minimal access within this laptop's advertised worker scope.

    Planning does not mint a local grant, create/store a program, or dispatch a
    job. Offline source generation remains the explicit /programs/generate API.
    """
    if service.jobs.closing:
        raise HTTPException(503, "SERVICE_CLOSING")
    try:
        connection = service.hub.get(identity.device_id)
    except PipelineError as error:
        raise HTTPException(error.status, error.reason) from None
    target = connection.hello.execution_target
    if target is None:
        raise HTTPException(503, "EXECUTION_TARGET_UNAVAILABLE")
    if (payload.platform, payload.architecture) != (target.platform, target.architecture):
        raise HTTPException(422, "TARGET_MISMATCH")
    if not set(payload.allowed_capabilities) <= set(target.capabilities):
        raise HTTPException(403, "CAPABILITY_UNAVAILABLE")
    target_scope = target.model_dump()
    try:
        async with asyncio.timeout(25):
            planned = await service.provider.plan(payload)
        request = GenerationRequest.model_validate(planned.model_dump() if isinstance(planned, GenerationRequest) else planned)
        payload.check_scope(request)
    except ProviderError as error:
        raise provider_failure(error) from None
    except TimeoutError:
        raise HTTPException(504, "PROVIDER_TIMEOUT") from None
    except (ValueError, TypeError, UnicodeError):
        raise HTTPException(502, "MALFORMED_RESPONSE") from None
    except OSError:
        raise HTTPException(503, "PROVIDER_UNAVAILABLE") from None
    if service.jobs.closing:
        raise HTTPException(503, "SERVICE_CLOSING")
    if connection.closed or service.hub.connections.get(identity.device_id) is not connection or connection.hello.execution_target is None or connection.hello.execution_target.model_dump() != target_scope:
        raise HTTPException(409, "EXECUTION_TARGET_CHANGED")
    return request.model_dump()


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
