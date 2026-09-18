"""L4.7W5 Gate 2 — render one hybrid decision.

The question this page answers is the one the failed Wild could not: *why did the system
answer that?* It shows, for a single customer turn, what the customer sent, what each
evidence producer said, which authority owned the outcome, and what canonical state changed.

Two rules shape everything below.

**Absence is rendered as absence.** A turn with no stored trace gets the not-captured page,
not an empty decision. A semantic engine that had not answered yet reads PENDING, not
"no claims". An inspector that quietly turns missing evidence into negative evidence is
worse than no inspector, because it is believed.

**It never re-derives.** Every value on this page is read from the stored payload. Nothing
is recomputed here, so the page cannot disagree with the decision it is describing.

Customer message bodies are not rendered: the trace carries ordered WAMIDs and the hash of
the normalized burst, and the words themselves stay in the WhatsApp thread, which is one
click away and already governed by the existing masking policy.
"""
from __future__ import annotations

import html
import json
from typing import Any, Optional

TRACE_NOT_CAPTURED = "TRACE NOT CAPTURED — predates hybrid-decision-trace/1.0"

# L4.7W5-HYBRID-TRACE-HARDENING — the scope of this page, stated on the page itself.
#
# A hybrid decision is a customer turn in which the semantic engine and the deterministic
# CE both interpreted the same evidence and a reconciler could have owned the outcome.
# That happens in `ConversationEngine.handle()` and nowhere else. A booking confirmed
# inside the Meta Booking Flow is an operational transaction on a different path: no
# interpretation happens, no claim is produced, nothing is reconciled. Rendering one as a
# hybrid decision would manufacture evidence, so this page says which it is looking at.
SCOPE_LABEL = "HYBRID CONVERSATION TRACE"
SCOPE_NOTE = (
    "Decisión conversacional (ConversationEngine.handle): el motor semántico y el motor "
    "determinista interpretaron la misma evidencia del cliente. Una reserva confirmada "
    "dentro del Flow de Meta es una transacción operativa en otro camino y no aparece "
    "aquí como decisión híbrida."
)
FLOW_TRANSACTION_LABEL = "FLOW TRANSACTION — NOT A HYBRID DECISION"
FLOW_TRANSACTION_NOTE = (
    "El envío de este turno salió por BOOKING_FLOW. Lo que el cliente haga dentro del "
    "Flow es una transacción operativa: no la interpretó ni el LLM ni el CE, y no está "
    "representada en esta traza."
)

# Every label this page can show, and what it means. Kept here rather than in prose
# elsewhere so the vocabulary and the renderer cannot drift apart.
LABEL_GLOSSARY = (
    (SCOPE_LABEL, "una decisión conversacional; el único tipo de turno que esta página describe"),
    ("RECONCILED", "una regla de reconciliación fue la autoridad del resultado"),
    ("DETERMINISTIC FLOOR", "una regla determinista produjo el resultado; no hubo reconciliación"),
    ("AGREE", "hubo reconciliación y todas las familias coincidieron"),
    ("CONFLICT", "hubo reconciliación y la evidencia se contradecía"),
    ("NO RULE", "hubo evidencia y ninguna autoridad la reconcilió — no es acuerdo"),
    ("SEMANTIC PENDING", "el intérprete se despachó async y aún no había respondido al decidir"),
    ("SEMANTIC MISSING", "no hubo interpretación semántica para este turno"),
    ("SEMANTIC ERROR", "el intérprete falló; la decisión se tomó sin evidencia semántica"),
    ("CE MISSING", "ninguna regla determinista se evaluó para este turno"),
    ("TRACE NOT CAPTURED", "no existe traza almacenada para ese turno"),
    (FLOW_TRANSACTION_LABEL,
     "acción originada en el Flow de Meta, mostrada como enlace, nunca como decisión híbrida"),
)

_BADGE_CLASS = {
    "CONFLICT": "bad", "SEMANTIC ERROR": "bad", "BLOCKED": "bad",
    "AGREE": "good", "RECONCILED": "good",
    "NO RULE": "warn", "SEMANTIC MISSING": "warn", "SEMANTIC PENDING": "warn",
    "CE MISSING": "warn", "DETERMINISTIC FLOOR": "warn", "HANDOFF": "warn",
    "CLARIFICATION": "warn", "FALLBACK": "warn",
    SCOPE_LABEL: "scope", FLOW_TRANSACTION_LABEL: "flow",
}

