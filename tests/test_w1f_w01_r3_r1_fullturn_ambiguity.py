"""W1F-W-01 R3 OWNER-REVIEW-R1 — the COMPLETE turn for an ambiguous locality.

The R3 checkpoint proved that an ambiguous locality writes no zone, and it proved that
`_apply_zone_from_text` "sends nothing". The owner review found the gap between those two
statements and the claim that the customer is asked to identify CABA: the R3 suite's
whole-turn group covered unsupported, supported and flag-OFF behaviour, but no test drove
an AMBIGUOUS turn through the router to the customer-visible response.

This module closes that gap by running the REAL turn. `ConversationEngine.handle` is
called, so every deterministic gate, the routing gate, the one clarification decision, the
durable attempt store, the Flow dispatcher and PricingService all execute. Nothing is
simulated except the two things that must never happen in a test:

  * the reply model's HTTP call — patched at `urllib.request.urlopen`;
  * transport — `_send_whatsapp_cloud_text` / `_send_whatsapp_cloud_flow` are captured and
    `OutboundSafetyGate` is a mock, so no `whatsapp_messages` row is written anywhere.

The invariant under test (owner review, requirement 3). An ambiguous locality must:
persist no inspection zone; produce no price, offer or booking; not silently choose CABA;
not restart the conversation; not send the generic uncertain-service greeting; not leave
the customer without a response; and ask one bounded clarification that distinguishes the
supported CABA locality from the unsupported province one.

Two corrections were needed and both are general:

  R1RV-01  the Location Flow's own `zona_general` dropdown is part of the customer's
           answer. The guard read only `localidad`, so "CABA" + "San Nicolás" — the exact
           answer to the question we had just asked — was refused and handed to a person.
           An explicit area selection now settles the ambiguity when it AGREES with the
           matched locality's area, and settles nothing when it disagrees.
  R1RV-02  the ambiguity now DRIVES the clarification instead of relying on the absence of
           a zone. Measured before the fix: "perdón, el auto está en San Nicolás" on a
           thread already quoted at CABA/Flores routed DOMAIN_NOT_RELEVANT and re-quoted
           the old locality as if the customer had said nothing.

Neither correction names a place. R1RV-01 compares two area strings; R1RV-02 reads a
verdict. The only city-specific datum in the system remains the one row in
`locality_text.CROSS_COVERAGE_AMBIGUOUS`, which is a declaration, not a route.
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg
import sqlalchemy.dialects.postgresql.json as _pgj
_pg.JSONB = sqlalchemy.JSON          # type: ignore[attr-defined]
_pgj.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

from sqlalchemy import create_engine, event, text as sql_text   # noqa: E402
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker  # noqa: E402


class Base(DeclarativeBase):
    pass


_ENGINE = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
_SessionLocal = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


@event.listens_for(_ENGINE, "connect")
def _pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


# ── stub app.db BEFORE app.models, exactly as the certified full-turn harnesses do ──
_db_mod = types.ModuleType("app.db")
_db_mod.Base = Base                              # type: ignore[attr-defined]
_db_mod.engine = _ENGINE                         # type: ignore[attr-defined]
_db_mod.SessionLocal = _SessionLocal             # type: ignore[attr-defined]
_db_mod.DATABASE_URL = "sqlite:///:memory:"      # type: ignore[attr-defined]


def _get_db_gen():
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


_db_mod.get_db = _get_db_gen                     # type: ignore[attr-defined]

#: Installed ONLY if nothing has imported `app.db` yet. Several certified harnesses install
#: a stub of their own, and the last writer would otherwise decide which engine every
#: later-imported module sees. Whichever `Base` ends up in play, `create_all` below builds
#: this module's schema on THIS engine, and every session the engine under test uses is one
#: this module handed it — so the choice cannot change what is measured here.
if "app.db" not in sys.modules:
    sys.modules["app.db"] = _db_mod

for _m in ["resend", "anthropic", "openai", "boto3", "botocore", "botocore.exceptions"]:
    if _m not in sys.modules:
        sys.modules[_m] = types.ModuleType(_m)

#: The kill switch is read from the process environment by `_outbound_armed`, so it is
#: armed per turn with `patch.dict` and NEVER set at module scope. A module-level write
#: is a global mutation: it leaks into every test imported afterwards (measured — it
#: broke the M19 outbound-gate suite) and it is itself overwritten by the next module
#: that sets it, which is how this very file first failed inside the full suite while
#: passing alone. Same discipline as `tests/_sb2b_harness.py`.
_ARMED = {"OUTBOUND_ENABLED": "true"}

import app.models  # noqa: F401,E402
from app.models import (                                        # noqa: E402
    Lead, ViaticosZone, WhatsAppContact, WhatsAppMessage, WhatsAppThread,
    WhatsAppThreadCandidate, WhatsAppThreadState,
)
Lead.__table__.metadata.create_all(_ENGINE)

from app.repositories.pricing_repository import PricingRepository      # noqa: E402
from app.schemas.conversation import ConversationHandleIn              # noqa: E402
from app.services import locality_text as LOC                          # noqa: E402
from app.services.conversation_engine import (                         # noqa: E402
    ConversationEngine, _Context, _COVERAGE_RESPONSE, _FALLBACK_WARM_HANDOFF,
    _LOCATION_CLARIFICATION_INTRO, STAGE_QUALIFYING, STAGE_QUOTED,
)


def _said(sent, canonical: str) -> bool:
    """Did the customer get this certified body?

    Compared on collapsed whitespace: `_validate_outbound_text` is the one authority
    on outbound shape and it folds the blank line inside the coverage response, so a
    byte comparison against the constant would assert the validator's absence rather
    than the message's presence."""
    want = " ".join(canonical.split())
    return any(" ".join(s.split()) == want for s in sent)
