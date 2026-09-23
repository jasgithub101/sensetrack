"""Finding the backend when the laptop's IP changes per network.

Resolution order:
  1. Explicit `backend.url` from config
  2. mDNS: http://<configured hostname>.local:<port>
  3. Last address that worked, cached on disk

Each candidate is probed with GET /health before being accepted.
"""

import logging
import socket
from pathlib import Path

import requests

log = logging.getLogger(__name__)

CACHE_FILE = Path(__file__).parent / ".backend_cache"


def _healthy(base_url: str, timeout: float = 3.0) -> bool:
    try:
        response = requests.get(f"{base_url}/health", timeout=timeout)
        return response.ok
    except requests.RequestException:
        return False


def _cached() -> str | None:
    if CACHE_FILE.exists():
        value = CACHE_FILE.read_text(encoding="utf-8").strip()
        return value or None
    return None


def _remember(base_url: str) -> None:
    try:
        CACHE_FILE.write_text(base_url, encoding="utf-8")
    except OSError as exc:
        log.warning("could not cache backend address: %s", exc)


def resolve(config: dict) -> str | None:
    """Returns a reachable base URL, or None if nothing answers."""
    port = config.get("port", 8000)
    candidates: list[str] = []

    if config.get("url"):
        candidates.append(str(config["url"]).rstrip("/"))

    hostname = config.get("hostname")
    if hostname:
        # Resolve .local first so a stale mDNS entry fails fast rather than
        # blocking on an HTTP timeout.
        mdns = f"{hostname}.local"
        try:
            ip = socket.gethostbyname(mdns)
            candidates.append(f"http://{ip}:{port}")
        except socket.gaierror:
            log.debug("mDNS lookup failed for %s", mdns)

    cached = _cached()
    if cached:
        candidates.append(cached)

    for candidate in dict.fromkeys(candidates):  # de-dupe, preserve order
        if _healthy(candidate):
            log.info("Backend at %s", candidate)
            _remember(candidate)
            return candidate
        log.debug("no response from %s", candidate)

    return None
