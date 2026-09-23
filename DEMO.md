# SenseTrack — Demo Runbook

Everything needed to go from a powered-off setup to a working demonstration.
Follow it in order; the checks exist because each one has already failed once
during development.

---

## Before the day

| Item | Why |
|---|---|
| **Powered USB hub for the webcam** | The Pi 3B under-volts with a webcam on its own USB bus. This caused a reboot that destroyed a 30-minute install. |
| **5V 2.5A supply** for the Pi | Not a laptop USB port (500mA) and not a random phone charger. |
| **Phone hotspot** decided in advance | Campus/office Wi-Fi often blocks device-to-device traffic. A hotspot is the reliable fallback — the Pi and laptop must be on the *same* network. |
| **VPN working on the laptop** | MongoDB uses port 27017, which many networks block. Without it Atlas is unreachable. |
| **Seeded history loaded** | A 1–2 hour session has no "yesterday" to answer questions about. |

---

## 1. Laptop — start the backend

```bash
cd C:\Users\jassu\Downloads\SenseTrack
```

Connect the VPN first, then verify credentials:

```bash
.venv\Scripts\python.exe -m scripts.check_setup
```

Expect `ok` for both MongoDB Atlas and Groq. If Atlas times out, the VPN is off
or the network is blocking port 27017.

```bash
.venv\Scripts\python.exe -m backend.main
```

It prints the LAN address the Pi should use. **Write that address down** — you
need it in step 3. Leave this window running.

Open the dashboard at the printed address (or <http://localhost:8000>).

### Seed history (once per demo, if the database is empty)

```bash
.venv\Scripts\python.exe -m scripts.seed_fake_events --days 3
```

Seeded rows are tagged `source: "seed"` and labelled *seeded* in the UI. Remove
them afterwards with `--clear`.

---

## 2. Laptop — allow inbound connections

One-time, in an **Administrator** PowerShell:

```bash
New-NetFirewallRule -DisplayName "SenseTrack" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

Windows marks new networks *Public* and blocks inbound traffic by default. A Pi
that "can't reach the backend" is usually this.

---

## 3. Raspberry Pi — start capture

Power the Pi, connect the webcam (through the powered hub), and SSH in:

```bash
ssh raspberrypi@raspberrypi.local
```

If the hostname doesn't resolve — mDNS is unreliable on Windows — find the Pi's
address on your hotspot and use it directly, e.g. `ssh raspberrypi@172.20.10.2`.

**Confirm the Pi can reach the laptop before anything else.** Substitute the
address from step 1:

```bash
curl -s -m 5 http://<LAPTOP_IP>:8000/health
```

You want `{"status":"ok","events":N}`. If it hangs: check the firewall rule, then
suspect client isolation on the network — no configuration fixes that, so move
both devices to the hotspot.

Then a single test cycle:

```bash
cd ~/SenseTrack && source .venv/bin/activate && python -m pi.main --once --backend http://<LAPTOP_IP>:8000 --verbose
```

Expect detected objects and an inference time, and a new event on the dashboard
within seconds. Then start the real loop:

```bash
cd ~/SenseTrack && source .venv/bin/activate && python -u -m pi.main --backend http://<LAPTOP_IP>:8000
```

To have it survive an SSH disconnect:

```bash
cd ~/SenseTrack && setsid nohup bash -c 'source .venv/bin/activate && python -u -m pi.main --backend http://<LAPTOP_IP>:8000' > loop.log 2>&1 &
```

Watch it with `tail -f ~/SenseTrack/loop.log`.

---

## 4. Demo script

Captures happen every 60 seconds, so pace the narration around that. Put a
**laptop, bottle, cup, phone, book, keyboard or mouse** in frame — those are the
COCO classes being reported.

| # | Do | Say |
|---|---|---|
| 1 | Show the dashboard with the timeline filling | "The Pi photographs the desk every minute and identifies objects **on the device itself** — only a small JSON event crosses the network, not a video stream." |
| 2 | Point at a thumbnail | "Each event carries the detected objects, a timestamp, the device ID, and a reference to the stored image." |
| 3 | Put an object into frame, wait one cycle | "The next capture picks it up, and it's flagged `changed` because it differs from the previous observation." |
| 4 | Leave it, wait another cycle | "Still recorded, but no longer flagged — that's the duplicate-detection logic." |
| 5 | Ask *"When was my laptop last detected?"* | "The question goes to an LLM, but only to extract structured intent. The database query itself is built in code." |
| 6 | Point at the line under the answer | "It shows how the question was interpreted and how many records were retrieved — the answer is traceable to real rows." |
| 7 | Ask *"What objects were present yesterday?"* | Answers from seeded history. |
| 8 | Ask *"What is the capital of France?"* | "It declines — it only answers from the monitored environment." |

### Optional: demonstrate resilience

Stop the backend on the laptop, wait a cycle (the Pi logs a failed send), restart
it. The queued event is delivered on the next cycle with nothing lost.

> "Every event is written to a local SQLite queue before being sent, and only
> deleted once the server confirms receipt. A dropped network costs delay, not
> data."

---

## 5. Shut down

On the Pi:

```bash
pkill -f pi.main && sudo poweroff
```

On the laptop: Ctrl-C the backend window. Optionally clear seeded rows with
`.venv\Scripts\python.exe -m scripts.seed_fake_events --clear`.

---

## If something breaks mid-demo

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard shows no new events | Pi can't reach the laptop | `curl .../health` from the Pi; check firewall and that both are on the hotspot |
| Backend errors on startup | VPN down, so Atlas unreachable | Reconnect the VPN, re-run `check_setup` |
| Questions return an error | Groq key or model | `check_setup` reports it; models change, see README |
| Pi reboots | Under-voltage | Powered hub for the webcam; `vcgencmd get_throttled` should read `0x0` |
| Objects not detected | Threshold or lighting | Lower `detection.confidence_threshold` in `pi/config.yaml`; improve lighting |
| `raspberrypi.local` won't resolve | Windows mDNS is unreliable | Use the Pi's IP directly |

**Worst case:** the dashboard works entirely from stored data. If the Pi fails,
seeded history still demonstrates the backend, search, and the natural-language
layer. Only the live capture is lost.
