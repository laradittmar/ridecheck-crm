# app/ui/kanban_actions.py
from __future__ import annotations

from datetime import date, time, datetime, timedelta
from typing import Any
from threading import Lock
import secrets
import os
from pathlib import Path

from fastapi import Depends, HTTPException, Form, Body, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..db import get_db
from ..models import Agencia, Lead, Revision, Profesional, Vendedor
from .kanban_view import (
    ESTADOS_VALIDOS,
    MOTIVOS_PERDIDA_VALIDOS,
    CANAL_OPCIONES,
    ESTADO_REVISION_OPCIONES,
    recalc_quote_if_possible,
    FLAG_VALUES,
    DEFAULT_OPER_ESTADO,
)

PENDING_DELETE_TTL_SECONDS = 7
_PENDING_DELETES: dict[str, dict[str, Any]] = {}
_PENDING_LOCK = Lock()

def _has(obj: Any, field: str) -> bool:
    return hasattr(obj, field)

def _set_if_has(obj: Any, field: str, value: Any) -> None:
    if _has(obj, field):
        setattr(obj, field, value)

def _clean_str(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    if s == "" or s.lower() == "string":
        return None
    return s

def _to_int(v: str | None) -> int | None:
    s = _clean_str(v)
    if s is None:
        return None
    return int(s)

def _to_bool(v: str | None) -> bool | None:
    s = _clean_str(v)
    if s is None:
        return None
    s2 = s.lower()
    if s2 in ("true", "si", "sí", "1"):
        return True
    if s2 in ("false", "no", "0"):
        return False
    return None

def _to_date(v: str | None) -> date | None:
    s = _clean_str(v)
    if s is None:
        return None
    return date.fromisoformat(s)

def _to_time(v: str | None) -> time | None:
    s = _clean_str(v)
    if s is None:
        return None
    return time.fromisoformat(s)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _save_uploaded_file(upload: UploadFile | None) -> tuple[str | None, str | None, datetime | None]:
    if not upload or not upload.filename:
        return None, None, None
    base_dir = os.getenv("UPLOAD_DIR", "app/uploads")
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    safe_name = f"{int(datetime.utcnow().timestamp())}_{Path(upload.filename).name}"
    abs_path = Path(base_dir) / safe_name
    with abs_path.open("wb") as out:
        out.write(upload.file.read())
    return str(abs_path), Path(upload.filename).name, datetime.utcnow()


def _pending_store(payload: dict[str, Any]) -> dict[str, Any]:
    token = secrets.token_urlsafe(18)
    deadline = _utcnow() + timedelta(seconds=PENDING_DELETE_TTL_SECONDS)
    item = dict(payload)
    item["token"] = token
    item["deadline"] = deadline
    with _PENDING_LOCK:
        _PENDING_DELETES[token] = item
    return item


def _pending_pop(token: str) -> dict[str, Any] | None:
    with _PENDING_LOCK:
        return _PENDING_DELETES.pop(token, None)


def _pending_take_if_expired(token: str) -> tuple[dict[str, Any] | None, str]:
    """
    Atomically fetch+remove a pending delete only when its deadline passed.
    This keeps commit/undo resilient when both actions race.
    """
    with _PENDING_LOCK:
        item = _PENDING_DELETES.get(token)
        if not item:
            return None, "missing"
        deadline = item.get("deadline")
        if not isinstance(deadline, datetime):
            _PENDING_DELETES.pop(token, None)
            return None, "invalid"
        if _utcnow() < deadline:
            return None, "not_expired"
        return _PENDING_DELETES.pop(token, None), "ok"


# -------- Lead actions --------

def ui_lead_create(
    nombre: str | None = Form(None),
    apellido: str | None = Form(None),
    telefono: str | None = Form(None),
    tel: str | None = Form(None),
    email: str | None = Form(None),
    canal: str | None = Form(None),
    compro_el_auto: str | None = Form(None),  # <-- correct field name
    db: Session = Depends(get_db),
):
    lead = Lead()

    _set_if_has(lead, "nombre", _clean_str(nombre))
    _set_if_has(lead, "apellido", _clean_str(apellido))
    telefono_val = _clean_str(telefono)
    if telefono_val is None:
        telefono_val = _clean_str(tel)
    _set_if_has(lead, "telefono", telefono_val)
    _set_if_has(lead, "email", _clean_str(email))

    if _has(lead, "canal"):
        c = _clean_str(canal)
        if c and c not in CANAL_OPCIONES:
            c = None
        lead.canal = c

    # FIX: model uses string "SI"/"NO" in compro_el_auto
    if _has(lead, "compro_el_auto"):
        val = _clean_str(compro_el_auto)
        if val in ("SI", "NO"):
            lead.compro_el_auto = val
        else:
            lead.compro_el_auto = None

    if _has(lead, "estado") and getattr(lead, "estado", None) is None:
        lead.estado = "CONSULTA_NUEVA"
    if _has(lead, "necesita_humano") and getattr(lead, "necesita_humano", None) is None:
        lead.necesita_humano = False

    db.add(lead)
    db.commit()
    return RedirectResponse(url="/kanban", status_code=303)


def ui_lead_update(
    lead_id: int = Form(...),
    nombre: str | None = Form(None),
    apellido: str | None = Form(None),
    telefono: str | None = Form(None),
    tel: str | None = Form(None),
    email: str | None = Form(None),
    canal: str | None = Form(None),
    compro_el_auto: str | None = Form(None),  # <-- correct field name
    necesita_humano: str | None = Form(None),
    estado: str | None = Form(None),
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    _set_if_has(lead, "nombre", _clean_str(nombre))
    _set_if_has(lead, "apellido", _clean_str(apellido))
    telefono_val = _clean_str(telefono)
    if telefono_val is None:
        telefono_val = _clean_str(tel)
    _set_if_has(lead, "telefono", telefono_val)
    _set_if_has(lead, "email", _clean_str(email))

    if _has(lead, "canal"):
        c = _clean_str(canal)
        if c and c not in CANAL_OPCIONES:
            c = None
        lead.canal = c

    if _has(lead, "compro_el_auto"):
        val = _clean_str(compro_el_auto)
        if val in ("SI", "NO"):
            lead.compro_el_auto = val
        else:
            lead.compro_el_auto = None

    if _has(lead, "necesita_humano"):
        parsed = _to_bool(necesita_humano)
        if parsed is not None:
            lead.necesita_humano = parsed

    if _has(lead, "estado"):
        s = _clean_str(estado)
        if s in ESTADOS_VALIDOS:
            lead.estado = s

    db.commit()
    return RedirectResponse(url=f"/kanban#lead-{lead_id}", status_code=303)


def ui_lead_delete(
    lead_id: int = Form(...),
    db: Session = Depends(get_db),
):
    # Safe fallback endpoint: real delete now uses request/undo/commit flow.
    return RedirectResponse(url="/kanban", status_code=303)


# -------- Kanban state actions --------

def ui_move(lead_id: int = Form(...), estado: str = Form(...), db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if estado not in ESTADOS_VALIDOS:
        raise HTTPException(status_code=400, detail="Estado inválido")
    lead.estado = estado
    if _has(lead, "motivo_perdida") and _has(lead, "flag"):
        if lead.flag != "PERDIDO":
            lead.motivo_perdida = None
    db.commit()
    return RedirectResponse(url="/kanban", status_code=303)


def ui_human(lead_id: int = Form(...), necesita_humano: str = Form(...), db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.necesita_humano = (necesita_humano.lower() == "true")
    db.commit()
    return RedirectResponse(url="/kanban", status_code=303)


def ui_move_lead(
    lead_id: int | None = Form(None),
    estado: str | None = Form(None),
    new_estado: str | None = Form(None),
    payload: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
):
    if payload:
        if lead_id is None:
            raw_id = payload.get("lead_id")
            try:
                lead_id = int(raw_id)
            except (TypeError, ValueError):
                lead_id = None
        if not new_estado:
            new_estado = payload.get("new_estado")
        if not estado:
            estado = payload.get("estado")

    if lead_id is None:
        raise HTTPException(status_code=400, detail="lead_id requerido")
    target_estado = _clean_str(new_estado) or _clean_str(estado)
    if not target_estado:
        raise HTTPException(status_code=400, detail="estado requerido")

    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if target_estado not in ESTADOS_VALIDOS:
        raise HTTPException(status_code=400, detail="Estado inválido")
    lead.estado = target_estado
    if _has(lead, "motivo_perdida") and _has(lead, "flag"):
        if lead.flag != "PERDIDO":
            lead.motivo_perdida = None
    db.commit()
    return JSONResponse({"ok": True, "estado": target_estado})


def ui_lead_move(lead_id: int, estado: str = Form(...), db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if estado not in ESTADOS_VALIDOS:
        raise HTTPException(status_code=400, detail="Estado inválido")
    lead.estado = estado
    if _has(lead, "motivo_perdida") and _has(lead, "flag"):
        if lead.flag != "PERDIDO":
            lead.motivo_perdida = None
    db.commit()
    return JSONResponse({"ok": True})


def ui_lead_toggle_humano(
    lead_id: int = Form(...),
    value: str = Form(...),
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead.necesita_humano = (str(value).strip() == "1")
    db.commit()
    return RedirectResponse(url="/kanban", status_code=303)


def ui_perdido(lead_id: int = Form(...), motivo_perdida: str | None = Form(None), db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if _has(lead, "flag"):
        lead.flag = "PERDIDO"
    if _has(lead, "motivo_perdida"):
        lead.motivo_perdida = _clean_str(motivo_perdida)
    db.commit()
    return RedirectResponse(url="/kanban", status_code=303)


# -------- Lead flag actions --------

def ui_lead_flag_set(
    lead_id: int = Form(...),
    flag: str = Form(...),
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if flag not in FLAG_VALUES:
        raise HTTPException(status_code=400, detail="Flag invÃ¡lido")
    if _has(lead, "flag"):
        lead.flag = flag
    if _has(lead, "motivo_perdida") and flag != "PERDIDO":
        lead.motivo_perdida = None
    db.commit()
    return RedirectResponse(url="/kanban", status_code=303)


def ui_lead_flag_clear(
    lead_id: int = Form(...),
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if _has(lead, "flag"):
        lead.flag = None
    if _has(lead, "motivo_perdida"):
        lead.motivo_perdida = None
    db.commit()
    return RedirectResponse(url="/kanban", status_code=303)


# -------- Revision actions --------

def ui_revision_create(
    lead_id: int = Form(...),
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Create a fresh new revision
    rev = Revision(lead_id=lead_id)
    db.add(rev)
    db.commit()
    return RedirectResponse(url="/kanban", status_code=303)


def ui_revision_latest_delete(
    lead_id: int = Form(...),
    db: Session = Depends(get_db),
):
    # Safe fallback endpoint: real delete now uses request/undo/commit flow.
    return RedirectResponse(url="/kanban", status_code=303)


def ui_request_delete_lead(
    lead_id: int = Form(...),
    db: Session = Depends(get_db),
):
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    item = _pending_store({
        "type": "lead",
        "lead_id": lead_id,
    })
    return JSONResponse({
        "ok": True,
        "token": item["token"],
        "lead_id": lead_id,
        "deadline_ts": int(item["deadline"].timestamp()),
        "countdown_seconds": PENDING_DELETE_TTL_SECONDS,
    })


def ui_request_delete_revision(
    lead_id: int = Form(...),
    revision_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    if not db.get(Lead, lead_id):
        raise HTTPException(status_code=404, detail="Lead not found")

    target: Revision | None = None
    if revision_id is not None:
        target = db.get(Revision, revision_id)
        if not target or target.lead_id != lead_id:
            target = None
    if target is None:
        target = db.execute(
            select(Revision)
            .where(Revision.lead_id == lead_id)
            .order_by(Revision.created_at.desc(), Revision.id.desc())
            .limit(1)
        ).scalars().first()

    if not target:
        raise HTTPException(status_code=404, detail="No revisions for this lead yet")

    item = _pending_store({
        "type": "revision",
        "lead_id": lead_id,
        "revision_id": target.id,
    })
    return JSONResponse({
        "ok": True,
        "token": item["token"],
        "lead_id": lead_id,
        "revision_id": target.id,
        "deadline_ts": int(item["deadline"].timestamp()),
        "countdown_seconds": PENDING_DELETE_TTL_SECONDS,
    })


def ui_undo_delete(
    token: str = Form(...),
):
    item = _pending_pop(token)
    return JSONResponse({
        "ok": True,
        "undone": item is not None,
    })


def ui_commit_delete(
    token: str = Form(...),
    db: Session = Depends(get_db),
):
    item, state = _pending_take_if_expired(token)
    if not item:
        # Idempotent/no-crash path: token may already be undone/committed/invalid.
        return JSONResponse({"ok": True, "committed": False, "state": state})

    kind = item.get("type")
    if kind == "lead":
        lead_id = int(item.get("lead_id"))
        lead = db.get(Lead, lead_id)
        if lead:
            db.query(Revision).filter(Revision.lead_id == lead_id).delete(synchronize_session=False)
            db.delete(lead)
            db.commit()
    elif kind == "revision":
        rev_id = int(item.get("revision_id"))
        rev = db.get(Revision, rev_id)
        if rev:
            db.delete(rev)
            db.commit()
    else:
        return JSONResponse({"ok": True, "committed": False, "state": "unsupported_type"})

    return JSONResponse({"ok": True, "committed": True})


def ui_revision_latest_update(
    lead_id: int = Form(...),

    tipo_vehiculo: str | None = Form(None),
    vendedor_tipo: str | None = Form(None),
    tipo_vendedor: str | None = Form(None),
    agencia_id: str | None = Form(None),
    agencia_nueva_nombre: str | None = Form(None),
    marca: str | None = Form(None),
    modelo: str | None = Form(None),
    anio: str | None = Form(None),
    link_compra: str | None = Form(None),
    presupuesto_compra: str | None = Form(None),
    compro: str | None = Form(None),
    resultado_link: str | None = Form(None),
    comision: str | None = Form(None),
    cobrado: str | None = Form(None),
    fecha_cobro: str | None = Form(None),

    zone_group: str | None = Form(None),
    zone_detail: str | None = Form(None),
    direccion_texto: str | None = Form(None),
    link_maps: str | None = Form(None),
    direccion_estado: str | None = Form(None),

    precio_base: str | None = Form(None),
    viaticos: str | None = Form(None),
    precio_total: str | None = Form(None),
    recalcular_presupuesto: str = Form("true"),

    pago: str | None = Form(None),
    medio_pago: str | None = Form(None),

    turno_fecha: str | None = Form(None),
    turno_hora: str | None = Form(None),
    cliente_presente: str | None = Form(None),
    turno_notas: str | None = Form(None),

    estado_revision: str | None = Form(None),
    resultado: str | None = Form(None),
    motivo_rechazo: str | None = Form(None),
    profesional_id: str | None = Form(None),

    db: Session = Depends(get_db),
):
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

    latest.tipo_vehiculo = _clean_str(tipo_vehiculo)
    tipo_raw = _clean_str(tipo_vendedor) or _clean_str(vendedor_tipo)
    tipo_val = tipo_raw.upper() if tipo_raw else None
    latest.vendedor_tipo = tipo_val
    if hasattr(latest, "tipo_vendedor"):
        latest.tipo_vendedor = tipo_val

    agencia_id_val = None
    agencia_quick_name = _clean_str(agencia_nueva_nombre)
    if agencia_quick_name:
        new_ag = Agencia(nombre_agencia=agencia_quick_name)
        db.add(new_ag)
        db.flush()
        agencia_id_val = new_ag.id
    else:
        agencia_raw = _clean_str(agencia_id)
        if agencia_raw:
            try:
                agencia_id_val = int(agencia_raw)
            except ValueError:
                agencia_id_val = None
    if hasattr(latest, "agencia_id"):
        latest.agencia_id = agencia_id_val if tipo_val == "AGENCIA" else None
    latest.marca = _clean_str(marca)
    latest.modelo = _clean_str(modelo)
    latest.anio = _to_int(anio)
    latest.link_compra = _clean_str(link_compra)
    latest.presupuesto_compra = _to_int(presupuesto_compra)
    if hasattr(latest, "compro"):
        compro_raw = _clean_str(compro)
        compro_val = compro_raw.upper() if compro_raw else None
        latest.compro = compro_val if compro_val in {"SI", "NO", "OFRECIDO"} else None
    if hasattr(latest, "resultado_link"):
        latest.resultado_link = _clean_str(resultado_link)
    if hasattr(latest, "comision"):
        latest.comision = _to_int(comision)
    if hasattr(latest, "cobrado"):
        cobrado_raw = _clean_str(cobrado)
        cobrado_val = cobrado_raw.upper() if cobrado_raw else None
        latest.cobrado = cobrado_val if cobrado_val in {"SI", "NO"} else None
    if hasattr(latest, "fecha_cobro"):
        latest.fecha_cobro = _to_date(fecha_cobro)

    latest.zone_group = _clean_str(zone_group)
    latest.zone_detail = _clean_str(zone_detail)
    latest.direccion_texto = _clean_str(direccion_texto)
    latest.link_maps = _clean_str(link_maps)
    latest.direccion_estado = _clean_str(direccion_estado)

    latest.precio_base = _to_int(precio_base)
    latest.viaticos = _to_int(viaticos)
    latest.precio_total = _to_int(precio_total)

    latest.pago = _to_bool(pago)
    latest.medio_pago = _clean_str(medio_pago)

    latest.turno_fecha = _to_date(turno_fecha)
    latest.turno_hora = _to_time(turno_hora)
    latest.cliente_presente = _to_bool(cliente_presente)
    latest.turno_notas = _clean_str(turno_notas)

    # Operational status dropdown (only accept valid options if provided)
    er = _clean_str(estado_revision)
    if er is not None:
        if er in ESTADO_REVISION_OPCIONES:
            latest.estado_revision = er
        # else ignore silently (don’t overwrite with junk)

    latest.resultado = _clean_str(resultado)
    latest.motivo_rechazo = _clean_str(motivo_rechazo)

    prof_raw = _clean_str(profesional_id)
    if prof_raw is None:
        latest.profesional_id = None
    else:
        try:
            latest.profesional_id = int(prof_raw)
        except ValueError:
            latest.profesional_id = None

    if recalcular_presupuesto.strip().lower() == "true":
        if _clean_str(precio_base) is None:
            latest.precio_base = None
        if _clean_str(viaticos) is None:
            latest.viaticos = None
        if _clean_str(precio_total) is None:
            latest.precio_total = None
        recalc_quote_if_possible(db, latest)

    if latest.precio_total is None and latest.precio_base is not None and latest.viaticos is not None:
        latest.precio_total = latest.precio_base + latest.viaticos

    db.commit()
    return RedirectResponse(url="/kanban", status_code=303)


def ui_profesional_create(
    nombre: str = Form(...),
    apellido: str = Form(...),
    email: str = Form(...),
    telefono: str | None = Form(None),
    cargo: str | None = Form(None),
    db: Session = Depends(get_db),
):
    prof = Profesional(
        nombre=nombre.strip(),
        apellido=apellido.strip(),
        email=email.strip(),
        telefono=_clean_str(telefono),
        cargo=_clean_str(cargo),
    )
    db.add(prof)
    db.commit()
    return RedirectResponse(url="/profesionales", status_code=303)


def ui_vendedor_create(
    nombre: str = Form(...),
    db: Session = Depends(get_db),
):
    vend = Vendedor(nombre=nombre.strip())
    db.add(vend)
    db.commit()
    return RedirectResponse(url="/agencias", status_code=303)


def ui_agencia_create(
    nombre_agencia: str = Form(...),
    direccion: str | None = Form(None),
    gmaps: str | None = Form(None),
    mail: str | None = Form(None),
    vendedor_id: str | None = Form(None),
    vendedor_nuevo: str | None = Form(None),
    telefono: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    vendedor_fk = None
    vendedor_name = _clean_str(vendedor_nuevo)
    if vendedor_name:
        vend = Vendedor(nombre=vendedor_name)
        db.add(vend)
        db.flush()
        vendedor_fk = vend.id
    else:
        raw = _clean_str(vendedor_id)
        if raw:
            try:
                vendedor_fk = int(raw)
            except ValueError:
                vendedor_fk = None

    file_path, file_name, fecha_subido = _save_uploaded_file(file)
    ag = Agencia(
        nombre_agencia=nombre_agencia.strip(),
        direccion=_clean_str(direccion),
        gmaps=_clean_str(gmaps),
        mail=_clean_str(mail),
        vendedor_id=vendedor_fk,
        telefono=_clean_str(telefono),
        file_path=file_path,
        file_name=file_name,
        fecha_subido=fecha_subido,
    )
    db.add(ag)
    db.commit()
    return RedirectResponse(url="/agencias", status_code=303)


def ui_agencia_update(
    agencia_id: int = Form(...),
    nombre_agencia: str = Form(...),
    direccion: str | None = Form(None),
    gmaps: str | None = Form(None),
    mail: str | None = Form(None),
    vendedor_id: str | None = Form(None),
    vendedor_nuevo: str | None = Form(None),
    telefono: str | None = Form(None),
    file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    ag = db.get(Agencia, agencia_id)
    if not ag:
        raise HTTPException(status_code=404, detail="Agencia not found")

    vendedor_fk = None
    vendedor_name = _clean_str(vendedor_nuevo)
    if vendedor_name:
        vend = Vendedor(nombre=vendedor_name)
        db.add(vend)
        db.flush()
        vendedor_fk = vend.id
    else:
        raw = _clean_str(vendedor_id)
        if raw:
            try:
                vendedor_fk = int(raw)
            except ValueError:
                vendedor_fk = None

    ag.nombre_agencia = nombre_agencia.strip()
    ag.direccion = _clean_str(direccion)
    ag.gmaps = _clean_str(gmaps)
    ag.mail = _clean_str(mail)
    ag.vendedor_id = vendedor_fk
    ag.telefono = _clean_str(telefono)

    file_path, file_name, fecha_subido = _save_uploaded_file(file)
    if file_path:
        ag.file_path = file_path
        ag.file_name = file_name
        ag.fecha_subido = fecha_subido

    db.commit()
    return RedirectResponse(url="/agencias", status_code=303)


def ui_agencia_delete(
    agencia_id: int = Form(...),
    db: Session = Depends(get_db),
):
    ag = db.get(Agencia, agencia_id)
    if not ag:
        raise HTTPException(status_code=404, detail="Agencia not found")
    db.delete(ag)
    db.commit()
    return RedirectResponse(url="/agencias", status_code=303)


