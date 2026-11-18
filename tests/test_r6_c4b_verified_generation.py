"""R6-C4B-R1 — claim-free natural bridge + deterministic canonical fact blocks.

Owner decision (R6-C4B-R1): a customer message is (1) an AI-generated natural BRIDGE that
contains no operational claim, (2) the deterministic canonical fact block, (3) the
deterministic canonical next action / Meta Booking Flow CTA. The Meta Booking Flow owns slot
selection, confirmation, booking persistence and the transition to BOOKED; before persisted
Flow success the customer is NOT booked and nothing may say or imply otherwise.

These tests use SCRIPTED generator / verifier stubs (never the shared historical replay,
never a prose parser): a scripted verifier reports exactly the findings the test says the
bridge contains, so every verdict below is the pipeline's own policy.

U*  pure units: bridge format, strict parsers, verdict policy, provenance type, pipeline
    policy, budget, input isolation (no engine, no DB).
P*  the full production path (CE → boundary → canonical renderer → bridge generator →
    bridge verifier → defence in depth → composition at the send boundary →
    OutboundSafetyGate → Meta recorder), four launch postures where it matters.
S*  structure: one factory, composition after validation, no state from a bridge.
"""
from __future__ import annotations

import ast
import json
import logging
import pathlib
import sys
import time as _time
import types
import unittest
from datetime import date, datetime, timezone
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "backend"), str(_ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import test_r6_full_engine_prose_corpus as C  # noqa: E402  (the shared harness module)
import test_r6_c4a_transactional_provenance as A  # noqa: E402  (seed / world helpers)
import _c4b_model_fakes as FAKES  # noqa: E402

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (AiEvent, Lead, ThreadRevision, WhatsAppBookingOffer,  # noqa: E402
                        WhatsAppMessage, WhatsAppOutstandingQuestion, WhatsAppThread,
                        WhatsAppThreadState)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services import conversation_engine as CE_MOD  # noqa: E402
from app.services import response_validator as RV  # noqa: E402
from app.services import verified_generation as VG  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402
from app.services.verified_generation import OperationalCategory as OC  # noqa: E402

H = C.H
BACKEND = _ROOT / "backend" / "app"
CE_SRC = (BACKEND / "services" / "conversation_engine.py").read_text(encoding="utf-8")
VG_SRC = (BACKEND / "services" / "verified_generation.py").read_text(encoding="utf-8")
INTERPRETER_BODY = "CUERPO DEL INTERPRETE: te esperamos el lunes 23 a las 10:00, ya quedó."
FLOW_CTA = CE_MOD._TX_FLOW_CTA_TEXT
#: pre-booking confirmations (owner list) and novel equivalents not copied from it
CONFIRMATIONS = (
    "Te esperamos el lunes 16 a las 10:00.",
    "Nos vemos el lunes 16 a las 10:00.",
    "Listo, quedó reservado.",
    "Ya te agendé.",
    "Tu turno está confirmado.",
    "Genial, entonces ya contamos con vos para esa visita.",
    "Perfecto, quedamos así entonces, ahí estaremos.",
)
CONFIRMATION_MARKERS = ("te esperamos", "nos vemos", "reservad", "agend", "confirmado",
                        "contamos con vos", "ahí estaremos", "quedamos así")


# ── scripted model stubs ──────────────────────────────────────────────────────

def verdict(*findings, unc=(), pure=None, contains=None):
    """A verifier JSON verdict: findings = (category, span) pairs."""
    f = [{"category": c.value if isinstance(c, OC) else c, "span": s} for c, s in findings]
    return json.dumps({
        "purely_conversational": (not f and not unc) if pure is None else pure,
        "contains_operational_claim": bool(f) if contains is None else contains,
        "findings": f, "uncertain": list(unc)}, ensure_ascii=False)


CLEAN = verdict()


class Script:
    """A deterministic bridge generator + an INDEPENDENT bridge verifier.

    gens: per-attempt items — a bridge string (wrapped as `{"bridge": …}`), an Exception
          instance to raise, or `("RAW", text)` to return `text` verbatim.
    verdicts: bridge → a verdict JSON string (see `verdict`) or an Exception. A bridge not
              listed is reported as purely conversational.
    """

    def __init__(self, gens, verdicts=None):
        self.gens, self.verdicts = list(gens), dict(verdicts or {})
        self.gen_inputs, self.ver_inputs, self.timeouts = [], [], []

    def generate(self, ce, messages, *, timeout):
        self.timeouts.append(("generator", timeout))
        self.gen_inputs.append(messages)
        item = self.gens[min(len(self.gen_inputs) - 1, len(self.gens) - 1)]
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, tuple) and item[0] == "RAW":
            return item[1]
        return json.dumps({"bridge": item}, ensure_ascii=False)

    def verify(self, ce, messages, *, timeout):
        self.timeouts.append(("verifier", timeout))
        self.ver_inputs.append(messages)
        sentence = json.loads(messages[-1]["content"])["sentence"]
        v = self.verdicts.get(sentence, CLEAN)
        if isinstance(v, BaseException):
            raise v
        return v

    def patches(self):
        s = self
        return (mock.patch.object(ConversationEngine, "_call_generator",
                                  lambda ce, messages, timeout: s.generate(ce, messages,
                                                                           timeout=timeout)),
                mock.patch.object(ConversationEngine, "_call_verifier",
                                  lambda ce, messages, timeout: s.verify(ce, messages,
                                                                         timeout=timeout)))


