"""iMessage sidecar endpoints. The sidecar (imessage/) forwards text here; every safety decision is made in
app.messaging.service. Authenticated with a service token and reachable from loopback only."""
import hmac
import logging
import os
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from app.agent_runtime.models import StrictModel
from app.messaging.service import MessagingService
from app.routes.agent_jobs import SERVICE
from app.routes.studio import DESKTOP

logger = logging.getLogger("uvicorn.error")
router = APIRouter(tags=["Messaging"])

LOOPBACK = {"127.0.0.1", "::1"}
# A reverse proxy or tunnel (cloudflared connects from localhost!) adds at least one of these, so their presence
# means the request did not originate on this machine even though the socket peer looks like loopback.
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "forwarded", "x-real-ip", "cf-connecting-ip", "cf-ray", "true-client-ip")
MIN_TOKEN = 32


class Inbound(StrictModel):
    sender: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    messageId: str = Field(min_length=1, max_length=256)


class Heartbeat(StrictModel):
    connected: bool = True


def messaging_service(request: Request, runtime: SERVICE) -> MessagingService:
    service = getattr(request.app.state, "messaging", None)
    if service is None:
        service = request.app.state.messaging = MessagingService.from_env(runtime, getattr(request.app.state, "tiger_store", None))
    return service


MESSAGING = Annotated[MessagingService, Depends(messaging_service)]


def service_caller(request: Request) -> None:
    client = request.client.host if request.client else ""
    if client not in LOOPBACK or any(name in request.headers for name in PROXY_HEADERS):
        raise HTTPException(403, "LOOPBACK_ONLY")
    expected = os.getenv("MESSAGING_SERVICE_TOKEN", "")
    if len(expected.strip()) < MIN_TOKEN:
        raise HTTPException(503, "MESSAGING_DISABLED")
    header = request.headers.get("authorization", "")
    supplied = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "UNAUTHORIZED")


@router.post("/api/messaging/inbound", dependencies=[Depends(service_caller)])
async def inbound(payload: Inbound, service: MESSAGING):
    reply = await service.handle(payload.sender, payload.text, payload.messageId)
    return {"reply": reply.text, "duplicate": reply.duplicate}


@router.post("/api/messaging/heartbeat", dependencies=[Depends(service_caller)])
async def heartbeat(payload: Heartbeat, service: MESSAGING):
    if payload.connected:
        service.heartbeat()
    else:
        service.disconnected()
    return {"ok": True}


@router.get("/api/v1/studio/messaging")
async def status(identity: DESKTOP, service: MESSAGING):
    missing = []
    if not service.enabled:
        missing.append("IMESSAGE_ALLOWED_SENDERS")
    if len(os.getenv("MESSAGING_SERVICE_TOKEN", "").strip()) < MIN_TOKEN:
        missing.append("MESSAGING_SERVICE_TOKEN")
    enabled = not missing
    connected = enabled and service.connected()
    mode = os.getenv("JANUS_IMESSAGE_MODE", "")
    # A standalone sidecar can keep Spectrum credentials outside the backend.
    # Managed startup shares them, so it can name missing settings precisely.
    if not connected and mode in ("auto", "on"):
        for name in ("SPECTRUM_PROJECT_ID", "SPECTRUM_PROJECT_SECRET"):
            value = os.getenv(name, "").strip()
            placeholder = re.fullmatch(r"\.+|<[^>]+>|(?:your|replace|example)[_-](?:project[_-]id|project[_-]secret|token|secret)", value, re.I)
            if not value or placeholder:
                missing.append(name)
    reason = (None if connected else "MESSAGING_DISABLED" if mode == "off" else
              "MESSAGING_NOT_CONFIGURED" if missing else "SIDECAR_NOT_CONNECTED")
    return {"enabled": enabled, "connected": connected, "reason": reason, "missing": missing}