from app.services.pricing import PricingService                        # noqa: E402
from app.services.schedule import ScheduleService                      # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "viaticos_zones_20261026.tsv"

#: The reply model's payload. It proposes nothing: with the renderer demotion on (the
#: deployed default) the mutations come from the reconciler, so this only supplies prose.
_AI_PAYLOAD = json.dumps({
    "intent": "QUALIFYING", "reply": "Perfecto, lo vemos.", "deferred_interest": False,
    "candidate": {"action": "none"}, "extracted": {}, "lead_flag": None,
    "needs_human": False,
})

_GREETING_MARK = "nos especializamos en revisiones pre-compra"
_seq = [0]


# ── fixtures ────────────────────────────────────────────────────────────────────
def _seed_catalogue(db: Session) -> None:
    if db.query(ViaticosZone).count():
        return
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        zid, grp, det, via = line.split("\t")
        db.add(ViaticosZone(id=int(zid), zone_group=grp, zone_detail=det or None,
                            viaticos=int(via or 0)))
    db.commit()


def _seed_thread(db: Session, *, vehicle: bool, zone=None, tipo="AUTO",
                 flag=None, stage=STAGE_QUALIFYING, flow_outstanding=False):
    _seq[0] += 1
    wa = f"549115336{_seq[0]:04d}"
    contact = WhatsAppContact(wa_id=wa, display_name="R1")
    db.add(contact); db.flush()
    lead = Lead(nombre="R1", telefono=wa, estado="CONSULTA_NUEVA", flag=flag)
    db.add(lead); db.flush()
    thread = WhatsAppThread(contact_id=contact.id, lead_id=lead.id)
    db.add(thread); db.flush()
    cand_id = None
    if vehicle:
        cand = WhatsAppThreadCandidate(
            thread_id=thread.id, marca="Toyota", modelo="Corolla", anio=2024,
            tipo_vehiculo=tipo, status="current_focus",
            zone_group=(zone[0] if zone else None),
            zone_detail=(zone[1] if zone else None),
            created_at=datetime(2026, 10, 26, 9, 0, 0, tzinfo=timezone.utc))
        db.add(cand); db.flush()
        cand_id = cand.id
    state = WhatsAppThreadState(
        thread_id=thread.id, last_stage=stage, current_focus_candidate_id=cand_id,
        home_zone_group=(zone[0] if zone else None),
        home_zone_detail=(zone[1] if zone else None),
        location_fallback_flow_sent=flow_outstanding)
    db.add(state); db.flush()
    db.commit()
    return thread.id, cand_id, wa


