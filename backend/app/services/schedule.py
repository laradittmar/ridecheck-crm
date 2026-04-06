from __future__ import annotations

from ..schemas.schedule import ScheduleCheckIn, ScheduleCheckOut


class ScheduleService:
    def check(self, payload: ScheduleCheckIn) -> ScheduleCheckOut:
        valid = all(
            (
                payload.address.strip(),
                payload.preferred_day.strip(),
                payload.preferred_time.strip(),
            )
        )
        return ScheduleCheckOut(valid=bool(valid), suggested_slots=[])
