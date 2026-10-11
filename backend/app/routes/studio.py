"""Authenticated panel configuration, phone pairing, and request reuse."""
import os
import re
import json
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request

from app.agent_runtime.connections import PipelineError
from app.agent_runtime.config import Identity
from app.agent_runtime.models import GenerationRequest, source_hash
from app.agent_runtime.similarity import suggestions
from app.panels import DispatchRequest, PairRequest, SaveLayout, action_request, dispatch
from app.routes.agent_jobs import CONTROLLER, SERVICE
from app.tunnel import get_tunnel_url
from app.agent_runtime.native import NATIVE_ACTIONS
from app.panels import checked_value

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


def public_origin(value: str, name: str) -> str:
    """Public pairing addresses are local configuration, never credential URLs."""
    try:
        if not isinstance(value, str):
            raise ValueError
        url = urlsplit(value)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.path not in ("", "/") or url.query or url.fragment
                or any(character.isspace() for character in url.netloc)):
            raise ValueError
        # Accessing port also rejects malformed or out-of-range values.
        url.port
        return f"https://{url.netloc}"
    except ValueError:
        raise HTTPException(503, f"INVALID_{name}") from None


def configured_origin(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return public_origin(value, name) if value else None


def managed_origins() -> tuple[bool, dict[str, str | None]]:
    """Read the launcher's atomic URL file on each request, including restarts."""
    filename = os.getenv("JANUS_PUBLIC_URLS_FILE", "").strip()
    if not filename:
        return False, {}
    try:
        with Path(filename).open(encoding="utf-8") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {"backend", "pwa"}:
            raise ValueError
        return True, {key: public_origin(value, "JANUS_PUBLIC_URLS_FILE") if value is not None else None
                      for key, value in document.items()}
    except FileNotFoundError:
        # The launcher clears unavailable origins instead of advertising stale links.
        return True, {"backend": None, "pwa": None}
    except (OSError, ValueError):
        raise HTTPException(503, "INVALID_JANUS_PUBLIC_URLS_FILE") from None


@router.post("/api/v1/studio/pairing")
async def pairing(request: Request, identity: DESKTOP, service: SERVICE):
    public = configured_origin("PUBLIC_BACKEND_URL")
    pwa = configured_origin("PWA_PUBLIC_URL")
    managed, origins = managed_origins() if not (public and pwa) else (False, {})
    public = public or origins.get("backend")
    pwa = pwa or origins.get("pwa")
    if managed and not public:
        raise HTTPException(503, "PUBLIC_BACKEND_UNAVAILABLE")
    # Keep a caller's existing HTTPS origin; a local Studio can use the backend's
    # automatic tunnel without knowing its generated hostname.
    host = public or (str(request.base_url).rstrip("/") if request.url.scheme == "https"
                      else get_tunnel_url() or str(request.base_url).rstrip("/"))
    return {"code": await service.store.pairing(identity), "expires_in_seconds": 60,
            "host": host, "pwa_url": pwa,
            "status": "ready" if host.startswith("https://") else "local"}


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


@router.post("/api/v1/native/commands")
async def native_command(payload: DispatchRequest, identity: CONTROLLER, service: SERVICE):
    try:
        document = await service.store.layout(identity)
        widget = next((item for item in document["layout"]["widgets"] if item["id"] == payload.widget_id), None)
        allowed = {"native_volume": {"slider", "knob"}, "native_mouse": {"joystick"},
                   "native_left_click": {"button"}, "native_right_click": {"button"}, "native_scroll": {"slider", "knob"}}
        if widget is None or widget.get("action") not in NATIVE_ACTIONS or widget["type"] not in allowed[widget["action"]]:
            raise PipelineError("NATIVE_ACTION_NOT_FOUND", 404)
        checked_value(widget, payload.value)
        if widget["action"] == "native_volume" and not 0 <= payload.value <= 100:
            raise PipelineError("INVALID_WIDGET_VALUE", 422)
        if widget["action"] == "native_scroll" and (not -100 <= payload.value <= 100 or int(payload.value) != payload.value):
            raise PipelineError("INVALID_WIDGET_VALUE", 422)
        return await service.native.command(identity, widget["action"], payload.value)
    except PipelineError as error:
        raise HTTPException(error.status, error.reason) from None
