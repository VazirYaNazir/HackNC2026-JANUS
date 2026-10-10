
import asyncio
import json
from websockets.asyncio.client import connect

async def main():
    uri = "ws://localhost:8000/ws/agent"

    async with connect(uri) as websocket:
        print("Connected to cloud backend")

        async for message in websocket:
            data = json.loads(message)
            print("Received:", data)

            await websocket.send(json.dumps({
                "type": "acknowledgment",
                "status": "received"
            }))

asyncio.run(main())
