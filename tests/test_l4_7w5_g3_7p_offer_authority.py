"""G3-7P — the offer lifecycle, asserted on real rows (SQLite in-memory).

No test in this file asserts on source text. The G3-7N independent review measured why that
matters: its three central guards were pinned only by `inspect.getsource` + `assertIn`, so
disabling each one left the whole suite green — the mutations changed the CONDITION and left
the string literal in place. Everything here therefore reads state back out of a database.

Concurrency is NOT claimed here. The live-offer invariant is a partial unique index and
acceptance is a conditional UPDATE under FOR UPDATE; both are proven in
`test_l4_7w5_g3_7p_offer_authority_pg.py` against real PostgreSQL.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base  # noqa: E402
import app.models as M  # noqa: E402
from app.services.offer_lifecycle import (  # noqa: E402
    OFFER_CONTRACT_VERSION, OfferLifecycle, OfferRefusal, OfferStatus,
    VEHICLE_DEPENDENT_SCHEDULING_FIELDS, WithdrawReason, token_fingerprint,
    _as_date, _as_time)

TOKEN = "7-1790000000-abcdef0123456789"


class OfferLifecycleRows(unittest.TestCase):
    """Every transition, read back from the database."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool,
            connect_args={"check_same_thread": False})
        # Take the metadata from a MODEL CLASS, not from the imported `Base`. Three suites
        # in this corpus call `Base.metadata.clear()` / `drop_all`, so a module that trusts
        # the shared registry builds an empty schema when it happens to run after one of
        # them — this suite passed alone and failed inside the full run for exactly that
        # reason. The class's own metadata always carries its table.
        M.WhatsAppBookingOffer.metadata.create_all(self.engine)
        self.SF = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.SF()
        ct = M.WhatsAppContact(wa_id="549110000001", display_name="P")
        self.db.add(ct); self.db.flush()
        lead = M.Lead(estado="CONSULTA_NUEVA"); self.db.add(lead); self.db.flush()
        th = M.WhatsAppThread(contact_id=ct.id, lead_id=lead.id)
        self.db.add(th); self.db.flush()
        st = M.WhatsAppThreadState(thread_id=th.id); self.db.add(st); self.db.flush()
        cand = M.WhatsAppThreadCandidate(thread_id=th.id, marca="Ford", modelo="Focus",
                                         tipo_vehiculo="AUTO", status="confirmed")
        self.db.add(cand); self.db.flush()
        st.current_focus_candidate_id = cand.id
        st.home_zone_group, st.home_zone_detail = "CABA", "Palermo"
        for f in VEHICLE_DEPENDENT_SCHEDULING_FIELDS:
            setattr(st, f, "2026-11-10" if "date" in f or "day" in f else "10:00")
        st.last_offered_slots = st.last_visible_slots = '["2026-11-10T10:00"]'
        self.db.commit()
        self.thread_id, self.cand_id, self.state = th.id, cand.id, st
        self.offers = OfferLifecycle(self.db)

    def tearDown(self):
        self.db.close()

    def _issue(self, token=TOKEN, **kw):
        kw.setdefault("candidate_id", self.cand_id)
        # R2: an offer carries the pricing context it was quoted from, and an offer with no
        # category cannot be accepted at all — a booking with no price basis is the harm the
        # category binding exists to prevent. The fixture's candidate is an AUTO.
        kw.setdefault("tipo_vehiculo", "AUTO")
        kw.setdefault("zone_group", "CABA")
        kw.setdefault("zone_detail", "Palermo")
        offer = self.offers.issue(self.state, thread_id=self.thread_id, token=token,
                                  cycle_key=0, **kw)
        self.db.commit()
        return offer

    def _rows(self):
        return [(o.id, o.status, o.terminal_reason)
                for o in self.db.execute(select(M.WhatsAppBookingOffer)
                                         .order_by(M.WhatsAppBookingOffer.id)).scalars()]

    # ── issue ────────────────────────────────────────────────────────────────

    def test_g7p_01_issue_creates_one_live_offer_and_sets_the_mirror(self):
        offer = self._issue()
        self.assertEqual(offer.status, OfferStatus.ISSUED)
        self.assertEqual(self.state.flow_booking_token, TOKEN)
        self.assertEqual(offer.contract_version, OFFER_CONTRACT_VERSION)
        self.assertEqual(offer.candidate_id, self.cand_id)
        self.assertEqual((offer.zone_group, offer.zone_detail), ("CABA", "Palermo"))
        self.assertIsNone(offer.terminal_at)

    def test_g7p_02_the_raw_token_is_never_persisted(self):
        offer = self._issue()
        self.assertEqual(offer.token_fingerprint, token_fingerprint(TOKEN))
        self.assertEqual(len(offer.token_fingerprint), 64)
        row = self.db.execute(
            select(M.WhatsAppBookingOffer).where(M.WhatsAppBookingOffer.id == offer.id)
        ).scalars().one()
        self.assertNotIn(TOKEN, str(row.__dict__))

    def test_g7p_03_issuing_again_supersedes_the_previous_offer(self):
        first = self._issue()
        second = self._issue(token="7-1790000001-second")
        rows = {o[0]: (o[1], o[2]) for o in self._rows()}
        self.assertEqual(rows[first.id], (OfferStatus.SUPERSEDED,
                                          WithdrawReason.REPLACED_BY_NEW_OFFER))
        self.assertEqual(rows[second.id][0], OfferStatus.ISSUED)
        self.assertEqual(self.state.flow_booking_token, "7-1790000001-second")

    def test_g7p_04_expiry_comes_from_the_existing_token_policy(self):
        from app.services.booking_flow_service import TOKEN_MAX_AGE_SECONDS
        offer = self._issue()
        self.assertIsNotNone(offer.expires_at)
        delta = offer.expires_at - offer.issued_at
        self.assertEqual(int(delta.total_seconds()), int(TOKEN_MAX_AGE_SECONDS))

    # ── withdrawal ───────────────────────────────────────────────────────────

    def test_g7p_05_withdrawal_clears_the_mirror_and_the_one_field_list(self):
        self._issue()
        n = self.offers.withdraw(self.state, thread_id=self.thread_id,
                                 reason=WithdrawReason.VEHICLE_CHANGED)
        self.db.commit()
        self.assertEqual(n, 1)
        self.assertEqual(self._rows()[0][1:], (OfferStatus.WITHDRAWN,
                                               WithdrawReason.VEHICLE_CHANGED))
        self.assertIsNone(self.state.flow_booking_token)
        for field in VEHICLE_DEPENDENT_SCHEDULING_FIELDS:
            self.assertIsNone(getattr(self.state, field), field)

    def test_g7p_06_withdrawal_can_preserve_the_scheduling_facts(self):
        """F7A hands the thread to a person; it does not discard what the customer said
        about when they are available."""
        self._issue()
        self.offers.withdraw(self.state, thread_id=self.thread_id,
                             reason=WithdrawReason.OFFER_REJECTED, clear_scheduling=False)
        self.db.commit()
        self.assertIsNone(self.state.flow_booking_token)
        self.assertIsNotNone(self.state.preferred_day)

    def test_g7p_07_withdrawal_is_idempotent_and_never_rewrites_history(self):
        offer = self._issue()
        self.offers.withdraw(self.state, thread_id=self.thread_id,
                             reason=WithdrawReason.VEHICLE_CHANGED)
        self.db.commit()
        again = self.offers.withdraw(self.state, thread_id=self.thread_id,
                                     reason=WithdrawReason.CYCLE_RESET)
        self.db.commit()
        self.assertEqual(again, 0)
        self.assertEqual(self._rows()[0][2], WithdrawReason.VEHICLE_CHANGED)

    def test_g7p_08_withdrawing_with_nothing_live_is_not_an_error(self):
        self.assertEqual(self.offers.withdraw(self.state, thread_id=self.thread_id,
                                              reason=WithdrawReason.VEHICLE_CHANGED), 0)

    # ── acceptance and its refusals ──────────────────────────────────────────

    def _claim(self, token, **kw):
        kw.setdefault("candidate_id", self.cand_id)
        kw.setdefault("tipo_vehiculo", "AUTO")      # R2: the pricing context is bound
        kw.setdefault("zone_group", "CABA")
        kw.setdefault("zone_detail", "Palermo")
        kw.setdefault("cycle_key", 0)
        return self.offers.claim(self.state, thread_id=self.thread_id, token=token, **kw)

    def test_g7p_09_a_live_bound_offer_is_claimed_once(self):
        offer = self._issue()
        claim = self._claim(TOKEN)
        self.db.commit()
        self.assertTrue(claim.ok)
        self.assertEqual(claim.offer_id, offer.id)
        self.assertEqual(self._rows()[0][1], OfferStatus.ACCEPTED)
        second = self._claim(TOKEN)
        self.assertFalse(second.ok)
        self.assertEqual(second.refusal, OfferRefusal.OFFER_ALREADY_ACCEPTED)

    def test_g7p_10_missing_evidence_is_never_permission(self):
        """The whole defect G3-7P closes: `expected IS NULL` used to accept anything."""
        self.assertIsNone(self.state.flow_booking_token)
        for token, expected in ((None, OfferRefusal.NO_TOKEN),
                                ("", OfferRefusal.NO_TOKEN),
                                ("   ", OfferRefusal.NO_TOKEN),
                                ("fabricated", OfferRefusal.UNKNOWN_TOKEN)):
            with self.subTest(token=token):
                claim = self._claim(token)
                self.assertFalse(claim.ok)
                self.assertEqual(claim.refusal, expected)

    def test_g7p_11_a_mirror_without_an_authority_row_refuses(self):
        """A token left by an application older than this table authorizes nothing."""
        self.state.flow_booking_token = "7-1700000000-legacy"
        self.db.commit()
        claim = self._claim("7-1700000000-legacy")
        self.assertFalse(claim.ok)
        self.assertEqual(claim.refusal, OfferRefusal.NO_OFFER_RECORD)

    def test_g7p_12_each_terminal_status_has_its_own_honest_refusal(self):
        cases = (
            (OfferStatus.WITHDRAWN, OfferRefusal.OFFER_WITHDRAWN),
            (OfferStatus.SUPERSEDED, OfferRefusal.SUPERSEDED_OFFER),
            (OfferStatus.ACCEPTED, OfferRefusal.OFFER_ALREADY_ACCEPTED),
            (OfferStatus.EXPIRED, OfferRefusal.OFFER_EXPIRED),
        )
        for status, refusal in cases:
            with self.subTest(status=status):
                self.setUp()
                offer = self._issue()
                offer.status = status
                offer.terminal_at = datetime.now(timezone.utc)
                if status == OfferStatus.ACCEPTED:
                    offer.accepted_at = offer.terminal_at
                self.db.commit()
                claim = self._claim(TOKEN)
                self.assertFalse(claim.ok)
                self.assertEqual(claim.refusal, refusal)
                self.assertEqual(claim.status, status)

    def test_g7p_13_a_foreign_threads_offer_refuses(self):
        offer = self._issue()
        offer.thread_id = self.thread_id + 999
        self.db.commit()
        claim = self._claim(TOKEN)
        self.assertFalse(claim.ok)
        self.assertEqual(claim.refusal, OfferRefusal.FOREIGN_OFFER)

    def test_g7p_14_the_candidate_binding_refuses_a_moved_vehicle(self):
        self._issue()
        claim = self._claim(TOKEN, candidate_id=self.cand_id + 1)
        self.assertFalse(claim.ok)
        self.assertEqual(claim.refusal, OfferRefusal.OFFER_CANDIDATE_CHANGED)

    def test_g7p_15_the_location_binding_refuses_a_moved_zone(self):
        self._issue()
        for zg, zd in (("Norte", "San Isidro"), ("CABA", "Belgrano"), (None, None)):
            with self.subTest(zone=(zg, zd)):
                claim = self._claim(TOKEN, zone_group=zg, zone_detail=zd)
                self.assertFalse(claim.ok)
                self.assertEqual(claim.refusal, OfferRefusal.OFFER_LOCATION_CHANGED)

    def test_g7p_16_an_expired_offer_refuses_and_is_marked_expired(self):
        offer = self._issue()
        offer.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.db.commit()
        claim = self._claim(TOKEN)
        self.db.commit()
        self.assertFalse(claim.ok)
        self.assertEqual(claim.refusal, OfferRefusal.OFFER_EXPIRED)
        self.assertEqual(self._rows()[0][1], OfferStatus.EXPIRED)
        self.assertIsNone(self.state.flow_booking_token)

    def test_g7p_17_every_refusal_is_recorded_against_the_offer_it_refused(self):
        """A Flow turn persists no hybrid-trace row, so the classification lives here."""
        self._issue()
        self.offers.withdraw(self.state, thread_id=self.thread_id,
                             reason=WithdrawReason.LOCATION_CHANGED)
        self.db.commit()
        for _ in range(3):
            self._claim(TOKEN)
        self.db.commit()
        row = self.db.execute(select(M.WhatsAppBookingOffer)).scalars().one()
        self.assertEqual(row.refused_count, 3)
        self.assertEqual(row.last_refusal, OfferRefusal.OFFER_WITHDRAWN)
        self.assertIsNotNone(row.last_refused_at)

    def test_g7p_18_consumption_records_what_the_offer_became(self):
        self._issue()
        claim = self._claim(TOKEN)
        self.assertTrue(claim.ok)
        self.offers.consume(self.state, thread_id=self.thread_id, revision_id=None,
                            thread_revision_id=None)
        self.db.commit()
        self.assertIsNone(self.state.flow_booking_token)
        self.assertEqual(self._rows()[0][1], OfferStatus.ACCEPTED)

    # ── the field list has exactly one owner ─────────────────────────────────

    def test_g7p_19_the_scheduling_field_list_is_the_quote_and_slot_inputs(self):
        self.assertEqual(set(VEHICLE_DEPENDENT_SCHEDULING_FIELDS), {
            "last_offered_slots", "last_visible_slots", "active_requested_date",
            "last_requested_time", "preferred_day", "preferred_time"})
        for field in VEHICLE_DEPENDENT_SCHEDULING_FIELDS:
            self.assertTrue(hasattr(M.WhatsAppThreadState, field), field)

    def test_g7p_20_token_coercion_never_guesses(self):
        self.assertEqual(_as_date("2026-11-10"), date(2026, 11, 10))
        self.assertEqual(_as_time("10:00"), time(10, 0))
        self.assertEqual(_as_time("10:00:30"), time(10, 0, 30))
        for junk in (None, "", "mañana", "99:99", 17, object()):
            with self.subTest(junk=junk):
                self.assertIsNone(_as_date(junk))
                self.assertIsNone(_as_time(junk))

    def test_g7p_21_a_fingerprint_is_not_reversible_and_is_stable(self):
        self.assertEqual(token_fingerprint(TOKEN), token_fingerprint(TOKEN))
        self.assertNotEqual(token_fingerprint(TOKEN), token_fingerprint(TOKEN + "x"))
        self.assertIsNone(token_fingerprint(None))
        self.assertIsNone(token_fingerprint(""))
        self.assertNotIn(TOKEN, token_fingerprint(TOKEN))


