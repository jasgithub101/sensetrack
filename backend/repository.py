"""All Mongo reads/writes live here.

Deliberately the only module that builds queries: the LLM layer produces a
validated Intent, and these functions turn that into Mongo. Nothing
model-generated reaches the database.
"""

from datetime import datetime
from typing import Any

from .db import as_utc, get_db
from .models import EventOut, ObjectState


def _to_out(doc: dict[str, Any]) -> EventOut:
    image_path = doc.get("image_path")
    return EventOut(
        id=str(doc["_id"]),
        device_id=doc["device_id"],
        ts=as_utc(doc["ts"]),
        objects=doc.get("objects", []),
        object_labels=doc.get("object_labels", []),
        changed=doc.get("changed", False),
        motion_since_last=doc.get("motion_since_last", False),
        image_url=f"/images/{image_path}" if image_path else None,
        inference_ms=doc.get("inference_ms"),
        source=doc.get("source", "device"),
    )


async def insert_event(doc: dict[str, Any]) -> str:
    result = await get_db()["events"].insert_one(doc)
    return str(result.inserted_id)


async def list_events(
    *,
    label: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    changed_only: bool = False,
    limit: int = 100,
) -> list[EventOut]:
    query: dict[str, Any] = {}
    if label:
        query["object_labels"] = label
    if changed_only:
        query["changed"] = True
    if start or end:
        window: dict[str, datetime] = {}
        if start:
            window["$gte"] = start
        if end:
            window["$lte"] = end
        query["ts"] = window

    cursor = get_db()["events"].find(query).sort("ts", -1).limit(limit)
    return [_to_out(doc) async for doc in cursor]


async def last_seen(label: str) -> EventOut | None:
    doc = await get_db()["events"].find_one({"object_labels": label}, sort=[("ts", -1)])
    return _to_out(doc) if doc else None


async def object_summary(
    start: datetime | None = None, end: datetime | None = None
) -> list[ObjectState]:
    """Per-object first/last sighting and count, optionally within a window."""
    match: dict[str, Any] = {}
    if start or end:
        window: dict[str, datetime] = {}
        if start:
            window["$gte"] = start
        if end:
            window["$lte"] = end
        match["ts"] = window

    pipeline: list[dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline += [
        {"$unwind": "$object_labels"},
        {
            "$group": {
                "_id": "$object_labels",
                "last_seen": {"$max": "$ts"},
                "first_seen": {"$min": "$ts"},
                "sightings": {"$sum": 1},
            }
        },
        {"$sort": {"last_seen": -1}},
    ]

    return [
        ObjectState(
            label=doc["_id"],
            last_seen=as_utc(doc["last_seen"]),
            first_seen=as_utc(doc["first_seen"]),
            sightings=doc["sightings"],
        )
        async for doc in get_db()["events"].aggregate(pipeline)
    ]


async def latest_event() -> EventOut | None:
    doc = await get_db()["events"].find_one({}, sort=[("ts", -1)])
    return _to_out(doc) if doc else None


async def count_events() -> int:
    return await get_db()["events"].count_documents({})
