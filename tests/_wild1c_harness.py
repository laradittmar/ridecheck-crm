"""WILD1-C — shared harness: the live H-1 posture, scripted models, one burst per turn.

Production code runs unmodified through the real `POST /api/conversation/handle` route
(`_sb2b_harness.run_turn`). Only the network edges and the three model calls are scripted:

* the SEMANTIC interpreter — at its transport seam (`SemanticTurnInterpreter._call_openai`),
  so the real parser, same-turn provider, evidence admission and persistence all run. The
  script maps a burst to the interpreter's JSON payload by a TEST-SIDE key; production
  never sees the key;
* the reply-model interpreter (`_call_openai_inner`) — its body is discarded on every route
  (R6-C4B), so it returns a fixed hostile body that must never reach the customer;
* the bridge generator / verifier — `Bridges`: natural, claim-free sentences, verified clean
  (or, per test, a verifier finding).

Posture = the live H-1 window (read from the running backend, read-only, 2026-11-16):
OUTBOUND / SMART_BOOKING / EVIDENCE_ADMISSION / OUTSTANDING_QUESTION / LOCALITY_SAFETY on;
SEMANTIC_SAME_TURN, SHADOW_UNDERSTAND, RECONCILER_ACCEPTANCE/SCHEDULING_AUTHORITY on;
LAYER_D_REGISTER_AND_CONTINUE, OPERATOR_WHATSAPP_NOTIFY, DIRECT_WEBHOOK, PREFILL off.

WILD1-C-R3 (review WC-04): the live window ALSO runs SHADOW_UNDERSTAND_ASYNC=true with a
6.0 s same-turn timeout — the interpreter runs on the shadow worker and a consumer waits for
it, bounded. The default `cfg()` below stays the SYNCHRONOUS posture (the reviewed WILD1-C
tests are unchanged); `cfg_live_async()` is the live posture, and setting `LIVE_ASYNC = True`
makes `cfg()` return it, which is how the live-posture variants of both WILD1-C suites run
every one of their tests a second time. In the async posture `run()` drains the worker before
the scripted interpreter is unpatched, so no job ever reaches a real provider.
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import pathlib
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(_ROOT / "tests"))

import test_r6_full_engine_prose_corpus as C  # noqa: E402  (shared engine + zone fixture)

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (AiEvent, Lead, ThreadRevision, ViaticosZone,  # noqa: E402
                        WhatsAppBookingOffer, WhatsAppContact, WhatsAppMessage, WhatsAppThread,
                        WhatsAppThreadCandidate, WhatsAppThreadEvidence, WhatsAppThreadState,
                        OperatorNotification, BookingCommandRecord)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services import conversation_engine as CE  # noqa: E402
from app.services import semantic_interpreter as SI  # noqa: E402

H = C.H
_SEQ = itertools.count(1)
FLOW_ID = "28104222025943520"
HOSTILE_BODY = ("Listo, tu turno quedó confirmado para mañana a las 10:00. Aceptamos débito. "
                "La revisión sale $99.999.")
#: H-1's live quote: SUV_4X4_DEPORTIVO base 150.000 (pricing_base.csv) + Berazategui
#: viáticos 90.000 = the $240.000 the customer was shown.
BERAZATEGUI_VIATICOS = 90_000
SLOTS = ["09:00", "10:00", "11:00", "15:00", "16:00"]


#: WILD1-C-R3: when True, `cfg()` is the live asynchronous posture (`cfg_live_async`).
LIVE_ASYNC = False
LIVE_TIMEOUT_SECONDS = 6.0          # the live SEMANTIC_SAME_TURN_TIMEOUT_SECONDS


def cfg_live_async(**over):
    """The live H-1 window posture: interpreter on the shadow worker, 6.0 s bounded wait."""
    return _cfg_sync(**{"shadow_understand_async": True,
                        "semantic_same_turn_timeout_seconds": LIVE_TIMEOUT_SECONDS, **over})


def cfg(**over):
    if LIVE_ASYNC:
        return cfg_live_async(**over)
    return _cfg_sync(**over)


def _cfg_sync(**over):
    base = dataclasses.replace(
        C._cfg(True, False),
        semantic_same_turn_enabled=True, semantic_same_turn_timeout_seconds=5.0,
        shadow_understand_enabled=True, shadow_evidence_path="",
        reconciler_acceptance_authority_enabled=True,
        reconciler_scheduling_authority_enabled=True,
        layer_d_register_and_continue_enabled=False,
        evidence_admission_enabled=True, outstanding_question_enabled=True,
        locality_resolution_safety_enabled=True, operator_whatsapp_notify_enabled=False,
        booking_flow_details_prefill_enabled=False, booking_flow_id=FLOW_ID)
    return dataclasses.replace(base, **over)


def engine():
    eng = C._engine()
    with Session(eng) as db:
        z = db.execute(select(ViaticosZone).where(ViaticosZone.zone_group == "Sur",
                                                  ViaticosZone.zone_detail == "Berazategui")
                       ).scalars().first()
        if z is None:
            db.add(ViaticosZone(zone_group="Sur", zone_detail="Berazategui",
                                viaticos=BERAZATEGUI_VIATICOS))
        else:
            z.viaticos = BERAZATEGUI_VIATICOS
        H.ensure_professional(db)
        db.commit()
    return eng


# ── scripted models ───────────────────────────────────────────────────────────

def payload(*, faq=(), quote=False, inspection=False, accept=None, decline_reason=None,
            vehicle=None, locations=(), day=None, availability=False):
    """The interpreter's JSON payload, in its own documented shape."""
    intents = []
    if inspection:
        intents.append({"kind": "INSPECTION", "value": "PREPURCHASE_INSPECTION",
                        "status": "CONFIRMED"})
    if quote:
        intents.append({"kind": "QUOTE_REQUEST", "value": True, "status": "CONFIRMED"})
    return {
        "service_intents": intents,
        "vehicles": ([{"make": vehicle[0], "model": vehicle[1], "year": vehicle[2],
                       "status": "CONFIRMED", "year_status": "CONFIRMED"}] if vehicle else []),
        "locations": [{"locality": loc, "role": role, "status": "CONFIRMED"}
                      for loc, role in locations],
        "faq_topics": list(faq),
        "acceptance": ({"signal": accept, "status": "CONFIRMED", "reason": "scripted",
                        "decline_reason": decline_reason} if accept else None),
        "scheduling_requests": ([{"priority": "PRIMARY", "day_expression": day,
                                  "flexible_time": True, "rank": 1, "status": "CONFIRMED"}]
                                if day else []),
        "scheduling_intents": ([{"kind": "AVAILABILITY_ENQUIRY", "status": "CONFIRMED"}]
                               if availability else []),
        "corrections": [], "identity": [], "handoff": None, "ambiguities": [],
        "conflicts": [], "notes": [],
    }


