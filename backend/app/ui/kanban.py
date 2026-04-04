# app/ui/kanban.py
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import Date, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import (
    Agencia,
    Lead,
    Profesional,
    Revision,
    Vendedor,
    ViaticosZone,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
)
from . import kanban_actions as actions
from .kanban_view import (
    _lead_flag_value,
    render_agencias_page,
    render_calendar_page,
    render_page,
    render_profesionales_page,
    render_revisions_table_page,
)

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory="app/templates")


def _zones_map(db: Session) -> dict[str, list[str]]:
    zones_map: dict[str, list[str]] = {}
    zones = db.execute(select(ViaticosZone)).scalars().all()
    for z in zones:
        if not z.zone_group:
            continue
        zones_map.setdefault(z.zone_group, [])
        if z.zone_detail and z.zone_detail not in zones_map[z.zone_group]:
            zones_map[z.zone_group].append(z.zone_detail)
    for k in zones_map:
        zones_map[k].sort()
    return zones_map


def _load_filtered_leads(
    db: Session,
    q: str | None,
    estado: list[str] | None,
    flag: list[str] | None,
    canal: str | None,
    marca: str | None,
    anio: str | None,
    zone_group: str | None,
    turno_fecha_from: str | None,
    turno_fecha_to: str | None,
    tipo_vehiculo: str | None,
    modelo: str | None,
    zone_detail: str | None,
    estado_revision: str | None,
    profesional_id: str | None = None,
):
    q = (q or "").strip()
    estado_list = [s.strip() for s in (estado or []) if s and s.strip()]
    flag_list = [s.strip() for s in (flag or []) if s and s.strip()]
    canal = (canal or "").strip()
    marca = (marca or "").strip()
    modelo = (modelo or "").strip()
    tipo_vehiculo = (tipo_vehiculo or "").strip()
    zone_group = (zone_group or "").strip()
    zone_detail = (zone_detail or "").strip()
    estado_revision = (estado_revision or "").strip()
    profesional_id = (profesional_id or "").strip()

    anio_int = None
    anio_str = str(anio).strip() if anio is not None else ""
    if anio_str:
        try:
            anio_int = int(anio_str)
        except ValueError:
            anio_int = None

    tf_from = None
    tf_from_str = str(turno_fecha_from).strip() if turno_fecha_from is not None else ""
    if tf_from_str:
        try:
            tf_from = date.fromisoformat(tf_from_str)
        except ValueError:
            tf_from = None

    tf_to = None
    tf_to_str = str(turno_fecha_to).strip() if turno_fecha_to is not None else ""
    if tf_to_str:
        try:
            tf_to = date.fromisoformat(tf_to_str)
        except ValueError:
            tf_to = None

    prof_id_int = None
    if profesional_id:
        try:
            prof_id_int = int(profesional_id)
        except ValueError:
            prof_id_int = None

    stmt = select(Lead).options(
        selectinload(Lead.revisions).selectinload(Revision.profesional),
        selectinload(Lead.revisions).selectinload(Revision.agencia),
    ).order_by(Lead.created_at.desc())
    if estado_list:
        stmt = stmt.where(Lead.estado.in_(estado_list))
    if canal and hasattr(Lead, "canal"):
        stmt = stmt.where(Lead.canal == canal)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Lead.nombre.ilike(like))
            | (Lead.apellido.ilike(like))
            | (Lead.telefono.ilike(like))
            | (Lead.email.ilike(like))
        )
    leads = db.execute(stmt).scalars().all()

    if flag_list:
        leads = [l for l in leads if _lead_flag_value(l) in flag_list]

    if any([
        tipo_vehiculo,
        marca,
        modelo,
        anio_int is not None,
        zone_group,
        zone_detail,
        estado_revision,
        tf_from,
        tf_to,
        prof_id_int is not None,
    ]):
        filtered: list[Lead] = []
        for l in leads:
            revs = list(getattr(l, "revisions", []) or [])
            if not revs:
                continue
            latest = sorted(revs, key=lambda r: (r.created_at or datetime.min), reverse=True)[0]
            if tipo_vehiculo and (latest.tipo_vehiculo or "") != tipo_vehiculo:
                continue
            if marca and (marca.lower() not in (latest.marca or "").lower()):
                continue
            if modelo and (modelo.lower() not in (latest.modelo or "").lower()):
                continue
            if anio_int is not None and (latest.anio or None) != anio_int:
                continue
            if zone_group and (latest.zone_group or "") != zone_group:
                continue
            if zone_detail and (latest.zone_detail or "") != zone_detail:
                continue
            if estado_revision and (latest.estado_revision or "") != estado_revision:
                continue
            if tf_from and (not latest.turno_fecha or latest.turno_fecha < tf_from):
                continue
            if tf_to and (not latest.turno_fecha or latest.turno_fecha > tf_to):
                continue
            if prof_id_int is not None and (latest.profesional_id or None) != prof_id_int:
                continue
            filtered.append(l)
        leads = filtered

    return leads, {
        "q": q,
        "estado": estado_list,
        "flag": flag_list,
        "canal": canal,
        "tipo_vehiculo": tipo_vehiculo,
        "marca": marca,
        "modelo": modelo,
        "anio": anio_str,
        "zone_group": zone_group,
        "zone_detail": zone_detail,
        "estado_revision": estado_revision,
        "profesional_id": profesional_id,
        "turno_fecha_from": tf_from_str,
        "turno_fecha_to": tf_to_str,
        "zones_map": _zones_map(db),
    }


