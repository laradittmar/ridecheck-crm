"""L4.7W5 Gate 2 — render one hybrid decision.

The question this page answers is the one the failed Wild could not: *why did the system
answer that?* It shows, for a single customer turn, what the customer sent, what each
evidence producer said, which authority owned the outcome, and what canonical state changed.

Two rules shape everything below.

**Absence is rendered as absence.** A turn with no stored trace gets the not-captured page,
not an empty decision. A semantic engine that had not answered yet reads PENDING, not
"no claims". An inspector that quietly turns missing evidence into negative evidence is
worse than no inspector, because it is believed.

**It never re-derives.** Every value on this page is read from the stored payload, and the
one reading that IS derived — a reconciliation row's effective classification — is derived
by `services.hybrid_trace`, the same function the JSON API calls. There is one
implementation, so the page and the API cannot tell different stories about a turn.

If a caller hands this page a record without that derived table, the page derives it rather
than rendering an empty reconciliation card: printing "ninguna reconciliación se registró"
for a turn that recorded two of them is precisely the class of falsehood this page exists
to stop.

Customer message bodies are not rendered: the trace carries ordered WAMIDs and the hash of
the normalized burst, and the words themselves stay in the WhatsApp thread, which is one
click away and already governed by the existing masking policy.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from typing import Any, Optional

TRACE_NOT_CAPTURED = "TRACE NOT CAPTURED — predates hybrid-decision-trace/1.0"

# ── WAMID masking (owner decision, 2026-09-22) ───────────────────────────────
#
# A WhatsApp message id base64-encodes the sender's MSISDN. Printing one in full on an
# operational page hands out a customer's phone number to anyone who can read the page and
# knows how to decode it — which is not a threat model, it is a base64 decoder.
#
# The rendered form is a SHA-256 prefix: stable, so the same message always reads the same
# and an operator can still tell two messages apart and follow their order; irreversible,
# so nothing about the number can be recovered from it. The stored payload is untouched,
# and the authenticated JSON API still returns the real id, because that id is the join key
# into `whatsapp_messages` and the outbound ledger. Masking is a rendering policy; breaking
# a durable forensic join would be a different and worse defect.
_WAMID_RE = re.compile(r"wamid\.[A-Za-z0-9+/=_-]{4,}")
WAMID_MASK_NOTE = (
    "Los WAMID se muestran como huella irreversible: identifican y ordenan el mensaje sin "
    "revelar el teléfono del cliente, que va codificado dentro del WAMID real.")


def mask_wamid(value: Any) -> Any:
    """`wamid.…` → a stable, irreversible fingerprint. Anything else is returned unchanged."""
    if not isinstance(value, str) or not value.startswith("wamid."):
        return value
    return "wamid⋯" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def mask_wamids_in_text(text: str) -> str:
    """Mask every WAMID inside an already-serialized blob, such as the raw JSON panel."""
    return _WAMID_RE.sub(lambda m: mask_wamid(m.group(0)), text)

# L4.7W5-HYBRID-TRACE-HARDENING — the scope of this page, stated on the page itself.
#
# A hybrid decision is a customer turn in which the semantic engine and the deterministic
# CE both interpreted the same evidence and a reconciler could have owned the outcome.
# That happens in `ConversationEngine.handle()` and nowhere else. A booking confirmed
# inside the Meta Booking Flow is an operational transaction on a different path: no
# interpretation happens, no claim is produced, nothing is reconciled. Rendering one as a
# hybrid decision would manufacture evidence, so this page says which it is looking at.
SCOPE_LABEL = "HYBRID CONVERSATION TRACE"
# The old wording said both engines "interpretaron la misma evidencia". Both engines may
# receive the same customer burst; that is a fact about the input, not about any particular
# decision. Whether a producer contributed to a given reconciliation is recorded per row and
# is frequently NO — which is the single most important thing this page has to convey.
SCOPE_NOTE = (
    "Decisión conversacional (ConversationEngine.handle). Ambos motores pueden haber "
    "recibido la misma ráfaga del cliente; eso NO significa que ambos hayan aportado "
    "evidencia a cada reconciliación. Qué fuente participó en cada decisión se indica "
    "fila por fila más abajo. Una reserva confirmada dentro del Flow de Meta es una "
    "transacción operativa en otro camino y no aparece aquí como decisión híbrida."
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
    ("AGREE",
     "dos o más fuentes DISTINTAS aportaron evidencia a la misma decisión y esa "
     "evidencia era compatible. Una sola fuente nunca produce AGREE"),
    ("CONFLICT",
     "dos o más fuentes distintas aportaron evidencia incompatible sobre el mismo tipo "
     "de dato. Es lo único que se llama contradicción"),
    ("SINGLE PRODUCER",
     "exactamente una fuente aportó evidencia utilizable; no hubo comparación, aunque el "
     "reconciliador haya aceptado el valor"),
    ("PARALLEL EVIDENCE",
     "participaron dos o más fuentes pero ninguna habló del mismo campo canónico: "
     "dijeron cosas distintas, lo cual no es acuerdo ni desacuerdo"),
    ("COMPARISON UNPROVEN",
     "sí hay un campo canónico compartido y la evidencia capturada no alcanza para probar "
     "que los valores significan lo mismo. No probado no es compatible"),
    ("AMBIGUOUS EVIDENCE",
     "la única fuente participante se contradice a sí misma; eso es ambigüedad de esa "
     "fuente, nunca un conflicto entre motores"),
    ("NO EVIDENCE",
     "ninguna fuente aportó evidencia utilizable a esa llamada; no culpa a ningún motor"),
    ("NOT ROUTED",
     "el intérprete sí produjo evidencia en este turno y no llegó a esa reconciliación"),
    ("ERROR", "un productor o el reconciliador falló y la comparación no pudo hacerse"),
    ("Autoridad y acción",
     "qué decidió el reconciliador o autorizador, si escribió estado canónico y qué "
     "acción quedó permitida. Que la evidencia sea válida NO significa que haya "
     "autoridad para mutar: son columnas distintas a propósito"),
    ("LEGACY PROVENANCE UNAVAILABLE",
     "traza 1.0: la fila registra que algo participó, pero no quién. No se puede probar "
     "ni acuerdo ni desacuerdo; la etiqueta original se conserva aparte"),
    ("SINGLE SOURCE DECISION",
     "ninguna comparación multi-fuente ocurrió; una sola fuente condujo las decisiones"),
    ("NO COMPARISON", "hay filas y ninguna recibió evidencia utilizable"),
    ("TRACE INCOMPLETE",
     "el registro no alcanza para afirmar algo más fuerte con verdad"),
    ("PARTIAL RECONCILIATION",
     "alguna familia se comparó y otra no pudo compararse; NO es contradicción"),
    ("SEMANTIC NOT ROUTED",
     "el intérprete sí produjo evidencia en este turno, pero no llegó a ese reconciliador"),
    ("NO RESPONSE PRODUCED",
     "no se produjo respuesta utilizable y no se intentó ningún envío; no es «replied»"),
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
    "PARTIAL RECONCILIATION": "warn", "SEMANTIC NOT ROUTED": "warn",
    "NO RESPONSE PRODUCED": "warn",
    "NO RULE": "warn", "SEMANTIC MISSING": "warn", "SEMANTIC PENDING": "warn",
    "SINGLE PRODUCER": "warn", "NO EVIDENCE": "warn", "NOT ROUTED": "warn",
    "PARALLEL EVIDENCE": "warn", "COMPARISON UNPROVEN": "warn",
    "AMBIGUOUS EVIDENCE": "warn",
    "ERROR": "bad", "LEGACY PROVENANCE UNAVAILABLE": "warn",
    "SINGLE SOURCE DECISION": "warn", "NO COMPARISON": "warn",
    "TRACE INCOMPLETE": "warn",
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
.sub2{color:var(--muted);font-size:11px;margin-top:3px;word-break:break-word}
.srcblock{margin-bottom:8px}
.srcblock:last-child{margin-bottom:0}
ul.vals{margin:3px 0 0;padding-left:16px}
ul.vals li{font-size:12px}
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
    """Assemble the page, then sweep every WAMID out of the finished HTML.

    The sweep is the backstop, and it is why the guarantee is structural rather than a
    promise. Masking each panel by hand nearly worked: the reconciliation table and the raw
    trace panel were masked, and the semantic-evidence panel was not, because a WAMID also
    travels inside `provenance.source_message_ids` on every claim the interpreter produced.
    A rule that depends on remembering every panel is a rule that fails at the next panel.

    Masking is idempotent — the masked form uses `⋯`, which the pattern cannot match — so
    running it over already-masked output changes nothing.
    """
    page = (f"<!DOCTYPE html>\n<html lang=\"es\">\n<head>\n"
            f"<meta charset=\"utf-8\">\n"
            f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
            f"<title>{html.escape(title)}</title>\n<style>{_CSS}</style>\n</head>\n"
            f"<body><div class=\"wrap\">{body}</div></body>\n</html>")
    return mask_wamids_in_text(page)


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
            f"<tr><td>{i + 1}</td><td class=\"mono\">{_e(mask_wamid(wamid))}</td>"
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
          'normalizada; las palabras del cliente están en la conversación.</p>'
        + f'<p class="note">{html.escape(WAMID_MASK_NOTE)}</p></div>')


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


NO_RESPONSE_PRODUCED_SENTENCE = (
    "No se produjo ninguna respuesta utilizable en este turno: no se intentó ningún envío "
    "y el cliente no recibió nada. Esto NO es una respuesta enviada.")
NO_RESPONSE_HISTORICAL_NOTE = (
    "La traza almacenada registra la acción como «replied» con motivo «no_reply_text». "
    "Ese registro se conserva sin modificar; la lectura correcta del turno es que no hubo "
    "respuesta.")

#: What each source name means on a reconciliation row. Only sources the executable audit
#: proved can appear; `SERVICE_COMPUTED` exists as an evidence class but no code builds a
#: claim with it, so there is no "system-derived" label here to be misapplied.
SOURCE_LABELS = {
    "SEMANTIC": ("Semántico", "el intérprete (producer `semantic:*`)"),
    "DETERMINISTIC": ("Determinista", "un parser o el catálogo del CE (producer `ce:*`)"),
    "CANONICAL_STATE": ("Estado canónico",
                        "evidencia derivada del estado ya establecido (producer `canonical:*`)"),
    "UNATTRIBUTED": ("Sin atribuir",
                     "la afirmación no declara productor; nunca cuenta como participante"),
}

NOT_ROUTED_SENTENCE = ("Se produjo evidencia semántica en este turno, pero no llegó a "
                       "este reconciliador.")
NO_SEMANTIC_SENTENCE = "No se produjo evidencia semántica utilizable en este turno."
NO_PARTICIPATION_SENTENCE = (
    "Ninguna fuente aportó evidencia utilizable a esta reconciliación. Eso no es un fallo "
    "del motor semántico: no aportó nadie.")
SINGLE_PRODUCER_SENTENCE = (
    "Una sola fuente aportó evidencia. Que el reconciliador haya aceptado el valor no "
    "convierte la decisión en un acuerdo entre motores: no hubo con qué compararla.")
PARALLEL_EVIDENCE_SENTENCE = (
    "Participaron dos o más fuentes y ninguna habló del mismo campo canónico. Que no haya "
    "contradicción NO significa que hayan coincidido: hablaron de cosas distintas.")
COMPARISON_UNPROVEN_SENTENCE = (
    "Hay un campo canónico compartido y la evidencia capturada no permite probar que los "
    "valores signifiquen lo mismo. Se informa como no probado, nunca como acuerdo.")
AMBIGUOUS_EVIDENCE_SENTENCE = (
    "La única fuente participante se contradice a sí misma. Se registra como ambigüedad de "
    "esa fuente y se conserva la polaridad; no es un conflicto entre motores.")
LEGACY_ROW_SENTENCE = (
    "Traza «hybrid-decision-trace/1.0»: esas filas registran que algo participó, pero no "
    "quién. No se puede probar acuerdo ni contradicción a nivel de fila. La etiqueta "
    "original queda a la vista, sin modificar, en la columna «Registrado».")
VALUES_WITHHELD_SENTENCE = (
    "Los valores que no figuran en la lista de exhibición del inspector se retienen y se "
    "muestran como referencia tipada (#huella). Dos referencias distintas son dos valores "
    "distintos; la misma referencia es el mismo valor.")


def _semantic_ran(semantic: dict) -> bool:
    return (semantic or {}).get("status") == "OK" and bool((semantic or {}).get("produced_claims"))


def _source_chip(source: str) -> str:
    label, _ = SOURCE_LABELS.get(str(source), (str(source), ""))
    return f'<span class="badge">{_e(label)}</span>'


def _sources_cell(row: dict) -> str:
    """Who actually participated — and who produced evidence that went elsewhere."""
    if row.get("legacy"):
        return ('<span class="badge warn">1.0 — sin procedencia</span>'
                f'<div class="sub2">semantic_input={_e(row.get("legacy_semantic_input"))} · '
                f'ce_input={_e(row.get("legacy_ce_input"))}</div>')
    present = row.get("participating_sources") or []
    unrouted = row.get("not_routed_sources") or []
    if present:
        body = "".join(_source_chip(s) for s in present)
    else:
        body = '<span class="empty">ninguna</span>'
    if unrouted:
        body += ('<div class="sub2">no ruteada: '
                 + ", ".join(_e(SOURCE_LABELS.get(str(s), (str(s), ""))[0]) for s in unrouted)
                 + "</div>")
    return body


def _evidence_cell(row: dict) -> str:
    """What each source supplied, under the Inspector's display allowlist."""
    if row.get("legacy"):
        return '<span class="empty">no registrado en 1.0</span>'
    contributions = row.get("source_evidence") or []
    if not contributions:
        return '<span class="empty">ninguna</span>'
    blocks = []
    for contribution in contributions:
        label = SOURCE_LABELS.get(str(contribution.get("source")),
                                  (str(contribution.get("source")), ""))[0]
        items = []
        types = contribution.get("claim_types") or []
        keys = contribution.get("value_keys") or []
        values = contribution.get("values") or []
        polarities = contribution.get("polarities") or []
        canonicals = contribution.get("canonical_values") or []
        for index, claim_type in enumerate(types):
            shown = values[index] if index < len(values) else None
            key = keys[index] if index < len(keys) else None
            polarity = polarities[index] if index < len(polarities) else None
            canonical = canonicals[index] if index < len(canonicals) else None
            rendered = (_e(shown) if shown is not None
                        else f'<span class="empty">«retenido»</span> '
                             f'<span class="mono">#{_e(key)}</span>')
            negated = ' <span class="badge bad">NEGADO</span>' if polarity == "NEGATED" else ""
            # The canonical identity is the resolver's own name for the thing, which is what
            # makes two differently-phrased values comparable at all. Shown so an operator
            # can see WHY they were treated as one, rather than being asked to trust it.
            canonical_html = (f' <span class="sub2">→ canónico: {_e(canonical)}</span>'
                              if canonical and canonical != shown else "")
            items.append(f'<li><span class="mono">{_e(claim_type)}</span> = '
                         f'{rendered}{negated}{canonical_html}</li>')
        producer = contribution.get("producer")
        confidences = [c for c in (contribution.get("confidences") or []) if c is not None]
        confidence_html = ""
        if confidences:
            confidence_html = ('<div class="sub2">confianza declarada: '
                               + ", ".join(_e(c) for c in confidences)
                               + ' (advisoria; ninguna regla la lee)</div>')
        classes = contribution.get("evidence_classes") or []
        classes_html = (f'<div class="sub2 mono">{_e(", ".join(str(c) for c in classes))}</div>'
                        if classes else "")
        blocks.append(f'<div class="srcblock"><strong>{_e(label)}</strong>'
                      f'<div class="sub2 mono">{_e(producer)}</div>'
                      f'{classes_html}'
                      f'<ul class="vals">{"".join(items)}</ul>{confidence_html}</div>')
    return "".join(blocks)