def vturn(eng, tid, wa, customer, script, sb, direct, *, body=INTERPRETER_BODY,
          now=A.NOON_WED):
    n = next(C._SEQ)
    wamid = f"wamid.R6C4BR1.IN.{n:07d}"
    with Session(eng) as db:
        db.add(WhatsAppMessage(thread_id=tid, direction="in", wa_message_id=wamid,
                               message_type="text", text=customer, status="received",
                               timestamp=datetime.now(timezone.utc)))
        db.add(AiEvent(thread_id=tid, wa_message_id=wamid, wa_id=wa, status="triggered"))
        db.commit()
    ev = ConversationHandleIn(thread_id=tid, wa_message_id=wamid, wa_id=wa,
                              message_type="text", text=customer)
    flows = []

    def hook(ce):
        ce._call_openai_inner = lambda messages: A._decision(body)

    def flow(*a, **k):
        flows.append(1)
        return (f"wamid.R6C4BR1.FLOW.{next(C._FSEQ):06d}", 200)
    p1, p2 = script.patches()
    t0 = _time.perf_counter()
    with p1, p2, mock.patch.object(RV, "_utcnow", lambda: now), \
            mock.patch("app.services.conversation_engine._send_whatsapp_cloud_flow", flow), \
            H.Meta() as m:
        out, ce = H.run_turn(eng, ev, cfg=C._cfg(sb, direct), engine_hook=hook)
    wall_ms = (_time.perf_counter() - t0) * 1000
    with Session(eng) as db:
        st = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == tid)).scalar_one()
        lead = db.get(Lead, db.get(WhatsAppThread, tid).lead_id)
        oqs = db.execute(select(WhatsAppOutstandingQuestion).where(
            WhatsAppOutstandingQuestion.thread_id == tid)).scalars().all()
        env = getattr(ce, "_turn_outbound_envelope", None)
        count = lambda Mdl: db.execute(select(func.count()).select_from(Mdl).where(  # noqa: E731
            Mdl.thread_id == tid)).scalar_one()
        booked = db.execute(select(func.count()).select_from(ThreadRevision).where(
            ThreadRevision.thread_id == tid, ThreadRevision.status == "booked")).scalar_one()
        vg = getattr(ce, "_vg_outcome", None)
        return dict(
            action=getattr(out, "action", None),
            route=getattr(getattr(ce, "_turn_route", None), "name", None),
            vg=(vg.summary() if vg is not None else None),
            gen_calls=len(script.gen_inputs), ver_calls=len(script.ver_inputs),
            sent=[t for _w, t in m.sent], flows=len(flows),
            bridge=getattr(ce, "_turn_bridge_sent", None),
            needs_human=st.needs_human, stage=st.last_stage, flag=lead.flag,
            preferred_time=st.preferred_time, preferred_day=st.preferred_day,
            token=st.flow_booking_token,
            oq=[(q.question_kind, q.asked_by) for q in oqs],
            revisions=count(ThreadRevision), booked=booked, offers=count(WhatsAppBookingOffer),
            provenance=getattr(getattr(env, "provenance", None), "name", None),
            answer_source=getattr(out, "answer_source", None), wall_ms=wall_ms,
            rejected=list(getattr(ce, "_vg_rejected_texts", []) or []))


def canonical_of(r):
    """The delivered message minus the bridge segment."""
    (text,) = r["sent"]
    if r["bridge"]:
        prefix = r["bridge"] + VG.BRIDGE_SEPARATOR
        assert text.startswith(prefix), (text, r["bridge"])
        return text[len(prefix):]
    return text


# ── worlds (canonical state, seeded) ──────────────────────────────────────────
#: route label → (seed kwargs, a customer message that reaches the reply model there)
WORLDS = {
    "QUALIFYING": (dict(stage="QUALIFYING", flag="PRESUPUESTANDO", zone=False),
                   "Dale, gracias por responder"),
    "GENERAL_INFORMATION": (dict(stage="QUALIFYING", flag="PRESUPUESTANDO", zone=False),
                            "Hola, ¿cómo funciona el servicio?"),
    "QUOTATION": (dict(), A._THINK),
    "SCHEDULING": (dict(stage="SCHEDULING", flag="ACEPTADO", date_iso=None), A._THINK),
    # a scheduling question on the FAQ route of a thread with a CURRENT ScheduleService
    # evaluation (Mon 16: 10:00 / 11:00 / 15:00 / 15:30), not booked, no Flow outstanding
    "EVALUATED": (dict(stage="SCHEDULING", flag="ACEPTADO"),
                  "Consulta, ¿hay lugar el lunes 16 a las 10?"),
}
ROUTE_OF = {"QUALIFYING": "QUALIFYING", "GENERAL_INFORMATION": "GENERAL_INFORMATION",
            "QUOTATION": "QUOTATION", "SCHEDULING": "SCHEDULING", "EVALUATED": "SCHEDULING"}
NATURAL = {"QUALIFYING": "Perfecto, gracias por la info.",
           "GENERAL_INFORMATION": "Claro, te cuento cómo seguimos.",
           "QUOTATION": "Dale, sin apuro.",
           "SCHEDULING": "Buenísimo, veamos.",
           "EVALUATED": "Perfecto, te muestro las opciones disponibles."}


