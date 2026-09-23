"""Natural-language query endpoint.

Orchestrates: intent extraction -> deterministic retrieval -> answer rendering.
The retrieval step in the middle is ordinary Mongo, so every answer is traceable
to rows you can inspect (they come back in the response as `events`).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from .. import llm, repository
from ..models import EventOut, Intent, ObjectState, QueryIn, QueryOut

router = APIRouter(prefix="/api", tags=["query"])

# Enough rows to answer from, small enough to stay well inside the context window.
MAX_CONTEXT_EVENTS = 60


async def _retrieve(intent: Intent) -> tuple[list[EventOut], list[ObjectState] | None]:
    """Turn a validated Intent into a Mongo read. No model output reaches the query.

    Returns (events, summary). For window questions the summary is the real
    answer: a capped list of raw rows only covers the newest slice of the window
    (60 rows out of a 1440-row day is just the last hour), which would silently
    misrepresent the period. The aggregate spans all of it.
    """
    if intent.query_type == "last_seen" and intent.object_label:
        event = await repository.last_seen(intent.object_label)
        return ([event] if event else []), None

    if intent.query_type in ("present_during", "timeline"):
        start, end = intent.start, intent.end
        if start is None and end is None:
            end = datetime.now(timezone.utc)
            start = end - timedelta(hours=24)

        summary = await repository.object_summary(start=start, end=end)

        # A timeline also wants the transitions themselves, not just totals.
        events: list[EventOut] = []
        if intent.query_type == "timeline":
            events = await repository.list_events(
                label=intent.object_label,
                start=start,
                end=end,
                changed_only=True,
                limit=MAX_CONTEXT_EVENTS,
            )
        return events, summary

    # On-topic but unclassifiable: hand over recent activity plus a 24h aggregate
    # so the model can answer "was X ever here?" truthfully.
    end = datetime.now(timezone.utc)
    return (
        await repository.list_events(limit=10),
        await repository.object_summary(start=end - timedelta(hours=24), end=end),
    )


@router.post("/query", response_model=QueryOut)
async def ask(payload: QueryIn):
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question is empty")

    try:
        intent = await llm.extract_intent(question)

        # Refuse off-topic questions deterministically. Relying on the answering
        # prompt to decline is unreliable — the model will happily answer
        # "what is the capital of France?" from its own training instead.
        if not intent.on_topic:
            return QueryOut(
                answer=(
                    "I can only answer questions about objects observed at this desk: "
                    "for example, when something was last detected, or what was "
                    "present during a given period."
                ),
                intent=intent.model_dump(mode="json"),
                events_used=0,
            )

        events, summary = await _retrieve(intent)
        answer = await llm.render_answer(question, intent, events, summary)
        # Window queries answer from the aggregate and carry no raw rows, so
        # count whichever actually fed the model.
        records_used = len(events) if events else len(summary or [])
    except RuntimeError as exc:  # missing API key
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # Groq unreachable, rate limited, etc.
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc

    return QueryOut(
        answer=answer,
        intent=intent.model_dump(mode="json"),
        events_used=records_used,
        events=events[:10],
    )
