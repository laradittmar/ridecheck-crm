from __future__ import annotations

from datetime import datetime
from typing import Protocol


class TravelTimeProvider(Protocol):
    def get_travel_minutes(
        self,
        origin_group: str | None,
        destination_group: str | None,
        departure: datetime | None = None,
    ) -> int: ...


class ZoneTravelProvider:
    """Deterministic zone-group travel model matching client Viaticos.gs rules.

    Same group → 30 min.
    CABA ↔ any other group → 60 min.
    Norte ↔ Oeste, Norte ↔ Sur, Oeste ↔ Sur → 90 min.
    Unknown group (None or empty) → 0 min (no constraint applied).
    """

    _CROSS: dict[frozenset[str], int] = {
        frozenset({"CABA", "NORTE"}): 60,
        frozenset({"CABA", "OESTE"}): 60,
        frozenset({"CABA", "SUR"}): 60,
        frozenset({"NORTE", "OESTE"}): 90,
        frozenset({"NORTE", "SUR"}): 90,
        frozenset({"OESTE", "SUR"}): 90,
    }

    def get_travel_minutes(
        self,
        origin_group: str | None,
        destination_group: str | None,
        departure: datetime | None = None,
    ) -> int:
        a = (origin_group or "").strip().upper()
        b = (destination_group or "").strip().upper()
        if not a or not b:
            return 0
        if a == b:
            return 30
        return self._CROSS.get(frozenset({a, b}), 90)
