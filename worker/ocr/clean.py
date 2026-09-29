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

# Maximum number of alphanumeric characters retained from the raw OCR text.
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
    """Strip non-alnum, uppercase letters, cap at 7 chars, match a format.

    Idempotent on already-valid plates: a value already in Mercosul_Format or
    Old_Format is returned unchanged (Req 5.6). Input matching neither pattern
    (including empty or non-alphanumeric-only input) yields an unreadable result
    with ``plate=None`` — never a partial or padded value (Req 5.4, 5.5).
    """
    # Req 5.1: remove disallowed chars, uppercase, cap at 7 characters.
    cleaned = _NON_ALNUM.sub("", raw).upper()[:_MAX_LEN]

    if not cleaned:
        # Req 5.5: empty / no alphanumeric characters remaining.
        return PlateResult(ok=False, plate=None, reason="empty")

    # Req 5.2 / 5.3 / 5.6: canonical formats are returned as-is (idempotent).
    if MERCOSUL.match(cleaned) or OLD.match(cleaned):
        return PlateResult(ok=True, plate=cleaned, reason=None)

    # Req 5.4: matches neither pattern — unreadable, no padded/partial value.
    return PlateResult(ok=False, plate=None, reason="no_match")
