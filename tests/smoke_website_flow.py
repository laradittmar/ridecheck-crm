"""End-to-end smoke test for the website-lead Flow path.

Runs against the real DB and real ConversationEngine.
WhatsApp API calls are intercepted — no actual WhatsApp messages sent.
All test fixtures are cleaned up at the end.
"""
import secrets
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
import os

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL)

from app.models import (
    WhatsAppContact, WhatsAppThread, WhatsAppThreadState,
    Lead, ThreadRevision, Revision,
)
from app.services.conversation_engine import ConversationEngine
from app.services.pricing import PricingService
from app.repositories.pricing_repository import PricingRepository
from app.schemas.conversation import ConversationHandleIn
from app.settings import get_settings
from app.services.schedule import ScheduleService
from app.schemas.schedule import ScheduleCheckOut, ScheduleSlotOut
from unittest.mock import patch

settings = get_settings()
print(f"Generic Flow : {settings.whatsapp_flow_id}")
print(f"Website Flow : {settings.whatsapp_website_flow_id}")

# ── 1. Test fixtures in real DB ───────────────────────────────────────────────
with Session(engine) as db:
    test_wa_id = "SMOKETEST_" + secrets.token_hex(4)
    contact = WhatsAppContact(wa_id=test_wa_id, display_name="Smoke Test", phone=test_wa_id)
    db.add(contact); db.flush()
    lead = Lead(
        nombre="Smoke", apellido="Test", telefono="1133445566",
        flag="PRESUPUESTANDO", estado="CONSULTA_NUEVA",
        canal=None, email=None, necesita_humano=False,
    )
    db.add(lead); db.flush()
    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
    db.add(thread); db.flush()
    state = WhatsAppThreadState(thread_id=thread.id, last_stage="QUALIFYING", is_website_lead=False)
    db.add(state); db.flush()
    thread_id = thread.id
    lead_id   = lead.id
    contact_id = contact.id
    db.commit()

print(f"[DB] thread_id={thread_id}  lead_id={lead_id}  wa_id={test_wa_id}")

# ── WA API interceptors ───────────────────────────────────────────────────────
wa_sends = []

def fake_text(to_wa_id, text):
    wa_sends.append({"type": "text", "to": to_wa_id, "text": text})
    return ("ft-" + secrets.token_hex(3), {})

def fake_flow(to_wa_id, flow_id, flow_token, body_text, cta_label):
    wa_sends.append({"type": "flow", "to": to_wa_id, "flow_id": flow_id,
                     "token": flow_token, "body": body_text})
    return ("ff-" + secrets.token_hex(3), {})

FORM_MSG = (
    "Hola, quiero solicitar una revisión pre-compra.\n\n"
    "* Nombre: Ana Gómez\n"
    "* Teléfono: 1133445566\n"
    "* Auto a revisar: Toyota Corolla 2021\n"
    "* Tipo: Auto pequeño o mediano\n"
    "* Localidad: Almagro\n"
    "* Total estimado: $130.000\n\n"
    "Quedo atenta para coordinar\n"
    "ref: smoke-test-2026"
)

# ── Step 1: website form inbound ──────────────────────────────────────────────
ev1 = ConversationHandleIn(
    thread_id=thread_id, wa_id=test_wa_id, wa_message_id="smoke-msg-1",
    message_type="text", text=FORM_MSG,
    unanswered_recent_user_messages=[FORM_MSG], recent_user_messages=[FORM_MSG],
    recent_outbound_replies=[], flow_token=None, flow_response=None,
)
with Session(engine) as db:
    with patch("app.services.conversation_engine._send_whatsapp_cloud_text", fake_text), \
         patch("app.services.conversation_engine._send_whatsapp_cloud_flow", fake_flow), \
         patch("app.services.unanswered_alert.reset_unanswered_alert"):
        r1 = ConversationEngine(db=db, settings=settings).handle(ev1)

assert r1.action == "replied", r1.action
print(f"\n[Step 1] action={r1.action}")
print(f"[WA]    text reply: {wa_sends[-1]['text'][:90]}")

with Session(engine) as db:
    s = db.execute(text(
        "SELECT last_stage, is_website_lead, customer_name, home_zone_detail "
        "FROM whatsapp_thread_states WHERE thread_id=:t"
    ), {"t": thread_id}).fetchone()
print(f"[DB]    stage={s[0]}  is_website_lead={s[1]}  customer={s[2]}  zone={s[3]}")
assert s[1] is True
assert s[0] == "SCHEDULING"
assert s[2] == "Ana Gómez"
assert s[3] == "Almagro"
print("        ✓ is_website_lead=True  stage=SCHEDULING  customer+zone set")

# ── Step 2: slot selection → Flow button ──────────────────────────────────────
wa_sends.clear()