def _load_filtered_revisions(
    db: Session,
    q: str | None,
    estado: list[str] | None,
    flag: list[str] | None,
    profesional_id: str | None,
    canal: str | None,
    marca: str | None,
    anio: str | None,
    zone_group: str | None,
    zone_detail: str | None,
    estado_revision: str | None,
    tipo_vehiculo: str | None,
    modelo: str | None,
    from_date: str | None,
    to_date: str | None,
    date_field: str | None,
):
    q = (q or "").strip()
    estado_list = [s.strip() for s in (estado or []) if s and s.strip()]
    flag_list = [s.strip() for s in (flag or []) if s and s.strip()]
    profesional_id = (profesional_id or "").strip()
    canal = (canal or "").strip()
    marca = (marca or "").strip()
    anio_str = str(anio).strip() if anio is not None else ""
    zone_group = (zone_group or "").strip()
    zone_detail = (zone_detail or "").strip()
    estado_revision = (estado_revision or "").strip()
    tipo_vehiculo = (tipo_vehiculo or "").strip()
    modelo = (modelo or "").strip()
    from_date_str = str(from_date).strip() if from_date is not None else ""
    to_date_str = str(to_date).strip() if to_date is not None else ""
    date_field = (date_field or "turno").strip().lower()
    if date_field not in ("turno", "created_at"):
        date_field = "turno"

    anio_int = None
    if anio_str:
        try:
            anio_int = int(anio_str)
        except ValueError:
            anio_int = None

    prof_id_int = None
    if profesional_id:
        try:
            prof_id_int = int(profesional_id)
        except ValueError:
            prof_id_int = None

    d_from = None
    if from_date_str:
        try:
            d_from = date.fromisoformat(from_date_str)
        except ValueError:
            d_from = None

    d_to = None
    if to_date_str:
        try:
            d_to = date.fromisoformat(to_date_str)
        except ValueError:
            d_to = None

    stmt = select(Revision).join(Lead).options(
        selectinload(Revision.lead),
        selectinload(Revision.profesional),
        selectinload(Revision.agencia),
    ).order_by(Revision.created_at.desc(), Revision.id.desc())

    if estado_list:
        stmt = stmt.where(Lead.estado.in_(estado_list))
    if canal:
        stmt = stmt.where(Lead.canal == canal)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Lead.nombre.ilike(like))
            | (Lead.apellido.ilike(like))
            | (Lead.telefono.ilike(like))
            | (Lead.email.ilike(like))
            | (Revision.marca.ilike(like))
            | (Revision.modelo.ilike(like))
        )
    if tipo_vehiculo:
        stmt = stmt.where(Revision.tipo_vehiculo == tipo_vehiculo)
    if marca:
        stmt = stmt.where(Revision.marca.ilike(f"%{marca}%"))
    if modelo:
        stmt = stmt.where(Revision.modelo.ilike(f"%{modelo}%"))
    if anio_int is not None:
        stmt = stmt.where(Revision.anio == anio_int)
    if zone_group:
        stmt = stmt.where(Revision.zone_group == zone_group)
    if zone_detail:
        stmt = stmt.where(Revision.zone_detail == zone_detail)
    if estado_revision:
        stmt = stmt.where(Revision.estado_revision == estado_revision)
    if prof_id_int is not None:
        stmt = stmt.where(Revision.profesional_id == prof_id_int)
    if d_from:
        if date_field == "created_at":
            stmt = stmt.where(Revision.created_at.cast(Date) >= d_from)
        else:
            stmt = stmt.where(Revision.turno_fecha >= d_from)
    if d_to:
        if date_field == "created_at":
            stmt = stmt.where(Revision.created_at.cast(Date) <= d_to)
        else:
            stmt = stmt.where(Revision.turno_fecha <= d_to)

    revisions = db.execute(stmt).scalars().all()
    if flag_list:
        revisions = [r for r in revisions if r.lead and _lead_flag_value(r.lead) in flag_list]

    return revisions, {
        "q": q,
        "estado": estado_list,
        "flag": flag_list,
        "profesional_id": profesional_id,
        "canal": canal,
        "tipo_vehiculo": tipo_vehiculo,
        "marca": marca,
        "modelo": modelo,
        "anio": anio_str,
        "zone_group": zone_group,
        "zone_detail": zone_detail,
        "estado_revision": estado_revision,
        "from_date": from_date_str,
        "to_date": to_date_str,
        "date_field": date_field,
        "zones_map": _zones_map(db),
    }


