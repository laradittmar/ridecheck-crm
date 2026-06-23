"""End-to-end smoke tests for Vehicle and Location fallback Flows.

Tests A and B run against the real DB with intercepted WhatsApp API calls.
No actual messages are sent to Meta or any real user.
All fixtures are cleaned up at the end.

Expected pricing:
  AUTO  + Palermo (CABA, viaticos=0)      = $130.000
  SUV/4x4 + Avellaneda (Sur, viaticos=30000) = $170.000
"""
import secrets
import json
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import os
from unittest.mock import patch

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)

from app.models import (
    WhatsAppContact, WhatsAppThread, WhatsAppThreadState,
    WhatsAppThreadCandidate, Lead, ThreadRevision, Revision,
)
from app.services.conversation_engine import ConversationEngine
from app.schemas.conversation import ConversationHandleIn
from app.settings import get_settings

settings = get_settings()


def _print_section(title):
    print(f"\n{'═'*60}")
    print(f"  {title}")
    print('═'*60)


def _make_fixtures(db, suffix):
    test_wa_id = f"SMOKETEST_FB_{suffix}_" + secrets.token_hex(3)
    contact = WhatsAppContact(wa_id=test_wa_id, display_name="Fallback Test", phone=test_wa_id)
    db.add(contact); db.flush()
    lead = Lead(
        nombre="Test", apellido="Fallback", telefono="1100000000",
        flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
        canal=None, email=None, necesita_humano=False,
    )
    db.add(lead); db.flush()
    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
    db.add(thread); db.flush()
    state = WhatsAppThreadState(
        thread_id=thread.id, last_stage="QUALIFYING", is_website_lead=False,
    )
    db.add(state); db.flush()
    db.commit()
    return contact.id, lead.id, thread.id, test_wa_id


def _cleanup(thread_id, lead_id, contact_id):
    with Session(engine) as db:
        for sql, p in [
            ("DELETE FROM thread_revisions WHERE thread_id=:t",       {"t": thread_id}),
            ("DELETE FROM revisions WHERE lead_id=:l",                {"l": lead_id}),
            ("DELETE FROM whatsapp_thread_states WHERE thread_id=:t", {"t": thread_id}),
            ("DELETE FROM whatsapp_messages WHERE thread_id=:t",      {"t": thread_id}),
            ("DELETE FROM whatsapp_thread_candidates WHERE thread_id=:t", {"t": thread_id}),
            ("DELETE FROM whatsapp_threads WHERE id=:t",              {"t": thread_id}),
            ("DELETE FROM leads WHERE id=:l",                         {"l": lead_id}),
            ("DELETE FROM whatsapp_contacts WHERE id=:c",             {"c": contact_id}),
        ]:
            db.execute(text(sql), p)
        db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Interceptors
# ─────────────────────────────────────────────────────────────────────────────

wa_sends = []

def fake_text(to_wa_id, text):
    wa_sends.append({"type": "text", "to": to_wa_id, "text": text})
    return ("ft-" + secrets.token_hex(3), {})

def fake_flow(to_wa_id, flow_id, flow_token, body_text, cta_label,
              initial_screen="MAIN"):
    payload = {
        "messaging_product": "whatsapp",
        "to": to_wa_id,
        "type": "interactive",
        "interactive": {
            "type": "flow",
            "body": {"text": body_text},
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_token": flow_token,
                    "flow_id": flow_id,
                    "flow_cta": cta_label,
                    "flow_action": "navigate",
                    "flow_action_payload": {"screen": initial_screen},
                },
            },
        },
    }
    wa_sends.append({
        "type": "flow",
        "to": to_wa_id,
        "flow_id": flow_id,
        "initial_screen": initial_screen,
        "token": flow_token,
        "body": body_text,
        "_full_payload": payload,
    })
    return ("ff-" + secrets.token_hex(3), {})


