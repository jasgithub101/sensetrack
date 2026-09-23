"""Groq-backed natural-language layer.

Two calls with deterministic Mongo work in between:

  question -> [LLM] -> Intent -> [Python/Mongo] -> events -> [LLM] -> answer

The model never writes a query. It only fills in a validated Intent, and only
ever sees rows we retrieved, so answers stay grounded in the database.
"""

import json
from datetime import datetime, timedelta

from groq import AsyncGroq

from .config import settings
from .models import EventOut, Intent, ObjectState

_client: AsyncGroq | None = None

# COCO labels that plausibly appear on a desk. Given to the model so it maps
# "my laptop" -> "laptop" and "my phone" -> "cell phone" rather than inventing labels.
DESK_LABELS = [
    "laptop",
    "cell phone",
    "bottle",
    "book",
    "keyboard",
    "mouse",
    "cup",
    "remote",
    "scissors",
    "backpack",
    "chair",
    "tv",
    "person",
]

_INTENT_PROMPT = """You convert questions about a monitored desk into JSON.

Valid object labels (COCO). Map everyday words onto these exactly:
{labels}

"phone"/"mobile" -> "cell phone".  "notebook"/"notepad" -> "book".
If the object has no match in the list, use null.

Current local time (with UTC offset): {now}
Emit start/end in this same local timezone, including the offset.

Return ONLY a JSON object:
{{
  "query_type": "last_seen" | "present_during" | "timeline" | "unknown",
  "object_label": <label or null>,
  "start": <ISO 8601 with local offset, or null>,
  "end": <ISO 8601 with local offset, or null>,
  "on_topic": true | false
}}

query_type meanings:
- last_seen: when a specific object was most recently detected
- present_during: what objects were around in a time window
- timeline: what happened / changed over a period
- unknown: about the desk, but not answerable from object-detection history

on_topic is false ONLY when the question has nothing to do with the monitored
desk, its objects, or its history — e.g. "what is the capital of France", "write
me a poem". A question asking about an object that was never detected (e.g. "was
there a giraffe on my desk?") IS on topic: it is answerable as "no record".

Resolve relative dates against the current local time above.
"yesterday" = the full 00:00-23:59 local time of the previous day.
"today" = 00:00 local today to now."""

_ANSWER_PROMPT = """You answer questions about a desk monitored by a camera that \
runs object detection every 60 seconds.

Use ONLY the data provided. Never invent a timestamp or an object. If the data is \
empty, say plainly that there is no record of it.

Where an aggregate is given, "minutes_present" is how many one-minute samples \
that object appeared in over the period — treat it as a rough measure of how \
long it was there, not an exact duration.

Be brief: one or two sentences. Timestamps you are given are already in the
user's local timezone; render them as-is, never restating the offset. Give
times in a readable form (e.g. "today at \
2:31 PM"). Do not mention JSON, databases, time zones, or that you were given context."""


def _get_client() -> AsyncGroq:
    global _client
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set in .env")
    if _client is None:
        _client = AsyncGroq(api_key=settings.groq_api_key)
    return _client


async def extract_intent(question: str) -> Intent:
    """Question -> structured Intent. Falls back to a keyword guess if the model misbehaves."""
    # Local time, not UTC: "yesterday" means the user's yesterday. Events are
    # stored in UTC; tz-aware bounds are converted back by the Mongo driver.
    now = datetime.now().astimezone()
    response = await _get_client().chat.completions.create(
        model=settings.groq_model,
        messages=[
            {
                "role": "system",
                "content": _INTENT_PROMPT.format(
                    labels=", ".join(DESK_LABELS), now=now.isoformat()
                ),
            },
            {"role": "user", "content": question},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    try:
        return Intent.model_validate_json(response.choices[0].message.content or "{}")
    except ValueError:
        return _fallback_intent(question, now)


def _fallback_intent(question: str, now: datetime) -> Intent:
    """Keyword heuristic, used only if the model returns something unparseable."""
    lowered = question.lower()

    label = next((lbl for lbl in DESK_LABELS if lbl in lowered), None)
    if label is None and ("phone" in lowered or "mobile" in lowered):
        label = "cell phone"

    if "yesterday" in lowered:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return Intent(
            query_type="present_during",
            object_label=label,
            start=midnight - timedelta(days=1),
            end=midnight - timedelta(microseconds=1),
        )

    if label:
        return Intent(query_type="last_seen", object_label=label)
    return Intent(query_type="timeline", start=now - timedelta(hours=24), end=now)


def _serialise(events: list[EventOut]) -> str:
    """Compact rows for the model. Bounding boxes and ids are noise here."""
    return json.dumps(
        [
            {
                "time": e.ts.astimezone().isoformat(),
                "objects": e.object_labels,
                "changed": e.changed,
            }
            for e in events
        ]
    )


def _serialise_summary(summary: list[ObjectState]) -> str:
    return json.dumps(
        [
            {
                "object": s.label,
                "first_seen": s.first_seen.astimezone().isoformat(),
                "last_seen": s.last_seen.astimezone().isoformat(),
                "minutes_present": s.sightings,
            }
            for s in summary
        ]
    )


async def render_answer(
    question: str,
    intent: Intent,
    events: list[EventOut],
    summary: list[ObjectState] | None = None,
) -> str:
    parts = [f"Question: {question}", f"Interpreted as: {intent.model_dump_json()}"]

    # An aggregate over the whole window, when we have one, is the honest answer
    # to "what was present during X" — a capped list of raw rows would only
    # cover a slice of the window and misrepresent it.
    if summary is not None:
        parts.append(
            f"Aggregated objects over the requested period "
            f"({len(summary)} distinct):\n{_serialise_summary(summary)}"
        )
    if events:
        parts.append(f"Individual records ({len(events)}):\n{_serialise(events)}")
    if summary is None and not events:
        parts.append("No matching records.")

    context = "\n".join(parts)

    response = await _get_client().chat.completions.create(
        model=settings.groq_model,
        messages=[
            {"role": "system", "content": _ANSWER_PROMPT},
            {"role": "user", "content": context},
        ],
        temperature=0.2,
        max_tokens=200,
    )
    return (response.choices[0].message.content or "").strip()
