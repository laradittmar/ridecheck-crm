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