@router.get("/kanban", response_class=HTMLResponse)
def kanban(
    request: Request,
    db: Session = Depends(get_db),
    q: str | None = Query(default=""),
    estado: list[str] | None = Query(default=None),
    flag: list[str] | None = Query(default=None),
    canal: str | None = Query(default=""),
    marca: str | None = Query(default=""),
    anio: str | None = Query(default=None),
    zone_group: str | None = Query(default=""),
    turno_fecha_from: str | None = Query(default=None),
    turno_fecha_to: str | None = Query(default=None),
    tipo_vehiculo: str | None = Query(default=None),
    modelo: str | None = Query(default=None),
    zone_detail: str | None = Query(default=None),
    estado_revision: str | None = Query(default=None),
):
    leads, filters = _load_filtered_leads(
        db=db,
        q=q,
        estado=estado,
        flag=flag,
        canal=canal,
        marca=marca,
        anio=anio,
        zone_group=zone_group,
        turno_fecha_from=turno_fecha_from,
        turno_fecha_to=turno_fecha_to,
        tipo_vehiculo=tipo_vehiculo,
        modelo=modelo,
        zone_detail=zone_detail,
        estado_revision=estado_revision,
    )
    profesionales = db.execute(
        select(Profesional).order_by(Profesional.nombre, Profesional.apellido)
    ).scalars().all()
    agencias = db.execute(select(Agencia).order_by(Agencia.nombre_agencia)).scalars().all()
    return HTMLResponse(
        render_page(
            leads,
            profesionales=profesionales,
            agencias=agencias,
            user_email=getattr(request.state, "user_email", ""),
            **filters,
        ),
        media_type="text/html; charset=utf-8",
    )


