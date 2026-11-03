"""PRELAUNCH-MINIMUM-LAUNCH (A) + R1 — what the customer who loses a slot race is told, and how
they book again: a DURABLE RECOVERY JOB, admitted by the Data Exchange request and executed by
a background worker.

The 2026-11-01 Wild measured F3a: after a terminal SUMMARY the handset cannot render the
refusal screen, so the Meta response is not the recovery mechanism (it is left exactly as it
is; the published Flow JSON is not touched). Recovery happens in the WhatsApp conversation.

The R1 independent review (audit 2026-11-03) measured the first design's defects: a claim
whose claimant died, raised or met a contended lock was SILENT (F-01); its provider calls ran
INSIDE the Data Exchange request, on the event loop, for an unbounded time (F-02); and the
claim used `booking_attempt_events` — forensic, "never authority" — as its admission (F-05).
This module is the correction.

ADMISSION — in the Data Exchange request, NO provider call (`admit_recovery`)
    After the typed SLOT_CONFLICT refusal is durable and the ledger re-read missed, ONE
    transaction under K1 (the thread-state row, bounded wait):
      * INSERT one `slot_recovery_jobs` row (UNIQUE recovery_identity = the losing offer)
        … ON CONFLICT DO NOTHING — concurrent requests for one losing offer create exactly
        one job; the others see it and do nothing;
      * stage SCHEDULING, `thread_state.needs_human = true`, `lead.necesita_humano = true`,
        recording in the job which flags IT turned false → true (only those it may clear).
    If K1 stays contended the job is still inserted (visible on the ops surface at once) and
    the worker raises the flags first, before anything is sent. The request then returns the
    unchanged refusal screen. A process death after this commit leaves a PENDING job that is
    listed for an operator and claimed by the worker.

WORKER — a dedicated thread, never the event loop (`run_once`, `slot_recovery_loop`)
    claim      conditional UPDATE: claim_token + lease, attempts + 1, only when the job is
               unclaimed or its lease expired. One active claimant per job.
    text       PENDING → SENDING_TEXT (committed, with the attempt's delivery identity)
               → gate + provider under a TOTAL deadline → TEXT_SENT (WAMID).
               Attempt 2 only after a DEFINITE non-delivery; ambiguity is never retried.
    flow       TEXT_SENT → (K1: losing offer must still own the thread; fresh offer issued,
               superseding it) + SENDING_FLOW, in ONE transaction → gate + provider under the
               deadline → RECOVERED. A Flow is never sent without a text WAMID in front of it.
    resume     a lease-expired SENDING_* job is inspected through the gate's own outbound row
               for that step's delivery identity: no row → nothing crossed → resumed; a
               `sent` row with a WAMID → recorded and continued; anything else (pending,
               failed, blocked) is UNCERTAIN → HANDOFF. Never re-sent.
    stop       HANDOFF (a classified degraded stop; the flags stay, a person continues) or
               FAILED (unexpected: internal error / lock contention / claims exhausted after
               bounded retries; the flags stay). RECOVERED clears ONLY the flags this job
               raised, and only if nothing else on the thread or lead needs a person.

TRANSPORT DEADLINE (`bounded_call`)
    The provider call runs on its own daemon thread; the worker waits at most
    `TRANSPORT_DEADLINE_SECONDS` for it, whatever it is blocked on (DNS, connect, TLS, read).
    Past the deadline the step is UNCERTAIN (typed TRANSPORT_DEADLINE_EXCEEDED) → HANDOFF.
    The underlying network operation is NOT cancelled — Python cannot cancel a blocking
    socket call — it stays alive on its daemon thread until urllib's own per-operation
    timeout ends it, and its late result is discarded (that thread never touches the
    database). At most `MAX_INFLIGHT_TRANSPORTS` such threads may be alive; with none free
    the worker defers (nothing is claimed into a transport) and the jobs stay listed.

What is proven, and what is not claimed:
  * exactly one job per losing offer (database UNIQUE), admitted with the human flags;
  * no provider call inside the Data Exchange request;
  * at most one successful provider send per delivery identity (the gate admits one identity
    once, ever — also across a lease expiry), ≤ 2 text attempts (the 2nd only after a definite
    failure), ≤ 1 Flow, never a Flow without a text WAMID;
  * every admitted job is listed on `/api/ops/booking-attention` until it is RECOVERED, and
    HANDOFF / FAILED stay listed.
  Exactly-once delivery by Meta is NOT claimed.

SEND REFUSAL — R2 (audit 2026-11-03 R1-01 / R1-02)
    A recovery sends ONLY while `booking_dispatch_allowed()` (SMART_BOOKING_CE_ENABLED, the
    one WILD1-F1 policy) is True AND the worker has not been told to stop. OUTBOUND_ENABLED
    alone is never sufficient. `send_refusal()` is checked at every boundary — on the pass
    before each claim (stop only), after the flags are up, before the text, before a text
    retry, before a text or Flow resume after a lease expiry, after the text and before the
    fresh offer, before the Flow — and LAST inside `bounded_call`, under `_SEND_LOCK`,
    immediately before the provider thread starts. `stop_worker()` sets the stop signal
    under the same lock, so once it returns no provider call can begin.
    A job met by a refusal is NEVER deferred until the flags come back (that would be a stale
    send): it ends HANDOFF at once, durably, with the human flags kept and the job listed
    (reason SLOT_RECOVERY_DISPATCH_OFF / SLOT_RECOVERY_WORKER_STOPPED). A fresh offer already
    issued for a Flow that is not sent is WITHDRAWN. A process killed before that transition
    leaves a lease that expires; the next process re-checks the policy before any resumed
    step, so an OFF restart sends nothing either.

WORKER LIFECYCLE — `start_worker()` / `stop_worker()`, owned by the application's startup and
    shutdown handlers. One daemon thread (it can never keep the process alive); startup is
    idempotent; shutdown sets the stop signal FIRST, then joins for a bounded time. A
    provider call already in flight is not cancelled (Python cannot): the worker stops
    waiting for it, records the step as uncertain (HANDOFF), and its late result is dropped.

Customer copy is fixed text; no code, reason, exception or SQL reaches a customer. Logs carry
ids and typed codes only. `booking_attempt_events` is neither written nor read here.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import and_, or_, select, update

logger = logging.getLogger(__name__)

#: Owner-approved launch copy (PRELAUNCH-MINIMUM-LAUNCH A): the slot is gone, nothing was
#: booked, another may be chosen — and nothing else.
RECOVERY_TEXT = ("Ese horario acaba de dejar de estar disponible. No se confirmó ningún "
                 "turno. Podés elegir otro horario.")
#: The body of the fresh Flow that follows the explanation.
RECOVERY_FLOW_BODY = ("Estos son los horarios disponibles ahora. Elegí uno y confirmá tu "
                      "turno.")
RECOVERY_FLOW_CTA = "Reservar turno"

MAX_TEXT_ATTEMPTS = 2
#: Total wall-clock the worker waits for ONE provider call (DNS + connect + TLS + read).
TRANSPORT_DEADLINE_SECONDS = 8.0
#: Provider calls that may still be alive after their deadline (abandoned, not cancelled).
MAX_INFLIGHT_TRANSPORTS = 4
#: A claim's lease. Far above one step's bound (two DB transactions + one deadline), so a live
#: claimant always records before it expires; an expired lease means the claimant is gone.
LEASE_SECONDS = 60
CHECK_INTERVAL_SECONDS = 5
#: Retry delay after lock contention, an internal error or no free transport slot.
RETRY_AFTER_SECONDS = 10
LOCK_TIMEOUT_MS = 1500
#: K1 attempts inside one admission / one worker step before deferring.
LOCK_TRIES = 2
#: A job not terminal after this long is listed as STALE (bounded age).
STALE_AFTER_SECONDS = 120
#: How long terminal degraded jobs and attention conditions stay listed.
ATTENTION_LOOKBACK_DAYS = 7

TERMINAL = ("RECOVERED", "HANDOFF", "FAILED")
NONTERMINAL = ("PENDING", "SENDING_TEXT", "TEXT_SENT", "SENDING_FLOW")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _h16(value) -> Optional[str]:
    if value is None or value == "":
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def recovery_identity(offer_id: int) -> str:
    """The recovery's identity: one per LOSING OFFER (64 lowercase hex).

    Per offer, not per submission: every resubmission of the same dead Flow — a Meta
    transport retry, a new tap with a fresh IV — loses for the same offer and must not
    produce a second explanation. A customer who loses AGAIN on the fresh Flow loses on a
    NEW offer and is owed a new recovery."""
    if type(offer_id) is not int and not str(offer_id).isdigit():
        raise ValueError("a slot recovery needs the losing offer's id")
    return hashlib.sha256(f"slot-conflict-recovery|v1|offer:{int(offer_id)}".encode()
                          ).hexdigest()


def delivery_identity(identity: str, kind: str, attempt: int = 1) -> str:
    """The gate delivery identity of one send of this recovery (64 lowercase hex)."""
    if kind not in ("text", "flow"):
        raise ValueError(kind)
    if kind == "text" and attempt not in range(1, MAX_TEXT_ATTEMPTS + 1):
        raise ValueError("a recovery text has at most two attempts")
    if kind == "flow" and attempt != 1:
        raise ValueError("a recovery dispatches at most one Flow")
    return hashlib.sha256(f"{identity}|{kind}|{attempt}".encode()).hexdigest()


def origin_delivery_hash(attempt_ref: Optional[str]) -> Optional[str]:
    """16-hex digest of a Data Exchange delivery reference (itself already a digest)."""
    return _h16(attempt_ref) if isinstance(attempt_ref, str) and attempt_ref.strip() else None


# ══ send refusal: Smart Booking OFF or the worker stopping (R2) ═══════════════════════════

#: Typed refusal reasons (also the suffix of the terminal HANDOFF outcome).
DISPATCH_OFF = "DISPATCH_OFF"
WORKER_STOPPING = "WORKER_STOPPING"

#: The current worker's stop signal. Each `start_worker` makes a new one, and a worker thread
#: reads ITS OWN (`_PASS.stop`), so a restarted worker can never un-stop an old one.
_STOP = threading.Event()
_PASS = threading.local()
#: Held while the last check runs and the provider thread starts; `stop_worker` sets the
#: signal under it. So: either a call had started before the signal, or it never starts.
_SEND_LOCK = threading.Lock()

#: Deterministic test barrier: called with (stage, job id) at every boundary BEFORE the
#: check. None in production.
_STAGE_HOOK = None


def _current_stop() -> threading.Event:
    return getattr(_PASS, "stop", None) or _STOP


def stopping() -> bool:
    return _current_stop().is_set()


def send_refusal() -> Optional[str]:
    """None when a recovery may send now; otherwise the typed reason it may not.

    The canonical WILD1-F1 policy, `booking_dispatch_allowed()`, decides Smart Booking — no
    second reading of the flag. Any failure to answer fails CLOSED."""
    if stopping():
        return WORKER_STOPPING
    try:
        from .booking_dispatch_gate import booking_dispatch_allowed
        allowed = booking_dispatch_allowed()
    except Exception:                                    # fail closed
        allowed = False
    return None if allowed is True else DISPATCH_OFF


def _stage(stage: str, job_id) -> None:
    hook = _STAGE_HOOK
    if hook is not None:
        hook(stage, job_id)


class _Halt(Exception):
    """A boundary refused: nothing more may be sent for this job, now or later."""

    def __init__(self, reason: str, stage: str):
        super().__init__(f"{reason}@{stage}")
        self.reason, self.stage = reason, stage


def _guard(stage: str, claim) -> None:
    _stage(stage, claim.job_id)
    why = send_refusal()
    if why is not None:
        raise _Halt(why, stage)


# ══ bounded transport ═════════════════════════════════════════════════════════════════════

class TransportDeadlineExceeded(Exception):
    """The provider call did not return within its deadline (or the worker stopped waiting
    for it at shutdown). It was NOT cancelled."""

    def __init__(self, message: str, *, shutdown: bool = False):
        super().__init__(message)
        self.shutdown = shutdown


_INFLIGHT = threading.BoundedSemaphore(MAX_INFLIGHT_TRANSPORTS)
_inflight_lock = threading.Lock()
_inflight_count = 0


def inflight_transports() -> int:
    with _inflight_lock:
        return _inflight_count


def transport_capacity_available() -> bool:
    return inflight_transports() < MAX_INFLIGHT_TRANSPORTS


def bounded_call(fn, kwargs: dict, deadline: float, *, guard=None, stop=None):
    """Run `fn(**kwargs)` on its own daemon thread and wait at most `deadline` seconds.

    Returns its result or re-raises its exception. Past the deadline raises
    TransportDeadlineExceeded: the call is ABANDONED, not cancelled — it keeps its thread (and
    a transport slot) until it ends by itself; its result is then discarded. The thread runs
    ONLY `fn` (the provider call), never a database operation.

    R2: `guard()` (→ None or a refusal reason) is the LAST check, made under `_SEND_LOCK`
    immediately before the provider thread starts; a refusal raises OutboundBlockedError
    (definite: nothing left the process). `stop` (an Event) ends the WAIT early — the call
    itself keeps running, exactly as past the deadline."""
    global _inflight_count
    from .outbound_guard import OutboundBlockedError
    if not _INFLIGHT.acquire(blocking=False):
        # nothing left the process: a definite, pre-dispatch non-send (the worker checks
        # capacity before every step, so this is a last line, not the normal path)
        raise OutboundBlockedError(sender_path="slot_recovery.bounded_call",
                                   kind="TRANSPORT_CAPACITY", to_wa_id="",
                                   gate_outcome="TRANSPORT_CAPACITY_EXHAUSTED")
    box: dict = {}

    def run():
        global _inflight_count
        try:
            box["result"] = fn(**kwargs)
        except BaseException as exc:          # delivered to the waiting worker, if any
            box["error"] = exc
        finally:
            with _inflight_lock:
                _inflight_count -= 1
            _INFLIGHT.release()

    t = threading.Thread(target=run, name="slot-recovery-transport", daemon=True)
    with _SEND_LOCK:
        why = guard() if guard is not None else None
        if why is not None:
            _INFLIGHT.release()
            raise OutboundBlockedError(sender_path="slot_recovery.bounded_call",
                                       kind="RECOVERY_SEND_REFUSED", to_wa_id="",
                                       gate_outcome=why)
        with _inflight_lock:
            _inflight_count += 1
        t.start()
    end = time.monotonic() + deadline
    while t.is_alive():
        left = end - time.monotonic()
        if left <= 0:
            raise TransportDeadlineExceeded(f"provider call exceeded {deadline:.1f}s")
        if stop is not None and stop.is_set():
            raise TransportDeadlineExceeded("worker stopped waiting at shutdown", shutdown=True)
        t.join(min(left, 0.05))
    if "error" in box:
        raise box["error"]
    return box["result"]


class _Deadline:
    """A provider sender wrapped in `bounded_call`, resolved at CALL time (so a test's
    patched sender is the one bounded). Remembers whether its deadline fired, and whether
    the last check refused (R2: `stage` names that boundary)."""

    def __init__(self, resolve, deadline: float, *, stage: str = "transport", job_id=None):
        self._resolve, self._deadline = resolve, deadline
        self._stage, self._job_id = stage, job_id
        self.fired = False
        self.shutdown = False
        self.refused: Optional[str] = None

    def _guard(self) -> Optional[str]:
        # runs under _SEND_LOCK: the barrier here may not call stop_worker (tests use it to
        # prove the lock's ordering from another thread)
        _stage(self._stage + "_locked", self._job_id)
        self.refused = send_refusal()
        return self.refused

    def __call__(self, **kwargs):
        _stage(self._stage, self._job_id)    # just before the lock: the last moment a stop
        try:
            return bounded_call(self._resolve(), kwargs, self._deadline, guard=self._guard,
                                stop=_current_stop())
        except TransportDeadlineExceeded as exc:
            self.fired, self.shutdown = True, exc.shutdown
            raise


def _text_sender():
    from ..ui import whatsapp_ui
    return whatsapp_ui._send_whatsapp_cloud_text


def _flow_sender():
    from ..ui import whatsapp_ui
    return whatsapp_ui._send_whatsapp_cloud_flow


# ══ admission (Data Exchange request; never a provider call) ══════════════════════════════

@dataclass(frozen=True)
class Admission:
    admitted: bool              # THIS call created the job
    job_id: Optional[int]
    reason: str                 # ADMITTED | ALREADY_ADMITTED | NO_OFFER_IDENTITY | ADMISSION_FAILED
    attention_applied: bool = False
    #: the pending confirm-time details (if any) were written in this transaction
    details_persisted: bool = False


def _insert_job(s, values: dict) -> Optional[int]:
    from ..models import SlotRecoveryJob as J
    dialect = s.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = (insert(J.__table__).values(**values)
            .on_conflict_do_nothing(index_elements=["recovery_identity"])
            .returning(J.__table__.c.id))
    return s.execute(stmt).scalar()


def _apply_attention(s, job, *, now: datetime) -> None:
    """Inside a K1 transaction: stage SCHEDULING + both human flags; record which flags THIS
    job turned false → true, and the thread's inbound marker at that moment."""
    from ..models import Lead, WhatsAppThreadState
    state = s.execute(select(WhatsAppThreadState).where(
        WhatsAppThreadState.thread_id == int(job.thread_id))).scalar_one_or_none()
    owns_state = owns_lead = False
    marker = None
    if state is not None:
        owns_state = not bool(state.needs_human)
        state.last_stage = "SCHEDULING"
        state.needs_human = True
        marker = _h16(state.last_processed_inbound_wa_message_id)
    lead = s.get(Lead, int(job.lead_id)) if job.lead_id is not None else None
    if lead is not None:
        owns_lead = not bool(lead.necesita_humano)
        lead.necesita_humano = True
    job.attention_applied = True
    job.owns_state_flag = owns_state
    job.owns_lead_flag = owns_lead
    job.inbound_marker = marker
    job.updated_at = now