class _Engine(unittest.TestCase):
    eng = None

    @classmethod
    def setUpClass(cls):
        cls.eng = C._engine()

    def world(self, label):
        kw, customer = WORLDS[label]
        tid, wa = A._seed(self.eng, **kw)
        return tid, wa, customer

    def canonical(self, label, sb=False, direct=False):
        """What the canonical-only message of a world is (no bridge: generator fails)."""
        tid, wa, customer = self.world(label)
        r = vturn(self.eng, tid, wa, customer, Script([RuntimeError("down")]), sb, direct)
        self.assertIsNone(r["bridge"], r)
        (text,) = r["sent"]
        return text

    def each_posture(self, fn):
        for sb, direct in C.POSTURES:
            with self.subTest(sb=sb, direct=direct):
                fn(sb, direct)

    def assert_not_booked_and_no_state_from_prose(self, r):
        self.assertIsNone(r["preferred_time"], r)
        self.assertIsNone(r["preferred_day"], r)
        self.assertEqual(r["revisions"], 0, r)
        self.assertEqual(r["booked"], 0, r)
        self.assertEqual(r["offers"], 0, r)
        self.assertNotEqual(r["stage"], "BOOKED", r)
        for kind, by in r["oq"]:
            self.assertNotEqual(by, "REPLY_MODEL", r)   # every question is canonical now

    def assert_no_confirmation(self, r):
        for t in r["sent"]:
            low = t.lower()
            for marker in CONFIRMATION_MARKERS:
                self.assertNotIn(marker, low, r)

    def assert_interpreter_body_never_sent(self, r):
        for t in r["sent"]:
            self.assertNotIn("CUERPO DEL INTERPRETE", t, r)


# ═════════════════════════════════════════════════════════════════════════════
# U — pure units
# ═════════════════════════════════════════════════════════════════════════════

class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _run(gens, verdicts=None, *, budget=VG.DEFAULT_BUDGET, clock=None, cost=(0.0, 0.0),
         route="SCHEDULING"):
    clock = clock or _Clock()
    seen = {"gen": [], "ver": []}
    gens, verdicts = list(gens), dict(verdicts or {})

    def generate(messages, timeout):
        seen["gen"].append((messages, timeout))
        clock.t += cost[0]
        item = gens[min(len(seen["gen"]) - 1, len(gens) - 1)]
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, tuple):
            return item[1]
        return json.dumps({"bridge": item})

    def verify(messages, timeout):
        seen["ver"].append((messages, timeout))
        clock.t += cost[1]
        v = verdicts.get(json.loads(messages[-1]["content"])["sentence"], CLEAN)
        if isinstance(v, BaseException):
            raise v
        return v
    ctx = VG.GenerationContext(route=route, segment="AVAILABILITY",
                               customer_messages=("hola",), business_date=date(2026, 11, 11))
    return VG.run_bridge_generation(context=ctx, generate=generate, verify=verify,
                                    budget=budget, clock=clock), seen


class U01_BridgeFormat(unittest.TestCase):
    def test_structural_format_refuses_only(self):
        self.assertIsNone(VG.bridge_format_violation("Perfecto, gracias por la info."))
        for bad in ("", "   ", "a" * (VG.MAX_BRIDGE_CHARS + 1), "Hola\nque tal",
                    "Sale 140 mil", "Te espero a las diez y 10", "$ dale", "50% off",
                    "mirá https://x", "ver www.x"):
            with self.subTest(bad=bad):
                self.assertIsNotNone(VG.bridge_format_violation(bad))


class U02_StrictParsers(unittest.TestCase):
    def test_generator_output(self):
        self.assertEqual(VG.parse_bridge('{"bridge": "  Dale.  "}'), "Dale.")
        for raw in ("no json", "[]", '{"bridge": 1}', '{"bridge": "x", "extra": 1}',
                    '{"message": "x"}', None):
            with self.subTest(raw=raw), self.assertRaises(VG.MalformedOutput):
                VG.parse_bridge(raw)

    def test_verifier_output(self):
        v = VG.parse_verdict(verdict((OC.BOOKING_STATUS, "te esperamos")))
        self.assertEqual(v.findings[0].category, OC.BOOKING_STATUS)
        for raw in ('{"purely_conversational": true}',
                    json.dumps({"purely_conversational": "yes", "contains_operational_claim":
                                False, "findings": [], "uncertain": []}),
                    json.dumps({"purely_conversational": True, "contains_operational_claim":
                                False, "findings": [{"category": "NOPE", "span": "x"}],
                                "uncertain": []}),
                    json.dumps({"purely_conversational": True, "contains_operational_claim":
                                False, "findings": [], "uncertain": [], "extra": 1})):
            with self.subTest(raw=raw), self.assertRaises(VG.MalformedOutput):
                VG.parse_verdict(raw)


class U03_VerdictPolicy(unittest.TestCase):
    def test_only_a_pure_consistent_empty_verdict_approves(self):
        ok = VG.parse_verdict(CLEAN)
        self.assertTrue(ok.approves and not ok.inconsistent)
        for raw in (verdict((OC.PRICE_OR_PAYMENT, "x")),             # a finding
                    verdict(unc=("¿quedamos?",)),                     # uncertainty
                    verdict(unc=("¿quedamos?",), pure=True),          # "pure" yet uncertain
                    verdict(pure=False),                              # not affirmed pure
                    verdict(contains=True),                           # claim, no span
                    verdict((OC.OTHER, "x"), pure=True, contains=False)):  # inconsistent
            with self.subTest(raw=raw):
                v = VG.parse_verdict(raw)
                self.assertFalse(v.approves and not v.inconsistent)


