"""M15.8 — Resend email notification service (HTTPS API, no SMTP)."""
from __future__ import annotations

import json
import logging
from urllib import error, request as urlrequest

logger = logging.getLogger(__name__)

_RESEND_API_URL = "https://api.resend.com/emails"


def _fmt(value: object, fallback: str = "Sin dato") -> str:
    s = str(value).strip() if value is not None else ""
    return s if s else fallback


def _fmt_money(value: str) -> str:
    """Format a numeric string as Argentine currency: 140000 → $140.000"""
    try:
        return f"${int(value):,}".replace(",", ".")
    except (ValueError, TypeError):
        return value if value else "Sin dato"


def _build_html(
    lead_id: int | str,
    revision_id: int | str,
    buyer_name: str,
    buyer_phone: str,
    buyer_email: str,
    source: str,
    marca: str,
    modelo: str,
    anio: str,
    tipo_vehiculo: str,
    zone_group: str,
    zone_detail: str,
    address: str,
    seller_type: str,
    seller_name: str,
    scheduled_date: str,
    scheduled_time: str,
    precio_base: str,
    viaticos: str,
    precio_total: str,
) -> str:
    # /calendar?highlight_lead_id=X&week=YYYY-MM-DD#day opens the calendar
    # on the appointment week and highlights the lead's slot.
    week_param = f"&week={scheduled_date}&date={scheduled_date}" if scheduled_date and scheduled_date != "Sin dato" else ""
    crm_cal_url  = f"https://crm.ridecheck.ar/calendar?highlight_lead_id={lead_id}{week_param}#day"
    crm_lead_url = f"https://crm.ridecheck.ar/kanban#lead-{lead_id}"
    precio_base_fmt  = _fmt_money(precio_base)
    viaticos_fmt     = _fmt_money(viaticos)
    precio_total_fmt = _fmt_money(precio_total)
    return f"""<!DOCTYPE html>
<html lang="es">
<body style="font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#1a1a2e;">🔔 Nueva solicitud de revisión</h2>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;"><strong>Lead ID:</strong></td><td>{lead_id}</td></tr>
<tr><td style="padding:4px 0;"><strong>Revisión ID:</strong></td><td>{revision_id}</td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Cliente</h3>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;width:140px;"><strong>Nombre:</strong></td><td>{buyer_name}</td></tr>
<tr><td style="padding:4px 0;"><strong>Teléfono:</strong></td><td>{buyer_phone}</td></tr>
<tr><td style="padding:4px 0;"><strong>Email:</strong></td><td>{buyer_email}</td></tr>
<tr><td style="padding:4px 0;"><strong>Cómo llegó:</strong></td><td>{source}</td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Vehículo</h3>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;width:140px;"><strong>Vehículo:</strong></td><td>{marca} {modelo} {anio}</td></tr>
<tr><td style="padding:4px 0;"><strong>Tipo:</strong></td><td>{tipo_vehiculo}</td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Ubicación</h3>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;width:140px;"><strong>Zona:</strong></td><td>{zone_group} / {zone_detail}</td></tr>
<tr><td style="padding:4px 0;"><strong>Dirección:</strong></td><td>{address}</td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Vendedor</h3>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;width:140px;"><strong>Tipo:</strong></td><td>{seller_type}</td></tr>
<tr><td style="padding:4px 0;"><strong>Nombre:</strong></td><td>{seller_name}</td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Turno solicitado</h3>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;width:140px;"><strong>Fecha:</strong></td><td>{scheduled_date}</td></tr>
<tr><td style="padding:4px 0;"><strong>Hora:</strong></td><td>{scheduled_time}</td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Presupuesto</h3>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;width:140px;"><strong>Base:</strong></td><td>{precio_base_fmt}</td></tr>
<tr><td style="padding:4px 0;"><strong>Viáticos:</strong></td><td>{viaticos_fmt}</td></tr>
<tr><td style="padding:4px 0;"><strong>Total:</strong></td><td><strong>{precio_total_fmt}</strong></td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">CRM</h3>
<table cellpadding="0" cellspacing="0" border="0" style="margin:12px 0 8px;">
  <tr>
    <td align="center" bgcolor="#0f766e" style="border-radius:6px;">
      <a href="{crm_cal_url}"
         style="display:inline-block;padding:12px 28px;color:#ffffff;text-decoration:none;font-weight:bold;font-family:Arial,sans-serif;font-size:15px;border-radius:6px;background-color:#0f766e;">
        Abrir turno en CRM
      </a>
    </td>
  </tr>
</table>
<p style="font-size:12px;color:#888;margin-top:4px;">{crm_lead_url}</p>
</body>
</html>"""