@router.get("/table", response_class=HTMLResponse)
def table_view(
    request: Request,
    db: Session = Depends(get_db),
    q: str | None = Query(default=""),
    estado: list[str] | None = Query(default=None),
    flag: list[str] | None = Query(default=None),
    profesional_id: str | None = Query(default=None),
    canal: str | None = Query(default=""),
    marca: str | None = Query(default=""),
    anio: str | None = Query(default=None),
    zone_group: str | None = Query(default=""),
    turno_fecha_from: str | None = Query(default=None),
    turno_fecha_to: str | None = Query(default=None),
    from_date: str | None = Query(default=None),
    to_date: str | None = Query(default=None),
    date_field: str | None = Query(default="turno"),
    tipo_vehiculo: str | None = Query(default=None),
    modelo: str | None = Query(default=None),
    zone_detail: str | None = Query(default=None),
    estado_revision: str | None = Query(default=None),
    open_filters: str | None = Query(default=None),
):
    # Backward-compatible aliases
    effective_from = from_date or turno_fecha_from
    effective_to = to_date or turno_fecha_to

    revisions, filters = _load_filtered_revisions(
        db=db,
        q=q,
        estado=estado,
        flag=flag,
        profesional_id=profesional_id,
        canal=canal,
        marca=marca,
        anio=anio,
        zone_group=zone_group,
        zone_detail=zone_detail,
        estado_revision=estado_revision,
        tipo_vehiculo=tipo_vehiculo,
        modelo=modelo,
        from_date=effective_from,
        to_date=effective_to,
        date_field=date_field,
    )
    profesionales = db.execute(
        select(Profesional).order_by(Profesional.nombre, Profesional.apellido)
    ).scalars().all()
    return HTMLResponse(
        render_revisions_table_page(
            revisions,
            profesionales=profesionales,
            open_filters=bool(open_filters),
            user_email=getattr(request.state, "user_email", ""),
            **filters,
        ),
        media_type="text/html; charset=utf-8",
    )


@router.get("/calendar", response_class=HTMLResponse)
def calendar(
    request: Request,
    db: Session = Depends(get_db),
    week: str | None = Query(default=None),
    q: str | None = Query(default=""),
    estado: list[str] | None = Query(default=None),
    flag: list[str] | None = Query(default=None),
    canal: str | None = Query(default=""),
    marca: str | None = Query(default=""),
    anio: str | None = Query(default=None),
    zone_group: str | None = Query(default=""),
    turno_fecha_from: str | None = Query(default=None),
    turno_fecha_to: str | None = Query(default=None),
    tipo_vehiculo: str | None = Query(default=None),
    modelo: str | None = Query(default=None),
    zone_detail: str | None = Query(default=None),
    estado_revision: str | None = Query(default=None),
):
    leads, _ = _load_filtered_leads(
        db=db,
        q=q,
        estado=estado,
        flag=flag,
        canal=canal,
        marca=marca,
        anio=anio,
        zone_group=zone_group,
        turno_fecha_from=turno_fecha_from,
        turno_fecha_to=turno_fecha_to,
        tipo_vehiculo=tipo_vehiculo,
        modelo=modelo,
        zone_detail=zone_detail,
        estado_revision=estado_revision,
    )
    profesionales = db.execute(
        select(Profesional).order_by(Profesional.nombre, Profesional.apellido)
    ).scalars().all()
    return HTMLResponse(
        render_calendar_page(
            leads,
            profesionales=profesionales,
            week=week,
            user_email=getattr(request.state, "user_email", ""),
        ),
        media_type="text/html; charset=utf-8",
    )


@router.get("/profesionales", response_class=HTMLResponse)
def profesionales(request: Request, db: Session = Depends(get_db)):
    profesionales = db.execute(
        select(Profesional).order_by(Profesional.nombre, Profesional.apellido)
    ).scalars().all()
    return HTMLResponse(
        render_profesionales_page(
            profesionales,
            user_email=getattr(request.state, "user_email", ""),
        ),
        media_type="text/html; charset=utf-8",
    )


@router.get("/agencias", response_class=HTMLResponse)
def agencias(request: Request, db: Session = Depends(get_db)):
    agencias = db.execute(
        select(Agencia).options(selectinload(Agencia.vendedor)).order_by(Agencia.nombre_agencia)
    ).scalars().all()
    vendedores = db.execute(select(Vendedor).order_by(Vendedor.nombre)).scalars().all()
    return HTMLResponse(
        render_agencias_page(
            agencias,
            vendedores=vendedores,
            user_email=getattr(request.state, "user_email", ""),
        ),
        media_type="text/html; charset=utf-8",
    )


