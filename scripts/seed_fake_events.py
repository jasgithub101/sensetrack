"""Backfill plausible desk history.

A 1-2 hour demo can't answer "what was here yesterday?", so this generates the
preceding days at the real 60-second cadence. Seeded documents are tagged
source="seed" — they are clearly distinguishable from captured data and can be
removed with --clear.

    python -m scripts.seed_fake_events --days 3
    python -m scripts.seed_fake_events --clear
"""

import argparse
import asyncio
import random
from datetime import datetime, timedelta, timezone

from backend import db

DEVICE_ID = "pi-desk-01"
INTERVAL = timedelta(minutes=1)

# label -> (hour_start, hour_end, probability of being present within that window)
SCHEDULE = {
    "laptop": (9, 18, 0.95),
    "keyboard": (9, 18, 0.9),
    "mouse": (9, 18, 0.85),
    "cup": (9, 12, 0.6),
    "bottle": (13, 20, 0.7),
    "cell phone": (9, 22, 0.45),
    "book": (14, 19, 0.3),
}


def _labels_at(when: datetime, rng: random.Random) -> list[str]:
    """Objects present at a given minute. Within-window randomness gives the
    appearing/disappearing behaviour that makes queries interesting."""
    present = []
    for label, (start_h, end_h, probability) in SCHEDULE.items():
        if start_h <= when.hour < end_h and rng.random() < probability:
            present.append(label)
    return sorted(present)


def _build(days: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    midnight_today = now.replace(hour=0, minute=0)

    docs: list[dict] = []
    previous: list[str] | None = None

    when = midnight_today - timedelta(days=days)
    while when < now:
        labels = _labels_at(when, rng)
        docs.append(
            {
                "device_id": DEVICE_ID,
                "ts": when,
                "objects": [
                    {"label": lbl, "confidence": round(rng.uniform(0.62, 0.96), 2), "bbox": []}
                    for lbl in labels
                ],
                "object_labels": labels,
                "changed": labels != previous,
                "motion_since_last": bool(labels) and rng.random() < 0.4,
                "inference_ms": round(rng.uniform(950, 1900), 1),
                "image_path": None,  # No real capture exists for seeded history.
                "source": "seed",
            }
        )
        previous = labels
        when += INTERVAL

    return docs


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed SenseTrack history")
    parser.add_argument("--days", type=int, default=3, help="days of history to generate")
    parser.add_argument("--clear", action="store_true", help="remove seeded events and exit")
    parser.add_argument("--seed", type=int, default=7, help="RNG seed for reproducibility")
    args = parser.parse_args()

    await db.connect()
    events = db.get_db()["events"]
    try:
        if args.clear:
            result = await events.delete_many({"source": "seed"})
            print(f"Removed {result.deleted_count} seeded events.")
            return

        # Re-seeding replaces rather than duplicates.
        removed = (await events.delete_many({"source": "seed"})).deleted_count
        if removed:
            print(f"Replaced {removed} existing seeded events.")

        docs = _build(args.days, args.seed)
        await events.insert_many(docs)
        print(f"Inserted {len(docs)} seeded events spanning {args.days} day(s).")
        print(f"Range: {docs[0]['ts']:%Y-%m-%d %H:%M} to {docs[-1]['ts']:%Y-%m-%d %H:%M} UTC")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
