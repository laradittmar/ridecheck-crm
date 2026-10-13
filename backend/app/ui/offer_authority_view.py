"""G3-7R/S9 — the booking-offer authority, rendered for an operator.

The G3-7P architecture audit measured that the acceptance decision had no operator surface
at all. `whatsapp_booking_offers` had zero references in `app/ui/`, `app/api/` and
`app/routes/`; the classification of every refusal existed only in a `logger.info` line, so
reading it meant reading container logs — which is exactly the container dependence
CLAUDE.md's forensic contract refuses. And a Flow turn persists no hybrid-trace row, so the
Inspector could not answer for it either.

So this page answers, for a thread and for one offer, the questions an operator actually
has when a booking did or did not happen:

    was an offer ever made?            row existence
    is it still live?                  status
    why did it end?                    terminal_reason
    what was it for?                   candidate, category, canonical location
    what was the customer shown?       offered_date / offered_time (HISTORICAL metadata)
    was anything refused, and what?    refused_count / last_refusal
    did it become a booking?           accepted ThreadRevision + Revision
    was that booking asked for twice?  replay_count / last_replay_at
    which transport decided?           the transport column, derived below

WHAT IS DELIBERATELY NOT HERE. No raw Flow token: the table stores only
`sha256(token)` and even that is rendered as a short prefix, because a fingerprint is a
matching key, not something an operator needs to read in full. No WAMIDs — the offer table
holds none, and the prefix rendering below means none can arrive through it either. No phone
number and no customer prose: an offer is a commercial decision about a vehicle, a place and
a time, and none of those require quoting the customer. Everything shown is already governed
by the authenticated CRM's existing policy, because `/control` is a protected prefix and the
session guard in `main.py` applies to it.

Absence is rendered as absence. A thread with no offers gets the "no offers" page, not an
empty table that reads like a refusal.
"""
from __future__ import annotations

import html
from typing import Any, Optional

#: One label per lifecycle state and per outcome an operator must be able to tell apart.
#: Kept here rather than in prose elsewhere so the vocabulary and the renderer cannot drift.
STATUS_LABELS = {
    "ISSUED": ("EMITIDA", "ok",
               "una oferta viva: puede convertirse en reserva si el turno sigue libre"),
    "ACCEPTED": ("ACEPTADA", "good",
                 "la oferta fue aceptada; si además hay una revisión reservada, la reserva existe"),
    "WITHDRAWN": ("RETIRADA", "warn",
                  "lo que se iba a reservar cambió, así que la oferta se invalidó"),
    "SUPERSEDED": ("REEMPLAZADA", "mute",
                   "una oferta nueva la reemplazó; no es un error"),
    "EXPIRED": ("VENCIDA", "mute", "pasó la ventana de validez del token"),
}

#: The terminal reasons the authority writes, in the operator's language. A reason that is
#: not in this map is rendered verbatim rather than hidden — an unknown reason is
#: information, and swallowing it would be the kind of silent gap this page exists to close.
REASON_LABELS = {
    "VEHICLE_CHANGED": "cambió el vehículo",
    "LOCATION_CHANGED": "cambió la zona",
    "FOCUS_CHANGED": "el foco pasó a otro vehículo",
    "TEXT_CORRECTION": "el cliente corrigió un dato por texto",
    "CYCLE_RESET": "se abrió un ciclo nuevo",
    "OFFER_REJECTED": "el cliente rechazó la propuesta",
    "HUMAN_HANDOFF": "pasó a un asesor",
    "MOTO_HANDOFF": "consulta de moto: pasó a un asesor",
    "REPLACED_BY_NEW_OFFER": "reemplazada por una oferta nueva",
    "CONSUMED_BY_BOOKING": "se convirtió en una reserva",
    "DISPATCH_FAILED": "no se pudo enviar el formulario",
    "DISPATCH_PREREQUISITES": "faltaban datos para enviar el formulario",
}

