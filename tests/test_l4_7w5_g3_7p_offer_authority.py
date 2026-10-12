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


class R3AContentionScope(unittest.TestCase):
    """R3A — WHICH turns may enter the booking lock order, decided without a database.

    The R3 independent review measured what an unconditional acquisition cost: on the parent
    a second conversation turn on the same thread completed in 0.05–0.20s while the first
    held the thread for twelve seconds; with R3's turn-entry lock on every turn, that second
    turn was DECLINED after 4.05s with `thread_contended`. An ordinary message never calls
    `claim()`, so serialising it protected nothing and lost customer turns.

    This suite lives here, not only in the PostgreSQL file, deliberately. The review's
    MUT-D showed the sealed gate could not see R3's §D mechanism being deleted — all of its
    coverage sat behind `G7P_PG_URL` and skipped. The PREDICATE is pure logic, so it is
    tested where the default regression gate will actually run it.
    """

    def setUp(self):
        from app.services.conversation_engine import ConversationEngine
        self.CE = ConversationEngine

    class _Ev:
        def __init__(self, message_type="text", flow_response=None):
            self.message_type = message_type
            self.flow_response = flow_response

    class _St:
        def __init__(self, token=None):
            self.flow_booking_token = token

    def _is(self, event, state=None):
        return self.CE._is_offer_acceptance_turn(
            self.CE.__new__(self.CE), event, state or self._St())

    BOOKING_PAYLOAD = {"nombre_apellido": "P C", "email": "p@e.t", "telefono": "5491100",
                       "como_llego": "IG", "direccion": "Palermo 1",
                       "tipo_vendedor": "particular"}

    # ── the turns that must NOT take the booking lock ─────────────────────────

    def test_r3a_01_ordinary_transports_never_enter_the_lock_order(self):
        for kind in ("text", "audio", "image", "interactive", "button", "list", ""):
            with self.subTest(message_type=kind):
                self.assertFalse(self._is(self._Ev(kind)),
                                 f"{kind!r} is not a booking writer and must not serialise")

    def test_r3a_02_a_text_turn_carrying_a_payload_is_still_not_a_booking_turn(self):
        """Only `message_type == "flow_response"` routes to `_process_flow_response`; a
        payload on any other transport never reaches `claim()`."""
        self.assertFalse(self._is(self._Ev("text", dict(self.BOOKING_PAYLOAD))))

    def test_r3a_03_the_vehicle_clarification_flow_is_not_a_booking_turn(self):
        self.assertFalse(self._is(self._Ev("flow_response", {"tipo_vehiculo": "AUTO"})))
        self.assertFalse(self._is(self._Ev(
            "flow_response", {"tipo_vehiculo": "MOTO", "marca": "Honda"})))

    def test_r3a_04_the_location_clarification_flow_is_not_a_booking_turn(self):
        self.assertFalse(self._is(self._Ev("flow_response", {"zona_general": "CABA"})))
        self.assertFalse(self._is(self._Ev(
            "flow_response", {"zona_general": "GBA_NORTE", "zona_detalle": "Tigre"})))

    def test_r3a_05_the_motorcycle_contact_flow_is_not_a_booking_turn(self):
        from app.services.conversation_engine import _MOTO_HANDOFF_TOKEN_PREFIX
        state = self._St(_MOTO_HANDOFF_TOKEN_PREFIX + "abc123")
        self.assertFalse(self._is(self._Ev(
            "flow_response", dict(self.BOOKING_PAYLOAD)), state))

    def test_r3a_06_an_empty_or_malformed_payload_is_not_a_booking_turn(self):
        for payload in (None, {}, [], "", 0, "nombre_apellido"):
            with self.subTest(payload=payload):
                self.assertFalse(self._is(self._Ev("flow_response", payload)))

    # ── the turns that MUST take it ───────────────────────────────────────────

    def test_r3a_07_a_booking_flow_response_does_enter_the_lock_order(self):
        """The one turn that can reach `claim()` and race the Data Exchange writer."""
        self.assertTrue(self._is(self._Ev("flow_response", dict(self.BOOKING_PAYLOAD))))

    def test_r3a_08_a_booking_response_with_an_unrelated_live_token_still_qualifies(self):
        """A booking mirror that is NOT the motorcycle family is a booking turn, and so is
        one with no mirror at all — the latter still calls `claim()`, which fails closed."""
        for token in (None, "", "bk-1234567890-abcdef"):
            with self.subTest(mirror=token):
                self.assertTrue(self._is(
                    self._Ev("flow_response", dict(self.BOOKING_PAYLOAD)),
                    self._St(token)))

    def test_r3a_09_the_predicate_agrees_with_the_engine_s_own_routing_order(self):
        """The discriminators and their ORDER are the same ones `_process_flow_response`
        routes on. If that routing is ever reordered, this test is the thing that notices
        the predicate no longer describes where the turn goes."""
        import inspect
        src = inspect.getsource(self.CE._process_flow_response)
        head = src[:src.index("G3-7P: FAIL-CLOSED ACCEPTANCE")]
        self.assertLess(head.index("_MOTO_HANDOFF_TOKEN_PREFIX"),
                        head.index('"tipo_vehiculo" in flow_data'))
        self.assertLess(head.index('"tipo_vehiculo" in flow_data'),
                        head.index('"zona_general" in flow_data'))
        pred = inspect.getsource(self.CE._is_offer_acceptance_turn)
        for token in ('"flow_response"', '"tipo_vehiculo"', '"zona_general"',
                      "_MOTO_HANDOFF_TOKEN_PREFIX"):
            self.assertIn(token, pred)
        self.assertNotIn("lower()", pred, "the predicate must not read customer prose")

    # ── the bound is scoped to an acquisition, not to a turn ──────────────────

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

    def test_r3a_11_the_bound_is_set_then_restored_around_the_acquisition(self):
        """R3 left `SET LOCAL lock_timeout` standing for the rest of the transaction, so a
        booking turn's bound governed every later statement in it. The set/restore pair is
        what keeps it on the acquisition it was chosen for."""
        from app.services.offer_lifecycle import OfferLifecycle
        issued = []

        class _Db:
            def execute(self, stmt, *a, **k):
                issued.append(str(stmt))

            def get_bind(self):
                class _B:
                    class dialect:
                        name = "postgresql"
                return _B()

        with OfferLifecycle(_Db())._bounded_lock_wait(4000):
            self.assertEqual(len(issued), 1)
            self.assertIn("SET LOCAL lock_timeout = 4000", issued[0])
        self.assertEqual(len(issued), 2)
        self.assertIn("SET LOCAL lock_timeout = DEFAULT", issued[1])

    def test_r3a_12_the_bound_is_restored_even_when_the_acquisition_fails(self):
        from app.services.offer_lifecycle import OfferLifecycle
        issued = []

        class _Db:
            def execute(self, stmt, *a, **k):
                issued.append(str(stmt))

            def get_bind(self):
                class _B:
                    class dialect:
                        name = "postgresql"
                return _B()

        with self.assertRaises(RuntimeError):
            with OfferLifecycle(_Db())._bounded_lock_wait(4000):
                raise RuntimeError("the acquisition timed out")
        self.assertIn("SET LOCAL lock_timeout = DEFAULT", issued[-1])

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