def _engine(db: Session, *, safety: bool) -> ConversationEngine:
    s = MagicMock()
    s.openai_api_key = "sk-r1"
    s.openai_chat_model = "gpt-4o-mini"
    s.backend_url = "http://localhost:8000"
    s.whatsapp_flow_id = "FLOW_GENERIC"
    s.whatsapp_vehicle_fallback_flow_id = "FLOW_VEHICLE"
    s.whatsapp_location_fallback_flow_id = "FLOW_LOCATION"
    s.whatsapp_website_flow_id = ""
    s.whatsapp_booking_flow_id = "FLOW_BOOKING"
    s.locality_resolution_safety_enabled = safety
    eng = ConversationEngine.__new__(ConversationEngine)
    eng.db = db
    eng.settings = s
    eng._pricing = PricingService(repository=PricingRepository())
    eng._schedule = ScheduleService(db=db)
    eng._ai_invoked = False
    eng._answer_source = None
    eng._contributing_sources = None
    eng._turn_faq_burst = None
    eng._burst_message_count = 1
    eng._burst_earliest_inbound_db_id = None
    return eng


class _Turn:
    """What the customer got, and what the system did, for one complete turn."""

    def __init__(self, out, sent, flows, pricing, engine, db, thread_id, cand_id):
        self.out, self.sent, self.flows, self.pricing = out, sent, flows, pricing
        self.engine = engine
        db.expire_all()
        st = db.query(WhatsAppThreadState).filter_by(thread_id=thread_id).one()
        cand = (db.query(WhatsAppThreadCandidate).filter_by(id=cand_id).one()
                if cand_id else None)
        th = db.query(WhatsAppThread).filter_by(id=thread_id).one()
        self.thread_zone = (st.home_zone_group, st.home_zone_detail)
        self.cand_zone = ((cand.zone_group, cand.zone_detail) if cand else None)
        self.needs_human = bool(st.needs_human)
        self.lead_needs_human = bool(db.query(Lead).filter_by(id=th.lead_id).one()
                                     .necesita_humano)
        self.stage = st.last_stage
        self.route = getattr(engine, "_turn_clarification_route", None)
        self.reason = getattr(engine, "_turn_clarification_reason", None)
        self.attempts = [tuple(r) for r in db.execute(sql_text(
            "SELECT family, status FROM whatsapp_clarification_attempts "
            "WHERE thread_id=:t ORDER BY id"), {"t": thread_id}).fetchall()]
        self.revisions = db.execute(
            sql_text("SELECT COUNT(*) FROM thread_revisions")).scalar()
        self.semantic_calls = getattr(engine, "_turn_semantic_calls", 0)

    @property
    def responded(self) -> bool:
        return bool(self.sent or self.flows)

    @property
    def all_text(self) -> str:
        return " ".join(self.sent + [f["body"] for f in self.flows])

    def no_zone_anywhere(self) -> bool:
        return (self.thread_zone == (None, None)
                and self.cand_zone in (None, (None, None)))


def _run(db: Session, eng: ConversationEngine, thread_id: int, wa: str,
         text: str, cand_id=None) -> _Turn:
    sent: list[str] = []
    flows: list[dict] = []
    pricing: list[dict] = []

    def _fake_text(*, to_wa_id, text):
        sent.append(text)
        return (f"fake-text-{len(sent)}", {})

    def _fake_flow(**kw):
        flows.append({"flow_id": kw.get("flow_id"), "body": kw.get("body_text"),
                      "screen": kw.get("initial_screen")})
        return (f"fake-flow-{len(flows)}", {})

    _real = eng._pricing.quote

    def _spy(**kw):
        pricing.append({k: kw.get(k) for k in
                        ("tipo_vehiculo", "zone_group", "zone_detail")})
        return _real(**kw)

    eng._pricing = MagicMock(wraps=eng._pricing)
    eng._pricing.quote = _spy

    _seq[0] += 1
    ev = ConversationHandleIn(
        thread_id=thread_id, wa_message_id=f"wamid.R1T{_seq[0]:05d}", wa_id=wa,
        text=text, unanswered_recent_user_messages=[text], recent_user_messages=[text])
    with patch.dict(os.environ, _ARMED), patch("urllib.request.urlopen") as mu:
        mu.return_value.__enter__ = lambda s: s
        mu.return_value.__exit__ = MagicMock()
        mu.return_value.read = lambda: json.dumps(
            {"choices": [{"message": {"content": _AI_PAYLOAD}}]}).encode()
        with patch("app.services.conversation_engine.OutboundSafetyGate") as MG:
            gi, gr = MagicMock(), MagicMock()
            gr.outcome = "allowed"
            gr.message_id = 1
            gi.attempt.return_value = gr
            MG.return_value = gi
            with patch("app.services.conversation_engine._send_whatsapp_cloud_text",
                       side_effect=_fake_text):
                with patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                           side_effect=_fake_flow):
                    with patch("app.services.conversation_engine."
                               "reset_unanswered_alert"):
                        out = eng.handle(ev)
    return _Turn(out, sent, flows, pricing, eng, db, thread_id, cand_id)


