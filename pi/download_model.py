"""Fetch the detection model and COCO labels into pi/models/.

    python -m pi.download_model

The model is the quantized SSD MobileNet V2 (COCO) from Coral's test-data
mirror — small enough for a Pi 3B and the standard choice for this class of
device.
"""

import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).parent / "models"

# ONNX (default): works everywhere onnxruntime installs, including Pi 3B with
# Python 3.13, where both TFLite runtimes are unusable.
ONNX_URL = (
    "https://github.com/onnx/models/raw/main/validated/vision/"
    "object_detection_segmentation/ssd-mobilenetv1/model/ssd_mobilenet_v1_10.onnx"
)
# TFLite: smaller and faster, but needs tflite-runtime (Python <= 3.11) or
# ai-edge-litert (crashes on Cortex-A53). Fine on a laptop or a Pi 4/5.
TFLITE_URL = (
    "https://github.com/google-coral/test_data/raw/master/"
    "ssd_mobilenet_v2_coco_quant_postprocess.tflite"
)
LABELS_URL = "https://github.com/google-coral/test_data/raw/master/coco_labels.txt"

FILES = [
    (ONNX_URL, MODELS_DIR / "ssd_mobilenet_v1_10.onnx"),
    (LABELS_URL, MODELS_DIR / "coco_labels.txt"),
]
TFLITE_FILE = (TFLITE_URL, MODELS_DIR / "ssd_mobilenet_v2_coco_quant.tflite")


def download(url: str, target: Path) -> None:
    if target.exists():
        print(f"  exists   {target.name} ({target.stat().st_size / 1e6:.1f} MB)")
        return

    print(f"  fetching {target.name} …", end="", flush=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, target)
    except Exception as exc:
        print(f" failed\n\n  {exc}\n  URL: {url}")
        raise
    print(f" done ({target.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch the detection model")
    parser.add_argument(
        "--tflite",
        action="store_true",
        help="also fetch the TFLite model (needs a working TFLite runtime)",
    )
    args = parser.parse_args()

    files = FILES + ([TFLITE_FILE] if args.tflite else [])
    print(f"Downloading detection model into {MODELS_DIR}")
    try:
        for url, target in files:
            download(url, target)
    except Exception:
        return 1
    print("\nReady. Run the capture loop with:  python -m pi.main --once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