#: The refusals, in the operator's language. Same rule: an unmapped value is shown as-is.
REFUSAL_LABELS = {
    "NO_TOKEN": "la respuesta no traía token",
    "UNKNOWN_TOKEN": "token inexistente, fabricado o alterado",
    "NO_OFFER_RECORD": "había un token espejo sin oferta detrás",
    "FOREIGN_OFFER": "la oferta era de otra conversación",
    "OFFER_WITHDRAWN": "la oferta ya estaba retirada",
    "SUPERSEDED_OFFER": "la oferta ya estaba reemplazada",
    "OFFER_ALREADY_ACCEPTED": "la oferta ya estaba aceptada",
    "OFFER_EXPIRED": "la oferta estaba vencida",
    "OFFER_CANDIDATE_CHANGED": "el vehículo de la oferta ya no está disponible",
    "OFFER_LOCATION_CHANGED": "cambió la zona canónica",
    "OFFER_VEHICLE_CATEGORY_CHANGED": "cambió la categoría del vehículo: hay que recotizar",
    "OFFER_VEHICLE_CATEGORY_UNRESOLVED": "no se pudo resolver la categoría: sin precio no se reserva",
    "OFFER_CYCLE_CHANGED": "la oferta pertenecía a un ciclo cerrado",
    "OFFER_CONTENDED": "otro escritor tenía la conversación tomada en ese momento",
    "OFFER_DEADLOCK": "DEADLOCK: dos escritores tomaron los recursos en orden incompatible",
    "SLOT_NO_LONGER_AVAILABLE": "el turno dejó de estar disponible en la revalidación final",
    "NO_CANONICAL_LOCATION": "sin zona canónica no se puede cotizar la reserva",
    "NO_SUBMITTED_SLOT": "no había fecha y hora utilizables para reservar",
}

#: Which code path decided this offer's outcome. Derived, not stored: the two transports
#: write different `flow_id` values — the published Booking Flow is endpoint-backed and its
#: acceptance runs in `BookingFlowService`, the legacy data-collection Flow's acceptance runs
#: in the ConversationEngine. Derivation is honest here because there is exactly one rule and
#: it is printed on the page beside the value.
TRANSPORT_NOTE = (
    "El transporte se deduce del Flow con el que se emitió la oferta: el Booking Flow "
    "publicado resuelve la aceptación en el endpoint de Data Exchange, y el Flow de "
    "recolección de datos la resuelve en el ConversationEngine.")

_CSS = """
:root{--bg:#f3f4f6;--card:#fff;--ink:#111827;--muted:#6b7280;--border:#e5e7eb;
--ok:#1d4ed8;--good:#047857;--warn:#b45309;--bad:#b91c1c;--mute:#6b7280}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:15px;margin:0 0 12px;text-transform:uppercase;letter-spacing:.06em;
color:var(--muted)}
.sub{color:var(--muted);margin:0 0 18px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;
padding:16px 18px;margin:0 0 16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.note{color:var(--muted);font-size:12.5px;margin:10px 0 0}
dl.kv{display:grid;grid-template-columns:minmax(150px,auto) 1fr;gap:6px 18px;margin:0}
dl.kv dt{color:var(--muted);font-size:12.5px}
dl.kv dd{margin:0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--border);
vertical-align:top}
th{color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11.5px;
font-weight:600;border:1px solid var(--border)}
.badge.ok{color:#1d4ed8;border-color:#bfdbfe;background:#eff6ff}
.badge.good{color:#047857;border-color:#a7f3d0;background:#ecfdf5}
.badge.warn{color:#b45309;border-color:#fde68a;background:#fffbeb}
.badge.bad{color:#b91c1c;border-color:#fecaca;background:#fef2f2}
.badge.mute{color:#4b5563;border-color:#e5e7eb;background:#f9fafb}
.empty{color:var(--muted);font-style:italic}
a{color:#1d4ed8}
"""


def _e(value: Any) -> str:
    """Escape, and render absence AS absence rather than as an empty cell."""
    if value is None or value == "":
        return "—"
    return html.escape(str(value))