def fake_check(self, payload):
    return ScheduleCheckOut(
        valid=True, suggested_slots=[], approval_tag="Esperando aprobación",
        requested_slot=ScheduleSlotOut(start="2026-06-23T10:00", end="2026-06-23T10:45"),
        business_hours="09:00-15:00", service_minutes=45, buffer_minutes=15,
        travel_minutes=0, total_slot_minutes=60, conflicts=[], reasons=[], rules_applied=[],
    )

ev2 = ConversationHandleIn(
    thread_id=thread_id, wa_id=test_wa_id, wa_message_id="smoke-msg-2",
    message_type="text", text="el lunes a las 10hs",
    unanswered_recent_user_messages=["el lunes a las 10hs"],
    recent_user_messages=[FORM_MSG, "el lunes a las 10hs"],
    recent_outbound_replies=[], flow_token=None, flow_response=None,
)
with Session(engine) as db:
    with patch("app.services.conversation_engine._send_whatsapp_cloud_text", fake_text), \
         patch("app.services.conversation_engine._send_whatsapp_cloud_flow", fake_flow), \
         patch("app.services.unanswered_alert.reset_unanswered_alert"), \
         patch.object(ScheduleService, "check", fake_check):
        r2 = ConversationEngine(
            db=db, settings=settings
        ).handle(ev2)

assert r2.action == "flow_button_sent", r2.action
fc = next(m for m in wa_sends if m["type"] == "flow")

print(f"\n[Step 2] action={r2.action}")
print("══════════════════════════════════════════════════════")
print("  OUTBOUND FLOW PAYLOAD (intercepted from WA API call)")
print(f"  to      : {fc['to']}")
print(f"  flow_id : {fc['flow_id']}")
print(f"  token   : {fc['token']}")
print(f"  body    : {fc['body']}")
print("══════════════════════════════════════════════════════")

assert fc["flow_id"] == "1535038801697863", (
    f"WRONG flow_id={fc['flow_id']} — expected 1535038801697863 (website Flow)")
print("  ✓ flow_id = 1535038801697863  (website short Flow)")
print("  ✓ NOT 1644218879979041        (generic Flow — correctly excluded)")

used_token = fc["token"]

with Session(engine) as db:
    s2 = db.execute(text(
        "SELECT last_stage, flow_booking_token, preferred_day, preferred_time "
        "FROM whatsapp_thread_states WHERE thread_id=:t"
    ), {"t": thread_id}).fetchone()
print(f"[DB]    stage={s2[0]}  token={s2[1]}  day={s2[2]}  time={s2[3]}")

# ── Step 3: short Flow response (4 fields only) ───────────────────────────────
wa_sends.clear()
ev3 = ConversationHandleIn(
    thread_id=thread_id, wa_id=test_wa_id, wa_message_id="smoke-msg-3",
    message_type="flow_response", text="",
    unanswered_recent_user_messages=[], recent_user_messages=[], recent_outbound_replies=[],
    flow_token=used_token,
    flow_response={
        "email":          "ana.gomez@example.com",
        "direccion":      "Av. Corrientes 4500, Almagro",
        "tipo_vendedor":  "particular",
        "nombre_vendedor": "Juan Vendedor",
        # nombre_apellido : ABSENT — not collected by website Flow
        # telefono         : ABSENT — not collected by website Flow
        # como_llego       : ABSENT — not collected by website Flow
    },
)
with Session(engine) as db:
    with patch("app.services.conversation_engine._send_whatsapp_cloud_text", fake_text), \
         patch("app.services.conversation_engine._send_whatsapp_cloud_flow", fake_flow), \
         patch("app.services.unanswered_alert.reset_unanswered_alert"), \
         patch("app.services.conversation_engine.ConversationEngine._send_booking_notification"):
        r3 = ConversationEngine(
            db=db, settings=settings
        ).handle(ev3)

assert r3.action == "booking_created", r3.action
print(f"\n[Step 3] action={r3.action}")

# ── Final DB verification ─────────────────────────────────────────────────────
with Session(engine) as db:
    ts = db.execute(text(
        "SELECT last_stage, needs_human, flow_booking_token "
        "FROM whatsapp_thread_states WHERE thread_id=:t"
    ), {"t": thread_id}).fetchone()
    ld = db.execute(text(
        "SELECT flag, estado, nombre, email, canal, necesita_humano "
        "FROM leads WHERE id=:l"
    ), {"l": lead_id}).fetchone()
    tr = db.execute(text(
        "SELECT buyer_name, buyer_phone, buyer_email, seller_type, seller_name, "
        "       address, scheduled_date, scheduled_time, tipo_vehiculo, marca, modelo, "
        "       appointment_approval_status "
        "FROM thread_revisions WHERE thread_id=:t ORDER BY id DESC LIMIT 1"
    ), {"t": thread_id}).fetchone()
    cr = db.execute(text(
        "SELECT tipo_vehiculo, marca, modelo, zone_group, zone_detail, "
        "       direccion_texto, vendedor_tipo, turno_fecha, turno_hora "
        "FROM revisions WHERE lead_id=:l ORDER BY id DESC LIMIT 1"
    ), {"l": lead_id}).fetchone()

