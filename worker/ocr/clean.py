"""Plate_Normalizer — pure license-plate normalization.

Normalizes raw OCR text into a canonical Brazilian license plate. Supports the
Mercosul format (``ABC1D23``) and the legacy Old_Format (``ABC1234`` — the
hyphen in ``ABC-1234`` is stripped during normalization). This module performs
no I/O and never raises for domain outcomes: an unreadable plate is returned as
a ``PlateResult`` with ``ok=False`` and ``plate=None``.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Length of a plate in both formats once the Old_Format hyphen is stripped.
_MAX_LEN = 7


@dataclass(frozen=True)
class PlateResult:
    ok: bool
    plate: str | None  # canonical plate when ok
    reason: str | None  # "empty" | "no_match" when not ok


# Mercosul: letter-letter-letter-digit-letter-digit-digit -> ABC1D23
MERCOSUL = re.compile(r"^[A-Z]{3}[0-9][A-Z][0-9]{2}$")
# Old_Format: letter-letter-letter-digit-digit-digit-digit -> ABC1234 (canonical, no hyphen)
OLD = re.compile(r"^[A-Z]{3}[0-9]{4}$")

# Strips every character that is not an ASCII letter or digit (Req 5.1).
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")

# Positional templates: L = letter, D = digit. Each position of a plate can
# only hold one kind of character, which lets OCR confusions be undone.
_TEMPLATES = ("LLLDLDD", "LLLDDDD")  # Mercosul first: it is the current standard

# Characters Tesseract commonly confuses on the plate typeface, mapped to the
# kind the position requires.
_LETTER_TO_DIGIT = str.maketrans("OQDUILJZASGTB", "0000111245678")
_DIGIT_TO_LETTER = str.maketrans("01245678", "OIZASGTB")

# Upper bound on substitutions per plate. Real reads of the Mercosul typeface
# need up to 2 (e.g. "FTRS1O5" -> "FTR5I05"); a letters-only string needs at
# least 3 (three digit positions), so noise such as "BRASIL" never matches.
_MAX_CORRECTIONS = 2

# When the plate was located by its Mercosul band, the text comes from a tight
# crop and can only be a Mercosul plate, so one more swap is allowed: the
# typeface's 5, I and slashed 0 are read as S, 1 and O ("FTRS1O5" -> "FTR5I05").
_MAX_CORRECTIONS_LOCATED = 3

# Text printed only on Mercosul plates; when OCR reads it, the plate cannot be
# in the Old_Format.
_MERCOSUL_MARKERS = ("BRASIL", "MERCOSUL")


def normalize(raw: str, *, mercosul: bool = False) -> PlateResult:
    """Find a Mercosul or Old_Format plate in raw OCR text.

    OCR of a real plate returns surrounding text (the ``BRASIL`` header of a
    Mercosul plate, frame noise), so the plate is searched for rather than
    assumed to be the first 7 characters. Each line is tried first, so text
    from neighbouring lines cannot glue onto the plate, then the whole text
    (the plate itself may be split across lines). Within a candidate, every
    7-character window of the cleaned (alphanumeric-only, uppercase) text is
    tested left to right (Req 5.1).

    Exact matches are searched first across all candidates. Only when none
    exists is a positional correction tried: each window is read against the
    Mercosul (``LLLDLDD``) and Old_Format (``LLLDDDD``) templates, swapping
    commonly confused characters into the kind the position requires (``O`` ->
    ``0`` in a digit slot, ``1`` -> ``I`` in a letter slot, ...), with at most
    ``_MAX_CORRECTIONS`` swaps. The window needing the fewest swaps wins
    (earliest on a tie, Mercosul before Old_Format). When the text carries the
    Mercosul header (``BRASIL``/``MERCOSUL``), any Mercosul fit wins over an
    Old_Format one: ``FTRS1O5`` is ``FTR5I05`` on a Mercosul plate even though
    ``FTR5105`` needs one swap less. ``mercosul=True`` (the OCR_Processor
    located the plate by its Mercosul band) has the same effect and allows
    ``_MAX_CORRECTIONS_LOCATED`` swaps.

    Idempotent on already-valid plates: a value already in Mercosul_Format or
    Old_Format is returned unchanged (Req 5.6). Input with no matching window
    (including empty or non-alphanumeric-only input) yields an unreadable
    result with ``plate=None`` — never a partial or padded value (Req 5.4, 5.5).
    """
    candidates = [_clean(line) for line in raw.splitlines()] + [_clean(raw)]

    if not any(candidates):
        # Req 5.5: empty / no alphanumeric characters remaining.
        return PlateResult(ok=False, plate=None, reason="empty")

    for cleaned in candidates:
        plate = _find_plate(cleaned)
        if plate is not None:
            # Req 5.2 / 5.3 / 5.6: canonical format, returned as-is.
            return PlateResult(ok=True, plate=plate, reason=None)

    mercosul_header = any(marker in candidates[-1] for marker in _MERCOSUL_MARKERS)
    templates = (_TEMPLATES[0],) if mercosul or mercosul_header else _TEMPLATES
    max_swaps = _MAX_CORRECTIONS_LOCATED if mercosul else _MAX_CORRECTIONS
    for cleaned in candidates:
        plate = _find_corrected_plate(cleaned, templates, max_swaps)
        if plate is not None:
            return PlateResult(ok=True, plate=plate, reason=None)

    # Req 5.4: no window matches either pattern — unreadable, no padded value.
    return PlateResult(ok=False, plate=None, reason="no_match")


def _clean(text: str) -> str:
    """Remove disallowed characters and uppercase letters (Req 5.1)."""

    return _NON_ALNUM.sub("", text).upper()


def _find_plate(cleaned: str) -> str | None:
    """Return the first 7-character window matching a plate format, if any."""

    for start in range(len(cleaned) - _MAX_LEN + 1):
        window = cleaned[start : start + _MAX_LEN]
        if MERCOSUL.match(window) or OLD.match(window):
            return window
    return None


def _find_corrected_plate(cleaned: str, templates: tuple[str, ...], max_swaps: int) -> str | None:
    """Return the window that fits a plate template with the fewest swaps."""

    best: tuple[int, str] | None = None
    for start in range(len(cleaned) - _MAX_LEN + 1):
        window = cleaned[start : start + _MAX_LEN]
        for template in templates:
            fitted = _fit(window, template, max_swaps)
            if fitted is not None and (best is None or fitted[0] < best[0]):
                best = fitted
    return best[1] if best is not None else None


def _fit(window: str, template: str, max_swaps: int) -> tuple[int, str] | None:
    """Coerce ``window`` into ``template``; return (swaps, plate) or None."""

    chars: list[str] = []
    swaps = 0
    for char, kind in zip(window, template, strict=True):
        wanted_digit = kind == "D"
        if char.isdigit() == wanted_digit:
            chars.append(char)
            continue
        table = _LETTER_TO_DIGIT if wanted_digit else _DIGIT_TO_LETTER
        swapped = char.translate(table)
        if swapped == char:
            return None  # no plausible confusion for this character
        chars.append(swapped)
        swaps += 1
        if swaps > max_swaps:
            return None
    return swaps, "".join(chars)