def send_booking_notification(
    *,
    api_key: str,
    from_email: str,
    to_email: str,
    reply_to_email: str = "",
    lead_id: int | str,
    revision_id: int | str,
    buyer_name: str,
    buyer_phone: str,
    buyer_email: str,
    source: str,
    marca: str,
    modelo: str,
    anio: str,
    tipo_vehiculo: str,
    zone_group: str,
    zone_detail: str,
    address: str,
    seller_type: str,
    seller_name: str,
    scheduled_date: str,
    scheduled_time: str,
    precio_base: str,
    viaticos: str,
    precio_total: str,
) -> bool:
    """Send booking notification via Resend HTTPS API. Returns True on success, False on failure."""
    reply_to_email = (reply_to_email or "").strip()
    if not api_key:
        logger.error("[M15.8] RESEND_API_KEY not set — email not sent for revision_id=%s", revision_id)
        return False
    if not to_email:
        logger.error("[M15.8] INTERNAL_BOOKING_EMAIL_TO not set — email not sent for revision_id=%s", revision_id)
        return False

    subject = f"Nueva solicitud de revisión — Lead #{lead_id} / Revisión #{revision_id}"
    html_body = _build_html(
        lead_id=lead_id,
        revision_id=revision_id,
        buyer_name=_fmt(buyer_name),
        buyer_phone=_fmt(buyer_phone),
        buyer_email=_fmt(buyer_email),
        source=_fmt(source),
        marca=_fmt(marca),
        modelo=_fmt(modelo),
        anio=_fmt(anio),
        tipo_vehiculo=_fmt(tipo_vehiculo),
        zone_group=_fmt(zone_group),
        zone_detail=_fmt(zone_detail),
        address=_fmt(address),
        seller_type=_fmt(seller_type),
        seller_name=_fmt(seller_name),
        scheduled_date=_fmt(scheduled_date),
        scheduled_time=_fmt(scheduled_time),
        precio_base=_fmt(precio_base),
        viaticos=_fmt(viaticos),
        precio_total=_fmt(precio_total),
    )

    _payload: dict = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }
    if reply_to_email:
        _payload["reply_to"] = reply_to_email
    payload = json.dumps(_payload).encode("utf-8")

    req = urlrequest.Request(
        _RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "RideCheck-CRM/1.0",
        },
    )

    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        logger.info("[M15.8] Resend email sent OK for revision_id=%s lead_id=%s — %s", revision_id, lead_id, body)
        return True
    except error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        logger.error("[M15.8] Resend HTTP error %s for revision_id=%s: %s", exc.code, revision_id, err_body)
        return False
    except error.URLError as exc:
        logger.error("[M15.8] Resend URL error for revision_id=%s: %s", revision_id, exc.reason)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("[M15.8] Resend unexpected error for revision_id=%s: %s", revision_id, exc)
        return False


