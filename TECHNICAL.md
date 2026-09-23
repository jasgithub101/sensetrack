# SenseTrack — Technical Report

A reference for defending the project: what was built, why each decision was
made, what deviates from the original proposal, what was measured, and where the
limits are.

---

## 1. What the system does

A Raspberry Pi photographs a workspace once a minute and runs an object-detection
model **on the device itself**. Each observation becomes a structured event —
detected objects, confidence scores, bounding boxes, timestamp, device ID, image
reference — which is sent over Wi-Fi to a backend and stored in a cloud database.
A web dashboard shows the resulting history, and a natural-language layer answers
questions about it such as *"When was my laptop last detected?"*

The point is not the object detection. It is the **end-to-end IoT pipeline**:
sensing, edge inference, event modelling, wireless transport, resilience,
storage, and remote retrieval.

---

## 2. Architecture

```
Raspberry Pi 3B                     Laptop                      Cloud
───────────────                     ──────                      ─────
60-second timer
  → USB webcam capture (JPEG)
  → SSD MobileNet v1 (ONNX)   ┌──> FastAPI ──────────────────> MongoDB Atlas
  → filter to target classes  │      │                          (events)
  → compare to previous cycle │      ├──> backend/data/images/  (JPEGs on disk)
  → POST multipart ───────────┘      │
  → SQLite outbox if offline         ├──> Web dashboard
                                     └──> Groq API ──────────>  qwen3.8-27b
```

**Why the backend runs on a laptop rather than the Pi.** It separates the
constrained edge device from the server tier, which is what a real deployment
looks like, and keeps the Pi 3B's 1GB of RAM for inference. The Pi never talks to
the database or the LLM directly — it only speaks one REST endpoint.

---

## 3. Technology choices

| Layer | Choice | Why |
|---|---|---|
| Edge device | Raspberry Pi 3B | Available hardware; forces genuine edge constraints |
| Camera | USB webcam (`/dev/video0`) | No CSI camera available; code supports both |
| Inference | ONNX Runtime 1.30 | The only runtime that works on this Pi (see §7) |
| Model | SSD MobileNet v1, COCO | Small enough for a Pi 3B; 80 usable object classes |
| Transport | HTTP REST over Wi-Fi | Simple, debuggable, standard |
| Backend | FastAPI (Python) | Async, automatic OpenAPI docs at `/docs` |
| Database | MongoDB Atlas M0 | Free tier; schemaless documents suit variable-length detections |
| Image store | Local disk, path in DB | Atlas M0 is 512MB — images would exhaust it |
| LLM | Groq, `qwen/qwen3.8-27b` | Free tier, fast, supports JSON mode |
| Dashboard | Plain HTML/JS + Tailwind CDN | No build step, nothing to break during a demo |

---

## 4. The detection model

`ssd_mobilenet_v1_10.onnx` — 29.3 MB, Single Shot Detector with a MobileNet v1
backbone, trained on COCO, converted from TensorFlow by `tf2onnx`, from the
ONNX Model Zoo.

- **Input:** `image_tensor:0`, uint8 NHWC, fed at 300×300
- **Outputs:** `detection_boxes` (ymin, xmin, ymax, xmax, normalised),
  `detection_classes`, `detection_scores`, `num_detections` — up to 100 per frame
- **Threshold:** 0.4 (tuned — see §8)
- **Class IDs are 1-based** (1 = person) while the label file is 0-based; the
  code applies a `label_offset` per backend. Getting this wrong shifts every
  label by one.

### All 80 detectable classes

The label file has 90 IDs, 10 of which are unused placeholders in COCO.

**People & animals** — person, bird, cat, dog, horse, sheep, cow, elephant, bear,
zebra, giraffe

**Vehicles & street** — bicycle, car, motorcycle, airplane, bus, train, truck,
boat, traffic light, fire hydrant, stop sign, parking meter, bench

**Accessories** — backpack, umbrella, handbag, tie, suitcase

**Sports** — frisbee, skis, snowboard, sports ball, kite, skateboard,
surfboard, tennis racket, baseball bat, baseball glove

**Kitchen** — bottle, wine glass, cup, fork, knife, spoon, bowl

**Food** — banana, apple, sandwich, orange, broccoli, carrot, hot dog, pizza,
donut, cake

