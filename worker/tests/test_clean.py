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

from ocr.clean import has_exact_plate, normalize

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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Real Tesseract reads of a Mercosul "FTR5I05" photo (typeface confusions).
        ("4\n\nFFTRS105\nBRASIL", "FTR5I05"),
        ("BRASIL\nLFTR5LO5", "FTR5L05"),  # I read as L: a letter slot, not fixable
        # Without the BRASIL header, fewest swaps wins: Old_Format needs one.
        ("FFTRS105", "FTR5105"),
        ("ABC12S4", "ABC1254"),
        ("ABCD123", "ABC0123"),
    ],
)
def test_positional_correction(raw, expected):
    result = normalize(raw)
    assert (result.ok, result.plate) == (True, expected)


def test_located_mercosul_plate_allows_a_third_swap():
    """Real read of the band-located strip of a Mercosul "FTR5I05" photo."""
    assert normalize("FTRS1O5", mercosul=True).plate == "FTR5I05"
    # Without the hint, 3 swaps are too many for Mercosul; Old_Format needs 2.
    assert normalize("FTRS1O5").plate == "FTR5105"


def test_located_mercosul_plate_is_coerced_to_mercosul_shape():
    # Without the hint this is the Old_Format "ABC1254" (1 swap).
    assert normalize("ABC12S4", mercosul=True).plate == "ABC1Z54"


@pytest.mark.parametrize(
    "raw",
    [
        # Real read of a Mercosul "FJB4E12" photo on a blue car: joining lines
        # and keeping "BRASIL" produced the false plate "ASI1B72".
        "MERCOSUL B RASIL\nB 7 2\n4\n\nBRASIL\n\nMERCOSUL\n\nHO\n\nFJB4E2\n\nBR\n\nS",
        # Real reads of separate Tesseract modes: gluing them gave "EFB4E12".
        "FRE\nFRE\nFB4E12",
    ],
)
def test_never_glues_lines_or_header_words_into_a_plate(raw):
    assert normalize(raw).plate is None


def test_most_frequent_exact_read_wins():
    """Real reads of a Mercosul "FJB4E12" photo: one crop misread J as I."""
    assert normalize("DB\nDB\nFIB4E12\nFJB4E12\nFJB4E12", mercosul=True).plate == "FJB4E12"
    assert normalize("ABC1234\nABC1D23").plate == "ABC1234"  # tie -> earliest


def test_real_read_of_a_tilted_old_format_plate():
    """Real read of an "HIG-1972" photo tilted ~19 degrees (I read as 1)."""
    raw = "H1G1972\nH1G1972\nVSS\nWG\nFEE\n\nE\n\n3\n\n0\n\n12\n\nH\n\nOY"
    assert normalize(raw).plate == "HIG1972"


def test_has_exact_plate():
    assert has_exact_plate("FE\nFJB4E12")
    assert not has_exact_plate("FTRS1O5\nBRASIL")


def test_exact_match_beats_correction():
    # "0BC1D23" (zero) is one swap from a plate; the next line is exact.
    assert normalize("0BC1D23\nABC1D23").plate == "ABC1D23"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "---",
        "BRASIL",
        "AB1234",
        "N\nBRASIL\nOEET\n\nY\n\nBRASIL",  # real read with no plate in it
        "ABCXYZW",  # needs 3 swaps
    ],
)
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