def _run_step(thread_id, wa_id, msg_id, msg_type, text="",
              unanswered=None, recent_user=None, flow_response=None, flow_token=None):
    ev = ConversationHandleIn(
        thread_id=thread_id, wa_id=wa_id, wa_message_id=msg_id,
        message_type=msg_type, text=text,
        unanswered_recent_user_messages=unanswered or ([text] if text else []),
        recent_user_messages=recent_user or ([text] if text else []),
        recent_outbound_replies=[],
        flow_token=flow_token,
        flow_response=flow_response,
    )
    with Session(engine) as db:
        with patch("app.services.conversation_engine._send_whatsapp_cloud_text", fake_text), \
             patch("app.services.conversation_engine._send_whatsapp_cloud_flow", fake_flow), \
             patch("app.services.unanswered_alert.reset_unanswered_alert"):
            return ConversationEngine(db=db, settings=settings).handle(ev)


def _db_state(thread_id):
    with Session(engine) as db:
        return db.execute(text("""
            SELECT last_stage, needs_human, home_zone_group, home_zone_detail,
                   vehicle_clarification_sent, location_clarification_sent,
                   vehicle_fallback_flow_sent, location_fallback_flow_sent,
                   current_focus_candidate_id
            FROM whatsapp_thread_states WHERE thread_id=:t
        """), {"t": thread_id}).fetchone()


def _db_lead(lead_id):
    with Session(engine) as db:
        return db.execute(text(
            "SELECT flag, estado, necesita_humano FROM leads WHERE id=:l"
        ), {"l": lead_id}).fetchone()


def _db_candidate(thread_id):
    with Session(engine) as db:
        return db.execute(text("""
            SELECT tipo_vehiculo, marca, modelo, anio
            FROM whatsapp_thread_candidates WHERE thread_id=:t
            ORDER BY id DESC LIMIT 1
        """), {"t": thread_id}).fetchone()


def _db_revision_count(thread_id, lead_id):
    with Session(engine) as db:
        tr = db.execute(text(
            "SELECT COUNT(*) FROM thread_revisions WHERE thread_id=:t"
        ), {"t": thread_id}).scalar()
        cr = db.execute(text(
            "SELECT COUNT(*) FROM revisions WHERE lead_id=:l"
        ), {"l": lead_id}).scalar()
        return tr, cr


# ═════════════════════════════════════════════════════════════════════════════
# TEST A: Unknown vehicle (Xylo Z9) + valid location (Palermo, CABA)
# Expected: clarification → Vehicle Flow (ID=27205677485784073, VEHICLE_DETAILS)
#           → Flow submit AUTO/Toyota/Corolla → quote $130.000
# ═════════════════════════════════════════════════════════════════════════════

_print_section("TEST A  —  Unknown vehicle + valid location (Palermo)")
print(f"  Vehicle fallback Flow ID : {settings.whatsapp_vehicle_fallback_flow_id}")
assert settings.whatsapp_vehicle_fallback_flow_id == "27205677485784073", "WRONG vehicle fallback ID"

with Session(engine) as db:
    cid_a, lid_a, tid_a, wid_a = _make_fixtures(db, "A")
print(f"  thread_id={tid_a}  lead_id={lid_a}")

# ── A-Step 1: First unclear message, valid zone ───────────────────────────────
wa_sends.clear()
r = _run_step(tid_a, wid_a, "sma-1", "text", "Quiero revisar un Xylo Z9 en Palermo")
assert r.action == "replied", f"Expected replied, got {r.action}"
s = _db_state(tid_a)
print(f"\n[A-Step 1]  action={r.action}")
print(f"  reply:  {wa_sends[-1]['text'][:100]}")
print(f"  DB:     zone_group={s[2]}  zone_detail={s[3]}")
print(f"          vehicle_clarification_sent={s[4]}  location_clarification_sent={s[5]}")
assert s[4] is True,  "vehicle_clarification_sent must be True after step 1"
assert s[5] is False, "location_clarification_sent must stay False (zone was found)"
# Zone may or may not be set at this point depending on Palermo DB hit + normalize
print(f"  ✓ vehicle_clarification_sent=True (chat clarification sent)")