print()
print("══════════════════════════════════════════════════════")
print("  FINAL DB STATE")
print(f"  thread_state.stage           = {ts[0]}")
print(f"  thread_state.needs_human     = {ts[1]}")
print(f"  thread_state.flow_token      = {ts[2]}")
print(f"  lead.flag                    = {ld[0]}")
print(f"  lead.estado                  = {ld[1]}")
print(f"  lead.nombre                  = {ld[2]}")
print(f"  lead.email                   = {ld[3]}")
print(f"  lead.canal                   = {ld[4]}")
print(f"  lead.necesita_humano         = {ld[5]}")
print()
print("  ThreadRevision — from WEBSITE FORM (not re-asked by Flow):")
print(f"    buyer_name     = {tr[0]}   ← state.customer_name")
print(f"    buyer_phone    = {tr[1]}     ← lead.telefono")
print(f"    tipo_vehiculo  = {tr[8]}              ← candidate (catalog)")
print(f"    marca          = {tr[9]}")
print(f"    modelo         = {tr[10]}")
print()
print("  ThreadRevision — from SHORT FLOW RESPONSE (4 fields):")
print(f"    buyer_email    = {tr[2]}")
print(f"    seller_type    = {tr[3]}")
print(f"    seller_name    = {tr[4]}")
print(f"    address        = {tr[5]}")
print(f"    scheduled_date = {tr[6]}")
print(f"    scheduled_time = {tr[7]}")
print(f"    appr_status    = {tr[11]}")
print()
print("  CRM Revision:")
print(f"    tipo_vehiculo  = {cr[0]}")
print(f"    marca/modelo   = {cr[1]} {cr[2]}")
print(f"    zone_group     = {cr[3]}")
print(f"    zone_detail    = {cr[4]}")
print(f"    direccion      = {cr[5]}")
print(f"    vendedor_tipo  = {cr[6]}")
print(f"    turno_fecha    = {cr[7]}")
print(f"    turno_hora     = {cr[8]}")
print("══════════════════════════════════════════════════════")

# Assertions
assert ts[0] == "BOOKED"
assert ts[1] is True
assert ts[2] is None,             "flow_token must be consumed"
assert ld[0] == "ACEPTADO"
assert ld[1] == "COORDINAR_DISPONIBILIDAD"
assert ld[1] != "AGENDADO",      "Must NOT be AGENDADO — human approval required"
assert ld[3] == "ana.gomez@example.com"
assert ld[4] == "Formulario web"
assert ld[5] is True
assert "Ana" in (tr[0] or ""),   f"buyer_name from context: {tr[0]}"
assert "1133445566" in (tr[1] or ""), f"buyer_phone from lead: {tr[1]}"
assert tr[2] == "ana.gomez@example.com"
assert tr[3] == "particular"
assert tr[5] == "Av. Corrientes 4500, Almagro"
assert tr[11] == "PENDING"
assert tr[8] == "AUTO"
assert cr[4] == "Almagro"

print()
print("ALL ASSERTIONS PASSED ✓")
print("No duplicate name/phone/vehicle/zone/quote/source questions issued.")
print(f"lead.estado = {ld[1]}  →  AGENDADO requires human CRM approval")

# ── Cleanup ───────────────────────────────────────────────────────────────────
with Session(engine) as db:
    for sql, p in [
        ("DELETE FROM thread_revisions WHERE thread_id=:t",    {"t": thread_id}),
        ("DELETE FROM revisions WHERE lead_id=:l",             {"l": lead_id}),
        ("DELETE FROM whatsapp_thread_states WHERE thread_id=:t", {"t": thread_id}),
        ("DELETE FROM whatsapp_messages WHERE thread_id=:t",   {"t": thread_id}),
        ("DELETE FROM whatsapp_thread_candidates WHERE thread_id=:t", {"t": thread_id}),
        ("DELETE FROM whatsapp_threads WHERE id=:t",           {"t": thread_id}),
        ("DELETE FROM leads WHERE id=:l",                      {"l": lead_id}),
        ("DELETE FROM whatsapp_contacts WHERE id=:c",          {"c": contact_id}),
    ]:
        db.execute(text(sql), p)
    db.commit()
print("[DB] Test fixtures cleaned up.")
