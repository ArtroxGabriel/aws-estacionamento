"""Plate localization — finds license plates in a photo before OCR.

Tesseract cannot read a plate that fills a small part of a car photo, and a
single global threshold rarely separates it from the car body. Two locators
produce crops for OCR:

1. ``find_mercosul_plates``: every Mercosul plate carries a saturated blue band
   across its top edge, easy to find by color; the characters sit right below
   it, in a strip whose height is a fixed fraction of the band width.
2. ``find_plate_lines``: works for both formats (Old_Format plates have no
   band, and on a blue car the band merges with the body). Finds plate-shaped
   rectangles by their edges (400 x 130 mm -> aspect ~3) and keeps those that
   contain a row of at least 5 character-like dark blobs, returning a tight
   crop of that row (excluding the city/UF line of old plates and the band of
   Mercosul plates).

Pure module (no I/O): works on an in-memory ``PIL.Image``.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image

__all__ = ["PlateLine", "find_mercosul_plates", "find_plate_lines"]

# OpenCV HSV range (H in 0-180) of the Mercosul band blue.
_BLUE_LOW = (95, 80, 60)
_BLUE_HIGH = (130, 255, 255)

# Shape filters for the band's bounding box: a wide, thin stripe that is not
# negligible in the frame (rejects blue badges, sky patches, stickers).
_MIN_ASPECT, _MAX_ASPECT = 4.0, 16.0
_MIN_BAND_WIDTH_PX = 60
_MIN_AREA_FRACTION = 0.001

# Character strip relative to the band: it starts slightly above the band's
# bottom edge (the band box overlaps the character tops) and is this fraction
# of the band width tall.
_STRIP_START = 0.8  # of the band height, from the band top
_STRIP_HEIGHT = 0.28  # of the band width

_MAX_PLATES = 3

# Edge detection runs on a copy this wide (speed, and stable Canny thresholds).
_DETECT_WIDTH = 1000

# Plate-shaped rectangle: aspect ratio of its min-area rectangle and the share
# of the frame it covers.
_PLATE_ASPECT = (2.0, 6.0)
_PLATE_AREA_FRACTION = (0.003, 0.5)

# Character-like blob inside a plate, relative to the plate crop.
_CHAR_HEIGHT = (0.3, 0.9)
_CHAR_WIDTH = (0.02, 0.25)
_MIN_CHARS = 5

# Margin around the character row, as a fraction of the character height.
_LINE_MARGIN = 0.15

# A plate is Mercosul when this share of the zone right above its characters
# (this many character heights tall) is blue: the band sits there, while an
# Old_Format plate has its gray city line.
_MERCOSUL_BLUE_SHARE = 0.3
_BAND_ZONE = 0.6


@dataclass(frozen=True)
class PlateLine:
    """A tight crop of a plate's character row."""

    image: Image.Image
    mercosul: bool  # the plate area above the characters is Mercosul blue


def find_mercosul_plates(image: Image.Image) -> list[Image.Image]:
    """Return crops of the character strip of each Mercosul plate found.

    Crops are ordered by band size (largest first) and capped at
    ``_MAX_PLATES``. An empty list means no Mercosul band was found.
    """

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    mask = _blue_mask(rgb)
    # Bridge the gaps the "BRASIL" lettering and the flag cut into the band.
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (15, 5))
    )
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    bands = []
    for contour in contours:
        x, y, band_w, band_h = cv2.boundingRect(contour)
        if band_w < _MIN_BAND_WIDTH_PX or band_h == 0:
            continue
        if not _MIN_ASPECT <= band_w / band_h <= _MAX_ASPECT:
            continue
        if band_w * band_h < _MIN_AREA_FRACTION * width * height:
            continue
        bands.append((band_w * band_h, x, y, band_w, band_h))

    crops = []
    for _, x, y, band_w, band_h in sorted(bands, reverse=True)[:_MAX_PLATES]:
        top = int(y + _STRIP_START * band_h)
        bottom = min(height, int(top + _STRIP_HEIGHT * band_w))
        if bottom - top < 10:
            continue
        crops.append(image.crop((x, top, x + band_w, bottom)))
    return crops


