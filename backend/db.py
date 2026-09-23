"""MongoDB Atlas access.

Single collection: `events`. Indexes are created at startup, which is idempotent
in MongoDB, so there's no separate migration step.
"""

from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from .config import settings

_client: AsyncIOMotorClient | None = None


def get_db() -> AsyncIOMotorDatabase:
    if _client is None:
        raise RuntimeError("connect() has not run yet")
    return _client[settings.mongodb_db]


async def connect() -> None:
    global _client
    _client = AsyncIOMotorClient(settings.mongodb_uri, serverSelectionTimeoutMS=8000)
    await _client.admin.command("ping")

    events = get_db()["events"]
    await events.create_index([("ts", DESCENDING)], name="ts_desc")
    await events.create_index(
        [("object_labels", ASCENDING), ("ts", DESCENDING)], name="labels_ts"
    )


async def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Mongo returns naive datetimes in UTC; make comparisons safe either way."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
