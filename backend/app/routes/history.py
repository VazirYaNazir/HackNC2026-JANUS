"""Read endpoints backed by the optional Tiger Data store. Without it (or when it is unreachable) they answer
`available: false` with an empty result so the UI simply hides the feature."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.routes.agent_jobs import CONTROLLER
from app.tiger.store import METRIC_NAME, RANGES, TigerStore, TigerUnavailable, parse_range

router = APIRouter(tags=["History"])


def tiger_store(request: Request) -> TigerStore | None:
    return getattr(request.app.state, "tiger_store", None)


STORE = Annotated[TigerStore | None, Depends(tiger_store)]


@router.get("/api/metrics/history")
async def metrics_history(identity: CONTROLLER, store: STORE, metric: Annotated[str, Query(min_length=1, max_length=64)],
                          range: Annotated[str, Query()] = "1h"):
    if not METRIC_NAME.fullmatch(metric):
        raise HTTPException(422, "INVALID_METRIC")
    try:
        minutes = parse_range(range)
    except ValueError:
        raise HTTPException(422, f"INVALID_RANGE (use one of {', '.join(RANGES)})") from None
    if store is None:
        return {"available": False, "metric": metric, "range": range, "points": []}
    try:
        points = await store.metric_history(identity.device_id, metric, minutes)
    except TigerUnavailable:
        return {"available": False, "metric": metric, "range": range, "points": []}
    return {"available": True, "metric": metric, "range": range, "points": points}


@router.get("/api/activity")
async def activity(identity: CONTROLLER, store: STORE):
    if store is None:
        return {"available": False, "events": []}
    try:
        return {"available": True, "events": await store.recent_activity(identity.device_id, 50)}
    except TigerUnavailable:
        return {"available": False, "events": []}
