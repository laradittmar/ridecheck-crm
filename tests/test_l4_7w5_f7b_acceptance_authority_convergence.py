"""L4.7W5-F7B — one business claim, one canonical producer.

A customer answered "si" to "¿avanzamos?" and asked "para cuando tenes" in the same
burst. The conversation deadlocked at QUOTED and they had to ask twice more.

F6 fixed the modality scoping in `claim_projection` and verified it by calling that
helper directly. The live path kept deadlocking, so F7A recorded a duplicate-producer
finding: `conversation_engine._authorize_acceptance` assembled its OWN QUOTE_ACCEPTED
claim with its own classifier (`turn_modality`) beside the projection's F6-scoped one.

Driving both producers proved something sharper than "they disagree":

  * `_is_acceptance` demands the WHOLE burst be acceptance words, so it returns False the
    moment a scheduling clause appears — exactly where the two classifiers differ. Across
    26 realistic bursts there was NO input on which the engine's producer both fired and
    disagreed. The duplication was real; the divergence was latent, held off only by a
    coincidence of firing conditions.
  * That same disjointness WAS the live defect. For "si" + "para cuando tenes" the
    deterministic side produced nothing at all, so acceptance existed only if the semantic
    model call came back ACCEPT. With no semantic evidence the authorizer saw no stance
    and answered HOLD — "no acceptance evidence in this turn" — which is the deadlock,
    reachable again on any timeout.

So the convergence is not a copy of the F6 fix into a second place. Both readings now
leave ONE producer, `claim_projection.acceptance_claims`, under one modality policy, and
the deterministic floor can read the Wild burst with no model call at all.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for extra in (ROOT / "tests", ROOT / "backend"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from test_l4_7w3_f1_semantic_authority_flow_first import (  # noqa: E402
    AcceptanceSignal,
    authorize_quote_acceptance,
    evidence,
    quoted_state,
)

import app.services.claim_projection as cp            # noqa: E402
import app.services.conversation_engine as ce         # noqa: E402
from app.schemas.claims import ClaimType, Polarity    # noqa: E402
from app.services.claim_projection import acceptance_claims, deterministic_acceptance  # noqa: E402


def decide(texts, sem=None, sched=None, state=None):
    claims = acceptance_claims(list(texts), sem, cycle_id="c1",
                               has_scheduling_evidence=sched)
    return authorize_quote_acceptance(claims, state or quoted_state()), claims


def accepted(claims):
    return [c for c in claims if c.claim_type == ClaimType.QUOTE_ACCEPTED
            and c.polarity is Polarity.ASSERTED]


# ── the Wild sequence, with NO semantic evidence ──────────────────────────────

class TestWildSequence(unittest.TestCase):
    """Every case here passes `sem=None`: if these pass, acceptance no longer depends
    on a model call being available."""

    def test_f7b_01_exact_wild_two_messages(self):
        d, claims = decide(["si", "para cuando tenes"], sched=True)
        self.assertEqual(d.result, "ALLOW", d.reason)
        self.assertEqual(d.stance, "ACCEPT")
        self.assertEqual(len(accepted(claims)), 1)

    def test_f7b_02_same_message(self):
        d, _ = decide(["si, para cuando tenes?"], sched=True)
        self.assertEqual(d.result, "ALLOW", d.reason)

    def test_f7b_03_dale_after_cta(self):
        self.assertEqual(decide(["dale"], sched=False)[0].result, "ALLOW")

    def test_f7b_04_bueno_si(self):
        self.assertEqual(decide(["bueno si"], sched=False)[0].result, "ALLOW")

    def test_f7b_05_acceptance_plus_same_day(self):
        d, _ = decide(["si", "para hoy que tenes?"], sched=True)
        self.assertEqual(d.result, "ALLOW", d.reason)

    def test_f7b_06_acceptance_plus_explicit_day(self):
        d, _ = decide(["dale", "el jueves puede ser?"], sched=True)
        self.assertEqual(d.result, "ALLOW", d.reason)

    def test_f7b_07_acceptance_plus_exact_time(self):
        d, _ = decide(["si", "a las 15 te queda bien?"], sched=True)
        self.assertEqual(d.result, "ALLOW", d.reason)

    def test_f7b_08_acceptance_plus_urgency(self):
        d, _ = decide(["dale", "lo antes posible por favor"], sched=True)
        self.assertEqual(d.result, "ALLOW", d.reason)

    def test_f7b_09_acceptance_without_scheduling(self):
        d, _ = decide(["si dale"], sched=False)
        self.assertEqual(d.result, "ALLOW", d.reason)


# ── what must still not advance ───────────────────────────────────────────────

class TestUnsafeAcceptanceStillBlocked(unittest.TestCase):

    def assert_not_allowed(self, texts, sched=True):
        d, claims = decide(texts, sched=sched)
        self.assertNotEqual(d.result, "ALLOW", f"{texts!r} advanced: {d.reason}")
        self.assertEqual(accepted(claims), [], f"{texts!r} produced acceptance evidence")

    def test_f7b_10_conditional_tomorrow(self):
        self.assert_not_allowed(["si mañana puedo"])

    def test_f7b_11_conditional_money(self):
        self.assert_not_allowed(["si consigo la plata"])

    def test_f7b_12_hesitation(self):
        self.assert_not_allowed(["capaz que si"])

    def test_f7b_13_deferred(self):
        self.assert_not_allowed(["si despues te aviso"])

    def test_f7b_14_bare_si_without_a_quote(self):
        d, _ = decide(["si"], sched=False, state=quoted_state(quote_total=None))
        self.assertNotEqual(d.result, "ALLOW")

    def test_f7b_15_explicit_rejection(self):
        self.assert_not_allowed(["no, no avancemos"])

    def test_f7b_16_same_burst_conflict(self):
        """Acceptance and rejection in one burst must not resolve by arrival order."""
        for texts in (["si dale", "no, mejor no avancemos"],
                      ["no, mejor no avancemos", "si dale"]):
            with self.subTest(texts=texts):
                self.assert_not_allowed(texts)

    def test_f7b_16b_semantic_rejection_withdraws_deterministic_acceptance(self):
        claims = acceptance_claims(["si", "para cuando tenes"],
                                   evidence(AcceptanceSignal.REJECT),
                                   cycle_id="c1", has_scheduling_evidence=True)
        self.assertEqual(accepted(claims), [])
        self.assertNotEqual(
            authorize_quote_acceptance(claims, quoted_state()).result, "ALLOW")

    def test_f7b_17_stale_quote(self):
        d, _ = decide(["si"], sched=False, state=quoted_state(quote_cycle_id="c0"))
        self.assertNotEqual(d.result, "ALLOW")

    def test_f7b_18_stale_candidate(self):
        d, _ = decide(["si"], sched=False, state=quoted_state(quote_candidate_id=99))
        self.assertNotEqual(d.result, "ALLOW")

    def test_f7b_19_stale_location(self):
        d, _ = decide(["si"], sched=False,
                      state=quoted_state(current_zone_detail="Pilar"))
        self.assertNotEqual(d.result, "ALLOW")


# ── single-claim / single-side-effect invariants ──────────────────────────────

class TestSingleClaimInvariant(unittest.TestCase):

    def test_f7b_20_one_canonical_claim_even_when_both_readings_fire(self):
        """Deterministic AND semantic both say yes — one claim reaches the authorizer."""
        claims = acceptance_claims(["si", "para cuando tenes"],
                                   evidence(AcceptanceSignal.ACCEPT),
                                   cycle_id="c1", has_scheduling_evidence=True)
        self.assertEqual(len(accepted(claims)), 1)

    def test_f7b_21_transition_authorized_exactly_once(self):
        """The producer is pure: calling it twice yields the same single decision."""
        first, c1 = decide(["si", "para cuando tenes"], sched=True)
        second, c2 = decide(["si", "para cuando tenes"], sched=True)
        self.assertEqual(first.result, second.result)
        self.assertEqual(len(accepted(c1)), len(accepted(c2)), 1)

    def test_f7b_22_producer_performs_no_state_mutation(self):
        src = inspect.getsource(cp.acceptance_claims)
        tree = ast.parse(inspect.getsource(cp))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "acceptance_claims")
        # No attribute assignment: the producer returns evidence, it never writes state.
        self.assertFalse([n for n in ast.walk(fn)
                          if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Attribute) for t in n.targets)])
        self.assertNotIn("db.commit", src)

    def test_f7b_23_producer_never_sends(self):
        src = inspect.getsource(cp.acceptance_claims)
        for forbidden in ("send_", "gate.attempt", "_reply", "outbound"):
            self.assertNotIn(forbidden, src)

    def test_f7b_24_producer_writes_no_booking(self):
        src = inspect.getsource(cp.acceptance_claims)
        for forbidden in ("ThreadRevision", "booked", "BookingFlowService"):
            self.assertNotIn(forbidden, src)


# ── static governance ─────────────────────────────────────────────────────────

def _code_only(fn) -> str:
    """The function's executable body, docstring removed.

    The docstring explains that this method no longer calls `turn_modality`; a plain
    source search finds that sentence and reports the very thing it disclaims. F7A was
    burned by comment-anchored assertions, so the prose is excluded from the evidence.
    """
    node = ast.parse(ast.unparse(fn)).body[0]
    body = list(node.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


def _function_nodes(module):
    return [n for n in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _constructs_quote_accepted(fn) -> bool:
    """True when this function body builds a QUOTE_ACCEPTED ClaimEvidence.

    AST, not substring: the engine still *mentions* QUOTE_ACCEPTED in a docstring
    explaining why it no longer produces one, and a text search would read that prose as
    a violation. F7A was burned by exactly that class of test.
    """
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in ("ClaimEvidence", "add"):
            continue
        for kw in node.keywords:
            if kw.arg in ("claim_type", None):
                if "QUOTE_ACCEPTED" in ast.unparse(kw.value):
                    return True
        for arg in node.args:
            if "QUOTE_ACCEPTED" in ast.unparse(arg):
                return True
    return False


class TestGovernance(unittest.TestCase):

    def test_f7b_27_quote_accepted_has_one_canonical_home(self):
        engine_producers = [f.name for f in _function_nodes(ce)
                            if _constructs_quote_accepted(f)]
        self.assertEqual(engine_producers, [],
                         f"conversation_engine produces QUOTE_ACCEPTED in {engine_producers}")
        projection_producers = sorted(f.name for f in _function_nodes(cp)
                                      if _constructs_quote_accepted(f))
        self.assertEqual(projection_producers,
                         ["acceptance_claims", "claims_from_turn_evidence"],
                         "acceptance evidence gained or lost a producer")

    def test_f7b_27b_authorize_acceptance_cannot_decide_on_its_own(self):
        fn = next(f for f in _function_nodes(ce) if f.name == "_authorize_acceptance")
        body = _code_only(fn)
        self.assertIn("acceptance_claims", body)
        self.assertIn("authorize_quote_acceptance", body)
        # It may not classify modality itself, nor build claims, nor mutate, nor send.
        for forbidden in ("turn_modality", "acceptance_modality", "ClaimEvidence",
                          "_is_acceptance", "last_stage =", "send_"):
            self.assertNotIn(forbidden, body, f"_authorize_acceptance still does {forbidden}")

    def test_f7b_28_live_handle_path_reaches_the_canonical_producer(self):
        """The chokepoint, by call graph rather than by name."""
        callers = [f.name for f in _function_nodes(ce)
                   if "_authorize_acceptance" in ast.unparse(f) and f.name != "_authorize_acceptance"]
        self.assertTrue(callers, "nothing calls _authorize_acceptance")
        fn = next(f for f in _function_nodes(ce) if f.name == "_authorize_acceptance")
        self.assertIn("acceptance_claims", ast.unparse(fn))

    def test_f7b_29_no_second_acceptance_producer_may_be_added(self):
        """A new producer anywhere in the services layer fails this test."""
        import importlib
        offenders = []
        services = (ROOT / "backend" / "app" / "services")
        for path in sorted(services.glob("*.py")):
            if path.name in ("claim_projection.py",):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for fn in [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                if _constructs_quote_accepted(fn):
                    offenders.append(f"{path.name}:{fn.name}")
        self.assertEqual(offenders, [], f"second acceptance producer(s): {offenders}")

    def test_f7b_30_no_new_feature_flag_was_introduced(self):
        """Convergence is unconditional — there is no flag to roll back or leave off."""
        src = inspect.getsource(cp.acceptance_claims) + inspect.getsource(cp.deterministic_acceptance)
        for forbidden in ("settings.", "getenv", "_enabled"):
            self.assertNotIn(forbidden, src)


# ── the deterministic floor itself ────────────────────────────────────────────

class TestDeterministicFloor(unittest.TestCase):

    def test_f7b_31_floor_reads_the_wild_burst_without_a_model(self):
        self.assertTrue(deterministic_acceptance(["si", "para cuando tenes"], True))
        self.assertTrue(deterministic_acceptance(["si, para cuando tenes?"], True))

    def test_f7b_32_floor_requires_scheduling_to_explain_extra_clauses(self):
        """Without scheduling evidence the coarse reading stands — the F6 rule."""
        self.assertFalse(deterministic_acceptance(["si", "necesito pensarlo"], False))

    def test_f7b_33_floor_rejects_conditional_and_deferred(self):
        for texts in (["si consigo la plata"], ["capaz que si"], ["si despues te aviso"]):
            with self.subTest(texts=texts):
                self.assertFalse(deterministic_acceptance(texts, True))

    def test_f7b_34_is_acceptance_unchanged_for_its_own_callers(self):
        """The keyword set moved; the historical predicate did not change behaviour."""
        self.assertTrue(ce._is_acceptance(["si"]))
        self.assertTrue(ce._is_acceptance(["si dale"]))
        self.assertFalse(ce._is_acceptance(["si", "para cuando tenes"]))
        self.assertIs(ce._ACCEPTANCE_KEYWORDS, cp.ACCEPTANCE_KEYWORDS)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()


# ── the live chokepoint: handle() → _handle() → authorize.quote_acceptance@v1 ──

import ast as _ast_unused  # noqa: E402,F401  (kept for module symmetry)
from datetime import datetime, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.models  # noqa: E402
from app.models import (  # noqa: E402
    Lead,
    ViaticosZone,
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppThread,
    WhatsAppThreadCandidate,
    WhatsAppThreadState,
)
from app.schemas.conversation import ConversationHandleIn  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402

_live_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@event.listens_for(_live_engine, "connect")
def _live_pragmas(conn, _rec):
    conn.execute("PRAGMA foreign_keys=OFF")


app.models.Base.metadata.create_all(_live_engine)
_LiveSession = sessionmaker(bind=_live_engine, autoflush=False, autocommit=False)
LIVE_WA_ID = "5491100000777"


class TestLivePath(unittest.TestCase):
    """F7B-28/29 — the real entry point, not a helper.

    The decisive assertion is STANCE. Before F7B the engine's own producer could not fire
    on "si" + "para cuando tenes" (`_is_acceptance` needs the whole burst to be acceptance
    words), so with no semantic evidence the authorizer recorded stance=None and answered
    HOLD — "no acceptance evidence in this turn". That is the deadlock, and it is visible
    on the live path without needing a priced fixture: stance is read off the decision the
    engine itself recorded.
    """

    def setUp(self):
        with _live_engine.begin() as conn:
            for tbl in reversed(app.models.Base.metadata.sorted_tables):
                conn.execute(tbl.delete())
        self.db = _LiveSession()
        self.db.add(ViaticosZone(zone_group="CABA", zone_detail="La Paternal", viaticos=0))
        contact = WhatsAppContact(wa_id=LIVE_WA_ID, display_name="Tester")
        self.db.add(contact); self.db.flush()
        self.lead = Lead(estado="CONSULTA_NUEVA", flag="PRESUPUESTO_ENVIADO",
                         necesita_humano=False)
        self.db.add(self.lead); self.db.flush()
        self.thread = WhatsAppThread(contact_id=contact.id, lead_id=self.lead.id)
        self.db.add(self.thread); self.db.flush()
        self.cand = WhatsAppThreadCandidate(
            thread_id=self.thread.id, marca="Renault", modelo="Sandero", anio=2020,
            tipo_vehiculo="AUTO", zone_group="CABA", zone_detail="La Paternal",
            status="current_focus")
        self.db.add(self.cand); self.db.flush()
        self.state = WhatsAppThreadState(
            thread_id=self.thread.id, last_stage="QUOTED", needs_human=False,
            home_zone_group="CABA", home_zone_detail="La Paternal",
            current_focus_candidate_id=self.cand.id)
        self.db.add(self.state); self.db.commit()

        # `_acceptance_authority_on()` tests `is True`, and a bare MagicMock attribute is
        # not True — a fixture built with MagicMock alone runs the LEGACY ungated path and
        # would certify nothing. The flags are set explicitly.
        settings = MagicMock()
        settings.reconciler_acceptance_authority_enabled = True
        settings.reconciler_scheduling_authority_enabled = True
        settings.reconciler_vehicle_authority_enabled = True
        settings.reconciler_location_authority_enabled = True
        settings.semantic_same_turn_enabled = False       # no model: the floor must carry it
        self.eng = ConversationEngine(db=self.db, settings=settings)
        self.sent: list[str] = []
        self.flows: list[dict] = []
        self.eng._send_text_to_wa = lambda ctx, text: (self.sent.append(text), "wamid.OUT")[1]
        self.eng._send_flow_button = lambda *a, **k: (self.flows.append(k), "wamid.FLOW")[1]
        self.eng._send_scheduling_handoff_email = lambda **k: None
        self.eng._send_booking_notification = lambda **k: None
        # Capture every authorization the engine records, without changing behaviour.
        self.decisions: list = []
        original = self.eng._record_authorization
        def _capture(ctx, decision, state):
            self.decisions.append(decision)
            return original(ctx, decision, state)
        self.eng._record_authorization = _capture

    def tearDown(self):
        self.db.close()

    def turn(self, *texts, wa_msg_id="wamid.F7B"):
        for i, t in enumerate(texts):
            self.db.add(WhatsAppMessage(
                thread_id=self.thread.id, direction="in", message_type="text",
                status="received", timestamp=datetime.now(timezone.utc), text=t,
                wa_message_id=f"{wa_msg_id}.{i}"))
        self.db.commit()
        event_in = ConversationHandleIn(
            thread_id=self.thread.id, wa_message_id=f"{wa_msg_id}.{len(texts) - 1}",
            wa_id=LIVE_WA_ID, text=texts[-1], recent_user_messages=list(texts),
            unanswered_recent_user_messages=list(texts))
        return self.eng.handle(event_in)

    def acceptance_decisions(self):
        return [d for d in self.decisions
                if getattr(d, "rule_id", "") == "authorize.quote_acceptance"]

    def test_f7b_28_live_path_reads_the_wild_burst_as_acceptance(self):
        """The exact Wild burst, through handle(). stance must not be None."""
        self.turn("si", "para cuando tenes")
        decisions = self.acceptance_decisions()
        self.assertTrue(decisions, "the live path never reached authorize.quote_acceptance")
        self.assertEqual(
            decisions[0].stance, "ACCEPT",
            f"live stance was {decisions[0].stance!r} — the deadlock this milestone fixes")
        # And it got there with no semantic evidence at all.
        self.assertFalse(self.eng.settings.semantic_same_turn_enabled)

    def test_f7b_28b_live_acceptance_is_present_and_factual(self):
        """The prerequisite F6 was about, proven on the live path with no model call."""
        self.turn("si", "para cuando tenes")
        d = self.acceptance_decisions()[0]
        self.assertIn("acceptance_is_present_and_factual", d.satisfied)
        self.assertNotIn("acceptance_is_present_and_factual", d.failed)

    def test_f7b_28c_live_allows_once_the_quote_was_delivered(self):
        """With the delivered-quote prerequisite met, the turn advances.

        Delivery proof normally comes from the outbound ledger. The fixture has no prior
        outbound turn, so it is supplied here — and ONLY here — mirroring whatever quote
        the engine itself computed, rather than inventing an amount.
        """
        self.eng._delivered_quote_amounts = lambda ctx: (
            (self.eng._turn_price_quote.precio_total,)
            if getattr(self.eng, "_turn_price_quote", None) is not None else ())
        self.turn("si", "para cuando tenes")
        d = self.acceptance_decisions()[0]
        self.assertEqual(d.result, "ALLOW", d.reason)
        self.db.expire_all()
        self.assertEqual(self.lead.flag, "ACEPTADO")
        self.assertEqual(self.state.last_stage, "SCHEDULING")

    def test_f7b_29_live_path_authorizes_acceptance_exactly_once(self):
        """One burst, one acceptance decision — no duplicate transition."""
        self.turn("si", "para cuando tenes")
        self.assertEqual(len(self.acceptance_decisions()), 1,
                         "acceptance was authorized more than once for one burst")

    def test_f7b_30_live_conditional_yes_is_not_acceptance(self):
        """Safety holds on the live path too, not only in the producer."""
        self.turn("si consigo la plata")
        for d in self.acceptance_decisions():
            self.assertNotEqual(d.stance, "ACCEPT", "a conditional yes accepted the quote")

    def test_f7b_31_live_same_burst_conflict_does_not_accept(self):
        self.turn("si dale", "no, mejor no avancemos")
        for d in self.acceptance_decisions():
            self.assertNotEqual(d.stance, "ACCEPT", "conflict resolved as acceptance")

    def test_f7b_32_live_path_writes_no_booking(self):
        from app.models import ThreadRevision
        self.turn("si", "para cuando tenes")
        self.assertEqual(
            self.db.query(ThreadRevision).filter_by(status="booked").count(), 0)
