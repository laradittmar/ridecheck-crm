# app/api/revisions.py
import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..models import Lead, Revision
from ..schemas.revisions import RevisionCreate, RevisionUpdate, RevisionOut
from ..services.db_errors import commit_or_400
from ..services.booking_authority import (
    BookingAttachmentPlan,
    BookingAuthority,
    BookingCommand,
    BookingOperation,
    BookingOutcome,
    BookingSource,
    PriceRecalculation,
    command_for_edit,
    normalize_status,
    status_occupies,
)
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
def create_revision(lead_id: int, payload: RevisionCreate, db: Session = Depends(get_db),
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if not db.get(Lead, lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    fields = payload.model_dump()
    fields["tipo_vendedor"] = payload.tipo_vendedor or payload.vendedor_tipo
    requested_status = fields.pop("estado_revision") or "PENDIENTE"
    turno_fecha = fields.pop("turno_fecha")
    turno_hora = fields.pop("turno_hora")

    # SB2A: only canonical statuses are ever written — a padded or aliased spelling can
    # neither slip a booking past the authority nor change what occupies the agenda.
    status = normalize_status(requested_status)
    if status is None:
        raise HTTPException(status_code=422, detail={
            "code": "VALIDATION_FAILED", "reason": "UNKNOWN_STATUS",
            "message": "Estado de revisión desconocido."})

    if not (turno_fecha is not None and turno_hora is not None and status_occupies(status)):
        # Not a booking: unscheduled, partial or non-occupying. Cannot occupy the agenda.
        rev = Revision(lead_id=lead_id, turno_fecha=turno_fecha, turno_hora=turno_hora,
                       estado_revision=status, **fields)
        _recalc_quote_if_possible(db, rev)
        db.add(rev)
        commit_or_400(db, detail="No se pudo crear la revisión: revisá longitudes y valores permitidos")
        db.refresh(rev)
        return rev

    command = BookingCommand(
        operation=BookingOperation.CREATE, source=BookingSource.API,
        idempotency_key=_api_idempotency_key(idempotency_key), lead_id=lead_id,
        requested_date=turno_fecha, requested_time=turno_hora, target_status=status,
        revision_fields=fields)
    # SB2A-R3: the price is a typed attachment the authority performs, not a callback.
    result = BookingAuthority(db).execute(
        command, attachments=BookingAttachmentPlan(price=PriceRecalculation()))
    if not result.ok:
        return _refusal_response(result)
    return db.get(Revision, result.revision_id)


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
def update_latest_revision(lead_id: int, payload: RevisionUpdate, db: Session = Depends(get_db),
                           if_match: str | None = Header(default=None, alias="If-Match"),
                           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
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

    # SB2A: booking fields become one command to the booking authority; everything else is
    # applied as before and commits in the same transaction (or not at all on a refusal).
    data = payload.model_dump(exclude_unset=True)
    booking = {k: data.pop(k) for k in _BOOKING_KEYS if k in data}
    _apply_revision_update(db, latest, RevisionUpdate(**data))

    command = command_for_edit(
        latest, source=BookingSource.API,
        new_date=booking.get("turno_fecha", latest.turno_fecha),
        new_time=booking.get("turno_hora", latest.turno_hora),
        new_status=booking.get("estado_revision"),
        new_profesional_id=None,
        expected_version=_parse_if_match(if_match),
        idempotency_key=_api_idempotency_key(idempotency_key))
    if command is None:
        commit_or_400(db, detail="No se pudo actualizar la revisión: revisá longitudes y valores permitidos")
        db.refresh(latest)
        return latest

    result = BookingAuthority(db).execute(command)
    if not result.ok:
        return _refusal_response(result)
    return db.get(Revision, result.revision_id)


# ── SB2A helpers ──────────────────────────────────────────────────────────────

_BOOKING_KEYS = ("turno_fecha", "turno_hora", "estado_revision")

#: A refusal is a controlled, structured response — never a 500 for a conflict.
_REFUSAL_STATUS = {
    BookingOutcome.SLOT_CONFLICT: 409, BookingOutcome.CALENDAR_EXCEPTION: 409,
    BookingOutcome.STALE_VERSION: 409, BookingOutcome.INVALID_TRANSITION: 409,
    BookingOutcome.MISSING_PROFESSIONAL: 409, BookingOutcome.VALIDATION_FAILED: 422,
    BookingOutcome.RETRYABLE_FAILURE: 503, BookingOutcome.INTERNAL_FAILURE: 500,
}


def _refusal_response(result) -> JSONResponse:
    body = {"detail": {
        "code": result.outcome.value,
        "reason": result.reason,
        "message": result.message,
        "retryable": result.retryable,
        "revision_id": result.revision_id,
        "booking_version": result.booking_version,
        "conflicts": [{"start": c.start.isoformat(timespec="minutes"),
                       "end": c.end.isoformat(timespec="minutes"), "kind": c.kind}
                      for c in result.conflicts],
    }}
    headers = {"Retry-After": "2"} if result.retryable else None
    return JSONResponse(status_code=_REFUSAL_STATUS.get(result.outcome, 409), content=body,
                        headers=headers)


def _api_idempotency_key(header: str | None) -> str | None:
    """The client's Idempotency-Key, bounded and opaque. None → the request is not idempotent."""
    if header is None or not header.strip():
        return None
    return "api:" + hashlib.sha256(header.strip().encode()).hexdigest()[:48]


def _parse_if_match(header: str | None) -> int | None:
    if header is None or not header.strip():
        return None
    raw = header.strip()
    if raw.startswith("W/"):
        raw = raw[2:]
    raw = raw.strip('"')
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail={
            "code": "VALIDATION_FAILED", "reason": "INVALID_IF_MATCH",
            "message": "If-Match debe ser la versión numérica del turno."})