# ── A-Step 2: Still no vehicle, clarification already sent → Vehicle Flow ─────
wa_sends.clear()
r = _run_step(tid_a, wid_a, "sma-2", "text",
              "es un auto compacto, no recuerdo bien la marca",
              unanswered=["es un auto compacto, no recuerdo bien la marca"],
              recent_user=["Quiero revisar un Xylo Z9 en Palermo",
                           "es un auto compacto, no recuerdo bien la marca"])
assert r.action == "replied", f"Expected replied, got {r.action}"
s = _db_state(tid_a)
flow_sends = [m for m in wa_sends if m["type"] == "flow"]
print(f"\n[A-Step 2]  action={r.action}  flow_sends={len(flow_sends)}")

assert len(flow_sends) == 1, f"Expected 1 Flow send, got {len(flow_sends)}"
fc = flow_sends[0]
print(f"\n  ── OUTBOUND VEHICLE FALLBACK FLOW PAYLOAD ──")
print(f"  to             : {fc['to']}")
print(f"  flow_id        : {fc['flow_id']}")
print(f"  initial_screen : {fc['initial_screen']}")
print(f"  body           : {fc['body']}")
print(f"  token          : {fc['token']}")
print(f"\n  Full Meta API payload:")
print(json.dumps(fc["_full_payload"], ensure_ascii=False, indent=4))

assert fc["flow_id"] == "27205677485784073",   f"WRONG flow_id={fc['flow_id']}"
assert fc["initial_screen"] == "VEHICLE_DETAILS", f"WRONG screen={fc['initial_screen']}"
assert s[6] is True, "vehicle_fallback_flow_sent must be True"
print(f"\n  ✓ flow_id=27205677485784073  initial_screen=VEHICLE_DETAILS")

# ── A-Step 2b: Message while Vehicle Flow is open → reminder, no AI, no new Flow ──
wa_sends.clear()
r = _run_step(tid_a, wid_a, "sma-2b", "text", "no puedo abrir el formulario",
              recent_user=["no puedo abrir el formulario"])
texts_2b = [m for m in wa_sends if m["type"] == "text"]
flows_2b = [m for m in wa_sends if m["type"] == "flow"]
print(f"\n[A-Step 2b] Message while Flow open → action={r.action}")
print(f"  texts={len(texts_2b)}  flows={len(flows_2b)}")
print(f"  reminder: {texts_2b[0]['text'][:100] if texts_2b else 'NONE'}")
assert len(flows_2b) == 0, "Must not re-dispatch Flow"
assert len(texts_2b) >= 1, "Must send reminder text"
assert "formulario" in texts_2b[0]["text"].lower(), "Reminder must mention formulario"
print(f"  ✓ no new Flow, no AI — reminder sent instead")

# ── A-Step 3: Vehicle Flow submit → updates candidate → quote ─────────────────
wa_sends.clear()
r = _run_step(tid_a, wid_a, "sma-3", "flow_response",
              flow_response={"tipo_vehiculo": "AUTO", "marca": "Toyota",
                             "modelo": "Corolla", "anio": "2021"},
              flow_token="fake-token-vehicle-a")
assert r.action == "replied", f"Expected replied, got {r.action}"
s = _db_state(tid_a)
lead_a = _db_lead(lid_a)
cand_a = _db_candidate(tid_a)
tr_a, cr_a = _db_revision_count(tid_a, lid_a)
reply_text = wa_sends[-1]["text"] if wa_sends else ""
print(f"\n[A-Step 3]  action={r.action}")
print(f"  reply:  {reply_text[:120]}")
print(f"  DB candidate: tipo={cand_a[0]}  marca={cand_a[1]}  modelo={cand_a[2]}  anio={cand_a[3]}")
print(f"  DB state:     stage={s[0]}  needs_human={s[1]}")
print(f"  DB lead:      flag={lead_a[0]}  estado={lead_a[1]}  necesita_humano={lead_a[2]}")
print(f"  Revisions:    thread_revisions={tr_a}  crm_revisions={cr_a}")

