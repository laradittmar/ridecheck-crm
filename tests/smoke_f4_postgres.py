"""WILD-04R-F4 Actual PostgreSQL Smoke

Runs Cases A-D through the real CE against crm_test PostgreSQL.
Creates isolated test rows tagged with SMOKE_WA_PREFIX; cleans them after.
No SQLite. No mocking of the DB layer. Outbound mocked (no WA messages sent).
"""
from __future__ import annotations

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, '/app')

# ── Pull DATABASE_URL from environment (set by Docker) ────────────────────────
DATABASE_URL = os.environ["DATABASE_URL"]

# Stub heavy optional deps before any app import
for _mod in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

os.environ.setdefault("OUTBOUND_ENABLED", "false")

# ── Import app with REAL PostgreSQL engine ─────────────────────────────────────
from sqlalchemy import create_engine, text as sql_text, select
from sqlalchemy.orm import sessionmaker, Session

_engine = create_engine(DATABASE_URL)
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

# Patch app.db BEFORE importing models (models import Base from app.db)
import app.db as _app_db
_app_db.engine = _engine
_app_db.SessionLocal = _SessionLocal

import app.models  # noqa: F401 — ensures tables exist
from app.models import (
    Lead, WhatsAppContact, WhatsAppThread, WhatsAppThreadCandidate,
    WhatsAppThreadState, ViaticosZone,
)
from app.repositories.pricing_repository import PricingRepository
from app.schemas.conversation import ConversationHandleIn
from app.services.conversation_engine import ConversationEngine, _Context
from app.services.pricing import PricingService
from app.services.schedule import ScheduleService

# ── Constants ─────────────────────────────────────────────────────────────────
SMOKE_PREFIX = "54919900F4S"   # unique prefix for test wa_ids; easy to clean

RESULTS: list[tuple[str, bool, str]] = []


def _pass(case: str, detail: str = "") -> None:
    RESULTS.append((case, True, detail))
    print(f"  PASS  {case}: {detail}")


def _fail(case: str, detail: str = "") -> None:
    RESULTS.append((case, False, detail))
    print(f"  FAIL  {case}: {detail}")


# ── Engine factory (real PostgreSQL session) ──────────────────────────────────
def _make_ce(db: Session) -> ConversationEngine:
    settings = MagicMock()
    settings.openai_api_key = "sk-test"
    settings.openai_chat_model = "gpt-4o-mini"
    settings.backend_url = "http://localhost:8000"
    settings.whatsapp_flow_id = ""
    settings.whatsapp_vehicle_fallback_flow_id = ""
    settings.whatsapp_location_fallback_flow_id = ""
    settings.whatsapp_website_flow_id = ""

    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = settings
    eng._pricing = PricingService(repository=PricingRepository())
    eng._schedule = ScheduleService(db=db)
    eng._ai_invoked = False
    eng._answer_source = None
    eng._contributing_sources = None
    eng._faq_reconciliation_burst = None
    return eng


def _run_ce(db, eng, thread_id, wa_id, msg_id, texts, ai_payload):
    ev = ConversationHandleIn(
        thread_id=thread_id,
        wa_message_id=msg_id,
        wa_id=wa_id,
        text=texts[-1],
        unanswered_recent_user_messages=texts,
        recent_user_messages=texts,
    )
    sent: list[str] = []
    _ctr = [0]

    def _fake_send(*, to_wa_id, text):
        sent.append(text)
        _ctr[0] += 1
        return (f"smoke-{_ctr[0]}", {})

    with patch("urllib.request.urlopen") as mock_url:
        mock_url.return_value.__enter__ = lambda s: s
        mock_url.return_value.__exit__ = MagicMock()
        mock_url.return_value.read = lambda: json.dumps(
            {"choices": [{"message": {"content": ai_payload}}]}
        ).encode()
        with patch("app.services.conversation_engine.OutboundSafetyGate") as _MG:
            gi = MagicMock()
            gr = MagicMock(); gr.outcome = "allowed"; gr.message_id = 1
            gi.attempt.return_value = gr
            _MG.return_value = gi
            with patch("app.services.conversation_engine._send_whatsapp_cloud_text",
                       side_effect=_fake_send):
                with patch("app.services.conversation_engine.reset_unanswered_alert"):
                    result = eng.handle(ev)
    return result, sent