class U04_ProvenanceType(unittest.TestCase):
    def mint(self, text="Dale."):
        out, _ = _run([text])
        self.assertTrue(out.approved, out.summary())
        return out.value

    def test_it_cannot_be_constructed_or_forged(self):
        with self.assertRaises(TypeError):
            VG.ApprovedBridgeText("hola")
        with self.assertRaises(TypeError):
            VG._mint(object(), "hola", VG.BridgeRecord("QUALIFYING", "GENERAL", 1))
        # review N-2: str.__new__ + object.__setattr__ gives the right type and record,
        # but the value was never registered by the pipeline — it is not approved
        forged = str.__new__(VG.ApprovedBridgeText, "Te esperamos el lunes.")
        object.__setattr__(forged, "_record", VG.BridgeRecord("SCHEDULING", "GENERAL", 1))
        self.assertFalse(VG.is_approved_bridge(forged))
        self.assertFalse(VG.is_verified(forged))
        self.assertEqual(VG.compose_bridged(forged, "Canon."), "Canon.")
        self.assertIsNot(type(VG.compose_bridged(forged, "Canon.")), VG.ComposedBridgedMessage)

    def test_minted_values_are_immutable_and_lose_provenance_when_edited(self):
        v = self.mint()
        self.assertTrue(VG.is_approved_bridge(v))
        for edited in (v + " ya quedó", v.replace("Dale", "Listo"), v[:3], f"{v}"):
            self.assertFalse(VG.is_approved_bridge(edited))
        with self.assertRaises(AttributeError):
            v._record = None
        import copy
        import pickle
        for fn in (copy.copy, copy.deepcopy, pickle.dumps):
            with self.assertRaises(TypeError):
                fn(v)

    def test_composition_is_bridge_then_canonical_verbatim(self):
        v = self.mint("Perfecto, gracias.")
        m = VG.compose_bridged(v, "Canon. ¿Pregunta?")
        self.assertIs(type(m), VG.ComposedBridgedMessage)
        self.assertEqual(str(m), "Perfecto, gracias.\n\nCanon. ¿Pregunta?")
        self.assertEqual((m.bridge, m.canonical), ("Perfecto, gracias.", "Canon. ¿Pregunta?"))
        self.assertEqual(VG.compose_bridged(None, "Canon."), "Canon.")
        self.assertEqual(VG.compose_bridged("plain str", "Canon."), "Canon.")
        with self.assertRaises(TypeError):
            VG.ComposedBridgedMessage("x")


class U05_PipelinePolicy(unittest.TestCase):
    def test_success_is_one_generation_and_one_verification(self):
        out, seen = _run(["Dale, gracias."])
        self.assertTrue(out.approved)
        self.assertEqual((out.generator_calls, out.verifier_calls, out.attempts), (1, 1, 1))

    def test_any_finding_regenerates_once_then_succeeds(self):
        bad = "Listo, quedó reservado."
        out, seen = _run([bad, "Perfecto, gracias."],
                         {bad: verdict((OC.BOOKING_STATUS, "quedó reservado"))})
        self.assertTrue(out.approved)
        self.assertEqual(str(out.value), "Perfecto, gracias.")
        self.assertEqual(out.attempt_results, [("BOOKING_STATUS",), ("APPROVED",)])
        regen = json.loads(seen["gen"][1][0][-1]["content"])
        self.assertEqual(regen["previous_attempt_rejected"]["categories"], ["BOOKING_STATUS"])
        self.assertNotIn(bad, json.dumps(regen, ensure_ascii=False))

    def test_a_second_rejection_means_no_bridge(self):
        bad = "Ya te agendé."
        out, _ = _run([bad], {bad: verdict((OC.BOOKING_STATUS, "te agendé"))})
        self.assertIsNone(out.value)
        self.assertEqual(out.fallback_reason, VG.FallbackReason.REJECTED_TWICE)
        self.assertEqual((out.generator_calls, out.verifier_calls), (2, 2))
        self.assertEqual(out.rejected_bridges, [bad, bad])

    def test_never_more_than_one_regeneration(self):
        bad = "Ya te agendé."
        out, _ = _run([bad], {bad: verdict(unc=("x",))},
                      budget=VG.VerificationBudget(max_attempts=9))
        self.assertEqual(out.generator_calls, 2)

    def test_a_format_violation_is_a_rejection_without_a_verifier_call(self):
        out, seen = _run(["Sale $140.000.", "Dale."])
        self.assertTrue(out.approved)
        self.assertEqual(out.attempt_results, [("FORMAT",), ("APPROVED",)])
        self.assertEqual(len(seen["ver"]), 1)

    def test_timeouts_errors_and_malformed_output_mean_no_bridge_without_retry(self):
        cases = [
            ([TimeoutError("t")], {}, VG.FallbackReason.GENERATOR_TIMEOUT, (1, 0)),
            ([RuntimeError("x")], {}, VG.FallbackReason.GENERATOR_ERROR, (1, 0)),
            ([("RAW", "nope")], {}, VG.FallbackReason.GENERATOR_MALFORMED, (1, 0)),
            (["Dale."], {"Dale.": TimeoutError("t")}, VG.FallbackReason.VERIFIER_TIMEOUT,
             (1, 1)),
            (["Dale."], {"Dale.": RuntimeError("x")}, VG.FallbackReason.VERIFIER_ERROR, (1, 1)),
            (["Dale."], {"Dale.": "not json"}, VG.FallbackReason.VERIFIER_MALFORMED, (1, 1)),
        ]
        for gens, verdicts, reason, calls in cases:
            with self.subTest(reason=reason):
                out, _ = _run(gens, verdicts)
                self.assertIsNone(out.value)
                self.assertEqual(out.fallback_reason, reason)
                self.assertEqual((out.generator_calls, out.verifier_calls), calls)


