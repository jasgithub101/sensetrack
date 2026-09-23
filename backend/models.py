"""Request/response schemas.

One document per capture cycle. The Pi posts every cycle regardless of whether
detections changed, so `last_seen` for an object is simply max(ts) over events
whose object_labels contain it.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Detection(BaseModel):
    label: str
    confidence: float
    bbox: list[float] = Field(default_factory=list, description="[x, y, w, h], normalised 0-1")


class EventIn(BaseModel):
    """What the Pi sends. The image rides alongside as a multipart file."""

    device_id: str
    ts: datetime
    objects: list[Detection] = Field(default_factory=list)
    changed: bool = True
    motion_since_last: bool = False
    inference_ms: float | None = None


class EventOut(BaseModel):
    id: str
    device_id: str
    ts: datetime
    objects: list[Detection]
    object_labels: list[str]
    changed: bool
    motion_since_last: bool
    image_url: str | None = None
    inference_ms: float | None = None
    source: str = "device"


class ObjectState(BaseModel):
    """Derived from the events collection, not stored separately."""

    label: str
    last_seen: datetime
    first_seen: datetime
    sightings: int


class QueryIn(BaseModel):
    question: str


class QueryOut(BaseModel):
    answer: str
    intent: dict[str, Any]
    events_used: int
    events: list[EventOut] = Field(default_factory=list)


class Intent(BaseModel):
    """Structured form of a natural-language question.

    The LLM fills this in; Python builds the Mongo query from it. The LLM never
    writes a query directly.
    """

    query_type: Literal["last_seen", "present_during", "timeline", "unknown"] = "unknown"
    object_label: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    # False for questions unrelated to the monitored desk. Checked before the
    # answering call so general-knowledge questions get a deterministic refusal
    # rather than a confident answer from the model's own training.
    on_topic: bool = True