def _run_form(db: Session, eng: ConversationEngine, thread_id: int, cand_id,
              flow_data: dict) -> _Turn:
    """Case (d): the Location Fallback Flow submit handler, with real transport capture."""
    sent: list[str] = []
    flows: list[dict] = []
    pricing: list[dict] = []
    th = db.query(WhatsAppThread).filter_by(id=thread_id).one()
    ctx = _Context(
        thread=th,
        contact=db.query(WhatsAppContact).filter_by(id=th.contact_id).one(),
        lead=db.query(Lead).filter_by(id=th.lead_id).one(),
        state=db.query(WhatsAppThreadState).filter_by(thread_id=thread_id).one(),
        candidates=db.query(WhatsAppThreadCandidate).filter_by(
            thread_id=thread_id).all(),
        db_messages=[], inbound_wa_message_id="wamid.R1FORM")
    for attr, val in [("_correlation_id", "r1"), ("_turn_price_quote", None),
                      ("_availability_checked", False), ("_ai_invoked", False),
                      ("_answer_source", None), ("_contributing_sources", None),
                      ("_burst_message_count", 1),
                      ("_burst_earliest_inbound_db_id", None),
                      ("_turn_faq_burst", None), ("_turn_semantic_calls", 0),
                      ("_turn_locality_ambiguity", None)]:
        setattr(eng, attr, val)
    _real = eng._pricing.quote

    def _spy(**kw):
        pricing.append({k: kw.get(k) for k in
                        ("tipo_vehiculo", "zone_group", "zone_detail")})
        return _real(**kw)

    eng._pricing = MagicMock(wraps=eng._pricing)
    eng._pricing.quote = _spy

    def _fake_text(*, to_wa_id, text):
        sent.append(text)
        return (f"fake-text-{len(sent)}", {})

    def _fake_flow(**kw):
        flows.append({"flow_id": kw.get("flow_id"), "body": kw.get("body_text"),
                      "screen": kw.get("initial_screen")})
        return (f"fake-flow-{len(flows)}", {})

    with patch.dict(os.environ, _ARMED), \
            patch("app.services.conversation_engine.OutboundSafetyGate") as MG:
        gi, gr = MagicMock(), MagicMock()
        gr.outcome = "allowed"
        gr.message_id = 1
        gi.attempt.return_value = gr
        MG.return_value = gi
        with patch("app.services.conversation_engine._send_whatsapp_cloud_text",
                   side_effect=_fake_text):
            with patch("app.services.conversation_engine._send_whatsapp_cloud_flow",
                       side_effect=_fake_flow):
                with patch("app.services.conversation_engine.reset_unanswered_alert"):
                    out = eng._process_location_fallback_response(
                        ctx, ctx.state, flow_data)
    db.commit()
    return _Turn(out, sent, flows, pricing, eng, db, thread_id, cand_id)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = _SessionLocal()
        _seed_catalogue(cls.db)

    def _thread(self, **kw):
        return _seed_thread(self.db, **kw)

    def _eng(self, safety=True):
        return _engine(self.db, safety=safety)