class U06_Budget(unittest.TestCase):
    def test_per_call_timeouts_and_the_total_budget(self):
        b = VG.VerificationBudget(generator_timeout_s=4, verifier_timeout_s=3, total_s=6,
                                  min_call_s=1)
        bad = "Ya te agendé."
        out, seen = _run([bad], {bad: verdict(unc=("x",))}, budget=b, cost=(2.0, 2.0))
        self.assertEqual(seen["gen"][0][1], 4)
        self.assertEqual(seen["ver"][0][1], 3)
        self.assertEqual(seen["gen"][1][1], 2)          # only what is left
        self.assertEqual(out.fallback_reason, VG.FallbackReason.BUDGET_EXHAUSTED)
        self.assertIsNone(out.value)

    def test_default_budget(self):
        b = VG.DEFAULT_BUDGET
        self.assertEqual((b.generator_timeout_s, b.verifier_timeout_s, b.total_s,
                          b.max_attempts), (5.0, 5.0, 12.0, 2))


class U07_InputIsolation(unittest.TestCase):
    def test_the_verifier_sees_only_the_bridge_and_the_categories(self):
        out, seen = _run(["Dale, gracias."], route="QUOTATION")
        (messages, _t), = seen["ver"]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertEqual(json.loads(messages[1]["content"]), {"sentence": "Dale, gracias."})
        self.assertNotIn("hola", messages[0]["content"])      # no customer words
        self.assertNotIn("QUOTATION", json.dumps(messages))   # no route
        for c in OC:
            self.assertIn(c.value, messages[0]["content"])

    def test_the_generator_never_receives_a_canonical_fact(self):
        out, seen = _run(["Dale."])
        payload = json.loads(seen["gen"][0][0][-1]["content"])
        self.assertEqual(set(payload), {"route", "followed_by", "style", "customer_messages"})
        self.assertIn(payload["followed_by"], VG.SEGMENT_KINDS)


# ═════════════════════════════════════════════════════════════════════════════
# P — production path
# ═════════════════════════════════════════════════════════════════════════════

class P01_NaturalBridgePlusCanonical(_Engine):
    """Every bridge route: the delivered message is the approved bridge, then the
    canonical message the engine sends without one — byte for byte."""

    def test_each_route_delivers_bridge_then_the_unchanged_canonical_segment(self):
        for label in ("QUALIFYING", "GENERAL_INFORMATION", "QUOTATION", "SCHEDULING",
                      "EVALUATED"):
            canonical = self.canonical(label)

            def check(sb, direct, label=label, canonical=canonical):
                tid, wa, customer = self.world(label)
                r = vturn(self.eng, tid, wa, customer, Script([NATURAL[label]]), sb, direct)
                self.assertEqual(r["sent"], [NATURAL[label] + "\n\n" + canonical], r)
                self.assertEqual(r["bridge"], NATURAL[label], r)
                self.assertEqual(r["vg"]["route"], ROUTE_OF[label], r)
                self.assertEqual((r["gen_calls"], r["ver_calls"]), (1, 1), r)
                self.assert_not_booked_and_no_state_from_prose(r)
                self.assert_interpreter_body_never_sent(r)
                self.assertEqual(r["flows"], 0, r)
            with self.subTest(label=label):
                self.each_posture(check)

    def test_the_quote_is_the_canonical_quotation(self):
        tid, wa, customer = self.world("QUOTATION")
        r = vturn(self.eng, tid, wa, customer, Script(["Dale, sin apuro."]), False, False)
        canonical = canonical_of(r)
        self.assertIn("140.000", canonical)
        self.assertNotIn("140", r["bridge"])


class P02_BookingConfirmationsAreNeverDelivered(_Engine):
    """Owner decision: before persisted Flow success nothing may say or imply a booking."""

    def test_confirmation_bridges_are_rejected_on_an_available_not_booked_thread(self):
        for phrase in CONFIRMATIONS:
            def check(sb, direct, phrase=phrase):
                tid, wa, customer = self.world("EVALUATED")
                s = Script([phrase], {phrase: verdict((OC.BOOKING_STATUS, phrase))})
                r = vturn(self.eng, tid, wa, customer, s, sb, direct)
                self.assertIsNone(r["bridge"], r)
                (text,) = r["sent"]
                self.assertNotIn(phrase, text)
                self.assert_no_confirmation(r)
                self.assert_not_booked_and_no_state_from_prose(r)
                self.assertIn(r["vg"]["fallback_reason"], ("REJECTED_TWICE",), r)
                # availability only, and the Meta Booking Flow as the way to book
                self.assertIn("hay disponibilidad a las 10:00", text)
                self.assertIn(FLOW_CTA, text)
            with self.subTest(phrase=phrase):
                self.each_posture(check)

    def test_digit_bearing_confirmations_never_even_reach_the_verifier(self):
        tid, wa, customer = self.world("EVALUATED")
        s = Script([CONFIRMATIONS[0], CONFIRMATIONS[1]])   # verifier would say "clean"
        r = vturn(self.eng, tid, wa, customer, s, False, False)
        self.assertIsNone(r["bridge"], r)
        self.assertEqual(r["ver_calls"], 0, r)
        self.assertEqual(r["vg"]["attempt_results"], [["FORMAT"], ["FORMAT"]], r)
        self.assert_no_confirmation(r)

    def test_a_verifier_miss_on_a_temporal_confirmation_is_refused_by_defence_in_depth(self):
        miss = "Dale, te esperamos mañana entonces."
        tid, wa, customer = self.world("EVALUATED")
        r = vturn(self.eng, tid, wa, customer, Script([miss]), False, False)   # verifier: clean
        self.assertIsNone(r["bridge"], r)
        self.assertEqual(r["vg"]["fallback_reason"], "DEFENCE_IN_DEPTH_REFUSED", r)
        self.assertNotIn(miss, r["sent"][0])


