import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.routes.agent import router as agent_router
from app.agent_runtime.runtime import AgentRuntime
from app.routes.agent_jobs import router as agent_jobs_router
from app.routes.studio import router as studio_router
from app.routes.panels import router as panels_router
from app.routes.scripts import router as scripts_router
from app.routes.ws import broadcast_system_stats_loop, ws_router
from app.tunnel import start_tunnel, stop_tunnel

logger = logging.getLogger("uvicorn.error")

# ==========================================
# 1. Admin API (Port 8001 - Localhost only)
# ==========================================
admin_app = FastAPI(title="HackNC Admin Backend (Builder UI)")

admin_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

admin_app.include_router(scripts_router)


@admin_app.get("/health")
async def admin_health():
    return {"status": "ok", "service": "admin"}


# ==========================================
# 2. Public API (Port 8000 - Cloudflare Tunnel)
# ==========================================
admin_server: uvicorn.Server | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global admin_server

    # This service stays on the public server's loop; the admin server has its
    # own thread/loop and does not share asyncio connection or job objects.
    app.state.agent_runtime = await AgentRuntime.persisted()

    # Launch Admin API strictly bound to localhost:8001
    admin_host = os.getenv("ADMIN_HOST", "127.0.0.1")
    admin_port = int(os.getenv("ADMIN_PORT", "8001"))

    admin_config = uvicorn.Config(
        admin_app,
        host=admin_host,
        port=admin_port,
        log_level="info",
    )
    admin_server = uvicorn.Server(admin_config)
    admin_thread = threading.Thread(target=admin_server.run, daemon=True)
    admin_thread.start()

    # Cloudflare Tunnel for the public API (port 8000) exposes the backend to the internet, so it only
    # starts on request (JANUS_AUTO_TUNNEL=1, set by `npm start -- --tunnel`).
    if os.getenv("JANUS_AUTO_TUNNEL", "").lower() in ("1", "true", "yes"):
        try:
            await start_tunnel(8000)
        except Exception as exc:
            logger.warning(f"Could not start Cloudflare tunnel: {exc}")

    # Launch background system stats broadcaster loop
    stats_task = asyncio.create_task(broadcast_system_stats_loop())

    try:
        yield
    finally:
        stats_task.cancel()
        try:
            await stats_task
        except asyncio.CancelledError:
            pass

        await app.state.agent_runtime.close()
        await stop_tunnel()
        if admin_server is not None:
            admin_server.should_exit = True


app = FastAPI(title="HackNC Public Backend (Mobile / Tunnel)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Only mount Panels, Agent, and Mobile WebSocket routes on the public app
app.include_router(panels_router)
app.include_router(agent_router)
app.include_router(agent_jobs_router)
app.include_router(studio_router)
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "public"}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