def _page(title: str, body: str) -> str:
    return ('<!DOCTYPE html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
            f"<title>{html.escape(title)}</title>\n<style>{_CSS}</style>\n</head>\n"
            f'<body><div class="wrap">{body}</div></body>\n</html>')


def fingerprint_prefix(value: Optional[str]) -> str:
    """Render the already-minimised fingerprint prefix, and never more.

    The raw token is not in the database to leak, and the full fingerprint is not needed to
    read a page: twelve characters identify an offer among a thread's handful of them and
    let an operator match a log line, while being useless as a matching key against anything
    else. The suffix is elided visibly, so nothing on this page can be mistaken for a
    complete credential.

    R2/RV-05. The truncation now happens in the ACCESSOR, so the JSON endpoint that shares
    it cannot emit the full value either. This function renders what it is handed and
    re-truncates defensively, rather than being the only place minimisation happens — the
    independent review found the page truncating and the API not.
    """
    if not value:
        return "—"
    text = str(value)
    return html.escape(text[:12]) + "⋯" if len(text) > 12 else html.escape(text)


def status_badge(status: Optional[str]) -> str:
    label, cls, _ = STATUS_LABELS.get(str(status or ""), (str(status or "—"), "mute", ""))
    return f'<span class="badge {cls}">{html.escape(label)}</span>'


def transport_of(row: dict, booking_flow_id: Optional[str] = None) -> str:
    """Which writer decided this offer, from the Flow it was issued with."""
    flow_id = (row.get("flow_id") or "").strip()
    if not flow_id:
        return "—"
    if booking_flow_id and flow_id == str(booking_flow_id).strip():
        return "Data Exchange (Booking Flow)"
    return "ConversationEngine (Flow de datos)"


def outcome_of(row: dict) -> tuple:
    """The ONE headline an operator reads first, and why.

    Derived from the row, with the booking as the decisive fact rather than the status: an
    offer can be ACCEPTED while its booking write failed, and calling that "reservado" would
    be the kind of false business fact this project keeps closing.
    """
    status = str(row.get("status") or "")
    booked = row.get("accepted_thread_revision_id") is not None
    if status == "ACCEPTED" and booked:
        if (row.get("replay_count") or 0) > 0:
            return ("RESERVA CONFIRMADA · REENVIADA", "good",
                    "la reserva existe y la misma oferta se volvió a presentar al menos "
                    "una vez; el reenvío no creó nada y no es un rechazo")
        return ("RESERVA CONFIRMADA", "good", "la oferta se convirtió en una reserva")
    if status == "ACCEPTED" and not booked:
        return ("ACEPTADA SIN RESERVA", "bad",
                "la aceptación quedó registrada y no hay revisión reservada asociada: "
                "revisar, porque es el único estado que no debería existir")
    if status == "ISSUED":
        return ("ESPERANDO AL CLIENTE", "ok", STATUS_LABELS["ISSUED"][2])
    if (row.get("refused_count") or 0) > 0:
        return ("RECHAZADA", "warn",
                REFUSAL_LABELS.get(str(row.get("last_refusal") or ""),
                                   str(row.get("last_refusal") or "sin motivo registrado")))
    label, cls, note = STATUS_LABELS.get(status, (status or "—", "mute", ""))
    return (label, cls, note)


