"""OCR_Processor example tests (Tesseract is stubbed; no binary required)."""

from __future__ import annotations

import io
import time

import pytest
from PIL import Image

from ocr import processor


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


def test_success_returns_raw_text(monkeypatch):
    calls = {}

    def fake(image, *args, **kwargs):
        calls.update(kwargs)
        return "ABC1D23\n"

    monkeypatch.setattr(processor.pytesseract, "image_to_string", fake)

    result = processor.extract_text(png_bytes())

    assert (result.ok, result.raw_text) == (True, "ABC1D23\n")
    assert calls["timeout"] == 10.0


def test_empty_output_is_no_text(monkeypatch):
    monkeypatch.setattr(processor.pytesseract, "image_to_string", lambda *a, **k: "  \n")

    assert processor.extract_text(png_bytes()).error == "no_text"


def test_undecodable_bytes_skip_tesseract(monkeypatch):
    def must_not_run(*args, **kwargs):
        pytest.fail("Tesseract must not run on undecodable bytes")

    monkeypatch.setattr(processor.pytesseract, "image_to_string", must_not_run)

    assert processor.extract_text(b"not an image").error == "decode"
