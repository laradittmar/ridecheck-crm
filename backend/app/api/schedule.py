from __future__ import annotations

from fastapi import APIRouter, Depends

from ..schemas.schedule import ScheduleCheckIn, ScheduleCheckOut
from ..services.schedule import ScheduleService

router = APIRouter(prefix="/api/schedule", tags=["schedule"])


def get_schedule_service() -> ScheduleService:
    return ScheduleService()


@router.post("/check", response_model=ScheduleCheckOut)
def check_schedule(
    payload: ScheduleCheckIn,
    service: ScheduleService = Depends(get_schedule_service),
):
    return service.check(payload)