def _offer_card(row: dict, *, booking_flow_id=None) -> str:
    headline, cls, why = outcome_of(row)
    reason = row.get("terminal_reason")
    refusal = row.get("last_refusal")
    thread_id = row.get("thread_id")
    thread_link = (f'<a href="/whatsapp/thread/{html.escape(str(thread_id))}">'
                   f"conversación {html.escape(str(thread_id))}</a>"
                   if thread_id else "—")
    tr_id = row.get("accepted_thread_revision_id")
    rev_id = row.get("accepted_revision_id")
    booking = ("sin reserva" if tr_id is None else
               f'ThreadRevision <span class="mono">{_e(tr_id)}</span>'
               + (f' · Revisión <a href="/kanban">{_e(rev_id)}</a>' if rev_id else
                  ' · <span class="empty">Revisión CRM no registrada</span>'))
    pairs = [
        ("Resultado", f'<span class="badge {cls}">{html.escape(headline)}</span> '
                      f'<span class="note">{html.escape(why)}</span>'),
        ("Oferta", f'<span class="mono">#{_e(row.get("id"))}</span>'),
        ("Conversación", thread_link),
        ("Estado", status_badge(row.get("status"))),
        ("Motivo terminal", _e(REASON_LABELS.get(str(reason or ""), reason))),
        ("Transporte que decidió", _e(transport_of(row, booking_flow_id))),
        ("Vehículo (candidato)", _e(row.get("candidate_id"))),
        ("Categoría cotizada", _e(row.get("tipo_vehiculo"))),
        ("Zona canónica", _e(" / ".join(p for p in (row.get("zone_group"),
                                                    row.get("zone_detail")) if p))),
        ("Turno mostrado al cliente",
         _e(" ".join(str(p) for p in (row.get("offered_date"), row.get("offered_time"))
                     if p)) + ' <span class="note">(histórico: el cliente puede haber '
                              'elegido otro horario en el Flow)</span>'),
        ("Ciclo", _e(row.get("cycle_key"))),
        ("Huella del token", f'<span class="mono">'
                             f'{fingerprint_prefix(row.get("token_fingerprint_prefix"))}'
                             f'</span>'),
        ("Rechazos", f'{_e(row.get("refused_count"))}'
                     + (f' · último: {html.escape(REFUSAL_LABELS.get(str(refusal), str(refusal)))}'
                        if refusal else "")),
        ("Reenvíos (replay)", _e(row.get("replay_count"))),
        ("Reserva", booking),
        ("Emitida", _e(row.get("issued_at"))),
        ("Vence", _e(row.get("expires_at"))),
        ("Terminó", _e(row.get("terminal_at"))),
        ("Aceptada", _e(row.get("accepted_at"))),
        ("Último rechazo", _e(row.get("last_refused_at"))),
        ("Último reenvío", _e(row.get("last_replay_at"))),
    ]
    rows = "".join(f"<dt>{html.escape(k)}</dt><dd>{v}</dd>" for k, v in pairs)
    return (f'<div class="card"><h2>Oferta #{_e(row.get("id"))}</h2>'
            f'<dl class="kv">{rows}</dl>'
            f'<p class="note">{html.escape(TRANSPORT_NOTE)}</p></div>')


def render_offer_detail_page(row: Optional[dict], *, offer_id=None,
                             booking_flow_id=None) -> str:
    """One offer, in full."""
    if not row:
        body = ('<h1>Oferta de reserva</h1>'
                f'<p class="sub mono">#{_e(offer_id)}</p>'
                '<div class="card"><p class="empty">No existe una oferta con ese '
                'identificador. La ausencia de una oferta nunca es un permiso: el camino '
                'de aceptación rechaza por defecto.</p></div>'
                '<p><a href="/control/offers">← Todas las ofertas</a></p>')
        return _page(f"Oferta {offer_id} — inexistente", body)
    body = ('<h1>Oferta de reserva</h1>'
            f'<p class="sub mono">#{_e(row.get("id"))}</p>'
            + _offer_card(row, booking_flow_id=booking_flow_id)
            + _glossary_card()
            + '<p><a href="/control/offers">← Todas las ofertas</a> · '
              '<a href="/control">Panel de control</a></p>')
    return _page(f"Oferta {row.get('id')}", body)