def _ensure_viaticos(db: Session) -> None:
    """Ensure required ViaticosZone rows exist (idempotent)."""
    for grp, det, viaticos in [
        ("CABA", "Palermo", 0),
        ("Norte", "Pilar", 50000),
        ("Oeste", "San Miguel", 90000),
    ]:
        exists = db.execute(
            sql_text("SELECT 1 FROM viaticos_zones WHERE zone_group=:g AND zone_detail=:d"),
            {"g": grp, "d": det}
        ).fetchone()
        if not exists:
            db.add(ViaticosZone(zone_group=grp, zone_detail=det, viaticos=viaticos))
    db.commit()


def _setup_thread(
    db: Session,
    wa_suffix: str,
    cand_marca: str, cand_modelo: str, cand_anio: int, cand_tipo: str,
    cand_zone_group: str, cand_zone_detail: str,
    state_zone_group: str, state_zone_detail: str,
    stage: str = "QUOTED",
) -> tuple[int, int, int]:
    """Returns (thread_id, candidate_id, contact_id)."""
    wa_id = SMOKE_PREFIX + wa_suffix
    lead = Lead(nombre=f"SmokeF4-{wa_suffix}", telefono=wa_id, flag="PRESUPUESTANDO")
    db.add(lead); db.flush()
    contact = WhatsAppContact(wa_id=wa_id, display_name=f"SmokeF4-{wa_suffix}")
    db.add(contact); db.flush()
    thread = WhatsAppThread(lead_id=lead.id, contact_id=contact.id)
    db.add(thread); db.flush()
    cand = WhatsAppThreadCandidate(
        thread_id=thread.id,
        marca=cand_marca, modelo=cand_modelo, anio=cand_anio, tipo_vehiculo=cand_tipo,
        zone_group=cand_zone_group, zone_detail=cand_zone_detail,
        status="current_focus",
    )
    db.add(cand); db.flush()
    state = WhatsAppThreadState(
        thread_id=thread.id, last_stage=stage,
        home_zone_group=state_zone_group, home_zone_detail=state_zone_detail,
        current_focus_candidate_id=cand.id,
    )
    db.add(state); db.flush()
    db.commit()
    return thread.id, cand.id, contact.id


def _cleanup(db: Session, wa_suffix: str) -> None:
    wa_id = SMOKE_PREFIX + wa_suffix
    db.execute(sql_text(
        "DELETE FROM whatsapp_messages WHERE thread_id IN "
        "(SELECT t.id FROM whatsapp_threads t JOIN whatsapp_contacts c ON c.id=t.contact_id WHERE c.wa_id=:w)"
    ), {"w": wa_id})
    db.execute(sql_text(
        "DELETE FROM whatsapp_thread_candidates WHERE thread_id IN "
        "(SELECT t.id FROM whatsapp_threads t JOIN whatsapp_contacts c ON c.id=t.contact_id WHERE c.wa_id=:w)"
    ), {"w": wa_id})
    db.execute(sql_text(
        "DELETE FROM whatsapp_thread_states WHERE thread_id IN "
        "(SELECT t.id FROM whatsapp_threads t JOIN whatsapp_contacts c ON c.id=t.contact_id WHERE c.wa_id=:w)"
    ), {"w": wa_id})
    db.execute(sql_text(
        "DELETE FROM whatsapp_threads WHERE contact_id IN "
        "(SELECT id FROM whatsapp_contacts WHERE wa_id=:w)"
    ), {"w": wa_id})
    db.execute(sql_text("DELETE FROM whatsapp_contacts WHERE wa_id=:w"), {"w": wa_id})
    db.execute(sql_text(
        "DELETE FROM leads WHERE telefono=:w"
    ), {"w": wa_id})
    db.commit()


# ══════════════════════════════════════════════════════════════════════════════
# CASE A — Active candidate location authority
# ══════════════════════════════════════════════════════════════════════════════