_VERDICT_LABEL = {
    "COMPATIBLE": ("compatible", "good"),
    "INCOMPATIBLE": ("incompatible", "bad"),
    "UNPROVEN": ("no probado", "warn"),
}


def _propositions_cell(row: dict) -> str:
    """What was actually compared, field by field, and on what basis.

    An empty cell beside two participating sources is the point: it says the producers
    never spoke about the same thing, which is why the row cannot read AGREE.
    """
    if row.get("legacy"):
        return '<span class="empty">no registrado en 1.0</span>'
    propositions = row.get("compared_propositions") or []
    if not propositions:
        return ('<span class="empty">ningún campo canónico compartido</span>')
    blocks = []
    for proposition in propositions:
        label, cls = _VERDICT_LABEL.get(str(proposition.get("verdict")),
                                        (str(proposition.get("verdict")), ""))
        items = []
        for entry in proposition.get("canonical_by_source") or []:
            source, shown, key, polarity = (list(entry) + [None, None, None, None])[:4]
            source_label = SOURCE_LABELS.get(str(source), (str(source), ""))[0]
            rendered = (_e(shown) if shown is not None
                        else f'<span class="empty">«retenido»</span> '
                             f'<span class="mono">#{_e(key)}</span>')
            negated = ' <span class="badge bad">NEGADO</span>' if polarity == "NEGATED" else ""
            items.append(f'<li>{_e(source_label)}: {rendered}{negated}</li>')
        blocks.append(
            f'<div class="srcblock"><span class="mono">{_e(proposition.get("claim_type"))}'
            f'</span> <span class="badge {cls}">{_e(label)}</span>'
            f'<ul class="vals">{"".join(items)}</ul>'
            f'<div class="sub2">{_e(proposition.get("basis"))}</div></div>')
    return "".join(blocks)