class R2BindingContract(OfferLifecycleRows):
    """R2 — the binding contract is enforced, not merely described.

    The G3-7P review found `tipo_vehiculo` and `cycle_key` stored as if they guarded
    acceptance and never compared. These tests assert the contract in both directions: an
    ACCEPTANCE BINDING refuses on mismatch, and HISTORICAL METADATA does not.
    """

    def _claim_r2(self, token=TOKEN, **over):
        kw = dict(candidate_id=self.cand_id, tipo_vehiculo="AUTO",
                  zone_group="CABA", zone_detail="Palermo", cycle_key=0)
        kw.update(over)
        return self.offers.claim(self.state, thread_id=self.thread_id, token=token, **kw)

    def _issue_r2(self, **over):
        kw = dict(tipo_vehiculo="AUTO")
        kw.update(over)
        return self._issue(**kw)

    # ── acceptance bindings: each one refuses on mismatch ────────────────────

    def test_r2_01_the_category_is_an_enforced_binding(self):
        self._issue_r2()
        claim = self._claim_r2(tipo_vehiculo="PICKUP")
        self.db.commit()
        self.assertFalse(claim.ok)
        self.assertEqual(claim.refusal, OfferRefusal.OFFER_VEHICLE_CATEGORY_CHANGED)
        self.assertEqual(self._rows()[0][1], OfferStatus.WITHDRAWN)
        self.assertIsNone(self.state.flow_booking_token)

    def test_r2_02_an_unresolvable_category_refuses(self):
        self._issue_r2()
        claim = self._claim_r2(tipo_vehiculo=None)
        self.assertFalse(claim.ok)
        self.assertIn(claim.refusal, (OfferRefusal.OFFER_VEHICLE_CATEGORY_CHANGED,
                                      OfferRefusal.OFFER_VEHICLE_CATEGORY_UNRESOLVED))

    def test_r2_03_an_offer_with_no_category_cannot_be_accepted(self):
        """An offer that never had a pricing basis must not become a priced booking."""
        self._issue_r2(tipo_vehiculo=None)
        claim = self._claim_r2(tipo_vehiculo=None)
        self.assertFalse(claim.ok)
        self.assertEqual(claim.refusal, OfferRefusal.OFFER_VEHICLE_CATEGORY_UNRESOLVED)

    def test_r2_04_the_cycle_is_an_enforced_binding(self):
        self._issue_r2()
        claim = self._claim_r2(cycle_key=99999)
        self.db.commit()
        self.assertFalse(claim.ok)
        self.assertEqual(claim.refusal, OfferRefusal.OFFER_CYCLE_CHANGED)
        self.assertEqual(self._rows()[0][1], OfferStatus.WITHDRAWN)
        self.assertIsNone(self.state.flow_booking_token)

    def test_r2_05_a_matching_bundle_is_accepted(self):
        self._issue_r2()
        claim = self._claim_r2()
        self.assertTrue(claim.ok, claim.refusal)

    # ── historical metadata: changing it must NOT refuse ─────────────────────

    def test_r2_06_the_offered_slot_is_metadata_not_a_binding(self):
        """The customer picks a slot inside the Flow and `ScheduleService` revalidates it,
        so comparing the slot the offer was issued for would refuse a legitimate choice."""
        import datetime as _dt
        offer = self._issue_r2(offered_date="2026-11-10", offered_time="10:00")
        self.assertEqual(offer.offered_date, _dt.date(2026, 11, 10))
        self.assertEqual(offer.offered_time, _dt.time(10, 0))
        self.state.preferred_day = "2026-12-25"       # the customer chose another slot
        self.state.preferred_time = "16:30"
        self.db.commit()
        self.assertTrue(self._claim_r2().ok, "a different chosen slot must still book")

    def test_r2_07_flow_id_and_contract_version_are_metadata(self):
        self._issue_r2(flow_id="28104222025943520")
        row = self.db.execute(select(M.WhatsAppBookingOffer)).scalars().one()
        self.assertEqual(row.flow_id, "28104222025943520")
        self.assertEqual(row.contract_version, OFFER_CONTRACT_VERSION)
        self.assertTrue(self._claim_r2().ok,
                        "neither is an acceptance binding, so neither may refuse")

    def test_r2_08_every_declared_binding_appears_in_the_claim_signature(self):
        """The contract and the signature cannot drift apart silently."""
        import inspect as _inspect
        params = set(_inspect.signature(OfferLifecycle.claim).parameters)
        for binding in ("token", "thread_id", "candidate_id", "tipo_vehiculo",
                        "zone_group", "zone_detail", "cycle_key"):
            self.assertIn(binding, params, binding)


