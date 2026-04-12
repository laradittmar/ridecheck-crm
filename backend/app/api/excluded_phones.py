from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal
from ..models import ExcludedPhone

router = APIRouter(prefix="/api/excluded-phones", tags=["excluded-phones"])


class ExcludedPhoneRead(BaseModel):
    id: int
    phone: str
    label: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ExcludedPhoneCreate(BaseModel):
    phone: str = Field(..., min_length=1, max_length=40)
    label: str | None = Field(default=None, max_length=100)


class ExcludedPhoneDeleteResult(BaseModel):
    deleted: bool


class ExcludedPhoneCheckResult(BaseModel):
    excluded: bool


def _normalize_phone(phone: str) -> str:
    normalized = str(phone or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Phone is required")
    if len(normalized) > 40:
        raise HTTPException(status_code=400, detail="Phone is too long")
    return normalized


@router.get("", response_model=list[ExcludedPhoneRead])
def list_excluded_phones() -> list[ExcludedPhoneRead]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ExcludedPhone)
            .order_by(ExcludedPhone.created_at.desc(), ExcludedPhone.id.desc())
            .all()
        )
        return [ExcludedPhoneRead.model_validate(row) for row in rows]
    finally:
        db.close()


@router.post("", response_model=ExcludedPhoneRead, status_code=201)
def create_excluded_phone(payload: ExcludedPhoneCreate) -> ExcludedPhoneRead:
    db = SessionLocal()
    try:
        row = ExcludedPhone(
            phone=_normalize_phone(payload.phone),
            label=(payload.label or "").strip() or None,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail="Phone already excluded") from exc
        db.refresh(row)
        return ExcludedPhoneRead.model_validate(row)
    finally:
        db.close()


@router.delete("/{phone}", response_model=ExcludedPhoneDeleteResult)
def delete_excluded_phone(phone: str) -> ExcludedPhoneDeleteResult:
    db = SessionLocal()
    try:
        normalized = _normalize_phone(phone)
        row = db.query(ExcludedPhone).filter(ExcludedPhone.phone == normalized).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Excluded phone not found")
        db.delete(row)
        db.commit()
        return ExcludedPhoneDeleteResult(deleted=True)
    finally:
        db.close()


@router.get("/check/{phone}", response_model=ExcludedPhoneCheckResult)
def check_excluded_phone(phone: str) -> ExcludedPhoneCheckResult:
    db = SessionLocal()
    try:
        normalized = _normalize_phone(phone)
        exists = (
            db.query(ExcludedPhone.id)
            .filter(ExcludedPhone.phone == normalized)
            .first()
            is not None
        )
        return ExcludedPhoneCheckResult(excluded=exists)
    finally:
        db.close()