_EFFECT_LABEL = {
    "NONE": ("no escribió estado", ""),
    "WROTE": ("escribió estado canónico", "warn"),
    "BLOCKED": ("escritura bloqueada", "bad"),
}


def _authority_cell(row: dict) -> str:
    """What the real authority decided, what it changed, and what it permitted.

    Five things kept apart on purpose (G3-1). Evidence classification lives in its own
    column; this one answers the questions a reader actually has next: did the authority
    allow it, did anything get written, and what was the customer then told. A row that
    reads ACCEPT and wrote nothing is a normal, correct outcome for a proposal — and
    before this column existed there was no way to see that.
    """
    result = row.get("authority_result")
    if result is None and row.get("outcome") is None:
        return '<span class="empty">—</span>'
    parts = [f'<div><strong>{_e(result or row.get("outcome"))}</strong></div>']
    verdict = row.get("authority_verdict")
    if verdict:
        label, cls = _VERDICT_LABEL.get(str(verdict), (str(verdict), ""))
        parts.append(f'<div class="sub2">la autoridad comparó: '
                     f'<span class="badge {cls}">{_e(label)}</span></div>')
    effect = row.get("canonical_effect")
    if effect:
        label, cls = _EFFECT_LABEL.get(str(effect), (str(effect), ""))
        badge = f'<span class="badge {cls}">{_e(label)}</span>' if cls else _e(label)
        parts.append(f'<div class="sub2">estado canónico: {badge}</div>')
    action = row.get("permitted_action")
    parts.append('<div class="sub2">acción permitida: '
                 + (f'<span class="mono">{_e(action)}</span>' if action
                    else '<span class="empty">ninguna</span>') + '</div>')
    outcome = row.get("business_outcome")
    if outcome:
        parts.append(f'<div class="sub2">resultado: {_e(outcome)}</div>')
    return "".join(parts)


