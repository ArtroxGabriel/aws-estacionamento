"""Amazon Rekognition (DetectText) as the primary OCR engine on AWS."""

from __future__ import annotations

import io

from PIL import Image

from ocr.processor import OcrResult
from ocr.rekognition import MAX_LONG_SIDE, HybridOcr, prepare


def jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "JPEG")
    return buf.getvalue()


def line(text: str, height: float) -> dict:
    return {"Type": "LINE", "DetectedText": text, "Geometry": {"BoundingBox": {"Height": height}}}


class FakeRekognition:
    def __init__(self, detections: list[dict] | None = None, error: Exception | None = None):
        self.detections = detections or []
        self.error = error
        self.calls: list[bytes] = []

    def detect_text(self, Image: dict) -> dict:  # noqa: N803 - boto3 parameter name
        self.calls.append(Image["Bytes"])
        if self.error:
            raise self.error
        return {"TextDetections": self.detections}


class FakeTesseract:
    def __init__(self, text: str | None = "TESSERACT"):
        self.text = text
        self.calls = 0

    def __call__(self, _image: bytes) -> OcrResult:
        self.calls += 1
        if self.text is None:
            return OcrResult(ok=False, raw_text=None, error="no_text")
        return OcrResult(ok=True, raw_text=self.text, error=None)


def test_the_largest_plate_wins_over_cars_in_the_background():
    client = FakeRekognition(
        [
            line("DZP-2224", 0.02),  # car in the background, read first
            line("BA-SALVADOR", 0.03),
            line("JSG-9648", 0.06),  # car in front of the camera
        ]
    )
    fallback = FakeTesseract()

    result = HybridOcr(client, fallback=fallback)(jpeg(800, 600))

    assert result.ok and result.raw_text == "JSG-9648"
    assert fallback.calls == 0


def test_text_without_a_plate_falls_back_to_tesseract():
    client = FakeRekognition([line("RENAULT", 0.05), line("SALVADOR", 0.04)])
    fallback = FakeTesseract("ABC1D23")

    result = HybridOcr(client, fallback=fallback)(jpeg(800, 600))

    assert result.raw_text == "ABC1D23"
    assert fallback.calls == 1


def test_a_rekognition_error_falls_back_to_tesseract():
    client = FakeRekognition(error=RuntimeError("AccessDenied"))
    fallback = FakeTesseract("ABC1234")

    result = HybridOcr(client, fallback=fallback)(jpeg(800, 600))

    assert result.raw_text == "ABC1234"


def test_words_are_ignored_only_lines_count():
    client = FakeRekognition(
        [{"Type": "WORD", "DetectedText": "ABC1D23", "Geometry": {"BoundingBox": {"Height": 0.9}}}]
    )
    fallback = FakeTesseract(None)

    result = HybridOcr(client, fallback=fallback)(jpeg(800, 600))

    assert not result.ok


def test_prepare_rescales_large_photos_to_a_jpeg():
    prepared = prepare(jpeg(4000, 3000))

    with Image.open(io.BytesIO(prepared)) as img:
        assert img.format == "JPEG"
        assert max(img.size) == MAX_LONG_SIDE


def test_prepare_keeps_small_photos_size():
    with Image.open(io.BytesIO(prepare(jpeg(640, 480)))) as img:
        assert img.size == (640, 480)


def test_undecodable_bytes_go_straight_to_tesseract():
    client = FakeRekognition([line("ABC1D23", 0.1)])
    fallback = FakeTesseract(None)

    result = HybridOcr(client, fallback=fallback)(b"not an image")

    assert client.calls == []
    assert fallback.calls == 1
    assert not result.ok
