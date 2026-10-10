import subprocess
import sys
from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.db import (
    get_all_scripts,
    get_script_by_id,
    register_device,
    validate_device_token,
    verify_and_consume_code,
)

router = APIRouter(prefix="/api/v1/panels", tags=["Panels Mobile"])

security = HTTPBearer()

class PairExchangeRequest(BaseModel):
    pairing_code: str
    device_name: str = "Mobile Phone"

def verify_device_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    device_info = validate_device_token(credentials.credentials)
    if not device_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Token invalid or revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return device_info

@router.post("/pair")
def pair_device(payload: PairExchangeRequest):
    if not verify_and_consume_code(payload.pairing_code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired pairing code.",
        )
    device_data = register_device(payload.device_name)
    return {"status": "paired", **device_data}

@router.get("/widgets")
def get_active_widgets(current_device: dict = Depends(verify_device_token)):
    scripts = get_all_scripts()
    widgets = [
        {"script_id": s["id"], "name": s["name"], "widget": s["widget"]}
        for s in scripts
    ]
    return {"device": current_device["name"], "widgets": widgets}

@router.post("/run/{script_id}")
def execute_script(
    script_id: str,
    payload: dict = None,
    current_device: dict = Depends(verify_device_token),
):
    script = get_script_by_id(script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")

    try:
        process = subprocess.run(
            [sys.executable, "-c", script["code"]],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {
            "status": "success",
            "triggered_by": current_device["name"],
            "returncode": process.returncode,
            "stdout": process.stdout.strip(),
            "stderr": process.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Script execution timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution error: {str(e)}")