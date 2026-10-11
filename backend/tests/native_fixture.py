"""Deterministic native output fixture. No desktop APIs or user devices involved."""
class NativeFixture:
    streams = ["volume", "mouse", "metrics", "processes", "screen"]
    def __init__(self):
        self.tasks, self.commands = [], []
        self.outbox = None
        self.closed = False

    async def start(self, outbox):
        self.outbox = outbox
        self.sample("volume", {"value": 25})
        self.sample("metrics", {"cpu_percent": 32, "ram": {"percent": 42, "used_bytes": 104857600}, "cpu_temperature_c": 55, "temperature_status": "available"})
        self.sample("processes", {"processes": [
            {"pid": 1, "parent_pid": None, "name": "Parent", "cpu_percent": None, "ram_bytes": None, "context": True},
            {"pid": 2, "parent_pid": 1, "name": "Busy app", "cpu_percent": 25, "ram_bytes": 209715200, "context": False}]})

    def sample(self, stream, data):
        self.outbox.sample({"type": "telemetry", "stream": stream, "data": data})

    def command(self, payload):
        self.commands.append(payload)
        data = {"value": payload["args"]["value"]} if payload["target"] == "volume" else {}
        self.outbox.results.put_nowait({"type": "command_result", "stream": payload["target"], "request_id": payload["request_id"], "ok": True, "data": data})
        self.outbox.ready.set()

    async def close(self):
        self.closed = True
