from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Revision, ThreadRevision, WhatsAppThreadCandidate
from ..schemas.schedule import (
    ScheduleAppointmentOut,
    ScheduleCheckIn,
    ScheduleCheckOut,
    ScheduleConflictOut,
    ScheduleSlotOut,
    ScheduleSlotsOut,
)
from .travel import TravelTimeProvider, ZoneTravelProvider

SERVICE_MINUTES = 45
APPROVAL_TAG = "Esperando aprobación"

# Monday zero-zone alternation: anchor date = Santa Catalina week
MONDAY_SANTA_ANCHOR = date(2026, 8, 17)
ZERO_ZONE_GROUP = "Norte"
ZERO_SANTA = "Santa Catalina"
ZERO_MELO = "Melo y Panamericana"

# Revision estados that do NOT occupy scheduling capacity.
# CANCELADO: appointment cancelled.
# REPROGRAMAR: appointment to be rescheduled (slot effectively vacated).
_NON_OCCUPYING_ESTADOS: frozenset[str] = frozenset({"CANCELADO", "REPROGRAMAR"})


@dataclass(frozen=True)
class OccupiedSlot:
    source: str
    identifier: int
    start: datetime
    end: datetime          # start + SERVICE_MINUTES (45 min, inspection only)
    label: str
    zone_group: str | None = None


# ── L4.3 Phase B: single business-hours authority ─────────────────────────────
# `_business_hours()` below delegates here so that every consumer (ScheduleService,
# CE FAQ answers, rejection explanations) reads ONE source for operating hours.
# Weekday index follows date.weekday(): Monday=0 … Sunday=6.
_WEEKDAY_HOURS: dict[int, tuple[time, time, bool]] = {
    0: (time(13, 0), time(18, 0), False),   # Monday    13:00-18:00
    1: (time(9, 30), time(14, 0), False),   # Tuesday   09:30-14:00
    2: (time(9, 0), time(18, 0), False),    # Wednesday 09:00-18:00
    3: (time(9, 0), time(14, 0), False),    # Thursday  09:00-14:00
    4: (time(9, 0), time(18, 0), False),    # Friday    09:00-18:00
    5: (time(9, 0), time(15, 0), False),    # Saturday  09:00-15:00
    6: (time(9, 0), time(9, 0), True),      # Sunday    closed
}

_HOLIDAY_HOURS: tuple[time, time, bool] = (time(9, 0), time(15, 0), False)

_WEEKDAY_LABELS_ES: dict[int, str] = {
    0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves",
    4: "viernes", 5: "sábados", 6: "domingos",
}


def business_hours_for_weekday(weekday: int, is_holiday: bool = False) -> tuple[time, time, bool]:
    """Canonical (start, end, closed) for a weekday index. Single source of truth."""
    if is_holiday:
        return _HOLIDAY_HOURS
    return _WEEKDAY_HOURS[int(weekday) % 7]


def _format_hour_es(value: time) -> str:
    """'9', '9.30', '13' — Argentine conversational hour rendering."""
    if value.minute == 0:
        return str(value.hour)
    return f"{value.hour}.{value.minute:02d}"


def format_business_hours_es(day: date, is_holiday: bool = False) -> str:
    """Human phrase for one day's operating hours: 'de 9 a 14 hs' / 'cerrado'."""
    start, end, closed = business_hours_for_weekday(day.weekday(), is_holiday)
    if closed:
        return "cerrado"
    return f"de {_format_hour_es(start)} a {_format_hour_es(end)} hs"


def business_hours_summary_es() -> str:
    """Canonical customer-facing hours answer, generated from _WEEKDAY_HOURS.

    Consecutive weekdays sharing identical hours are grouped, so the phrasing stays
    natural while remaining derived from the scheduling authority (never hard-coded).
    """
    groups: list[tuple[list[int], tuple[time, time, bool]]] = []
    for wd in range(6):  # Monday..Saturday; Sunday reported separately
        hours = business_hours_for_weekday(wd)
        if groups and groups[-1][1] == hours:
            groups[-1][0].append(wd)
        else:
            groups.append(([wd], hours))

    parts: list[str] = []
    for days, (start, end, closed) in groups:
        if closed:
            continue
        if len(days) == 1:
            label = _WEEKDAY_LABELS_ES[days[0]]
        else:
            label = f"{_WEEKDAY_LABELS_ES[days[0]]} a {_WEEKDAY_LABELS_ES[days[-1]]}"
        parts.append(f"{label} de {_format_hour_es(start)} a {_format_hour_es(end)} hs")

    if len(parts) >= 2:
        body = ", ".join(parts[:-1]) + " y " + parts[-1]
    else:
        body = parts[0] if parts else "sin horarios operativos"
    return f"Trabajamos {body}. Los domingos no trabajamos."


