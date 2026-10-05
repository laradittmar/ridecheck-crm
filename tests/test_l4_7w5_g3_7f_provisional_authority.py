"""L4.7W5-GATE3-G3-7F Commit 3 — provisional correction authority.

A grounded correction is not automatically a safe one. G3-7C and G3-7D measured the
interpreter mis-assigning a referent while reporting CONFIRMED, so anything that moves a
price, an identity or a quote's validity now waits for the customer. Only a price-neutral
year applies immediately — and then the response must echo it, because a year reaches the
quote prose and `ThreadRevision.anio` even though it moves no money.

The hard requirement is the generic affirmation. A typed ACCEPT may apply a patch only when
canonical state proves it can mean nothing else: one outstanding proposal, no legacy
proposal active, the correction question was the last thing we said, the cycle and
before-values unmoved, the fingerprint matching, and no newer correction in the same turn.
Not one of those clauses is a word.

A  tiers, derived from source-proven impact
B  atomicity
C  the pending structure, fingerprint and strict parsing
D  persistence across a restart
E  confirmation authority, clause by clause
F  generic-affirmation safety
G  legacy mutual exclusion
H  rejection, supersession, expiry
I  the blocking predicate and the G3-8 interface
J  single-writer integrity and proposed-vs-canonical
K  trace and Inspector truth
L  flags, and no phrase catalogue
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT / "backend"), str(ROOT / "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import pending_correction as pc
from app.services.pending_correction import (CorrectionTier, ImpactClass,
                                             InvalidationReason, PendingCorrection,
                                             PendingStatus)


def state(**over):
    base = dict(pending_correction=None, pending_fuzzy_catalog_key=None,
                pending_location_proposal=None, pending_turn_evidence_text=None,
                flow_booking_token=None, last_offered_slots=None,
                active_requested_date=None, vehicle_clarification_sent=False,
                location_clarification_sent=False,
                inspectability_clarification_sent=False)
    base.update(over)
    return SimpleNamespace(**base)


def row(cid=1, **over):
    base = dict(id=cid, marca="Ford", modelo="Focus", anio=2019, tipo_vehiculo="AUTO",
                status="current_focus", zone_group="CABA", zone_detail="Palermo",
                version_text=None)
    base.update(over)
    return SimpleNamespace(**base)


_SENTINEL = object()

YEAR_ONLY = [("anio", 2019, 2018)]
WILD_B = [("anio", 2019, 2018), ("zone_detail", "Palermo", "Pilar"),
          ("zone_group", "CABA", "Norte")]


def out_row(mid=11, *, status="sent", wamid="wamid.CONFIRM1", thread_id=1,
            direction="out"):
    """The confirmation question's own outbound record.

    G3-7F-R2: `confirmation_admissible` re-reads this row, so every admission test must now
    supply it. `status="sent"` is the weakest state in `OUTBOUND_DISPATCH_ACCEPTED` — using
    the weakest one keeps these tests honest about what the allow-list actually permits.
    """
    return SimpleNamespace(id=mid, status=status, wa_message_id=wamid,
                           thread_id=thread_id, direction=direction)


def build(fields=WILD_B, *, cid=1, cycle="c1"):
    return PendingCorrection.build(
        thread_id=1, turn_id="t1", target_candidate_id=cid,
        grounding_basis="G2_SINGLETON", relation="CORRECT_EXISTING",
        target_scope="CURRENT_REFERENT", corrected_fields=fields,
        evidence_refs=("corrections[0]",), cycle_id=cycle,
        origin_message_ids=("m1",))


# ══ A — tiers ══════════════════════════════════════════════════════════════

class A_Tiers(unittest.TestCase):

    def test_g37f_76_year_is_the_only_immediate_field(self):
        """Derived, not chosen: `quote()` reads tipo and zone, `_catalog_tipo_for` takes no
        year, and `quote_inputs_unchanged()` never compares `anio`."""
        immediate = [f for f in ("anio", "zone_detail", "zone_group", "marca", "modelo",
                                 "tipo_vehiculo", "version_text")
                     if pc.classify_patch([(f, "a", "b")])[0] == CorrectionTier.IMMEDIATE]
        self.assertEqual(immediate, ["anio"])

    def test_g37f_77_every_supported_field_is_classified(self):
        expected = {
            "anio": (ImpactClass.NONE, False, False),
            "zone_detail": (ImpactClass.AFFECTS_PRICE, False, True),
            "zone_group": (ImpactClass.AFFECTS_PRICE, False, True),
            "tipo_vehiculo": (ImpactClass.AFFECTS_PRICE, False, True),
            "marca": (ImpactClass.AFFECTS_IDENTITY, True, False),
            "modelo": (ImpactClass.AFFECTS_IDENTITY, True, False),
        }
        for name, want in expected.items():
            with self.subTest(field=name):
                self.assertEqual(pc.classify_field(name), want)

    def test_g37f_78_an_unknown_field_is_treated_as_the_worst_case(self):
        impact, identity, invalidating = pc.classify_field("version_text")
        self.assertEqual(impact, ImpactClass.UNKNOWN)
        self.assertTrue(invalidating)
        self.assertEqual(pc.classify_patch([("version_text", None, "XEI")])[0],
                         CorrectionTier.PROVISIONAL)

    def test_g37f_79_an_empty_patch_is_not_actionable(self):
        self.assertEqual(pc.classify_patch([])[0], CorrectionTier.HOLD)

    def test_g37f_80_identity_reaches_price_through_the_catalogue(self):
        tier, price, identity, _inv = pc.classify_patch([("marca", "Ford", "Toyota")])
        self.assertEqual(tier, CorrectionTier.PROVISIONAL)
        self.assertEqual(price, ImpactClass.AFFECTS_PRICE)
        self.assertTrue(identity)


# ══ B — atomicity ══════════════════════════════════════════════════════════

class B_Atomic(unittest.TestCase):

    def test_g37f_81_one_tier2_entry_makes_the_whole_patch_tier2(self):
        """Splitting Wild Case B would leave 2018/Palermo — a state the customer never
        described, which the quote prose would then assert with fresh authority."""
        self.assertEqual(pc.classify_patch(WILD_B)[0], CorrectionTier.PROVISIONAL)

    def test_g37f_82_the_patch_reaches_the_writer_whole_or_not_at_all(self):
        p = build(WILD_B)
        data = p.writer_patch
        self.assertEqual(data["anio"], 2018)
        self.assertEqual(data["zone_detail"], "Pilar")
        self.assertEqual(data["zone_group"], "Norte")

    def test_g37f_83_a_correction_patch_never_carries_a_status(self):
        self.assertNotIn("status", build().writer_patch)

    def test_g37f_84_the_patch_names_exactly_one_row(self):
        self.assertEqual(build().writer_patch["id"], 1)


# ══ C — structure, fingerprint, strict parsing ═════════════════════════════

class C_Structure(unittest.TestCase):

    def test_g37f_85_build_records_everything_the_design_requires(self):
        p = build()
        for attr in ("pending_id", "thread_id", "turn_id", "target_candidate_id",
                     "grounding_basis", "relation", "target_scope", "corrected_fields",
                     "tier", "price_impact", "identity_impact", "quote_invalidating",
                     "evidence_refs", "cycle_id", "origin_message_ids", "created_at",
                     "status", "fingerprint"):
            with self.subTest(attr=attr):
                self.assertIsNotNone(getattr(p, attr))
        self.assertEqual(p.status, PendingStatus.PENDING)
        self.assertIsNone(p.invalidation_reason)

    def test_g37f_86_the_fingerprint_binds_row_fields_and_cycle(self):
        base = build()
        self.assertNotEqual(base.fingerprint, build(cid=2).fingerprint)
        self.assertNotEqual(base.fingerprint, build(cycle="c2").fingerprint)
        self.assertNotEqual(base.fingerprint, build(YEAR_ONLY).fingerprint)
        self.assertEqual(base.fingerprint, build().fingerprint)

    def test_g37f_87_the_payload_round_trips(self):
        p = build()
        back = PendingCorrection.from_payload(p.to_payload())
        self.assertEqual(back.pending_id, p.pending_id)
        self.assertEqual(back.corrected_fields, p.corrected_fields)
        self.assertEqual(back.fingerprint, p.fingerprint)

    def test_g37f_88_a_malformed_payload_is_not_a_half_understood_proposal(self):
        good = build().to_payload()
        for label, mutate in (
            ("not a dict", lambda d: "nope"),
            ("no pending_id", lambda d: {**d, "pending_id": None}),
            ("no fingerprint", lambda d: {**d, "fingerprint": ""}),
            ("bad status", lambda d: {**d, "status": "MAYBE"}),
            ("bad tier", lambda d: {**d, "tier": "SORT_OF"}),
            ("bad invalidation", lambda d: {**d, "invalidation_reason": "BECAUSE"}),
            ("non-int candidate", lambda d: {**d, "target_candidate_id": "1"}),
            ("field entry not a dict", lambda d: {**d, "corrected_fields": ["anio"]}),
            ("field entry has no name",
             lambda d: {**d, "corrected_fields": [{"before": 1, "proposed": 2}]}),
        ):
            with self.subTest(case=label):
                self.assertIsNone(PendingCorrection.from_payload(mutate(good)))

    def test_g37f_89_load_never_raises(self):
        for value in (None, "x", 7, [], {"pending_id": "a"}):
            with self.subTest(value=value):
                self.assertIsNone(pc.load(state(pending_correction=value)))


# ══ D — persistence ════════════════════════════════════════════════════════

class D_Persistence(unittest.TestCase):

    def test_g37f_90_a_pending_patch_survives_a_process_restart(self):
        """The engine is request-scoped, so the proposal has to be durable. This proves the
        round trip through a JSON column, which is what the DB stores."""
        stored = json.loads(json.dumps(build().to_payload()))
        back = pc.load(state(pending_correction=stored))
        self.assertIsNotNone(back)
        self.assertEqual(back.status, PendingStatus.PENDING)
        self.assertEqual([f for f, _b, _a in back.corrected_fields],
                         ["anio", "zone_detail", "zone_group"])

    def test_g37f_91_the_column_is_additive_nullable_and_json(self):
        import app.models as models
        col = models.WhatsAppThreadState.__table__.c.pending_correction
        self.assertTrue(col.nullable)
        self.assertIn("JSON", str(col.type).upper())

    def test_g37f_92_the_migration_is_additive_and_reversible(self):
        src = (ROOT / "backend/migrations/versions/20261005_pending_correction.py"
               ).read_text(encoding="utf-8")
        self.assertIn("op.add_column(", src)
        self.assertIn("op.drop_column(", src)
        self.assertIn("nullable=True", src)
        for destructive in ("drop_table", "alter_column", "drop_index", "drop_constraint"):
            with self.subTest(forbidden=destructive):
                self.assertNotIn(destructive, src)


# ══ E — confirmation authority ═════════════════════════════════════════════

class E_Confirmation(unittest.TestCase):

    def _admit(self, pending, *, st=None, candidates=None, cycle="c1", latest=11,
               newer=False, confirmation_row=_SENTINEL, thread_id=1,
               grounding_enabled=True):
        if confirmation_row is _SENTINEL:
            confirmation_row = out_row(pending.confirmation_message_id or latest)
        return pc.confirmation_admissible(
            pending, state=st or state(), candidates=candidates or [row()],
            cycle_id=cycle, latest_outbound_message_id=latest,
            newer_correction_this_turn=newer,
            confirmation_row=confirmation_row, thread_id=thread_id,
            grounding_enabled=grounding_enabled)

    def test_g37f_93_a_clean_confirmation_is_admissible(self):
        ok, why = self._admit(build().with_confirmation_message(11))
        self.assertTrue(ok)
        self.assertIsNone(why)

    def test_g37f_94_a_moved_before_value_invalidates_it(self):
        ok, why = self._admit(build().with_confirmation_message(11),
                              candidates=[row(anio=2017)])
        self.assertFalse(ok)
        self.assertEqual(why, InvalidationReason.BEFORE_VALUES_MOVED)

    def test_g37f_95_a_lost_target_invalidates_it(self):
        ok, why = self._admit(build().with_confirmation_message(11),
                              candidates=[row(cid=9)])
        self.assertFalse(ok)
        self.assertEqual(why, InvalidationReason.TARGET_LOST)

    def test_g37f_96_a_new_cycle_invalidates_it(self):
        ok, why = self._admit(build().with_confirmation_message(11), cycle="c2")
        self.assertFalse(ok)
        self.assertEqual(why, InvalidationReason.CYCLE_ENDED)

    def test_g37f_97_a_tampered_fingerprint_invalidates_it(self):
        import dataclasses
        p = dataclasses.replace(build().with_confirmation_message(11),
                                fingerprint="0" * 32)
        ok, why = self._admit(p)
        self.assertFalse(ok)
        self.assertEqual(why, InvalidationReason.FINGERPRINT_MISMATCH)

    def test_g37f_98_a_terminal_patch_is_never_confirmable_again(self):
        for status in PendingStatus.TERMINAL:
            with self.subTest(status=status):
                ok, _why = self._admit(
                    build().with_confirmation_message(11).with_status(status))
                self.assertFalse(ok)

    def test_g37f_99_confirming_is_idempotent(self):
        """A re-delivered affirmation finds the patch no longer PENDING and does nothing."""
        p = build().with_confirmation_message(11)
        self.assertTrue(self._admit(p)[0])
        applied = p.with_status(PendingStatus.CONFIRMED)
        self.assertFalse(self._admit(applied)[0])


# ══ F — generic-affirmation safety ═════════════════════════════════════════

class F_GenericAffirmation(unittest.TestCase):
    """The clause that makes a bare "sí" unambiguous is canonical, not lexical."""

    def _admit(self, pending, **kw):
        kw.setdefault("st", state())
        kw.setdefault("candidates", [row()])
        kw.setdefault("confirmation_row", out_row(
            pending.confirmation_message_id or kw.get("latest", 11)))
        return pc.confirmation_admissible(
            pending, state=kw["st"], candidates=kw["candidates"],
            confirmation_row=kw["confirmation_row"], thread_id=1,
            cycle_id=kw.get("cycle", "c1"),
            latest_outbound_message_id=kw.get("latest", 11),
            newer_correction_this_turn=kw.get("newer", False))

    def test_g37f_100_an_intervening_bot_message_invalidates_confirmation(self):
        """A quote, a slot, a Flow prompt or any later question means an affirmation has
        more than one referent — and ambiguity here is a wrong-car mutation."""
        ok, why = self._admit(build().with_confirmation_message(11), latest=12)
        self.assertFalse(ok)
        self.assertEqual(why, InvalidationReason.NOT_LATEST_QUESTION)

    def test_g37f_101_a_patch_that_was_never_asked_is_not_confirmable(self):
        ok, why = self._admit(build())            # confirmation_message_id is None
        self.assertFalse(ok)
        self.assertEqual(why, InvalidationReason.NOT_LATEST_QUESTION)

    def test_g37f_102_re_attaching_the_question_keeps_it_admissible(self):
        """FAQ plus confirmation in ONE message updates the stored id, so re-asking does
        not disarm the patch."""
        p = build().with_confirmation_message(11).with_confirmation_message(14)
        self.assertTrue(self._admit(p, latest=14)[0])

    def test_g37f_103_a_newer_correction_is_not_confirmed_in_the_same_turn(self):
        ok, why = self._admit(build().with_confirmation_message(11), newer=True)
        self.assertFalse(ok)
        self.assertEqual(why, InvalidationReason.NEWER_CORRECTION)

    def test_g37f_104_confirmation_reads_no_customer_words_at_all(self):
        """Word boundaries, not substrings: `flow_booking_token` contains "ok" and proves
        nothing. What must be absent is an affirmation VOCABULARY."""
        import inspect
        import re as _re
        body = inspect.getsource(pc.confirmation_admissible)
        for word in ("sí", "si", "dale", "ok", "oka", "correcto", "exacto", "confirmo",
                     "claro", "perfecto"):
            with self.subTest(word=word):
                self.assertEqual(_re.findall(rf"\b{word}\b", body, _re.I), [])
        self.assertNotIn("re.compile", body)
        self.assertNotIn("current_turn_text", body,
                         "confirmation must not look at raw customer text at all")


# ══ G — legacy mutual exclusion ════════════════════════════════════════════

class G_LegacyExclusion(unittest.TestCase):

    def test_g37f_105_every_legacy_confirmable_proposal_is_detected(self):
        for name in pc.LEGACY_PROPOSAL_FIELDS:
            value = True if name.endswith("_sent") else "something"
            with self.subTest(field=name):
                self.assertEqual(pc.legacy_proposal_active(state(**{name: value})), name)

    def test_g37f_106_a_clean_thread_has_no_legacy_proposal(self):
        self.assertIsNone(pc.legacy_proposal_active(state()))

    def test_g37f_107_a_legacy_proposal_blocks_confirmation(self):
        ok, why = pc.confirmation_admissible(
            build().with_confirmation_message(11),
            state=state(pending_fuzzy_catalog_key="Ford||Focus"), candidates=[row()],
            cycle_id="c1", latest_outbound_message_id=11,
            newer_correction_this_turn=False)
        self.assertFalse(ok)
        self.assertEqual(why, InvalidationReason.LEGACY_PROPOSAL_ACTIVE)

    def test_g37f_108_arming_refuses_rather_than_superseding_or_coexisting(self):
        """The bounded answer: no legacy workflow is rewritten, and two confirmable
        proposals never coexist.

        G3-7F-R2 replaced the blanket "is anything set" refusal with
        `proposal_disposition`, which is strictly narrower: a proposal the correction
        ANSWERS or INVALIDATES is withdrawn canonically, and only a sound, unanswered one
        refuses. The invariant under test is unchanged — arming never supersedes a legacy
        proposal and never coexists with one — so this now exercises the behaviour instead
        of grepping for the old call.
        """
        import inspect
        from app.services.conversation_engine import ConversationEngine as E
        body = inspect.getsource(E._arm_pending_correction)
        self.assertIn("proposal_disposition", body)
        self.assertIn("LEGACY_PROPOSAL_ACTIVE", body)
        self.assertNotIn("_FUZZY_ACCEPTANCE_RE", body)

        e = E.__new__(E)
        e.settings = SimpleNamespace(correction_target_grounding_enabled=True,
                                     provisional_correction_enabled=True)
        e._correlation_id = "t1"
        e._canonical_turn_burst = SimpleNamespace(ordered_message_ids=("m1",))
        e._reconciler_cycle_id = lambda _st: "c1"
        res = SimpleNamespace(
            target_candidate_id=1, grounding_basis="G2_SINGLETON",
            target_relation="CORRECT_EXISTING", target_scope_value="CURRENT_REFERENT",
            corrected_fields=tuple(YEAR_ONLY), correction_evidence_ref="corrections[0]",
            vehicle_evidence_ref=None, location_evidence_ref=None)
        ctx = SimpleNamespace(state=None, thread=SimpleNamespace(id=1),
                              candidates=[row()])

        # a SOUND, unanswered proposal a year correction neither answers nor invalidates
        st = state(inspectability_clarification_sent=True)
        ctx.state = st
        pending, refusal = e._arm_pending_correction(ctx, st, res)
        self.assertIsNone(pending, "a sound legacy proposal must refuse arming")
        self.assertEqual(refusal, pc.InvalidationReason.LEGACY_PROPOSAL_ACTIVE)
        self.assertTrue(st.inspectability_clarification_sent,
                        "refusing must not quietly consume the legacy proposal")

        # and with nothing outstanding it arms
        st2 = state()
        ctx.state = st2
        pending2, refusal2 = e._arm_pending_correction(ctx, st2, res)
        self.assertIsNotNone(pending2)
        self.assertIsNone(refusal2)

    def test_g37f_109_the_legacy_phrase_detectors_are_never_used_by_the_new_path(self):
        import inspect
        from app.services.conversation_engine import ConversationEngine as E
        for fn in (E._arm_pending_correction, E._request_correction_confirmation,
                   E._hold_for_correction_clarification, E._correction_tier,
                   E._latest_outbound_message_id):
            body = inspect.getsource(fn)
            with self.subTest(fn=fn.__name__):
                self.assertNotIn("_FUZZY_ACCEPTANCE_RE", body)
                self.assertNotIn("_FUZZY_REJECTION_RE", body)


# ══ H — rejection, supersession, expiry ════════════════════════════════════

class H_Lifecycle(unittest.TestCase):

    def test_g37f_110_every_status_and_reason_is_enumerated(self):
        self.assertEqual(set(PendingStatus.ALL),
                         {"PENDING", "CONFIRMED", "REJECTED", "SUPERSEDED", "EXPIRED"})
        self.assertIn(InvalidationReason.HUMAN_HANDOFF, InvalidationReason.ALL)
        self.assertIn(InvalidationReason.CUSTOMER_REJECTED, InvalidationReason.ALL)

    def test_g37f_111_a_transition_is_pure(self):
        p = build()
        moved = p.with_status(PendingStatus.REJECTED,
                              InvalidationReason.CUSTOMER_REJECTED)
        self.assertEqual(p.status, PendingStatus.PENDING, "the original is untouched")
        self.assertEqual(moved.status, PendingStatus.REJECTED)
        self.assertEqual(moved.invalidation_reason,
                         InvalidationReason.CUSTOMER_REJECTED)

    def test_g37f_112_a_terminal_patch_no_longer_blocks(self):
        for status in PendingStatus.TERMINAL:
            with self.subTest(status=status):
                self.assertFalse(build().with_status(status).blocking)

    def test_g37f_113_human_handoff_has_its_own_reason(self):
        p = build().with_status(PendingStatus.EXPIRED,
                                InvalidationReason.HUMAN_HANDOFF)
        self.assertFalse(p.blocking)
        self.assertEqual(p.invalidation_reason, InvalidationReason.HUMAN_HANDOFF)


# ══ I — the blocking predicate ═════════════════════════════════════════════

class I_Blocking(unittest.TestCase):

    def test_g37f_114_every_tier2_patch_blocks(self):
        for fields in ([("zone_detail", "Palermo", "Pilar")],
                       [("marca", "Ford", "Toyota")],
                       [("tipo_vehiculo", "AUTO", "SUV_4X4_DEPORTIVO")],
                       [("version_text", None, "XEI")],
                       WILD_B):
            with self.subTest(fields=[f for f, _b, _a in fields]):
                st = state(pending_correction=build(fields).to_payload())
                blocking, reason = pc.has_blocking_pending_correction(st)
                self.assertTrue(blocking)
                self.assertIn("pending_correction:PROVISIONAL", reason)

    def test_g37f_115_a_price_neutral_patch_does_not_block(self):
        st = state(pending_correction=build(YEAR_ONLY).to_payload())
        blocking, reason = pc.has_blocking_pending_correction(st)
        self.assertFalse(blocking)
        self.assertIsNone(reason)

    def test_g37f_116_no_patch_means_no_blocking(self):
        self.assertEqual(pc.has_blocking_pending_correction(state()), (False, None))

    def test_g37f_117_the_predicate_is_the_whole_g3_8_interface(self):
        """G3-7F supplies state and this predicate; enforcing it at the early-return gates
        is G3-8's work, and the trace says so rather than implying it is done."""
        self.assertTrue(callable(pc.has_blocking_pending_correction))
        import inspect
        self.assertIn("G3-8", inspect.getdoc(pc.has_blocking_pending_correction))


