"""OCR_Processor — pure image pre-processing and Tesseract text extraction.

This module is part of the pure OCR layer (`ocr/`): it has no AWS or database
dependencies and returns explicit result objects rather than raising for domain
outcomes (undecodable bytes, empty OCR output, timeout). See design.md
"OCR_Processor (`ocr/processor.py`) — pure" and Requirement 4.

Two kinds of image go through rescale -> grayscale -> binary threshold ->
Tesseract (Req 4.1), in this order and within one shared timeout budget:

1. The character strip of each Mercosul plate found by ``ocr.locate`` (by its
   blue band). This is what reads real car photos.
2. The whole photo, as a fallback for plates without a band (Old_Format) or
   photos that are already a tight crop.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass

import pytesseract
from PIL import Image, ImageOps
from PIL.Image import UnidentifiedImageError

from ocr.locate import find_mercosul_plates

# Characters a Brazilian plate can contain.
_WHITELIST = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# Page segmentation modes for the whole photo. No single mode reads every
# photo: 7 (single line) and 6 (block) read a tight crop but miss a plate
# inside a car photo; 11 (sparse text) finds it in a car photo but returns
# nothing for a binarized crop whose "BRASIL" band turned into a black block.
_TESSERACT_CONFIGS = tuple(f"--psm {psm} {_WHITELIST}" for psm in (7, 6, 11))

# Modes for a located character strip: single word (8), raw line (13) and
# single line (7).
_PLATE_CONFIGS = tuple(f"--psm {psm} {_WHITELIST}" for psm in (8, 13, 7))

# The whole photo is rescaled so its longer side lands in this range: the
# previous fixed 2x upscale made the characters of a 1920 px photo ~400 px
# tall, far above what Tesseract reads, and heavy downscaling loses them.
_MIN_LONG_SIDE, _MAX_LONG_SIDE = 1000, 2000

# A located strip is rescaled to this height (characters ~70 px tall), which
# read the Mercosul typeface most reliably in calibration.
_PLATE_HEIGHT = 100

# Left part of the strip holding the "BR" mark and the QR code, not characters.
_PLATE_LEFT_TRIM = 0.08


@dataclass
class OcrResult:
    ok: bool
    raw_text: str | None
    error: str | None  # "decode" | "no_text" | "timeout"
    # True when a Mercosul plate was located by its blue band, so the text can
    # only be a Mercosul plate (lets the Plate_Normalizer rule out Old_Format).
    mercosul: bool = False


def _decode(image_bytes: bytes) -> Image.Image:
    """Decode the bytes into an RGB image.

    Raises UnidentifiedImageError / OSError when the bytes are not a decodable
    image so the caller can map that to a ``decode`` domain error before any
    OCR is attempted (Req 4.4).
    """
    with Image.open(io.BytesIO(image_bytes)) as opened:
        # Force decode of the pixel data while the file handle is open.
        return opened.convert("RGB")


def _binarize(image: Image.Image) -> Image.Image:
    """Grayscale then binary threshold, with Otsu's per-image cut-off."""

    gray = ImageOps.grayscale(image)
    threshold = otsu_threshold(gray.histogram())
    return gray.point(lambda px: 255 if px > threshold else 0, mode="1")


def _preprocess(image: Image.Image) -> Image.Image:
    """Rescale -> grayscale -> binary threshold for the whole photo (Req 4.1)."""

    width, height = image.size
    long_side = max(width, height)
    if long_side < _MIN_LONG_SIDE:
        scale = _MIN_LONG_SIDE / long_side
    elif long_side > _MAX_LONG_SIDE:
        scale = _MAX_LONG_SIDE / long_side
    else:
        scale = 1.0
    if scale != 1.0:
        image = image.resize(
            (round(width * scale), round(height * scale)), Image.Resampling.LANCZOS
        )
    return _binarize(image)


def _preprocess_plate(strip: Image.Image) -> Image.Image:
    """Rescale -> grayscale -> binary threshold for a located strip (Req 4.1).

    Drops the "BR"/QR zone and adds a white margin: Tesseract misreads glyphs
    that touch the image border.
    """

    width, height = strip.size
    strip = strip.crop((int(width * _PLATE_LEFT_TRIM), 0, width, height))
    width, height = strip.size
    strip = strip.resize(
        (max(1, round(width * _PLATE_HEIGHT / height)), _PLATE_HEIGHT),
        Image.Resampling.LANCZOS,
    )
    return ImageOps.expand(_binarize(strip).convert("L"), border=_PLATE_HEIGHT // 2, fill=255)


def otsu_threshold(histogram: list[int]) -> int:
    """Return the gray level that best separates dark from light pixels.

    Otsu's method: pick the cut-off that maximizes the between-class variance
    of the two pixel populations. Pixels ``<= threshold`` become black.
    """
    total = sum(histogram)
    if total == 0:
        return 127
    weighted_total = sum(level * count for level, count in enumerate(histogram))

    best_level, best_variance = 127, -1.0
    background, weighted_background = 0, 0
    for level, count in enumerate(histogram):
        background += count
        if background == 0:
            continue
        foreground = total - background
        if foreground == 0:
            break
        weighted_background += level * count
        mean_background = weighted_background / background
        mean_foreground = (weighted_total - weighted_background) / foreground
        variance = background * foreground * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_level, best_variance = level, variance
    return best_level


def extract_text(image_bytes: bytes, timeout_s: float = 10.0) -> OcrResult:
    """Locate plates, pre-process, and run Tesseract (Req 4.1-4.6).

    Returns the raw OCR text of every located plate strip followed by the
    whole photo, one Tesseract output per line. Domain outcomes are returned
    as typed ``OcrResult`` values and never raised:
      - ``decode``   : bytes cannot be decoded into an image (Tesseract skipped).
      - ``no_text``  : OCR produced no non-whitespace text.
      - ``timeout``  : OCR did not complete within ``timeout_s`` seconds.
    """
    # Decode first. Undecodable bytes short-circuit before OCR.
    try:
        image = _decode(image_bytes)
    except UnidentifiedImageError, OSError, ValueError:
        return OcrResult(ok=False, raw_text=None, error="decode")

    plates = find_mercosul_plates(image)
    jobs = [(_preprocess_plate(strip), _PLATE_CONFIGS) for strip in plates]
    jobs.append((_preprocess(image), _TESSERACT_CONFIGS))

    # Run Tesseract under a timeout guard (Req 4.2, 4.6) shared by every call.
    # pytesseract kills the tesseract subprocess when its budget is exceeded
    # and raises RuntimeError, so a hung OCR never blocks the Poller.
    deadline = time.monotonic() + timeout_s
    outputs: list[str] = []
    for prepared, configs in jobs:
        for config in configs:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return OcrResult(ok=False, raw_text=None, error="timeout")
            try:
                text = pytesseract.image_to_string(prepared, config=config, timeout=remaining)
            except RuntimeError as exc:
                if "timeout" not in str(exc).lower():
                    raise
                return OcrResult(ok=False, raw_text=None, error="timeout")
            if text and text.strip():
                outputs.append(text.strip())

    # Empty / whitespace-only output from every call is an extraction failure
    # (Req 4.5).
    if not outputs:
        return OcrResult(ok=False, raw_text=None, error="no_text")

    # Success: return the raw text to the caller (Req 4.3).
    return OcrResult(ok=True, raw_text="\n".join(outputs), error=None, mercosul=bool(plates))