**Furniture** — chair, couch, potted plant, bed, dining table, toilet

**Electronics** — tv, laptop, mouse, remote, keyboard, cell phone

**Appliances** — microwave, oven, toaster, sink, refrigerator

**Other household** — book, clock, vase, scissors, teddy bear, hair drier,
toothbrush

### Classes actually reported

`pi/config.yaml` filters output to the desk-relevant subset, so a passing chair
or person doesn't pollute the history:

```
laptop, cell phone, bottle, book, keyboard, mouse, cup
```

> **Important for your report:** the original proposal lists **headphones** as a
> target object. **COCO has no headphones class**, so this model cannot detect
> them — that would require training a custom model. `book` stands in for
> "notebook". This is a factual limitation, not an oversight; state it plainly.

---

## 5. Data model

One document per capture cycle, in a single `events` collection:

```js
{
  device_id: "pi-desk-01",
  ts: ISODate,                          // UTC
  objects: [{ label: "laptop", confidence: 0.87, bbox: [x, y, w, h] }],
  object_labels: ["laptop", "bottle"],  // denormalised for indexed queries
  changed: true,                        // differs from the previous cycle?
  motion_since_last: false,             // optional PIR metadata
  image_path: "2026-09-23/102623_948.jpg",
  inference_ms: 1888,
  source: "device"                      // or "seed"
}
```

**Indexes:** `ts` descending, and a compound `(object_labels, ts)`. The compound
index is what makes "when was my laptop last seen" a single indexed lookup rather
than a collection scan.

`object_labels` duplicates data already in `objects`. That is deliberate:
MongoDB cannot efficiently index inside an array of subdocuments for this access
pattern, so the flat array of label strings is what the index is built on.

---

## 6. Key engineering decisions

### 6.1 Every cycle is transmitted — the dedupe logic does not suppress

The original proposal says to avoid transmitting when detections haven't
changed. Implemented literally, **this breaks the project's own headline
query.** If a laptop sits on the desk from 9am to 5pm, only the 9am *change*
event exists, so "when was my laptop last detected?" answers 9am — wrong by
eight hours.

The system therefore sends an event every cycle. `last_seen` is then simply
`max(ts)` over events containing that label. The comparison against the previous
cycle is still computed and stored as `changed`, which drives the dashboard's
transition highlighting and demonstrates the concept — it just never discards
data.

The cost is affordable because the system runs in demo sessions, not
continuously: roughly 60–120 events and ~12MB of images per session, against a
512MB database tier.

### 6.2 The LLM never writes a database query

```
question → [LLM] → Intent → [Python/Mongo] → events → [LLM] → answer
```

The model fills in a **validated schema**:

```python
query_type: "last_seen" | "present_during" | "timeline" | "unknown"
object_label: str | None
start / end: datetime | None
on_topic: bool
```

Python builds the Mongo query from that. Model output never reaches the database.
This matters for three reasons: it prevents query injection, it makes failures
debuggable (you can see exactly how a question was interpreted), and every answer
is traceable to rows returned in the same API response. The dashboard displays
the interpreted intent and record count beneath each answer.

### 6.3 Off-topic questions are refused deterministically

Early testing: *"What is the capital of France?"* → *"Paris."* The model answered
from its training data, not from the desk history. Prompt instructions alone were
not reliable. The intent schema now carries an `on_topic` flag, and off-topic
questions are refused **in code**, before the answering call is made.

Note the distinction: *"Was there a giraffe on my desk?"* **is** on topic — it is
answerable as "no record" — and still works.

### 6.4 Window questions use aggregation, not sampled rows

*"What objects were present yesterday?"* initially answered *"no objects were
detected."* The intent was correct, but retrieval listed raw events sorted
newest-first with a cap of 60 — and over a 1440-event day that returned only
23:00–23:59, which was empty. The model faithfully described an unrepresentative
sample.

Window queries now run a MongoDB **aggregation pipeline** over the entire period
(`$unwind` on labels, `$group` for first/last seen and counts), so the answer
reflects the whole window rather than a slice of it.

### 6.5 Store-and-forward

Every event is written to a local SQLite queue on the Pi **before** being sent,
and deleted only once the backend returns 2xx. Sends are attempted oldest-first
and stop at the first failure, preserving order. A dropped connection, a sleeping
laptop, or moving between networks costs delay, not data.