def run_case_a() -> dict:
    print("\n--- CASE A: Active candidate location authority ---")
    db = _SessionLocal()
    _ensure_viaticos(db)
    suffix = "A1"

    # Fresh state for this case
    _cleanup(db, suffix)

    thread_id, cand_id, _ = _setup_thread(
        db, suffix,
        "Ford", "Focus", 2019, "AUTO",
        cand_zone_group="CABA", cand_zone_detail="Palermo",
        state_zone_group="Oeste", state_zone_detail="San Miguel",
    )

    eng = _make_ce(db)

    # Direct accessor check (pre-turn)
    db.expire_all()
    thread = db.get(WhatsAppThread, thread_id)
    state_obj = db.execute(select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread_id)).scalar_one()
    cands = list(db.execute(select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread_id)).scalars())
    ctx = _Context(thread=thread, contact=db.get(WhatsAppContact, thread.contact_id),
                   lead=db.get(Lead, thread.lead_id), state=state_obj, candidates=cands, db_messages=[])
    acc_grp, acc_det = eng._get_active_inspection_location(ctx, state_obj)
    quote = eng._compute_price_quote(ctx, state_obj)

    print(f"  _get_active_inspection_location → {acc_grp!r} / {acc_det!r}")
    print(f"  _compute_price_quote zone → {quote.zone_group!r} / {quote.zone_detail!r}, total={quote.precio_total}")

    # Run CE turn (price restatement path via "cuánto sale")
    ai_payload = json.dumps({
        "intent": "PRESUPUESTO_ENVIADO", "reply": "Acá va tu presupuesto.",
        "deferred_interest": False, "candidate": {"action": "none"},
        "extracted": {}, "lead_flag": None, "needs_human": False,
    })
    _, sent = _run_ce(db, eng, thread_id, SMOKE_PREFIX + suffix,
                      "smokeA-01", ["¿Cuánto sale la revisión?"], ai_payload)

    full_reply = " ".join(sent)
    print(f"  Reply: {full_reply!r}")

    result = {
        "acc_grp": acc_grp, "acc_det": acc_det,
        "pricing_grp": quote.zone_group if quote else None,
        "pricing_det": quote.zone_detail if quote else None,
        "reply": full_reply,
        "cand_id": cand_id,
    }

    _cleanup(db, suffix)
    db.close()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CASE B — Location correction
# ══════════════════════════════════════════════════════════════════════════════

def run_case_b() -> dict:
    print("\n--- CASE B: Location correction ---")
    db = _SessionLocal()
    _ensure_viaticos(db)
    suffix = "B1"

    _cleanup(db, suffix)

    thread_id, cand_id, _ = _setup_thread(
        db, suffix,
        "Ford", "Focus", 2019, "AUTO",
        cand_zone_group="CABA", cand_zone_detail="Palermo",
        state_zone_group="CABA", state_zone_detail="Palermo",
    )

    eng = _make_ce(db)

    # AI says candidate zone changed to Norte/Pilar
    ai_payload = json.dumps({
        "intent": "QUALIFYING", "reply": "Gracias por la corrección.",
        "deferred_interest": False,
        "candidate": {"action": "update", "zone_group": "Norte", "zone_detail": "Pilar"},
        "extracted": {}, "lead_flag": None, "needs_human": False,
    })
    _, sent = _run_ce(db, eng, thread_id, SMOKE_PREFIX + suffix,
                      "smokeB-01", ["Perdón, está en Pilar, no Palermo."], ai_payload)

    full_reply = " ".join(sent)
    print(f"  Reply: {full_reply!r}")

    # Check PostgreSQL state
    db.expire_all()
    cand = db.get(WhatsAppThreadCandidate, cand_id)
    state_obj = db.execute(select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread_id)).scalar_one()
    print(f"  Candidate ({cand_id}): zone_group={cand.zone_group!r}, zone_detail={cand.zone_detail!r}")

    result = {
        "cand_id_preserved": cand_id,
        "cand_zone_group": cand.zone_group,
        "cand_zone_detail": cand.zone_detail,
        "reply": full_reply,
    }

    _cleanup(db, suffix)
    db.close()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CASE C — Vehicle + location replacement
# ══════════════════════════════════════════════════════════════════════════════

