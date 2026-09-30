"""OCR_Processor example tests.

Tesseract is stubbed except in the test that is skipped when the binary is not
installed.
"""

from __future__ import annotations

import io
import shutil
import time

import pytest
from PIL import Image, ImageDraw, ImageFont

from ocr import processor
from ocr.clean import normalize


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_timeout_returns_promptly(monkeypatch):
    """Regression: the timeout returned inside `with ThreadPoolExecutor`, whose
    exit waits for the Tesseract call, so a hung OCR blocked the worker."""

    def slow_tesseract(image, *args, timeout=0, **kwargs):
        if timeout:
            time.sleep(timeout)
            raise RuntimeError("Tesseract process timeout")
        time.sleep(3)
        return "ABC1D23"

    monkeypatch.setattr(processor.pytesseract, "image_to_string", slow_tesseract)

    started = time.monotonic()
    result = processor.extract_text(png_bytes(), timeout_s=0.2)
    elapsed = time.monotonic() - started

    assert result.error == "timeout"
    assert elapsed < 1.5


def test_located_plate_is_read_first_and_flags_mercosul(monkeypatch):
    img = Image.new("RGB", (1600, 900), (225, 225, 225))
    draw = ImageDraw.Draw(img)
    draw.rectangle([500, 300, 1300, 540], fill="white")
    draw.rectangle([500, 300, 1300, 410], fill=(30, 70, 190))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    sizes = []

    def fake(image, *args, config="", **kwargs):
        sizes.append(image.size)
        return "FTRS1O5" if len(sizes) == 1 else ""

    monkeypatch.setattr(processor.pytesseract, "image_to_string", fake)

    result = processor.extract_text(buf.getvalue())

    assert (result.ok, result.raw_text, result.mercosul) == (True, "FTRS1O5", True)
    assert sizes[0][1] == 2 * 50 + 100  # strip at 100 px + white margins
    assert len(sizes) == 6  # 3 modes on the strip, 3 on the whole photo


def test_whole_photo_is_rescaled_into_range():
    small = processor._preprocess(Image.new("RGB", (400, 200), "white"))
    large = processor._preprocess(Image.new("RGB", (4000, 3000), "white"))
    fits = processor._preprocess(Image.new("RGB", (1920, 1080), "white"))

    assert (small.size, large.size, fits.size) == ((1000, 500), (2000, 1500), (1920, 1080))


def test_joins_the_output_of_every_segmentation_mode(monkeypatch):
    """Regression: with only --psm 11, a binarized Mercosul plate crop came
    back empty although --psm 6/7 read it (found against real Tesseract)."""
    outputs = {"7": "ABC1D23\n", "6": "", "11": "BRASIL\n"}
    timeouts = []

    def fake(image, *args, config="", timeout=0, **kwargs):
        timeouts.append(timeout)
        return outputs[config.split()[1]]

    monkeypatch.setattr(processor.pytesseract, "image_to_string", fake)

    result = processor.extract_text(png_bytes())

    assert (result.ok, result.raw_text) == (True, "ABC1D23\nBRASIL")
    assert len(timeouts) == 3
    assert all(0 < t <= 10.0 for t in timeouts)


def test_timeout_budget_is_shared_across_modes(monkeypatch):
    def slow(image, *args, timeout=0, **kwargs):
        time.sleep(0.15)
        return "XYZ"

    monkeypatch.setattr(processor.pytesseract, "image_to_string", slow)

    assert processor.extract_text(png_bytes(), timeout_s=0.2).error == "timeout"


def test_otsu_splits_a_bimodal_histogram():
    histogram = [0] * 256
    histogram[40] = 300  # dark characters
    histogram[210] = 700  # light plate background

    assert 40 <= processor.otsu_threshold(histogram) < 210


def test_otsu_follows_an_overexposed_image():
    """A fixed 128 cut-off turns an overexposed image all white; Otsu moves
    the cut-off up to where the characters and background actually are."""
    histogram = [0] * 256
    histogram[150] = 300  # characters, lighter than 128
    histogram[230] = 700  # background

    assert 150 <= processor.otsu_threshold(histogram) < 230


def test_otsu_on_empty_histogram():
    assert processor.otsu_threshold([0] * 256) == 127


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract binary not installed")
def test_real_tesseract_reads_a_rendered_mercosul_plate():
    img = Image.new("RGB", (520, 170), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 519, 40], fill=(0, 51, 160))
    draw.text((230, 8), "BRASIL", fill="white", font=ImageFont.load_default(size=22))
    draw.rectangle([2, 2, 517, 167], outline="black", width=4)
    draw.text((40, 50), "ABC1D23", fill="black", font=ImageFont.load_default(size=100))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")

    result = processor.extract_text(buf.getvalue())

    assert result.ok, result.error
    assert normalize(result.raw_text).plate == "ABC1D23"


def test_empty_output_is_no_text(monkeypatch):
    monkeypatch.setattr(processor.pytesseract, "image_to_string", lambda *a, **k: "  \n")

    assert processor.extract_text(png_bytes()).error == "no_text"


def test_undecodable_bytes_skip_tesseract(monkeypatch):
    def must_not_run(*args, **kwargs):
        pytest.fail("Tesseract must not run on undecodable bytes")

    monkeypatch.setattr(processor.pytesseract, "image_to_string", must_not_run)

    assert processor.extract_text(b"not an image").error == "decode"