class Interpreter:
    """Burst → payload by a test-side key found in the burst. Unknown bursts: empty."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, messages, model):
        # Patched onto the class as a plain callable object: never bound, so no `self` of
        # the interpreter arrives here.
        user = messages[-1]["content"]
        burst = user.split("\n\nCONTEXTO")[0]
        self.calls.append(burst)
        for key, body in self.script:
            if key in burst:
                return json.dumps(body, ensure_ascii=False), {"total_tokens": 1}
        return json.dumps(payload(), ensure_ascii=False), {"total_tokens": 1}


CLEAN = json.dumps({"purely_conversational": True, "contains_operational_claim": False,
                    "findings": [], "uncertain": []})


class Bridges:
    """Claim-free natural bridges, one per generator call; the verifier approves each unless
    told otherwise. `omit` — the generator returns a bridge that answers nothing (the case a
    required claim must survive)."""

    def __init__(self, lines=("¡Hola! Gracias por escribirnos.",), verdicts=None):
        self.lines = list(lines)
        self.verdicts = dict(verdicts or {})
        self.generated = []

    def generate(self, ce, messages, *, timeout):
        line = self.lines[min(len(self.generated), len(self.lines) - 1)]
        self.generated.append(line)
        return json.dumps({"bridge": line}, ensure_ascii=False)

    def verify(self, ce, messages, *, timeout):
        sentence = json.loads(messages[-1]["content"])["sentence"]
        return self.verdicts.get(sentence, CLEAN)


def reply_decision(_messages, body=HOSTILE_BODY):
    return json.dumps({
        "intent": "OTHER", "reply": body, "lead_flag": None, "needs_human": False,
        "deferred_interest": False,
        "vehicle_make_model": None, "vehicle_year": None, "vehicle_location": None,
        "customer_origin": None, "inspectability": None,
        "asks_price": False, "asks_faq": False, "asks_schedule": False,
        "extracted": {"customer_name": None, "zone_detail": None, "preferred_day_iso": None,
                      "preferred_time_str": None, "tipo_vehiculo": None,
                      "vendedor_tipo": None},
        "candidate": {"action": "none"}})


# ── worlds ────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Thread:
    tid: int
    wa: str
    lead_id: int


def new_thread(eng, *, stage="QUALIFYING", flag=None, vehicle=None, zone=None,
               motivo=None) -> Thread:
    """A thread; `vehicle` = (marca, modelo, anio, tipo); `zone` = (group, detail)."""
    n = next(_SEQ)
    with Session(eng) as db:
        c = WhatsAppContact(wa_id=f"54900{87000000 + n:08d}", display_name="WILD1C Fixture")
        lead = Lead(estado="CONSULTA_NUEVA", necesita_humano=False, flag=flag,
                    motivo_perdida=motivo)
        db.add_all([c, lead]); db.flush()
        th = WhatsAppThread(contact_id=c.id, lead_id=lead.id, unread_count=0)
        db.add(th); db.flush()
        focus = None
        if vehicle:
            cand = WhatsAppThreadCandidate(
                thread_id=th.id, marca=vehicle[0], modelo=vehicle[1], anio=vehicle[2],
                tipo_vehiculo=vehicle[3], status="current_focus",
                zone_group=(zone[0] if zone else None), zone_detail=(zone[1] if zone else None),
                source_text=f"{vehicle[0]} {vehicle[1]} {vehicle[2]}")
            db.add(cand); db.flush(); focus = cand.id
        db.add(WhatsAppThreadState(
            thread_id=th.id, last_stage=stage, needs_human=False,
            current_focus_candidate_id=focus,
            home_zone_group=(zone[0] if zone else None),
            home_zone_detail=(zone[1] if zone else None),
            vehicle_clarification_sent=False, location_clarification_sent=False,
            vehicle_fallback_flow_sent=False, location_fallback_flow_sent=False))
        db.commit()
        return Thread(th.id, c.wa_id, lead.id)


def add_outbound(eng, t: Thread, text: str):
    """A delivered outbound row (e.g. the quote the customer already received)."""
    n = next(_SEQ)
    with Session(eng) as db:
        db.add(WhatsAppMessage(thread_id=t.tid, direction="out", message_type="text",
                               wa_message_id=f"wamid.WILD1C.OUT.SEED.{n:07d}", text=text,
                               status="read", automated=True, path_id="CE_TEXT",
                               timestamp=datetime.now(timezone.utc)))
        db.commit()


def burst(eng, t: Thread, texts, *, message_type="text"):
    """Store the inbound rows of one burst and return the event n8n would post."""
    wamids = []
    with Session(eng) as db:
        for text in texts:
            n = next(_SEQ)
            w = f"wamid.WILD1C.IN.{n:07d}"
            wamids.append(w)
            db.add(WhatsAppMessage(thread_id=t.tid, direction="in", wa_message_id=w,
                                   message_type=message_type, text=text, status="received",
                                   timestamp=datetime.now(timezone.utc)))
        db.add(AiEvent(thread_id=t.tid, wa_message_id=wamids[-1], wa_id=t.wa,
                       status="triggered"))
        db.commit()
    return ConversationHandleIn(thread_id=t.tid, wa_message_id=wamids[-1], wa_id=t.wa,
                                message_type=message_type, text=texts[-1],
                                unanswered_recent_user_messages=list(texts))


def run(eng, ev, *, interp: Interpreter, bridges: Bridges | None = None, config=None,
        slots=SLOTS, hook=None, reply_body=HOSTILE_BODY):
    """One production turn. Returns a namespace with out, sent texts, flows, engine."""
    bridges = bridges or Bridges()
    flows = []

    def _hook(ce):
        ce._call_openai_inner = lambda messages: reply_decision(messages, reply_body)
        ce._call_generator = lambda messages, *, timeout: bridges.generate(ce, messages,
                                                                           timeout=timeout)
        ce._call_verifier = lambda messages, *, timeout: bridges.verify(ce, messages,
                                                                        timeout=timeout)
        ce._schedule.list_slots = lambda _in: SimpleNamespace(
            slots=list(slots), business_hours="09:00-18:00")
        if hook is not None:
            hook(ce)

    def _flow(*a, **k):
        flows.append(k.get("body_text"))
        return (f"wamid.WILD1C.FLOW.{next(_SEQ):06d}", 200)

    settings = config or cfg()
    import time as _t
    with mock.patch.object(SI.SemanticTurnInterpreter, "_call_openai", interp), \
            mock.patch("app.services.conversation_engine._send_whatsapp_cloud_flow", _flow), \
            mock.patch("app.settings.get_settings", lambda: settings), \
            H.Meta() as m:
        t0 = _t.monotonic()
        out, ce = H.run_turn(eng, ev, cfg=settings, engine_hook=_hook)
        elapsed = _t.monotonic() - t0
        if getattr(settings, "shadow_understand_async", False) is True:
            # live posture: let the worker finish THIS turn's job while the scripted
            # interpreter is still patched in (a late reading never changes the turn).
            from app.services.shadow_worker import get_worker
            get_worker().drain(timeout=30.0)
        # WILD1-C-R4 (review R3RV-06): the pending-question re-reading runs on its own
        # thread in BOTH postures; join it while the scripted interpreter is still patched
        # in, so no re-reading outlives this turn's patch scope or reaches a real provider.
        join_pending_resolution()
    age(eng, t_id=ev.thread_id)
    return SimpleNamespace(out=out, sent=[tx for _w, tx in m.sent], flows=flows, ce=ce,
                           egress=m.egress, coverage=getattr(ce, "_turn_coverage", None),
                           elapsed=elapsed)


def join_pending_resolution(timeout: float = 30.0) -> int:
    """Join every live `pending-question-resolution` thread; returns how many were joined.
    Raises if one is still alive after `timeout` (a leak the test must see)."""
    import threading
    import time as _t
    joined = 0
    end = _t.monotonic() + timeout
    for th in [t for t in threading.enumerate() if t.name == "pending-question-resolution"]:
        th.join(max(0.0, end - _t.monotonic()))
        if th.is_alive():
            raise AssertionError("pending-question-resolution thread outlived the turn")
        joined += 1
    return joined


def age(eng, *, t_id: int, minutes: int = 3):
    """Move this thread's history `minutes` into the past. H-1's bursts were minutes apart;
    a replay runs in milliseconds, and the outbound flood gate (3 per 60 s) is real."""
    from datetime import timedelta
    from app.models import WhatsAppOutboundDedup
    with Session(eng) as db:
        for m in db.execute(select(WhatsAppMessage).where(
                WhatsAppMessage.thread_id == t_id)).scalars():
            if m.timestamp is not None:
                m.timestamp = m.timestamp - timedelta(minutes=minutes)
        for d in db.execute(select(WhatsAppOutboundDedup)).scalars():
            if d.created_at is not None:
                d.created_at = d.created_at - timedelta(minutes=minutes)
        db.commit()


def snap(eng, t: Thread) -> dict:
    with Session(eng) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == t.tid)).scalar_one()
        lead = db.get(Lead, t.lead_id)
        count = lambda M, *w: db.execute(select(func.count()).select_from(M).where(  # noqa: E731
            M.thread_id == t.tid, *w)).scalar_one()
        decisions = [(r.value, r.reason, r.producer) for r in db.execute(
            select(WhatsAppThreadEvidence).where(
                WhatsAppThreadEvidence.thread_id == t.tid,
                WhatsAppThreadEvidence.claim_type == "quote_decision")
            .order_by(WhatsAppThreadEvidence.id)).scalars()]
        owed = [r.value for r in db.execute(
            select(WhatsAppThreadEvidence).where(
                WhatsAppThreadEvidence.thread_id == t.tid,
                WhatsAppThreadEvidence.claim_type == "required_claim")).scalars()]
        pending = [(r.status, tuple(r.source_message_ids or ()), r.reason) for r in db.execute(
            select(WhatsAppThreadEvidence).where(
                WhatsAppThreadEvidence.thread_id == t.tid,
                WhatsAppThreadEvidence.claim_type == "pending_question")
            .order_by(WhatsAppThreadEvidence.id)).scalars()]
        outs = [(o.path_id, o.message_type, o.text) for o in db.execute(
            select(WhatsAppMessage).where(WhatsAppMessage.thread_id == t.tid,
                                          WhatsAppMessage.direction == "out")
            .order_by(WhatsAppMessage.id)).scalars()]
        return dict(
            stage=st.last_stage, needs_human=st.needs_human, flag=lead.flag,
            motivo=lead.motivo_perdida, necesita_humano=lead.necesita_humano,
            token=st.flow_booking_token, revisions=count(ThreadRevision),
            booked=count(ThreadRevision, ThreadRevision.status == "booked"),
            offers=count(WhatsAppBookingOffer), decisions=decisions, owed=owed,
            pending=pending,
            outbound=outs,
            notices=db.execute(select(func.count()).select_from(OperatorNotification)
                               ).scalar_one(),
            ledger=count(BookingCommandRecord))


def block(base, viaticos, total):
    return CE._rv_render_price_breakdown(base, viaticos, total)