class P03_AvailabilityIsNotBooking(_Engine):
    def test_the_pre_booking_message_states_availability_and_the_flow_cta_only(self):
        tid, wa, customer = self.world("EVALUATED")
        r = vturn(self.eng, tid, wa, customer, Script([NATURAL["EVALUATED"]]), False, False)
        canonical = canonical_of(r)
        self.assertEqual(canonical,
                         "Para el lunes 16/11 hay disponibilidad a las 10:00, 11:00, 15:00 y "
                         f"15:30. {FLOW_CTA}")
        self.assertEqual(r["provenance"], "DETERMINISTIC_COMPOSED", r)
        self.assert_no_confirmation(r)
        self.assert_not_booked_and_no_state_from_prose(r)
        self.assertIsNone(r["token"], r)          # no Flow armed by text
        self.assertEqual(r["flows"], 0, r)        # the Flow is dispatched only by its own path

    def test_without_a_current_evaluation_the_next_step_is_the_day_question(self):
        tid, wa, customer = self.world("SCHEDULING")
        r = vturn(self.eng, tid, wa, customer, Script(["Buenísimo."]), False, False)
        self.assertEqual(canonical_of(r), CE_MOD._TX_SCHEDULING_ASK_TEXT)
        self.assert_not_booked_and_no_state_from_prose(r)


class P04_ConfirmationRequiresPersistedFlowSuccess(_Engine):
    def test_booked_keeps_the_deterministic_persisted_confirmation_and_no_bridge(self):
        def check(sb, direct):
            tid, wa = A._seed(self.eng, stage="BOOKED", flag="ACEPTADO", booked=A.BOOKED)
            r = vturn(self.eng, tid, wa, A._THINK, Script(["NUNCA"]), sb, direct)
            self.assertEqual(r["gen_calls"], 0, r)
            self.assertIsNone(r["bridge"], r)
            self.assertTrue(r["sent"][0].startswith(
                "Tu turno está confirmado para el lunes 16/11 a las 10:00."), r)
            self.assertTrue(r["needs_human"], r)
        self.each_posture(check)

    def test_no_persisted_booking_means_no_confirmation(self):
        # a revision that is not a committed booking (the Flow failed / was abandoned /
        # expired before persistence): never a confirmation
        for status in ("cancelled", "draft", "collecting_data", "reschedule_pending"):
            with self.subTest(status=status):
                tid, wa = A._seed(self.eng, stage="SCHEDULING", flag="ACEPTADO",
                                  booked=A.BOOKED, revision_status=status)
                r = vturn(self.eng, tid, wa, A._THINK, Script(["Dale."]), False, False)
                self.assert_no_confirmation(r)
                self.assertEqual(r["booked"], 0, r)

    def test_flow_pending_is_deterministic_and_dispatches_no_duplicate_flow(self):
        def check(sb, direct):
            tid, wa = A._seed(self.eng, stage="SCHEDULING", flag="ACEPTADO",
                              token="tok-r1-pending")
            r = vturn(self.eng, tid, wa, A._THINK, Script(["NUNCA"]), sb, direct)
            self.assertEqual(r["gen_calls"], 0, r)
            self.assertEqual(r["flows"], 0, r)
            self.assertEqual(r["sent"], [CE_MOD._TX_FLOW_PENDING_TEXT], r)
            self.assert_no_confirmation(r)
        self.each_posture(check)


class P05_BridgesAreClaimFreeEvenWhenTheFactIsCanonical(_Engine):
    def test_correct_facts_and_mixed_bridges_are_rejected_completely(self):
        cases = (
            # a correct canonical fact (Corolla, Palermo, efectivo) — still rejected
            ("Buenísimo, el Corolla queda en buenas manos.", OC.VEHICLE),
            ("Perfecto, en Palermo andamos seguido.", OC.LOCATION_OR_COVERAGE),
            # harmless acknowledgement + one operational claim
            ("Gracias por escribir, aceptamos efectivo.", OC.PRICE_OR_PAYMENT),
            ("Dale, genial, y te damos garantía.", OC.WARRANTY),
            ("Perfecto, enseguida te llama un asesor.", OC.HANDOFF),
        )
        for bridge, cat in cases:
            with self.subTest(bridge=bridge):
                tid, wa, customer = self.world("QUOTATION")
                s = Script([bridge], {bridge: verdict((cat, bridge))})
                r = vturn(self.eng, tid, wa, customer, s, False, False)
                self.assertIsNone(r["bridge"], r)
                for t in r["sent"]:
                    self.assertNotIn(bridge, t)
                    # nothing of the bridge survives, not even its harmless half
                    self.assertFalse(t.startswith(bridge.split(",")[0]), t)
                self.assertEqual(r["vg"]["attempt_results"], [[cat.value], [cat.value]], r)

    def test_uncertainty_or_a_non_affirmed_verdict_rejects(self):
        b = "Dale, lo vemos."
        for v in (verdict(unc=("lo vemos",)), verdict(unc=("lo vemos",), pure=True),
                  verdict(pure=False)):
            with self.subTest(v=v):
                tid, wa, customer = self.world("QUALIFYING")
                r = vturn(self.eng, tid, wa, customer, Script([b], {b: v}), False, False)
                self.assertIsNone(r["bridge"], r)


