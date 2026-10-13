"""G3-7N — a confirmed vehicle change withdraws the scheduling offer it invalidated.

G3-7P NOTE (2026-10-10). The INVARIANT this suite protects is unchanged and is now proven
more strongly; the MECHANISM it was written against was replaced. G3-7N revoked an offer by
writing a truthy tombstone into `flow_booking_token`, and its independent review measured
two costs: the tombstone is a non-empty string, so the eight production sites that read that
column for truthiness reported a WITHDRAWN offer as outstanding and re-armed L4.7W5-F7A; and
the three refusal guards were asserted here only by `inspect.getsource` + `assertIn`, so
disabling each one left the entire suite green (104 -> 104 identities, NEW=[], GONE=[]).

Authority therefore moved to `whatsapp_booking_offers` and the mirror went back to meaning
one thing. The tests below that named the tombstone now name the offer row, and the
source-string refusal tests are superseded by behavioural ones in
`test_l4_7w5_g3_7p_offer_authority.py` and `..._pg.py`, which assert on state and fail when
a guard is removed. Nothing that was true stopped being asserted; it is asserted better.


F1, the defect this closes. G3-7M reset the stage and the lead flag on a vehicle change but
left `flow_booking_token`, `last_offered_slots`, `last_visible_slots`,
`active_requested_date`, `preferred_day` and `preferred_time` **live**. Reproduced on real
PostgreSQL: a customer mid-scheduling for a Ford who clarified to a Peugeot could then tap
the superseded booking Flow and create a Revision — a booking for a car they no longer had.

Clearing the token would not have fixed it. The booking path's check is
`if expected and flow_token and flow_token != expected`, so a NULL `expected`
short-circuits and a late tap sails through. Revocation therefore writes a TOMBSTONE —
`withdrawn-offer-<sha256 prefix>` — which is non-null and unmatchable, and which survives
process restart, duplicate delivery and delayed submission because it is a persisted column.

Two further holes the late-tap matrix found, both from the same warn-only comparison: an
old token regained authority the moment a NEW offer overwrote the tombstone, and a token
carried to a different thread was accepted there because that thread had nothing to compare
against. Both are now refused — `SUPERSEDED_OFFER` and `FOREIGN_OFFER`.

F2 (G3-7M audit): the completion handler's obedience to the resolved target had no
behavioural test. `test_g7n_20` asserts on canonical rows, so restoring `focus.id` fails on
state rather than on source text.

Late-tap, restart, concurrency and new-booking proofs run against real PostgreSQL and are
recorded in the closeout; these tests pin the rules and the handler contract.
"""
from __future__ import annotations

import inspect
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from app.services.conversation_engine import ConversationEngine as CE
from app.services.offer_lifecycle import OfferLifecycle, OfferStatus, WithdrawReason
import test_m18_business_logic as M18


SCHEDULING_FIELDS = ("last_offered_slots", "last_visible_slots", "active_requested_date",
                     "last_requested_time", "preferred_day", "preferred_time")


def offered_state(**over):
    st = M18._make_state(vehicle_fallback_flow_sent=True)
    st.flow_booking_token = "37-1760000000"
    st.last_offered_slots = '["2026-10-13T10:00"]'
    st.last_visible_slots = '["2026-10-13T10:00"]'
    st.active_requested_date = "2026-10-13"
    st.last_requested_time = "10:00"
    st.preferred_day = "2026-10-13"
    st.preferred_time = "10:00"
    st.last_stage = "SCHEDULING"
    st.current_revision_id = 55
    for k, v in over.items():
        setattr(st, k, v)
    return st


def completion(cands, payload, state=None):
    """Run the real completion handler with transport replaced at the send boundary."""
    eng = M18._make_engine()
    eng.db.add = lambda x: None
    eng.db.flush = lambda: None
    eng.db.commit = lambda: None
    eng._send_text_to_wa = lambda ctx, text, **k: "wamid"
    eng._send_fallback_human_review_notification = lambda *a, **k: None
    eng._compute_price_quote = lambda ctx, s: None
    eng._dispatch_location_flow_direct = lambda *a, **k: None
    st = state if state is not None else offered_state()
    ctx = M18._make_ctx(candidates=list(cands), state=st)
    eng._process_vehicle_fallback_response(ctx, st, payload)
    return eng, ctx, st


