"""Compatibility aliases for the same scoped panel and laptop pipeline."""
from fastapi import APIRouter, HTTPException
from pydantic import Field
from app.agent_runtime.models import StrictModel
from app.agent_runtime.runtime import generation_available
from app.panels import PairRequest, DispatchRequest
from app.routes.agent_jobs import CONTROLLER, SERVICE
from app.routes.studio import pair, submit

router = APIRouter(prefix="/api/v1/panels", tags=["Panels Mobile"])


@router.get("/session")
async def session(identity: CONTROLLER, service: SERVICE):
    connection = service.hub.connections.get(identity.device_id)
    return {"device_id": identity.device_id, "online": bool(connection and not connection.closed),
            "target": connection.hello.model_dump() if connection and not connection.closed else None,
            "generation_available": generation_available(service.provider)}


class PairExchangeRequest(StrictModel):
    pairing_code: str = Field(min_length=1, max_length=32)
    device_name: str = Field(default="Mobile Phone", min_length=1, max_length=80)


@router.post("/pair")
async def pair_device(payload: PairExchangeRequest, service: SERVICE):
    result = await pair(PairRequest(code=payload.pairing_code, name=payload.device_name), service)
    return {"status": "paired", "device_id": result["deviceId"], **result}


@router.get("/widgets")
async def get_active_widgets(identity: CONTROLLER, service: SERVICE):
    document = await service.store.layout(identity)
    return {"device": identity.device_id, "widgets": document["layout"]["widgets"], **document}


@router.post("/run/{script_id}", status_code=202)
async def execute_script(script_id: str, payload: DispatchRequest, identity: CONTROLLER, service: SERVICE):
    if script_id != payload.widget_id:
        raise HTTPException(422, "WIDGET_MISMATCH")
    return await submit(payload, identity, service)