class P06_CanonicalFactsExactlyOnce(_Engine):
    def test_no_duplicate_price_availability_or_cta(self):
        # WILD1-C: the quote is the canonical breakdown block; with viáticos $0 the amount is
        # on both its Revisión and Total lines, so "stated once" is the BLOCK stated once.
        for label, needles in (("QUOTATION", (CE_MOD._rv_render_price_breakdown(140000, 0, 140000),
                                              "Total: $140.000")),
                               ("EVALUATED", ("10:00", FLOW_CTA, "disponibilidad")),
                               ("SCHEDULING", (CE_MOD._TX_SCHEDULING_ASK_TEXT,)),
                               ("QUALIFYING", ("¿En qué localidad o barrio está el auto?",))):
            with self.subTest(label=label):
                tid, wa, customer = self.world(label)
                r = vturn(self.eng, tid, wa, customer, Script([NATURAL[label]]), False, False)
                (text,) = r["sent"]
                for n in needles:
                    self.assertEqual(text.count(n), 1, (n, text))


class P07_FailureSendsTheCanonicalMessageWithoutABridge(_Engine):
    def test_generation_or_verification_failure(self):
        canonical = self.canonical("QUOTATION")
        for name, gens, verdicts in (("gen-timeout", [TimeoutError("t")], {}),
                                     ("gen-error", [RuntimeError("x")], {}),
                                     ("gen-malformed", [("RAW", "nope")], {}),
                                     ("ver-timeout", ["Dale."], {"Dale.": TimeoutError("t")}),
                                     ("ver-malformed", ["Dale."], {"Dale.": "{}"})):
            def check(sb, direct, gens=gens, verdicts=verdicts):
                tid, wa, customer = self.world("QUOTATION")
                r = vturn(self.eng, tid, wa, customer, Script(gens, verdicts), sb, direct)
                self.assertEqual(r["sent"], [canonical], r)
                self.assertIsNone(r["bridge"], r)
                self.assertEqual(r["gen_calls"], 1, r)
            with self.subTest(name=name):
                self.each_posture(check)

    def test_a_bridge_carrying_the_discarded_interpreter_body_is_dropped(self):
        body = "Perfecto, ya lo tengo anotado todo"
        tid, wa, customer = self.world("QUALIFYING")
        r = vturn(self.eng, tid, wa, customer, Script([body]), False, False, body=body)
        self.assertIsNone(r["bridge"], r)
        self.assertEqual(r["vg"]["fallback_reason"], "DISCARDED_BODY", r)
        self.assertNotIn(body, r["sent"][0])


class P08_NeedsHuman(_Engine):
    def test_no_bridge_on_a_needs_human_thread(self):
        tid, wa, customer = self.world("QUOTATION")
        with Session(self.eng) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == tid)).scalar_one()
            st.needs_human = True
            db.commit()
        r = vturn(self.eng, tid, wa, customer, Script(["Dale."]), False, False)
        self.assertEqual(r["gen_calls"], 0, r)
        self.assertIsNone(r["bridge"], r)


class P09_Observability(_Engine):
    def test_the_outcome_is_recorded_without_customer_text(self):
        bridge = "Perfecto, gracias por la info."
        tid, wa, customer = self.world("QUALIFYING")
        with self.assertLogs("app.services.conversation_engine", level=logging.INFO) as cap:
            vturn(self.eng, tid, wa, customer, Script([bridge]), False, False)
        line = [x for x in cap.output if "R6-C4B-R1 bridge" in x]
        self.assertEqual(len(line), 1, cap.output)
        for key in ("route", "segment", "approved", "attempts", "generator_calls",
                    "verifier_calls", "fallback_reason", "latency_ms", "generator_ms",
                    "verifier_ms"):
            self.assertIn(f'"{key}"', line[0])
        for secret in (bridge, customer, wa):
            self.assertNotIn(secret, line[0])


class P10_ProviderSeams(unittest.TestCase):
    """The real bridge seams: separate calls, explicit timeouts, no provider contact here
    (urlopen is stubbed, or the missing key stops the call first)."""

    def eng(self, key="sk-test"):
        e = object.__new__(ConversationEngine)
        e.settings = types.SimpleNamespace(openai_api_key=key, openai_chat_model="chat-model")
        e._call_generator = types.MethodType(FAKES.ORIGINALS["_call_generator"], e)
        e._call_verifier = types.MethodType(FAKES.ORIGINALS["_call_verifier"], e)
        return e

    def test_each_seam_sends_its_own_prompt_with_its_own_timeout(self):
        e = self.eng()
        seen = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

        def fake_urlopen(req, timeout):
            seen.append((json.loads(req.data), timeout))
            return Resp()
        with mock.patch("app.services.conversation_engine.urlrequest.urlopen", fake_urlopen), \
                mock.patch("app.services.booking_authority.forbid_external_io", lambda *_: None):
            e._call_generator([{"role": "user", "content": "g"}], timeout=4.5)
            e._call_verifier([{"role": "user", "content": "v"}], timeout=3.5)
        (g, gt), (v, vt) = seen
        self.assertEqual((gt, vt), (4.5, 3.5))
        self.assertEqual((g["model"], v["model"]), ("chat-model", "chat-model"))
        self.assertEqual((g["temperature"], v["temperature"]), (0.5, 0.0))
        self.assertEqual((g["max_tokens"], v["max_tokens"]), (80, 300))
        self.assertEqual(g["response_format"], {"type": "json_object"})
        self.assertEqual((e._generator_calls, e._verifier_calls), (1, 1))

    def test_no_key_is_a_generator_error_and_no_bridge(self):
        e = self.eng(key="")
        out = VG.run_bridge_generation(
            context=VG.GenerationContext("QUALIFYING", "GENERAL", ("hola",),
                                         date(2026, 11, 11)),
            generate=e._call_generator, verify=e._call_verifier)
        self.assertEqual(out.fallback_reason, VG.FallbackReason.GENERATOR_ERROR)
        self.assertIsNone(out.value)