PEUGEOT = {"marca": "Peugeot", "modelo": "208", "anio": "2016", "tipo_vehiculo": "AUTO"}


# ══ A · the withdrawal authority ══════════════════════════════════════════════════════
class G7N_Withdrawal(unittest.TestCase):

    def two_cars(self):
        return [M18._make_candidate(id=1, marca="Ford", modelo="Focus", anio=2019,
                                    tipo_vehiculo="AUTO", status="current_focus"),
                M18._make_candidate(id=2, marca="Peugeot", modelo="208", anio=None,
                                    tipo_vehiculo="AUTO", status="mentioned")]

    def test_g7n_01_a_vehicle_change_withdraws_every_dependent_field(self):
        _eng, _ctx, st = completion(self.two_cars(), PEUGEOT)
        for f in SCHEDULING_FIELDS:
            with self.subTest(field=f):
                self.assertIsNone(getattr(st, f), f"{f} must not survive a vehicle change")

    def test_g7n_02_the_mirror_is_cleared_because_authority_moved(self):
        """G3-7P: clearing is now correct. The tombstone existed only because a NULL
        `expected` short-circuited the booking check; the offer row answers that question
        now, so a cleared mirror grants nothing and keeps the F7A gate open."""
        self.assertTrue(hasattr(CE, "_withdraw_booking_offer"))
        import app.services.conversation_engine as ce
        self.assertFalse(hasattr(ce, "_WITHDRAWN_OFFER_TOKEN_PREFIX"))

    def test_g7n_03_revocation_is_a_terminal_status_not_a_magic_string(self):
        self.assertEqual(OfferStatus.WITHDRAWN, "WITHDRAWN")
        self.assertIn(OfferStatus.WITHDRAWN, OfferStatus.TERMINAL)
        self.assertEqual(WithdrawReason.VEHICLE_CHANGED, "VEHICLE_CHANGED")

    def test_g7n_04_only_a_fingerprint_of_the_token_is_kept(self):
        from app.services.offer_lifecycle import token_fingerprint
        fp = token_fingerprint("37-1760000000")
        self.assertEqual(len(fp), 64)
        self.assertNotIn("37-1760000000", fp)

    def test_g7n_05_unrelated_evidence_is_preserved(self):
        """A vehicle change is not a cycle reset."""
        st = offered_state()
        st.home_zone_group, st.home_zone_detail = "CABA", "Palermo"
        st.current_cycle_start_message_db_id = 91
        _eng, ctx, st2 = completion(self.two_cars(), PEUGEOT, state=st)
        self.assertEqual((st2.home_zone_group, st2.home_zone_detail), ("CABA", "Palermo"))
        self.assertEqual(st2.current_cycle_start_message_db_id, 91)
        self.assertEqual(len(ctx.candidates), 2, "candidate evidence preserved")

    def test_g7n_06_a_booked_revision_pointer_is_not_touched(self):
        """`current_revision_id` is history, not an offer, and this path never books."""
        _eng, _ctx, st = completion(self.two_cars(), PEUGEOT)
        self.assertEqual(st.current_revision_id, 55)

    def test_g7n_07_withdrawal_is_one_canonical_operation(self):
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        self.assertEqual(src.count("_withdraw_booking_offer("), 1)

    def test_g7n_08_it_does_not_run_when_the_target_is_unresolved(self):
        ambiguous = [M18._make_candidate(id=1, marca="Peugeot", modelo="208", anio=None,
                                         tipo_vehiculo="AUTO", status="current_focus"),
                     M18._make_candidate(id=2, marca="Peugeot", modelo="208", anio=None,
                                         tipo_vehiculo="AUTO", status="mentioned")]
        _eng, _ctx, st = completion(ambiguous, PEUGEOT)
        self.assertEqual(st.flow_booking_token, "37-1760000000",
                         "an unresolved target writes nothing, so it withdraws nothing")
        self.assertIsNotNone(st.last_offered_slots)

    def test_g7n_09_it_does_not_run_on_a_motorcycle_handoff(self):
        eng = M18._make_engine()
        eng.db.commit = lambda: None
        eng._send_text_to_wa = lambda ctx, text, **k: "wamid"
        eng._send_fallback_human_review_notification = lambda *a, **k: None
        hit = {"n": 0}
        eng._motorcycle_human_handoff = lambda *a, **k: (
            hit.update(n=1), types.SimpleNamespace(action="handoff"))[1]
        st = offered_state()
        ctx = M18._make_ctx(candidates=self.two_cars(), state=st)
        eng._process_vehicle_fallback_response(
            ctx, st, {"marca": "Honda", "modelo": "Titan", "anio": "2020",
                      "tipo_vehiculo": "MOTO"})
        self.assertEqual(hit["n"], 1)
        self.assertEqual(st.flow_booking_token, "37-1760000000")

    def test_g7n_10_the_reason_is_typed_and_traced(self):
        """G3-7P: the reason is a typed `WithdrawReason` recorded on the offer row
        (`terminal_reason`) rather than a write-only `_turn_*` attribute, and the per-turn
        field that remains IS read — by the withdrawal decision row. Behavioural proof that
        the reason reaches the database: `test_g7p_pg_11` / `test_g7p_pg_12`."""
        eng, _ctx, _st = completion(self.two_cars(), PEUGEOT)
        reason, _count = getattr(eng, "_turn_offer_withdrawal", (None, None))
        self.assertEqual(reason, WithdrawReason.VEHICLE_CHANGED)