# ══ J — single writer, proposed vs canonical ═══════════════════════════════

class J_WriterIntegrity(unittest.TestCase):

    def test_g37f_118_the_module_writes_nothing(self):
        """Calls, not mentions: the module docstring names `_apply_candidate` precisely to
        record that it is the only writer, and saying so is not calling it."""
        src = (ROOT / "backend/app/services/pending_correction.py").read_text(
            encoding="utf-8")
        for forbidden in ("db.add(", "db.commit(", "db.flush(", "session.add(",
                          "_apply_candidate(", "setattr(", "op.add_column("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)

    def test_g37f_119_apply_candidate_is_still_the_only_writer(self):
        """Counting one name is not enough — a variant under another name is still a second
        writer, and the hostile control proved that slips past a name count. So: exactly one
        `_apply_candidate*` method exists, and the candidate-row construction sites are the
        four documented ones (the writer itself, the catalogue creator, the Flow/form
        fallback and the MOTO handoff record)."""
        import ast
        import app.services.conversation_engine as ce
        src = (ROOT / "backend/app/services/conversation_engine.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        engine_cls = next(n for n in ast.walk(tree)
                          if isinstance(n, ast.ClassDef) and n.name == "ConversationEngine")
        writers = sorted(n.name for n in engine_cls.body
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and n.name.startswith("_apply_candidate"))
        self.assertEqual(writers, ["_apply_candidate"],
                         f"a second candidate writer appeared: {writers}")
        self.assertEqual(src.count("WhatsAppThreadCandidate("), 4,
                         "a new candidate-row construction site appeared")

    def test_g37f_120_the_pending_patch_is_never_read_as_canonical_truth(self):
        """Canonical reads go to the candidate row. Nothing consults the proposal for a
        customer-visible fact."""
        src = (ROOT / "backend/app/services/conversation_engine.py").read_text(
            encoding="utf-8")
        for canonical in ("_compute_price_quote", "_commercial_state",
                          "_build_quote_reply"):
            idx = src.find(f"def {canonical}")
            if idx < 0:
                continue
            body = src[idx:idx + 3000]
            with self.subTest(fn=canonical):
                self.assertNotIn("pending_correction", body)

    def test_g37f_121_a_confirmed_patch_authorizes_exactly_one_writer_call(self):
        data = build(WILD_B).writer_patch
        self.assertEqual(data["action"], "update")
        self.assertEqual(len([k for k in data if k not in ("action", "id")]), 3)


