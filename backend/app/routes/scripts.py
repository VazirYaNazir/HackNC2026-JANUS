import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/scripts", tags=["Scripts Admin"])

# In-memory database mock (replace with SQLite/JSON store for hackathon)
SCRIPTS_DB = {}

class WidgetConfig(BaseModel):
    widget_type: str  # e.g., "button", "slider", "toggle"
    label: str
    default_val: Optional[str] = None

class ScriptCreateRequest(BaseModel):
    name: str
    code: str
    widget: WidgetConfig

class AIGenerateRequest(BaseModel):
    prompt: str

@router.get("/")
def get_all_scripts():
    """Returns all saved scripts and their metadata for BuilderUI."""
    return list(SCRIPTS_DB.values())

@router.post("/")
def create_script(payload: ScriptCreateRequest):
    """Saves a new manual or AI-generated script."""
    script_id = f"script_{len(SCRIPTS_DB) + 1}"
    script_data = {
        "id": script_id,
        "name": payload.name,
        "code": payload.code,
        "widget": payload.widget.model_dump()
    }
    SCRIPTS_DB[script_id] = script_data
    return {"status": "created", "script": script_data}

@router.post("/ai-generate")
def generate_ai_script(payload: AIGenerateRequest):
    """
    Sends the user's prompt to an LLM (OpenAI/Anthropic API) 
    and returns python code + suggested UI widget config.
    """
    # System prompt to ensure LLM responds in JSON format:
    # { "code": "import os...", "widget_type": "button", "label": "Press Me" }
    
    # Mock LLM response for demonstration
    generated_code = f"# Auto-generated code for: {payload.prompt}\nimport os\nprint('Executing AI Action')"
    
    return {
        "suggested_name": payload.prompt[:20],
        "code": generated_code,
        "widget": {
            "widget_type": "button",
            "label": payload.prompt.capitalize()
        }
    }

@router.delete("/{script_id}")
def delete_script(script_id: str):
    if script_id in SCRIPTS_DB:
        del SCRIPTS_DB[script_id]
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Script not found")