# ══ B · durable revocation at the booking boundary ═══════════════════════════════════
class G7N_Revocation(unittest.TestCase):

    def test_g7n_11_a_withdrawn_offer_is_refused_before_any_write(self):
        """Still true, and G3-7R made the order it is asserted on STRONGER.

        This used to require `_offers.claim(` before `buyer_email` — acceptance decided
        before a booking field is read. G3-7R split that one decision into two: ADMISSION,
        which is the boundary and now happens before ANY field is parsed, and the claim,
        which is the terminal transition and happens after the final availability
        revalidation. So the assertion moves to the boundary, where it belongs, and gains a
        second clause: nothing may be WRITTEN before the claim either.

        The reordering is why revalidation can leave a refused offer ISSUED instead of
        relying on a rollback to undo an acceptance it should never have made. The only
        parsed field revalidation receives is the address, and `ScheduleService.check()`
        does not read it — so no customer-supplied value influences admission or
        availability.

        The BEHAVIOUR is proven in `test_l4_7w5_g3_7p_offer_authority_pg.py::test_g7p_pg_07`.
        """
        src = inspect.getsource(CE._process_flow_response)
        self.assertLess(src.index("_offers.admit("), src.index("buyer_email"),
                        "admission must be decided before a booking field is read")
        self.assertLess(src.index("_offers.claim("), src.index("thread_rev = ThreadRevision("),
                        "acceptance must be decided before a booking row is built")

    def test_g7n_12_the_refusal_precedes_booking_processing(self):
        src = inspect.getsource(CE._process_flow_response)
        self.assertLess(src.index("if not _adm.granted:"), src.index("buyer_email"))
        self.assertLess(src.index("if not _claim.ok:"),
                        src.index("thread_rev = ThreadRevision("))

    def test_g7n_13_a_foreign_token_is_refused_not_warned(self):
        """Superseded by behavioural proof: `test_g7p_pg_06` (foreign thread) and
        `test_g7p_13`. The classification still exists and is typed."""
        from app.services.offer_lifecycle import OfferRefusal
        self.assertEqual(OfferRefusal.FOREIGN_OFFER, "FOREIGN_OFFER")

    def test_g7n_14_a_superseded_token_is_refused_not_warned(self):
        """Superseded by `test_g7p_pg_08` and `test_g7p_12`."""
        from app.services.offer_lifecycle import OfferRefusal
        self.assertEqual(OfferRefusal.SUPERSEDED_OFFER, "SUPERSEDED_OFFER")

    def test_g7n_15_no_warn_only_path_remains(self):
        """The old comparison only logged, which is why an old token regained authority
        once a new offer overwrote the tombstone. There is now no path where a mismatched
        token is merely warned about."""
        src = inspect.getsource(CE._process_flow_response)
        self.assertNotIn("flow token mismatch", src)
        # G3-7P: there is no comparison left to warn about. G3-7R: and there is no
        # turn-entry prediction left either. The boundary is one typed admission that
        # either grants, replays or refuses, decided before any booking field is parsed,
        # and the terminal claim happens before any booking row is built. Neither path can
        # fall through to the booking code.
        self.assertLess(src.index("_offers.admit("), src.index("buyer_email"))
        self.assertIn("if not _adm.granted:", src)
        self.assertIn("if not _claim.ok:", src)

    def test_g7n_16_the_refusal_survives_a_reload(self):
        """The durable half moved from a string in one column to a row with a status. Proven
        across a real restart in `test_g7p_pg_14`."""
        from app.services.offer_lifecycle import OfferLifecycle as OL
        for name in ("claim", "withdraw", "issue", "supersede", "consume"):
            self.assertTrue(callable(getattr(OL, name)), name)

