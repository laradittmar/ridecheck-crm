from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel, Field


class ScheduleCheckIn(BaseModel):
    address: str = Field(min_length=1)
    preferred_day: date
    preferred_time: time
    zone_group: str | None = Field(default=None, max_length=50)
    zone_detail: str | None = Field(default=None, max_length=80)
    distance_km: float | None = Field(default=None, ge=0)
    is_holiday: bool = False
    exclude_revision_id: int | None = None


class ScheduleSlotOut(BaseModel):
    start: str
    end: str


class ScheduleConflictOut(BaseModel):
    source: str
    source_id: int
    start: str
    end: str
    label: str


class ScheduleCheckOut(BaseModel):
    valid: bool
    suggested_slots: list[str]
    approval_tag: str = "Esperando aprobación"
    requested_slot: ScheduleSlotOut
    business_hours: str
    service_minutes: int = 45
    buffer_minutes: int = 15
    travel_minutes: int = 0
    total_slot_minutes: int = 60
    conflicts: list[ScheduleConflictOut] = []
    reasons: list[str] = []
    rules_applied: list[str] = []


class ScheduleSlotsOut(BaseModel):
    preferred_day: date
    business_hours: str
    slots: list[str]
    rules_applied: list[str] = []
