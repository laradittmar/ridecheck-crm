"""Convergence Phase 3 — the CE and Data Exchange cutover, in the DEFAULT regression gate.

Everything here runs on SQLite with no network, through the production route function behind
`POST /api/conversation/handle` and the production Data Exchange service (`_sb2b_harness`).
SQLite is wiring and truthfulness evidence only; locks, races, timings and crash windows are
proven on PostgreSQL in `test_convergence_p3_race_pg.py`.

What this file pins, each as behaviour unless a structural fact is the subject:

  * the flag is an OFF switch with exactly two meanings — true books through the authority,
    anything else (false, unset, malformed, merely truthy) is a truthful handoff — and it is
    read per request, never cached;
  * no flag state reaches a legacy writer: nothing in the Conversation Engine or the Data
    Exchange builds a booking row, and no one-argument advisory lock exists anywhere;
  * the ledger is read FIRST — before the cursor, the cycle reset and `needs_human` — and is
    re-read before every terminal answer;
  * CE and the Data Exchange derive the SAME `flow:` key for one Flow submission;
  * a hostile `str` subclass is refused before any use (SB2B-RV-02);
  * the CE synchronisation writes CE-owned fields only, and the acknowledgement is sent once,
    through the ledger lease, with the one owner-approved copy.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _sb2b_harness as H  # noqa: E402

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (BookingCommandRecord, Lead, Revision, ThreadRevision,  # noqa: E402
                        WhatsAppBookingOffer, WhatsAppThreadState)
from app.services import booking_ce_adapter as adapter  # noqa: E402
from app.services import booking_flow_service as bfs  # noqa: E402
from app.services.booking_authority import BookingAuthority, flow_idempotency_key  # noqa: E402
from app.services.conversation_engine import ConversationEngine  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"


class _Calls:
    """Counts calls of a class attribute or module function while delegating to it."""

    def __init__(self, owner, name):
        self.owner, self.name, self.calls = owner, name, 0
        self.real = getattr(owner, name)

    def __enter__(self):
        real = self.real

        def wrapper(*a, **k):
            self.calls += 1
            return real(*a, **k)
        self._p = mock.patch.object(self.owner, self.name, wrapper)
        self._p.start()
        return self

    def __exit__(self, *exc):
        self._p.stop()
        return False


def _rows(engine, w):
    with Session(engine) as db:
        return {
            "revisions": db.query(Revision).filter(Revision.lead_id == w.lead_id).count(),
            "thread_revisions": db.query(ThreadRevision).filter(
                ThreadRevision.thread_id == w.thread_id).count(),
            "ledger": db.query(BookingCommandRecord).count(),
            "offer": db.execute(select(WhatsAppBookingOffer.status).where(
                WhatsAppBookingOffer.thread_id == w.thread_id)).scalars().all(),
        }


# ══ 1. the emergency flag: exactly two meanings ═════════════════════════════════════════

class P3_Flag(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def _turn(self, flag_value):
        import dataclasses
        w = H.seed(self.engine)
        value = mock.MagicMock() if flag_value == "mock" else flag_value
        cfg = dataclasses.replace(H.settings(), smart_booking_ce_enabled=value)
        with _Calls(BookingAuthority, "execute") as ex, H.Meta() as m:
            out, eng = H.run_turn(self.engine, H.flow_event(w), cfg=cfg)
        return w, out, ex.calls, m

    def test_p3u_01_true_books_through_the_authority_only(self):
        w, out, calls, m = self._turn(True)
        self.assertEqual((out.action, calls), ("booking_created", 1))
        self.assertEqual(_rows(self.engine, w)["offer"], ["ACCEPTED"])
        self.assertEqual([t for _w, t in m.sent], [H.OWNER_TEXT])

    def test_p3u_02_false_unset_malformed_and_merely_truthy_are_all_off(self):
        # False; None (unset); the string "true"; 1; a MagicMock attribute (truthy object).
        for value in (False, None, "true", 1, "mock"):
            with self.subTest(value=value):
                w, out, calls, m = self._turn(value)
                f = _rows(self.engine, w)
                self.assertEqual((out.action, out.detail, calls),
                                 ("booking_refused_handoff", "SMART_BOOKING_DISABLED", 0))
                self.assertEqual((f["revisions"], f["thread_revisions"], f["ledger"]), (0, 0, 0))
                self.assertEqual(f["offer"], ["ISSUED"], "OFF never consumes the offer")
                self.assertEqual([t for _w, t in m.sent], [H.HANDOFF_TEXT])
                self.assertNotIn(H.OWNER_TEXT, [t for _w, t in m.sent], "OFF never says booked")
                with Session(self.engine) as db:
                    st = db.execute(select(WhatsAppThreadState).where(
                        WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
                    # the turn is consumed as a HANDOFF, never as though the authority committed
                    self.assertEqual((st.last_processed_inbound_wa_message_id, st.needs_human,
                                      st.last_stage), (w.wamid, True, "HUMAN_REQUIRED"))

    def test_p3u_03_the_default_is_off(self):
        from app.settings import get_settings
        self.assertIs(get_settings().smart_booking_ce_enabled, False)
        eng = ConversationEngine.__new__(ConversationEngine)
        eng.settings = mock.MagicMock()
        self.assertFalse(eng._smart_booking_ce_on())

    def test_p3u_04_data_exchange_reads_the_flag_per_request(self):
        """Two consecutive confirmations of the same world: OFF, then ON. The first hands off
        and books nothing; the second (the flag read anew) books once."""
        w = H.seed(self.engine)
        with H.Meta() as m, _Calls(BookingAuthority, "execute") as ex:
            with self.assertRaises(bfs.BookingSlotConflictError) as off:
                H.dx_confirm(self.engine, w, flag=False)
            self.assertEqual(off.exception.refreshed_data["data"]["slot_conflict_message"],
                             H.HANDOFF_TEXT)
            self.assertEqual((ex.calls, _rows(self.engine, w)["revisions"]), (0, 0))
            on = H.dx_confirm(self.engine, w, flag=True)
        self.assertEqual((on["screen"], ex.calls, _rows(self.engine, w)["revisions"]),
                         ("SUCCESS", 1, 1))
        self.assertEqual([t for _w, t in m.sent], [H.OWNER_TEXT])

    def test_p3u_05_data_exchange_off_hands_the_thread_to_a_person(self):
        w = H.seed(self.engine)
        with H.Meta():
            with self.assertRaises(bfs.BookingSlotConflictError):
                H.dx_confirm(self.engine, w, flag=False)
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            self.assertTrue(st.needs_human, "the screen promised an advisor")
            self.assertTrue(db.get(Lead, w.lead_id).necesita_humano)


# ══ 2. no legacy writer under any flag ═════════════════════════════════════════════════

class P3_NoLegacyWriter(unittest.TestCase):

    def _functions(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        return {n.name: ast.unparse(n) for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def test_p3u_10_neither_transport_builds_a_booking_row(self):
        for rel in ("services/conversation_engine.py", "services/booking_flow_service.py"):
            for name, body in self._functions(APP / rel).items():
                with self.subTest(file=rel, fn=name):
                    self.assertNotIn("ThreadRevision(", body)
                    self.assertNotIn("Revision(lead_id", body)
                    self.assertNotIn("_offers.claim(", body)
                    self.assertNotIn("_offers.consume(", body)

    def test_p3u_11_no_one_argument_booking_advisory_lock_exists(self):
        for path in sorted(APP.rglob("*.py")):
            text = path.read_text(encoding="utf-8-sig")
            with self.subTest(file=path.name):
                self.assertNotIn("pg_try_advisory_xact_lock", text)
                self.assertNotIn("_acquire_advisory_lock", text)
                self.assertNotIn("_revalidate_booking_slot", text)

    def test_p3u_12_the_turn_entry_acquisition_is_gone(self):
        import textwrap
        src = ast.unparse(ast.parse(textwrap.dedent(inspect.getsource(ConversationEngine._handle))))
        self.assertNotIn("_EntryOffers", src)
        self.assertEqual(src.count("begin_offer_transaction"), 0)
        self.assertEqual(src.count("_enter_cycle_reset_bound("), 1)

    def test_p3u_13_the_legacy_acknowledgement_planner_has_no_production_caller(self):
        callers = []
        for path in sorted(APP.rglob("*.py")):
            for name, body in self._functions(path).items():
                for planner in ("_plan_flow_completion_ack(", "_flow_transaction_key(",
                                "_flow_ack_already_sent(", "build_booking_receipt_message("):
                    if planner in body and not name.startswith(planner.rstrip("(")):
                        callers.append((path.name, name, planner))
        allowed = {("conversation_engine.py", "_plan_flow_completion_ack", "_flow_transaction_key("),
                   ("conversation_engine.py", "_plan_flow_completion_ack",
                    "_flow_ack_already_sent(")}
        self.assertEqual(set(callers) - allowed, set(),
                         "a legacy acknowledgement helper is reachable from production code")

    def test_p3u_14_every_flag_state_writes_through_the_authority_or_not_at_all(self):
        """Behaviour, both flag states, every booking-shaped CE path in this harness: the only
        rows ever written are written inside `BookingAuthority.execute`."""
        from app.services.booking_authority import booking_transaction_active
        outside = []

        def guard(session, flush_context, instances):
            for obj in list(session.new) + list(session.dirty):
                if isinstance(obj, (Revision, ThreadRevision)) and booking_transaction_active() is None:
                    outside.append(type(obj).__name__)
        from sqlalchemy import event
        event.listen(Session, "before_flush", guard)
        try:
            for flag in (True, False):
                engine = H.sqlite_engine()
                for w in (H.seed(engine), H.seed(engine, cycle_reset=True),
                          H.seed(engine, needs_human=True)):
                    with H.Meta():
                        H.run_turn(engine, H.flow_event(w), cfg=H.settings(flag=flag))
        finally:
            event.remove(Session, "before_flush", guard)
        self.assertEqual(outside, [])


# ══ 3. ledger first, and re-read before every terminal answer ═══════════════════════════

class P3_LedgerFirst(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def test_p3u_20_a_hit_precedes_the_cursor_the_reset_and_needs_human(self):
        w = H.seed(self.engine)
        with H.Meta():
            H.dx_confirm(self.engine, w)                     # committed + acknowledged
        with Session(self.engine) as db:                     # then: reset pending, human owner
            db.execute(update(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id).values(
                cycle_reset_pending=True, needs_human=True))
            db.commit()
        with _Calls(ConversationEngine, "_execute_cycle_reset") as reset, \
                _Calls(BookingAuthority, "execute") as ex, H.Meta() as m:
            out, eng = H.run_turn(self.engine, H.flow_event(w))
        self.assertEqual((out.action, reset.calls, ex.calls, m.sent),
                         ("booking_replayed", 0, 0, []))
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            self.assertEqual((st.last_processed_inbound_wa_message_id, st.cycle_reset_pending),
                             (w.wamid, True), "the reset is left for the next ordinary turn")
            rec = db.query(BookingCommandRecord).one()
            self.assertEqual((rec.ack_status, rec.replay_count), ("SENT", 1))

    def test_p3u_21_every_terminal_branch_rereads_the_ledger(self):
        """Each pre-execute refusal of the converged CE path, with a booking committed by the
        Data Exchange BETWEEN the turn's entry lookup and the refusal: the refusal is replaced
        by the replay. A branch that skipped the re-read would hand off a booked customer."""
        cases = {
            "SMART_BOOKING_DISABLED": dict(cfg=H.settings(flag=False)),
            "FLOW_TOKEN_NOT_ACTIVE": dict(mirror="other-token"),
            "OFFER_CANDIDATE_CHANGED": dict(bound=None),
            "NO_SUBMITTED_SLOT": dict(slot=(None, None)),
            "NO_CANONICAL_LOCATION": dict(location=(None, None)),
            "CE_TRANSACTION_NOT_CLEAN": dict(clean=False),
        }
        for reason, kw in cases.items():
            with self.subTest(reason=reason):
                engine = H.sqlite_engine()
                w = H.seed(engine)
                state = {"booked": False}
                real_hit = ConversationEngine._sb_ledger_hit

                def hit(eng, key):
                    # the entry lookup misses; the DX commits; the re-read must find it
                    if not state["booked"]:
                        state["booked"] = True
                        with H.Meta():
                            H.dx_confirm(engine, w)
                        return None
                    return real_hit(eng, key)
                patches = [mock.patch.object(ConversationEngine, "_sb_ledger_hit", hit)]
                if "mirror" in kw:
                    patches.append(mock.patch.object(
                        ConversationEngine, "_converged_flow_book",
                        _with_mirror(ConversationEngine._converged_flow_book, kw["mirror"])))
                if "bound" in kw:
                    patches.append(mock.patch.object(ConversationEngine, "_offer_bound_candidate",
                                                     lambda *a, **k: None))
                if "slot" in kw:
                    patches.append(mock.patch.object(ConversationEngine, "_submitted_booking_slot",
                                                     staticmethod(lambda st: (None, None))))
                if "location" in kw:
                    patches.append(mock.patch.object(ConversationEngine, "_inspection_location_for",
                                                     staticmethod(lambda c, s: (None, None))))
                if "clean" in kw:
                    patches.append(mock.patch.object(ConversationEngine, "_sb_transaction_clean",
                                                     lambda eng: False))
                for p in patches:
                    p.start()
                try:
                    with H.Meta() as m:
                        out, eng = H.run_turn(engine, H.flow_event(w),
                                              cfg=kw.get("cfg") or H.settings())
                finally:
                    for p in patches:
                        p.stop()
                self.assertEqual(out.action, "booking_replayed", reason)
                self.assertNotIn(H.HANDOFF_TEXT, [t for _w, t in m.sent], reason)
                self.assertEqual(_rows(engine, w)["revisions"], 1)

    def test_p3u_22_a_refused_execute_rereads_the_ledger(self):
        """The authority refuses (here: the key was committed by the Data Exchange inside the
        execute window), and the B-1 re-read turns it into the replay."""
        w = H.seed(self.engine)
        real = adapter.book_from_flow_response

        def dx_first(db, req, **k):
            with H.Meta():
                H.dx_confirm(self.engine, w)
            return real(db, req, **k)
        with mock.patch.object(adapter, "book_from_flow_response", dx_first), H.Meta() as m:
            out, eng = H.run_turn(self.engine, H.flow_event(w))
        self.assertEqual((out.action, m.sent), ("booking_replayed", []))
        self.assertEqual(_rows(self.engine, w)["revisions"], 1)

    def test_p3u_23_a_rolled_back_attempt_is_a_fresh_attempt_not_a_replay(self):
        w = H.seed(self.engine)
        with mock.patch.object(BookingAuthority, "_write", side_effect=RuntimeError("boom")), \
                H.Meta():
            out1, _ = H.run_turn(self.engine, H.flow_event(w))
        self.assertEqual(out1.detail, "INTERNAL")
        self.assertEqual(_rows(self.engine, w)["ledger"], 0)
        # the refusal handed off; an operator clears the human owner and the customer submits
        with Session(self.engine) as db:
            db.execute(update(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id).values(needs_human=False))
            db.commit()
        with H.Meta() as m:
            out2, _ = H.run_turn(self.engine, H.flow_event(w, wamid=w.wamid + ".2"))
        self.assertEqual(out2.action, "booking_created")
        self.assertEqual(_rows(self.engine, w)["ledger"], 1)
        self.assertEqual([t for _w, t in m.sent], [H.OWNER_TEXT])

    def test_p3u_24_a_detached_key_never_rebooks_and_never_acknowledges(self):
        w = H.seed(self.engine)
        with H.Meta():
            H.run_turn(self.engine, H.flow_event(w))
        with Session(self.engine) as db:                     # the production detach (reset)
            from app.services.tester_reset import _detach_ledger
            self.assertEqual(_detach_ledger(db, [w.thread_id], [w.lead_id]), 1)
            db.commit()
            r = BookingAuthority(db).lookup(flow_idempotency_key(w.token))
        self.assertEqual(r.outcome.value, "COMMAND_DETACHED")
        self.assertIsNone(adapter.lookup_booking(H.session_factory(self.engine)(),
                                                 flow_idempotency_key(w.token)))
        with _Calls(BookingAuthority, "claim_acknowledgement") as claims, H.Meta() as m:
            out, eng = H.run_turn(self.engine, H.flow_event(w, wamid=w.wamid + ".2"))
        self.assertNotIn(out.action, ("booking_created", "booking_replayed"))
        self.assertEqual((claims.calls, [t for _w, t in m.sent if t == H.OWNER_TEXT]), (0, []))
        self.assertEqual(_rows(self.engine, w)["revisions"], 1)


def _with_mirror(real, mirror):
    def wrapped(self, ctx, state, flow_data, flow_token):
        state.flow_booking_token = mirror
        return real(self, ctx, state, flow_data, flow_token)
    return wrapped


# ══ 4. one key per Flow submission; hostile values refused ══════════════════════════════

class P3_Keys(unittest.TestCase):

    def test_p3u_30_ce_and_data_exchange_derive_the_same_key(self):
        token = "41-1792000000-abcdef0123456789"
        ce = adapter.flow_booking_key(41, token, "wamid.X")
        dx = flow_idempotency_key(token)
        self.assertEqual(ce, dx)
        from app.services.offer_lifecycle import token_fingerprint
        from app.services.booking_authority import flow_idempotency_key_for_fingerprint
        self.assertEqual(dx, flow_idempotency_key_for_fingerprint(token_fingerprint(token)))

    def test_p3u_31_a_hostile_str_subclass_is_refused_unread(self):
        touched = []

        class Hostile(str):
            def __hash__(self):
                touched.append("hash")
                return str.__hash__(self)

            def __eq__(self, other):
                touched.append("eq")
                return str.__eq__(self, other)

            def __format__(self, spec):
                touched.append("format")
                return "x"
        fields = {Hostile("tipo_vehiculo"): "AUTO"}           # building it hashes once
        touched.clear()
        r = adapter._fields_refusal(fields)
        self.assertEqual((r.outcome.value, r.reason), ("VALIDATION_FAILED", "CE_FIELD_KEY_NOT_STR"))
        self.assertEqual(touched, [])
        self.assertIsNone(adapter.flow_booking_key(1, Hostile("tok"), "wamid"))

    def test_p3u_32_a_hostile_token_cannot_reach_the_authority(self):
        engine = H.sqlite_engine()
        w = H.seed(engine)

        class Hostile(str):
            pass
        ev = H.flow_event(w)
        object.__setattr__(ev, "flow_token", Hostile(w.token))
        with _Calls(BookingAuthority, "execute") as ex, H.Meta():
            out, _ = H.run_turn(engine, ev)
        self.assertEqual(ex.calls, 0)
        self.assertEqual(out.action, "booking_refused_handoff")


# ══ 5. CE synchronisation owns CE fields only; one acknowledgement ══════════════════════

class P3_Sync(unittest.TestCase):

    def setUp(self):
        self.engine = H.sqlite_engine()

    def test_p3u_40_sync_rewrites_no_authority_owned_field(self):
        w = H.seed(self.engine)
        seen = {}
        real = ConversationEngine._sb_synchronize

        def observe(eng, ctx, **kw):
            with Session(self.engine) as db:
                seen["before"] = _booking_rows(db, w)
            out = real(eng, ctx, **kw)
            with Session(self.engine) as db:
                seen["after"] = _booking_rows(db, w)
            return out
        with mock.patch.object(ConversationEngine, "_sb_synchronize", observe), H.Meta():
            out, eng = H.run_turn(self.engine, H.flow_event(w))
        self.assertEqual(out.action, "booking_created")
        self.assertEqual(seen["before"], seen["after"],
                         "the CE synchronisation rewrote booking-owned state")
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            self.assertEqual(st.last_processed_inbound_wa_message_id, w.wamid)

    def test_p3u_41_sync_cannot_clear_a_human_owner(self):
        w = H.seed(self.engine)
        with H.Meta():
            H.run_turn(self.engine, H.flow_event(w))
        with Session(self.engine) as db:
            st = db.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
            self.assertTrue(st.needs_human)             # set by the authority, kept by sync
        src = inspect.getsource(ConversationEngine._sb_synchronize)
        self.assertNotIn("needs_human", src)

    def test_p3u_42_sync_retry_is_idempotent(self):
        w = H.seed(self.engine)
        with mock.patch.object(ConversationEngine, "_sb_synchronize",
                               lambda eng, ctx, **kw: False), H.Meta() as m1:
            out1, _ = H.run_turn(self.engine, H.flow_event(w))   # booking + ack, sync lost
        self.assertEqual(out1.action, "booking_created")
        with Session(self.engine) as db:
            before = _booking_rows(db, w)
        with H.Meta() as m2:
            out2, _ = H.run_turn(self.engine, H.flow_event(w))   # redelivery: ledger hit
            out3, _ = H.run_turn(self.engine, H.flow_event(w))   # and the dedup after it
        with Session(self.engine) as db:
            self.assertEqual(_booking_rows(db, w), before)
        self.assertEqual((out2.action, out3.action), ("booking_replayed", "skipped_dedup"))
        self.assertEqual(([t for _w, t in m1.sent], m2.sent), ([H.OWNER_TEXT], []))

    def test_p3u_43_one_success_copy_one_sender(self):
        from app.services import booking_acknowledgement as ack
        self.assertIs(ConversationEngine._FLOW_COMPLETION_ACK, ack.BOOKING_FLOW_RECEIPT_TEXT)
        holders = [p.name for p in sorted(APP.rglob("*.py"))
                   if ack.BOOKING_FLOW_RECEIPT_TEXT in p.read_text(encoding="utf-8-sig")]
        self.assertEqual(holders, ["booking_acknowledgement.py"])

    def test_p3u_44_the_acknowledgement_carries_the_causal_inbound_through_the_gate(self):
        """B-5: CE sends through the gate on BOOKING_FLOW with THIS turn's causal inbound, so
        the gate's dedup is scoped to the inbound that caused it (WILD-01 FINDING-03)."""
        from app.models import WhatsAppOutboundDedup
        w = H.seed(self.engine)
        with H.Meta():
            H.run_turn(self.engine, H.flow_event(w))
        with Session(self.engine) as db:
            rows = db.execute(select(WhatsAppOutboundDedup.causal_inbound_wa_message_id)).all()
            out = db.execute(select(WhatsAppMessage_.path_id, WhatsAppMessage_.text).where(
                WhatsAppMessage_.thread_id == w.thread_id,
                WhatsAppMessage_.direction == "out")).all()
        self.assertEqual([r[0] for r in rows], [w.wamid])
        self.assertEqual([tuple(r) for r in out], [("BOOKING_FLOW", H.OWNER_TEXT)])

    def test_p3u_45_the_location_is_the_offer_bound_candidates_not_the_focus(self):
        """P2-04. The offer is bound to candidate A (Norte/Benavidez); the thread's focus
        POINTER is NULL and another candidate B (Sur/Quilmes) carries `current_focus`, so
        `_focus_candidate` would answer B. The booking is A's, at A's location."""
        w = H.seed(self.engine)
        with Session(self.engine) as db:
            from app.models import WhatsAppThreadCandidate
            db.add(WhatsAppThreadCandidate(thread_id=w.thread_id, marca="Fiat", modelo="Uno",
                                           tipo_vehiculo="Auto", status="current_focus",
                                           zone_group="Sur", zone_detail="Quilmes"))
            db.execute(update(WhatsAppThreadCandidate).where(
                WhatsAppThreadCandidate.id == w.candidate_id).values(status="mentioned"))
            db.execute(update(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == w.thread_id).values(
                current_focus_candidate_id=None))
            db.commit()
        with H.Meta():
            out, eng = H.run_turn(self.engine, H.flow_event(w))
        self.assertEqual(out.action, "booking_created")
        with Session(self.engine) as db:
            rev = db.query(Revision).filter(Revision.lead_id == w.lead_id).one()
            self.assertEqual((rev.zone_group, rev.zone_detail, rev.marca),
                             ("Norte", "Benavidez", "Peugeot"))

    def test_p3u_46_no_outbound_attempt_happens_inside_the_booking_transaction(self):
        """Stage 7–8 rule: nothing customer-visible is ATTEMPTED while the authority's
        transaction (and its K1..K9 locks) is open. Every gate attempt of a booking turn is
        observed with the booking scope closed — the acknowledgement included."""
        from app.services.booking_authority import booking_transaction_active
        from app.services.outbound_safety_gate import OutboundSafetyGate
        seen = []
        real = OutboundSafetyGate.attempt

        def spy(gate, *a, **k):
            seen.append(booking_transaction_active())
            return real(gate, *a, **k)
        w = H.seed(self.engine)
        with mock.patch.object(OutboundSafetyGate, "attempt", spy), H.Meta() as m:
            out, eng = H.run_turn(self.engine, H.flow_event(w))
            from datetime import time as _time
            self.assertEqual(H.dx_confirm(self.engine, H.seed(self.engine, at=_time(11, 30)))
                             ["screen"], "SUCCESS")
        self.assertEqual(out.action, "booking_created")
        self.assertTrue(seen, "no outbound attempt observed — this test would prove nothing")
        self.assertEqual(set(seen), {None}, f"an outbound attempt inside the booking: {seen}")


