"""Event ingest and retrieval.

The Pi posts multipart: a JSON part describing the cycle plus an optional JPEG.
Images go to disk (Atlas M0 is 512MB and must hold documents only); the document
keeps a relative path.
"""

from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from .. import repository
from ..config import IMAGE_DIR
from ..models import EventIn, EventOut

router = APIRouter(prefix="/api", tags=["events"])


def _save_image(image: UploadFile, ts: datetime) -> str:
    """Write the JPEG under images/YYYY-MM-DD/HHMMSS.jpg, return the relative path."""
    day = ts.strftime("%Y-%m-%d")
    (IMAGE_DIR / day).mkdir(parents=True, exist_ok=True)

    # Microseconds keep the name unique if two cycles land in the same second.
    name = f"{ts.strftime('%H%M%S')}_{ts.microsecond // 1000:03d}.jpg"
    (IMAGE_DIR / day / name).write_bytes(image.file.read())
    return f"{day}/{name}"


@router.post("/events", response_model=dict)
async def create_event(
    event: str = Form(..., description="EventIn as a JSON string"),
    image: UploadFile | None = File(None),
):
    try:
        parsed = EventIn.model_validate_json(event)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid event payload: {exc}") from exc

    doc = {
        "device_id": parsed.device_id,
        "ts": parsed.ts,
        "objects": [d.model_dump() for d in parsed.objects],
        # Denormalised so the compound index can serve label+time queries directly.
        "object_labels": sorted({d.label for d in parsed.objects}),
        "changed": parsed.changed,
        "motion_since_last": parsed.motion_since_last,
        "inference_ms": parsed.inference_ms,
        "image_path": _save_image(image, parsed.ts) if image else None,
        "source": "device",
    }

    return {"id": await repository.insert_event(doc)}


@router.get("/events", response_model=list[EventOut])
async def get_events(
    label: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    changed_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
):
    return await repository.list_events(
        label=label, start=start, end=end, changed_only=changed_only, limit=limit
    )


@router.get("/events/latest", response_model=EventOut | None)
async def get_latest():
    return await repository.latest_event()
