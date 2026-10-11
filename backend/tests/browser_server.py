"""Browser-test server: exact trusted C fixtures only, never live Gemini code."""
import asyncio
import signal
import tempfile
from pathlib import Path
from unittest.mock import patch

from app import db
from app.agent_runtime.store import SQLiteStore
from agent.jobs import ValidationService
from agent.isolation import IsolationBoundary
from agent.validation import ExecutionPolicy, Fixture
from tests.support import RunningServer, TrustedFixtureBoundary, registry
from tests.native_fixture import NativeFixture


async def main():
    with tempfile.TemporaryDirectory(prefix="janus-browser-db-") as folder, patch.object(db, "DB_PATH", Path(folder) / "app.db"):
        store = SQLiteStore()
        await store.initialize(registry())
        backend = await RunningServer().start(store=store, port=18980)
        boundary = TrustedFixtureBoundary()
        policies = {job_id: ExecutionPolicy(job_id, "compute", execute_authorized=True, share_outputs=True,
            side_effects=False, output_protocol="json-v1", fixtures=(Fixture(stdout='{"version":1,"value":42}\n'),)) for job_id in ("studio-browser", "pwa-browser")}
        native = NativeFixture()
        await backend.agent(ValidationService(boundary.compiler, policies, boundary, local_platform=("Darwin", "x86_64")), native=native)
        # A second real agent can advertise the production default unavailable
        # boundary. Draft-only browser tests must never send it validation jobs.
        source_service = ValidationService(boundary.compiler, boundary=IsolationBoundary(), local_platform=("Darwin", "x86_64"))
        source_agent = None
        @backend.app.get("/health")
        async def health():
            return {"status": "ok", "service": "trusted-browser-test"}
        @backend.app.get("/test/metrics")
        async def metrics():
            return {"generation_calls": len(backend.sdk.calls), "native_commands": native.commands,
                    "source_agent_jobs": len(source_service.seen_jobs)}
        @backend.app.post("/test/source-agent/connect")
        async def connect_source_agent():
            nonlocal source_agent
            if source_agent is None:
                source_agent = await backend.agent(source_service, device=2)
            return {"connected": True}
        @backend.app.post("/test/source-agent/disconnect")
        async def disconnect_source_agent():
            nonlocal source_agent
            if source_agent is not None:
                websocket, task = source_agent
                await websocket.close()
                await task
                source_agent = None
            return {"connected": False}
        @backend.app.post("/test/native/screen")
        async def screen(payload: dict):
            native.sample("screen", {"mime": "image/jpeg", "base64": payload["base64"]})
            return {"sent": True}
        @backend.app.post("/test/pairing/{code}/expire")
        async def expire_pairing(code: str):
            def expire():
                with db.get_db() as connection:
                    connection.execute("UPDATE panel_pairing SET expires_at=0 WHERE code=?", (code,))
                    connection.commit()
            await store.call(expire)
            return {"expired": True}
        @backend.app.post("/test/phones/{device_id}/revoke")
        async def revoke_phone(device_id: str):
            await store.call(db.revoke_device_by_id, device_id)
            return {"revoked": True}
        stopped = asyncio.Event()
        for name in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(name, stopped.set)
        try:
            await stopped.wait()
        finally:
            await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