def _write_details(s, details: dict, *, now: datetime) -> bool:
    """The pending details, in a savepoint of the admission transaction (K1 held)."""
    from .booking_flow_details import write_detail_rows
    try:
        with s.begin_nested():
            write_detail_rows(s, thread_id=int(details["thread_id"]),
                              offer_id=details.get("offer_id"),
                              candidate_id=details.get("candidate_id"),
                              values=details.get("values") or {}, stamp=now,
                              producer_version="confirm_booking/1")
        return True
    except Exception as exc:
        logger.error("SLOT_RECOVERY details not kept at admission thread_id=%s: %s",
                     details.get("thread_id"), type(exc).__name__)
        return False


def _k1(s, thread_id: int) -> Optional[str]:
    """K1 with bounded retries. None when held; the typed contention otherwise."""
    from .offer_lifecycle import OfferLifecycle
    why = None
    for _ in range(LOCK_TRIES):
        why = OfferLifecycle(s).begin_offer_transaction(int(thread_id),
                                                        lock_timeout_ms=LOCK_TIMEOUT_MS)
        if why is None:
            return None
    return str(why)


def admit_recovery(db, ctx, *, losing_offer_id: Optional[int], attempt_ref: Optional[str] = None,
                   now: Optional[datetime] = None, details: Optional[dict] = None) -> Admission:
    """Admit the recovery owed for one typed SLOT_CONFLICT. Never sends; never raises.

    One short transaction on its own session (never the request's): K1 → INSERT the job
    (ON CONFLICT DO NOTHING) → the human flags → commit. K1 contended after bounded retries:
    the job is still inserted (attention_applied=false) so it is listed at once, and the
    worker raises the flags before it sends anything.

    `details` (R1, C-3): the loser's confirm-time details write that could not take K1
    before the authority. It is retried HERE, under the K1 this transaction holds, in a
    savepoint — a failure there never costs the admission. `details_persisted` reports it;
    the caller records a typed condition when it is false."""
    from ..models import SlotRecoveryJob as J
    from .booking_ce_adapter import isolated_session
    now = now or _utcnow()
    if losing_offer_id is None or ctx is None:
        return Admission(False, None, "NO_OFFER_IDENTITY")
    thread_id = int(ctx.thread.id)
    identity = recovery_identity(int(losing_offer_id))
    values = dict(
        recovery_identity=identity, thread_id=thread_id,
        lead_id=getattr(ctx.lead, "id", None), contact_id=getattr(ctx.contact, "id", None),
        candidate_id=getattr(ctx.candidate, "id", None), losing_offer_id=int(losing_offer_id),
        origin_delivery_hash=origin_delivery_hash(attempt_ref), state="PENDING", attempts=0,
        text_attempts=0, flow_attempts=0, attention_applied=False, owns_state_flag=False,
        owns_lead_flag=False, next_attempt_at=now, created_at=now, updated_at=now)
    try:
        with isolated_session(db) as s:
            contended = _k1(s, thread_id)
            kept = False
            if details and contended is None:
                kept = _write_details(s, details, now=now)
            job_id = _insert_job(s, values)
            if job_id is None:
                if kept:
                    s.commit()                        # the details stand on their own
                else:
                    s.rollback()
                existing = s.execute(select(J.id).where(
                    J.recovery_identity == identity)).scalar()
                logger.info("SLOT_RECOVERY already admitted thread_id=%s job_id=%s — nothing "
                            "new", thread_id, existing)
                return Admission(False, existing, "ALREADY_ADMITTED", details_persisted=kept)
            applied = False
            if contended is None:
                job = s.get(J, int(job_id))
                _apply_attention(s, job, now=now)
                applied = True
            s.commit()
        if applied:
            logger.info("SLOT_RECOVERY admitted thread_id=%s job_id=%s stage=SCHEDULING "
                        "needs_human=true", thread_id, job_id)
        else:
            logger.error("SLOT_RECOVERY admitted thread_id=%s job_id=%s WITHOUT the human "
                         "flags (K1 %s) — listed for an operator; the worker raises them "
                         "before any send", thread_id, job_id, contended)
        return Admission(True, int(job_id), "ADMITTED", attention_applied=applied,
                         details_persisted=kept)
    except Exception as exc:
        logger.error("SLOT_RECOVERY admission failed thread_id=%s offer_id=%s: %s", thread_id,
                     losing_offer_id, type(exc).__name__)
        return Admission(False, None, "ADMISSION_FAILED")


