"""SB1 — route anchors: where a working day starts and where it ends.

Owner policy OD-02 and SB0 brief §4.1, stated as data rather than inferred from a label:

    START   Tue / Thu / Sat → Home          Wed / Fri → School
            Mon alternates: 2026-09-07 School, 2026-09-14 Home, 09-21 School, 09-28 Home
    END     Mon / Wed / Sat → at the last inspection
            Tue / Thu       → School (an exception needs Julián's authorization)
            Fri alternates: 2026-09-11 last inspection, 2026-09-18 School

Home and School are distinct anchors even when they share a commercial zone. The legacy
School display label in `schedule.py` does not supersede the School anchor (OD-02); that
legacy display path is left untouched in SB1 and its start-anchor parity is pinned to this
registry by test.

Privacy (SB1-R3): an anchor is a private person's operational location, so this tracked
module holds STRUCTURE only — a stable key, a type, a generic label and the weekly rules.
The real label, the owner-supplied location reference and, later, coordinates are never
committed. Deployment supplies them from protected configuration or restricted database
records keyed on `RouteAnchor.key`, before SB3/SB4 consume them; until then
`location_ref` and `coordinates` are None. Nothing here geocodes, resolves a reference or
calls any service.

Alternation is pure calendar parity on the business date: whole weeks from the reference
date, even → the reference value. An instant must be turned into a business date first
(`schedule.business_date_at`), never by the host clock.

Read by nothing in SB1 — availability is unchanged. This is the representation SB3/SB4
route feasibility will consume.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Union


class AnchorType(str, Enum):
    HOME = "HOME"
    SCHOOL = "SCHOOL"


class AnchorRole(str, Enum):
    START = "START"
    END = "END"


@dataclass(frozen=True)
class RouteAnchor:
    #: Stable logical identity. Protected configuration and SB3 coordinates key on it.
    key: str
    anchor_type: AnchorType
    #: Generic, non-identifying label. The real one lives in protected configuration.
    label: str
    #: Owner-supplied location reference — deployment-provided, never in git. None here.
    location_ref: Optional[str] = None
    #: (lat, lng) — SB3, deployment-provided. None here.
    coordinates: Optional[tuple[float, float]] = None


HOME = RouteAnchor(key="anchor.home", anchor_type=AnchorType.HOME, label="Home")
SCHOOL = RouteAnchor(key="anchor.school", anchor_type=AnchorType.SCHOOL, label="School")

ANCHORS: dict[str, RouteAnchor] = {a.key: a for a in (HOME, SCHOOL)}


class EndAtLastInspection(Enum):
    """The day ends wherever the last inspection is — no return travel is reserved."""
    LAST_INSPECTION = "LAST_INSPECTION"


LAST_INSPECTION = EndAtLastInspection.LAST_INSPECTION

Target = Union[RouteAnchor, EndAtLastInspection]

#: The only authority that may relax the Tue/Thu School end anchor (OD-08). A marker only:
#: the override record itself (actor, reason, before/after) is SB5's.
OVERRIDE_AUTHORITY_JULIAN = "julian"


@dataclass(frozen=True)
class AnchorRule:
    weekday: int                      # date.weekday(): Monday=0 … Saturday=5
    role: AnchorRole
    target: Target                    # fixed target, or the reference-parity target
    alternate: Optional[Target] = None
    reference_date: Optional[date] = None
    override_authority: Optional[str] = None

    @property
    def alternates(self) -> bool:
        return self.alternate is not None


_MON, _TUE, _WED, _THU, _FRI, _SAT = range(6)

#: Owner reference dates. 2026-09-07 is a Monday, 2026-09-11 a Friday (asserted by test).
MONDAY_START_REFERENCE = date(2026, 9, 7)    # School
FRIDAY_END_REFERENCE = date(2026, 9, 11)     # last inspection

RULES: tuple[AnchorRule, ...] = (
    # ── START ──
    AnchorRule(_MON, AnchorRole.START, SCHOOL, alternate=HOME,
               reference_date=MONDAY_START_REFERENCE),
    AnchorRule(_TUE, AnchorRole.START, HOME),
    AnchorRule(_WED, AnchorRole.START, SCHOOL),
    AnchorRule(_THU, AnchorRole.START, HOME),
    AnchorRule(_FRI, AnchorRole.START, SCHOOL),
    AnchorRule(_SAT, AnchorRole.START, HOME),
    # ── END ──
    AnchorRule(_MON, AnchorRole.END, LAST_INSPECTION),
    AnchorRule(_TUE, AnchorRole.END, SCHOOL, override_authority=OVERRIDE_AUTHORITY_JULIAN),
    AnchorRule(_WED, AnchorRole.END, LAST_INSPECTION),
    AnchorRule(_THU, AnchorRole.END, SCHOOL, override_authority=OVERRIDE_AUTHORITY_JULIAN),
    AnchorRule(_FRI, AnchorRole.END, LAST_INSPECTION, alternate=SCHOOL,
               reference_date=FRIDAY_END_REFERENCE),
    AnchorRule(_SAT, AnchorRole.END, LAST_INSPECTION),
)

_RULE_INDEX: dict[tuple[int, AnchorRole], AnchorRule] = {(r.weekday, r.role): r for r in RULES}


@dataclass(frozen=True)
class AnchorResolution:
    day: date
    role: AnchorRole
    target: Target
    rule: AnchorRule

    @property
    def anchor(self) -> Optional[RouteAnchor]:
        return self.target if isinstance(self.target, RouteAnchor) else None

    @property
    def override_authority(self) -> Optional[str]:
        return self.rule.override_authority


def _parity_is_reference(day: date, reference: date) -> bool:
    return ((day - reference).days // 7) % 2 == 0


def resolve(day: date, role: AnchorRole) -> Optional[AnchorResolution]:
    """The anchor for `day` (a business-calendar date) in `role`. None on Sunday (closed)."""
    rule = _RULE_INDEX.get((day.weekday(), role))
    if rule is None:
        return None
    target = rule.target
    if rule.alternates and not _parity_is_reference(day, rule.reference_date):
        target = rule.alternate
    return AnchorResolution(day=day, role=role, target=target, rule=rule)


def start_anchor(day: date) -> Optional[AnchorResolution]:
    return resolve(day, AnchorRole.START)


def end_anchor(day: date) -> Optional[AnchorResolution]:
    return resolve(day, AnchorRole.END)
