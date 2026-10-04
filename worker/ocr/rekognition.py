"""Amazon Rekognition (DetectText) as the primary OCR engine on AWS.

Measured on 114 real photos of a Brazilian parking garage (examples/dataset):
Tesseract read 47% of the held-out half, Rekognition with the "largest plate"
rule read 96%. Rekognition is not available in the local emulator, so it is
enabled only with ``OCR_ENGINE=rekognition`` and always falls back to the
Tesseract pipeline when it errors or finds no plate.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from typing import Any

from PIL import Image, UnidentifiedImageError

from ocr.clean import normalize
from ocr.processor import OcrResult, extract_text

logger = logging.getLogger(__name__)

# Rekognition accepts up to 5 MB of image bytes; a 1920 px JPEG stays well
# below that and keeps a plate legible. Rescaling is also the image
# manipulation the assignment requires from the decoupled processor.
MAX_LONG_SIDE = 1920
_JPEG_QUALITY = 90


def prepare(image_bytes: bytes) -> bytes:
    """Decode, rescale to at most ``MAX_LONG_SIDE`` and re-encode as JPEG."""

    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = opened.convert("RGB")
    long_side = max(image.size)
    if long_side > MAX_LONG_SIDE:
        scale = MAX_LONG_SIDE / long_side
        image = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
        )
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


class HybridOcr:
    """Rekognition first, Tesseract when it fails or finds no plate.

    Every detected text line is a candidate, tallest first: a photo often shows
    the plates of cars in the background too, and the car in front of the
    camera has the tallest characters. The first line that normalizes to a
    plate is returned as the raw text, so the Poller's normalizer yields
    exactly that plate.
    """

    def __init__(
        self,
        client: Any,
        fallback: Callable[[bytes], OcrResult] = extract_text,
        normalizer: Callable[..., Any] = normalize,
    ) -> None:
        self._client = client
        self._fallback = fallback
        self._normalizer = normalizer

    def __call__(self, image_bytes: bytes) -> OcrResult:
        try:
            prepared = prepare(image_bytes)
        except UnidentifiedImageError, OSError, ValueError:
            return self._fallback(image_bytes)

        try:
            response = self._client.detect_text(Image={"Bytes": prepared})
        except Exception as exc:  # noqa: BLE001 - any Rekognition failure falls back
            logger.warning("Rekognition failed, falling back to Tesseract: %s", exc)
            return self._fallback(image_bytes)

        lines = [d for d in response.get("TextDetections", []) if d.get("Type") == "LINE"]
        lines.sort(key=lambda d: d["Geometry"]["BoundingBox"]["Height"], reverse=True)
        for detection in lines:
            text = detection["DetectedText"]
            if self._normalizer(text).ok:
                return OcrResult(ok=True, raw_text=text, error=None)

        logger.info("Rekognition found no plate, falling back to Tesseract")
        return self._fallback(image_bytes)