def run_case_c() -> dict:
    print("\n--- CASE C: Vehicle + location replacement ---")
    db = _SessionLocal()
    _ensure_viaticos(db)
    suffix = "C1"

    _cleanup(db, suffix)

    # Precondition: Peugeot/San Miguel, QUOTED
    thread_id, peugeot_id, _ = _setup_thread(
        db, suffix,
        "Peugeot", "2008", 2014, "AUTO",
        cand_zone_group="Oeste", cand_zone_detail="San Miguel",
        state_zone_group="Oeste", state_zone_detail="San Miguel",
    )

    eng = _make_ce(db)

    # AI: create new Focus/Palermo candidate as current_focus (QUALIFYING intent)
    # F3-T2 fires: zone changed San Miguel→Palermo while QUOTED → reset to QUALIFYING
    # Deterministic override re-prices Focus+CABA/Palermo → 140k → QUOTED
    ai_payload = json.dumps({
        "intent": "QUALIFYING", "reply": "Anotado el Focus 2019.",
        "deferred_interest": False,
        "candidate": {
            "action": "create",
            "marca": "Ford", "modelo": "Focus", "anio": 2019, "tipo_vehiculo": "AUTO",
            "zone_group": "CABA", "zone_detail": "Palermo",
            "status": "current_focus",
        },
        "extracted": {}, "lead_flag": None, "needs_human": False,
    })
    _, sent = _run_ce(db, eng, thread_id, SMOKE_PREFIX + suffix,
                      "smokeC-01",
                      ["Al final ese auto se cayó. Encontré un Focus 2019 en Palermo."],
                      ai_payload)

    full_reply = " ".join(sent)
    print(f"  Reply: {full_reply!r}")

    db.expire_all()
    peugeot = db.get(WhatsAppThreadCandidate, peugeot_id)
    all_cands = list(db.execute(
        select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread_id)
    ).scalars())
    state_obj = db.execute(select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread_id)).scalar_one()
    focus_cands = [c for c in all_cands if c.marca == "Ford" and c.modelo == "Focus"]
    focus = focus_cands[0] if focus_cands else None

    print(f"  Peugeot ({peugeot_id}): zone={peugeot.zone_group!r}/{peugeot.zone_detail!r}, status={peugeot.status!r}")
    if focus:
        print(f"  Focus ({focus.id}): zone={focus.zone_group!r}/{focus.zone_detail!r}, status={focus.status!r}")
    print(f"  state.current_focus_candidate_id={state_obj.current_focus_candidate_id}")

    # Check pricing using new focus
    cands = list(db.execute(select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread_id)).scalars())
    thread = db.get(WhatsAppThread, thread_id)
    ctx = _Context(thread=thread, contact=db.get(WhatsAppContact, thread.contact_id),
                   lead=db.get(Lead, thread.lead_id), state=state_obj, candidates=cands, db_messages=[])
    quote = eng._compute_price_quote(ctx, state_obj)
    acc_grp, acc_det = eng._get_active_inspection_location(ctx, state_obj)
    print(f"  _get_active_inspection_location → {acc_grp!r}/{acc_det!r}")
    print(f"  Pricing zone → {quote.zone_group if quote else None!r}/{quote.zone_detail if quote else None!r}")

    result = {
        "peugeot_preserved": peugeot is not None,
        "peugeot_zone": f"{peugeot.zone_group}/{peugeot.zone_detail}" if peugeot else None,
        "focus_created": focus is not None,
        "focus_id": focus.id if focus else None,
        "focus_zone": f"{focus.zone_group}/{focus.zone_detail}" if focus else None,
        "current_focus_id": state_obj.current_focus_candidate_id,
        "pricing_grp": quote.zone_group if quote else None,
        "reply": full_reply,
    }

    _cleanup(db, suffix)
    db.close()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CASE D — Switch back to prior candidate
# ══════════════════════════════════════════════════════════════════════════════