class G7N_Atomicity(unittest.TestCase):

    def test_g7n_17_candidate_write_and_withdrawal_share_one_transaction(self):
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        w = src.index("_withdraw_booking_offer(")
        a = src.index("_apply_candidate(")
        self.assertLess(a, w, "the candidate write comes first")
        self.assertNotIn("self.db.commit()", src[a:w],
                         "no commit may separate the write from the withdrawal")

    def test_g7n_18_withdrawal_runs_before_the_commit_that_makes_both_authoritative(self):
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        w = src.index("_withdraw_booking_offer(")
        self.assertIn("self.db.commit()", src[w:], "a commit must follow the withdrawal")

    def test_g7n_19_withdrawal_itself_never_commits_or_sends(self):
        # CODE, not comments: G3-7R's comment here NAMES `_apply_candidate`, to explain why
        # a terminal reason may need refining when that method withdrew first in the same
        # turn. An oracle that cannot tell a comment from a call would make that
        # explanation unwritable.
        import ast
        import textwrap
        src = ast.unparse(ast.parse(textwrap.dedent(
            inspect.getsource(CE._withdraw_booking_offer))))
        for forbidden in ("commit(", "_send_text_to_wa", "_apply_candidate("):
            self.assertNotIn(forbidden, src)


# ══ D · F2 — handler obedience, asserted on canonical rows ═══════════════════════════
class G7N_HandlerObedience(unittest.TestCase):
    """G3-7M audit F2: restoring `id=focus.id` reintroduced the destructive overwrite with
    42/42 passing, because the tests asserted the resolver's DECISION. These assert the
    rows that actually changed."""

    def rows(self):
        return [M18._make_candidate(id=1, marca="Ford", modelo="Focus", anio=2019,
                                    tipo_vehiculo="AUTO", status="current_focus"),
                M18._make_candidate(id=2, marca="Peugeot", modelo="208", anio=None,
                                    tipo_vehiculo="AUTO", status="mentioned")]

    def test_g7n_20_the_row_that_changes_is_the_resolved_target(self):
        cands = self.rows()
        before_ford = (cands[0].marca, cands[0].modelo, cands[0].anio)
        eng, ctx, _st = completion(cands, PEUGEOT)
        target = getattr(eng, "_turn_completion_target", None)
        self.assertEqual(target.target_candidate_id, 2)
        ford = next(c for c in ctx.candidates if c.id == 1)
        peugeot = next(c for c in ctx.candidates if c.id == 2)
        # the Ford keeps its identity — only its focus status may move
        self.assertEqual((ford.marca, ford.modelo, ford.anio), before_ford,
                         "the non-target row must not be rewritten")
        # the resolved target receives the submitted year
        self.assertEqual((peugeot.marca, peugeot.modelo, peugeot.anio),
                         ("Peugeot", "208", 2016))
        self.assertEqual(len(ctx.candidates), 2, "row count must be stable")

    def test_g7n_21_the_writer_input_carries_the_resolved_id(self):
        captured = {}
        eng = M18._make_engine()
        eng.db.add = lambda x: None
        eng.db.flush = lambda: None
        eng.db.commit = lambda: None
        eng._send_text_to_wa = lambda ctx, text, **k: "wamid"
        eng._send_fallback_human_review_notification = lambda *a, **k: None
        eng._compute_price_quote = lambda ctx, s: None
        eng._dispatch_location_flow_direct = lambda *a, **k: None
        real = eng._apply_candidate

        def _spy(ctx, data, **kw):
            captured.update(data)
            return real(ctx, data, **kw)
        eng._apply_candidate = _spy
        st = offered_state()
        ctx = M18._make_ctx(candidates=self.rows(), state=st)
        eng._process_vehicle_fallback_response(ctx, st, PEUGEOT)
        self.assertEqual(captured.get("action"), "update")
        self.assertEqual(captured.get("id"), 2,
                         "the handler must pass the RESOLVED id, never focus.id")

    def test_g7n_22_focus_moves_to_the_resolved_target(self):
        _eng, _ctx, st = completion(self.rows(), PEUGEOT)
        self.assertEqual(st.current_focus_candidate_id, 2)