# ══ worker: claim → steps → terminal ══════════════════════════════════════════════════════

@dataclass(frozen=True)
class Claim:
    job_id: int
    token: str
    state: str                  # the state at claim time (SENDING_* = a claimant was lost)
    attempts: int


class _LostClaim(Exception):
    """This claimant no longer owns the job (its lease expired and another claimed it)."""


class _Defer(Exception):
    """Release the claim and come back later (contention, no transport slot)."""

    def __init__(self, error: str):
        super().__init__(error)
        self.error = error


def _begin(db):
    """A write transaction on the worker's session, from a clean state: the worker never
    carries a read transaction (or its snapshot) from one step into the next."""
    if db.in_transaction():
        db.rollback()
    return db.begin()


def claim_next(db, *, now: Optional[datetime] = None) -> Optional[Claim]:
    """Claim one due job: unclaimed and due, or holding an EXPIRED lease. One conditional
    UPDATE, committed before any work. Jobs whose claims are exhausted become FAILED."""
    from ..models import SLOT_RECOVERY_MAX_CLAIMS, SlotRecoveryJob as J
    now = now or _utcnow()
    due = or_(and_(J.claim_token.is_(None),
                   or_(J.next_attempt_at.is_(None), J.next_attempt_at <= now)),
              and_(J.claim_token.is_not(None), J.lease_expires_at < now))
    rows = db.execute(select(J.id, J.state, J.attempts).where(J.state.in_(NONTERMINAL), due)
                      .order_by(J.id).limit(5)).all()
    db.rollback()
    for jid, seen, attempts in rows:
        if int(attempts) >= SLOT_RECOVERY_MAX_CLAIMS:
            with _begin(db):
                db.execute(update(J).where(J.id == jid, J.state == seen, due).values(
                    state="FAILED", outcome="ATTEMPTS_EXHAUSTED", completed_at=now,
                    claim_token=None, lease_expires_at=None, next_attempt_at=None,
                    updated_at=now))
            logger.error("SLOT_RECOVERY job_id=%s FAILED ATTEMPTS_EXHAUSTED (listed)", jid)
            continue
        token = str(uuid.uuid4())
        with _begin(db):
            res = db.execute(update(J).where(J.id == jid, J.state == seen,
                                             J.attempts == attempts, due)
                             .values(claim_token=token, attempts=J.attempts + 1,
                                     lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                                     updated_at=now))
        if res.rowcount == 1:
            return Claim(job_id=int(jid), token=token, state=str(seen),
                         attempts=int(attempts) + 1)
    return None