assert cand_a[0] == "AUTO",       f"tipo_vehiculo must be AUTO, got {cand_a[0]}"
assert cand_a[1] == "Toyota",     f"marca must be Toyota, got {cand_a[1]}"
assert cand_a[2] == "Corolla",    f"modelo must be Corolla, got {cand_a[2]}"
assert cand_a[3] == 2021,         f"anio must be 2021, got {cand_a[3]}"
assert s[0] == "QUOTED",          f"stage must be QUOTED, got {s[0]}"
assert lead_a[0] == "PRESUPUESTO_ENVIADO", f"flag must be PRESUPUESTO_ENVIADO, got {lead_a[0]}"
assert lead_a[1] != "AGENDADO",   "lead.estado must NOT be AGENDADO"
assert lead_a[2] is False,        "necesita_humano must be False"
assert tr_a == 0,                 f"No ThreadRevision expected, got {tr_a}"
assert cr_a == 0,                 f"No CRM Revision expected, got {cr_a}"
assert "130" in reply_text,       f"Reply must contain price 130.000, got: {reply_text}"
assert s[6] is False,             "vehicle_fallback_flow_sent must be cleared after submit"
print(f"  ✓ candidate updated  stage=QUOTED  flag=PRESUPUESTO_ENVIADO")
print(f"  ✓ price $130.000 in reply (AUTO + Palermo CABA, viaticos=0)")
print(f"  ✓ NO ThreadRevision  NO CRM Revision  NOT AGENDADO")


# ═════════════════════════════════════════════════════════════════════════════
# TEST B: Valid vehicle (Peugeot 3008 → SUV/4x4) + unknown location
# "Puerto Nuevo Delta" → not in zones DB
# Expected: clarification → Location Flow (ID=2550767958730294, LOCATION_DETAILS)
#           → Flow submit SUR/Avellaneda → quote $170.000
# ═════════════════════════════════════════════════════════════════════════════

_print_section("TEST B  —  Valid vehicle (Peugeot 3008) + unknown location")
print(f"  Location fallback Flow ID : {settings.whatsapp_location_fallback_flow_id}")
assert settings.whatsapp_location_fallback_flow_id == "2550767958730294", "WRONG location fallback ID"

with Session(engine) as db:
    cid_b, lid_b, tid_b, wid_b = _make_fixtures(db, "B")
print(f"  thread_id={tid_b}  lead_id={lid_b}")

# ── B-Step 1: Known vehicle, unknown location → location clarification ─────────
wa_sends.clear()
r = _run_step(tid_b, wid_b, "smb-1", "text",
              "Peugeot 3008 en Puerto Nuevo Delta")
assert r.action == "replied", f"Expected replied, got {r.action}"
s = _db_state(tid_b)
cand_b1 = _db_candidate(tid_b)
print(f"\n[B-Step 1]  action={r.action}")
print(f"  reply:     {wa_sends[-1]['text'][:100]}")
print(f"  DB state:  zone_group={s[2]}  zone_detail={s[3]}")
print(f"             location_clarification_sent={s[5]}  vehicle_clarification_sent={s[4]}")
print(f"  candidate: tipo={cand_b1[0]}  marca={cand_b1[1]}  modelo={cand_b1[2]}")
assert cand_b1 is not None,     "Candidate must be created (Peugeot 3008 catalog hit)"
assert cand_b1[0] == "SUV/4x4", f"tipo_vehiculo must be SUV/4x4, got {cand_b1[0]}"
assert s[5] is True,            "location_clarification_sent must be True after step 1"
assert s[4] is False,           "vehicle_clarification_sent must stay False (vehicle was found)"
print(f"  ✓ Peugeot 3008 → SUV/4x4 candidate created")
print(f"  ✓ location_clarification_sent=True (chat clarification sent)")