_CSS = """
:root{--bg:#f6f7f9;--fg:#1c2530;--muted:#66717f;--line:#dfe3e8;--card:#fff;
--good:#1f7a4d;--goodbg:#e6f4ec;--warn:#8a5a00;--warnbg:#fdf3e0;--bad:#a12626;--badbg:#fbeaea;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:24px 20px 64px}
a{color:#1d4ed8}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
margin:0 0 10px}
.sub{color:var(--muted);margin:0 0 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:16px;margin-bottom:16px}
.badges{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}
.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;
font-weight:600;background:#eceff3;color:#3c4655}
.badge.good{background:var(--goodbg);color:var(--good)}
.badge.warn{background:var(--warnbg);color:var(--warn)}
.badge.bad{background:var(--badbg);color:var(--bad)}
.badge.scope{background:#e7edf6;color:#2b4a7a;letter-spacing:.04em}
.badge.flow{background:#efe9f7;color:#553a7a;letter-spacing:.04em}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);
vertical-align:top;font-size:13px}
th{color:var(--muted);font-weight:600;white-space:nowrap}
tr:last-child td{border-bottom:0}
.kv{display:grid;grid-template-columns:200px 1fr;gap:6px 16px;font-size:13px}
.kv dt{color:var(--muted)}
.kv dd{margin:0;font-variant-numeric:tabular-nums;word-break:break-word}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.empty{color:var(--muted);font-style:italic}
.changed{background:#fff8e1}
.tableWrap{overflow-x:auto}
details{margin-top:12px}
summary{cursor:pointer;color:var(--muted);font-size:13px}
pre{background:#0f1720;color:#dfe7ef;padding:12px;border-radius:6px;overflow-x:auto;
font-size:12px;line-height:1.45}
.note{border-left:3px solid var(--line);padding-left:12px;color:var(--muted);font-size:13px}
"""


