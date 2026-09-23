"""Structured object queries — the non-LLM path.

Worth keeping distinct from /api/query: these are exact, and the web UI uses them
for filtering. The LLM route is a convenience layer on top of the same data.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException

from .. import repository
from ..models import EventOut, ObjectState

router = APIRouter(prefix="/api/objects", tags=["objects"])


@router.get("", response_model=list[ObjectState])
async def list_objects(start: datetime | None = None, end: datetime | None = None):
    return await repository.object_summary(start=start, end=end)


@router.get("/{label}/last", response_model=EventOut)
async def get_last_seen(label: str):
    event = await repository.last_seen(label)
    if event is None:
        raise HTTPException(status_code=404, detail=f"no record of '{label}'")
    return event