# ── B-Step 2: Still no zone, clarification already sent → Location Flow ────────
wa_sends.clear()
r = _run_step(tid_b, wid_b, "smb-2", "text",
              "es en puerto nuevo delta cerca del rio, no sé qué zona es",
              unanswered=["es en puerto nuevo delta cerca del rio, no sé qué zona es"],
              recent_user=["Peugeot 3008 en Puerto Nuevo Delta",
                           "es en puerto nuevo delta cerca del rio, no sé qué zona es"])
assert r.action == "replied", f"Expected replied, got {r.action}"
s = _db_state(tid_b)
flow_sends_b = [m for m in wa_sends if m["type"] == "flow"]
print(f"\n[B-Step 2]  action={r.action}  flow_sends={len(flow_sends_b)}")

assert len(flow_sends_b) == 1, f"Expected 1 Flow send, got {len(flow_sends_b)}"
fc_b = flow_sends_b[0]
print(f"\n  ── OUTBOUND LOCATION FALLBACK FLOW PAYLOAD ──")
print(f"  to             : {fc_b['to']}")
print(f"  flow_id        : {fc_b['flow_id']}")
print(f"  initial_screen : {fc_b['initial_screen']}")
print(f"  body           : {fc_b['body']}")
print(f"  token          : {fc_b['token']}")
print(f"\n  Full Meta API payload:")
print(json.dumps(fc_b["_full_payload"], ensure_ascii=False, indent=4))

assert fc_b["flow_id"] == "2550767958730294",    f"WRONG flow_id={fc_b['flow_id']}"
assert fc_b["initial_screen"] == "LOCATION_DETAILS", f"WRONG screen={fc_b['initial_screen']}"
assert s[7] is True, "location_fallback_flow_sent must be True"
print(f"\n  ✓ flow_id=2550767958730294  initial_screen=LOCATION_DETAILS")

# ── B-Step 2b: Message while Location Flow is open → reminder, no AI, no new Flow ──
wa_sends.clear()
r = _run_step(tid_b, wid_b, "smb-2b", "text", "no sé cómo usar esto",
              recent_user=["no sé cómo usar esto"])
texts_b2b = [m for m in wa_sends if m["type"] == "text"]
flows_b2b = [m for m in wa_sends if m["type"] == "flow"]
print(f"\n[B-Step 2b] Message while Location Flow open → action={r.action}")
print(f"  texts={len(texts_b2b)}  flows={len(flows_b2b)}")
print(f"  reminder: {texts_b2b[0]['text'][:100] if texts_b2b else 'NONE'}")
assert len(flows_b2b) == 0, "Must not re-dispatch Location Flow"
assert len(texts_b2b) >= 1, "Must send reminder text"
assert "formulario" in texts_b2b[0]["text"].lower()
print(f"  ✓ no new Flow, no AI — reminder sent instead")

# ── B-Step 3: Location Flow submit (SUR/Avellaneda) → resolves zone → quote ────
wa_sends.clear()
r = _run_step(tid_b, wid_b, "smb-3", "flow_response",
              flow_response={"zona_general": "SUR", "localidad": "Avellaneda",
                             "referencia_ubicacion": ""},
              flow_token="fake-token-location-b")
assert r.action == "replied", f"Expected replied, got {r.action}"
s = _db_state(tid_b)
lead_b = _db_lead(lid_b)
cand_b3 = _db_candidate(tid_b)
tr_b, cr_b = _db_revision_count(tid_b, lid_b)
reply_b = wa_sends[-1]["text"] if wa_sends else ""
print(f"\n[B-Step 3]  action={r.action}")
print(f"  reply:     {reply_b[:120]}")
print(f"  DB state:  zone_group={s[2]}  zone_detail={s[3]}  stage={s[0]}")
print(f"  DB lead:   flag={lead_b[0]}  estado={lead_b[1]}  necesita_humano={lead_b[2]}")
print(f"  Revisions: thread_revisions={tr_b}  crm_revisions={cr_b}")