def render_offer_list_page(rows, *, thread_id=None, booking_flow_id=None) -> str:
    """Every offer, newest first — or one thread's, when a thread is named."""
    scope = (f"conversación {html.escape(str(thread_id))}" if thread_id
             else "todas las conversaciones")
    if not rows:
        body = ('<h1>Autoridad de ofertas de reserva</h1>'
                f'<p class="sub">{scope}</p>'
                '<div class="card"><p class="empty">No hay ofertas registradas. Eso no es '
                'un rechazo ni un error: simplemente todavía no se emitió ninguna.</p>'
                '</div><p><a href="/control">← Panel de control</a></p>')
        return _page("Ofertas de reserva", body)

    head = ("<tr><th>Oferta</th><th>Conversación</th><th>Estado</th><th>Resultado</th>"
            "<th>Motivo</th><th>Vehículo</th><th>Zona</th><th>Turno mostrado</th>"
            "<th class='num'>Rech.</th><th class='num'>Replay</th><th>Reserva</th>"
            "<th>Emitida</th></tr>")
    body_rows = []
    for row in rows:
        headline, cls, _why = outcome_of(row)
        reason = row.get("terminal_reason")
        refusal = row.get("last_refusal")
        detail = reason or refusal
        tr_id = row.get("accepted_thread_revision_id")
        body_rows.append(
            "<tr>"
            f'<td><a href="/control/offer/{_e(row.get("id"))}" class="mono">'
            f'#{_e(row.get("id"))}</a></td>'
            f'<td><a href="/whatsapp/thread/{_e(row.get("thread_id"))}">'
            f'{_e(row.get("thread_id"))}</a></td>'
            f"<td>{status_badge(row.get('status'))}</td>"
            f'<td><span class="badge {cls}">{html.escape(headline)}</span></td>'
            f"<td>{_e(REASON_LABELS.get(str(detail or ''), REFUSAL_LABELS.get(str(detail or ''), detail)))}</td>"
            f"<td>{_e(row.get('tipo_vehiculo'))}</td>"
            f"<td>{_e(' / '.join(p for p in (row.get('zone_group'), row.get('zone_detail')) if p))}</td>"
            f"<td>{_e(' '.join(str(p) for p in (row.get('offered_date'), row.get('offered_time')) if p))}</td>"
            f"<td class='num'>{_e(row.get('refused_count'))}</td>"
            f"<td class='num'>{_e(row.get('replay_count'))}</td>"
            f"<td>{'—' if tr_id is None else _e(tr_id)}</td>"
            f"<td>{_e(row.get('issued_at'))}</td>"
            "</tr>")
    body = ('<h1>Autoridad de ofertas de reserva</h1>'
            f'<p class="sub">{scope} · {len(rows)} oferta(s), de la más reciente a la más '
            'antigua</p>'
            f'<div class="card"><table>{head}{"".join(body_rows)}</table></div>'
            + _glossary_card()
            + '<p><a href="/control">← Panel de control</a></p>')
    return _page("Ofertas de reserva", body)


def _glossary_card() -> str:
    """Every label this page can show, and what it means — so the vocabulary lives beside
    the renderer instead of in prose somewhere else."""
    items = [(label, note) for label, _cls, note in STATUS_LABELS.values() if note]
    items += [
        ("RESERVA CONFIRMADA", "la oferta fue aceptada Y existe la revisión reservada"),
        ("RESERVA CONFIRMADA · REENVIADA",
         "lo mismo, y además la oferta se volvió a presentar: el reenvío devuelve la "
         "reserva que ya existe, no crea otra y no cuenta como rechazo"),
        ("ACEPTADA SIN RESERVA",
         "aceptación registrada sin revisión reservada; es el único estado que no debería "
         "existir y hay que revisarlo"),
        ("RECHAZADA", "la oferta terminó sin reserva y hay un motivo registrado"),
        ("Rech. / Replay",
         "dos contadores distintos a propósito: un rechazo es una presentación que no pudo "
         "reservar, un replay es la misma reserva pedida otra vez"),
    ]
    rows = "".join(f"<dt>{html.escape(k)}</dt><dd>{html.escape(v)}</dd>" for k, v in items)
    return (f'<div class="card"><h2>Glosario</h2><dl class="kv">{rows}</dl>'
            '<p class="note">No se muestran tokens crudos, secretos, WAMID completos ni '
            'texto del cliente: la huella del token se recorta y el token en claro nunca '
            'se guarda en la base.</p></div>')
