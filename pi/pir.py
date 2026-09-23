"""PIR motion sensor.

Per the design decision, motion never triggers capture — capture is strictly on
the 60s timer. The PIR runs as a background watcher whose "did anything move
since the last capture?" latch is attached to each event as metadata.
"""

import logging
import threading
import time

log = logging.getLogger(__name__)


class PirSensor:
    """Latches motion between reads. `consume()` returns and clears the latch."""

    def __init__(self, pin: int = 4, poll_interval: float = 0.2):
        self.pin = pin
        self.poll_interval = poll_interval
        self._motion = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gpio = None

        try:
            import RPi.GPIO as GPIO

            GPIO.setmode(GPIO.BCM)
            GPIO.setup(pin, GPIO.IN)
            self._gpio = GPIO
        except (ImportError, RuntimeError) as exc:
            log.info("PIR unavailable (%s); motion will always report False", exc)
            return

        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()
        log.info("PIR watching BCM pin %d", pin)

    @property
    def available(self) -> bool:
        return self._gpio is not None

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                if self._gpio.input(self.pin):
                    with self._lock:
                        self._motion = True
            except Exception as exc:
                log.warning("PIR read failed: %s", exc)
                return
            time.sleep(self.poll_interval)

    def consume(self) -> bool:
        """True if motion occurred since the previous call. Clears the latch."""
        with self._lock:
            seen, self._motion = self._motion, False
        return seen

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._gpio is not None:
            self._gpio.cleanup(self.pin)