This was verified by killing the backend mid-run, confirming events queued, then
restarting and confirming they flushed with nothing lost.

### 6.6 Backend discovery

The laptop's IP changes per network, so the Pi resolves the backend in order:
configured URL → mDNS (`<hostname>.local`) → last-known-good address cached on
disk. Each candidate is probed with `GET /health` before use, and the Pi
re-resolves when its outbox starts backing up.

### 6.7 Timezone handling

Timestamps are stored in UTC. Early on, the LLM reported UTC values as
wall-clock time — answering "10:33 AM" while the dashboard correctly showed
"4:03 PM". Both intent extraction (so "yesterday" means *your* yesterday) and
answer rendering now convert to local time before the model sees anything.

---

## 7. Problems encountered and how they were resolved

These are worth knowing; they are the most likely questions.

| Problem | Diagnosis | Resolution |
|---|---|---|
| Atlas connection timed out with `ReplicaSetNoPrimary` | DNS resolved but TCP to port 27017 timed out. Tested against a host that accepts any port — 443 open, 27017 blocked. **The network blocked the port**, not an Atlas misconfiguration | VPN. Alternatives: hotspot, or a local MongoDB |
| Groq returned `model does not exist` | `llama-3.3-70b-versatile` not available on this account; Groq rotates its catalogue | Queried the models endpoint, switched to `qwen/qwen3.8-27b` — the available model that supports JSON mode |
| `pip install` hung for a long time on the Pi | `numpy==1.26.4` publishes **no wheels for Python 3.13**, so pip was compiling it from source on a 4-core ARM board | Used Debian's prebuilt apt packages; relaxed version pins |
| `tflite-runtime` would not install | No wheels published beyond **Python 3.11**; the Pi runs Debian 13 with Python 3.13 | — |
| `ai-edge-litert` crashed on import, exit code 135 | **SIGBUS** — the aarch64 wheel assumes CPU/memory features the Pi 3B's Cortex-A53 lacks. Crashed before loading any model | Added an ONNX backend using `onnxruntime`. `detector.py` selects backend by file extension, so TFLite still works on a laptop or Pi 4/5 |
| Pi rebooted spontaneously, destroying an install | `vcgencmd get_throttled` returned `0x50005` — under-voltage **and** throttling, currently active. A USB webcam draws 200–500mA off the Pi's bus | Powered USB hub for the webcam, and a genuine 5V 2.5A supply |
| Webcam returned stale frames | OpenCV buffers frames; with captures a minute apart, a single `read()` returns the *previous* cycle's image | Flush the buffer with `grab()` before reading |

**The wider point for a viva:** three separate failures (TFLite, numpy, power)
were all consequences of the same root constraint — an older ARM board running a
very new OS and Python. That is the authentic character of embedded work, and
diagnosing it required distinguishing *hang* from *crash* (exit 135 = SIGBUS),
and *blocked port* from *misconfigured service*.

---

## 8. Measured performance

Measured on the Pi 3B **while under-volted and throttled** — expect improvement
with adequate power.

| Metric | Value |
|---|---|
| Inference time | mean **1155 ms**, range 960–1888 ms (first is cold-start) |
| Capture cadence | exactly **60 s** between consecutive cycles |
| Image size | ~97 KB per JPEG at 640×480 |
| Event document | ~400 bytes |
| Detection confidence | 0.46–0.97 observed on desk objects |
| Webcam open time | ~21 s one-time at startup (GStreamer negotiation) |
| Model load | a few seconds, once per run |
| CPU temperature | 53 °C — thermally fine; the problem was purely power |

**Threshold tuning:** the default 0.5 was marginal. A laptop detected at 0.52 in
one frame dropped out entirely in the next, and a later true detection measured
0.46 — which the 0.5 threshold would have discarded. Lowering to **0.4** captured
it. This is the classic precision/recall trade-off, tuned against observed data
rather than guessed.

---

## 9. Honest limitations

State these before a marker finds them.

1. **Headphones cannot be detected** — no COCO class exists. The original
   proposal listed it.
2. **Detection is imperfect at this scale.** A quantized/small SSD model on
   640×480 webcam frames at desk distance yields confidences in the 0.4–0.6 range
   for some objects. Small or partly occluded items are missed.
