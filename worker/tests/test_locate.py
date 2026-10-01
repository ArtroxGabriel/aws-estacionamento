"""Plate localization by the Mercosul blue band (OpenCV, no Tesseract)."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from ocr.locate import find_mercosul_plates, find_plate_lines

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


def plate_photo(*, body, plate_bg, band: bool, text: str) -> Image.Image:
    """A car body with a 400x130 mm plate: optional Mercosul band on top,
    otherwise an Old_Format city line."""
    img = Image.new("RGB", (1600, 900), body)
    draw = ImageDraw.Draw(img)
    x, y, w, h = 400, 330, 800, 260
    draw.rectangle([x, y, x + w, y + h], fill=plate_bg, outline=(20, 20, 20), width=10)
    if band:
        draw.rectangle([x + 10, y + 10, x + w - 10, y + 85], fill=BAND_BLUE)
    else:
        draw.text(
            (x + w // 2, y + 38),
            "SAO PAULO - SP",
            fill=(30, 30, 30),
            anchor="mm",
            font=ImageFont.load_default(size=36),
        )
    draw.text(
        (x + w // 2, y + 165),
        text,
        fill=(25, 25, 25),
        anchor="mm",
        font=ImageFont.load_default(size=150),
    )
    return img


def test_finds_the_character_row_of_an_old_format_plate():
    lines = find_plate_lines(
        plate_photo(body=(120, 20, 25), plate_bg=(205, 205, 200), band=False, text="ABC-1234")
    )

    assert len(lines) == 1
    assert lines[0].mercosul is False
    width, height = lines[0].image.size
    assert 3 < width / height < 8  # the character row only, not the city line


def test_flags_mercosul_on_a_blue_car_where_the_band_locator_fails():
    """Regression: on a blue car the band merges with the body, so the band
    locator finds nothing; the plate-shape locator still does."""
    photo = plate_photo(body=BAND_BLUE, plate_bg="white", band=True, text="FJB4E12")

    assert find_mercosul_plates(photo) == []
    lines = find_plate_lines(photo)
    assert len(lines) == 1 and lines[0].mercosul is True


def test_plate_shaped_regions_without_characters_are_ignored():
    img = Image.new("RGB", (1600, 900), (120, 20, 25))
    draw = ImageDraw.Draw(img)
    for i in range(6):  # grille slots and an empty plate frame
        draw.rectangle([200 + i * 200, 700, 360 + i * 200, 760], outline=(60, 60, 60), width=6)
    draw.rectangle([400, 300, 1200, 560], fill=(205, 205, 200), outline=(20, 20, 20), width=10)

    assert find_plate_lines(img) == []


def test_finds_a_tilted_old_format_plate():
    """Regression: a real "HIG-1972" photo tilted ~19 degrees was not found,
    because its characters are not on a horizontal row until straightened."""
    photo = plate_photo(body=(60, 60, 65), plate_bg=(205, 205, 200), band=False, text="ABC-1234")
    tilted = photo.rotate(15, resample=Image.Resampling.BICUBIC, fillcolor=(60, 60, 65))

    lines = find_plate_lines(tilted)

    assert len(lines) == 1 and lines[0].mercosul is False
    width, height = lines[0].image.size
    assert width / height > 3  # an upright row, not a tilted bounding box


def test_bluish_shadow_or_narrow_badge_is_not_a_band():
    """Regression: a dark bluish shadow on the bumper of an Old_Format photo
    (S ~95, V ~65) passed as a Mercosul band and forced the Mercosul format."""
    img = Image.new("RGB", (740, 420), (90, 90, 95))
    draw = ImageDraw.Draw(img)
    draw.rectangle([70, 394, 142, 410], fill=(40, 50, 75))  # shadow, aspect 4.5
    draw.rectangle([400, 100, 460, 112], fill=BAND_BLUE)  # bright but 8% wide

    assert find_mercosul_plates(img) == []
