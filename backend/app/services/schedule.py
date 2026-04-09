from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Revision, ThreadRevision
from ..schemas.schedule import (
    ScheduleCheckIn,
    ScheduleCheckOut,
    ScheduleConflictOut,
    ScheduleSlotOut,
    ScheduleSlotsOut,
)

SERVICE_MINUTES = 45
BUFFER_MINUTES = 15
ANCHOR_MONDAY = date(2026, 4, 6)
APPROVAL_TAG = "Esperando aprobación"
PRIORITY_CUTOFF = time(12, 0)
SPECIAL_EXTENSION_ZONES = ("san isidro", "vicente lopez")
PRIORITY_ZONES = {
    "villa ortuzar",
    "villa pueyrredon",
    "villa real",
    "villa riachuelo",
    "villa santa rita",
    "villa soldati",
    "coronel brandsen",
    "la plata",
    "berisso",
    "ensenada",
    "canuelas",
    "general las heras",
    "lujan",
    "exaltacion de la cruz",
    "zarate",
    "campana",
}


@dataclass(frozen=True)
class OccupiedSlot:
    source: str
    identifier: int
    start: datetime
    end: datetime
    label: str


class ScheduleService:
    def __init__(self, db: Session):
        self.db = db

    def check(self, payload: ScheduleCheckIn) -> ScheduleCheckOut:
        requested_start = datetime.combine(payload.preferred_day, payload.preferred_time)
        travel_minutes = self._travel_minutes(payload.distance_km)
        total_slot_minutes = SERVICE_MINUTES + BUFFER_MINUTES + travel_minutes
        requested_end = requested_start + timedelta(minutes=total_slot_minutes)

        rules_applied = [
            "Duracion fija de revision: 45 minutos",
            "Buffer operativo: 15 minutos",
            f"Traslado estimado: {travel_minutes} minutos",
            f"Tiempo total reservado: {total_slot_minutes} minutos",
        ]
        reasons: list[str] = []

        hours = self._business_hours(
            preferred_day=payload.preferred_day,
            normalized_context=self._normalized_context(payload),
            is_holiday=payload.is_holiday,
        )
        rules_applied.append(f"Horario operativo del dia: {self._format_hours(hours.start, hours.end)}")
        if payload.is_holiday:
            rules_applied.append("Feriado: se usa horario reducido 09:00 a 15:00")
        if hours.extended_for_zone:
            rules_applied.append("Extension especial aplicada para San Isidro/Vicente Lopez")
        if hours.alternating_note:
            rules_applied.append(hours.alternating_note)

        if requested_start.time() < hours.start or requested_end.time() > hours.end:
            reasons.append(
                "El turno no entra en el horario operativo del dia considerando revision, buffer y traslado"
            )

        if self._is_priority_zone(payload) and payload.preferred_time >= PRIORITY_CUTOFF:
            reasons.append("La zona solicitada es prioritaria y debe asignarse entre las 09:00 y las 12:00")
            rules_applied.append("Zona prioritaria: validacion reforzada para 09:00 a 12:00")

        occupied_slots = self._load_occupied_slots(
            preferred_day=payload.preferred_day,
            exclude_revision_id=payload.exclude_revision_id,
        )
        overlaps = [
            slot for slot in occupied_slots
            if requested_start < slot.end and requested_end > slot.start
        ]
        conflicts = [
            ScheduleConflictOut(
                source=slot.source,
                source_id=slot.identifier,
                start=slot.start.isoformat(timespec="minutes"),
                end=slot.end.isoformat(timespec="minutes"),
                label=slot.label,
            )
            for slot in overlaps
        ]
        if conflicts:
            reasons.append("El horario solicitado se superpone con un turno ya reservado en el CRM")

        suggested_slots = self._suggest_slots(
            preferred_day=payload.preferred_day,
            occupied_slots=occupied_slots,
            hours=hours,
            total_slot_minutes=total_slot_minutes,
            payload=payload,
        )

        valid = not reasons
        if valid:
            rules_applied.append("Resultado automatico: el turno es compatible y queda pendiente de aprobacion")

        return ScheduleCheckOut(
            valid=valid,
            suggested_slots=suggested_slots,
            approval_tag=APPROVAL_TAG,
            requested_slot=ScheduleSlotOut(
                start=requested_start.isoformat(timespec="minutes"),
                end=requested_end.isoformat(timespec="minutes"),
            ),
            business_hours=self._format_hours(hours.start, hours.end),
            service_minutes=SERVICE_MINUTES,
            buffer_minutes=BUFFER_MINUTES,
            travel_minutes=travel_minutes,
            total_slot_minutes=total_slot_minutes,
            conflicts=conflicts,
            reasons=reasons,
            rules_applied=rules_applied,
        )

    def list_slots(self, payload: ScheduleCheckIn) -> ScheduleSlotsOut:
        hours = self._business_hours(
            preferred_day=payload.preferred_day,
            normalized_context=self._normalized_context(payload),
            is_holiday=payload.is_holiday,
        )
        travel_minutes = self._travel_minutes(payload.distance_km)
        total_slot_minutes = SERVICE_MINUTES + BUFFER_MINUTES + travel_minutes
        occupied_slots = self._load_occupied_slots(
            preferred_day=payload.preferred_day,
            exclude_revision_id=payload.exclude_revision_id,
        )
        rules_applied = [
            "Duracion fija de revision: 45 minutos",
            "Buffer operativo: 15 minutos",
            f"Traslado estimado: {travel_minutes} minutos",
            f"Tiempo total reservado: {total_slot_minutes} minutos",
            f"Horario operativo del dia: {self._format_hours(hours.start, hours.end)}",
        ]
        if payload.is_holiday:
            rules_applied.append("Feriado: se usa horario reducido 09:00 a 15:00")
        if hours.extended_for_zone:
            rules_applied.append("Extension especial aplicada para San Isidro/Vicente Lopez")
        if hours.alternating_note:
            rules_applied.append(hours.alternating_note)
        if self._is_priority_zone(payload):
            rules_applied.append("Zona prioritaria: se ofrecen solo horarios compatibles con la franja 09:00 a 12:00")

        return ScheduleSlotsOut(
            preferred_day=payload.preferred_day,
            business_hours=self._format_hours(hours.start, hours.end),
            slots=self._suggest_slots(
                preferred_day=payload.preferred_day,
                occupied_slots=occupied_slots,
                hours=hours,
                total_slot_minutes=total_slot_minutes,
                payload=payload,
                max_results=24,
            ),
            rules_applied=rules_applied,
        )

    def _load_occupied_slots(self, preferred_day: date, exclude_revision_id: int | None = None) -> list[OccupiedSlot]:
        slots: list[OccupiedSlot] = []

        revisions = self.db.execute(
            select(Revision)
            .where(Revision.turno_fecha == preferred_day)
            .where(Revision.turno_hora.is_not(None))
        ).scalars().all()
        for revision in revisions:
            if exclude_revision_id is not None and int(revision.id) == int(exclude_revision_id):
                continue
            start_dt = datetime.combine(revision.turno_fecha, revision.turno_hora)
            slots.append(
                OccupiedSlot(
                    source="revision",
                    identifier=int(revision.id),
                    start=start_dt,
                    end=start_dt + timedelta(minutes=SERVICE_MINUTES + BUFFER_MINUTES),
                    label=f"Lead revision #{revision.id}",
                )
            )

        thread_revisions = self.db.execute(
            select(ThreadRevision)
            .where(ThreadRevision.scheduled_date == preferred_day)
            .where(ThreadRevision.scheduled_time.is_not(None))
            .where(ThreadRevision.status.in_(("booked", "completed")))
        ).scalars().all()
        for revision in thread_revisions:
            start_dt = datetime.combine(revision.scheduled_date, revision.scheduled_time)
            slots.append(
                OccupiedSlot(
                    source="thread_revision",
                    identifier=int(revision.id),
                    start=start_dt,
                    end=start_dt + timedelta(minutes=SERVICE_MINUTES + BUFFER_MINUTES),
                    label=f"Thread revision #{revision.id}",
                )
            )

        return sorted(slots, key=lambda slot: (slot.start, slot.end, slot.source, slot.identifier))

    def _suggest_slots(
        self,
        preferred_day: date,
        occupied_slots: list[OccupiedSlot],
        hours: "_BusinessHours",
        total_slot_minutes: int,
        payload: ScheduleCheckIn,
        max_results: int = 5,
    ) -> list[str]:
        suggestions: list[str] = []
        candidate = datetime.combine(preferred_day, hours.start)
        hard_end = datetime.combine(preferred_day, hours.end)
        while candidate + timedelta(minutes=total_slot_minutes) <= hard_end and len(suggestions) < max_results:
            if self._is_candidate_usable(candidate, total_slot_minutes, occupied_slots, payload):
                suggestions.append(candidate.isoformat(timespec="minutes"))
            candidate += timedelta(minutes=30)
        return suggestions

    def _is_candidate_usable(
        self,
        candidate: datetime,
        total_slot_minutes: int,
        occupied_slots: list[OccupiedSlot],
        payload: ScheduleCheckIn,
    ) -> bool:
        candidate_end = candidate + timedelta(minutes=total_slot_minutes)
        if self._is_priority_zone(payload) and candidate.time() >= PRIORITY_CUTOFF:
            return False
        return not any(candidate < slot.end and candidate_end > slot.start for slot in occupied_slots)

    @staticmethod
    def _travel_minutes(distance_km: float | None) -> int:
        if distance_km is None:
            return 0
        return int(math.ceil(distance_km * 1.7))

    @staticmethod
    def _normalized_text(value: str | None) -> str:
        stripped = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
        return " ".join(stripped.strip().lower().split())

    def _normalized_context(self, payload: ScheduleCheckIn) -> str:
        return " ".join(
            filter(
                None,
                (
                    self._normalized_text(payload.address),
                    self._normalized_text(payload.zone_group),
                    self._normalized_text(payload.zone_detail),
                ),
            )
        )

    def _is_priority_zone(self, payload: ScheduleCheckIn) -> bool:
        context = self._normalized_context(payload)
        return any(zone in context for zone in PRIORITY_ZONES)

    def _business_hours(self, preferred_day: date, normalized_context: str, is_holiday: bool) -> "_BusinessHours":
        if is_holiday:
            return _BusinessHours(start=time(9, 0), end=time(15, 0))

        weekday = preferred_day.weekday()
        if weekday == 0:
            return _BusinessHours(
                start=time(13, 0),
                end=time(18, 0),
                alternating_note="Lunes alternado: la jornada arranca en la zona cero correspondiente",
            )
        if weekday == 1:
            end_time = time(15, 0) if self._has_special_extension(normalized_context) else time(14, 0)
            return _BusinessHours(start=time(9, 30), end=end_time, extended_for_zone=end_time == time(15, 0))
        if weekday == 2:
            return _BusinessHours(start=time(9, 0), end=time(18, 0))
        if weekday == 3:
            return _BusinessHours(start=time(9, 0), end=time(14, 0))
        if weekday == 4:
            start_time = time(9, 0)
            end_time = time(18, 0)
            note = "Viernes: jornada normal de 09:00 a 18:00"
            return _BusinessHours(
                start=start_time,
                end=end_time,
                extended_for_zone=False,
                alternating_note=note,
            )
        if weekday == 5:
            return _BusinessHours(start=time(9, 0), end=time(15, 0))
        return _BusinessHours(start=time(9, 0), end=time(15, 0))

    def _has_special_extension(self, normalized_context: str) -> bool:
        return any(zone in normalized_context for zone in SPECIAL_EXTENSION_ZONES)

    @staticmethod
    def _is_alternating_week(preferred_day: date) -> bool:
        delta_days = (preferred_day - ANCHOR_MONDAY).days
        if delta_days < 0:
            return False
        return (delta_days // 7) % 2 == 0

    @staticmethod
    def _format_hours(start: time, end: time) -> str:
        return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"


@dataclass(frozen=True)
class _BusinessHours:
    start: time
    end: time
    extended_for_zone: bool = False
    alternating_note: str | None = None
