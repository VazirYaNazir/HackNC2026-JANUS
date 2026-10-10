
from fastapi import FastAPI

app = FastAPI(
    title="HackNC2026 API",
    version="0.1.0"
)

@app.get("/")
async def root():
    return {
        "application": "HackNC2026",
        "status": "running"
    }

@app.get("/health")
async def health():
    return {"status": "ok"}
