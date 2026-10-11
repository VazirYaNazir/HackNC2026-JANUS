"""Explicit scoped reuse suggestions; never deduplicate or replay executions."""
import json
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .llm import LLMProvider, ProviderError
from .config import Identity
from .models import GenerationRequest
from .store import SQLiteStore


def normalized(value: str) -> str:
    # Preserve case, numbers, quoted whitespace, and negations in exact matches.
    return unicodedata.normalize("NFC", value).strip()


async def suggestions(store: SQLiteStore, provider: LLMProvider, identity: Identity, request: GenerationRequest) -> dict[str, Any]:
    rows = await store.programs(identity, request)
    candidates = [{"id": row["id"], "description": json.loads(row["request_json"])["description"],
                   "summary": json.loads(row["program_json"])["summary"]} for row in rows]
    exact = [item for item in candidates if normalized(item["description"]) == normalized(request.description)]
    if exact:
        return {"matches": [{**item, "match": "exact"} for item in exact[:5]], "checked": True}
    ranked = sorted(candidates, key=lambda item: SequenceMatcher(None, normalized(request.description), normalized(item["description"])).ratio(), reverse=True)
    ranked = [item for item in ranked[:5] if SequenceMatcher(None, normalized(request.description), normalized(item["description"])).ratio() >= .3]
    if not ranked:
        return {"matches": [], "checked": True}
    try:
        ids = await provider.similar(request.description, [{"id": item["id"], "description": item["description"]} for item in ranked])
    except ProviderError:
        return {"matches": [], "checked": False}
    return {"matches": [{**item, "match": "semantic"} for item in ranked if item["id"] in ids], "checked": True}
