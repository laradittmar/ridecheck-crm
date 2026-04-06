# app/api/revisions.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..models import Lead, Revision
from ..schemas.revisions import RevisionCreate, RevisionUpdate, RevisionOut
from ..services.db_errors import commit_or_400
from ..services.pricing import PricingService
from ..repositories.pricing_repository import PricingRepository

router = APIRouter(prefix="/leads/{lead_id}/revisions", tags=["revisions"])


def _recalc_quote_if_possible(db: Session, rev: Revision) -> None:
    PricingService(repository=PricingRepository()).recalculate_revision_if_possible(db=db, revision=rev)


def _apply_revision_update(db: Session, revision: Revision, payload: RevisionUpdate) -> None:
    data = payload.model_dump(exclude_unset=True)
    recalc = data.pop("recalcular_presupuesto", True)

    for k, v in data.items():
        setattr(revision, k, v)

    if recalc:
        if "tipo_vehiculo" in data and "precio_base" not in data:
            revision.precio_base = None
        if ("zone_group" in data or "zone_detail" in data) and "viaticos" not in data:
            revision.viaticos = None
        if "precio_total" not in data:
            revision.precio_total = None

        _recalc_quote_if_possible(db, revision)

    if revision.precio_total is None and revision.precio_base is not None and revision.viaticos is not None:
        revision.precio_total = revision.precio_base + revision.viaticos


@router.post("", response_model=RevisionOut)
def create_revision(lead_id: int, payload: RevisionCreate, db: Session = Depends(get_db)):
    if not db.get(Lead, lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    rev = Revision(
        lead_id=lead_id,

        tipo_vehiculo=payload.tipo_vehiculo,
        marca=payload.marca,
        modelo=payload.modelo,
        anio=payload.anio,
        link_compra=payload.link_compra,
        presupuesto_compra=payload.presupuesto_compra,
        vendedor_tipo=payload.vendedor_tipo,
        tipo_vendedor=payload.tipo_vendedor or payload.vendedor_tipo,
        agencia_id=payload.agencia_id,
        compro=payload.compro,
        resultado_link=payload.resultado_link,
        comision=payload.comision,
        cobrado=payload.cobrado,
        fecha_cobro=payload.fecha_cobro,

        zone_group=payload.zone_group,
        zone_detail=payload.zone_detail,
        direccion_texto=payload.direccion_texto,
        link_maps=payload.link_maps,
        direccion_estado=payload.direccion_estado,

        precio_base=payload.precio_base,
        viaticos=payload.viaticos,
        precio_total=payload.precio_total,
        pago=payload.pago,
        medio_pago=payload.medio_pago,

        turno_fecha=payload.turno_fecha,
        turno_hora=payload.turno_hora,
        cliente_presente=payload.cliente_presente,
        turno_notas=payload.turno_notas,

        estado_revision=(payload.estado_revision or "PENDIENTE"),
        resultado=payload.resultado,
        motivo_rechazo=payload.motivo_rechazo,
    )

    _recalc_quote_if_possible(db, rev)

    db.add(rev)
    commit_or_400(db, detail="No se pudo crear la revisión: revisá longitudes y valores permitidos")
    db.refresh(rev)
    return rev


@router.get("", response_model=list[RevisionOut])
def list_revisions(lead_id: int, db: Session = Depends(get_db)):
    if not db.get(Lead, lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    return db.execute(
        select(Revision)
        .where(Revision.lead_id == lead_id)
        .order_by(Revision.created_at.desc())
    ).scalars().all()


@router.get("/latest", response_model=RevisionOut)
def get_latest_revision(lead_id: int, db: Session = Depends(get_db)):
    if not db.get(Lead, lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    latest = db.execute(
        select(Revision)
        .where(Revision.lead_id == lead_id)
        .order_by(Revision.created_at.desc())
        .limit(1)
    ).scalars().first()

    if not latest:
        raise HTTPException(status_code=404, detail="No revisions for this lead yet")
    return latest


@router.patch("/latest", response_model=RevisionOut)
def update_latest_revision(lead_id: int, payload: RevisionUpdate, db: Session = Depends(get_db)):
    if not db.get(Lead, lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    latest = db.execute(
        select(Revision)
        .where(Revision.lead_id == lead_id)
        .order_by(Revision.created_at.desc())
        .limit(1)
    ).scalars().first()

    if not latest:
        raise HTTPException(status_code=404, detail="No revisions for this lead yet")

    _apply_revision_update(db, latest, payload)
    commit_or_400(db, detail="No se pudo actualizar la revisión: revisá longitudes y valores permitidos")
    db.refresh(latest)
    return latest