# ══ E · the approved acknowledgement ═════════════════════════════════════════════════
class G7N_Acknowledgement(unittest.TestCase):

    APPROVED = ("Gracias por completarlo. Un representante de RideCheck "
                "se estará comunicando con vos.")

    def test_g7n_23_the_copy_is_exactly_the_owner_approved_sentence(self):
        """Owner policy, G3-7N §13: Track A's successful-completion acknowledgement is
        this sentence verbatim. The previous "se va a comunicar" variant is retired."""
        src = inspect.getsource(CE)
        self.assertIn("se estará comunicando con vos.", src)
        self.assertNotIn("Un representante de RideCheck se va a comunicar", src)

    def test_g7n_24_it_appears_exactly_once(self):
        src = inspect.getsource(CE)
        self.assertEqual(src.count("Gracias por completarlo"), 1)

    def test_g7n_25_the_ack_is_not_used_for_a_refused_submission(self):
        src = inspect.getsource(CE._process_flow_response)
        # The slice must START at the refusal branch and END at the first `replied` return
        # AFTER it. G3-7P-R3 added an earlier `replied` return — the typed outcome for a
        # submission that lost the lock race — and an unanchored `index` picked that one up,
        # producing an EMPTY slice that asserted nothing at all.
        # G3-7R routed EVERY refusal on this path — admission, contention, revalidation
        # and acceptance — through one helper, so the property is asserted on that helper
        # rather than on a source slice. One place to read means the four refusals cannot
        # drift into different customer experiences, which is what a slice-based oracle
        # could never have noticed.
        for branch in ("if not _adm.granted:", "if not _claim.ok:",
                       "_refuse_booking_submission("):
            self.assertIn(branch, src, branch)
        refusal = inspect.getsource(CE._refuse_booking_submission)
        self.assertNotIn("_FLOW_COMPLETION_ACK", refusal)
        self.assertIn("_FALLBACK_WARM_HANDOFF", refusal)
        # And the completion acknowledgement is planned only on the path that committed.
        self.assertIn("_plan_flow_completion_ack", src)
        self.assertNotIn("_plan_flow_completion_ack", refusal)

class G7N_PreservedSafety(unittest.TestCase):

    def test_g7n_26_no_revision_or_booking_on_the_completion_path(self):
        import ast
        import textwrap
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(CE._process_vehicle_fallback_response)))
        node = tree.body[0]
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        code = "\n".join(ast.unparse(s) for s in body)
        for forbidden in ("ThreadRevision(", "_send_booking_flow", "AGENDADO"):
            self.assertNotIn(forbidden, code)

    def test_g7n_27_one_candidate_writer_on_the_path(self):
        src = inspect.getsource(CE._process_vehicle_fallback_response)
        self.assertEqual(src.count("_apply_candidate("), 1)

    def test_g7n_28_no_phrase_list_entered_the_withdrawal(self):
        import ast
        import textwrap
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(CE._withdraw_booking_offer)))
        node = tree.body[0]
        body = [s for s in node.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        code = "\n".join(ast.unparse(s) for s in body).lower()
        for token in ("otro", "al final", "perdon", "gracias"):
            self.assertNotIn(token, code)

    def test_g7n_29_b15_remains_an_open_w3_defect(self):
        cands = [M18._make_candidate(id=1, marca="Peugeot", modelo="2008", anio=2014,
                                     tipo_vehiculo="AUTO", status="current_focus")]
        eng, ctx, _st = completion(cands, {"marca": "Ford", "modelo": "Focus",
                                           "anio": None, "tipo_vehiculo": "AUTO"})
        self.assertIsNotNone(getattr(eng, "_turn_completion_target", None),
                             "G3-7N did not change B15's class of defect")


if __name__ == "__main__":
    unittest.main(verbosity=2)
