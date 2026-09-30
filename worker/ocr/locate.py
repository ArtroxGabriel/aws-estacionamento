"""Plate localization — finds Mercosul plates in a photo before OCR.

Tesseract cannot read a plate that fills a small part of a car photo, and a
single global threshold rarely separates it from the car body. Every Mercosul
plate carries a saturated blue band across its top edge, which is easy to find
by color: the characters sit right below it, in a strip whose height is a fixed
fraction of the band width (the plate is 400 x 130 mm).

Pure module (no I/O): works on an in-memory ``PIL.Image``.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

__all__ = ["find_mercosul_plates"]

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


def find_mercosul_plates(image: Image.Image) -> list[Image.Image]:
    """Return crops of the character strip of each Mercosul plate found.

    Crops are ordered by band size (largest first) and capped at
    ``_MAX_PLATES``. An empty list means no Mercosul band was found.
    """

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, _BLUE_LOW, _BLUE_HIGH)
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