def _e(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if value is True:
        return "sí"
    if value is False:
        return "no"
    return html.escape(str(value))


def _page(title: str, body: str) -> str:
    return (f"<!DOCTYPE html>\n<html lang=\"es\">\n<head>\n"
            f"<meta charset=\"utf-8\">\n"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
            f"<title>{html.escape(title)}</title>\n<style>{_CSS}</style>\n</head>\n"
            f"<body><div class=\"wrap\">{body}</div></body>\n</html>")


def _badges(badges) -> str:
    if not badges:
        return ""
    out = []
    for badge in badges:
        cls = _BADGE_CLASS.get(str(badge).upper(), "")
        out.append(f'<span class="badge {cls}">{_e(badge)}</span>')
    return f'<div class="badges">{"".join(out)}</div>'


def _kv(pairs) -> str:
    rows = "".join(f"<dt>{html.escape(k)}</dt><dd>{v}</dd>" for k, v in pairs)
    return f'<dl class="kv">{rows}</dl>'


def _messages_card(trace: dict) -> str:
    ids = trace.get("ordered_message_ids") or []
    stamps = trace.get("message_timestamps") or []
    if not ids:
        rows = '<tr><td colspan="3" class="empty">Sin mensajes registrados</td></tr>'
    else:
        rows = "".join(
            f"<tr><td>{i + 1}</td><td class=\"mono\">{_e(wamid)}</td>"
            f"<td>{_e(stamps[i] if i < len(stamps) else None)}</td></tr>"
            for i, wamid in enumerate(ids))
    return (
        '<div class="card"><h2>Turno del cliente</h2>'
        '<div class="tableWrap"><table><thead><tr><th>#</th><th>WAMID</th>'
        f'<th>Recibido</th></tr></thead><tbody>{rows}</tbody></table></div>'
        + _kv([
            ("Mensajes en la ráfaga", _e(trace.get("message_count"))),
            ("Hash de entrada", f'<span class="mono">{_e(trace.get("input_hash"))}</span>'),
        ])
        + '<p class="note">El texto no se reproduce aquí. El hash identifica la ráfaga '
          'normalizada; las palabras del cliente están en la conversación.</p></div>')


def _semantic_card(sem: dict) -> str:
    status = str(sem.get("status") or "ABSENT")
    claims = sem.get("produced_claims") or []
    claims_html = (", ".join(_e(c) for c in claims) if claims
                   else '<span class="empty">ninguna</span>')
    explain = {
        "PENDING": "El intérprete se despachó de forma asíncrona y todavía no había "
                   "respondido cuando se tomó la decisión. No es un error.",
        "ABSENT": "No hubo interpretación semántica para este turno.",
        "OK": "",
        "ERROR": "El intérprete falló. La decisión se tomó sin evidencia semántica.",
    }.get(status, "")
    body = _kv([
        ("Estado", _e(status)),
        ("Despacho", _e(sem.get("dispatch"))),
        ("Modelo", _e(sem.get("model"))),
        ("Prompt", _e(sem.get("prompt_version"))),
        ("Esquema", _e(sem.get("schema_version"))),
        ("Latencia", f'{_e(sem.get("latency_ms"))} ms' if sem.get("latency_ms") else "—"),
        ("Tokens", _e(sem.get("total_tokens"))),
        ("Categoría de error", _e(sem.get("error_category"))),
        ("Familias propuestas", claims_html),
    ])
    note = f'<p class="note">{html.escape(explain)}</p>' if explain else ""
    detail = ""
    if sem.get("evidence"):
        dumped = html.escape(json.dumps(sem["evidence"], ensure_ascii=False, indent=2,
                                        sort_keys=True))
        detail = (f"<details><summary>Evidencia semántica completa</summary>"
                  f"<pre>{dumped}</pre></details>")
    return f'<div class="card"><h2>Motor semántico</h2>{body}{note}{detail}</div>'


def _ce_card(rules) -> str:
    if not rules:
        rows = '<tr><td colspan="4" class="empty">Ninguna regla evaluada</td></tr>'
    else:
        rows = "".join(
            f'<tr><td class="mono">{_e(r.get("rule_id"))}</td>'
            f'<td>{_e(r.get("rule_version"))}</td>'
            f'<td>{_e(r.get("value"))}</td>'
            f'<td>{_e(r.get("note"))}</td></tr>' for r in rules)
    return ('<div class="card"><h2>Motor determinista</h2><div class="tableWrap"><table>'
            '<thead><tr><th>Regla</th><th>Versión</th><th>Valor</th><th>Nota</th></tr>'
            f'</thead><tbody>{rows}</tbody></table></div></div>')


def _reconciliation_card(items) -> str:
    if not items:
        body = ('<p class="empty">Ninguna reconciliación se registró para este turno.</p>'
                '<p class="note">Esto no significa que los motores hayan coincidido: '
                'significa que ninguna autoridad reconcilió esta decisión.</p>')
    else:
        rows = "".join(
            f'<tr><td>{_e(r.get("claim_family"))}</td>'
            f'<td>{_e(r.get("semantic_input"))}</td>'
            f'<td>{_e(r.get("ce_input"))}</td>'
            f'<td>{_e(r.get("classification"))}</td>'
            f'<td>{_e(r.get("outcome"))}</td>'
            f'<td class="mono">{_e(r.get("rule_id"))}@{_e(r.get("rule_version"))}</td>'
            f'<td>{_e(r.get("reason_code"))}</td></tr>' for r in items)
        body = ('<div class="tableWrap"><table><thead><tr><th>Familia</th>'
                '<th>Semántico</th><th>Determinista</th><th>Clasificación</th>'
                '<th>Resultado</th><th>Regla</th><th>Motivo</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></div>')
    return f'<div class="card"><h2>Reconciliación</h2>{body}</div>'


_SNAPSHOT_LABELS = (
    ("stage", "Etapa"),
    ("needs_human", "Requiere humano"),
    ("lead_estado", "Estado del lead"),
    ("lead_necesita_humano", "Lead: requiere humano"),
    ("candidate_id", "Candidato"),
    ("revision_id", "Revisión"),
    ("zone_group", "Zona"),
    ("zone_detail", "Localidad"),
    ("offer_outstanding", "Oferta pendiente"),
    ("active_requested_date", "Fecha pedida"),
    ("offered_slots_count", "Turnos ofrecidos"),
    ("booking_token_present", "Token de reserva"),
    ("booking_token_fingerprint", "Huella del token"),
)


def _state_card(before: dict, after: dict) -> str:
    rows = []
    for key, label in _SNAPSHOT_LABELS:
        old, new = before.get(key), after.get(key)
        cls = ' class="changed"' if old != new else ""
        rows.append(f"<tr{cls}><td>{html.escape(label)}</td>"
                    f"<td>{_e(old)}</td><td>{_e(new)}</td></tr>")
    return ('<div class="card"><h2>Estado canónico</h2><div class="tableWrap"><table>'
            '<thead><tr><th>Campo</th><th>Antes</th><th>Después</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="note">La huella del token prueba que el token cambió o se retiró '
            'sin revelarlo nunca.</p></div>')


def _glossary_card() -> str:
    rows = "".join(f'<tr><td><span class="badge {_BADGE_CLASS.get(label, "")}">{_e(label)}'
                   f'</span></td><td>{_e(meaning)}</td></tr>'
                   for label, meaning in LABEL_GLOSSARY)
    return ('<details><summary>Qué significa cada etiqueta</summary>'
            f'<div class="card"><div class="tableWrap"><table><tbody>{rows}'
            '</tbody></table></div></div></details>')


def _outcome_card(trace: dict) -> str:
    path = trace.get("outbound_path_id")
    flow_row = ""
    if path == "BOOKING_FLOW":
        flow_row = (f'<p class="note"><span class="badge flow">'
                    f'{_e(FLOW_TRANSACTION_LABEL)}</span> '
                    f'{html.escape(FLOW_TRANSACTION_NOTE)}</p>')
    return ('<div class="card"><h2>Resultado</h2>' + _kv([
        ("Cómo se produjo", _e(trace.get("result_kind"))),
        ("Acción", _e(trace.get("response_plan_kind"))),
        ("Fuente de la respuesta", _e(trace.get("response_plan_source"))),
        ("Motivo", _e(trace.get("reason_code"))),
        ("Permitido", _e(trace.get("allowed"))),
        ("Transición", _e(trace.get("transition"))),
        ("Mensaje saliente", _e(trace.get("outbound_message_id"))),
        ("Camino de envío", _e(trace.get("outbound_path_id"))),
        ("Estado del envío", _e(trace.get("outbound_status"))),
        ("WAMID (final)", f'<span class="mono">{_e(trace.get("outbound_wamid_tail"))}</span>'),
    ]) + flow_row + "</div>")


def render_turn_not_captured(turn_id: str) -> str:
    """Scope H — a turn the inspector cannot speak for."""
    body = (f"<h1>Turno {html.escape(str(turn_id))}</h1>"
            f'<p class="sub">{html.escape(TRACE_NOT_CAPTURED)}</p>'
            '<div class="card"><p>No existe una traza almacenada para este turno.</p>'
            '<p class="note">Esto describe el registro, no la conversación: el turno pudo '
            'haber ocurrido normalmente antes de que la captura existiera, o con la captura '
            'desactivada. Ausencia de traza no es evidencia de que algo fallara.</p>'
            f'<p class="note">{html.escape(SCOPE_NOTE)}</p></div>'
            '<p><a href="/control">← Volver al panel de control</a></p>')
    return _page(f"Turno {turn_id} — sin traza", body)


def render_turn_trace_page(record: Optional[dict], turn_id: str = "") -> str:
    """Render one stored decision. `record` is the API payload, or None."""
    if not record or not record.get("captured") or not record.get("trace"):
        return render_turn_not_captured(record.get("turn_id") if record else turn_id)

    trace = record["trace"] or {}
    thread_id = trace.get("thread_id")
    thread_link = (f'<a href="/whatsapp/thread/{html.escape(str(thread_id))}">'
                   f"conversación {html.escape(str(thread_id))}</a>"
                   if thread_id else "—")
    head = _kv([
        ("Conversación", thread_link),
        ("Lead", _e(trace.get("lead_id"))),
        ("Versión del contrato", _e(trace.get("trace_version"))),
        ("Despliegue", f'<span class="mono">{_e(trace.get("deployment_sha"))}</span>'),
        ("Inicio", _e(trace.get("started_at"))),
        ("Fin", _e(trace.get("completed_at"))),
        ("Duración", f'{_e(trace.get("duration_ms"))} ms'
                     if trace.get("duration_ms") is not None else "—"),
    ])
    dumped = html.escape(json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True))
    body = (
        f"<h1>Decisión del turno</h1>"
        f'<p class="sub mono">{_e(trace.get("turn_id"))}</p>'
        + _badges((SCOPE_LABEL,) + tuple(trace.get("badges") or ()))
        + f'<p class="note">{html.escape(SCOPE_NOTE)}</p>' 
        + f'<div class="card"><h2>Identidad</h2>{head}</div>'
        + _messages_card(trace)
        + _semantic_card(trace.get("semantic") or {})
        + _ce_card(trace.get("ce_evidence") or [])
        + _reconciliation_card(trace.get("reconciliation") or [])
        + _state_card(trace.get("canonical_before") or {}, trace.get("canonical_after") or {})
        + _outcome_card(trace)
        + f"<details><summary>Traza completa (JSON)</summary><pre>{dumped}</pre></details>"
        + _glossary_card()
        + '<p><a href="/control">← Volver al panel de control</a></p>')
    return _page(f"Turno {trace.get('turn_id') or turn_id}", body)