def _build_handoff_html(
    lead_id: int | str,
    thread_id: int | str,
    revision_id: int | str,
    buyer_name: str,
    buyer_phone: str,
    vehicle: str,
    tipo_vehiculo: str,
    zone_group: str,
    zone_detail: str,
    precio_total: str,
    requested_slot: str,
    offered_slots: str,
    last_message: str,
) -> str:
    crm_lead_url = f"https://crm.ridecheck.ar/kanban#lead-{lead_id}"
    precio_fmt = _fmt_money(precio_total) if precio_total not in ("Sin dato", "") else "Sin dato"
    return f"""<!DOCTYPE html>
<html lang="es">
<body style="font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#b45309;">⚠️ Cliente solicita horario no disponible — coordinación manual</h2>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;"><strong>Lead ID:</strong></td><td>{lead_id}</td></tr>
<tr><td style="padding:4px 0;"><strong>Thread ID:</strong></td><td>{thread_id}</td></tr>
<tr><td style="padding:4px 0;"><strong>Revisión provisional ID:</strong></td><td>{revision_id}</td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Cliente</h3>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;width:160px;"><strong>Nombre:</strong></td><td>{buyer_name}</td></tr>
<tr><td style="padding:4px 0;"><strong>WhatsApp:</strong></td><td>{buyer_phone}</td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Vehículo</h3>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;width:160px;"><strong>Vehículo:</strong></td><td>{vehicle}</td></tr>
<tr><td style="padding:4px 0;"><strong>Tipo:</strong></td><td>{tipo_vehiculo}</td></tr>
<tr><td style="padding:4px 0;"><strong>Zona:</strong></td><td>{zone_group} / {zone_detail}</td></tr>
<tr><td style="padding:4px 0;"><strong>Presupuesto total:</strong></td><td><strong>{precio_fmt}</strong></td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Turno solicitado (no disponible)</h3>
<table style="width:100%;border-collapse:collapse;">
<tr><td style="padding:4px 0;width:160px;"><strong>Horario pedido:</strong></td><td>{requested_slot}</td></tr>
<tr><td style="padding:4px 0;"><strong>Alternativas ofrecidas:</strong></td><td>{offered_slots}</td></tr>
</table>

<h3 style="color:#444;border-bottom:1px solid #ddd;padding-bottom:6px;">Último mensaje del cliente</h3>
<p style="background:#f5f5f5;padding:10px;border-radius:4px;font-style:italic;">"{last_message}"</p>

<p style="color:#b45309;font-weight:bold;">
  ⚠️ No se creó turno confirmado. Cliente aceptó presupuesto y está intentando coordinar.
  La revisión provisional está en estado PENDIENTE en el CRM.
</p>

<table cellpadding="0" cellspacing="0" border="0" style="margin:12px 0 8px;">
  <tr>
    <td align="center" bgcolor="#b45309" style="border-radius:6px;">
      <a href="{crm_lead_url}"
         style="display:inline-block;padding:12px 28px;color:#ffffff;text-decoration:none;font-weight:bold;font-family:Arial,sans-serif;font-size:15px;border-radius:6px;background-color:#b45309;">
        Ver lead en CRM
      </a>
    </td>
  </tr>
</table>
</body>
</html>"""


def send_scheduling_handoff_notification(
    *,
    api_key: str,
    from_email: str,
    to_email: str,
    reply_to_email: str = "",
    lead_id: int | str,
    thread_id: int | str,
    revision_id: int | str,
    buyer_name: str,
    buyer_phone: str,
    vehicle: str,
    tipo_vehiculo: str,
    zone_group: str,
    zone_detail: str,
    precio_total: str,
    requested_slot: str,
    offered_slots: str,
    last_message: str,
) -> bool:
    """Send a scheduling handoff alert to the ops inbox via Resend. Returns True on success."""
    reply_to_email = (reply_to_email or "").strip()
    if not api_key:
        logger.error("[M15.8] RESEND_API_KEY not set — handoff email not sent lead_id=%s", lead_id)
        return False
    if not to_email:
        logger.error("[M15.8] INTERNAL_BOOKING_EMAIL_TO not set — handoff email not sent lead_id=%s", lead_id)
        return False

    vehicle_short = vehicle or "Vehículo sin dato"
    zone_short = zone_detail or zone_group or "zona sin dato"
    subject = f"Cliente pide revisar disponibilidad manual — {vehicle_short} en {zone_short}"

    html_body = _build_handoff_html(
        lead_id=_fmt(lead_id),
        thread_id=_fmt(thread_id),
        revision_id=_fmt(revision_id),
        buyer_name=_fmt(buyer_name),
        buyer_phone=_fmt(buyer_phone),
        vehicle=_fmt(vehicle),
        tipo_vehiculo=_fmt(tipo_vehiculo),
        zone_group=_fmt(zone_group),
        zone_detail=_fmt(zone_detail),
        precio_total=_fmt(precio_total),
        requested_slot=_fmt(requested_slot),
        offered_slots=_fmt(offered_slots),
        last_message=_fmt(last_message),
    )

    _payload: dict = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }
    if reply_to_email:
        _payload["reply_to"] = reply_to_email
    payload = json.dumps(_payload).encode("utf-8")

    req = urlrequest.Request(
        _RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "RideCheck-CRM/1.0",
        },
    )

    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        logger.info("[M15.8] Handoff email sent OK lead_id=%s — %s", lead_id, body)
        return True
    except error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        logger.error("[M15.8] Handoff email HTTP error %s lead_id=%s: %s", exc.code, lead_id, err_body)
        return False
    except error.URLError as exc:
        logger.error("[M15.8] Handoff email URL error lead_id=%s: %s", lead_id, exc.reason)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("[M15.8] Handoff email unexpected error lead_id=%s: %s", lead_id, exc)
        return False


