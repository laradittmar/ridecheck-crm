# app/api/leads.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import FeedbackPostRevision, Lead, Revision, WhatsAppThread
from ..schemas.leads import LeadCreate, LeadDetailOut, LeadOut, LeadUpdate
from ..schemas.revisions import RevisionSummaryOut
from ..schemas.whatsapp_api import WhatsAppThreadOut
from ..services.db_errors import commit_or_400
from ..services.phone_utils import normalize_phone_or_422, normalized_phone_sql
from ..services.whatsapp_threads import load_thread_payload

router = APIRouter(prefix="/leads", tags=["leads"])

ESTADOS_VALIDOS = {
    "CONSULTA_NUEVA",
    "COORDINAR_DISPONIBILIDAD",
    "AGENDADO",
    "REVISION_COMPLETA",
    "ATENCION_HUMANA",
}

FLAG_VALUES = {
    "PRESUPUESTANDO",
    "PRESUPUESTO_ENVIADO",
    "ACEPTADO",
    "RECOMPRA",
    "PERDIDO",
}

MOTIVOS_PERDIDA_VALIDOS = {"PRECIO", "DISPONIBILIDAD", "OTRO"}


@router.post("", response_model=LeadOut)
def create_lead(payload: LeadCreate, db: Session = Depends(get_db)):
    lead = Lead(
        estado="CONSULTA_NUEVA",
        telefono=normalize_phone_or_422(payload.telefono),
        nombre=payload.nombre,
        apellido=payload.apellido,
        email=payload.email,
        canal=payload.canal,
        compro_el_auto=payload.compro_el_auto,
        necesita_humano=False,
    )
    db.add(lead)
    commit_or_400(db, detail="No se pudo crear el lead: revisá longitudes y valores permitidos")
    db.refresh(lead)
    return lead


@router.get("", response_model=list[LeadOut])
def list_leads(telefono: str | None = Query(default=None), db: Session = Depends(get_db)):
    stmt = select(Lead)
    if telefono is not None:
        normalized = normalize_phone_or_422(telefono)
        if normalized is not None:
            stmt = stmt.where(normalized_phone_sql(Lead.telefono) == normalized)
    return db.execute(stmt.order_by(Lead.created_at.desc())).scalars().all()


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead(lead_id: int, payload: LeadUpdate, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if payload.estado is not None:
        if payload.estado not in ESTADOS_VALIDOS:
            raise HTTPException(status_code=400, detail="Estado inválido")

        lead.estado = payload.estado

        if payload.estado == "REVISION_COMPLETA" and lead.feedback is None:
            db.add(FeedbackPostRevision(lead_id=lead.id))

        if lead.flag != "PERDIDO":
            lead.motivo_perdida = None

    if getattr(payload, "flag", None) is not None:
        if payload.flag not in FLAG_VALUES:
            raise HTTPException(status_code=400, detail="Flag invÃ¡lido")
        lead.flag = payload.flag
        if payload.flag != "PERDIDO":
            lead.motivo_perdida = None

    if payload.motivo_perdida is not None:
        if lead.flag != "PERDIDO":
            raise HTTPException(status_code=400, detail="motivo_perdida solo aplica si flag=PERDIDO")
        if payload.motivo_perdida not in MOTIVOS_PERDIDA_VALIDOS:
            raise HTTPException(status_code=400, detail="Motivo de pérdida inválido")
        lead.motivo_perdida = payload.motivo_perdida

    if getattr(payload, "necesita_humano", None) is not None:
        lead.necesita_humano = payload.necesita_humano

    # optional lead fields
    if getattr(payload, "canal", None) is not None:
        lead.canal = payload.canal
    if getattr(payload, "compro_el_auto", None) is not None:
        lead.compro_el_auto = payload.compro_el_auto
    if getattr(payload, "email", None) is not None:
        lead.email = payload.email
    if getattr(payload, "nombre", None) is not None:
        lead.nombre = payload.nombre
    if getattr(payload, "apellido", None) is not None:
        lead.apellido = payload.apellido
    if hasattr(payload, "telefono") and payload.telefono is not None:
        lead.telefono = normalize_phone_or_422(payload.telefono)

    commit_or_400(db, detail="No se pudo actualizar el lead: revisá longitudes y valores permitidos")
    db.refresh(lead)
    return lead


@router.get("/{lead_id}", response_model=LeadDetailOut)
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    latest_revision = db.execute(
        select(Revision)
        .where(Revision.lead_id == lead_id)
        .order_by(Revision.created_at.desc(), Revision.id.desc())
        .limit(1)
    ).scalars().first()

    payload = LeadDetailOut.model_validate(lead)
    payload.latest_revision = RevisionSummaryOut.model_validate(latest_revision) if latest_revision else None
    return payload


@router.get("/{lead_id}/whatsapp", response_model=WhatsAppThreadOut)
def get_lead_whatsapp(lead_id: int, db: Session = Depends(get_db)):
    if not db.get(Lead, lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    thread = db.execute(
        select(WhatsAppThread)
        .where(WhatsAppThread.lead_id == lead_id)
        .order_by(WhatsAppThread.last_message_at.desc().nullslast(), WhatsAppThread.id.desc())
        .limit(1)
    ).scalars().first()
    if not thread:
        raise HTTPException(status_code=404, detail="WhatsApp thread not found for this lead")

    payload = load_thread_payload(db, thread.id)
    if payload is None:
        raise HTTPException(status_code=404, detail="WhatsApp thread not found for this lead")
    return payload