# ── R3-J: the ambiguous locality, whole turn ────────────────────────────────────
class TestR3J_AmbiguousWholeTurn(_Base):
    """Owner review requirement 1 (a, b, c) and requirement 3, measured end to end."""

    def test_j01_new_conversation_persists_nothing_and_still_answers(self):
        """Case (a). The Layer F qualifying gate owns this turn and returns before the
        zone block, so the locality is neither written nor acted on. R3's guarantees hold
        — no zone, no price, no booking, a response — but the bounded locality question is
        NOT asked here, and cannot be without the R1 admission change. This is W1FRV-01
        (positional authority), it is identical with the flag off, and it is recorded as a
        measured limitation rather than asserted away."""
        tid, cid, wa = self._thread(vehicle=False)
        t = _run(self.db, self._eng(), tid, wa, "en San Nicolás", cid)
        self.assertTrue(t.no_zone_anywhere())
        self.assertEqual(t.pricing, [])
        self.assertEqual(t.revisions, 0)
        self.assertTrue(t.responded)                 # never silent
        self.assertFalse(t.needs_human)
        self.assertEqual(t.stage, STAGE_QUALIFYING)  # no restart
        self.assertIn(_GREETING_MARK, t.all_text)    # the qualifying gate, as inherited

    def test_j01b_new_conversation_is_identical_with_the_flag_off(self):
        tid, cid, wa = self._thread(vehicle=False)
        off = _run(self.db, self._eng(safety=False), tid, wa, "en San Nicolás", cid)
        tid2, cid2, wa2 = self._thread(vehicle=False)
        on = _run(self.db, self._eng(safety=True), tid2, wa2, "en San Nicolás", cid2)
        self.assertEqual(off.sent, on.sent)
        self.assertEqual(off.thread_zone, on.thread_zone)
        self.assertEqual(off.pricing, on.pricing)

    def test_j02_bare_ambiguous_locality_asks_the_bounded_question(self):
        """Case (b): a known vehicle awaiting its location."""
        tid, cid, wa = self._thread(vehicle=True)
        t = _run(self.db, self._eng(), tid, wa, "en San Nicolás", cid)
        self.assertEqual(t.route, "LOCATION_FLOW_REQUIRED")
        self.assertEqual(t.reason, "LOCATION_UNRESOLVED")
        self.assertEqual(len(t.flows), 1)
        self.assertEqual(t.flows[0]["flow_id"], "FLOW_LOCATION")
        self.assertEqual(t.flows[0]["screen"], "LOCATION_DETAILS")
        self.assertEqual(t.flows[0]["body"], _LOCATION_CLARIFICATION_INTRO)
        self.assertEqual(t.sent, [])                      # one response, not two
        self.assertEqual(t.attempts, [("LOCATION", "DISPATCHED")])  # one bounded attempt
        self.assertTrue(t.no_zone_anywhere())
        self.assertEqual(t.pricing, [])
        self.assertEqual(t.revisions, 0)
        self.assertFalse(t.needs_human)
        self.assertEqual(t.stage, STAGE_QUALIFYING)
        self.assertNotIn(_GREETING_MARK, t.all_text)
        self.assertNotIn("CABA y GBA.\n\nPara una revisión fuera", t.all_text)

    def test_j03_explicit_clause_ambiguous_locality_asks_the_same_question(self):
        """Case (c)."""
        tid, cid, wa = self._thread(vehicle=True)
        t = _run(self.db, self._eng(), tid, wa, "el auto está en San Nicolás", cid)
        self.assertEqual((t.route, t.reason),
                         ("LOCATION_FLOW_REQUIRED", "LOCATION_UNRESOLVED"))
        self.assertEqual(t.attempts, [("LOCATION", "DISPATCHED")])
        self.assertTrue(t.no_zone_anywhere())
        self.assertEqual(t.pricing, [])
        self.assertEqual(t.revisions, 0)

    def test_j04_an_ambiguous_correction_is_asked_about_not_ignored(self):
        """R1RV-02. A zone is already known, so `required_next` is False and relevance has
        to come from the ambiguity itself. Before the correction this turn routed
        DOMAIN_NOT_RELEVANT and re-quoted CABA/Flores as if nothing had been said."""
        tid, cid, wa = self._thread(vehicle=True, zone=("CABA", "Flores"),
                                    flag="PRESUPUESTANDO", stage=STAGE_QUOTED)
        t = _run(self.db, self._eng(), tid, wa,
                 "perdón, el auto está en San Nicolás", cid)
        self.assertEqual((t.route, t.reason),
                         ("LOCATION_FLOW_REQUIRED", "LOCATION_UNRESOLVED"))
        self.assertEqual(len(t.flows), 1)
        self.assertEqual(t.sent, [])                     # no new quote is sent
        self.assertNotIn("$", t.all_text)
        self.assertEqual(t.attempts, [("LOCATION", "DISPATCHED")])
        # Asking does not delete what is stored: the old zone stands until it is answered.
        self.assertEqual(t.cand_zone, ("CABA", "Flores"))
        self.assertEqual(t.thread_zone, ("CABA", "Flores"))
        self.assertEqual(t.stage, STAGE_QUOTED)          # no restart
        self.assertFalse(t.needs_human)

    def test_j05_the_ambiguity_never_silently_becomes_caba(self):
        for text in ("en San Nicolás", "el auto está en San Nicolás",
                     "los autos están en San Nicolás"):
            with self.subTest(text=text):
                tid, cid, wa = self._thread(vehicle=True)
                t = _run(self.db, self._eng(), tid, wa, text, cid)
                self.assertTrue(t.no_zone_anywhere())
                self.assertNotIn("San Nicolás", t.all_text)
                self.assertEqual(t.pricing, [])

    def test_j06_no_model_call_decides_the_ambiguity(self):
        tid, cid, wa = self._thread(vehicle=True)
        t = _run(self.db, self._eng(), tid, wa, "en San Nicolás", cid)
        self.assertEqual(t.semantic_calls, 0)


