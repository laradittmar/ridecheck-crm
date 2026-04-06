from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Revision
from ..repositories.pricing_repository import PricingRepository


@dataclass(frozen=True)
class PricingQuote:
    tipo_vehiculo: str
    zone_group: str
    zone_detail: str
    precio_base: int
    viaticos: int

    @property
    def precio_total(self) -> int:
        return self.precio_base + self.viaticos


class PricingNotFoundError(Exception):
    pass


class PricingService:
    def __init__(self, repository: PricingRepository):
        self.repository = repository

    def quote(self, db: Session, tipo_vehiculo: str, zone_group: str, zone_detail: str) -> PricingQuote:
        canonical_tipo = self._canonical_vehicle_type(tipo_vehiculo)
        price_row = self.repository.find_base_price(canonical_tipo)
        if price_row is None:
            raise PricingNotFoundError("tipo_vehiculo_not_found")

        zone = self.repository.find_zone_by_group_and_detail(
            db=db,
            zone_group=zone_group,
            zone_detail=zone_detail,
        )
        if zone is None or zone.viaticos is None:
            raise PricingNotFoundError("zone_not_found")

        return PricingQuote(
            tipo_vehiculo=price_row.tipo_vehiculo,
            zone_group=(zone.zone_group or zone_group).strip(),
            zone_detail=(zone.zone_detail or zone_detail).strip(),
            precio_base=price_row.precio_base,
            viaticos=int(zone.viaticos),
        )

    def recalculate_revision_if_possible(self, db: Session, revision: Revision) -> None:
        if not revision.tipo_vehiculo:
            return
        if not revision.zone_group and not revision.zone_detail:
            return

        try:
            quote = self.quote(
                db=db,
                tipo_vehiculo=revision.tipo_vehiculo,
                zone_group=revision.zone_group or "",
                zone_detail=revision.zone_detail or "",
            )
        except PricingNotFoundError:
            if revision.precio_base is None:
                price_row = self.repository.find_base_price(self._canonical_vehicle_type(revision.tipo_vehiculo))
                if price_row is not None:
                    revision.precio_base = price_row.precio_base
            return

        if revision.precio_base is None:
            revision.precio_base = quote.precio_base
        if revision.viaticos is None:
            revision.viaticos = quote.viaticos
        if revision.precio_total is None:
            revision.precio_total = quote.precio_total

    @staticmethod
    def _canonical_vehicle_type(tipo_vehiculo: str) -> str:
        normalized = " ".join((tipo_vehiculo or "").strip().upper().replace("-", " ").replace("_", " ").split())
        aliases = {
            "AUTO": "AUTO",
            "SUV 4X4": "SUV/4x4",
            "SUV/4X4": "SUV/4x4",
            "SUV 4X4 DEPORTIVO": "SUV_4X4_DEPORTIVO",
            "SUV 4X4 DEPORTIVO ": "SUV_4X4_DEPORTIVO",
            "SUV_4X4_DEPORTIVO": "SUV_4X4_DEPORTIVO",
            "CLASICO": "CLASICO",
            "ESCANEO MOTOR": "ESCANEO_MOTOR",
            "ESCANEO_MOTOR": "ESCANEO_MOTOR",
            "MOTO": "MOTO",
        }
        return aliases.get(normalized, tipo_vehiculo.strip())
