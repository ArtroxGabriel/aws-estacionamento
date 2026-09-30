"""Plate localization by the Mercosul blue band (OpenCV, no Tesseract)."""

from __future__ import annotations

from PIL import Image, ImageDraw

from ocr.locate import find_mercosul_plates

BAND_BLUE = (30, 70, 190)


def car_photo(*, band: tuple[int, int, int, int] | None = None, badge: bool = False) -> Image.Image:
    img = Image.new("RGB", (1600, 900), (225, 225, 225))  # white car body
    draw = ImageDraw.Draw(img)
    if band is not None:
        x, y, w, h = band
        draw.rectangle([x, y, x + w, y + int(0.3 * w)], fill="white")  # plate
        draw.rectangle([x, y, x + w, y + h], fill=BAND_BLUE)
        draw.text((x + w // 2, y + 5), "BRASIL", fill="white")  # gap in the band
    if badge:
        draw.rectangle([1300, 600, 1400, 660], fill=BAND_BLUE)  # e.g. "FLEX" badge
    return img


def test_finds_the_strip_below_the_band():
    crops = find_mercosul_plates(car_photo(band=(500, 300, 800, 110)))

    assert len(crops) == 1
    width, height = crops[0].size
    assert width == 801
    assert 0.2 * width < height < 0.35 * width


def test_no_band_means_no_plate():
    assert find_mercosul_plates(car_photo()) == []


def test_ignores_small_or_square_blue_shapes():
    assert find_mercosul_plates(car_photo(badge=True)) == []


def test_band_is_found_next_to_a_blue_badge():
    crops = find_mercosul_plates(car_photo(band=(500, 300, 800, 110), badge=True))
    assert len(crops) == 1
