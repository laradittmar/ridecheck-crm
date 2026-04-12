from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import SystemSetting

router = APIRouter(prefix="/api/settings", tags=["settings"])

AI_ENABLED_KEY = "ai_enabled"


class AiEnabledRead(BaseModel):
    ai_enabled: bool


class AiEnabledPatch(BaseModel):
    ai_enabled: bool


def _parse_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def _ensure_ai_enabled_row(db: Session) -> SystemSetting:
    setting = db.get(SystemSetting, AI_ENABLED_KEY)
    if setting is None:
        setting = SystemSetting(key=AI_ENABLED_KEY, value="true")
        db.add(setting)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            setting = db.get(SystemSetting, AI_ENABLED_KEY)
            if setting is None:
                raise
        else:
            db.refresh(setting)
    return setting


@router.on_event("startup")
def seed_ai_enabled_setting() -> None:
    db = SessionLocal()
    try:
        _ensure_ai_enabled_row(db)
    finally:
        db.close()


@router.get("/ai-enabled", response_model=AiEnabledRead)
def get_ai_enabled() -> AiEnabledRead:
    db = SessionLocal()
    try:
        setting = _ensure_ai_enabled_row(db)
        return AiEnabledRead(ai_enabled=_parse_bool(setting.value))
    finally:
        db.close()


@router.patch("/ai-enabled", response_model=AiEnabledRead)
def patch_ai_enabled(payload: AiEnabledPatch) -> AiEnabledRead:
    db = SessionLocal()
    try:
        setting = _ensure_ai_enabled_row(db)
        setting.value = "true" if payload.ai_enabled else "false"
        db.add(setting)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail="No se pudo actualizar ai_enabled") from exc
        db.refresh(setting)
        return AiEnabledRead(ai_enabled=_parse_bool(setting.value))
    finally:
        db.close()
