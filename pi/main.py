"""SenseTrack edge loop.

Every 60 seconds: capture -> detect -> build event -> queue -> flush queue.

An event is produced on every cycle regardless of whether detections changed.
The comparison against the previous cycle is still computed and sent as
`changed`, which drives the UI's transition highlighting, but it never
suppresses a transmission — suppressing would make "when was X last seen?"
report the time X *arrived* rather than the last time it was actually there.

    python -m pi.main
    python -m pi.main --once      # single cycle, useful for testing
"""

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from . import discovery
from .camera import Camera, CameraError
from .detector import Detector
from .outbox import Outbox
from .pir import PirSensor

HERE = Path(__file__).parent
log = logging.getLogger("sensetrack")

_running = True


def _handle_signal(*_):
    global _running
    _running = False
    log.info("Stopping after this cycle…")


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def send(base_url: str, payload: dict, image: bytes | None, timeout: float) -> bool:
    """POST one event. Returns True only on a 2xx."""
    files = {"event": (None, json.dumps(payload), "application/json")}
    if image:
        files["image"] = ("capture.jpg", image, "image/jpeg")

    try:
        response = requests.post(f"{base_url}/api/events", files=files, timeout=timeout)
        if response.ok:
            return True
        log.warning("backend rejected event: %s %s", response.status_code, response.text[:200])
        return False
    except requests.RequestException as exc:
        log.warning("send failed: %s", exc)
        return False


def flush(outbox: Outbox, base_url: str | None, timeout: float) -> None:
    """Drain queued events oldest-first. Stops at the first failure so ordering
    is preserved and we don't hammer an unreachable backend."""
    if base_url is None:
        return

    sent = 0
    for row_id, payload, image in outbox.pending():
        if send(base_url, payload, image, timeout):
            outbox.mark_sent(row_id)
            sent += 1
        else:
            outbox.mark_failed(row_id)
            break

    if sent:
        log.info("Flushed %d queued event(s); %d remaining", sent, outbox.count())


def run(config: dict, once: bool) -> int:
    capture_cfg = config["capture"]
    detect_cfg = config["detection"]
    backend_cfg = config["backend"]
    timeout = backend_cfg.get("timeout_seconds", 15)

    try:
        camera = Camera(
            capture_cfg["width"],
            capture_cfg["height"],
            backend=capture_cfg.get("backend", "auto"),
            index=capture_cfg.get("webcam_index", 0),
        )
    except CameraError as exc:
        log.error("%s", exc)
        return 1

    try:
        detector = Detector(
            model_path=HERE / detect_cfg["model"],
            labels_path=HERE / detect_cfg["labels"],
            threshold=detect_cfg["confidence_threshold"],
            target_labels=detect_cfg.get("target_labels"),
        )
    except (FileNotFoundError, RuntimeError) as exc:
        log.error("%s", exc)
        camera.close()
        return 1

    pir_cfg = config.get("pir", {})
    pir = PirSensor(pir_cfg["gpio_pin"]) if pir_cfg.get("enabled") else None

    outbox = Outbox(HERE / "outbox.db")
    if outbox.count():
        log.info("%d event(s) already queued from a previous run", outbox.count())

    base_url = discovery.resolve(backend_cfg)
    if base_url is None:
        log.warning("Backend not reachable — events will queue locally until it is")

    interval = capture_cfg["interval_seconds"]
    previous_labels: list[str] | None = None

    while _running:
        cycle_started = time.monotonic()

        try:
            jpeg = camera.capture_jpeg()
            detections, inference_ms = detector.detect(jpeg)
        except Exception as exc:
            log.error("capture/detect failed: %s", exc)
            if once:
                break
            time.sleep(interval)
            continue

        labels = sorted({d.label for d in detections})
        changed = labels != previous_labels
        previous_labels = labels

        payload = {
            "device_id": config["device_id"],
            "ts": datetime.now(timezone.utc).isoformat(),
            "objects": [d.to_dict() for d in detections],
            "changed": changed,
            "motion_since_last": pir.consume() if pir else False,
            "inference_ms": round(inference_ms, 1),
        }

        log.info(
            "%s  %s%s  (%.0f ms)",
            datetime.now().strftime("%H:%M:%S"),
            ", ".join(labels) if labels else "nothing detected",
            "  [changed]" if changed else "",
            inference_ms,
        )

        if capture_cfg.get("save_local_copy"):
            local = HERE / "captures" / f"{datetime.now():%Y%m%d_%H%M%S}.jpg"
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(jpeg)

        outbox.add(payload, jpeg)

        # Re-resolve if we lost the backend — covers moving to another network.
        if base_url is None or outbox.count() > 3:
            base_url = discovery.resolve(backend_cfg) or base_url
        flush(outbox, base_url, timeout)

        if once:
            break

        # Subtract the work we just did so the cadence stays at `interval`.
        remaining = interval - (time.monotonic() - cycle_started)
        while remaining > 0 and _running:
            time.sleep(min(1.0, remaining))
            remaining -= 1.0

    camera.close()
    if pir:
        pir.close()
    outbox.close()
    log.info("Stopped.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SenseTrack edge capture loop")
    parser.add_argument("--config", type=Path, default=HERE / "config.yaml")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    parser.add_argument(
        "--backend",
        help="override backend.url, e.g. http://10.4.20.28:8000 (skips discovery)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    config = load_config(args.config)
    if args.backend:
        config["backend"]["url"] = args.backend.rstrip("/")

    return run(config, args.once)


if __name__ == "__main__":
    sys.exit(main())
