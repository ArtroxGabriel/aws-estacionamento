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


def normalize(raw: str) -> PlateResult:
    """Find a Mercosul or Old_Format plate in raw OCR text.

    OCR of a real plate returns surrounding text (the ``BRASIL`` header of a
    Mercosul plate, frame noise), so the plate is searched for rather than
    assumed to be the first 7 characters. Each line is tried first, so text
    from neighbouring lines cannot glue onto the plate, then the whole text
    (the plate itself may be split across lines). Within a candidate, every
    7-character window of the cleaned (alphanumeric-only, uppercase) text is
    tested left to right (Req 5.1).

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
