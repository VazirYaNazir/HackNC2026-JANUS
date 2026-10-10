from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.agent import router as agent_router
from app.routes.panels import router as panels_router
from app.routes.scripts import router as scripts_router

app = FastAPI(title="HackNC Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent_router)
app.include_router(panels_router)
app.include_router(scripts_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
