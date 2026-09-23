# SenseTrack

An IoT visual monitoring system. A Raspberry Pi photographs a workspace once a
minute, identifies objects on-device, and ships each observation to a backend that
builds a searchable history you can query in plain English.

> "When was my laptop last detected?" → *"Your laptop was last detected today at 2:31 PM."*

---

## How it works

```
Pi 3B                                   Laptop                        Cloud
─────                                   ──────                        ─────
60s timer
  → USB webcam / CSI capture
  → SSD MobileNet (ONNX)          ┌──> FastAPI ──────────────────> MongoDB Atlas
  → filter to COCO target classes │      │                          (events)
  → flag changed vs prev cycle    │      ├──> backend/data/images/  (JPEGs)
  → POST every cycle ─────────────┘      │
  → (optional PIR metadata)              ├──> Web dashboard
  → SQLite outbox if offline             │
                                         └──> Groq API ──────────> qwen3.8-27b
```

---

## Setup

### 1. Backend (your laptop)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt

cp .env.example .env            # then fill in MONGODB_URI and GROQ_API_KEY
```

- **MongoDB Atlas** — create a free M0 cluster, add a database user, and under
  *Network Access* allow your current IP (or `0.0.0.0/0` for a demo). Copy the
  driver connection string into `MONGODB_URI`.
- **Groq** — get a free key at <https://console.groq.com/keys>.

Start it:

```bash
python -m backend.main
```

It prints the LAN address the Pi should post to. Open the dashboard at that URL.

### 2. Seed some history

A 1–2 hour demo has no "yesterday" to talk about, so backfill a few days:

```bash
python -m scripts.seed_fake_events --days 3
```

Seeded events are tagged `source: "seed"` and shown as *seeded* in the UI, so
they're never confused with real captures. Remove them with `--clear`.

### 3. Raspberry Pi

Install the Python libraries from **apt**, not pip. On a Pi 3B pip will compile
anything lacking a wheel for your Python version, which takes hours and can trip
undervoltage:

```bash
sudo apt install -y python3-numpy python3-pil python3-requests python3-yaml python3-opencv python3-picamera2 python3-rpi.gpio python3-venv
```

Create the venv so it can see those system packages, then add the one library
Debian doesn't ship:

```bash
cd ~/SenseTrack && python3 -m venv --system-site-packages .venv && source .venv/bin/activate
```

```bash
pip install --only-binary=:all: onnxruntime
```

> **Inference runtime — read this before choosing.** On a Pi 3B running
> Debian 13 / Python 3.13, **both TFLite runtimes are unusable**:
> `tflite-runtime` publishes no wheels past Python 3.11, and `ai-edge-litert`'s
> aarch64 wheel dies with **SIGBUS** on the Cortex-A53. Use the ONNX model with
> `onnxruntime`, which is the default. `pi/detector.py` picks its backend from
> the model file's extension, so TFLite still works on a laptop or a Pi 4/5 —
> fetch it with `python -m pi.download_model --tflite` and point
> `detection.model` at the `.tflite` file.

```bash
python -m pi.download_model     # fetches the ONNX model + COCO labels
```

Edit `pi/config.yaml` — set `backend.hostname` to your laptop's hostname (so
mDNS can find it as `<hostname>.local`), or set `backend.url` explicitly.

```bash
python -m pi.main --once        # single cycle, to check wiring
python -m pi.main               # the real loop
```

Install `pi/sensetrack.service` to have it start on boot.

**Camera:** a USB webcam (`backend: webcam`) or the CSI ribbon camera
(`backend: picamera`) both work — see `capture.backend` in `pi/config.yaml`.
Check the device is present with `ls /dev/video*`.

**Optional PIR wiring:** VCC → 5V (pin 2), GND → GND (pin 6), OUT → GPIO4
(pin 7). Disabled by default; see the design note below.

---

## Design notes

### Every cycle is transmitted — no dedupe suppression

The original spec suppressed transmission when detections hadn't changed. Taken
literally that breaks its own headline query: if a laptop sits on the desk from
9am to 5pm, only the 9am *change* event exists, so "when was my laptop last
detected?" answers 9am — wrong by eight hours.

Instead the Pi sends an event every cycle, and `last_seen` is just `max(ts)` over
events containing that label. The comparison against the previous cycle is still
computed and stored as `changed`, which drives the UI's transition highlighting
and demonstrates the dedupe concept — it just never suppresses data.

This is affordable because the system runs for demo sessions, not continuously:
~60–120 events and ~12MB of images per session.

### Capture is periodic; the PIR is optional and off

The source document specified motion-triggered capture in one section and
60-second periodic capture in another. Periodic won: a still desk with a laptop
on it is exactly the state you want recorded, and PIR detects *motion*, not
*presence*.

Because the sensor could then only ever contribute metadata, it is disabled
(`pir.enabled: false`) and this build ships without one — events report
`motion_since_last: false`. The support code in `pi/pir.py` remains: set
`enabled: true` and wire OUT to GPIO4 to have motion latched between cycles and
attached to each event.

*If you keep this arrangement, drop the PIR row from the components table in
your report so the document matches what was actually built.*

### The LLM never writes a query

`question → [LLM] → Intent → [Python/Mongo] → events → [LLM] → answer`

The model fills in a validated `Intent` schema; Python builds the Mongo query
from it. Model output never reaches the database, and every answer is traceable
to rows returned in the same API response. The dashboard shows the interpreted
intent and record count beneath each answer.

### Store-and-forward

Every event is written to a local SQLite outbox before being sent, and is only
deleted once the backend confirms receipt. Moving between networks, a sleeping
laptop, or a dropped connection costs delay, not data. The Pi re-resolves the
backend address when the queue starts backing up.

---

## IoT concepts demonstrated

| Concept | Where |
|---|---|
| Sensing | `pi/camera.py` (USB or CSI); optional `pi/pir.py` |
| Edge computing | `pi/detector.py` — ONNX inference on-device (~1.9s/frame on a Pi 3B) |
| Event-driven acquisition | `pi/main.py` — structured events, `changed` flag |
| Wireless communication | `pi/main.py:send` — REST over Wi-Fi |
| Device-to-server protocol | `backend/routes/events.py` — multipart JSON + JPEG |
| Data storage | MongoDB Atlas + local image store |
| Remote monitoring | `web/` dashboard, reachable from any LAN device |
| Resilience | `pi/outbox.py`, `pi/discovery.py` |

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/events` | Ingest one cycle (multipart: JSON + optional JPEG) |
| `GET` | `/api/events` | Filter by `label`, `start`, `end`, `changed_only`, `limit` |
| `GET` | `/api/events/latest` | Most recent event |
| `GET` | `/api/objects` | Per-object first/last sighting and count |
| `GET` | `/api/objects/{label}/last` | Last sighting of one object |
| `POST` | `/api/query` | Natural-language question |
| `GET` | `/health` | Status and event count |

