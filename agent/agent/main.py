
import asyncio
import json
import os

from dotenv import load_dotenv
from websockets.asyncio.client import connect

from .executor import execute_job


load_dotenv()

SERVER_URL = os.getenv(
    "BACKEND_WS_URL",
    "ws://127.0.0.1:8000/ws/agent"
)

AGENT_TOKEN = os.environ["AGENT_TOKEN"]


async def run_agent():
    while True:
        try:
            async with connect(
                SERVER_URL,
                additional_headers={
                    "Authorization": f"Bearer {AGENT_TOKEN}"
                },
                ping_interval=20,
                ping_timeout=20,
            ) as websocket:

                print("Connected to backend")

                async for raw_message in websocket:
                    message = json.loads(raw_message)

                    if message.get("type") != "job":
                        continue

                    job_id = message.get("job_id")

                    print(f"Received job: {job_id}")

                    try:
                        # Execute on this machine
                        result = execute_job(message)

                        response = {
                            "type": "job_result",
                            "job_id": job_id,
                            "status": "completed",
                            "result": result,
                        }

                    except Exception as error:
                        response = {
                            "type": "job_result",
                            "job_id": job_id,
                            "status": "error",
                            "error": str(error),
                        }

                    await websocket.send(
                        json.dumps(response)
                    )

        except Exception as error:
            print(f"Connection lost: {error}")
            print("Reconnecting in 3 seconds...")

            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(run_agent())
