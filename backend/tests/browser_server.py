"""Browser-test server: exact trusted C fixtures only, never live Gemini code."""
import asyncio
import signal
import tempfile
from pathlib import Path
from unittest.mock import patch

from app import db
from app.agent_runtime.store import SQLiteStore
from agent.jobs import ValidationService
from agent.validation import ExecutionPolicy, Fixture
from tests.support import RunningServer, TrustedFixtureBoundary, registry


async def main():
    with tempfile.TemporaryDirectory(prefix="janus-browser-db-") as folder, patch.object(db, "DB_PATH", Path(folder) / "app.db"):
        store = SQLiteStore()
        await store.initialize(registry())
        backend = await RunningServer().start(store=store, port=18980)
        boundary = TrustedFixtureBoundary()
        policies = {job_id: ExecutionPolicy(job_id, "compute", execute_authorized=True, share_outputs=True,
            side_effects=False, output_protocol="json-v1", fixtures=(Fixture(stdout='{"version":1,"value":42}\n'),)) for job_id in ("studio-browser", "pwa-browser")}
        await backend.agent(ValidationService(boundary.compiler, policies, boundary, local_platform=("Darwin", "x86_64")))
        @backend.app.get("/health")
        async def health():
            return {"status": "ok", "service": "trusted-browser-test"}
        @backend.app.get("/test/metrics")
        async def metrics():
            return {"generation_calls": len(backend.sdk.calls)}
        stopped = asyncio.Event()
        for name in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(name, stopped.set)
        try:
            await stopped.wait()
        finally:
            await backend.close()


if __name__ == "__main__":
    asyncio.run(main())