# ── R3-J2: the disambiguated inputs ─────────────────────────────────────────────
class TestR3J2_Disambiguated(_Base):
    """Owner review requirement 1 (e): the customer who settles it is served."""

    def test_j07_text_naming_caba_resolves_and_quotes(self):
        for text in ("el auto está en San Nicolás, CABA",
                     "el auto está en San Nicolás, Capital",
                     "San Nicolás, CABA"):
            with self.subTest(text=text):
                tid, cid, wa = self._thread(vehicle=True)
                t = _run(self.db, self._eng(), tid, wa, text, cid)
                self.assertEqual(t.cand_zone, ("CABA", "San Nicolás"))
                self.assertEqual(t.flows, [])
                self.assertEqual(t.attempts, [])
                self.assertIn({"tipo_vehiculo": "AUTO", "zone_group": "CABA",
                               "zone_detail": "San Nicolás"}, t.pricing)
                self.assertIn("$140.000", t.all_text)
                self.assertFalse(t.needs_human)

    def test_j08_the_province_reading_is_still_refused(self):
        tid, cid, wa = self._thread(vehicle=True)
        t = _run(self.db, self._eng(), tid, wa,
                 "el auto está en San Nicolás de los Arroyos", cid)
        self.assertTrue(t.no_zone_anywhere())
        self.assertEqual(t.pricing, [])


# ── R3-J3: the form — case (d) ──────────────────────────────────────────────────
class TestR3J3_FormAnswer(_Base):
    """R1RV-01. The dropdown is part of the answer, and a conflicting dropdown is not."""

    def test_j09_area_selection_settles_the_ambiguity(self):
        tid, cid, wa = self._thread(vehicle=True, flow_outstanding=True)
        t = _run_form(self.db, self._eng(), tid, cid,
                      {"zona_general": "CABA", "localidad": "San Nicolás"})
        self.assertEqual(t.thread_zone, ("CABA", "San Nicolás"))
        self.assertFalse(t.needs_human)
        self.assertFalse(_said(t.sent, _FALLBACK_WARM_HANDOFF))
        self.assertIn({"tipo_vehiculo": "AUTO", "zone_group": "CABA",
                       "zone_detail": "San Nicolás"}, t.pricing)

    def test_j10_a_conflicting_area_selection_settles_nothing(self):
        """"Sur" + "San Nicolás" are two answers that cannot both be true. The locality's
        own area is CABA, so the selection does not qualify it — a person owns the
        outcome and no zone is written."""
        tid, cid, wa = self._thread(vehicle=True, flow_outstanding=True)
        t = _run_form(self.db, self._eng(), tid, cid,
                      {"zona_general": "SUR", "localidad": "San Nicolás"})
        self.assertEqual(t.thread_zone, (None, None))
        self.assertTrue(t.needs_human)
        self.assertTrue(t.lead_needs_human)
        self.assertTrue(_said(t.sent, _FALLBACK_WARM_HANDOFF))
        self.assertEqual(t.pricing, [])

    def test_j11_an_unambiguous_form_answer_is_untouched(self):
        tid, cid, wa = self._thread(vehicle=True, flow_outstanding=True)
        t = _run_form(self.db, self._eng(), tid, cid,
                      {"zona_general": "CABA", "localidad": "Flores"})
        self.assertEqual(t.thread_zone, ("CABA", "Flores"))
        self.assertFalse(t.needs_human)

    def test_j12_an_unsupported_form_answer_still_gets_coverage_and_no_zone(self):
        tid, cid, wa = self._thread(vehicle=True, flow_outstanding=True)
        t = _run_form(self.db, self._eng(), tid, cid,
                      {"zona_general": "SUR", "localidad": "Mar del Plata"})
        self.assertEqual(t.thread_zone, (None, None))
        self.assertTrue(t.needs_human)
        self.assertTrue(_said(t.sent, _COVERAGE_RESPONSE))
        self.assertEqual(t.pricing, [])

    def test_j13_the_area_hint_cannot_rescue_an_unsupported_locality(self):
        """Coverage is decided before ambiguity, so no dropdown can admit Mar del Plata."""
        for area in ("CABA", "SUR", "NORTE", "OESTE"):
            with self.subTest(area=area):
                tid, cid, wa = self._thread(vehicle=True, flow_outstanding=True)
                t = _run_form(self.db, self._eng(), tid, cid,
                              {"zona_general": area, "localidad": "Mar del Plata"})
                self.assertEqual(t.thread_zone, (None, None))
                self.assertEqual(t.pricing, [])


