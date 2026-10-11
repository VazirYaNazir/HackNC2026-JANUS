"""Authenticated panel configuration, phone pairing, and request reuse."""
import re
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request

from app.agent_runtime.connections import PipelineError
from app.agent_runtime.config import Identity
from app.agent_runtime.models import GenerationRequest, source_hash
from app.agent_runtime.similarity import suggestions
from app.panels import DispatchRequest, PairRequest, SaveLayout, action_request, dispatch
from app.routes.agent_jobs import CONTROLLER, SERVICE

router = APIRouter(tags=["Connected panels"])


async def desktop(request: Request, service: SERVICE):
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    identity = await service.authenticate(token, phones=False)
    if identity is None:
        raise HTTPException(401, "UNAUTHORIZED")
    return identity


DESKTOP = Annotated[Identity, Depends(desktop)]


@router.get("/api/v1/studio/session")
async def session(identity: DESKTOP, service: SERVICE):
    connection = service.hub.connections.get(identity.device_id)
    return {"device_id": identity.device_id, "owner_id": identity.owner_id,
            "online": bool(connection and not connection.closed),
            "target": connection.hello.model_dump() if connection and not connection.closed else None}


@router.get("/api/layout")
async def layout(identity: CONTROLLER, service: SERVICE):
    return await service.store.layout(identity)


@router.put("/api/v1/studio/layout")
async def save_layout(payload: SaveLayout, identity: DESKTOP, service: SERVICE):
    document = payload.layout.model_dump(exclude_none=True)
    revision = await service.store.save_layout(identity, document, payload.revision)
    if revision is None:
        raise HTTPException(409, "LAYOUT_REVISION_CONFLICT")
    service.events.publish(identity, {"type": "layout_updated"})
    return {"layout": document, "revision": revision}


@router.put("/api/v1/studio/actions/{action_id}")
async def configure_action(action_id: str, payload: GenerationRequest, identity: DESKTOP, service: SERVICE):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", action_id):
        raise HTTPException(422, "INVALID_ACTION_ID")
    await service.store.action(identity, action_id, payload)
    return {"action_id": action_id, "request": payload.model_dump()}


@router.get("/api/v1/studio/actions/{action_id}")
async def configured_action(action_id: str, identity: CONTROLLER, service: SERVICE):
    request = await service.store.action(identity, action_id)
    if request is None:
        raise HTTPException(404, "ACTION_NOT_FOUND")
    return request.model_dump()


@router.post("/api/v1/studio/pairing")
async def pairing(identity: DESKTOP, service: SERVICE):
    return {"code": await service.store.pairing(identity), "expires_in_seconds": 60}


@router.post("/api/pair")
async def pair(payload: PairRequest, service: SERVICE):
    result = await service.store.pair(payload.code, payload.name)
    if result is None:
        raise HTTPException(400, "INVALID_OR_EXPIRED_PAIRING_CODE")
    return result


@router.get("/api/v1/agent-jobs")
async def history(identity: CONTROLLER, service: SERVICE):
    return {"jobs": await service.store.history(identity)}


@router.post("/api/v1/programs/similar")
async def similar(payload: GenerationRequest, identity: CONTROLLER, service: SERVICE):
    return await suggestions(service.store, service.provider, identity, payload)


@router.post("/api/v1/panel-actions/prepare")
async def prepare(payload: DispatchRequest, identity: CONTROLLER, service: SERVICE):
    try:
        _, request = await action_request(service, identity, payload)
        return {"request": request.model_dump(), "request_sha256": source_hash(request.model_dump_json()), **await suggestions(service.store, service.provider, identity, request)}
    except PipelineError as error:
        raise HTTPException(error.status, error.reason) from None


@router.post("/api/v1/panel-actions/dispatch", status_code=202)
async def submit(payload: DispatchRequest, identity: CONTROLLER, service: SERVICE):
    try:
        return (await dispatch(service, identity, payload)).view()
    except PipelineError as error:
        raise HTTPException(error.status, error.reason) from None
