"""PRELAUNCH-MINIMUM-LAUNCH-VEHICLE-IDENTITY — the ONE rule for `Kilometraje` and
`Dominio / patente`.

Both are optional facts about the vehicle a customer asked to have inspected. Every entry
point — the Booking Flow, the API schemas, the CRM form — goes through these two functions, so
a value cannot be valid in one place and refused, or differently normalised, in another.

  kilometraje  an integer number of kilometres. Blank means NULL, never zero; an explicit 0 is
               a real reading (a new car) and is kept. Accepted as plain digits or as valid
               Argentine thousands grouping (owner decision D-3): `85.000` is 85000 and
               `1.234.567` is 1234567. A grouped value starts with a non-zero digit and every
               later group is exactly three digits. Anything else is refused rather than
               guessed at (`85.00`, `85..000`, `85,000`, `85,5`, `0.000`): an odometer reading
               must not be invented. The upper bound is the column's (PostgreSQL `integer`), not
               a business opinion. The result is always the integer: grouping is input only.
  dominio      the canonical searchable form: surrounding whitespace trimmed, upper-cased,
               display separators (spaces, hyphens, dots) removed. `ABC123` and `AB123CD` are
               the common Argentine forms but the rule assumes neither: any ASCII alphanumeric
               value up to `DOMINIO_MAX` is accepted. Deliberately NOT unique — one vehicle is
               inspected many times.

Error messages are fixed text. They reach logs (`BOOKING_FLOW_VALIDATION_ERROR`), so they must
never quote the value.
"""
from __future__ import annotations

import re
from typing import Optional

#: PostgreSQL `integer`. A storage bound, not a plausibility limit.
KILOMETRAJE_MAX = 2_147_483_647
#: Longest canonical plate (`revisions.dominio` is VARCHAR(16)).
DOMINIO_MAX = 16

_DIGITS = re.compile(r"[0-9]+")
#: Argentine thousands grouping: `85.000`, `1.234.567`. Never a comma, never a leading zero.
_GROUPED = re.compile(r"[1-9][0-9]{0,2}(?:\.[0-9]{3})+")
_CANONICAL_DOMINIO = re.compile(r"[A-Z0-9]+")
#: Whitespace, ASCII hyphen, dot, and the Unicode dashes phone keyboards substitute for "-".
_SEPARATORS = re.compile(r"[\s.\-‐-―]+")

KILOMETRAJE_INVALID = "kilometraje inválido: debe ser un número entero de kilómetros"
DOMINIO_INVALID = "dominio inválido: usá solo letras y números"


def parse_kilometraje(value) -> Optional[int]:
    """The canonical kilometres, or None for blank. Raises `ValueError` otherwise."""
    if value is None:
        return None
    if type(value) is int:                    # exact: a bool is not a reading
        number = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if _GROUPED.fullmatch(text):
            text = text.replace(".", "")
        if len(text) > len(str(KILOMETRAJE_MAX)) or not _DIGITS.fullmatch(text):
            raise ValueError(KILOMETRAJE_INVALID)
        number = int(text)
    else:
        raise ValueError(KILOMETRAJE_INVALID)
    if number < 0 or number > KILOMETRAJE_MAX:
        raise ValueError(KILOMETRAJE_INVALID)
    return number


def normalize_dominio(value) -> Optional[str]:
    """The canonical plate, or None for blank. Raises `ValueError` otherwise."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(DOMINIO_INVALID)
    canonical = _SEPARATORS.sub("", value).upper()
    if not canonical:
        return None
    if len(canonical) > DOMINIO_MAX or not _CANONICAL_DOMINIO.fullmatch(canonical):
        raise ValueError(DOMINIO_INVALID)
    return canonical


def format_kilometraje(value) -> str:
    """`85000` → `85.000 km`; `0` → `0 km`; None → "". A caller shows the label only when this
    is non-empty, so a blank reading can never read as `0 km`. A stored value that is not a
    reading is returned as it is, for the caller to escape: display never raises."""
    try:
        number = parse_kilometraje(value)
    except ValueError:
        return str(value)
    if number is None:
        return ""
    return f"{number:,}".replace(",", ".") + " km"