def _counts_line(counts: dict) -> str:
    """Row counts are row counts; family counts deduplicate. Never one printed as the other."""
    parts = [
        f'familias distintas: <strong>{_e(counts.get("distinct_families"))}</strong>',
        f'filas de comparación: <strong>{_e(counts.get("comparison_rows"))}</strong>',
        f'comparaciones lógicas distintas: {_e(counts.get("distinct_comparisons"))}',
        f'comparadas: {_e(counts.get("compared"))}',
        f'una sola fuente: {_e(counts.get("single_producer"))}',
        f'evidencia paralela: {_e(counts.get("parallel_evidence"))}',
        f'no probadas: {_e(counts.get("comparison_unproven"))}',
        f'sin evidencia: {_e(counts.get("no_evidence"))}',
        f'no ruteadas: {_e(counts.get("not_routed"))}',
    ]
    if counts.get("ambiguous_evidence"):
        parts.append(f'ambiguas: {_e(counts.get("ambiguous_evidence"))}')
    if counts.get("errored"):
        parts.append(f'con error: {_e(counts.get("errored"))}')
    if counts.get("legacy_unknown"):
        parts.append(f'procedencia 1.0 desconocida: {_e(counts.get("legacy_unknown"))}')
    return " · ".join(parts)


def _rows_and_counts(record: dict, trace: dict) -> tuple:
    """The reconciliation table, from the record when present and derived when not."""
    rows = (record or {}).get("reconciliation_rows")
    counts = (record or {}).get("reconciliation_counts")
    if rows is None or counts is None:
        from ..services.hybrid_trace import (counts_from_payload,
                                             row_summaries_from_payload)
        rows = list(row_summaries_from_payload(trace))
        counts = counts_from_payload(trace)
    return list(rows), dict(counts or {})