# ══ K — trace and Inspector ════════════════════════════════════════════════

class K_Trace(unittest.TestCase):

    def test_g37f_122_the_trace_carries_the_proposal_and_what_it_blocked(self):
        from app.services.hybrid_trace import build_trace
        from app.schemas.hybrid_trace import CanonicalSnapshot, SemanticEvidence
        payload = build(WILD_B).to_payload()
        payload.update({"blocking": True, "blocking_reason": "pending_correction:…",
                        "enforced_at_gates": False})
        trace = build_trace(
            turn_id="t1", thread_id=1, lead_id=1, deployment_sha="x", started_at=None,
            completed_at=None, duration_ms=1, ordered_message_ids=("m1",),
            message_timestamps=(None,), burst_texts=["x"], semantic=SemanticEvidence(),
            ce_rules=(), reconciliations=(), before=CanonicalSnapshot(),
            after=CanonicalSnapshot(), action="REPLY", detail=None, answer_source="CE",
            outbound={}, pending_correction=payload)
        section = trace.to_payload()["pending_correction"]
        self.assertEqual(section["tier"], CorrectionTier.PROVISIONAL)
        self.assertEqual(section["status"], PendingStatus.PENDING)
        self.assertTrue(section["blocking"])
        self.assertFalse(section["enforced_at_gates"])

    def test_g37f_123_the_inspector_distinguishes_every_lifecycle_state(self):
        from app.ui.hybrid_trace_view import _pending_correction_card
        for status, fragment in (("PENDING", "esperando confirmación"),
                                 ("CONFIRMED", "confirmada y aplicada"),
                                 ("REJECTED", "rechazada"),
                                 ("SUPERSEDED", "reemplazada"),
                                 ("EXPIRED", "vencida")):
            with self.subTest(status=status):
                html_out = _pending_correction_card(
                    {"pending_correction": build().with_status(status).to_payload()})
                self.assertIn(fragment, html_out)

    def test_g37f_124_the_inspector_says_the_patch_is_only_proposed(self):
        from app.ui.hybrid_trace_view import _pending_correction_card
        html_out = _pending_correction_card(
            {"pending_correction": build(WILD_B).to_payload()})
        self.assertIn("PROPUESTO", html_out)
        self.assertIn("2019 → 2018", html_out)
        self.assertIn("Palermo → Pilar", html_out)

    def test_g37f_125_the_inspector_names_why_a_patch_stopped_being_confirmable(self):
        from app.ui.hybrid_trace_view import _pending_correction_card
        for reason, fragment in (
                (InvalidationReason.NOT_LATEST_QUESTION, "última pregunta"),
                (InvalidationReason.LEGACY_PROPOSAL_ACTIVE, "otra propuesta confirmable"),
                (InvalidationReason.BEFORE_VALUES_MOVED, "valores previos"),
                (InvalidationReason.HUMAN_HANDOFF, "humano")):
            with self.subTest(reason=reason):
                html_out = _pending_correction_card(
                    {"pending_correction": build().with_status(
                        PendingStatus.EXPIRED, reason).to_payload()})
                self.assertIn(fragment, html_out)

    def test_g37f_126_a_turn_with_no_proposal_renders_nothing(self):
        from app.ui.hybrid_trace_view import _pending_correction_card
        self.assertEqual(_pending_correction_card({"pending_correction": None}), "")

    def test_g37f_127_the_card_leaks_no_wamid_phone_or_prose(self):
        from app.ui.hybrid_trace_view import _pending_correction_card
        import re
        html_out = _pending_correction_card(
            {"pending_correction": build(WILD_B).to_payload()})
        self.assertEqual(re.findall(r"wamid\.[A-Za-z0-9_=-]{8,}", html_out), [])
        self.assertEqual(re.findall(r"\b549\d{8,}\b", html_out), [])
        for prose in ("Perdón", "no Palermo"):
            self.assertNotIn(prose, html_out)

    def test_g37f_128_the_page_includes_the_card(self):
        from app.ui.hybrid_trace_view import render_turn_trace_page
        from app.schemas.hybrid_trace import TRACE_VERSION
        page = render_turn_trace_page(
            {"captured": True, "turn_id": "t1",
             "trace": {"trace_version": TRACE_VERSION, "turn_id": "t1",
                       "pending_correction": build(WILD_B).to_payload()}}, "t1")
        self.assertIn("Corrección provisional", page)


