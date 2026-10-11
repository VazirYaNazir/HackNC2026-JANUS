"""Device-scoped fixed native controls and bounded laptop readings."""
import asyncio
import base64
import json
import math
from uuid import uuid4
from .connections import PipelineError

STREAMS = {"volume", "mouse", "metrics", "processes", "screen"}
NATIVE_ACTIONS = {"native_volume", "native_mouse", "native_left_click", "native_right_click", "native_scroll"}


def number(value, low, high):
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise ValueError("Invalid native reading")
    return value


def readings(event):
    stream = event.get("stream")
    if stream not in STREAMS:
        raise ValueError("Unknown native stream")
    if event.get("type") == "error":
        error = event.get("error")
        if not isinstance(error, str) or len(error) > 2048:
            raise ValueError("Invalid native error")
        source = {"metrics": "cpu.temperature", "processes": "process.tree", "screen": "screen.frame", "volume": "volume.percent"}.get(stream)
        return {**({source: None} if source else {}), f"native.{stream}.status": error}
    if event.get("type") not in ("telemetry", "command_result") or not isinstance(event.get("data"), dict):
        raise ValueError("Invalid native data")
    data = event["data"]
    result = {f"native.{stream}.status": "Live"}
    if stream == "volume":
        result["volume.percent"] = number(data.get("value"), 0, 100)
    elif stream == "metrics":
        cpu = data.get("cpu_percent")
        result["cpu.percent"] = number(cpu, 0, 100) if cpu is not None else None
        ram = data.get("ram")
        result["mem.percent"] = number(ram.get("percent"), 0, 100) if isinstance(ram, dict) else None
        result["mem.used_mb"] = number(ram.get("used_bytes"), 0, 2**64) / 1048576 if isinstance(ram, dict) else None
        temperature = data.get("cpu_temperature_c")
        result["cpu.temperature"] = number(temperature, -100, 300) if temperature is not None else None
        result["native.metrics.status"] = str(data.get("temperature_status", "Live"))[:2048]
    elif stream == "processes":
        processes = data.get("processes")
        if not isinstance(processes, list) or len(processes) > 16384:
            raise ValueError("Invalid process list")
        entries = {}
        for process in processes:
            if not isinstance(process, dict):
                raise ValueError("Invalid process entry")
            pid = number(process.get("pid"), 0, 2**32)
            parent = process.get("parent_pid")
            if type(pid) is not int or pid in entries or parent is not None and (type(parent) is not int or not 0 <= parent <= 2**32):
                raise ValueError("Invalid process identifier")
            name = process.get("name")
            if not isinstance(name, str) or len(name) > 4096 or type(process.get("context")) is not bool:
                raise ValueError("Invalid process name or context")
            entries[pid] = {"pid": pid, "parent_pid": parent, "name": name[:256], "context": process["context"],
                "cpu_percent": number(process["cpu_percent"], 0, 100000) if process.get("cpu_percent") is not None else None,
                "ram_bytes": number(process["ram_bytes"], 0, 2**64) if process.get("ram_bytes") is not None else None}
        selected = sorted((p for p in entries.values() if not p["context"]), key=lambda p: (p["cpu_percent"] or 0, p["ram_bytes"] or 0), reverse=True)[:128]
        kept = {}
        for process in selected:
            while process is not None and process["pid"] not in kept and len(kept) < 512:
                kept[process["pid"]] = process
                process = entries.get(process["parent_pid"])
        result["process.tree"] = json.dumps(list(kept.values()), separators=(",", ":"), allow_nan=False)
    elif stream == "screen":
        encoded = data.get("base64")
        if data.get("mime") != "image/jpeg" or not isinstance(encoded, str) or len(encoded) > 800000:
            raise ValueError("Invalid screen frame")
        raw = base64.b64decode(encoded, validate=True)
        if not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
            raise ValueError("Invalid JPEG")
        result["screen.frame"] = "data:image/jpeg;base64," + encoded
    return result


class NativeManager:
    def __init__(self, hub, events):
        self.hub, self.events = hub, events
        self.pending, self.latest = {}, {}

    async def receive(self, connection, frame):
        if frame.job_id is not None or frame.state != "CONNECTED":
            raise ValueError("Invalid native frame")
        event = frame.payload
        if event.get("stream") not in connection.hello.native_streams:
            raise ValueError("Native stream was not enabled locally")
        if event.get("type") == "command_result":
            pending = self.pending.get((connection, event.get("request_id")))
            if type(event.get("ok")) is not bool:
                raise ValueError("Invalid command result")
            if pending and not pending.done():
                pending.set_result(event)
            if not event["ok"]:
                return
        values = readings(event)
        self.latest.setdefault(connection.identity, {}).update(values)
        for source, value in values.items():
            self.events.publish(connection.identity, {"type": "data", "source": source, "value": value})

    async def command(self, identity, action, value):
        if action not in NATIVE_ACTIONS:
            raise PipelineError("NATIVE_ACTION_NOT_FOUND", 404)
        connection = self.hub.get(identity.device_id)
        target = "volume" if action == "native_volume" else "mouse"
        if target not in connection.hello.native_streams:
            raise PipelineError("NATIVE_TOOLS_NOT_ENABLED", 409)
        if sum(key[0] is connection for key in self.pending) >= 32:
            raise PipelineError("NATIVE_COMMAND_CAPACITY", 429)
        args = {"value": value} if target == "volume" else value if action == "native_mouse" else {"amount": value}
        kind = "set" if target == "volume" else "move" if action == "native_mouse" else "scroll"
        async def send(kind, args):
            request_id = uuid4().hex
            future = asyncio.get_running_loop().create_future()
            key = (connection, request_id)
            self.pending[key] = future
            try:
                await connection.send("native_command", None, "CONNECTED", {"request_id": request_id, "target": target, "action": kind, "args": args})
                result = await asyncio.wait_for(future, 3)
                if not result["ok"]:
                    raise PipelineError("NATIVE_COMMAND_FAILED", 409)
                return {"ok": True, "request_id": request_id}
            except TimeoutError:
                raise PipelineError("NATIVE_COMMAND_TIMEOUT", 504) from None
            finally:
                self.pending.pop(key, None)
        if action in ("native_left_click", "native_right_click"):
            button = "left" if action == "native_left_click" else "right"
            await send("button", {"button": button, "state": "down"})
            return await send("button", {"button": button, "state": "up"})
        return await send(kind, args)

    def disconnected(self, connection):
        self.latest.pop(connection.identity, None)
        for (owner, _), future in list(self.pending.items()):
            if owner is connection and not future.done():
                future.set_exception(PipelineError("AGENT_DISCONNECTED", 503))