from app.models import WhatsAppMessage as WhatsAppMessage_  # noqa: E402



def _booking_rows(db, w):
    st = db.execute(select(WhatsAppThreadState).where(
        WhatsAppThreadState.thread_id == w.thread_id)).scalar_one()
    return {
        "revisions": [tuple(r) for r in db.execute(select(
            Revision.id, Revision.turno_fecha, Revision.turno_hora, Revision.estado_revision,
            Revision.booking_version).where(Revision.lead_id == w.lead_id)).all()],
        "thread_revisions": [tuple(r) for r in db.execute(select(
            ThreadRevision.id, ThreadRevision.status, ThreadRevision.offer_id,
            ThreadRevision.scheduled_date).where(ThreadRevision.thread_id == w.thread_id)).all()],
        "offer": [tuple(r) for r in db.execute(select(
            WhatsAppBookingOffer.status, WhatsAppBookingOffer.accepted_revision_id,
            WhatsAppBookingOffer.accepted_thread_revision_id).where(
            WhatsAppBookingOffer.thread_id == w.thread_id)).all()],
        "ledger": [tuple(r) for r in db.execute(select(
            BookingCommandRecord.idempotency_key, BookingCommandRecord.revision_id,
            BookingCommandRecord.thread_revision_id, BookingCommandRecord.ack_status)).all()],
        "state": (st.current_revision_id, st.last_stage, st.needs_human, st.flow_booking_token),
        "lead": tuple(db.execute(select(Lead.estado, Lead.flag, Lead.necesita_humano).where(
            Lead.id == w.lead_id)).one()),
    }


if __name__ == "__main__":                                   # pragma: no cover
    unittest.main()