def find_plate_lines(image: Image.Image) -> list[PlateLine]:
    """Return the character row of each plate-shaped region, any format.

    Ordered by number of characters found, then plate area (largest first),
    duplicates removed, capped at ``_MAX_PLATES``.
    """

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    scale = _DETECT_WIDTH / width
    small = cv2.cvtColor(
        cv2.resize(rgb, (_DETECT_WIDTH, max(1, round(height * scale)))), cv2.COLOR_RGB2GRAY
    )
    edges = cv2.Canny(cv2.bilateralFilter(small, 9, 75, 75), 30, 200)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    frame_area = small.shape[0] * small.shape[1]
    found: dict[tuple[int, ...], tuple[int, int, bool, tuple[int, int, int, int]]] = {}
    for contour in contours:
        _, (rect_w, rect_h), _ = cv2.minAreaRect(contour)
        if min(rect_w, rect_h) == 0:
            continue
        aspect = max(rect_w, rect_h) / min(rect_w, rect_h)
        area = rect_w * rect_h / frame_area
        if not (_PLATE_ASPECT[0] <= aspect <= _PLATE_ASPECT[1]):
            continue
        if not (_PLATE_AREA_FRACTION[0] <= area <= _PLATE_AREA_FRACTION[1]):
            continue

        x, y, w, h = (round(v / scale) for v in cv2.boundingRect(contour))
        row = _character_row(gray[y : y + h, x : x + w])
        if row is None:
            continue
        x0, y0, x1, y1, count = row
        margin = round(_LINE_MARGIN * (y1 - y0))
        box = (
            max(0, x + x0 - margin),
            max(0, y + y0 - margin),
            min(width, x + x1 + margin),
            min(height, y + y1 + margin),
        )
        # Measured from the character row, not the contour: the contour found
        # may be the white area below the band rather than the whole plate.
        top = y + y0
        zone_top = max(0, round(top - _BAND_ZONE * (y1 - y0)))
        mercosul = _is_blue(rgb[zone_top:top, x + x0 : x + x1])
        # The same plate is usually found by several nested contours.
        key = tuple(round(v / 20) for v in box)
        if key not in found or w * h > found[key][1]:
            found[key] = (count, w * h, mercosul, box)

    ranked = sorted(found.values(), key=lambda item: (item[0], item[1]), reverse=True)
    return [PlateLine(image.crop(box), mercosul) for _, _, mercosul, box in ranked[:_MAX_PLATES]]


def _character_row(gray: np.ndarray) -> tuple[int, int, int, int, int] | None:
    """Bounding box (x0, y0, x1, y1, count) of the row of characters, or None.

    Characters are dark blobs (Otsu) of plate-character size, of similar
    height and vertically aligned; at least ``_MIN_CHARS`` are required.
    """

    height, width = gray.shape[:2]
    if height < 10 or width < 30:
        return None
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    blobs = [
        (x, y, w, h)
        for x, y, w, h, _ in stats[1:]
        if _CHAR_HEIGHT[0] * height <= h <= _CHAR_HEIGHT[1] * height
        and _CHAR_WIDTH[0] * width <= w <= _CHAR_WIDTH[1] * width
        and h >= 0.8 * w
    ]
    if len(blobs) < _MIN_CHARS:
        return None
    median_h = float(np.median([h for _, _, _, h in blobs]))
    blobs = [b for b in blobs if abs(b[3] - median_h) <= 0.3 * median_h]
    center = float(np.median([y + h / 2 for _, y, _, h in blobs]))
    blobs = [b for b in blobs if abs(b[1] + b[3] / 2 - center) <= 0.25 * median_h]
    if len(blobs) < _MIN_CHARS:
        return None
    return (
        min(x for x, _, _, _ in blobs),
        min(y for _, y, _, _ in blobs),
        max(x + w for x, _, w, _ in blobs),
        max(y + h for _, y, _, h in blobs),
        len(blobs),
    )


def _blue_mask(rgb: np.ndarray) -> np.ndarray:
    return cv2.inRange(cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV), _BLUE_LOW, _BLUE_HIGH)


def _is_blue(rgb: np.ndarray) -> bool:
    if rgb.size == 0:
        return False
    return float(np.count_nonzero(_blue_mask(rgb))) / (rgb.shape[0] * rgb.shape[1]) >= (
        _MERCOSUL_BLUE_SHARE
    )
