import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent_runtime.runtime import AgentRuntime
from app.main import app, lifespan
from tests.support import FakeSDK, registry


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_server_registers_new_routes_without_replacing_mobile_routes(self):
        paths = app.openapi()["paths"]
        for path in ("/health", "/api/v1/panels/widgets", "/api/v1/panels/run/{script_id}", "/jobs", "/api/v1/agent-jobs/generate", "/api/v1/agent-jobs/source", "/api/v1/agent-jobs/{job_id}/cancel"):
            self.assertIn(path, paths)

    async def test_runtime_and_existing_services_close_even_on_lifespan_failure(self):
        runtime = SimpleNamespace(close=AsyncMock())
        server = MagicMock()
        async def stats():
            await asyncio.Future()
        target = SimpleNamespace(state=SimpleNamespace())
        with patch("app.main.AgentRuntime.persisted", AsyncMock(return_value=runtime)) as database, patch("app.main.uvicorn.Server", return_value=server), patch("app.main.threading.Thread") as thread, patch("app.main.start_tunnel", AsyncMock()) as tunnel, patch("app.main.stop_tunnel", AsyncMock()) as stop, patch("app.main.broadcast_system_stats_loop", side_effect=stats):
            with self.assertRaisesRegex(RuntimeError, "fixture startup caller failure"):
                async with lifespan(target):
                    self.assertIs(target.state.agent_runtime, runtime)
                    raise RuntimeError("fixture startup caller failure")
            database.assert_awaited_once()
            thread.return_value.start.assert_called_once()
            tunnel.assert_awaited_once_with(8000)
            stop.assert_awaited_once()
            runtime.close.assert_awaited_once()
            self.assertTrue(server.should_exit)

    async def test_runtime_missing_optional_provider_credentials_starts_and_closes(self):
        with patch.dict(os.environ, {}, clear=True):
            runtime = AgentRuntime.create(registry=registry())
            self.assertEqual(len(runtime.registry.devices), 2)
            self.assertIsNone(runtime.provider.client)
            await runtime.close()