class OwnerPolicyPreserved(unittest.TestCase):
    """G3-7P must not move customer-facing prose or the approved acknowledgement."""

    def test_g7p_22_the_approved_acknowledgement_is_unchanged(self):
        from app.services.conversation_engine import ConversationEngine
        self.assertEqual(
            ConversationEngine._FLOW_COMPLETION_ACK,
            "Gracias por completarlo. Un representante de RideCheck se estará "
            "comunicando con vos.")

    def test_g7p_23_the_truthy_tombstone_is_gone(self):
        """G3-7N's tombstone was the meaning-bearing offer state and re-armed F7A."""
        import app.services.conversation_engine as ce
        self.assertFalse(hasattr(ce, "_WITHDRAWN_OFFER_TOKEN_PREFIX"))

    def test_g7p_24_the_trace_contract_was_bumped_for_the_new_payload(self):
        from app.schemas.hybrid_trace import TRACE_VERSION, CanonicalSnapshot
        self.assertEqual(TRACE_VERSION, "hybrid-decision-trace/1.10")
        snap = CanonicalSnapshot()
        for field in ("offer_id", "offer_status", "offer_terminal_reason", "offer_refusal"):
            self.assertTrue(hasattr(snap, field), field)

    def test_g7p_25_older_trace_contracts_still_render(self):
        from app.ui.hybrid_trace_view import CONTRACTS_WITH_CANDIDATE_SECTION
        for older in ("hybrid-decision-trace/1.6", "hybrid-decision-trace/1.7",
                      "hybrid-decision-trace/1.8", "hybrid-decision-trace/1.9"):
            self.assertIn(older, CONTRACTS_WITH_CANDIDATE_SECTION)
        self.assertIn("hybrid-decision-trace/1.10", CONTRACTS_WITH_CANDIDATE_SECTION)


