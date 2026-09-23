"""SenseTrack backend.

Run from the project root:  python -m backend.main
"""

import socket
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import db, repository
from .config import IMAGE_DIR, PROJECT_ROOT, settings
from .routes import events, objects, query


@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.connect()
    yield
    await db.close()


app = FastAPI(title="SenseTrack", version="1.0.0", lifespan=lifespan)

# The UI is served from the same origin, but the Pi and ad-hoc tools may not be.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(events.router)
app.include_router(objects.router)
app.include_router(query.router)


@app.get("/health")
async def health():
    return {"status": "ok", "events": await repository.count_events()}


# Mounts go last: a mount at "/" matches every remaining path, so any route
# declared after it would be unreachable.
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")
app.mount("/", StaticFiles(directory=PROJECT_ROOT / "web", html=True), name="web")


def _lan_ip() -> str:
    """Best-effort LAN address to print at startup, so you can point the Pi at it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # No packets sent; just picks the outbound interface.
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


if __name__ == "__main__":
    import uvicorn

    ip = _lan_ip()
    print(f"\n  SenseTrack backend")
    print(f"  Dashboard : http://{ip}:{settings.port}/")
    print(f"  Pi should post to: http://{ip}:{settings.port}")
    print(f"  (or http://{socket.gethostname()}.local:{settings.port} via mDNS)\n")

    uvicorn.run("backend.main:app", host=settings.host, port=settings.port, reload=False)