# ── R3-J4: the unsupported and unresolved turns — requirement 6 ─────────────────
class TestR3J4_UnsupportedWholeTurn(_Base):

    def test_j14_mar_del_plata_bare_and_explicit(self):
        for text in ("en Mar del Plata", "el auto está en Mar del Plata"):
            with self.subTest(text=text):
                tid, cid, wa = self._thread(vehicle=True)
                t = _run(self.db, self._eng(), tid, wa, text, cid)
                self.assertTrue(t.no_zone_anywhere())
                self.assertEqual(t.pricing, [])
                self.assertEqual(t.revisions, 0)
                self.assertTrue(t.needs_human)
                self.assertTrue(t.lead_needs_human)
                self.assertTrue(_said(t.sent, _COVERAGE_RESPONSE))
                self.assertNotIn("La Plata", t.all_text)

    def test_j15_san_miguel_del_monte_asks_rather_than_quoting_san_miguel(self):
        tid, cid, wa = self._thread(vehicle=True)
        t = _run(self.db, self._eng(), tid, wa, "en San Miguel del Monte", cid)
        self.assertTrue(t.no_zone_anywhere())
        self.assertEqual(t.pricing, [])
        self.assertEqual(t.revisions, 0)
        self.assertTrue(t.responded)
        self.assertNotIn("$", t.all_text)
        self.assertEqual(t.attempts, [("LOCATION", "DISPATCHED")])

    def test_j16_a_supported_locality_is_the_control(self):
        tid, cid, wa = self._thread(vehicle=True)
        t = _run(self.db, self._eng(), tid, wa, "el auto está en Flores", cid)
        self.assertEqual(t.cand_zone, ("CABA", "Flores"))
        self.assertIn("$140.000", t.all_text)
        self.assertEqual(t.flows, [])
        self.assertFalse(t.needs_human)

    def test_j17_an_ambiguous_origin_does_not_suppress_a_supported_vehicle_location(self):
        for text in ("yo soy de San Nicolás pero el auto está en Caballito",
                     "el auto está en Caballito, yo soy de San Nicolás"):
            with self.subTest(text=text):
                tid, cid, wa = self._thread(vehicle=True)
                t = _run(self.db, self._eng(), tid, wa, text, cid)
                self.assertEqual(t.cand_zone, ("CABA", "Caballito"))
                self.assertIn("$140.000", t.all_text)


# ── R3-J5: the flag OFF posture is still the deployed one ───────────────────────
class TestR3J5_FlagOffUnchanged(_Base):

    def test_j18_the_defect_reproduces_with_the_flag_off(self):
        """The rollback is real: with safety off the deployed wrong answers come back."""
        tid, cid, wa = self._thread(vehicle=True)
        t = _run(self.db, self._eng(safety=False), tid, wa, "en San Nicolás", cid)
        self.assertEqual(t.cand_zone, ("CABA", "San Nicolás"))
        self.assertIn("$140.000", t.all_text)
        self.assertEqual(t.flows, [])

    def test_j19_mar_del_plata_still_resolves_to_la_plata_with_the_flag_off(self):
        tid, cid, wa = self._thread(vehicle=True)
        t = _run(self.db, self._eng(safety=False), tid, wa,
                 "el auto está en Mar del Plata", cid)
        self.assertEqual(t.cand_zone, ("Sur", "La Plata"))

    def test_j20_the_form_guard_is_inert_with_the_flag_off(self):
        """R1RV-01 adds no behaviour to the deployed posture: the dropdown is not even
        consulted, because the guard it feeds does not run."""
        tid, cid, wa = self._thread(vehicle=True, flow_outstanding=True)
        t = _run_form(self.db, self._eng(safety=False), tid, cid,
                      {"zona_general": "SUR", "localidad": "San Nicolás"})
        self.assertEqual(t.thread_zone, ("CABA", "San Nicolás"))
        self.assertFalse(t.needs_human)

    def test_j21_the_ambiguity_relevance_is_inert_with_the_flag_off(self):
        tid, cid, wa = self._thread(vehicle=True, zone=("CABA", "Flores"),
                                    flag="PRESUPUESTANDO", stage=STAGE_QUOTED)
        t = _run(self.db, self._eng(safety=False), tid, wa,
                 "perdón, el auto está en San Nicolás", cid)
        self.assertEqual(t.route, "NO_CLARIFICATION_REQUIRED")
        self.assertEqual(t.flows, [])
        self.assertEqual(t.cand_zone, ("CABA", "San Nicolás"))


