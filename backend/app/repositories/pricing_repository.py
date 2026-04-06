from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ViaticosZone


@dataclass(frozen=True)
class BasePriceRow:
    tipo_vehiculo: str
    precio_base: int


class PricingRepository:
    def __init__(self, csv_path: Path | None = None):
        self._csv_path = csv_path or Path(__file__).resolve().parents[1] / "data" / "pricing_base.csv"

    @lru_cache(maxsize=1)
    def load_base_prices(self) -> tuple[BasePriceRow, ...]:
        rows: list[BasePriceRow] = []
        with self._csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for raw in reader:
                tipo = str(raw.get("tipo_vehiculo", "")).strip()
                precio = str(raw.get("precio_base", "")).strip()
                if not tipo or not precio:
                    continue
                rows.append(BasePriceRow(tipo_vehiculo=tipo, precio_base=int(precio)))
        return tuple(rows)

    def find_base_price(self, tipo_vehiculo: str) -> BasePriceRow | None:
        normalized = self._normalize(tipo_vehiculo)
        for row in self.load_base_prices():
            if self._normalize(row.tipo_vehiculo) == normalized:
                return row
        return None

    def find_zone_by_group_and_detail(
        self,
        db: Session,
        zone_group: str | None,
        zone_detail: str | None,
    ) -> ViaticosZone | None:
        normalized_group = self._normalize(zone_group)
        normalized_detail = self._normalize(zone_detail)

        if normalized_group and normalized_detail:
            row = db.execute(
                select(ViaticosZone)
                .where(func.lower(func.trim(ViaticosZone.zone_group)) == normalized_group)
                .where(func.lower(func.trim(ViaticosZone.zone_detail)) == normalized_detail)
                .limit(1)
            ).scalars().first()
            if row:
                return row

        if normalized_detail:
            stmt = (
                select(ViaticosZone)
                .where(func.lower(func.trim(ViaticosZone.zone_detail)) == normalized_detail)
                .order_by(ViaticosZone.zone_group.asc())
            )
            matches = db.execute(stmt).scalars().all()
            if normalized_group:
                for row in matches:
                    if self._normalize(row.zone_group) == normalized_group:
                        return row
            if matches:
                return matches[0]

        if normalized_group:
            return db.execute(
                select(ViaticosZone)
                .where(func.lower(func.trim(ViaticosZone.zone_group)) == normalized_group)
                .where(ViaticosZone.zone_detail.is_(None))
                .limit(1)
            ).scalars().first()

        return None

    @staticmethod
    def _normalize(value: str | None) -> str:
        return " ".join((value or "").strip().lower().split())