def _owned(db, claim: Claim, **values) -> None:
    """Write `values` to the job only while this claim owns it (renewing the lease)."""
    from ..models import SlotRecoveryJob as J
    now = _utcnow()
    values.setdefault("lease_expires_at", now + timedelta(seconds=LEASE_SECONDS))
    with _begin(db):
        res = db.execute(update(J).where(J.id == claim.job_id, J.claim_token == claim.token,
                                         J.state.in_(NONTERMINAL))
                         .values(updated_at=now, **values))
    if res.rowcount != 1:
        raise _LostClaim(str(claim.job_id))


def _release(db, claim: Claim, error: str) -> None:
    now = _utcnow()
    try:
        _owned(db, claim, claim_token=None, lease_expires_at=None, last_error=error[:64],
               next_attempt_at=now + timedelta(seconds=RETRY_AFTER_SECONDS))
    except _LostClaim:
        pass
    logger.warning("SLOT_RECOVERY job_id=%s deferred: %s", claim.job_id, error[:64])


def _finish(db, claim: Claim, state: str, outcome: str, *, error: Optional[str] = None,
            **values) -> None:
    now = _utcnow()
    _owned(db, claim, state=state, outcome=outcome[:48], completed_at=now, claim_token=None,
           lease_expires_at=None, next_attempt_at=None,
           **({"last_error": error[:64]} if error else {}), **values)
    log = logger.info if state == "RECOVERED" else logger.error
    log("SLOT_RECOVERY job_id=%s -> %s %s", claim.job_id, state, outcome)


def _load(db, claim: Claim):
    from ..models import SlotRecoveryJob as J
    if db.in_transaction():
        db.rollback()
    job = db.get(J, claim.job_id)
    if job is not None:
        db.refresh(job)                       # every column loaded, then detached
        db.expunge(job)
    db.rollback()
    return job


def _ctx(db, job):
    """The minimal send context (thread id, recipient) read from canonical rows."""
    from ..models import WhatsAppContact, WhatsAppThread
    thread = db.get(WhatsAppThread, int(job.thread_id))
    contact = db.get(WhatsAppContact, int(thread.contact_id)) if thread is not None else None
    ids = (int(thread.id), contact.wa_id) if thread is not None and contact is not None else None
    db.rollback()
    if ids is None:
        return None
    return SimpleNamespace(thread=SimpleNamespace(id=ids[0]),
                           contact=SimpleNamespace(wa_id=ids[1]),
                           lead=SimpleNamespace(id=job.lead_id), state=None, candidate=None)


def _ensure_attention(db, claim: Claim, job) -> None:
    """Raise the human flags BEFORE anything is sent, when admission could not."""
    from ..models import SlotRecoveryJob as J
    from .booking_ce_adapter import isolated_session
    if job.attention_applied:
        return
    with isolated_session(db) as s:
        contended = _k1(s, int(job.thread_id))
        if contended is not None:
            raise _Defer("K1_CONTENDED")
        row = s.get(J, claim.job_id)
        if row is None or row.claim_token != claim.token:
            s.rollback()
            raise _LostClaim(str(claim.job_id))
        _apply_attention(s, row, now=_utcnow())
        s.commit()
    job.attention_applied = True
    logger.info("SLOT_RECOVERY job_id=%s human flags raised by the worker", claim.job_id)


def _outbound_row(db, thread_id: int, identity: Optional[str]):
    """The gate's outbound row for one delivery identity, as (status, wamid) — or None when
    that identity never crossed the gate."""
    from ..models import WhatsAppMessage as M
    if not identity:
        return None
    row = db.execute(select(M.status, M.wa_message_id).where(
        M.thread_id == int(thread_id), M.direction == "out",
        M.content_fingerprint == identity).order_by(M.id.desc())).first()
    db.rollback()
    return row


def _send_text(db, claim: Claim, job, ctx, *, attempt: int, resume: bool) -> str:
    """One text attempt. Returns SENT | DEFINITE | AMBIGUOUS | DEADLINE."""
    from .booking_acknowledgement import _send_ledger_acknowledgement
    identity = delivery_identity(job.recovery_identity, "text", attempt)
    if not resume:
        _owned(db, claim, state="SENDING_TEXT", text_attempts=attempt,
               text_delivery_identity=identity)
    transport = _Deadline(_text_sender, TRANSPORT_DEADLINE_SECONDS, stage="text_transport",
                          job_id=claim.job_id)
    outcome = _send_ledger_acknowledgement(
        db, ctx, name="", selected_date=None, selected_time=None,
        correlation_id=f"slotrec:{job.recovery_identity[:28]}",
        delivery_fingerprint=identity, text=RECOVERY_TEXT, transport=transport)
    db.rollback()
    if outcome:
        _owned(db, claim, state="TEXT_SENT", text_provider_message_id=outcome.wamid,
               last_error=None)
        job.state = "TEXT_SENT"
        return "SENT"
    if transport.refused is not None:
        # the last check refused: the gate row is marked failed, nothing left the process
        raise _Halt(transport.refused, "text_transport")
    if transport.fired:
        _owned(db, claim, last_error=("TRANSPORT_ABANDONED_AT_SHUTDOWN" if transport.shutdown
                                      else "TRANSPORT_DEADLINE_EXCEEDED"))
        return "DEADLINE"
    _owned(db, claim, last_error=(outcome.error or "UNKNOWN")[:64])
    return "AMBIGUOUS" if outcome.ambiguous else "DEFINITE"