if __name__ == "__main__":                       # pragma: no cover
    unittest.main()


class R3DurabilityContract(OfferLifecycleRows):
    """R3 — the parts of the durability work that hold on any engine.

    The lock behaviour itself cannot be proven here: SQLite has no row locks, so the
    interleaving tests live in `test_l4_7w5_g3_7p_offer_authority_pg.py` and run against
    real PostgreSQL or not at all. What this suite pins down is everything that must be
    true regardless of engine — the out-of-band refusal record, the no-op on an engine
    with nothing to lock, and the action vocabulary a contended turn resolves to.

    The last one matters more than its size suggests. If the contended action were absent
    from `HANDLED_ACTIONS`, n8n's `IF - Engine Handled?` false branch would hand the turn
    to its LEGACY booking chain — a second booking authority, reached at the exact moment
    the first one is mid-booking.
    """

    def test_r3_01_an_out_of_band_refusal_is_recorded_and_committed(self):
        """A contended claim leaves the transaction aborted, so the in-transaction counters
        cannot be written. The refusal must still become durable, or a contention outcome
        would exist only in a container log — the dependence CLAUDE.md forbids."""
        self._issue()
        ok = self.offers.record_refusal_out_of_band(TOKEN, OfferRefusal.OFFER_CONTENDED)
        self.assertTrue(ok)
        fresh = self.SF()                       # a DIFFERENT session: it was committed
        try:
            row = fresh.execute(select(M.WhatsAppBookingOffer)).scalars().one()
            self.assertEqual(row.last_refusal, OfferRefusal.OFFER_CONTENDED)
            self.assertEqual(row.refused_count, 1)
            self.assertIsNotNone(row.last_refused_at)
            self.assertEqual(row.status, OfferStatus.ISSUED,
                             "recording a refusal must not terminalise the offer")
        finally:
            fresh.close()

    def test_r3_02_an_out_of_band_refusal_for_an_unknown_token_changes_nothing(self):
        self._issue()
        self.assertFalse(
            self.offers.record_refusal_out_of_band("not-a-real-token", "X"))
        self.assertFalse(self.offers.record_refusal_out_of_band(None, "X"))
        row = self.db.execute(select(M.WhatsAppBookingOffer)).scalars().one()
        self.assertEqual(row.refused_count, 0)
        self.assertIsNone(row.last_refusal)

    def test_r3_03_entering_the_offer_transaction_never_refuses_without_row_locks(self):
        """SQLite has nothing to take, so the guard must be a no-op — never a refusal that
        would make every booking on this engine fail closed for the wrong reason."""
        self.assertIsNone(self.offers.begin_offer_transaction(self.thread_id))
        self._issue()
        claim = self._claim(TOKEN)
        self.assertTrue(claim.ok, "the no-op guard blocked an otherwise valid acceptance")

    def test_r3_04_the_lock_order_is_one_declared_order_starting_at_the_thread(self):
        """Both writers call `begin_offer_transaction`, which takes step 1; `claim()` takes
        step 2. The order below is what makes those two steps the same two steps for both
        of them — the PostgreSQL suite proves the consequence."""
        from app.services.offer_lifecycle import (DEFAULT_OFFER_LOCK_TIMEOUT_MS,
                                                  OFFER_LOCK_ORDER)
        self.assertEqual(OFFER_LOCK_ORDER,
                         ("whatsapp_thread_states", "whatsapp_booking_offers"))
        self.assertGreater(DEFAULT_OFFER_LOCK_TIMEOUT_MS, 0,
                           "a zero timeout means PostgreSQL waits forever")

    def test_r3_05_a_contended_turn_is_an_owned_turn(self):
        from app.schemas.conversation import ACTION_THREAD_CONTENDED, HANDLED_ACTIONS
        from app.services.conversation_engine import (_NO_REPLY_REQUIRED_ACTIONS,
                                                      _REPLY_PRODUCED_ACTIONS, _out)
        self.assertIn(ACTION_THREAD_CONTENDED, HANDLED_ACTIONS,
                      "an unhandled action hands the turn to n8n's legacy booking chain")
        self.assertNotIn(ACTION_THREAD_CONTENDED, _REPLY_PRODUCED_ACTIONS)
        self.assertNotIn(ACTION_THREAD_CONTENDED, _NO_REPLY_REQUIRED_ACTIONS,
                         "a dropped turn must stay visible to the no-reply rescue")
        out = _out(ACTION_THREAD_CONTENDED, detail=OfferRefusal.OFFER_CONTENDED)
        self.assertTrue(out.handled)
        self.assertTrue(out.reply_required)
        self.assertFalse(out.reply_produced)
        self.assertTrue(out.alert_eligible)
        self.assertNotEqual(out.action, "error")

    def test_r3_06_contention_is_a_typed_refusal_in_the_one_refusal_vocabulary(self):
        """Not a free string invented at a call site: the losing writer's reason has to be
        the same named value on both writers, and short enough for the column that stores
        it (`last_refusal`, String(64))."""
        self.assertEqual(OfferRefusal.OFFER_CONTENDED, "OFFER_CONTENDED")
        self.assertLessEqual(len(OfferRefusal.OFFER_CONTENDED), 64)

    def test_r3_07_a_contention_sqlstate_is_told_apart_from_a_real_failure(self):
        """Classifying an ordinary programming error as contention would turn a bug into a
        silent refusal, which is how a defect hides for a release."""
        class _Orig:
            def __init__(self, sqlstate): self.sqlstate = sqlstate

        class _Exc(Exception):
            def __init__(self, sqlstate): self.orig = _Orig(sqlstate)

        for sqlstate in ("55P03", "40P01", "40001"):
            self.assertEqual(self.offers.contention_sqlstate(_Exc(sqlstate)), sqlstate)
        for sqlstate in ("42P01", "23505", "22P02", None):
            self.assertIsNone(self.offers.contention_sqlstate(_Exc(sqlstate)),
                              f"{sqlstate} was misread as contention")
        self.assertIsNone(self.offers.contention_sqlstate(ValueError("plain")))


