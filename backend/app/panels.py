"""One panel/action contract shared by the desktop Studio and paired PWA."""
import json
import math
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, ValidationError, model_validator

from app.agent_runtime.connections import PipelineError
from app.agent_runtime.config import Identity
from app.agent_runtime.models import HASH, IDENTIFIER, GenerationRequest, StrictModel, source_hash

if TYPE_CHECKING:
    from app.agent_runtime.runtime import AgentRuntime
    from app.agent_runtime.jobs import JobRecord

INPUTS = {"button", "toggle", "slider", "joystick", "dpad", "knob", "command", "agent"}


class Canvas(StrictModel):
    width: Literal[360]
    height: Literal[780]


class PanelWidget(StrictModel):
    id: IDENTIFIER
    type: Literal["button", "toggle", "slider", "joystick", "dpad", "knob", "command", "agent", "gauge", "bar", "line", "ring", "temperature", "number", "log", "stream"]
    label: str = Field(min_length=1, max_length=128)
    x: float = Field(ge=0, le=360, allow_inf_nan=False)
    y: float = Field(ge=0, le=780, allow_inf_nan=False)
    w: float = Field(gt=0, le=360, allow_inf_nan=False)
    h: float = Field(gt=0, le=780, allow_inf_nan=False)
    icon: str | None = Field(default=None, max_length=128)
    color: Literal["amber", "cyan", "green", "graphite", "coral"] | None = None
    action: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{0,128}$")
    confirm: bool | None = None
    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)
    step: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    variant: Literal["switch", "push"] | None = None
    source: str | None = Field(default=None, max_length=128)
    unit: str | None = Field(default=None, max_length=32)
    warn: float | None = Field(default=None, allow_inf_nan=False)
    critical: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def geometry(self):
        if self.x + self.w > 360 or self.y + self.h > 780:
            raise ValueError("Widget lies outside the canvas")
        if self.type in INPUTS and self.action is None or self.type not in INPUTS and self.source is None:
            raise ValueError("Widget binding is required")
        if self.min is not None and self.max is not None and self.min >= self.max:
            raise ValueError("Invalid control range")
        return self


class PanelLayout(StrictModel):
    schemaVersion: Literal[2]
    coordinateSpace: Literal["logical"]
    canvas: Canvas
    name: str = Field(min_length=1, max_length=80)
    widgets: list[PanelWidget] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_and_nonoverlapping(self):
        if len({item.id for item in self.widgets}) != len(self.widgets):
            raise ValueError("Duplicate widget ID")
        for n, item in enumerate(self.widgets):
            for other in self.widgets[:n]:
                if item.x < other.x + other.w and item.x + item.w > other.x and item.y < other.y + other.h and item.y + item.h > other.y:
                    raise ValueError("Overlapping widgets")
        return self


class SaveLayout(StrictModel):
    layout: PanelLayout
    revision: int = Field(ge=0)


class PairRequest(StrictModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(default="Mobile phone", min_length=1, max_length=80)


class DispatchRequest(StrictModel):
    widget_id: IDENTIFIER
    job_id: IDENTIFIER
    value: Any = None
    program_id: IDENTIFIER | None = None
    request_sha256: HASH | None = None


def checked_value(widget: dict[str, Any], value: Any) -> None:
    kind = widget["type"]
    if kind == "button":
        valid = value is None
    elif kind == "toggle":
        valid = type(value) is bool
    elif kind in ("slider", "knob"):
        valid = type(value) in (int, float) and widget.get("min", 0) <= value <= widget.get("max", 100) and math.isfinite(value)
    elif kind == "joystick":
        valid = isinstance(value, dict) and set(value) == {"x", "y"} and all(type(v) in (int, float) and -1 <= v <= 1 and math.isfinite(v) for v in value.values())
    elif kind == "dpad":
        valid = value in ("up", "down", "left", "right", "center")
    else:
        valid = isinstance(value, str) and 0 < len(value.strip()) <= 4096
    if not valid:
        raise PipelineError("INVALID_WIDGET_VALUE", 422)


async def action_request(runtime: "AgentRuntime", identity: Identity, payload: DispatchRequest) -> tuple[dict[str, Any], GenerationRequest]:
    document = await runtime.store.layout(identity)
    widget = next((item for item in document["layout"]["widgets"] if item["id"] == payload.widget_id), None)
    if widget is None or widget["type"] not in INPUTS:
        raise PipelineError("ACTION_NOT_FOUND", 404)
    checked_value(widget, payload.value)
    request = await runtime.store.action(identity, widget["action"])
    if request is None:
        raise PipelineError("ACTION_NOT_CONFIGURED", 409)
    if widget["type"] in ("agent", "command"):
        request = request.model_copy(update={"description": payload.value.strip()})
    elif payload.value is not None:
        description = request.description + "\nRequested control value (JSON): " + json.dumps(payload.value, allow_nan=False)
        try:
            request = GenerationRequest.model_validate({**request.model_dump(), "description": description})
        except ValidationError:
            raise PipelineError("INVALID_CONTROL_REQUEST", 422) from None
    return widget, request


async def dispatch(runtime: "AgentRuntime", identity: Identity, payload: DispatchRequest) -> "JobRecord":
    widget, request = await action_request(runtime, identity, payload)
    if payload.request_sha256 is not None and payload.request_sha256 != source_hash(request.model_dump_json()):
        raise PipelineError("REQUEST_CHANGED", 409)
    program = None
    if payload.program_id is not None:
        program = await runtime.store.program(identity, payload.program_id, request)
        if program is None:
            raise PipelineError("PROGRAM_NOT_FOUND", 404)
    return await runtime.jobs.submit(identity, identity.device_id, payload.job_id, request, program,
                                     widget_id=widget["id"], action_id=widget["action"])