assert s[2] == "Sur",                 f"zone_group must be Sur, got {s[2]}"
assert s[3] == "Avellaneda",          f"zone_detail must be Avellaneda, got {s[3]}"
assert s[0] == "QUOTED",              f"stage must be QUOTED, got {s[0]}"
assert lead_b[0] == "PRESUPUESTO_ENVIADO", f"flag must be PRESUPUESTO_ENVIADO, got {lead_b[0]}"
assert lead_b[1] != "AGENDADO",       "lead.estado must NOT be AGENDADO"
assert lead_b[2] is False,            "necesita_humano must be False"
assert tr_b == 0,                     f"No ThreadRevision expected, got {tr_b}"
assert cr_b == 0,                     f"No CRM Revision expected, got {cr_b}"
assert "170" in reply_b,              f"Reply must contain price 170.000, got: {reply_b}"
assert s[7] is False,                 "location_fallback_flow_sent must be cleared after submit"
print(f"  ✓ zone normalized: Sur/Avellaneda")
print(f"  ✓ price $170.000 in reply (SUV/4x4 + Avellaneda Sur, viaticos=30000)")
print(f"  ✓ NO ThreadRevision  NO CRM Revision  NOT AGENDADO")


# ═════════════════════════════════════════════════════════════════════════════
# TEST C: OTRO in Vehicle Flow → needs_human + warm handoff
# ═════════════════════════════════════════════════════════════════════════════

_print_section("TEST C  —  OTRO in Vehicle Flow submit → needs_human")
with Session(engine) as db:
    cid_c, lid_c, tid_c, wid_c = _make_fixtures(db, "C")
    # Pre-seed state: vehicle flow already dispatched
    db.execute(text("""
        UPDATE whatsapp_thread_states
        SET vehicle_clarification_sent=true, vehicle_fallback_flow_sent=true
        WHERE thread_id=:t
    """), {"t": tid_c})
    db.commit()
print(f"  thread_id={tid_c}  (pre-seeded: vehicle_fallback_flow_sent=True)")

wa_sends.clear()
r = _run_step(tid_c, wid_c, "smc-1", "flow_response",
              flow_response={"tipo_vehiculo": "OTRO", "marca": "", "modelo": "", "anio": ""},
              flow_token="fake-token-c")
s = _db_state(tid_c)
lead_c = _db_lead(lid_c)
tr_c, cr_c = _db_revision_count(tid_c, lid_c)
print(f"  action={r.action}  reply: {wa_sends[-1]['text'][:100] if wa_sends else 'NONE'}")
print(f"  needs_human={s[1]}  lead.necesita_humano={lead_c[2]}")
print(f"  thread_revisions={tr_c}  crm_revisions={cr_c}")
assert s[1] is True,      "needs_human must be True after OTRO submit"
assert lead_c[2] is True, "lead.necesita_humano must be True"
assert tr_c == 0,         "No ThreadRevision must be created"
assert cr_c == 0,         "No CRM Revision must be created"
assert lead_c[1] != "AGENDADO", "lead.estado must NOT be AGENDADO"
print(f"  ✓ needs_human=True  warm handoff sent  no revision  NOT AGENDADO")


# ═════════════════════════════════════════════════════════════════════════════
# TEST D: OTRO in Location Flow → needs_human + warm handoff
# ═════════════════════════════════════════════════════════════════════════════

_print_section("TEST D  —  OTRO in Location Flow submit → needs_human")
with Session(engine) as db:
    cid_d, lid_d, tid_d, wid_d = _make_fixtures(db, "D")
    db.execute(text("""
        UPDATE whatsapp_thread_states
        SET location_clarification_sent=true, location_fallback_flow_sent=true
        WHERE thread_id=:t
    """), {"t": tid_d})
    db.commit()
print(f"  thread_id={tid_d}  (pre-seeded: location_fallback_flow_sent=True)")

