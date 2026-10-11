"""Trusted, author-written fixture adapter; NOT an OS isolation implementation."""
import asyncio
import hashlib
import json
import shutil
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import uvicorn
from fastapi import FastAPI
from websockets.asyncio.client import connect

sys.path.append(str(Path(__file__).resolve().parents[2] / "agent"))
from agent.compiler import CCompiler
from agent.isolation import IsolationBoundary, REQUIRED_GUARANTEES
from agent.process import run_bounded
from agent.transport import connected_session

from app.agent_runtime.config import DeviceRegistry, Registration, RuntimeSettings
from app.agent_runtime.llm import GeminiProvider
from app.agent_runtime.models import GeneratedProgram, GenerationRequest
from app.agent_runtime.runtime import AgentRuntime
from app.routes.agent_jobs import router

GOOD = '#include <stdio.h>\nint main(void) { puts("{\\"version\\":1,\\"value\\":42}"); return 0; }\n'
BAD = 'int main(void) { return fixture_missing; }\n'
BAD_TWO = 'int main(void) { fixture_unknown value; return 0; }\n'
BAD_THREE = 'int main(void) { return 1 + ; }\n'
TRUSTED_SOURCES = frozenset({GOOD, BAD, BAD_TWO, BAD_THREE})


def request(description="fixture") -> GenerationRequest:
    return GenerationRequest(description=description, operation="compute", platform="Darwin", architecture="x86_64", output_protocol="json-v1")


def program(req=None, source=GOOD) -> GeneratedProgram:
    req = req or request()
    return GeneratedProgram(language="c11", source=source, summary="Trusted test fixture",
                            **req.model_dump(exclude={"description"}))


def registry() -> DeviceRegistry:
    return DeviceRegistry([Registration(device_id=f"device-{n}", owner_id=f"owner-{n}",
                                       agent_token=f"agent-{n}-" + "a" * 32,
                                       controller_token=f"control-{n}-" + "b" * 32) for n in (1, 2)])


def headers(device=1):
    return {"Authorization": f"Bearer control-{device}-" + "b" * 32}


class FakeSDK:
    def __init__(self, script=None, *, delay=0):
        self.script, self.delay = script or {}, delay
        self.calls, self.active, self.peak = [], 0, 0
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(side_effect=self.generate)), aclose=AsyncMock())

    async def generate(self, **kwargs):
        prompt = json.loads(kwargs["contents"])
        self.calls.append(prompt)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            req = GenerationRequest.model_validate(prompt["request"])
            sequence = self.script.get(req.description)
            outcome = sequence.pop(0) if sequence else GOOD
            if isinstance(outcome, BaseException):
                raise outcome
            return SimpleNamespace(text=program(req, outcome).model_dump_json())
        finally:
            self.active -= 1


class TrustedFixtureBoundary(IsolationBoundary):
    """Mock guarantee flags, but really compile/run ONLY the known fixtures.

    Never import this adapter into production or use it with live Gemini output.
    It establishes portable C plumbing, not filesystem/network/OS containment.
    """
    build_guarantees = REQUIRED_GUARANTEES
    run_guarantees = REQUIRED_GUARANTEES

    def __init__(self, *, compile_delay=0, run_delay=0):
        compiler = shutil.which("cc")
        if not compiler:
            raise RuntimeError("Host cc is required for the trusted fixture test")
        self.compiler = CCompiler("gcc", compiler, "Darwin")
        self.compile_delay, self.run_delay = compile_delay, run_delay
        self.binaries, self.directories = set(), []
        self.active, self.peak = 0, 0
        self.lock = threading.Lock()
        self.started = asyncio.Event()

    async def _thread(self, function):
        cancellation = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(function, cancellation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise

    async def compile(self, compiler, source, output, limits, link_args):
        self.started.set()
        self.directories.append(source.parent)
        def work(cancellation):
            if source.read_text() not in TRUSTED_SOURCES or link_args:
                raise AssertionError("Only exact author-written fixtures may reach a compiler")
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                cancellation.wait(self.compile_delay)
                result = self.compiler.compile_detailed(source, output, limits.compile_seconds, cancel_event=cancellation)
                if result.success:
                    self.binaries.add(hashlib.sha256(output.read_bytes()).hexdigest())
                return result
            finally:
                with self.lock:
                    self.active -= 1
        return await self._thread(work)

    async def run(self, executable, stdin, limits, capabilities):
        if stdin or capabilities:
            raise AssertionError("Fixtures do not accept user input or permissions")
        await asyncio.sleep(self.run_delay)
        def work(cancellation):
            if hashlib.sha256(executable.read_bytes()).hexdigest() not in self.binaries:
                raise AssertionError("Only a verified trusted fixture may run")
            return run_bounded([str(executable)], cwd=executable.parent, timeout=limits.run_seconds,
                               stdout_limit=limits.stdout_bytes, stderr_limit=limits.stderr_bytes,
                               cancel_event=cancellation)
        return await self._thread(work)


class RunningServer:
    async def start(self, *, sdk=None, settings=None):
        self.sdk = sdk or FakeSDK()
        settings = settings or RuntimeSettings(require_tls=False, heartbeat_interval=.04, heartbeat_timeout=.5, send_timeout=.5)
        self.runtime = AgentRuntime.create(registry(), GeminiProvider(client=self.sdk, timeout=2), settings)
        @asynccontextmanager
        async def lifespan(app):
            yield
            await self.runtime.close()
        self.app = FastAPI(lifespan=lifespan)
        self.app.state.agent_runtime = self.runtime
        self.app.include_router(router)
        ready = asyncio.Event()
        class Server(uvicorn.Server):
            async def startup(inner, sockets=None):
                await super().startup(sockets)
                ready.set()
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        self.port = self.socket.getsockname()[1]
        config = uvicorn.Config(self.app, log_level="error", ws="websockets-sansio", ws_max_size=1024 * 1024)
        self.server = Server(config)
        self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]))
        await asyncio.wait_for(ready.wait(), 5)
        self.http = httpx.AsyncClient(base_url=f"http://127.0.0.1:{self.port}", timeout=5)
        self.agent_tasks, self.sockets = [], []
        return self

    async def agent(self, service, device=1):
        websocket = await connect(f"ws://127.0.0.1:{self.port}/ws/agents/device-{device}",
                                  additional_headers={"Authorization": f"Bearer agent-{device}-" + "a" * 32})
        ready = asyncio.Event()
        task = asyncio.create_task(connected_session(websocket, f"device-{device}", service, on_ready=ready.set))
        self.sockets.append(websocket)
        self.agent_tasks.append(task)
        await asyncio.wait_for(ready.wait(), 2)
        return websocket, task

    async def generate(self, job_id, *, device=1, description="fixture"):
        return await self.http.post("/api/v1/agent-jobs/generate", headers=headers(device),
                                    json={"device_id": f"device-{device}", "job_id": job_id, "request": request(description).model_dump()})

    async def result(self, job_id, *, device=1):
        result = await self.http.get(f"/api/v1/agent-jobs/{job_id}/wait", headers=headers(device), params={"timeout": 4})
        if result.status_code != 200:
            raise AssertionError(result.text)
        return result.json()

    async def close(self):
        for websocket in self.sockets:
            await websocket.close()
        for task in self.agent_tasks:
            task.cancel()
        await asyncio.gather(*self.agent_tasks, return_exceptions=True)
        await self.http.aclose()
        self.server.should_exit = True
        await asyncio.wait_for(self.task, 5)
        self.socket.close()