def _reconciliation_card(rows, counts: dict, *, semantic: dict) -> str:
    """One row per reconciliation CALL, never one per family.

    Every column answers a different question, and they are not collapsed into one label:
    which decision, which family, which sources participated, what they supplied, what the
    comparison proves, what the reconciler decided, under which rule, and why.
    """
    if not rows:
        body = ('<p class="empty">Ninguna reconciliación se registró para este turno.</p>'
                '<p class="note">Esto no significa que los motores hayan coincidido: '
                'significa que ninguna autoridad reconcilió esta decisión.</p>')
        return f'<div class="card"><h2>Reconciliación</h2>{body}</div>'

    cells = []
    for row in rows:
        effective = str(row.get("effective_classification") or "")
        badge_cls = _BADGE_CLASS.get(effective.replace("_", " "), "")
        captured = row.get("captured_classification")
        captured_html = ""
        if row.get("reclassified"):
            captured_html = (f'<div class="sub2">registrado: '
                             f'<span class="mono">{_e(captured)}</span></div>')
        purpose = (row.get("decision_purpose")
                   or row.get("decision_site_id")
                   or "—")
        site = row.get("decision_site_id")
        site_html = (f'<div class="sub2 mono">{_e(site)}</div>' if site else
                     '<div class="sub2 empty">sitio no registrado (1.0)</div>')
        identity = row.get("logical_comparison_id")
        if identity:
            occurrence = row.get("occurrence_index") or 0
            repeat = f' · ejecución {occurrence + 1}' if occurrence else ""
            site_html += (f'<div class="sub2 mono">#{_e(identity)}{_e(repeat)}</div>')
        contradiction = row.get("self_contradiction_sources") or []
        contradiction_html = ""
        if contradiction:
            names = ", ".join(_e(SOURCE_LABELS.get(str(s), (str(s), ""))[0])
                              for s in contradiction)
            contradiction_html = (f'<div class="sub2">se contradice a sí misma: {names}</div>')
        cells.append(
            f'<tr><td>{_e(purpose)}{site_html}</td>'
            f'<td class="mono">{_e(row.get("claim_family"))}</td>'
            f'<td>{_sources_cell(row)}</td>'
            f'<td>{_evidence_cell(row)}</td>'
            f'<td>{_propositions_cell(row)}</td>'
            f'<td><span class="badge {badge_cls}">'
            f'{_e(effective.replace("_", " "))}</span>{captured_html}'
            f'<div class="sub2">polaridad: {_e(row.get("information_state"))}</div>'
            f'{contradiction_html}</td>'
            f'<td>{_authority_cell(row)}</td>'
            f'<td class="mono">{_e(row.get("rule_id"))}@{_e(row.get("rule_version"))}</td>'
            f'<td>{_e(row.get("reason_code"))}</td></tr>')

    body = ('<div class="tableWrap"><table><thead><tr>'
            '<th>Decisión</th><th>Familia</th><th>Fuentes que participaron</th>'
            '<th>Evidencia aportada</th><th>Qué se comparó</th><th>Clasificación</th>'
            '<th>Autoridad y acción</th><th>Regla</th><th>Motivo</th></tr></thead>'
            f'<tbody>{"".join(cells)}</tbody></table></div>')
    body += f'<p class="note">{_counts_line(counts or {})}</p>'

    kinds = [str(r.get("effective_classification") or "") for r in rows]
    notes = []
    if any(r.get("legacy") for r in rows):
        notes.append(html.escape(LEGACY_ROW_SENTENCE))
    if "SINGLE_PRODUCER" in kinds:
        notes.append(html.escape(SINGLE_PRODUCER_SENTENCE))
    if "PARALLEL_EVIDENCE" in kinds:
        notes.append(html.escape(PARALLEL_EVIDENCE_SENTENCE))
    if "COMPARISON_UNPROVEN" in kinds:
        notes.append(html.escape(COMPARISON_UNPROVEN_SENTENCE))
    if "AMBIGUOUS_EVIDENCE" in kinds:
        notes.append(html.escape(AMBIGUOUS_EVIDENCE_SENTENCE))
    if "NOT_ROUTED" in kinds or any(r.get("not_routed_sources") for r in rows):
        notes.append(html.escape(NOT_ROUTED_SENTENCE))
    if "NO_EVIDENCE" in kinds:
        notes.append(html.escape(NO_PARTICIPATION_SENTENCE if _semantic_ran(semantic)
                                 else NO_SEMANTIC_SENTENCE))
    if any(r.get("source_evidence") for r in rows):
        notes.append(html.escape(VALUES_WITHHELD_SENTENCE))
    if "AGREE" in kinds and "CONFLICT" not in kinds:
        notes.append("Las filas marcadas AGREE compararon dos o más fuentes distintas y "
                     "la evidencia resultó compatible.")
    if "CONFLICT" not in kinds:
        notes.append("<strong>Ninguna fila prueba una contradicción entre fuentes "
                     "distintas en este turno.</strong>")
    body += "".join(f'<p class="note">{n}</p>' for n in notes)
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