wa_sends.clear()
r = _run_step(tid_d, wid_d, "smd-1", "flow_response",
              flow_response={"zona_general": "OTRO", "localidad": "", "referencia_ubicacion": ""},
              flow_token="fake-token-d")
s = _db_state(tid_d)
lead_d = _db_lead(lid_d)
tr_d, cr_d = _db_revision_count(tid_d, lid_d)
print(f"  action={r.action}  reply: {wa_sends[-1]['text'][:100] if wa_sends else 'NONE'}")
print(f"  needs_human={s[1]}  lead.necesita_humano={lead_d[2]}")
print(f"  thread_revisions={tr_d}  crm_revisions={cr_d}")
assert s[1] is True,      "needs_human must be True after OTRO zona_general"
assert lead_d[2] is True, "lead.necesita_humano must be True"
assert tr_d == 0,         "No ThreadRevision must be created"
assert cr_d == 0,         "No CRM Revision must be created"
assert lead_d[1] != "AGENDADO", "lead.estado must NOT be AGENDADO"
print(f"  ✓ needs_human=True  warm handoff sent  no revision  NOT AGENDADO")


# ═════════════════════════════════════════════════════════════════════════════
# TEST E: Real-production replay — Peugeot 3008 + NORTE/Benavidez
#   Now that Benavidez is in viaticos_zones (Norte, viaticos=0) the submit
#   must resolve to a quote ($140.000) NOT needs_human.
# ═════════════════════════════════════════════════════════════════════════════

_print_section("TEST E  —  Peugeot 3008 + NORTE/Benavidez (prod replay, should now quote)")
with Session(engine) as db:
    cid_e, lid_e, tid_e, wid_e = _make_fixtures(db, "E")
    # Pre-seed: Peugeot 3008 candidate already created, location Flow dispatched
    db.execute(text("""
        INSERT INTO whatsapp_thread_candidates
          (thread_id, tipo_vehiculo, marca, modelo, status, source_text)
        VALUES (:t, 'SUV/4x4', 'Peugeot', '3008', 'current_focus', 'smoke_e')
    """), {"t": tid_e})
    db.execute(text("""
        UPDATE whatsapp_thread_states
        SET location_clarification_sent=true, location_fallback_flow_sent=true,
            current_focus_candidate_id=(
                SELECT id FROM whatsapp_thread_candidates WHERE thread_id=:t LIMIT 1
            )
        WHERE thread_id=:t
    """), {"t": tid_e})
    db.commit()
print(f"  thread_id={tid_e}  (Peugeot 3008 candidate, location_fallback_flow_sent=True)")

wa_sends.clear()
r = _run_step(tid_e, wid_e, "sme-1", "flow_response",
              flow_response={"zona_general": "NORTE", "localidad": "Benavidez",
                             "referencia_ubicacion": ""},
              flow_token="fake-token-e")
assert r.action == "replied", f"Expected replied, got {r.action}"
s = _db_state(tid_e)
lead_e = _db_lead(lid_e)
tr_e, cr_e = _db_revision_count(tid_e, lid_e)
reply_e = wa_sends[-1]["text"] if wa_sends else ""
print(f"  action={r.action}")
print(f"  reply:  {reply_e[:120]}")
print(f"  DB state:  zone_group={s[2]}  zone_detail={s[3]}  stage={s[0]}")
print(f"  DB lead:   flag={lead_e[0]}  necesita_humano={lead_e[2]}")
print(f"  Revisions: thread_revisions={tr_e}  crm_revisions={cr_e}")

