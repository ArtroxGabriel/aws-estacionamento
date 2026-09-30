"""OCR_Processor — pure image pre-processing and Tesseract text extraction.

This module is part of the pure OCR layer (`ocr/`): it has no AWS or database
dependencies and returns explicit result objects rather than raising for domain
outcomes (undecodable bytes, empty OCR output, timeout). See design.md
"OCR_Processor (`ocr/processor.py`) — pure" and Requirement 4.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pytesseract
from PIL import Image, ImageOps
from PIL.Image import UnidentifiedImageError

# Factor applied when rescaling the source image before OCR. Upscaling small
# plate crops improves Tesseract accuracy (Req 4.1 — rescaling step).
_RESCALE_FACTOR = 2

# Luminance cut-off for the binary threshold step. Pixels at or above this value
# become white (255); below it become black (0) (Req 4.1 — binary thresholding).
_BINARY_THRESHOLD = 128

# Sparse-text page segmentation (the plate is a small block somewhere in a car
# photo) restricted to the characters a Brazilian plate can contain.
_TESSERACT_CONFIG = "--psm 11 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass
class OcrResult:
    ok: bool
    raw_text: str | None
    error: str | None  # "decode" | "no_text" | "timeout"


def _preprocess(image_bytes: bytes) -> Image.Image:
    """Decode the bytes and apply rescale -> grayscale -> binary threshold.

    Raises UnidentifiedImageError / OSError when the bytes are not a decodable
    image so the caller can map that to a ``decode`` domain error before any
    OCR is attempted (Req 4.4).
    """
    with Image.open(io.BytesIO(image_bytes)) as opened:
        # Force decode of the pixel data while the file handle is open.
        image = opened.convert("RGB")

    # 1. Rescale.
    width, height = image.size
    if width > 0 and height > 0:
        image = image.resize(
            (width * _RESCALE_FACTOR, height * _RESCALE_FACTOR),
            Image.Resampling.LANCZOS,
        )

    # 2. Grayscale.
    image = ImageOps.grayscale(image)

    # 3. Binary threshold.
    image = image.point(lambda px: 255 if px >= _BINARY_THRESHOLD else 0, mode="1")

    return image


def extract_text(image_bytes: bytes, timeout_s: float = 10.0) -> OcrResult:
    """Rescale -> grayscale -> binary threshold -> Tesseract (Req 4.1-4.6).

    Returns raw OCR text on success. Domain outcomes are returned as typed
    ``OcrResult`` values and never raised:
      - ``decode``   : bytes cannot be decoded into an image (Tesseract skipped).
      - ``no_text``  : OCR produced no non-whitespace text.
      - ``timeout``  : OCR did not complete within ``timeout_s`` seconds.
    """
    # Pre-process (includes decode). Undecodable bytes short-circuit before OCR.
    try:
        image = _preprocess(image_bytes)
    except UnidentifiedImageError, OSError, ValueError:
        return OcrResult(ok=False, raw_text=None, error="decode")

    # Run Tesseract under a timeout guard (Req 4.2, 4.6). pytesseract kills the
    # tesseract subprocess when the budget is exceeded and raises RuntimeError,
    # so a hung OCR never blocks the Poller.
    try:
        raw_text = pytesseract.image_to_string(image, config=_TESSERACT_CONFIG, timeout=timeout_s)
    except RuntimeError as exc:
        if "timeout" not in str(exc).lower():
            raise
        return OcrResult(ok=False, raw_text=None, error="timeout")

    # Empty / whitespace-only output is an extraction failure (Req 4.5).
    if raw_text is None or raw_text.strip() == "":
        return OcrResult(ok=False, raw_text=None, error="no_text")

    # Success: return the raw text to the caller (Req 4.3).
    return OcrResult(ok=True, raw_text=raw_text, error=None)