def _text_phase(db, claim: Claim, job, ctx) -> bool:
    """PENDING / SENDING_TEXT → TEXT_SENT, or a terminal HANDOFF. True when the text is sent."""
    if job.state == "SENDING_TEXT":
        # A previous claimant was lost mid-step. The gate's own row decides, never a guess.
        row = _outbound_row(db, job.thread_id, job.text_delivery_identity)
        if row is not None and row.status in ("sent", "delivered", "read") and row.wa_message_id:
            _owned(db, claim, state="TEXT_SENT", text_provider_message_id=row.wa_message_id)
            job.state = "TEXT_SENT"
            return True
        if row is not None:
            _finish(db, claim, "HANDOFF", "TEXT_UNCERTAIN_LOST_SENDER")
            return False
        attempt, resume = max(int(job.text_attempts), 1), True     # nothing crossed: resume
        stage = "text_resume"
    else:
        attempt, resume = int(job.text_attempts) + 1, False
        stage = "text"
    while True:
        _guard(stage, claim)                 # before the text, a resumed text and a retry
        if not transport_capacity_available():
            raise _Defer("TRANSPORT_CAPACITY")
        kind = _send_text(db, claim, job, ctx, attempt=attempt, resume=resume)
        if kind == "SENT":
            return True
        if kind == "DEADLINE":
            _finish(db, claim, "HANDOFF", "TEXT_TRANSPORT_DEADLINE")
            return False
        if kind == "AMBIGUOUS":
            _finish(db, claim, "HANDOFF", "TEXT_UNCERTAIN")
            return False
        if attempt >= MAX_TEXT_ATTEMPTS:
            _finish(db, claim, "HANDOFF", "TEXT_NOT_DELIVERED")
            return False
        _owned(db, claim, state="PENDING")
        attempt, resume, stage = attempt + 1, False, "text_retry"


def _issue_fresh_offer(db, claim: Claim, job, identity: str) -> "tuple[Optional[str], Optional[int], Optional[str]]":
    """Under K1, ONE transaction: the losing offer must still own the thread; a fresh offer is
    issued (superseding it) AND the job moves to SENDING_FLOW. (token, offer id, None) or
    (None, None, why-not)."""
    from ..models import (SlotRecoveryJob as J, WhatsAppBookingOffer as O,
                          WhatsAppThreadState)
    from ..settings import get_settings
    from .booking_ce_adapter import isolated_session
    from .booking_flow_service import make_booking_token
    from .clarification_attempt import cycle_key as _cycle_key
    from .offer_lifecycle import OfferLifecycle, token_fingerprint
    thread_id = int(job.thread_id)
    with isolated_session(db) as s:
        contended = _k1(s, thread_id)
        if contended is not None:
            return None, None, "K1_CONTENDED"
        row = s.get(J, claim.job_id)
        if row is None or row.claim_token != claim.token or row.state != "TEXT_SENT":
            s.rollback()
            raise _LostClaim(str(claim.job_id))
        state = s.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == thread_id)).scalar_one_or_none()
        losing = s.get(O, int(job.losing_offer_id))
        if (state is None or losing is None or losing.status != "ISSUED"
                or token_fingerprint(state.flow_booking_token or "") != losing.token_fingerprint):
            s.rollback()
            return None, None, "SUPERSEDED_MEANWHILE"
        offers = OfferLifecycle(s)
        offers.clear_scheduling_fields(state)     # the lost slot must not narrow the picker
        token = make_booking_token(thread_id)
        offer = offers.issue(
            state, thread_id=thread_id, token=token, cycle_key=int(_cycle_key(state)),
            candidate_id=losing.candidate_id, tipo_vehiculo=losing.tipo_vehiculo,
            zone_group=losing.zone_group, zone_detail=losing.zone_detail,
            offered_date=None, offered_time=None,
            flow_id=(losing.flow_id or getattr(get_settings(), "booking_flow_id", "") or None))
        state.last_stage = "SCHEDULING"
        s.flush()
        now = _utcnow()
        row.state, row.new_offer_id, row.flow_attempts = "SENDING_FLOW", int(offer.id), 1
        row.flow_delivery_identity = identity
        row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        row.updated_at = now
        offer_id = int(offer.id)
        s.commit()
    job.state, job.new_offer_id = "SENDING_FLOW", offer_id
    return token, offer_id, None


def _withdraw(db, *, thread_id: int, offer_id: int) -> bool:
    from ..models import WhatsAppThreadState
    from .booking_ce_adapter import isolated_session
    from .offer_lifecycle import OfferLifecycle
    with isolated_session(db) as s:
        if _k1(s, thread_id) is not None:
            return False
        state = s.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == int(thread_id))).scalar_one_or_none()
        offers = OfferLifecycle(s)
        offers._terminalise(int(thread_id), status="WITHDRAWN",
                            reason="SLOT_RECOVERY_FLOW_NOT_SENT", offer_id=int(offer_id))
        if state is not None:
            offers._clear_mirror(state)
        s.commit()
    return True


