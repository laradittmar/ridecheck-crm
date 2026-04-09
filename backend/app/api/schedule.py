from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas.schedule import ScheduleCheckIn, ScheduleCheckOut
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
