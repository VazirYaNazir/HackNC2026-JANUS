"""`python -m app.tiger.check`: prints one JSON line {configured, reachable, schema, missing} for `npm run doctor`."""
import asyncio
import json

from .connection import tiger_url
from .store import TigerStore


async def report() -> dict:
    url = tiger_url()
    if not url:
        return {"configured": False}
    return {"configured": True, **await TigerStore(url).check()}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(report())))