class G37RAdmissionScope(unittest.TestCase):
    """G3-7R/S2 — ordinary traffic performs no booking lock operation, and the reason is
    now structural rather than predicted.

    This class replaces `R3AContentionScope`, which tested
    `ConversationEngine._is_offer_acceptance_turn`. That predicate is DELETED, so the tests
    that asserted its answers are deleted with it — but the PROPERTY they were protecting is
    not, and it matters more than ever. The R3 independent review measured the cost of
    getting it wrong: on the parent commit a second conversation turn on the same thread
    completed in 0.05–0.20s while the first held the thread for twelve seconds; with R3's
    turn-entry acquisition that second turn was DECLINED after 4.05s with
    `thread_contended`. A lost customer message.

    What changed is WHERE the property comes from. R3A kept the property by making the
    predicate narrower, and a predicate that answers from the payload shape and the
    transport mirror can always be made to answer wrongly — its own docstring recorded that
    it answered True when it could not tell, so its fallback was to serialise traffic that
    could never book. G3-7R removes the gate instead of narrowing it: there is no turn-entry
    acquisition left for any turn to be classified into, and the boundary question is
    answered by `OfferLifecycle.admit()` at the acceptance point, with one indexed read and
    no lock at all.

    So this suite asserts three things, all without a database, because the regression gate
    that runs by default is the sealed SQLite one — the review's MUT-D showed that coverage
    sitting behind `G7P_PG_URL` could not see R3's own mechanism being deleted:

        * the gate is GONE and has not been reintroduced under another name;
        * admission fails closed on every shape of non-booking and malformed input, and
          decides from the offer table rather than from prose, prefixes or payload keys;
        * admission acquires nothing.
    """

    def setUp(self):
        from app.services.conversation_engine import ConversationEngine
        self.CE = ConversationEngine

    # ── the gate is gone (S2) ─────────────────────────────────────────────────

    def test_g37r_01_the_turn_entry_predicate_no_longer_exists(self):
        """Deleted, not deprecated. A predicate that still exists is a predicate a later
        change can start calling again."""
        self.assertFalse(hasattr(self.CE, "_is_offer_acceptance_turn"),
                         "_is_offer_acceptance_turn must be deleted, not retained")
        self.assertFalse(hasattr(self.CE, "_begin_thread_transaction"),
                         "_begin_thread_transaction must be deleted, not retained")

    def test_g37r_02_handle_takes_no_UNCONDITIONAL_booking_lock_at_turn_entry(self):
        """MUTATION ORACLE (required mutation 5), RESTATED BY R3 — read the reason.

        G3-7R wrote this oracle as "no `begin_offer_transaction` anywhere in `_handle`,
        under any predicate", because the gate it had just deleted was unconditional and
        the measured cost was 4.05s on threads the parent served in 0.05–0.20s.

        R3 had to acquire resource 1 in `_handle` to close RVR2-01, and this test is
        therefore DELIBERATELY WEAKENED IN ONE RESPECT AND STRENGTHENED IN FOUR. The
        milestone prompt authorises exactly that and no more: "If the bound must move
        earlier, gate it only on an already-established booking-shaped submission and
        document the exact predicate", and "No correction may restore the deleted general
        turn-entry gate."

        The invariant the old form was really protecting is not "no acquisition here"; it
        is "no acquisition on a turn that cannot book". That is what is asserted now, and
        it is asserted on the AST rather than on a substring:

          1. the legacy mechanisms stay deleted by name;
          2. there is NO acquisition at the statement level of `_handle` — every one is
             inside a guard;
          3. every guard is one of exactly two permitted predicates — the booking-shaped
             transport predicate, or `cycle_reset_pending`, which is a real writer;
          4. the booking-shaped flag comes from `_is_booking_shaped_submission` and nothing
             else;
          5. that predicate reads no prose, no semantic result and no model output.

        The behavioural half lives on PostgreSQL, where an ordinary text turn is measured
        running to completion while another session holds resource 1
        (`test_r3_pg_07_an_ordinary_text_turn_takes_no_booking_lock`). A structural
        statement about a lock is not a concurrency proof, and this one does not claim to
        be: it exists so the predicate cannot quietly widen.
        """
        import ast
        import inspect
        import textwrap
        # CODE, not comments: the comments in `_handle` name the deleted mechanism in order
        # to explain why it is gone, and an oracle that cannot tell a comment from a call
        # would make that explanation unwritable.
        tree = ast.parse(textwrap.dedent(inspect.getsource(self.CE._handle)))
        src = ast.unparse(tree)
        for forbidden in ("_begin_thread_transaction", "_is_offer_acceptance_turn",
                          "FOR NO KEY UPDATE"):
            self.assertNotIn(forbidden, src,
                             f"_handle must take no booking lock at turn entry: {forbidden}")

        ACQUIRE = ("begin_offer_transaction", "_enter_cycle_reset_bound",
                   "_acquire_thread_state", "_require_thread_state",
                   "_sb_enter_thread_state")
        fn = tree.body[0]

        def acquisitions(node):
            found = []
            for n in ast.walk(node):
                if isinstance(n, ast.Call):
                    name = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                    if name in ACQUIRE:
                        found.append(name)
            return found

        # (2) nothing unconditional: every acquisition sits inside an `if`.
        for stmt in fn.body:
            if isinstance(stmt, ast.If):
                continue
            self.assertEqual(
                acquisitions(stmt), [],
                "`_handle` acquires the booking lock OUTSIDE any guard — that is the "
                "unconditional turn-entry gate, restored")

        # (3) every acquisition sits under a permitted predicate. Convergence Phase 3
        # adaptation: OLD — two guarded acquisitions (the booking-shaped turn ENTRY and the
        # cycle reset). NEW — the turn-entry acquisition is DELETED (`BookingAuthority` takes
        # K1 itself, explicitly and first, on its own connection; a CE-held K1 would make it
        # wait on its own caller, CD-01). What remains: the cycle reset (a real writer), and on
        # booking-shaped turns ONLY, the bounded K1 taken immediately before the `no_lead`
        # and `skipped_human` cursor commits — never at turn entry, never for ordinary turns.
        permitted = ("_booking_shaped", "cycle_reset_pending")

        def guards_of(node, chain=()):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.Call):
                    name = getattr(child.func, "attr", None) or getattr(child.func, "id", None)
                    if name in ACQUIRE:
                        yield name, chain
                nxt = chain + ((ast.unparse(child.test),) if isinstance(child, ast.If) else ())
                yield from guards_of(child, nxt)
        found = list(guards_of(fn))
        self.assertTrue(found)
        for name, chain in found:
            self.assertTrue(any(p in t for t in chain for p in permitted),
                            "`_handle` calls %s under %r — neither the booking-shaped "
                            "predicate nor cycle_reset_pending" % (name, chain))
        self.assertEqual(sorted(n for n, _c in found),
                         ["_enter_cycle_reset_bound", "_sb_enter_thread_state",
                          "_sb_enter_thread_state"],
                         "unexpected booking-lock acquisitions in `_handle`: %r" % found)

        # (4) the flag is derived from the declared predicate and nothing else.
        assigns = [ast.unparse(n.value) for n in ast.walk(fn)
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "_booking_shaped"
                           for t in n.targets)]
        self.assertEqual(len(assigns), 1,
                         "_booking_shaped must be assigned exactly once, found %r"
                         % (assigns,))
        self.assertIn("_is_booking_shaped_submission", assigns[0],
                      "_booking_shaped is derived from something other than the declared "
                      "predicate: %r" % assigns[0])

        # (5) the predicate itself reads nothing semantic. CODE only: the docstring
        # explains what the predicate must not read, and an oracle that could not tell a
        # docstring from a statement would make that explanation unwritable.
        _pnode = ast.parse(textwrap.dedent(inspect.getsource(
            self.CE._is_booking_shaped_submission))).body[0]
        _pbody = list(_pnode.body)
        if (_pbody and isinstance(_pbody[0], ast.Expr)
                and isinstance(_pbody[0].value, ast.Constant)):
            _pbody = _pbody[1:]
        pred = "\n".join(ast.unparse(n) for n in _pbody)
        for forbidden in ("semantic", "understand", "interpret", "_live_plan", "proposal",
                          "LEXICON", "PHRASE", "lower()", "self.db", "execute(",
                          "openai", "llm"):
            self.assertNotIn(forbidden, pred,
                             "the booking-shaped predicate reads %r — a predicate that "
                             "gates a lock must not depend on prose or on a model"
                             % forbidden)

    def test_g37r_03_no_phrase_catalogue_decides_admission(self):
        """The boundary must not be able to drift back into reading customer prose."""
        import inspect
        from app.services import offer_lifecycle as OL
        src = inspect.getsource(OL.OfferLifecycle.admit)
        for forbidden in ("lower()", "startswith", "in text", "LEXICON", "PHRASE"):
            self.assertNotIn(forbidden, src,
                             f"admission must not read prose or prefixes: {forbidden}")

    # ── admission fails closed, and decides from the offer row ────────────────

    class _Offers:
        """A fake offer table. `admit()` is pure logic over the row it finds, so the row is
        the only thing that has to be faked — which is the point: nothing about the payload,
        the transport or the mirror reaches the decision."""

        def __init__(self, row=None, now=None):
            self.row = row
            self._now = now or datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
            self.sql = []

        def make(self, **kw):
            from app.services.offer_lifecycle import OfferLifecycle, OfferStatus
            lifecycle = OfferLifecycle.__new__(OfferLifecycle)
            lifecycle.db = self
            lifecycle._postgres = lambda: False
            lifecycle._now = lambda: self._now
            lifecycle.offer_by_token = lambda token: self.row
            return lifecycle

        def execute(self, stmt, *a, **k):          # pragma: no cover - must not be reached
            self.sql.append(str(stmt))
            raise AssertionError("admission must issue no SQL of its own")

    class _Row:
        def __init__(self, **kw):
            from app.services.offer_lifecycle import OfferStatus
            self.id = kw.get("id", 7)
            self.thread_id = kw.get("thread_id", 1)
            self.status = kw.get("status", OfferStatus.ISSUED)
            self.candidate_id = kw.get("candidate_id", 11)
            self.tipo_vehiculo = kw.get("tipo_vehiculo", "AUTO")
            self.zone_group = kw.get("zone_group", "CABA")
            self.zone_detail = kw.get("zone_detail", "Palermo")
            self.cycle_key = kw.get("cycle_key", 0)
            self.offered_date = kw.get("offered_date")
            self.offered_time = kw.get("offered_time")
            self.expires_at = kw.get("expires_at")
            self.accepted_thread_revision_id = kw.get("accepted_thread_revision_id")
            self.accepted_revision_id = kw.get("accepted_revision_id")

    def _admit(self, row, *, token="bk-1-abc", thread_id=1, now=None):
        fake = self._Offers(row, now=now)
        adm = fake.make().admit(thread_id=thread_id, token=token)
        self.assertEqual(fake.sql, [], "admission must take no lock and issue no SQL")
        return adm

    def test_g37r_04_absence_of_an_offer_row_is_a_refusal(self):
        """Fails closed. The only thing a missing record can honestly authorize is nothing."""
        from app.services.offer_lifecycle import OfferRefusal
        adm = self._admit(None)
        self.assertFalse(adm.granted)
        self.assertFalse(adm.replay)
        self.assertEqual(adm.refusal, OfferRefusal.UNKNOWN_TOKEN)

    def test_g37r_05_every_shape_of_missing_token_is_a_refusal(self):
        """Including the one the G3-7N review measured booking successfully: NO TOKEN."""
        from app.services.offer_lifecycle import OfferRefusal
        for token in (None, "", "   ", 0, [], {}, 12345, object()):
            with self.subTest(token=repr(token)):
                adm = self._admit(self._Row(), token=token)
                self.assertFalse(adm.granted)
                self.assertEqual(adm.refusal, OfferRefusal.NO_TOKEN)

    def test_g37r_06_a_foreign_offer_is_a_refusal(self):
        from app.services.offer_lifecycle import OfferRefusal
        adm = self._admit(self._Row(thread_id=999), thread_id=1)
        self.assertFalse(adm.granted)
        self.assertEqual(adm.refusal, OfferRefusal.FOREIGN_OFFER)

    def test_g37r_07_every_terminal_status_is_a_refusal_with_its_own_reason(self):
        from app.services.offer_lifecycle import OfferRefusal, OfferStatus
        expected = {
            OfferStatus.WITHDRAWN: OfferRefusal.OFFER_WITHDRAWN,
            OfferStatus.SUPERSEDED: OfferRefusal.SUPERSEDED_OFFER,
            OfferStatus.EXPIRED: OfferRefusal.OFFER_EXPIRED,
        }
        for status, refusal in expected.items():
            with self.subTest(status=status):
                adm = self._admit(self._Row(status=status))
                self.assertFalse(adm.granted)
                self.assertFalse(adm.replay)
                self.assertEqual(adm.refusal, refusal)

    def test_g37r_08_an_expired_offer_is_refused_and_admission_writes_nothing(self):
        from app.services.offer_lifecycle import OfferRefusal
        adm = self._admit(self._Row(
            expires_at=datetime(2026, 10, 13, 11, 0, tzinfo=timezone.utc)))
        self.assertEqual(adm.refusal, OfferRefusal.OFFER_EXPIRED)

    def test_g37r_09_an_issued_offer_is_granted_and_carries_its_bindings(self):
        """The grant travels with the immutable facts, so the execution path re-resolves
        nothing — which is what makes the two transports agree (S3)."""
        adm = self._admit(self._Row(candidate_id=42, tipo_vehiculo="CAMIONETA",
                                    zone_group="GBA_NORTE", zone_detail="Tigre",
                                    cycle_key=9))
        self.assertTrue(adm.granted)
        self.assertTrue(bool(adm))
        self.assertIsNone(adm.refusal)
        self.assertEqual(adm.candidate_id, 42)
        self.assertEqual(adm.tipo_vehiculo, "CAMIONETA")
        self.assertEqual((adm.zone_group, adm.zone_detail), ("GBA_NORTE", "Tigre"))
        self.assertEqual(adm.cycle_key, 9)

    def test_g37r_10_accepted_with_a_booking_is_a_replay_not_a_refusal(self):
        """S6. The architecture audit measured this case refused `OFFER_ALREADY_ACCEPTED`,
        which set `needs_human=True` and told the customer their session had expired —
        about a booking that had just succeeded."""
        from app.services.offer_lifecycle import OfferStatus
        adm = self._admit(self._Row(status=OfferStatus.ACCEPTED,
                                    accepted_thread_revision_id=55,
                                    accepted_revision_id=66))
        self.assertTrue(adm.replay)
        self.assertFalse(adm.granted)
        self.assertFalse(bool(adm), "a replay is not a permission to execute")
        self.assertIsNone(adm.refusal, "a replay must never carry a refusal")
        self.assertEqual(adm.thread_revision_id, 55)
        self.assertEqual(adm.revision_id, 66)

    def test_g37r_11_accepted_without_a_booking_is_refused_not_replayed(self):
        """There is no authoritative booking to return, so inventing one would be worse
        than refusing."""
        from app.services.offer_lifecycle import OfferRefusal, OfferStatus
        adm = self._admit(self._Row(status=OfferStatus.ACCEPTED,
                                    accepted_thread_revision_id=None))
        self.assertFalse(adm.replay)
        self.assertFalse(adm.granted)
        self.assertEqual(adm.refusal, OfferRefusal.OFFER_ALREADY_ACCEPTED)

    def test_g37r_12_an_unrecognised_status_fails_closed(self):
        from app.services.offer_lifecycle import OfferRefusal
        adm = self._admit(self._Row(status="SOMETHING_NEW"))
        self.assertFalse(adm.granted)
        self.assertEqual(adm.refusal, OfferRefusal.UNKNOWN_TOKEN)

    # ── the bound is scoped to an acquisition, and restored exactly (S7) ──────

    class _PgDb:
        """A PostgreSQL-shaped session that answers `SHOW lock_timeout` truthfully, so the
        restore path has a real prior value to put back. The previous fake returned None for
        every statement, which made `SHOW` fail and the restore fall through to DEFAULT —
        so the old test passed whether the bug was present or not."""

        def __init__(self, initial="0"):
            self.value = initial
            self.issued = []
            #: R2/RV-01. The `lock_timeout` in force when the CALLER's pending writes were
            #: flushed. This is the whole point of the correction: the flush must happen
            #: inside the bound, never before it. `None` means it never happened.
            self.flushed_under = None
            self.autoflush_suppressed = 0

        class _Res:
            def __init__(self, v):
                self._v = v

            def scalar(self):
                return self._v

        @property
        def no_autoflush(self):
            import contextlib
            outer = self

            @contextlib.contextmanager
            def _ctx():
                outer.autoflush_suppressed += 1
                yield

            return _ctx()

        def flush(self):
            self.flushed_under = self.value

        def execute(self, stmt, *a, **k):
            sql = str(stmt)
            self.issued.append(sql)
            if sql.strip().upper().startswith("SHOW"):
                return self._Res(self.value)
            if "SET LOCAL lock_timeout" in sql:
                raw = sql.split("=", 1)[1].strip().strip("'")
                self.value = "0" if raw.upper() == "DEFAULT" else raw
            return self._Res(None)

        def get_bind(self):
            class _B:
                class dialect:
                    name = "postgresql"
            return _B()

    def test_r3a_10_the_bounded_wait_is_a_no_op_without_row_locks(self):
        """SQLite has nothing to bound, so the context manager must not touch the session —
        and must still yield, or every booking on this engine would break."""
        from app.services.offer_lifecycle import (DEFAULT_OFFER_LOCK_TIMEOUT_MS,
                                                  OfferLifecycle)
        executed = []

        class _Db:
            def execute(self, *a, **k):
                executed.append(a)
                raise AssertionError("the no-op path must issue no SQL")

            def get_bind(self):
                class _B:
                    class dialect:
                        name = "sqlite"
                return _B()

        lifecycle = OfferLifecycle(_Db())
        entered = []
        with lifecycle._bounded_lock_wait(DEFAULT_OFFER_LOCK_TIMEOUT_MS):
            entered.append(True)
        self.assertEqual(entered, [True])
        self.assertEqual(executed, [])

    def test_g37r_13_the_bound_is_set_then_the_PRIOR_value_restored(self):
        """MUTATION ORACLE (required mutation 12). R3A restored `DEFAULT`, which on this
        database means NO LIMIT: a caller that had deliberately set a stricter bound was
        silently handed unlimited waiting by the inner block's cleanup. The prior value is
        read and that exact value is put back."""
        from app.services.offer_lifecycle import OfferLifecycle
        db = self._PgDb(initial="250ms")
        with OfferLifecycle(db)._bounded_lock_wait(4000):
            self.assertTrue(any("SET LOCAL lock_timeout = 4000" in s for s in db.issued))
            self.assertEqual(db.value, "4000")
        self.assertEqual(db.value, "250ms",
                         "the caller's own stricter bound must be restored, not DEFAULT")
        self.assertNotIn("SET LOCAL lock_timeout = DEFAULT", db.issued,
                         "DEFAULT would widen a stricter caller to unlimited")
        # ── R2/RV-01, the sealed half ─────────────────────────────────────────
        # MUTATION ORACLE for required mutations 1 and 2. The caller's pending writes must
        # be flushed INSIDE the bound. G3-7R let SQLAlchemy's autoflush decide when, and on
        # the refusal paths it decided "at the first write, before any bound" — measured at
        # 42s and 72s against a 12s hold, where the parent answered in 4s.
        self.assertEqual(db.flushed_under, "4000",
                         "the caller's pending writes must flush under the bound, not "
                         "before it")
        # TWO suppressions, not one: the preparatory `SHOW`/`SET` pair and the restore.
        # `>= 1` passed with the preparatory suppression removed, because the restore
        # still entered one — the count has to pin the half that matters.
        self.assertGreaterEqual(db.autoflush_suppressed, 2,
                                "this block's own statements must not autoflush the "
                                "caller's pending writes — before the bound OR after it")

    def test_g37r_14_nested_bounds_compose(self):
        """The inner block restores the OUTER block's bound, not the session's."""
        from app.services.offer_lifecycle import OfferLifecycle
        db = self._PgDb(initial="0")
        lifecycle = OfferLifecycle(db)
        with lifecycle._bounded_lock_wait(4000):
            self.assertEqual(db.value, "4000")
            with lifecycle._bounded_lock_wait(1000):
                self.assertEqual(db.value, "1000")
            self.assertEqual(db.value, "4000", "the inner block must restore the outer bound")
        self.assertEqual(db.value, "0")

    def test_g37r_15_an_unset_prior_bound_restores_DEFAULT(self):
        """`DEFAULT` is correct in exactly one case: there was genuinely nothing to put back.
        A session that cannot answer `SHOW` is that case."""
        from app.services.offer_lifecycle import OfferLifecycle

        class _Mute(G37RAdmissionScope._PgDb):
            def execute(self, stmt, *a, **k):
                sql = str(stmt)
                self.issued.append(sql)
                if sql.strip().upper().startswith("SHOW"):
                    raise RuntimeError("this session will not say")
                return self._Res(None)

        db = _Mute()
        with OfferLifecycle(db)._bounded_lock_wait(4000):
            pass
        self.assertIn("SET LOCAL lock_timeout = DEFAULT", db.issued[-1])

    def test_g37r_16_a_non_literal_prior_value_is_never_re_inlined(self):
        """`SET` takes no bind parameter, so a restored value is re-inlined — and only after
        it has matched a timeout literal. Anything else falls back to DEFAULT rather than
        reaching the statement."""
        from app.services.offer_lifecycle import OfferLifecycle
        db = self._PgDb(initial="4s'; DROP TABLE whatsapp_booking_offers; --")
        with OfferLifecycle(db)._bounded_lock_wait(4000):
            pass
        joined = " ".join(db.issued)
        self.assertNotIn("DROP TABLE", joined)
        self.assertIn("SET LOCAL lock_timeout = DEFAULT", db.issued[-1])

    def test_r3a_12_the_bound_is_restored_even_when_the_acquisition_fails(self):
        """And the ORIGINAL exception is what the caller sees — never the restore's."""
        from app.services.offer_lifecycle import OfferLifecycle
        db = self._PgDb(initial="750ms")
        with self.assertRaises(RuntimeError) as caught:
            with OfferLifecycle(db)._bounded_lock_wait(4000):
                raise RuntimeError("the acquisition timed out")
        self.assertEqual(str(caught.exception), "the acquisition timed out")
        self.assertEqual(db.value, "750ms")

    def test_g37r_17_a_failed_restore_does_not_replace_the_original_exception(self):
        """After a transaction abort the session rejects every statement, so the restore
        cannot run. What the caller must see is still the original failure."""
        from app.services.offer_lifecycle import OfferLifecycle

        class _Aborted(G37RAdmissionScope._PgDb):
            def __init__(self):
                super().__init__(initial="500ms")
                self.aborted = False

            def execute(self, stmt, *a, **k):
                if self.aborted:
                    raise RuntimeError("current transaction is aborted")
                return super().execute(stmt, *a, **k)

        db = _Aborted()
        with self.assertRaises(ValueError) as caught:
            with OfferLifecycle(db)._bounded_lock_wait(4000):
                db.aborted = True
                raise ValueError("55P03 lock_not_available")
        self.assertEqual(str(caught.exception), "55P03 lock_not_available")

    # ── a deadlock is classified apart from ordinary contention (S4) ──────────

    def test_g37r_18_40P01_is_not_reported_as_contention(self):
        """MUTATION ORACLE. Under ONE declared lock order a deadlock is impossible, so it is
        evidence of a protocol violation — and R3 folded it in with 55P03, which is how the
        architecture audit's measured ISSUE/acceptance deadlock reached operators as
        `OFFER_CONTENDED`: the wrong diagnosis of a real defect."""
        from app.services.offer_lifecycle import OfferLifecycle, OfferRefusal
        self.assertEqual(OfferLifecycle.refusal_for_sqlstate("40P01"),
                         OfferRefusal.OFFER_DEADLOCK)
        for state in ("55P03", "40001"):
            with self.subTest(sqlstate=state):
                self.assertEqual(OfferLifecycle.refusal_for_sqlstate(state),
                                 OfferRefusal.OFFER_CONTENDED)
        self.assertNotEqual(OfferRefusal.OFFER_DEADLOCK, OfferRefusal.OFFER_CONTENDED)

    def test_g37r_19_a_deadlock_is_still_CAUGHT_and_never_escapes_as_an_error(self):
        """Classified separately, but still answered with a typed domain outcome: the
        customer must not meet an internal error either way."""
        from app.services.offer_lifecycle import OfferLifecycle

        class _Exc(Exception):
            sqlstate = "40P01"

        self.assertEqual(OfferLifecycle.contention_sqlstate(_Exc()), "40P01")

    def test_g37r_20_an_unrelated_sqlstate_still_raises(self):
        """A bug must not be absorbed into the contention vocabulary."""
        from app.services.offer_lifecycle import OfferLifecycle

        class _Exc(Exception):
            sqlstate = "23502"

        self.assertIsNone(OfferLifecycle.contention_sqlstate(_Exc()))

    def test_r3a_13_a_contended_booking_turn_is_still_an_owned_turn(self):
        """R3A narrows WHO contends; it must not change what a real contention means. An
        unhandled action takes n8n's legacy booking chain."""
        from app.schemas.conversation import ACTION_THREAD_CONTENDED, HANDLED_ACTIONS
        from app.services.conversation_engine import _out
        self.assertIn(ACTION_THREAD_CONTENDED, HANDLED_ACTIONS)
        out = _out(ACTION_THREAD_CONTENDED, detail="OFFER_CONTENDED")
        self.assertTrue(out.handled)
        self.assertTrue(out.reply_required)
        self.assertFalse(out.reply_produced)
        self.assertNotEqual(out.action, "error")