def send_human_review_notification(
    *,
    api_key: str,
    from_email: str,
    to_email: str,
    reply_to_email: str = "",
    lead_id: int | str,
    thread_id: int | str,
    wa_id: str,
    vehicle: str,
    zone_group: str,
    zone_detail: str,
    reason: str,
) -> bool:
    """Send an alert when a fallback Flow submit requires manual human review."""
    reply_to_email = (reply_to_email or "").strip()
    if not api_key:
        logger.error("[M15.8] RESEND_API_KEY not set — human review email not sent lead_id=%s", lead_id)
        return False
    if not to_email:
        logger.error("[M15.8] INTERNAL_BOOKING_EMAIL_TO not set — human review email not sent lead_id=%s", lead_id)
        return False

    reason_labels: dict[str, str] = {
        "unresolved_localidad": "Localidad no encontrada en base de viáticos",
        "otro_vehicle_type": "Tipo de vehículo 'OTRO' seleccionado en formulario",
        "otro_zona_general": "Zona 'OTRO' seleccionada en formulario de ubicación",
    }
    reason_label = reason_labels.get(reason, reason)
    vehicle_display = _fmt(vehicle)
    zone_display = f"{zone_group} / {zone_detail}" if zone_detail else zone_group or "Sin dato"
    crm_url = f"https://crm.ridecheck.ar/whatsapp/thread/{thread_id}"

    subject = f"⚠️ Revisión manual — {vehicle_display} · {_fmt(zone_display)}"

    html_body = f"""<!DOCTYPE html>
<html lang="es">
<body style="font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#b45309;">⚠️ Revisión manual requerida</h2>
<p>Un cliente completó un formulario de calificación pero no pudo resolverse automáticamente.</p>
<table style="width:100%;border-collapse:collapse;margin:12px 0;">
<tr><td style="padding:4px 0;width:160px;"><strong>Motivo:</strong></td><td>{_fmt(reason_label)}</td></tr>
<tr><td style="padding:4px 0;"><strong>Lead ID:</strong></td><td>{_fmt(lead_id)}</td></tr>
<tr><td style="padding:4px 0;"><strong>Thread ID:</strong></td><td>{_fmt(thread_id)}</td></tr>
<tr><td style="padding:4px 0;"><strong>WhatsApp:</strong></td><td>{_fmt(wa_id)}</td></tr>
<tr><td style="padding:4px 0;"><strong>Vehículo:</strong></td><td>{_fmt(vehicle)}</td></tr>
<tr><td style="padding:4px 0;"><strong>Zona:</strong></td><td>{_fmt(zone_display)}</td></tr>
</table>
<table cellpadding="0" cellspacing="0" border="0" style="margin:16px 0 8px;">
  <tr>
    <td align="center" bgcolor="#b45309" style="border-radius:6px;">
      <a href="{crm_url}"
         style="display:inline-block;padding:12px 28px;color:#fff;text-decoration:none;font-weight:bold;font-family:Arial,sans-serif;font-size:15px;border-radius:6px;background-color:#b45309;">
        Ver conversación en CRM
      </a>
    </td>
  </tr>
</table>
</body>
</html>"""

    _payload: dict = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }
    if reply_to_email:
        _payload["reply_to"] = reply_to_email
    payload = json.dumps(_payload).encode("utf-8")

    req = urlrequest.Request(
        _RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "RideCheck-CRM/1.0",
        },
    )

    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        logger.info(
            "[M15.8] Human review email sent OK lead_id=%s reason=%s — %s",
            lead_id, reason, body,
        )
        return True
    except error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        logger.error("[M15.8] Human review email HTTP error %s lead_id=%s: %s", exc.code, lead_id, err_body)
        return False
    except error.URLError as exc:
        logger.error("[M15.8] Human review email URL error lead_id=%s: %s", lead_id, exc.reason)
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("[M15.8] Human review email unexpected error lead_id=%s: %s", lead_id, exc)
        return False
