from __future__ import annotations

from pydantic import BaseModel, Field


class ScheduleCheckIn(BaseModel):
    address: str = Field(min_length=1)
    preferred_day: str = Field(min_length=1)
    preferred_time: str = Field(min_length=1)


class ScheduleCheckOut(BaseModel):
    valid: bool
    suggested_slots: list[str]
