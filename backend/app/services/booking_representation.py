"""SB1 — canonical booking representation: interval, resource and legacy classification.

Before SB1 an appointment was a start only (`turno_fecha` + `turno_hora`); its length was
`SERVICE_MINUTES = 45` inside `schedule.py`, nobody was recorded as attending it, and nothing
in the database stopped two appointments landing on top of each other.

SB1 makes the appointment a real interval and gives it an owner, without moving any writer
(that is SB2) and without changing what the availability path offers today:

    occupied interval = [turno_fecha + turno_hora,
                         turno_fecha + turno_hora + service_minutes + buffer_minutes)

Owner policy OD-03/04: service 30 min, operational buffer 15 min, stored SEPARATELY, 45 min
occupied in total. Travel is additional and is never part of the 45. The pre-SB1 Python
constant happens to equal 45 as well; it is deliberately NOT reinterpreted here as
"inspection + buffer" — the two parts exist as two columns.

The interval is half-open, so 10:00–10:45 and 10:45–11:30 coexist and a 10:30 start does
not. It is Buenos Aires wall-clock time, the same interpretation `turno_fecha`/`turno_hora`
have always had; `APPOINTMENT_TIMEZONE` names it rather than leaving it implied.

PostgreSQL is the authority (`20261007_sb1_booking_repr`): two STORED generated columns
derive `agenda_occupying` and `occupied_during` from the start columns every existing
writer already sets, and an exclusion constraint forbids overlap per professional. The
functions here mirror those expressions for code that runs without PostgreSQL. They are a
description of the rule, never a substitute for it — SQLite proves nothing about overlap.

This module imports only the standard library so `models.py` can read its defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Optional

#: OD-03 — the inspection itself.
SERVICE_MINUTES_DEFAULT = 30
#: OD-04 — operational buffer, stored apart from the service so either can change alone.
BUFFER_MINUTES_DEFAULT = 15
#: The occupied interval. Travel is additional and never included.
OCCUPIED_MINUTES_DEFAULT = SERVICE_MINUTES_DEFAULT + BUFFER_MINUTES_DEFAULT

#: Interpretation of `turno_fecha`/`turno_hora`: Buenos Aires wall-clock time. Equal to
#: `schedule.BUSINESS_TIMEZONE` (asserted by test; not imported, to keep this module free of
#: the models → services import cycle).
APPOINTMENT_TIMEZONE = "America/Argentina/Buenos_Aires"

#: Estados that do not occupy the agenda. Mirrors `schedule._NON_OCCUPYING_ESTADOS` and its
#: case handling (`upper()`, no strip) exactly; a test pins the two together.
NON_OCCUPYING_ESTADOS: frozenset[str] = frozenset({"CANCELADO", "REPROGRAMAR"})
#: What `estado_revision` means when absent — the column default.
DEFAULT_ESTADO = "PENDIENTE"

#: OD-01 — the deterministic identity of the one launch professional resource. The row
#: itself is created by an operator with real details (the existing /profesionales page);
#: `designate_launch_professional` binds this key to it. Nothing here invents a surname,
#: email or phone.
LAUNCH_PROFESSIONAL_KEY = "inspector.julian"


def _upper_estado(estado: Optional[str]) -> str:
    return (estado if estado is not None else DEFAULT_ESTADO).upper()


def is_occupying(turno_fecha: Optional[date], turno_hora: Optional[time],
                 estado_revision: Optional[str]) -> bool:
    """Mirror of the `agenda_occupying` generated column."""
    return (turno_fecha is not None and turno_hora is not None
            and _upper_estado(estado_revision) not in NON_OCCUPYING_ESTADOS)


@dataclass(frozen=True)
class OccupiedInterval:
    """A half-open `[start, end)` wall-clock interval in `APPOINTMENT_TIMEZONE`."""
    start: datetime
    end: datetime
    service_minutes: int
    buffer_minutes: int

    def overlaps(self, other: "OccupiedInterval") -> bool:
        return self.start < other.end and other.start < self.end

    def aware(self) -> tuple[datetime, datetime]:
        """The same interval as absolute instants — interpretation made explicit."""
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(APPOINTMENT_TIMEZONE)
        return self.start.replace(tzinfo=tz), self.end.replace(tzinfo=tz)


def occupied_interval(turno_fecha: Optional[date], turno_hora: Optional[time],
                      service_minutes: Optional[int] = None,
                      buffer_minutes: Optional[int] = None) -> Optional[OccupiedInterval]:
    """Mirror of the `occupied_during` generated column. None when unscheduled.

    A missing duration (an object not yet flushed) reads as the column default, which is
    what the database will store.
    """
    if turno_fecha is None or turno_hora is None:
        return None
    service = SERVICE_MINUTES_DEFAULT if service_minutes is None else int(service_minutes)
    buffer = BUFFER_MINUTES_DEFAULT if buffer_minutes is None else int(buffer_minutes)
    start = datetime.combine(turno_fecha, turno_hora)
    return OccupiedInterval(start=start, end=start + timedelta(minutes=service + buffer),
                            service_minutes=service, buffer_minutes=buffer)


def revision_interval(revision: Any) -> Optional[OccupiedInterval]:
    return occupied_interval(revision.turno_fecha, revision.turno_hora,
                             getattr(revision, "service_minutes", None),
                             getattr(revision, "buffer_minutes", None))


# ── legacy classification ────────────────────────────────────────────────────

class LegacyClass(str, Enum):
    """Deterministic classification of a pre-SB1 row. Computed, never stored."""
    OCCUPYING_ATTRIBUTED = "OCCUPYING_ATTRIBUTED"
    OCCUPYING_UNATTRIBUTED = "OCCUPYING_UNATTRIBUTED"
    NON_OCCUPYING = "NON_OCCUPYING"          # CANCELADO / REPROGRAMAR with a slot
    UNSCHEDULED = "UNSCHEDULED"              # neither date nor time
    INCOMPLETE = "INCOMPLETE"                # exactly one of date / time


def classify(turno_fecha: Optional[date], turno_hora: Optional[time],
             estado_revision: Optional[str], profesional_id: Optional[int]) -> LegacyClass:
    if turno_fecha is None and turno_hora is None:
        return LegacyClass.UNSCHEDULED
    if turno_fecha is None or turno_hora is None:
        return LegacyClass.INCOMPLETE
    if _upper_estado(estado_revision) in NON_OCCUPYING_ESTADOS:
        return LegacyClass.NON_OCCUPYING
    if profesional_id is None:
        return LegacyClass.OCCUPYING_UNATTRIBUTED
    return LegacyClass.OCCUPYING_ATTRIBUTED


#: Read-only aggregate over the pre-SB1 columns only, so it runs unchanged before and after
#: the migration. Returns (class, rows, rows_on_or_after_today_BA). No identifying column
#: is selected.
LEGACY_CLASSIFICATION_SQL = """
SELECT cls, count(*) AS n,
       count(*) FILTER (WHERE turno_fecha >= (now() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date) AS upcoming
FROM (
  SELECT turno_fecha,
    CASE
      WHEN turno_fecha IS NULL AND turno_hora IS NULL THEN 'UNSCHEDULED'
      WHEN turno_fecha IS NULL OR turno_hora IS NULL THEN 'INCOMPLETE'
      WHEN upper(coalesce(estado_revision, 'PENDIENTE')) IN ('CANCELADO', 'REPROGRAMAR')
        THEN 'NON_OCCUPYING'
      WHEN profesional_id IS NULL THEN 'OCCUPYING_UNATTRIBUTED'
      ELSE 'OCCUPYING_ATTRIBUTED'
    END AS cls
  FROM revisions
) c
GROUP BY cls ORDER BY cls
"""

#: Occupying rows that would collide with each other at the default 45-minute interval if
#: attributed to one professional. A non-zero answer means the backfill needs owner input.
LEGACY_OVERLAP_SQL = f"""
WITH o AS (
  SELECT id, turno_fecha + turno_hora AS s
  FROM revisions
  WHERE turno_fecha IS NOT NULL AND turno_hora IS NOT NULL
    AND upper(coalesce(estado_revision, 'PENDIENTE')) NOT IN ('CANCELADO', 'REPROGRAMAR')
)
SELECT count(*) FROM o a JOIN o b
  ON a.id < b.id
 AND a.s < b.s + make_interval(mins => {OCCUPIED_MINUTES_DEFAULT})
 AND b.s < a.s + make_interval(mins => {OCCUPIED_MINUTES_DEFAULT})
"""


# ── launch professional (OD-01) ──────────────────────────────────────────────

class LaunchProfessionalError(RuntimeError):
    pass


def designate_launch_professional(db: Any, profesional_id: int) -> None:
    """Bind `LAUNCH_PROFESSIONAL_KEY` to an operator-created professional and make it the
    single launch-default resource. Deployment-checkpoint operation; never automatic.

    Refuses if a different professional already holds the designation — moving it is an
    owner decision, not something a helper does quietly. The partial unique index enforces
    "at most one" in the database regardless.
    """
    from ..models import Profesional
    target = db.get(Profesional, profesional_id)
    if target is None:
        raise LaunchProfessionalError(f"professional {profesional_id} does not exist")
    current = db.query(Profesional).filter(Profesional.is_launch_default.is_(True)).all()
    if any(p.id != target.id for p in current):
        raise LaunchProfessionalError("another professional is already the launch default")
    target.resource_key = LAUNCH_PROFESSIONAL_KEY
    target.is_launch_default = True
    db.flush()


@dataclass(frozen=True)
class BackfillResult:
    eligible: int
    attributed: int
    dry_run: bool


def backfill_legacy_attribution(db: Any, *, dry_run: bool = True) -> BackfillResult:
    """Attribute every OCCUPYING_UNATTRIBUTED row to the launch-default professional.

    Owner-checkpoint operation (closeout §legacy). Dry run by default. One UPDATE, so it is
    all-or-nothing: if two legacy rows overlap, the exclusion constraint rejects the whole
    statement and nothing is attributed — overlap is surfaced, never resolved by guessing.
    Non-occupying, unscheduled and incomplete rows are never touched.
    """
    from sqlalchemy import text
    default_id = db.execute(text(
        "SELECT id FROM profesionales WHERE is_launch_default")).scalar()
    if default_id is None:
        raise LaunchProfessionalError("no launch-default professional is designated")
    where = "agenda_occupying AND profesional_id IS NULL"
    eligible = int(db.execute(text(f"SELECT count(*) FROM revisions WHERE {where}")).scalar())
    if dry_run:
        return BackfillResult(eligible=eligible, attributed=0, dry_run=True)
    result = db.execute(text(f"UPDATE revisions SET profesional_id = :pid WHERE {where}"),
                        {"pid": default_id})
    return BackfillResult(eligible=eligible, attributed=int(result.rowcount), dry_run=False)
