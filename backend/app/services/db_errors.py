from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session


def commit_or_400(db: Session, detail: str = "Datos inválidos para persistir") -> None:
    try:
        db.commit()
    except (DataError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=detail) from exc