def _no_response_produced(trace: dict) -> tuple:
    """(is_no_response, is_historical_representation).

    Two shapes describe the same outcome. A trace written after
    L4.7W5-NO-REPLY-ALERT-INTEGRITY names it directly. One written before it carries
    `replied` + `no_reply_text` and no outbound — the stored record is evidence and is never
    rewritten, so the reading is derived here instead.
    """
    action = trace.get("response_plan_kind")
    reason = trace.get("reason_code")
    no_outbound = not trace.get("outbound_message_id") and not trace.get("outbound_path_id")
    if action == "no_reply_produced":
        return True, False
    if action == "replied" and reason == "no_reply_text" and no_outbound:
        return True, True
    return False, False


def _outcome_card(trace: dict) -> str:
    path = trace.get("outbound_path_id")
    flow_row = ""
    if path == "BOOKING_FLOW":
        flow_row = (f'<p class="note"><span class="badge flow">'
                    f'{_e(FLOW_TRANSACTION_LABEL)}</span> '
                    f'{html.escape(FLOW_TRANSACTION_NOTE)}</p>')
    body = '<div class="card"><h2>Resultado</h2>' + _kv([
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
    ])
    no_response, historical = _no_response_produced(trace)
    if no_response:
        body += (f'<p class="note"><span class="badge warn">NO RESPONSE PRODUCED</span> '
                 f'{html.escape(NO_RESPONSE_PRODUCED_SENTENCE)}</p>')
        if historical:
            body += f'<p class="note">{html.escape(NO_RESPONSE_HISTORICAL_NOTE)}</p>'
    return body + flow_row + "</div>"


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