def run_case_d() -> dict:
    print("\n--- CASE D: Switch back to Peugeot ---")
    db = _SessionLocal()
    _ensure_viaticos(db)
    suffix = "D1"

    _cleanup(db, suffix)

    # Set up: Focus/Palermo as current_focus, Peugeot/San Miguel as inactive
    wa_id = SMOKE_PREFIX + suffix
    lead = Lead(nombre=f"SmokeF4-{suffix}", telefono=wa_id, flag="PRESUPUESTANDO")
    db.add(lead); db.flush()
    contact = WhatsAppContact(wa_id=wa_id, display_name=f"SmokeF4-{suffix}")
    db.add(contact); db.flush()
    thread = WhatsAppThread(lead_id=lead.id, contact_id=contact.id)
    db.add(thread); db.flush()

    focus_cand = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Ford", modelo="Focus", anio=2019,
        tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="Palermo",
        status="current_focus",
    )
    db.add(focus_cand); db.flush()

    peugeot_cand = WhatsAppThreadCandidate(
        thread_id=thread.id, marca="Peugeot", modelo="2008", anio=2014,
        tipo_vehiculo="AUTO", zone_group="Oeste", zone_detail="San Miguel",
        status="inactive",
    )
    db.add(peugeot_cand); db.flush()

    state = WhatsAppThreadState(
        thread_id=thread.id, last_stage="QUOTED",
        home_zone_group="CABA", home_zone_detail="Palermo",
        current_focus_candidate_id=focus_cand.id,
    )
    db.add(state); db.flush()
    db.commit()

    thread_id = thread.id
    peugeot_id = peugeot_cand.id
    focus_id = focus_cand.id

    eng = _make_ce(db)

    # AI re-focuses on Peugeot (dedup: same marca+modelo → update existing)
    ai_payload = json.dumps({
        "intent": "PRESUPUESTO_ENVIADO", "reply": "Acá va el presupuesto del Peugeot.",
        "deferred_interest": False,
        "candidate": {
            "action": "create",
            "marca": "Peugeot", "modelo": "2008", "anio": 2014, "tipo_vehiculo": "AUTO",
            "zone_group": "Oeste", "zone_detail": "San Miguel",
        },
        "extracted": {}, "lead_flag": None, "needs_human": False,
    })
    _, sent = _run_ce(db, eng, thread_id, wa_id,
                      "smokeD-01", ["Al final volvamos con el Peugeot."], ai_payload)

    full_reply = " ".join(sent)
    print(f"  Reply: {full_reply!r}")

    db.expire_all()
    state_obj = db.execute(select(WhatsAppThreadState).where(WhatsAppThreadState.thread_id == thread_id)).scalar_one()
    cands = list(db.execute(select(WhatsAppThreadCandidate).where(WhatsAppThreadCandidate.thread_id == thread_id)).scalars())
    thread_obj = db.get(WhatsAppThread, thread_id)
    ctx = _Context(thread=thread_obj, contact=db.get(WhatsAppContact, thread_obj.contact_id),
                   lead=db.get(Lead, thread_obj.lead_id), state=state_obj, candidates=cands, db_messages=[])
    acc_grp, acc_det = eng._get_active_inspection_location(ctx, state_obj)
    focus_now = eng._focus_candidate(ctx)
    print(f"  current_focus_candidate_id={state_obj.current_focus_candidate_id}")
    print(f"  focus_candidate={focus_now.marca!r} {focus_now.modelo!r} {focus_now.zone_group!r}/{focus_now.zone_detail!r}")
    print(f"  _get_active_inspection_location → {acc_grp!r}/{acc_det!r}")

    result = {
        "current_focus_id": state_obj.current_focus_candidate_id,
        "focus_marca": focus_now.marca if focus_now else None,
        "acc_grp": acc_grp, "acc_det": acc_det,
        "reply": full_reply,
    }

    _cleanup(db, suffix)
    db.close()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("WILD-04R-F4 ACTUAL POSTGRESQL SMOKE")
    print("=" * 60)

    # ── CASE A ──────────────────────────────────────────────────────────────
    ra = run_case_a()

    a_acc_ok = ra["acc_grp"] == "CABA" and ra["acc_det"] == "Palermo"
    a_price_ok = ra["pricing_grp"] == "CABA"
    a_reply_palermo = "Palermo" in ra["reply"]
    a_reply_no_san = "San Miguel" not in ra["reply"]

    if all([a_acc_ok, a_price_ok, a_reply_palermo, a_reply_no_san]):
        _pass("A", f"accessor=CABA/Palermo, pricing=CABA, reply has Palermo, no San Miguel")
    else:
        issues = []
        if not a_acc_ok: issues.append(f"accessor={ra['acc_grp']}/{ra['acc_det']}")
        if not a_price_ok: issues.append(f"pricing={ra['pricing_grp']}")
        if not a_reply_palermo: issues.append("reply missing Palermo")
        if not a_reply_no_san: issues.append("reply has San Miguel")
        _fail("A", "; ".join(issues))

    # ── CASE B ──────────────────────────────────────────────────────────────
    rb = run_case_b()

    b_zone_ok = rb["cand_zone_group"] == "Norte" and rb["cand_zone_detail"] == "Pilar"
    b_reply_pilar = "Pilar" in rb["reply"]
    b_reply_no_palermo = "Palermo" not in rb["reply"]

    if all([b_zone_ok, b_reply_pilar, b_reply_no_palermo]):
        _pass("B", f"cand_id={rb['cand_id_preserved']} preserved, zone=Norte/Pilar, reply has Pilar, no Palermo")
    else:
        issues = []
        if not b_zone_ok: issues.append(f"cand_zone={rb['cand_zone_group']}/{rb['cand_zone_detail']}")
        if not b_reply_pilar: issues.append("reply missing Pilar")
        if not b_reply_no_palermo: issues.append("reply has Palermo")
        _fail("B", "; ".join(issues))

    # ── CASE C ──────────────────────────────────────────────────────────────
    rc = run_case_c()

    c_peugeot_preserved = rc["peugeot_preserved"]
    c_focus_created = rc["focus_created"]
    c_focus_zone_ok = rc["focus_zone"] == "CABA/Palermo" if rc["focus_zone"] else False
    c_peugeot_zone_ok = rc["peugeot_zone"] == "Oeste/San Miguel"
    c_pricing_ok = rc["pricing_grp"] == "CABA"
    c_reply_palermo = "Palermo" in rc["reply"]
    c_reply_no_san = "San Miguel" not in rc["reply"]

    if all([c_peugeot_preserved, c_focus_created, c_focus_zone_ok,
            c_peugeot_zone_ok, c_pricing_ok, c_reply_palermo, c_reply_no_san]):
        _pass("C", f"Peugeot preserved, Focus({rc['focus_id']}) CABA/Palermo, pricing CABA, reply Palermo, no San Miguel leak")
    else:
        issues = []
        if not c_peugeot_preserved: issues.append("Peugeot gone")
        if not c_focus_created: issues.append("Focus not created")
        if not c_focus_zone_ok: issues.append(f"Focus zone={rc['focus_zone']}")
        if not c_peugeot_zone_ok: issues.append(f"Peugeot zone={rc['peugeot_zone']}")
        if not c_pricing_ok: issues.append(f"pricing={rc['pricing_grp']}")
        if not c_reply_palermo: issues.append("reply missing Palermo")
        if not c_reply_no_san: issues.append("reply has San Miguel")
        _fail("C", "; ".join(issues))

    # ── CASE D ──────────────────────────────────────────────────────────────
    rd = run_case_d()

    d_focus_peugeot = rd["focus_marca"] == "Peugeot"
    d_acc_san_miguel = rd["acc_grp"] == "Oeste" and rd["acc_det"] == "San Miguel"
    d_reply_san_miguel = "San Miguel" in rd["reply"]
    d_reply_no_palermo = "Palermo" not in rd["reply"]

    if all([d_focus_peugeot, d_acc_san_miguel, d_reply_san_miguel, d_reply_no_palermo]):
        _pass("D", f"Peugeot is focus, accessor=Oeste/San Miguel, reply has San Miguel, no Palermo")
    else:
        issues = []
        if not d_focus_peugeot: issues.append(f"focus={rd['focus_marca']}")
        if not d_acc_san_miguel: issues.append(f"accessor={rd['acc_grp']}/{rd['acc_det']}")
        if not d_reply_san_miguel: issues.append("reply missing San Miguel")
        if not d_reply_no_palermo: issues.append("reply has Palermo")
        _fail("D", "; ".join(issues))

    # ── SUMMARY ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"RESULT: {passed}/{total} PASS")
    for case, ok, detail in RESULTS:
        print(f"  {'PASS' if ok else 'FAIL'}  Case {case}: {detail}")
    print("=" * 60)

    return all(ok for _, ok, _ in RESULTS)


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
