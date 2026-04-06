from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, Field


ThreadRevisionStatus = Literal["draft", "collecting_data", "booked", "completed"]


class ThreadRevisionCreateIn(BaseModel):
    thread_id: int
    candidate_id: int


class ThreadRevisionCreateOut(BaseModel):
    revision_id: int


class ThreadRevisionPatch(BaseModel):
    status: ThreadRevisionStatus | None = None
    buyer_name: str | None = Field(default=None, max_length=120)
    buyer_phone: str | None = Field(default=None, max_length=40)
    buyer_email: str | None = Field(default=None, max_length=120)
    seller_type: str | None = Field(default=None, max_length=30)
    seller_name: str | None = Field(default=None, max_length=120)
    address: str | None = None
    scheduled_date: date | None = None
    scheduled_time: time | None = None
    tipo_vehiculo: str | None = Field(default=None, max_length=30)
    marca: str | None = Field(default=None, max_length=50)
    modelo: str | None = Field(default=None, max_length=50)
    anio: int | None = None
    publication_url: str | None = None


class ThreadRevisionOut(BaseModel):
    id: int
    thread_id: int
    candidate_id: int | None = None
    status: ThreadRevisionStatus
    buyer_name: str | None = None
    buyer_phone: str | None = None
    buyer_email: str | None = None
    seller_type: str | None = None
    seller_name: str | None = None
    address: str | None = None
    scheduled_date: date | None = None
    scheduled_time: time | None = None
    tipo_vehiculo: str | None = None
    marca: str | None = None
    modelo: str | None = None
    anio: int | None = None
    publication_url: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