class P11_PerformanceEvidence(_Engine):
    """Stubbed / instrumented latency only — real provider latency is in the closeout."""

    def test_stubbed_success_regeneration_and_no_bridge_latency(self):
        rows = []
        bad = "Ya te agendé."
        for name, script in (
                ("success", Script(["Dale, gracias."])),
                ("regen-success", Script([bad, "Dale, gracias."],
                                         {bad: verdict((OC.BOOKING_STATUS, bad))})),
                ("no-bridge", Script([bad], {bad: verdict((OC.BOOKING_STATUS, bad))}))):
            tid, wa, customer = self.world("SCHEDULING")
            r = vturn(self.eng, tid, wa, customer, script, False, False)
            rows.append((name, r["vg"]["latency_ms"], round(r["wall_ms"], 1),
                         r["gen_calls"], r["ver_calls"]))
            self.assertLess(r["vg"]["latency_ms"], 1000, r)
        print("\nR6C4BR1-PERF " + json.dumps(rows))


# ═════════════════════════════════════════════════════════════════════════════
# S — structure
# ═════════════════════════════════════════════════════════════════════════════

def _fn(src, name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(name)


class S01_OneFactory(unittest.TestCase):
    def test_only_the_pipeline_mints(self):
        for path in BACKEND.rglob("*.py"):
            src = path.read_text(encoding="utf-8")
            if path.name == "verified_generation.py":
                self.assertEqual(src.count("_mint(_MINT"), 1)
                self.assertEqual(src.count("str.__new__(ApprovedBridgeText"), 1)
                self.assertIn("_mint(_MINT", _fn(src, "run_bridge_generation"))
                continue
            for needle in ("_mint(", "._MINT", "import _MINT", "._MINTED",
                           "str.__new__(ApprovedBridgeText", "ApprovedBridgeText(",
                           "ComposedBridgedMessage("):
                self.assertNotIn(needle, src, f"{path.name}: {needle}")

    def test_the_mint_follows_an_approving_consistent_verdict(self):
        body = _fn(VG_SRC, "run_bridge_generation")
        self.assertLess(body.index("verdict = parse_verdict(vraw)"),
                        body.index("if verdict.approves and not verdict.inconsistent:"))
        self.assertLess(body.index("if verdict.approves and not verdict.inconsistent:"),
                        body.index("_mint(_MINT"))
        self.assertIn("min(int(budget.max_attempts), 2)", body)
        self.assertIn("verifier_messages(bridge)", body)


class S02_Composition(unittest.TestCase):
    def test_the_boundary_discards_the_interpreter_body_on_every_route(self):
        body = _fn(CE_SRC, "_apply_transactional_boundary")
        self.assertIn('decision["reply"] = None', body)
        self.assertNotIn("if route.transactional", body)

    def test_the_bridge_is_attached_after_validation_and_before_composition(self):
        body = _fn(CE_SRC, "_send_text_to_wa")
        self.assertLess(body.index("self._validate_outbound_text("),
                        body.index("_vg.compose_bridged(_bridge, text)"))
        # before the plan composes: nothing is added after composition (G3-6 / G3-7)
        self.assertLess(body.index("_vg.compose_bridged(_bridge, text)"),
                        body.index("text = plan.compose(text)"))
        self.assertLess(body.index("_vg.compose_bridged(_bridge, text)"),
                        body.index("gate.attempt("))
        self.assertEqual(body.count("compose_bridged("), 1)

    def test_the_bridge_passes_defence_in_depth_alone(self):
        body = _fn(CE_SRC, "_approved_bridge")
        self.assertIn("validate_envelope(\n                    bridge,", body)
        self.assertIn("generate=self._call_generator, verify=self._call_verifier", body)
        self.assertIn("needs_human", body)

    def test_no_bridge_function_writes_state(self):
        for name in ("_approved_bridge", "_send_with_bridge", "_scheduling_next_step",
                     "_canonical_qualification_value", "_call_generator", "_call_verifier"):
            body = _fn(CE_SRC, name)
            for writer in ("state.preferred", "state.last_stage", "lead.flag",
                           "_apply_extracted", "db.add(", "db.commit(", "_send_booking_flow(",
                           "_dispatch_booking_flow_for_day(", ".issue("):
                self.assertNotIn(writer, body, f"{name}: {writer}")

    def test_booked_and_flow_pending_never_carry_a_bridge(self):
        self.assertIsNone(ConversationEngine._vg_route_label(CE_MOD.TransactionalRoute.BOOKED,
                                                             None))
        self.assertIsNone(ConversationEngine._vg_route_label(
            CE_MOD.TransactionalRoute.FLOW_PENDING, None))
        body = _fn(CE_SRC, "_send_transactional_reply")
        self.assertIn("route in (TransactionalRoute.QUOTATION, TransactionalRoute.SCHEDULING)",
                      body)


if __name__ == "__main__":
    unittest.main()
