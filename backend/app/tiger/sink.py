"""Turns what the backend already sees into Tiger rows. Every method is cheap, synchronous and exception-safe, so a
problem here can never reach the agent/stats path that calls it."""
import json
import logging
import time
from typing import Any, Callable

logger = logging.getLogger("uvicorn.error")

# Reading name (as the native layer publishes it) -> metric name stored in Tiger.
METRICS = ("cpu.percent", "mem.percent", "mem.used_mb", "cpu.temperature")
METRIC_INTERVAL_SECONDS = 5.0
PROCESS_INTERVAL_SECONDS = 10.0
TOP_PROCESSES = 10

# Joystick moves arrive many times a second and would drown the activity feed; slider drags are thinned.
NEVER_RECORDED = {"native_mouse"}
MIN_SECONDS_BETWEEN = {"native_volume": 1.0, "native_scroll": 0.5}


class TigerSink:
    def __init__(self, writer: Any, *, clock: Callable[[], float] = time.monotonic,
                 metric_interval: float = METRIC_INTERVAL_SECONDS, process_interval: float = PROCESS_INTERVAL_SECONDS) -> None:
        self.writer = writer
        self.clock = clock
        self.metric_interval, self.process_interval = metric_interval, process_interval
        self._last_metric: dict[tuple[str, str], float] = {}
        self._last_processes: dict[str, float] = {}
        self._last_action: dict[tuple[str, str], float] = {}

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.writer, "enabled", False))

    def readings(self, device_id: str, values: dict) -> None:
        """`values` is the dict of readings from one native event (cpu.percent, process.tree, ...)."""
        if not self.enabled:
            return
        try:
            now = self.clock()
            for name in METRICS:
                value = values.get(name)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                key = (device_id, name)
                if now - self._last_metric.get(key, -1e9) >= self.metric_interval:
                    self._last_metric[key] = now
                    self.writer.metric(device_id, name, value)
            tree = values.get("process.tree")
            if isinstance(tree, str) and now - self._last_processes.get(device_id, -1e9) >= self.process_interval:
                self._last_processes[device_id] = now
                rows = top_processes(tree)
                if rows:
                    self.writer.process_samples(device_id, rows)
        except Exception:  # noqa: BLE001 - telemetry must never break the stats path
            logger.debug("tiger sink ignored a reading", exc_info=True)

    def action(self, device_id: str, source: str, action_id: str, args: dict | None, outcome: str, request_id: str | None = None) -> None:
        if not self.enabled or action_id in NEVER_RECORDED:
            return
        try:
            gap = MIN_SECONDS_BETWEEN.get(action_id)
            now = self.clock()
            if gap is not None:
                key = (device_id, action_id)
                if now - self._last_action.get(key, -1e9) < gap:
                    return
                self._last_action[key] = now
            self.writer.action_event(device_id, source, action_id, args, outcome, request_id)
        except Exception:  # noqa: BLE001
            logger.debug("tiger sink ignored an action", exc_info=True)


def top_processes(process_tree_json: str, limit: int = TOP_PROCESSES) -> list[dict]:
    """The busiest `limit` non-context processes from the native `process.tree` reading."""
    try:
        entries = json.loads(process_tree_json)
    except ValueError:
        return []
    usable = [e for e in entries if isinstance(e, dict) and not e.get("context") and isinstance(e.get("cpu_percent"), (int, float))]
    usable.sort(key=lambda e: e["cpu_percent"], reverse=True)
    rows = []
    for entry in usable[:limit]:
        ram = entry.get("ram_bytes")
        rows.append({"name": entry.get("name", "?"), "pid": entry.get("pid", 0), "cpu": entry["cpu_percent"],
                     "ram_mb": ram / 1048576 if isinstance(ram, (int, float)) else None})
    return rows
