"""Amazon Rekognition (DetectText) as the primary OCR engine on AWS."""

from __future__ import annotations

import io

from PIL import Image

from ocr.clean import normalize
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
        self.mercosul = False
        self.calls = 0

    def __call__(self, _image: bytes) -> OcrResult:
        self.calls += 1
        if self.text is None:
            return OcrResult(ok=False, raw_text=None, error="no_text")
        return OcrResult(ok=True, raw_text=self.text, error=None, mercosul=self.mercosul)


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


def test_country_name_on_another_line_unlocks_its_format():
    # Rekognition returns "ARGENTINA" and the plate as separate lines.
    client = FakeRekognition([line("ARGENTINA", 0.02), line("MWV 724", 0.06)])

    result = HybridOcr(client, fallback=FakeTesseract(None))(jpeg(800, 600))

    assert result.ok
    assert normalize(result.raw_text).plate == "MWV724"


def test_six_characters_without_the_country_name_fall_back():
    client = FakeRekognition([line("MWV 724", 0.06)])
    fallback = FakeTesseract(None)

    HybridOcr(client, fallback=fallback)(jpeg(800, 600))

    assert fallback.calls == 1


def test_tesseract_fallback_keeps_only_safe_reads():
    """Corrections can turn Tesseract noise into a valid but wrong plate."""
    client = FakeRekognition([line("BRASIL", 0.05)])

    def run(text: str, mercosul: bool = False) -> OcrResult:
        fallback = FakeTesseract(text)
        fallback.mercosul = mercosul
        return HybridOcr(client, fallback=fallback)(jpeg(800, 600))

    assert run("noise\nABC-1234").raw_text == "ABC1234"  # exact
    assert not run("ABC12S4").ok  # one correction, no Mercosul band
    # One correction on a located Mercosul plate: the I read as 1 (LSN4I49).
    assert run("LSN4149", mercosul=True).raw_text == "LSN4I49"
    assert not run("PII7AII", mercosul=True).ok  # noise needing several corrections


class SequenceRekognition:
    """Returns one response per call: the photo first, then the framed photo."""

    def __init__(self, *responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[bytes] = []

    def detect_text(self, Image: dict) -> dict:  # noqa: N803 - boto3 parameter name
        self.calls.append(Image["Bytes"])
        return {"TextDetections": self.responses[len(self.calls) - 1]}


def test_a_close_up_is_retried_framed():
    client = SequenceRekognition([line("BRASIL", 0.3)], [line("LSN4I49", 0.2)])
    fallback = FakeTesseract(None)

    result = HybridOcr(client, fallback=fallback)(jpeg(600, 200))

    assert result.raw_text == "LSN4I49"
    assert len(client.calls) == 2
    with Image.open(io.BytesIO(client.calls[1])) as framed_img:
        assert framed_img.size == (1200, 400)  # twice the photo, with margin
    assert fallback.calls == 0


def test_a_plate_found_at_first_is_not_retried():
    client = SequenceRekognition([line("ABC1D23", 0.1)])

    HybridOcr(client, fallback=FakeTesseract(None))(jpeg(800, 600))

    assert len(client.calls) == 1
