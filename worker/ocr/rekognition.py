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
import re
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


def framed(image_bytes: bytes) -> bytes:
    """Center the photo on a canvas twice its size (gray margin around it).

    Rekognition misses text that fills the whole image: on 100 tight plate
    crops it read 13 with the right aspect ratio and 83 once framed.
    """

    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = opened.convert("RGB")
    canvas = Image.new("RGB", (image.width * 2, image.height * 2), (128, 128, 128))
    canvas.paste(image, (image.width // 2, image.height // 2))
    buf = io.BytesIO()
    canvas.save(buf, "JPEG", quality=_JPEG_QUALITY)
    return prepare(buf.getvalue())


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
            plate_raw = self._detect(prepared)
            if plate_raw is None:
                # A close-up of the plate (no margin around the text) is often
                # missed; a second call on the framed photo usually reads it.
                plate_raw = self._detect(framed(image_bytes))
        except Exception as exc:  # noqa: BLE001 - any Rekognition failure falls back
            logger.warning("Rekognition failed, falling back to Tesseract: %s", exc)
            return self._fallback(image_bytes)
        if plate_raw is not None:
            return OcrResult(ok=True, raw_text=plate_raw, error=None)

        logger.info("Rekognition found no plate, falling back to Tesseract (exact reads only)")
        return self._exact_fallback(image_bytes)

    def _detect(self, prepared: bytes) -> str | None:
        """Raw text of the tallest line that normalizes to a plate, or None."""

        response = self._client.detect_text(Image={"Bytes": prepared})
        lines = [d for d in response.get("TextDetections", []) if d.get("Type") == "LINE"]
        lines.sort(key=lambda d: d["Geometry"]["BoundingBox"]["Height"], reverse=True)
        # The country name of a foreign Mercosul plate comes as its own line;
        # it is what unlocks the Paraguayan and old Argentine formats.
        countries = [
            d["DetectedText"]
            for d in lines
            if any(c in d["DetectedText"].upper() for c in ("ARGENTINA", "PARAGUAY", "URUGUAY"))
        ]
        for detection in lines:
            raw = "\n".join([detection["DetectedText"], *countries])
            if self._normalizer(raw).ok:
                return raw
        return None

    def _exact_fallback(self, image_bytes: bytes) -> OcrResult:
        """Tesseract after Rekognition found no plate, keeping only safe reads.

        Where Rekognition read no plate, Tesseract usually reads noise, and the
        normalizer's character corrections can turn that noise into a valid but
        wrong plate (a wrong plate charges the wrong car; an unread one goes to
        the cashier). A read is kept when it is exact, or when it needed a
        single correction on a plate located by its Mercosul band (the typeface
        I/1 confusion, e.g. LSN4I49, whose hologram also fools Rekognition).
        On 1,520 test photos this kept all 7 plates the fallback recovered and
        dropped 9 of its 14 wrong ones.
        """

        result = self._fallback(image_bytes)
        if result.ok and result.raw_text:
            plate = self._normalizer(result.raw_text, mercosul=result.mercosul)
            if plate.ok:
                swaps = _swaps(plate.plate, result.raw_text)
                if swaps == 0 or (swaps == 1 and result.mercosul):
                    return OcrResult(
                        ok=True, raw_text=plate.plate, error=None, mercosul=result.mercosul
                    )
        # Rekognition answered and no safe plate was found: an unreadable plate
        # (FAILED at once, the cashier types it), not an OCR error, which the
        # worker would retry for ~15 min with the same result.
        return OcrResult(ok=True, raw_text="", error=None)


def _swaps(plate: str, raw: str) -> int:
    """Fewest characters that differ between ``plate`` and any window of ``raw``."""

    best = len(plate)
    for line in raw.splitlines():
        text = re.sub(r"[^A-Z0-9]", "", line.upper())
        for start in range(len(text) - len(plate) + 1):
            window = text[start : start + len(plate)]
            best = min(best, sum(a != b for a, b in zip(plate, window, strict=True)))
    return best