3. **One-minute granularity.** An object present for 30 seconds between captures
   is never recorded. This is a deliberate trade for bandwidth and power.
4. **Single device.** The schema carries `device_id`, so multiple Pis would work,
   but only one was tested.
5. **No authentication.** The REST API is open on the LAN. Acceptable for a
   local demo; a real deployment needs device tokens and TLS.
6. **Privacy.** The system photographs a workspace and stores the images. In real
   use this needs consent, retention limits, and encryption at rest.
7. **The LLM can still phrase things loosely**, even though the retrieved data is
   correct. The intent and record count are displayed so answers can be checked
   against the underlying rows.
8. **Requires a VPN** on networks that block port 27017.

---

## 10. Deviations from the original proposal

| Proposal | Built | Why |
|---|---|---|
| PIR sensor triggers capture | Periodic 60s capture; PIR removed | The proposal contradicted itself (one section periodic, one motion-triggered). PIR detects *motion*, not *presence* — a still laptop on a desk is exactly the state worth recording, and PIR would miss it |
| Raspberry Pi Camera (CSI) | USB webcam | No CSI camera available; the code supports both via `capture.backend` |
| Suppress duplicate transmissions | Transmit always, flag `changed` | Suppression breaks "when was X last detected" (§6.1) |
| Headphones, notebook detected | `book`; headphones not possible | Not COCO classes |
| TFLite model | ONNX model | Neither TFLite runtime works on this hardware (§7) |
| Two-person split of work | Single implementation | — |

---

## 11. Likely questions

**Why not run detection in the cloud?**
That would defeat the purpose. Edge inference means only ~400 bytes of JSON
crosses the network per cycle instead of a 97 KB image or a video stream —
roughly 250× less traffic, plus lower latency and better privacy, since raw
images need never leave the device. Demonstrating that trade-off is the point of
the project.

**Why MongoDB rather than SQL?**
Each event holds a variable-length array of detections with nested fields, which
maps naturally to a document. The access patterns are "latest by time" and
"latest containing label X", both served by a compound index. A relational schema
would need an events table plus a detections table and a join on every query.

**Why is the LLM not generating the queries?**
Injection risk, non-determinism, and debuggability. The LLM extracts structured
intent into a validated schema; the query is built in code. Every answer is
traceable to rows returned in the same response.

**How do you know the answers aren't hallucinated?**
The API returns the retrieved records alongside the answer, and the dashboard
shows the interpreted intent and record count. Off-topic questions are refused in
code before the model is asked. This was tested — the system declines "what is
the capital of France?" while still correctly answering "was there a giraffe on
my desk?" with "no record."

**What happens if the network drops?**
Events queue in a local SQLite outbox and flush in order on reconnection. Tested
by killing the backend mid-run and restarting it; nothing was lost.

**Why is inference over a second? Isn't that slow?**
For a Pi 3B with no ML accelerator, ~1.1s for SSD MobileNet is expected — and it
is irrelevant at a 60-second cadence, using under 2% of the duty cycle. A faster
model was not needed; the measurement was taken while the board was throttled, so
it is a conservative figure.

**Why 60 seconds?**
It matches the proposal, and it suits the phenomenon: desk objects change on a
scale of minutes to hours, not seconds. Faster polling would multiply storage and
power cost for no extra information.

**What would you do differently?**
Check runtime and wheel availability against the target board's Python version
*before* committing to a model format — that single check would have avoided the
TFLite dead end. And measure power draw with the peripherals attached before
assuming a supply is adequate.

---

## 12. Where the code lives

| Concept | File |
|---|---|
| Sensing | `pi/camera.py` (USB and CSI), optional `pi/pir.py` |
| Edge inference | `pi/detector.py` — ONNX and TFLite backends |
| Event creation, dedupe flag, scheduling | `pi/main.py` |
| Resilience | `pi/outbox.py`, `pi/discovery.py` |
| Ingest API | `backend/routes/events.py` |
| Queries and aggregation | `backend/repository.py` |
| Natural language | `backend/llm.py`, `backend/routes/query.py` |
| Dashboard | `web/index.html`, `web/app.js` |
| Demo data | `scripts/seed_fake_events.py` |
| Setup verification | `scripts/check_setup.py` |

Interactive API documentation is generated automatically at `/docs`.
