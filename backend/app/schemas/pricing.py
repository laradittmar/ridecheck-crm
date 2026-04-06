from __future__ import annotations

from pydantic import BaseModel, Field


class PricingQuoteIn(BaseModel):
    tipo_vehiculo: str = Field(min_length=1, max_length=30)
    zone_group: str = Field(min_length=1, max_length=50)
    zone_detail: str = Field(min_length=1, max_length=80)


class PricingQuoteOut(BaseModel):
    tipo_vehiculo: str
    zone_group: str
    zone_detail: str
    precio_base: int
    viaticos: int
    precio_total: int
