from __future__ import annotations

from datetime import date, time

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas.schedule import ScheduleCheckIn, ScheduleCheckOut, ScheduleSlotsOut
from ..services.schedule import ScheduleService

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


def get_schedule_service(db: Session = Depends(get_db)) -> ScheduleService:
    return ScheduleService(db=db)


@router.post("/check", response_model=ScheduleCheckOut)
def check_schedule(
    payload: ScheduleCheckIn,
    service: ScheduleService = Depends(get_schedule_service),
):
    return service.check(payload)


@router.get("/slots", response_model=ScheduleSlotsOut)
def list_schedule_slots(
    preferred_day: date,
    address: str = Query(..., min_length=1),
    zone_group: str | None = Query(default=None),
    zone_detail: str | None = Query(default=None),
    distance_km: float | None = Query(default=None, ge=0),
    is_holiday: bool = Query(default=False),
    exclude_revision_id: int | None = Query(default=None),
    service: ScheduleService = Depends(get_schedule_service),
):
    payload = ScheduleCheckIn(
        address=address,
        preferred_day=preferred_day,
        preferred_time=time(9, 0),
        zone_group=zone_group,
        zone_detail=zone_detail,
        distance_km=distance_km,
        is_holiday=is_holiday,
        exclude_revision_id=exclude_revision_id,
    )
    return service.list_slots(payload)