def _send_flow(db, ctx, job, *, token: str, flow_id: str,
               claim: Optional["Claim"] = None) -> "tuple[str, str]":
    """('SENT'|'DEFINITE'|'AMBIGUOUS'|'DEADLINE'|'REFUSED', detail). Gate, data_exchange
    mode. REFUSED (R2): the last check refused; the gate row is marked failed, nothing left
    the process, detail is the refusal reason."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    from ..ui.whatsapp_ui import FLOW_MODE_DATA_EXCHANGE, MetaSendError
    from .booking_acknowledgement import meta_rejection_is_definite
    from .outbound_guard import OutboundBlockedError
    from .outbound_path_registry import OutboundPathId, get_deployment_id
    from .outbound_safety_gate import GateOutcome, OutboundSafetyGate
    gate = OutboundSafetyGate(db)
    result = gate.attempt(
        wa_id=ctx.contact.wa_id, thread_id=int(ctx.thread.id), text=RECOVERY_FLOW_BODY,
        message_type="flow", now=_dt.now(ZoneInfo("America/Argentina/Buenos_Aires")),
        path_id=OutboundPathId.BOOKING_FLOW.value, deployment_id=get_deployment_id(),
        correlation_id=f"slotrec:{job.recovery_identity[:28]}",
        delivery_identity=job.flow_delivery_identity)
    if result.outcome != GateOutcome.ALLOWED:
        if result.outcome == GateOutcome.BLOCKED_DUPLICATE:
            return "AMBIGUOUS", "FLOW_IDENTITY_SEEN"
        return "DEFINITE", f"GATE_{result.outcome.value}"[:64]
    transport = _Deadline(_flow_sender, TRANSPORT_DEADLINE_SECONDS, stage="flow_transport",
                          job_id=claim.job_id if claim is not None else None)
    try:
        wamid, _ = transport(
            to_wa_id=ctx.contact.wa_id, flow_id=flow_id, flow_token=token,
            body_text=RECOVERY_FLOW_BODY, cta_label=RECOVERY_FLOW_CTA,
            initial_screen="APPOINTMENT", mode=FLOW_MODE_DATA_EXCHANGE)
    except TransportDeadlineExceeded as exc:
        gate.mark_failed(result.message_id)
        return "DEADLINE", ("TRANSPORT_ABANDONED_AT_SHUTDOWN" if exc.shutdown
                            else "TRANSPORT_DEADLINE_EXCEEDED")
    except OutboundBlockedError:
        gate.mark_failed(result.message_id)
        if transport.refused is not None:
            return "REFUSED", transport.refused
        return "DEFINITE", "PRE_DISPATCH_OUTBOUND_BLOCKED"
    except MetaSendError as exc:
        gate.mark_failed(result.message_id, meta_http_status=exc.http_status,
                         meta_error_payload=exc.to_payload())
        if meta_rejection_is_definite(exc):
            return "DEFINITE", f"META_REJECTED_{exc.http_status}"
        return "AMBIGUOUS", f"AMBIGUOUS_META_{exc.http_status or 'NETWORK'}"[:64]
    except RuntimeError as exc:
        # the transport's own configuration checks, before the network
        gate.mark_failed(result.message_id)
        return "DEFINITE", f"PRE_DISPATCH_{type(exc).__name__}"[:64]
    except Exception as exc:
        gate.mark_failed(result.message_id)
        return "AMBIGUOUS", f"AMBIGUOUS_{type(exc).__name__}"[:64]
    wamid = (wamid or "").strip()
    if not wamid:
        return "AMBIGUOUS", "AMBIGUOUS_SUCCESS_WITHOUT_WAMID"
    try:
        gate.mark_sent(result.message_id, wamid)
    except Exception:                         # bookkeeping ≠ delivery
        logger.exception("SLOT_RECOVERY Flow sent but gate bookkeeping failed")
    return "SENT", wamid


def _flow_settled(db, claim: Claim, job, kind: str, detail: str) -> None:
    if kind == "REFUSED":
        raise _Halt(detail, "flow_transport")
    if kind == "SENT":
        _recovered(db, claim, job, wamid=detail)
        return
    if kind == "DEFINITE":
        withdrawn = _withdraw(db, thread_id=int(job.thread_id), offer_id=int(job.new_offer_id))
        _finish(db, claim, "HANDOFF" if withdrawn else "FAILED",
                "FLOW_NOT_SENT" if withdrawn else "HANDOFF_LOCK_CONTENDED", error=detail)
        return
    # AMBIGUOUS / DEADLINE: the Flow may be on the handset, so its offer stays live.
    _finish(db, claim, "HANDOFF",
            "FLOW_TRANSPORT_DEADLINE" if kind == "DEADLINE" else "FLOW_UNCERTAIN", error=detail)


def _flow_phase(db, claim: Claim, job, ctx) -> None:
    from ..models import WhatsAppBookingOffer as O, WhatsAppThreadState
    from ..settings import get_settings
    from .booking_flow_service import BookingFlowService
    from .offer_lifecycle import token_fingerprint
    flow_id = (getattr(get_settings(), "booking_flow_id", "") or "").strip()
    if job.state == "SENDING_FLOW":
        # A claimant was lost between the fresh offer's commit and the Flow's settlement.
        row = _outbound_row(db, job.thread_id, job.flow_delivery_identity)
        if row is not None and row.status in ("sent", "delivered", "read") and row.wa_message_id:
            _recovered(db, claim, job, wamid=row.wa_message_id)
            return
        if row is not None:
            _finish(db, claim, "HANDOFF", "FLOW_UNCERTAIN_LOST_SENDER")
            return
        # nothing crossed for the Flow: resuming WOULD send — the policy decides first (R2)
        _guard("flow_resume", claim)
        state = db.execute(select(WhatsAppThreadState).where(
            WhatsAppThreadState.thread_id == int(job.thread_id))).scalar_one_or_none()
        offer = db.get(O, int(job.new_offer_id)) if job.new_offer_id is not None else None
        token = state.flow_booking_token if state is not None else None
        db.rollback()
        if (offer is None or offer.status != "ISSUED" or not token
                or token_fingerprint(token) != offer.token_fingerprint):
            _finish(db, claim, "HANDOFF", "FLOW_SUPERSEDED")
            return
    else:
        _guard("after_text", claim)          # after the text, before anything of the Flow
        losing = db.get(O, int(job.losing_offer_id))
        zone_group = losing.zone_group if losing is not None else None
        db.rollback()
        try:
            free_days = BookingFlowService(db)._available_dates(zone_group)
        except Exception as exc:
            logger.error("SLOT_RECOVERY availability failed: %s", type(exc).__name__)
            free_days = []
        db.rollback()
        if not free_days:
            _finish(db, claim, "HANDOFF", "FLOW_NO_AVAILABILITY")
            return
        if not flow_id:
            _finish(db, claim, "HANDOFF", "FLOW_NOT_CONFIGURED")
            return
        if not transport_capacity_available():
            raise _Defer("TRANSPORT_CAPACITY")
        _guard("offer", claim)               # never issue an offer the policy would not send
        token, _offer_id, why = _issue_fresh_offer(
            db, claim, job, delivery_identity(job.recovery_identity, "flow"))
        if why == "K1_CONTENDED":
            raise _Defer("K1_CONTENDED")
        if token is None:
            # something newer owns the conversation; the explanation stands, a person checks
            _finish(db, claim, "HANDOFF", "FLOW_SUPERSEDED")
            return
        job.flow_delivery_identity = delivery_identity(job.recovery_identity, "flow")
    try:
        db.expire_all()
        BookingFlowService(db).resolve_context(token)
        db.rollback()
    except Exception as exc:
        db.rollback()
        logger.error("SLOT_RECOVERY new offer does not resolve: %s", type(exc).__name__)
        withdrawn = _withdraw(db, thread_id=int(job.thread_id), offer_id=int(job.new_offer_id))
        _finish(db, claim, "HANDOFF" if withdrawn else "FAILED",
                "FLOW_CONTEXT_FAILED" if withdrawn else "HANDOFF_LOCK_CONTENDED")
        return
    if not transport_capacity_available():
        raise _Defer("TRANSPORT_CAPACITY")
    _guard("flow", claim)                    # before the Flow (fresh or resumed)
    try:
        kind, detail = _send_flow(db, ctx, job, token=token, flow_id=flow_id, claim=claim)
    except Exception as exc:                  # e.g. the gate itself failed: nothing sent
        db.rollback()
        kind, detail = "DEFINITE", f"GATE_FAILED_{type(exc).__name__}"[:64]
    db.rollback()
    _flow_settled(db, claim, job, kind, detail)


def _other_attention(s, job, *, now: datetime) -> bool:
    """Does anything ELSE on this thread or lead still need a person?"""
    from ..models import BookingAttentionCondition as C, SlotRecoveryJob as J
    since = now - timedelta(days=ATTENTION_LOOKBACK_DAYS)
    owners = [J.thread_id == int(job.thread_id)]
    cowners = [C.thread_id == int(job.thread_id)]
    if job.lead_id is not None:
        owners.append(J.lead_id == int(job.lead_id))
        cowners.append(C.lead_id == int(job.lead_id))
    other_job = s.execute(select(J.id).where(
        J.id != int(job.id), or_(*owners),
        or_(J.state.in_(NONTERMINAL),
            and_(J.state.in_(("HANDOFF", "FAILED")), J.created_at >= since)))
        .limit(1)).first()
    condition = s.execute(select(C.id).where(or_(*cowners), C.created_at >= since)
                          .limit(1)).first()
    return other_job is not None or condition is not None


def _recovered(db, claim: Claim, job, *, wamid: str) -> None:
    """RECOVERED, and — in the same K1 transaction — clear ONLY the flags this job raised,
    only if nothing else needs a person and no turn ran since the flags were raised. Never a
    flag that pre-existed, or that another reason asserted later."""
    from ..models import Lead, SlotRecoveryJob as J, WhatsAppThread, WhatsAppThreadState
    from .booking_ce_adapter import isolated_session
    now = _utcnow()
    cleared = (False, False)
    kept = None
    with isolated_session(db) as s:
        contended = _k1(s, int(job.thread_id))
        row = s.get(J, claim.job_id)
        if row is None or row.claim_token != claim.token or row.state not in NONTERMINAL:
            s.rollback()
            raise _LostClaim(str(claim.job_id))
        if contended is None and (row.owns_state_flag or row.owns_lead_flag):
            state = s.execute(select(WhatsAppThreadState).where(
                WhatsAppThreadState.thread_id == int(row.thread_id))).scalar_one_or_none()
            untouched = (state is not None
                         and _h16(state.last_processed_inbound_wa_message_id) == row.inbound_marker)
            if untouched and not _other_attention(s, row, now=now):
                cs = cl = False
                if row.owns_state_flag and state.needs_human:
                    state.needs_human = False
                    cs = True
                lead = s.get(Lead, int(row.lead_id)) if row.lead_id is not None else None
                if row.owns_lead_flag and lead is not None and lead.necesita_humano:
                    # another conversation of the same lead that still needs a person keeps
                    # the lead's flag up
                    busy = s.execute(select(WhatsAppThreadState.id).join(
                        WhatsAppThread, WhatsAppThread.id == WhatsAppThreadState.thread_id)
                        .where(WhatsAppThread.lead_id == int(row.lead_id),
                               WhatsAppThreadState.thread_id != int(row.thread_id),
                               WhatsAppThreadState.needs_human.is_(True)).limit(1)).first()
                    if busy is None:
                        lead.necesita_humano = False
                        cl = True
                cleared = (cs, cl)
            else:
                kept = "FLAGS_KEPT_OTHER_ATTENTION"
        elif contended is not None and (row.owns_state_flag or row.owns_lead_flag):
            kept = "FLAGS_KEPT_CONTENDED"
        row.state, row.outcome, row.completed_at = "RECOVERED", "RECOVERED", now
        row.flow_provider_message_id = wamid
        row.claim_token, row.lease_expires_at, row.next_attempt_at = None, None, None
        row.last_error = kept
        row.updated_at = now
        s.commit()
    logger.info("SLOT_RECOVERY job_id=%s -> RECOVERED thread_id=%s cleared_state=%s "
                "cleared_lead=%s%s", claim.job_id, job.thread_id, cleared[0], cleared[1],
                f" ({kept})" if kept else "")


#: Which part of the recovery a refusal stopped, for its typed terminal outcome.
_HALT_PREFIX = {"process": "", "text": "TEXT_", "text_resume": "TEXT_", "text_retry": "TEXT_",
                "text_transport": "TEXT_", "after_text": "FLOW_", "offer": "FLOW_",
                "flow_resume": "FLOW_", "flow": "FLOW_", "flow_transport": "FLOW_"}


def _halt(db, claim: Claim, job, h: _Halt) -> None:
    """A refusal is TERMINAL at once (never deferred to a later, stale send): HANDOFF, flags
    kept (they were raised before any boundary), listed. A fresh offer whose Flow was not
    sent is withdrawn; if K1 does not allow that, FAILED with the lead flagged."""
    outcome = f"{_HALT_PREFIX.get(h.stage, '')}{h.reason}"
    error = f"AT_{h.stage.upper()}"
    if job is not None and job.state == "SENDING_FLOW" and job.new_offer_id is not None:
        if not _withdraw(db, thread_id=int(job.thread_id), offer_id=int(job.new_offer_id)):
            _fail_safe(db, claim, "HANDOFF_LOCK_CONTENDED", f"{outcome}_{error}")
            return
    _finish(db, claim, "HANDOFF", outcome, error=error)


def process(db, claim: Claim) -> None:
    """Run one claimed job as far as it goes. Never raises."""
    from ..models import SLOT_RECOVERY_MAX_CLAIMS
    job = None
    try:
        job = _load(db, claim)
        if job is None or job.state in TERMINAL:
            return
        _ensure_attention(db, claim, job)
        _guard("process", claim)             # before ANY step of a claimed job
        ctx = _ctx(db, job)
        if ctx is None:
            _finish(db, claim, "HANDOFF", "THREAD_UNAVAILABLE")
            return
        if job.state in ("PENDING", "SENDING_TEXT"):
            if not _text_phase(db, claim, job, ctx):
                return
        if job.state in ("TEXT_SENT", "SENDING_FLOW"):
            _flow_phase(db, claim, job, ctx)
    except _Halt as h:
        try:
            db.rollback()
            _halt(db, claim, job, h)
        except _LostClaim:
            pass
        except Exception as exc:              # the lease expires; the next pass re-checks
            try:
                db.rollback()
            except Exception:                  # pragma: no cover
                pass
            logger.error("SLOT_RECOVERY job_id=%s halt not recorded: %s", claim.job_id,
                         type(exc).__name__)
    except _LostClaim:
        logger.warning("SLOT_RECOVERY job_id=%s claim lost — another claimant owns it",
                       claim.job_id)
    except _Defer as d:
        if claim.attempts >= SLOT_RECOVERY_MAX_CLAIMS:
            _fail_safe(db, claim, "HANDOFF_LOCK_CONTENDED" if d.error == "K1_CONTENDED"
                       else "ATTEMPTS_EXHAUSTED", d.error)
        else:
            _release(db, claim, d.error)
    except Exception as exc:
        try:
            db.rollback()
        except Exception:                      # pragma: no cover
            pass
        error = f"INTERNAL_{type(exc).__name__}"[:64]
        logger.error("SLOT_RECOVERY job_id=%s internal error: %s", claim.job_id, error)
        if claim.attempts >= SLOT_RECOVERY_MAX_CLAIMS:
            _fail_safe(db, claim, "INTERNAL_ERROR", error)
        else:
            _release(db, claim, error)


def _fail_safe(db, claim: Claim, outcome: str, error: str) -> None:
    """FAILED, and the lead flag raised best-effort (lead row only — no K1 needed)."""
    from ..models import Lead, SlotRecoveryJob as J
    try:
        _finish(db, claim, "FAILED", outcome, error=error)
    except _LostClaim:
        return
    try:
        job = db.get(J, claim.job_id)
        lead = db.get(Lead, int(job.lead_id)) if job is not None and job.lead_id else None
        if lead is not None and not lead.necesita_humano:
            lead.necesita_humano = True
        db.commit()
    except Exception:
        db.rollback()


def run_once(db, *, now: Optional[datetime] = None, max_jobs: int = 10) -> list:
    """One worker pass: claim and process up to `max_jobs` due jobs. [(job id, final state)]"""
    from ..models import SlotRecoveryJob as J
    done = []
    for _ in range(max_jobs):
        _stage("claim", None)
        if stopping():                        # no new claim after the stop signal (R2)
            break
        if not transport_capacity_available():
            logger.warning("SLOT_RECOVERY pass deferred: %s transports still alive",
                           inflight_transports())
            break
        claim = claim_next(db, now=now)
        if claim is None:
            break
        process(db, claim)
        state = db.execute(select(J.state).where(J.id == claim.job_id)).scalar()
        db.rollback()
        done.append((claim.job_id, state))
    return done


def _run_check() -> None:
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        run_once(db)
    except Exception:
        logger.exception("slot_recovery pass failed")
    finally:
        db.close()


# ══ worker lifecycle (R2) ═════════════════════════════════════════════════════════════════

#: Bounded wait of `stop_worker`. A pass blocked past it is a daemon thread: it cannot keep
#: the process alive, and — the signal being set — it can begin no further provider call.
JOIN_TIMEOUT_SECONDS = 3.0

_LIFECYCLE = threading.Lock()
_WORKER: Optional[threading.Thread] = None


def _worker_main(stop: threading.Event) -> None:
    """The worker's own daemon thread: a slow pass never occupies the event loop, FastAPI's
    threadpool or the operator-notification executor. Inert when no job is due."""
    _PASS.stop = stop
    while not stop.wait(CHECK_INTERVAL_SECONDS):
        try:
            _run_check()
        except Exception:                     # pragma: no cover — _run_check never raises
            logger.exception("slot_recovery worker unhandled error")


def start_worker() -> threading.Thread:
    """Start THE worker (application startup). Idempotent: a live, unstopped worker is
    returned as is. Each start owns a NEW stop signal, so a previous worker that is still
    finishing after its stop stays stopped."""
    global _WORKER, _STOP
    with _LIFECYCLE:
        if _WORKER is not None and _WORKER.is_alive() and not getattr(
                _WORKER, "_slotrec_stop").is_set():
            return _WORKER
        stop = threading.Event()
        _STOP = stop
        t = threading.Thread(target=_worker_main, args=(stop,), name="slot-recovery-worker",
                             daemon=True)
        t._slotrec_stop = stop
        t.start()
        _WORKER = t
        logger.info("slot_recovery worker started")
        return t


def stop_worker(timeout: float = JOIN_TIMEOUT_SECONDS) -> bool:
    """Application shutdown: set the stop signal FIRST (under `_SEND_LOCK`, so no provider
    call can begin afterwards), then join for at most `timeout`. Repeating it is harmless.
    True when the worker thread has ended."""
    global _WORKER
    with _LIFECYCLE:
        t = _WORKER
        with _SEND_LOCK:
            _STOP.set()
            if t is not None:
                getattr(t, "_slotrec_stop").set()
    if t is None:
        return True
    if t is threading.current_thread():      # asked from inside the pass: signal only
        return False
    t.join(max(0.0, float(timeout)))
    ended = not t.is_alive()
    with _LIFECYCLE:
        if ended and _WORKER is t:
            _WORKER = None
    if not ended:
        logger.error("slot_recovery worker still in a pass after %.1fs — abandoned (daemon; "
                     "no further provider call can begin)", timeout)
    return ended


# ══ attention: what an operator must see ══════════════════════════════════════════════════

_REASON_FOR_OUTCOME = {
    "HANDOFF_LOCK_CONTENDED": "SLOT_RECOVERY_LOCK_CONTENDED",
    "INTERNAL_ERROR": "SLOT_RECOVERY_INTERNAL",
}


def _refusal_reason(outcome: Optional[str], last_error: Optional[str]) -> Optional[str]:
    text = f"{outcome or ''} {last_error or ''}"
    if DISPATCH_OFF in text:
        return "SLOT_RECOVERY_DISPATCH_OFF"
    if WORKER_STOPPING in text or "ABANDONED_AT_SHUTDOWN" in text:
        return "SLOT_RECOVERY_WORKER_STOPPED"
    return None


def recovery_attention(db, *, now: Optional[datetime] = None) -> list[dict]:
    """Every slot-recovery job a person must see, from durable fields only. Read only.

    Listed: every NONTERMINAL job (PENDING / in progress — a customer is owed a message),
    with LEASE_EXPIRED / STALE / INTERNAL / LOCK_CONTENDED when they apply; every HANDOFF and
    FAILED job of the lookback window. RECOVERED is not listed. No PII: ids, states, typed
    codes and the thread link."""
    from ..models import SlotRecoveryJob as J
    now = now or _utcnow()
    since = now - timedelta(days=ATTENTION_LOOKBACK_DAYS)
    jobs = db.execute(select(J).where(or_(
        J.state.in_(NONTERMINAL),
        and_(J.state.in_(("HANDOFF", "FAILED")), J.created_at >= since)))
        .order_by(J.id)).scalars().all()
    out = []
    for j in jobs:
        created, lease = _aware(j.created_at), _aware(j.lease_expires_at)
        age = int((now - created).total_seconds()) if created else None
        reasons = []
        if j.state in NONTERMINAL:
            reasons.append("SLOT_RECOVERY_PENDING" if j.state == "PENDING"
                           else "SLOT_RECOVERY_IN_PROGRESS")
            if j.claim_token is not None and lease is not None and lease < now:
                reasons.append("SLOT_RECOVERY_LEASE_EXPIRED")
            if age is not None and age >= STALE_AFTER_SECONDS:
                reasons.append("SLOT_RECOVERY_STALE")
            if not j.attention_applied:
                reasons.append("SLOT_RECOVERY_FLAGS_NOT_RAISED")
        elif j.state == "HANDOFF":
            reasons.append("SLOT_RECOVERY_HANDOFF")
            refusal = _refusal_reason(j.outcome, j.last_error)
            if refusal:
                reasons.append(refusal)
        else:
            reasons.append("SLOT_RECOVERY_FAILED")
            extra = _REASON_FOR_OUTCOME.get(j.outcome or "")
            if extra:
                reasons.append(extra)
            refusal = _refusal_reason(j.outcome, j.last_error)
            if refusal:
                reasons.append(refusal)
        err = j.last_error or ""
        if err.startswith("INTERNAL") and "SLOT_RECOVERY_INTERNAL" not in reasons:
            reasons.append("SLOT_RECOVERY_INTERNAL")
        if err == "K1_CONTENDED" and "SLOT_RECOVERY_LOCK_CONTENDED" not in reasons:
            reasons.append("SLOT_RECOVERY_LOCK_CONTENDED")
        out.append({
            "job_id": int(j.id), "thread_id": int(j.thread_id), "lead_id": j.lead_id,
            "losing_offer_id": j.losing_offer_id, "new_offer_id": j.new_offer_id,
            "state": j.state, "outcome": j.outcome, "attempts": j.attempts,
            "text_attempts": j.text_attempts, "flow_attempts": j.flow_attempts,
            "last_error": j.last_error, "attention_applied": bool(j.attention_applied),
            "created_at": created.isoformat() if created else None,
            "updated_at": _aware(j.updated_at).isoformat() if j.updated_at else None,
            "lease_expires_at": lease.isoformat() if lease else None,
            "age_seconds": age, "reasons": reasons,
            "recovery": ("WAIT_FOR_WORKER" if reasons == ["SLOT_RECOVERY_PENDING"]
                         or reasons == ["SLOT_RECOVERY_IN_PROGRESS"]
                         else "CONTACT_CUSTOMER"),
            "thread_url": f"/whatsapp/thread/{int(j.thread_id)}",
        })
    return out


def condition_attention(db, *, now: Optional[datetime] = None) -> list[dict]:
    """Every booking attention condition of the lookback window. Read only; no PII."""
    from ..models import BookingAttentionCondition as C
    now = now or _utcnow()
    since = now - timedelta(days=ATTENTION_LOOKBACK_DAYS)
    rows = db.execute(select(C).where(C.created_at >= since).order_by(C.id)).scalars().all()
    return [{
        "condition_id": int(c.id), "kind": c.kind, "reason": c.reason,
        "thread_id": int(c.thread_id), "lead_id": c.lead_id, "offer_id": c.offer_id,
        "flags_applied": bool(c.flags_applied),
        "created_at": _aware(c.created_at).isoformat() if c.created_at else None,
        "reasons": [c.kind], "recovery": "COLLECT_DETAILS_FROM_CUSTOMER",
        "thread_url": f"/whatsapp/thread/{int(c.thread_id)}",
    } for c in rows]
