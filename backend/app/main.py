from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.websocket("/ws/agent")
async def agent_connection(websocket: WebSocket):
    await websocket.accept()

    try:
        await websocket.send_json({
            "type": "test",
            "message": "Hello from the backend!"
        })

        while True:
            message = await websocket.receive_json()
            print("Agent response:", message)

    except WebSocketDisconnect:
        print("Agent disconnected")