@router.get("/integrations/whatsapp/debug", response_class=HTMLResponse)
def whatsapp_debug(request: Request, db: Session = Depends(get_db)):
    threads = db.execute(
        select(
            WhatsAppThread.id.label("thread_id"),
            WhatsAppContact.wa_id.label("wa_id"),
            WhatsAppContact.display_name.label("display_name"),
            WhatsAppThread.unread_count.label("unread_count"),
            WhatsAppThread.last_message_at.label("last_message_at"),
        )
        .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
        .order_by(WhatsAppThread.last_message_at.desc().nullslast(), WhatsAppThread.id.desc())
        .limit(20)
    ).mappings().all()
    messages = db.execute(
        select(
            WhatsAppThread.id.label("thread_id"),
            WhatsAppContact.wa_id.label("wa_id"),
            WhatsAppContact.display_name.label("display_name"),
            WhatsAppMessage.direction.label("direction"),
            WhatsAppMessage.status.label("status"),
            WhatsAppMessage.timestamp.label("timestamp"),
            WhatsAppMessage.text.label("text"),
            WhatsAppMessage.wa_message_id.label("wa_message_id"),
        )
        .join(WhatsAppThread, WhatsAppMessage.thread_id == WhatsAppThread.id)
        .join(WhatsAppContact, WhatsAppThread.contact_id == WhatsAppContact.id)
        .order_by(WhatsAppMessage.timestamp.desc(), WhatsAppMessage.id.desc())
        .limit(50)
    ).mappings().all()
    return templates.TemplateResponse(
        "whatsapp_debug.html",
        {
            "request": request,
            "threads": threads,
            "messages": messages,
            "user_email": getattr(request.state, "user_email", ""),
        },
    )


@router.get("/ui/agencia_file/{agencia_id}")
def agencia_file_download(agencia_id: int, db: Session = Depends(get_db)):
    ag = db.get(Agencia, agencia_id)
    if not ag or not ag.file_path:
        return HTMLResponse("Archivo no encontrado", status_code=404)
    p = Path(ag.file_path)
    if not p.exists() or not p.is_file():
        return HTMLResponse("Archivo no disponible", status_code=404)
    return FileResponse(str(p), filename=(ag.file_name or p.name))


# --- Lead actions ---
router.add_api_route("/ui/lead_create", actions.ui_lead_create, methods=["POST"])
router.add_api_route("/ui/lead_update", actions.ui_lead_update, methods=["POST"])
router.add_api_route("/ui/lead_toggle_humano", actions.ui_lead_toggle_humano, methods=["POST"])
router.add_api_route("/ui/lead_delete", actions.ui_lead_delete, methods=["POST"])
router.add_api_route("/ui/lead_flag_set", actions.ui_lead_flag_set, methods=["POST"])
router.add_api_route("/ui/lead_flag_clear", actions.ui_lead_flag_clear, methods=["POST"])

# --- Kanban actions ---
router.add_api_route("/ui/move", actions.ui_move, methods=["POST"])
router.add_api_route("/ui/move_lead", actions.ui_move_lead, methods=["POST"])
router.add_api_route("/ui/lead/{lead_id}/move", actions.ui_lead_move, methods=["POST"])
router.add_api_route("/ui/human", actions.ui_human, methods=["POST"])
router.add_api_route("/ui/perdido", actions.ui_perdido, methods=["POST"])
router.add_api_route("/ui/request_delete_lead", actions.ui_request_delete_lead, methods=["POST"])
router.add_api_route("/ui/request_delete_revision", actions.ui_request_delete_revision, methods=["POST"])
router.add_api_route("/ui/undo_delete", actions.ui_undo_delete, methods=["POST"])
router.add_api_route("/ui/commit_delete", actions.ui_commit_delete, methods=["POST"])

# --- Revision actions ---
router.add_api_route("/ui/revision_create", actions.ui_revision_create, methods=["POST"])
router.add_api_route("/ui/revision_latest_update", actions.ui_revision_latest_update, methods=["POST"])
router.add_api_route("/ui/revision_latest_delete", actions.ui_revision_latest_delete, methods=["POST"])

# --- Profesionales actions ---
router.add_api_route("/ui/profesional_create", actions.ui_profesional_create, methods=["POST"])

# --- Agencias / vendedores actions ---
router.add_api_route("/ui/vendedor_create", actions.ui_vendedor_create, methods=["POST"])
router.add_api_route("/ui/agencia_create", actions.ui_agencia_create, methods=["POST"])
router.add_api_route("/ui/agencia_update", actions.ui_agencia_update, methods=["POST"])
router.add_api_route("/ui/agencia_delete", actions.ui_agencia_delete, methods=["POST"])