# ── R3-J6: the two corrections are general, not phrase-specific ─────────────────
class TestR3J6_Structural(_Base):

    def test_j22_the_area_hint_rule_compares_two_areas_and_names_no_place(self):
        self.assertTrue(LOC.disambiguated_by_text("san nicolas", "CABA",
                                                  area_hint="CABA"))
        self.assertTrue(LOC.disambiguated_by_text("san nicolas", "CABA",
                                                  area_hint="caba"))
        self.assertFalse(LOC.disambiguated_by_text("san nicolas", "CABA",
                                                   area_hint="Sur"))
        self.assertFalse(LOC.disambiguated_by_text("san nicolas", "CABA",
                                                   area_hint=None))
        # A hint cannot invent a disambiguation where the locality has no area.
        self.assertFalse(LOC.disambiguated_by_text("san nicolas", None,
                                                   area_hint="CABA"))

    def test_j23_no_place_name_is_executable_outside_the_declaration(self):
        """`locality_text` DECLARES the places; nothing else may name one in code.

        Checked over the AST, so a comment or a docstring citing the measured case is
        allowed — as it is everywhere else in this codebase — while a string literal that
        could participate in a decision is not. This is the structural form of owner
        constraint 7: a new textual pattern may contribute validated evidence, never decide
        whether evidence is retained."""
        import ast

        declared = set(LOC.CROSS_COVERAGE_AMBIGUOUS) | set(LOC.OUTSIDE_COVERAGE_CITIES)
        self.assertIn("san nicolas", declared)
        self.assertIn("mar del plata", declared)
        for name in ("conversation_engine.py", "field_evidence.py"):
            path = BACKEND_DIR / "app" / "services" / name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc is not None:
                        docstrings.add(doc)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                if node.value in docstrings:
                    continue
                lowered = LOC.norm_locality_text(node.value)
                for place in declared:
                    self.assertNotIn(
                        place, lowered,
                        f"{name}:{node.lineno} contains the place name {place!r} in "
                        f"executable code: {node.value[:60]!r}")

    def test_j23b_the_ambiguity_relevance_reads_a_verdict(self):
        src = (BACKEND_DIR / "app" / "services" / "conversation_engine.py").read_text(
            encoding="utf-8")
        self.assertIn("_turn_locality_ambiguity", src)
        self.assertEqual(list(LOC.CROSS_COVERAGE_AMBIGUOUS), ["san nicolas"])

    def test_j24_the_ambiguity_is_per_turn_memory(self):
        """A reused engine must never report a previous turn's verdict as this turn's."""
        tid, cid, wa = self._thread(vehicle=True)
        eng = self._eng()
        t1 = _run(self.db, eng, tid, wa, "en San Nicolás", cid)
        self.assertTrue(getattr(eng, "_turn_locality_ambiguity", None))
        tid2, cid2, wa2 = self._thread(vehicle=True)
        t2 = _run(self.db, eng, tid2, wa2, "el auto está en Flores", cid2)
        self.assertIsNone(getattr(eng, "_turn_locality_ambiguity", None))
        self.assertEqual(t2.cand_zone, ("CABA", "Flores"))
        self.assertEqual(t1.attempts, [("LOCATION", "DISPATCHED")])

    def test_j25_nothing_bookable_exists_after_any_refused_locality(self):
        for text in ("en San Nicolás", "en Mar del Plata", "en San Miguel del Monte"):
            with self.subTest(text=text):
                tid, cid, wa = self._thread(vehicle=True)
                t = _run(self.db, self._eng(), tid, wa, text, cid)
                self.assertEqual(t.revisions, 0)
                self.assertEqual(t.pricing, [])
                self.assertTrue(t.no_zone_anywhere())


if __name__ == "__main__":
    unittest.main(verbosity=2)
