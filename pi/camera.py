"""Image capture.

picamera2 on the Pi; OpenCV webcam otherwise, so the whole pipeline can be
developed and tested on a laptop before the hardware is wired up.
"""

import io
import logging

log = logging.getLogger(__name__)


class CameraError(RuntimeError):
    pass


class Camera:
    """Returns JPEG bytes. Backend is chosen once, at construction.

    backend:
      "auto"      try the CSI camera, fall back to USB/OpenCV
      "picamera"  CSI ribbon camera only (fail loudly if absent)
      "webcam"    USB webcam only — also the laptop development path
    """

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        backend: str = "auto",
        index: int = 0,
    ):
        self.width = width
        self.height = height
        self.index = index
        self._picam = None
        self._cv = None

        if backend not in ("auto", "picamera", "webcam"):
            raise CameraError(f"unknown camera backend {backend!r}")

        if backend in ("auto", "picamera"):
            try:
                self._start_picamera()
                return
            except Exception as exc:
                if backend == "picamera":
                    raise CameraError(f"Pi camera unavailable: {exc}") from exc
                log.info("picamera2 unavailable (%s); trying webcam", exc)

        self._start_webcam()

    def _start_picamera(self) -> None:
        from picamera2 import Picamera2

        self._picam = Picamera2()
        self._picam.configure(
            self._picam.create_still_configuration(main={"size": (self.width, self.height)})
        )
        self._picam.start()
        log.info("Using Pi camera (picamera2) at %dx%d", self.width, self.height)

    def _start_webcam(self) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise CameraError(
                "no camera available: install python3-picamera2 (CSI camera) "
                "or opencv-python (USB webcam)"
            ) from exc

        self._cv = cv2.VideoCapture(self.index)
        if not self._cv.isOpened():
            raise CameraError(
                f"no webcam at index {self.index} — check `ls /dev/video*`, "
                f"or set capture.webcam_index in config.yaml"
            )
        self._cv.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cv.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        log.info("Using USB webcam (index %d) at %dx%d", self.index, self.width, self.height)

    def capture_jpeg(self) -> bytes:
        if self._picam is not None:
            buffer = io.BytesIO()
            self._picam.capture_file(buffer, format="jpeg")
            return buffer.getvalue()

        import cv2

        # OpenCV keeps a short frame buffer. Between captures a minute apart that
        # buffer holds stale frames, so a single read() would return an image
        # from the *previous* cycle. Discard the backlog first.
        for _ in range(5):
            self._cv.grab()

        ok, frame = self._cv.read()
        if not ok:
            raise CameraError("webcam read failed")
        ok, encoded = cv2.imencode(".jpg", frame)
        if not ok:
            raise CameraError("JPEG encode failed")
        return encoded.tobytes()

    def close(self) -> None:
        if self._picam is not None:
            self._picam.stop()
        if self._cv is not None:
            self._cv.release()