# ══ L — flags and no phrases ═══════════════════════════════════════════════

class L_Flags(unittest.TestCase):

    def test_g37f_129_both_new_flags_default_false(self):
        import dataclasses
        from app.settings import Settings
        for name in ("correction_target_grounding_enabled",
                     "provisional_correction_enabled"):
            with self.subTest(flag=name):
                field = next(f for f in dataclasses.fields(Settings) if f.name == name)
                self.assertIs(field.default, False)

    def test_g37f_130_only_a_real_true_arms_provisional_authority(self):
        from app.services.conversation_engine import ConversationEngine as E
        for value, expected in ((True, True), (False, False), ("true", False),
                                (1, False), (None, False)):
            with self.subTest(value=value):
                e = E.__new__(E)
                e.settings = SimpleNamespace(provisional_correction_enabled=value)
                self.assertIs(e._provisional_correction_on(), expected)
        bare = E.__new__(E)
        bare.settings = SimpleNamespace()
        self.assertFalse(bare._provisional_correction_on())

    def test_g37f_131_the_whole_module_contains_no_phrase_catalogue(self):
        src = (ROOT / "backend/app/services/pending_correction.py").read_text(
            encoding="utf-8")
        self.assertNotIn("re.compile", src)
        for word in ("dale", "exacto", "correcto", "el otro", "no ese", "confirmo"):
            with self.subTest(word=word):
                self.assertNotIn(word, src.lower())

    def test_g37f_132_the_confirmation_question_repeats_only_corrected_facts(self):
        """G3-7F-R2: the wording moved to the composer, which is the point of G37FRR-02.

        The engine no longer owns a correction sentence at all, so this reads the renderer
        the response plan actually calls.
        """
        from app.services.response_composition import render_correction_confirmation
        from app.services.conversation_engine import ConversationEngine as E
        self.assertFalse(hasattr(E, "_render_correction_confirmation"),
                         "correction prose must not live in the engine")
        text = render_correction_confirmation(
            [(f, a) for f, _b, a in build(WILD_B).corrected_fields])
        self.assertIn("2018", text)
        self.assertIn("Pilar", text)
        for unrelated in ("Ford", "Focus", "$", "CABA"):
            with self.subTest(unrelated=unrelated):
                self.assertNotIn(unrelated, text)
        self.assertEqual(text.count("?"), 1, "one question, asked once")


if __name__ == "__main__":        # pragma: no cover
    unittest.main()