Interactive docs at `/docs`.

---

## Troubleshooting

Run `python -m scripts.check_setup` first — it tests Atlas and Groq separately
and names the fix for the common failures.

**`ReplicaSetNoPrimary` / Atlas connection times out.**
Many college, corporate and public networks allow only ports 80 and 443, and
MongoDB uses **27017**. If DNS resolves but the connection times out, this is
almost certainly the cause rather than an allowlist problem. Confirm with:

```python
import socket; socket.create_connection(("portquiz.net", 27017), timeout=8)
```

That host accepts connections on any port, so a timeout there means your network
is blocking the port. Fixes: connect a VPN, use a phone hotspot, or run MongoDB
locally (`MONGODB_URI=mongodb://localhost:27017` — no other code changes).

*If you rely on a VPN, it has to be running during the demo too.*

**Groq: `model does not exist or you do not have access to it`.**
Groq rotates its catalogue often, so `GROQ_MODEL` goes stale. List what your key
can reach:

```python
from groq import Groq; print([m.id for m in Groq(api_key="gsk_…").models.list().data])
```

The model must support JSON mode for intent extraction — not all of them do.

**Pi can't reach the laptop.**
1. Confirm both are on the same network, then `curl http://<laptop-ip>:8000/health` from the Pi.
2. **Windows Firewall** blocks inbound connections on networks marked *Public*.
   Set the network to Private, or add a rule:
   ```powershell
   New-NetFirewallRule -DisplayName "SenseTrack" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
   ```
3. **Client isolation.** Many campus and public networks block device-to-device
   traffic entirely — no code change fixes this. Use a phone hotspot instead.
   *Test this on the actual demo network before you depend on it.*

**`No TFLite runtime available`, or a SIGBUS / "Bus error" crash on import.**
Use the ONNX model instead — it is the default. `tflite-runtime` has no wheels
beyond Python 3.11, and `ai-edge-litert` crashes on the Pi 3B's Cortex-A53.

**Pi reboots on its own, or `vcgencmd get_throttled` is not `0x0`.** Undervoltage.
A Pi 3B needs a real 5V 2.5A supply, and a USB webcam draws 200-500mA more on
top. Use a powered USB hub for the webcam, or a stronger supply and a short,
thick cable. Repeated undervoltage can corrupt the SD card.

**Detections are poor** — lower `detection.confidence_threshold` in
`pi/config.yaml`, and check lighting. The model is a small quantized one; it is
reliable on laptops and bottles, less so on small or partly hidden objects.

**Note on object classes:** the model recognises COCO's 80 classes. `laptop`,
`cell phone`, `bottle`, `book`, `keyboard`, `mouse` and `cup` are the
desk-relevant ones. **Headphones has no COCO class** and cannot be detected
without training a custom model; `book` stands in for a notebook.