class ScheduleService:
    def __init__(self, db: Session, travel_provider: TravelTimeProvider | None = None):
        self.db = db
        self._travel: TravelTimeProvider = travel_provider or ZoneTravelProvider()

    def check(self, payload: ScheduleCheckIn) -> ScheduleCheckOut:
        requested_start = datetime.combine(payload.preferred_day, payload.preferred_time)
        requested_end = requested_start + timedelta(minutes=SERVICE_MINUTES)

        hours = self._business_hours(payload.preferred_day, payload.is_holiday)
        reasons: list[str] = []
        rules_applied = [
            f"Duracion fija de revision: {SERVICE_MINUTES} minutos",
            f"Horario operativo del dia: {self._format_hours(hours.start, hours.end)}",
        ]

        if hours.closed:
            reasons.append("Domingo: sin operaciones. Por favor elegí un día de lunes a sábado")
            return ScheduleCheckOut(
                valid=False,
                suggested_slots=[],
                approval_tag=APPROVAL_TAG,
                requested_slot=ScheduleSlotOut(
                    start=requested_start.isoformat(timespec="minutes"),
                    end=requested_end.isoformat(timespec="minutes"),
                ),
                business_hours="cerrado",
                service_minutes=SERVICE_MINUTES,
                buffer_minutes=0,
                travel_minutes=0,
                total_slot_minutes=SERVICE_MINUTES,
                conflicts=[],
                reasons=reasons,
                rules_applied=rules_applied,
            )

        occupied_slots = self._load_occupied_slots(
            preferred_day=payload.preferred_day,
            exclude_revision_id=payload.exclude_revision_id,
        )

        if requested_start.time() < hours.start or requested_end.time() > hours.end:
            reasons.append(
                "El turno no entra en el horario operativo del dia considerando revision y traslado"
            )

        jornada_start_dt = datetime.combine(payload.preferred_day, hours.start)
        if not self._is_travel_valid_slot(
            candidate=requested_start,
            zone_group=payload.zone_group,
            occupied_slots=occupied_slots,
            jornada_start=jornada_start_dt,
            zero_zone=self._zero_zone_group(payload.preferred_day),
        ):
            reasons.append(
                "El turno no satisface las restricciones de traslado (origen, turno anterior o siguiente)"
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
            zone_group=payload.zone_group,
        )

        return ScheduleCheckOut(
            valid=not reasons,
            suggested_slots=suggested_slots,
            approval_tag=APPROVAL_TAG,
            requested_slot=ScheduleSlotOut(
                start=requested_start.isoformat(timespec="minutes"),
                end=requested_end.isoformat(timespec="minutes"),
            ),
            business_hours=self._format_hours(hours.start, hours.end),
            service_minutes=SERVICE_MINUTES,
            buffer_minutes=0,
            travel_minutes=0,
            total_slot_minutes=SERVICE_MINUTES,
            conflicts=conflicts,
            reasons=reasons,
            rules_applied=rules_applied,
        )

    def list_slots(self, payload: ScheduleCheckIn) -> ScheduleSlotsOut:
        hours = self._business_hours(payload.preferred_day, payload.is_holiday)
        occupied_slots = self._load_occupied_slots(
            preferred_day=payload.preferred_day,
            exclude_revision_id=payload.exclude_revision_id,
        )
        rules_applied = [
            f"Duracion fija de revision: {SERVICE_MINUTES} minutos",
            f"Horario operativo del dia: {self._format_hours(hours.start, hours.end)}",
        ]
        if hours.closed:
            rules_applied.append("Domingo: sin operaciones")
            return ScheduleSlotsOut(
                preferred_day=payload.preferred_day,
                business_hours="cerrado",
                slots=[],
                rules_applied=rules_applied,
            )

        return ScheduleSlotsOut(
            preferred_day=payload.preferred_day,
            business_hours=self._format_hours(hours.start, hours.end),
            slots=self._suggest_slots(
                preferred_day=payload.preferred_day,
                occupied_slots=occupied_slots,
                hours=hours,
                zone_group=payload.zone_group,
                max_results=24,
            ),
            rules_applied=rules_applied,
        )

    def list_appointments_that_day(self, scheduled_day: date) -> list[ScheduleAppointmentOut]:
        appointments: list[ScheduleAppointmentOut] = []

        revisions = self.db.execute(
            select(Revision)
            .where(Revision.turno_fecha == scheduled_day)
            .where(Revision.turno_hora.is_not(None))
            .where(~Revision.estado_revision.in_(list(_NON_OCCUPYING_ESTADOS)))
        ).scalars().all()
        for revision in revisions:
            appointments.append(
                ScheduleAppointmentOut(
                    time=revision.turno_hora.strftime("%H:%M"),
                    address=self._revision_address(revision),
                    source="revision",
                )
            )

        thread_revisions = self.db.execute(
            select(ThreadRevision)
            .where(ThreadRevision.scheduled_date == scheduled_day)
            .where(ThreadRevision.scheduled_time.is_not(None))
            .where(ThreadRevision.status.in_(("booked", "completed")))
        ).scalars().all()
        for revision in thread_revisions:
            appointments.append(
                ScheduleAppointmentOut(
                    time=revision.scheduled_time.strftime("%H:%M"),
                    address=(revision.address or "").strip() or "-",
                    source="thread_revision",
                )
            )

        return sorted(appointments, key=lambda item: (item.time, item.source, item.address))

    def _load_occupied_slots(
        self,
        preferred_day: date,
        exclude_revision_id: int | None = None,
    ) -> list[OccupiedSlot]:
        slots: list[OccupiedSlot] = []

        # SQL filter excludes non-occupying estados; Python filter handles FakeDB in tests.
        raw_revisions = self.db.execute(
            select(Revision)
            .where(Revision.turno_fecha == preferred_day)
            .where(Revision.turno_hora.is_not(None))
            .where(~Revision.estado_revision.in_(list(_NON_OCCUPYING_ESTADOS)))
        ).scalars().all()

        for revision in raw_revisions:
            estado = (revision.estado_revision or "PENDIENTE").upper()
            if estado in _NON_OCCUPYING_ESTADOS:
                continue
            if exclude_revision_id is not None and int(revision.id) == int(exclude_revision_id):
                continue
            start_dt = datetime.combine(revision.turno_fecha, revision.turno_hora)
            slots.append(
                OccupiedSlot(
                    source="revision",
                    identifier=int(revision.id),
                    start=start_dt,
                    end=start_dt + timedelta(minutes=SERVICE_MINUTES),
                    label=f"Lead revision #{revision.id}",
                    zone_group=revision.zone_group,
                )
            )

        thread_revisions = self.db.execute(
            select(ThreadRevision)
            .where(ThreadRevision.scheduled_date == preferred_day)
            .where(ThreadRevision.scheduled_time.is_not(None))
            .where(ThreadRevision.status.in_(("booked", "completed")))
        ).scalars().all()

        # Resolve zone_group: prefer ThreadRevision.zone_group; fall back to linked candidate.
        tr_need_cand = [
            tr for tr in thread_revisions
            if tr.zone_group is None and tr.candidate_id is not None
        ]
        cand_zone_map: dict[int, str | None] = {}
        if tr_need_cand:
            cand_ids = [tr.candidate_id for tr in tr_need_cand]
            cand_rows = self.db.execute(
                select(WhatsAppThreadCandidate)
                .where(WhatsAppThreadCandidate.id.in_(cand_ids))
            ).scalars().all()
            cand_zone_map = {c.id: c.zone_group for c in cand_rows}

        for tr in thread_revisions:
            zone_group = tr.zone_group
            if zone_group is None and tr.candidate_id is not None:
                zone_group = cand_zone_map.get(tr.candidate_id)
            start_dt = datetime.combine(tr.scheduled_date, tr.scheduled_time)
            slots.append(
                OccupiedSlot(
                    source="thread_revision",
                    identifier=int(tr.id),
                    start=start_dt,
                    end=start_dt + timedelta(minutes=SERVICE_MINUTES),
                    label=f"Thread revision #{tr.id}",
                    zone_group=zone_group,
                )
            )

        return sorted(slots, key=lambda s: (s.start, s.end, s.source, s.identifier))

    def _suggest_slots(
        self,
        preferred_day: date,
        occupied_slots: list[OccupiedSlot],
        hours: "_BusinessHours",
        zone_group: str | None,
        max_results: int = 5,
    ) -> list[str]:
        jornada_start_dt = datetime.combine(preferred_day, hours.start)
        hard_end = datetime.combine(preferred_day, hours.end)
        zero_zone = self._zero_zone_group(preferred_day)
        candidate = jornada_start_dt
        suggestions: list[str] = []

        while (
            candidate + timedelta(minutes=SERVICE_MINUTES) <= hard_end
            and len(suggestions) < max_results
        ):
            if self._is_travel_valid_slot(
                candidate=candidate,
                zone_group=zone_group,
                occupied_slots=occupied_slots,
                jornada_start=jornada_start_dt,
                zero_zone=zero_zone,
            ):
                suggestions.append(candidate.strftime("%H:%M"))
            candidate += timedelta(minutes=30)

        return suggestions

    def _is_travel_valid_slot(
        self,
        candidate: datetime,
        zone_group: str | None,
        occupied_slots: list[OccupiedSlot],
        jornada_start: datetime,
        zero_zone: str | None,
    ) -> bool:
        cand_end = candidate + timedelta(minutes=SERVICE_MINUTES)

        # Physical overlap: service windows must not collide.
        for slot in occupied_slots:
            if candidate < slot.end and cand_end > slot.start:
                return False

        # Latest slot whose service ends at or before this candidate's start.
        prev: OccupiedSlot | None = None
        for slot in occupied_slots:
            if slot.end <= candidate:
                if prev is None or slot.start > prev.start:
                    prev = slot

        # Earliest slot that starts at or after this candidate's end.
        nxt: OccupiedSlot | None = None
        for slot in occupied_slots:
            if slot.start >= cand_end:
                if nxt is None or slot.start < nxt.start:
                    nxt = slot

        # First appointment of day OR previous appointment travel constraint.
        if prev is None:
            travel_from_origin = self._travel.get_travel_minutes(zero_zone, zone_group)
            if jornada_start + timedelta(minutes=travel_from_origin) > candidate:
                return False
        else:
            travel_from_prev = self._travel.get_travel_minutes(prev.zone_group, zone_group)
            if prev.start + timedelta(minutes=SERVICE_MINUTES + travel_from_prev) > candidate:
                return False

        # Next appointment travel constraint (CRM improvement over client Agenda.gs —
        # prevents inserting a slot that makes the next booked appointment unreachable).
        if nxt is not None:
            travel_to_next = self._travel.get_travel_minutes(zone_group, nxt.zone_group)
            if cand_end + timedelta(minutes=travel_to_next) > nxt.start:
                return False

        return True

    def _business_hours(self, preferred_day: date, is_holiday: bool) -> "_BusinessHours":
        # L4.3 Phase B: delegates to the module-level authority so CE FAQ answers,
        # rejection explanations and slot computation can never diverge.
        start, end, closed = business_hours_for_weekday(preferred_day.weekday(), is_holiday)
        return _BusinessHours(start=start, end=end, closed=closed)

    def _zero_zone_group(self, preferred_day: date) -> str:
        return ZERO_ZONE_GROUP

    def _zero_zone_detail(self, preferred_day: date) -> str:
        weekday = preferred_day.weekday()
        if weekday == 0:
            delta = (preferred_day - MONDAY_SANTA_ANCHOR).days
            return ZERO_SANTA if (delta // 7) % 2 == 0 else ZERO_MELO
        if weekday in (1, 3, 5):
            return ZERO_SANTA
        return ZERO_MELO  # Wed, Fri

    def get_day_start_info(self, day: date) -> dict:
        """Return operational day-start metadata for the agenda view."""
        hours = self._business_hours(day, False)
        zero_group = self._zero_zone_group(day)
        zero_detail = self._zero_zone_detail(day)
        biz_str = "cerrado" if hours.closed else self._format_hours(hours.start, hours.end)
        return {
            "is_closed": hours.closed,
            "business_hours_str": biz_str,
            "start_time": hours.start.strftime("%H:%M"),
            "end_time": hours.end.strftime("%H:%M"),
            "zero_zone_group": zero_group,
            "zero_zone_detail": zero_detail,
        }

    @staticmethod
    def _revision_address(revision: Revision) -> str:
        parts = [
            str(revision.direccion_texto or "").strip(),
            str(revision.zone_detail or "").strip(),
            str(revision.zone_group or "").strip(),
        ]
        clean_parts = [part for part in parts if part]
        return ", ".join(clean_parts) if clean_parts else "-"

    @staticmethod
    def _normalized_text(value: str | None) -> str:
        stripped = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
        return " ".join(stripped.strip().lower().split())

    @staticmethod
    def _format_hours(start: time, end: time) -> str:
        return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"


@dataclass(frozen=True)
class _BusinessHours:
    start: time
    end: time
    closed: bool = False