def _effective_badges(record: dict, trace: dict) -> tuple:
    """The badge strip, headed by the EFFECTIVE classification.

    A trace stored before this correction carries the old headline in `payload.badges`; that
    record is evidence and is not rewritten, so the page re-derives the label instead of
    reprinting it. Everything after the headline — result kind, semantic pending — is taken
    from the stored strip unchanged.
    """
    stored = tuple(trace.get("badges") or ())
    effective = (record or {}).get("effective_classification")
    if not effective:
        return stored
    conditions = tuple((record or {}).get("supporting_conditions") or ())
    head = [str(effective).replace("_", " ")]
    head += [str(c).replace("_", " ") for c in conditions if c != effective]
    carried = [b for b in stored[1:] if b not in head]
    return tuple(dict.fromkeys(head + carried))


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
    # The stored payload is evidence and is never altered; what is RENDERED from it is
    # masked, exactly as the table above is. A panel that reprints the raw ids would undo
    # the masking three lines further down the page.
    dumped = html.escape(mask_wamids_in_text(
        json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True)))
    body = (
        f"<h1>Decisión del turno</h1>"
        f'<p class="sub mono">{_e(trace.get("turn_id"))}</p>'
        + _badges((SCOPE_LABEL,) + _effective_badges(record, trace))
        + f'<p class="note">{html.escape(SCOPE_NOTE)}</p>' 
        + f'<div class="card"><h2>Identidad</h2>{head}</div>'
        + _messages_card(trace)
        + _semantic_card(trace.get("semantic") or {})
        + _ce_card(trace.get("ce_evidence") or [])
        + _reconciliation_card(*_rows_and_counts(record, trace),
                               semantic=trace.get("semantic") or {})
        + _state_card(trace.get("canonical_before") or {}, trace.get("canonical_after") or {})
        + _outcome_card(trace)
        + f"<details><summary>Traza completa (JSON)</summary><pre>{dumped}</pre></details>"
        + _glossary_card()
        + '<p><a href="/control">← Volver al panel de control</a></p>')
    return _page(f"Turno {trace.get('turn_id') or turn_id}", body)
