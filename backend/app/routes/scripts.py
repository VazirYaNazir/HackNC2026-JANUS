# app/routes/scripts.py
import secrets
from typing import Optional
from fastapi import APIRouter, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.config import settings
from app.db import (
    delete_script_by_id,
    generate_pairing_code,
    get_all_devices,
    get_all_scripts,
    revoke_device_by_id,
    save_script,
)

router = APIRouter(prefix="/api/v1/scripts", tags=["Scripts Admin"])

# Header extractor for local BuilderUI app
ADMIN_KEY_HEADER = APIKeyHeader(name="X-Admin-Secret", auto_error=False)

def verify_admin_key(api_key: str = Security(ADMIN_KEY_HEADER)):
    """Verifies that requests to scripts.py carry the matching admin secret."""
    if api_key != settings.ADMIN_SECRET:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Invalid or missing local admin credentials."
        )

class WidgetConfig(BaseModel):
    widget_type: str
    label: str
    default_val: Optional[str] = None

class ScriptCreateRequest(BaseModel):
    name: str
    code: str
    widget: WidgetConfig

@router.get("/", dependencies=[Security(verify_admin_key)])
def list_scripts():
    return get_all_scripts()

@router.post("/", dependencies=[Security(verify_admin_key)])
def create_script(payload: ScriptCreateRequest):
    script_id = f"script_{secrets.token_hex(4)}"
    save_script(
        script_id=script_id,
        name=payload.name,
        code=payload.code,
        widget_type=payload.widget.widget_type,
        label=payload.widget.label,
        default_val=payload.widget.default_val,
    )
    return {"status": "created", "script_id": script_id}

@router.delete("/{script_id}", dependencies=[Security(verify_admin_key)])
def delete_script(script_id: str):
    if not delete_script_by_id(script_id):
        raise HTTPException(status_code=404, detail="Script not found")
    return {"status": "deleted"}

@router.get("/pairing/request", dependencies=[Security(verify_admin_key)])
def request_pairing_code(tunnel_url: str = "http://localhost:8000"):
    code = generate_pairing_code(ttl_seconds=60)
    qr_payload = f"{tunnel_url.strip('/')}?pairing_code={code}"
    return {
        "pairing_code": code,
        "expires_in_seconds": 60,
        "qr_payload": qr_payload,
    }

@router.get("/devices", dependencies=[Security(verify_admin_key)])
def list_devices():
    return {"devices": get_all_devices()}

@router.delete("/devices/{device_id}", dependencies=[Security(verify_admin_key)])
def revoke_device(device_id: str):
    if not revoke_device_by_id(device_id):
        raise HTTPException(status_code=404, detail="Device not found")
    return {"status": "revoked", "device_id": device_id}