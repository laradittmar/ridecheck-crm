from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..repositories.pricing_repository import PricingRepository
from ..schemas.pricing import PricingQuoteIn, PricingQuoteOut
from ..services.pricing import PricingNotFoundError, PricingService

router = APIRouter(prefix="/api/pricing", tags=["pricing"])


def get_pricing_service() -> PricingService:
    return PricingService(repository=PricingRepository())


@router.post("/quote", response_model=PricingQuoteOut)
def quote_pricing(
    payload: PricingQuoteIn,
    db: Session = Depends(get_db),
    service: PricingService = Depends(get_pricing_service),
):
    try:
        quote = service.quote(
            db=db,
            tipo_vehiculo=payload.tipo_vehiculo,
            zone_group=payload.zone_group,
            zone_detail=payload.zone_detail,
        )
    except PricingNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Pricing matrix match not found") from exc

    return PricingQuoteOut(
        tipo_vehiculo=quote.tipo_vehiculo,
        zone_group=quote.zone_group,
        zone_detail=quote.zone_detail,
        precio_base=quote.precio_base,
        viaticos=quote.viaticos,
        precio_total=quote.precio_total,
    )
