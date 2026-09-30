"""Plate_Normalizer tests.

Feature: python-ocr-worker, Property 1: Normalization output invariant.
Feature: python-ocr-worker, Property 2: Valid plates are accepted, canonicalized,
and idempotent.
Feature: python-ocr-worker, Property 3: Unmatchable input yields an unreadable
result with no padded value.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ocr.clean import normalize

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"
letter = st.sampled_from(LETTERS)
digit = st.sampled_from(DIGITS)

mercosul = st.tuples(letter, letter, letter, digit, letter, digit, digit).map("".join)
old_format = st.tuples(letter, letter, letter, digit, digit, digit, digit).map("".join)
valid_plate = st.one_of(mercosul, old_format)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ABC1D23", "ABC1D23"),
        ("abc-1234", "ABC1234"),
        ("ABC 1D23\n", "ABC1D23"),
        # Regression: realistic OCR output of a full Mercosul plate. The old
        # implementation truncated to the first 7 chars ("BRASILA").
        ("BRASIL\nABC1D23", "ABC1D23"),
        ("BRASIL\nABC 1D23\n", "ABC1D23"),
        ("| BR ABC-1234 |", "ABC1234"),
        ("XABC1234", "ABC1234"),
    ],
)
def test_extracts_plate_from_ocr_text(raw, expected):
    result = normalize(raw)
    assert (result.ok, result.plate) == (True, expected)


@pytest.mark.parametrize("raw", ["", "   ", "---", "BRASIL", "AB1234", "ABCD123"])
def test_unreadable(raw):
    result = normalize(raw)
    assert (result.ok, result.plate) == (False, None)


@given(st.text())
def test_output_invariant(raw):
    result = normalize(raw)
    if result.ok:
        assert re.fullmatch(r"[A-Z0-9]{7}", result.plate)
        assert normalize(result.plate) == result
    else:
        assert result.plate is None


@given(valid_plate)
def test_valid_plates_are_idempotent(plate):
    result = normalize(plate)
    assert (result.ok, result.plate) == (True, plate)
    assert 1 <= len(result.plate) <= 16


@given(st.text(alphabet=LETTERS + " -\n"))
def test_letters_only_never_match(raw):
    result = normalize(raw)
    assert (result.ok, result.plate) == (False, None)
