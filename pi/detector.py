"""On-device object detection.

Two interchangeable backends, chosen by the model file's extension:

  .onnx    onnxruntime — the working path on Debian 13 / Python 3.13 / Pi 3B
  .tflite  tflite_runtime or ai-edge-litert

Prefer ONNX on a Pi 3B. `tflite-runtime` publishes no wheels beyond Python 3.11,
and `ai-edge-litert`'s aarch64 wheel crashes with SIGBUS on the Pi 3B's
Cortex-A53. The TFLite path is kept for newer boards and for laptops, where both
work fine.

Measured on a Pi 3B (throttled): SSD MobileNet v1 ONNX, roughly 0.7-1.5s per
frame. Comfortable against a 60s capture interval, and the reason YOLO is not
used here.
"""

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

# SSD MobileNet's native input resolution.
INPUT_SIZE = (300, 300)


@dataclass
class Detection:
    label: str
    confidence: float
    bbox: list[float]  # [x, y, w, h] normalised to 0-1

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "bbox": [round(v, 4) for v in self.bbox],
        }


class _OnnxBackend:
    """TensorFlow Object Detection API models exported to ONNX.

    Class ids are 1-based (1 = person), whereas the COCO label file is 0-based,
    hence label_offset.
    """

    label_offset = 1

    def __init__(self, model_path: Path):
        import onnxruntime as ort

        options = ort.SessionOptions()
        # The Pi 3B has four slow cores; letting ORT spawn more threads than
        # that only adds contention.
        options.intra_op_num_threads = 4
        self.session = ort.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def infer(self, image: np.ndarray):
        outputs = self.session.run(None, {self.input_name: image[np.newaxis, ...]})
        boxes, classes, scores = outputs[0][0], outputs[1][0], outputs[2][0]
        return boxes, classes, scores


class _TFLiteBackend:
    """Quantized TFLite SSD with the standard detection postprocess op.

    Class ids are 0-based here, matching the COCO label file directly.
    """

    label_offset = 0

    def __init__(self, model_path: Path):
        self.interpreter = self._load(model_path)
        self.interpreter.allocate_tensors()

        details = self.interpreter.get_input_details()[0]
        self._index = details["index"]
        self._dtype = details["dtype"]
        self._outputs = self.interpreter.get_output_details()

    @staticmethod
    def _load(model_path: Path):
        attempts = []
        for module, name in (
            ("tflite_runtime.interpreter", "tflite_runtime"),
            ("ai_edge_litert.interpreter", "ai-edge-litert"),
            ("tensorflow.lite.python.interpreter", "tensorflow"),
        ):
            try:
                mod = __import__(module, fromlist=["Interpreter"])
                return mod.Interpreter(model_path=str(model_path))
            except ImportError as exc:
                attempts.append(f"{name}: {exc}")

        raise RuntimeError(
            "No TFLite runtime available. On a Pi 3B with Python 3.13 use an "
            "ONNX model instead (see pi/download_model.py) — tflite-runtime has "
            "no wheels past Python 3.11 and ai-edge-litert crashes on this CPU.\n"
            + "\n".join(f"  tried {a}" for a in attempts)
        )

    def infer(self, image: np.ndarray):
        tensor = image[np.newaxis, ...]
        if self._dtype == np.float32:
            tensor = (tensor.astype(np.float32) - 127.5) / 127.5
        else:
            tensor = tensor.astype(self._dtype)

        self.interpreter.set_tensor(self._index, tensor)
        self.interpreter.invoke()

        by_name = {}
        for detail in self._outputs:
            name = detail["name"].lower()
            value = self.interpreter.get_tensor(detail["index"])[0]
            for key in ("box", "class", "score"):
                if key in name:
                    by_name.setdefault(key, value)

        if {"box", "class", "score"} <= by_name.keys():
            return by_name["box"], by_name["class"], by_name["score"]

        # Conventional ordering: boxes, classes, scores, num_detections.
        tensors = [self.interpreter.get_tensor(d["index"])[0] for d in self._outputs]
        return tensors[0], tensors[1], tensors[2]


class Detector:
    def __init__(
        self,
        model_path: Path,
        labels_path: Path,
        threshold: float = 0.5,
        target_labels: list[str] | None = None,
    ):
        if not model_path.exists():
            raise FileNotFoundError(
                f"model not found: {model_path}\nRun: python -m pi.download_model"
            )

        self.threshold = threshold
        # Empty/None means "report every class".
        self.targets = set(target_labels) if target_labels else None
        self.labels = self._load_labels(labels_path)

        suffix = model_path.suffix.lower()
        if suffix == ".onnx":
            self.backend = _OnnxBackend(model_path)
        elif suffix == ".tflite":
            self.backend = _TFLiteBackend(model_path)
        else:
            raise ValueError(f"unsupported model type {suffix!r}: use .onnx or .tflite")

        log.info(
            "Detector ready: %s via %s, %d labels",
            model_path.name,
            type(self.backend).__name__,
            len(self.labels),
        )

    @staticmethod
    def _load_labels(path: Path) -> dict[int, str]:
        """Supports both 'id name' and bare one-per-line label files."""
        if not path.exists():
            raise FileNotFoundError(f"labels not found: {path}")

        labels: dict[int, str] = {}
        for index, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
            line = raw.strip()
            if not line:
                continue
            head, _, rest = line.partition(" ")
            if head.isdigit() and rest:
                labels[int(head)] = rest.strip()
            else:
                labels[index] = line
        return labels

    def detect(self, jpeg: bytes) -> tuple[list[Detection], float]:
        """Returns (detections, inference milliseconds)."""
        image = np.asarray(
            Image.open(io.BytesIO(jpeg)).convert("RGB").resize(INPUT_SIZE), dtype=np.uint8
        )

        started = time.perf_counter()
        boxes, classes, scores = self.backend.infer(image)
        elapsed_ms = (time.perf_counter() - started) * 1000

        detections: list[Detection] = []
        for box, class_id, score in zip(boxes, classes, scores):
            if score < self.threshold:
                continue

            index = int(class_id) - self.backend.label_offset
            label = self.labels.get(index, f"id:{index}")
            if self.targets is not None and label not in self.targets:
                continue

            # Both backends emit [ymin, xmin, ymax, xmax]; convert to [x, y, w, h].
            ymin, xmin, ymax, xmax = (float(v) for v in box)
            x, y = max(0.0, xmin), max(0.0, ymin)
            detections.append(
                Detection(
                    label=label,
                    confidence=float(score),
                    bbox=[x, y, min(1.0, xmax) - x, min(1.0, ymax) - y],
                )
            )

        return detections, elapsed_ms