assert s[2] == "Norte",                   f"zone_group must be Norte, got {s[2]}"
assert s[3] == "Benavidez",              f"zone_detail must be Benavidez, got {s[3]}"
assert s[0] == "QUOTED",                  f"stage must be QUOTED, got {s[0]}"
assert s[1] is False,                     "needs_human must be False (Benavidez now in DB)"
assert lead_e[0] == "PRESUPUESTO_ENVIADO", f"flag must be PRESUPUESTO_ENVIADO, got {lead_e[0]}"
assert lead_e[2] is False,               "necesita_humano must be False"
assert tr_e == 0,                         f"No ThreadRevision expected, got {tr_e}"
assert cr_e == 0,                         f"No CRM Revision expected, got {cr_e}"
assert "140" in reply_e,                  f"Reply must contain $140.000 (SUV/4x4+Norte/viaticos=0), got: {reply_e}"
print(f"  ✓ Norte/Benavidez now resolves to a quote (viaticos=0)")
print(f"  ✓ price $140.000  stage=QUOTED  NO revision  NOT AGENDADO")


# ═════════════════════════════════════════════════════════════════════════════
# TEST F: Outbound Flow message saved with message_type='flow'
# ═════════════════════════════════════════════════════════════════════════════

_print_section("TEST F  —  Outbound Flow message_type='flow' in DB")
with Session(engine) as db:
    # Check thread A — the vehicle fallback flow was sent in step 2
    # But tid_a fixtures are already cleaned up (we'll verify from thread E flow step)
    # Instead verify a fresh step via thread lookup
    _cid_f, _lid_f, _tid_f, _wid_f = _make_fixtures(db, "F")

with Session(engine) as db:
    db.execute(text("""
        UPDATE whatsapp_thread_states
        SET vehicle_clarification_sent=true
        WHERE thread_id=:t
    """), {"t": _tid_f})
    db.commit()

wa_sends.clear()
_run_step(_tid_f, _wid_f, "smf-1", "text", "no sé qué auto tengo",
          unanswered=["no sé qué auto tengo"],
          recent_user=["no sé qué auto tengo"])

with Session(engine) as db:
    row = db.execute(text("""
        SELECT message_type, text FROM whatsapp_messages
        WHERE thread_id=:t AND direction='out'
        ORDER BY id DESC LIMIT 1
    """), {"t": _tid_f}).fetchone()

print(f"  Outbound message: type={row[0]}  text={row[1][:60]!r}")
assert row[0] == "flow", f"message_type must be 'flow', got {row[0]!r}"
assert row[1] and row[1] != "-", "Flow body text must be stored (not empty)"
print(f"  ✓ message_type='flow' correctly stored for CRM rendering")
_cleanup(_tid_f, _lid_f, _cid_f)


# ═════════════════════════════════════════════════════════════════════════════
# Summary
# ═════════════════════════════════════════════════════════════════════════════

_print_section("FINAL COUNTS")
with Session(engine) as db:
    def count(sql, p): return db.execute(text(sql), p).scalar()

    for label, tid, lid in [("Test A", tid_a, lid_a), ("Test B", tid_b, lid_b),
                              ("Test C", tid_c, lid_c), ("Test D", tid_d, lid_d),
                              ("Test E", tid_e, lid_e)]:
        tc = count("SELECT COUNT(*) FROM whatsapp_thread_candidates WHERE thread_id=:t", {"t": tid})
        tr_ = count("SELECT COUNT(*) FROM thread_revisions WHERE thread_id=:t", {"t": tid})
        cr_ = count("SELECT COUNT(*) FROM revisions WHERE lead_id=:l", {"l": lid})
        ld_ = db.execute(text("SELECT flag, estado FROM leads WHERE id=:l"), {"l": lid}).fetchone()
        print(f"  {label}: candidates={tc}  thread_revisions={tr_}  crm_revisions={cr_}"
              f"  flag={ld_[0]}  estado={ld_[1]}")

# ─────────────────────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────────────────────

for tid, lid, cid in [(tid_a, lid_a, cid_a), (tid_b, lid_b, cid_b),
                       (tid_c, lid_c, cid_c), (tid_d, lid_d, cid_d),
                       (tid_e, lid_e, cid_e)]:
    _cleanup(tid, lid, cid)

print("\n[DB] All test fixtures cleaned up.")
print("\n" + "═"*60)
print("  ALL SMOKE ASSERTIONS PASSED ✓")
print("═"*60)
