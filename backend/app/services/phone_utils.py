from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import String, func


PHONE_ERROR_DETAIL = "telefono inválido: debe tener entre 8 y 15 dígitos (puede iniciar con +)"


def normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if raw == "":
        return None

    cleaned = re.sub(r"[\s\-\(\)]", "", raw)
    if cleaned == "":
        return None

    has_plus = cleaned.startswith("+")
    digits = cleaned[1:] if has_plus else cleaned
    if digits == "" or not digits.isdigit():
        raise HTTPException(status_code=422, detail=PHONE_ERROR_DETAIL)
    if len(digits) < 8 or len(digits) > 15:
        raise HTTPException(status_code=422, detail=PHONE_ERROR_DETAIL)
    return f"+{digits}" if has_plus else digits


def normalize_phone_or_422(value: str | None) -> str | None:
    return normalize_phone(value)


def normalized_phone_sql(column):
    compact = func.regexp_replace(func.coalesce(column, ""), r"[\s\-\(\)]", "", "g")
    return func.cast(compact, String)


# ── PRELAUNCH-MINIMUM-LAUNCH (D): the canonical phone IDENTITY ─────────────────────────
#
# `normalize_phone` is the DISPLAY form and keeps a leading '+', so "+549…" and "549…" are
# different strings for the same line. The live Wild created two leads for one customer
# through a read-empty/read-empty/double-insert race, and a uniqueness rule on the display
# column could not have caught the '+' variant. Identity is therefore DIGITS ONLY, and the
# database enforces it on exactly this expression (migration `20261102_lead_phone_identity`,
# index `uq_leads_phone_identity`). The literals below are that index's own; a test pins them.
#
# Deliberately NOT a business normalisation: a national form (11…) and an E.164 form
# (5491…) of one Argentine line remain different identities. Deciding that they are the same
# is an owner rule, not a regex.
PHONE_IDENTITY_SQL = "regexp_replace(telefono, '[^0-9]', '', 'g')"
#: R1 (review F-11): the identity itself must be non-empty, so a digit-free value ("-", "s/n")
#: has NO identity and behaves like NULL — exactly what `canonical_phone_identity` returns.
PHONE_IDENTITY_PREDICATE = "regexp_replace(telefono, '[^0-9]', '', 'g') <> ''"


def canonical_phone_identity(value: str | None) -> str | None:
    """The digits-only identity of a phone value, or None when it carries no digits.

    The same function as the index expression: every non-digit removed. It never validates
    (that is `normalize_phone`'s job at the API boundary) and never adds or removes a
    country code."""
    if value is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return digits or None
