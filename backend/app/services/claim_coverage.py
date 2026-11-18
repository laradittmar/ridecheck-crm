"""WILD1-C — required-claim coverage: an explicit supported question is never silently dropped.

The first live WILD (window H-1) asked, across five bursts, what the inspection covers, what
the report contains, whether cash and debit are accepted, how much the viáticos are and
whether there was availability tomorrow. The replies answered payment once, restated a total
twice, and discarded the rest. No component was lying; the turn simply had no record of
what it OWED the customer, so whichever handler ran first decided what was said.

This module is that record. It is deliberately small and owns no words:

* **What is owed** is a `RequiredClaim`, derived from TYPED evidence only — the semantic
  interpreter's per-burst FAQ topics and QUOTE_REQUEST, the engine's existing deterministic
  floors, and the persisted per-burst evidence of earlier turns. Nothing here reads a
  customer sentence or keeps a phrase list; paraphrase, reordering and voice transcription
  are the interpreter's job, which it already does.
* **What answers it** is the canonical text the deterministic layer owns (the FAQ constants,
  the PricingService block). A claim is covered only when that canonical text is present in
  the FINAL outbound message — by identity, never by a model's paraphrase. A natural AI
  bridge can therefore never satisfy a required claim, and can never cause one to be dropped.
* **Across turns**, a claim asked on an earlier burst stays outstanding until an outbound
  message sent AFTER the asking inbound carries its canonical answer (ordered by row id, the
  repository's one monotonic ordering).

The AI owns natural phrasing; this layer owns which verified facts must be in the message.
"""
from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Optional


class RequiredClaim(enum.Enum):
    """A supported customer question, by the business fact that answers it."""
    SERVICE_SCOPE = "service_scope"        # what the pre-purchase inspection covers
    REPORT = "report"                      # what the report communicates
    PAYMENT = "payment"                    # methods, including whether debit is accepted
    PRICE_BREAKDOWN = "price_breakdown"    # revisión / viáticos / total
    AVAILABILITY = "availability"          # a day/time availability request


#: Interpreter / deterministic FAQ topics that are coverage-tracked. Other topics (hours,
#: presence) keep their existing producers and dedup rules unchanged.
TOPIC_CLAIMS: Mapping[str, RequiredClaim] = {
    "service_scope": RequiredClaim.SERVICE_SCOPE,
    "report": RequiredClaim.REPORT,
    "payment": RequiredClaim.PAYMENT,
}
CLAIM_TOPICS: Mapping[RequiredClaim, str] = {v: k for k, v in TOPIC_CLAIMS.items()}

#: The claims a TEXT message can be checked for. AVAILABILITY is satisfied by the scheduling
#: path (a ScheduleService statement or the Booking Flow), recorded by the caller.
TEXT_CHECKED = frozenset({RequiredClaim.SERVICE_SCOPE, RequiredClaim.REPORT,
                          RequiredClaim.PAYMENT, RequiredClaim.PRICE_BREAKDOWN})


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"\s+", " ", text).strip()


def claims_for_topics(topics: Iterable[str]) -> frozenset:
    return frozenset(TOPIC_CLAIMS[t] for t in (topics or ()) if t in TOPIC_CLAIMS)


def is_covered(claim: RequiredClaim, text: str,
               canonical: Mapping[RequiredClaim, str]) -> bool:
    """True when the canonical answer for `claim` is present in `text`, by identity.

    Whitespace-insensitive (the plan joins segments with spaces or blank lines), case- and
    Unicode-normalised; otherwise exact. A claim with no canonical answer is never covered."""
    answer = canonical.get(claim)
    if not answer:
        return False
    return _norm(answer) in _norm(text)


#: WILD1-C-R3 (WC-01). The turn received a burst that MAY carry a supported question, and the
#: semantic reading that would say WHICH question did not arrive in time (timed out) or at all
#: (failed / unavailable). It is not a business fact, so no text covers it: it is reported as
#: PENDING (never as answered, never as silently absent) and durably retained as a
#: `pending_question` row until a later turn resolves it.
PENDING_QUESTION = "pending_question"


@dataclass(frozen=True)
class CoverageReport:
    required: frozenset = frozenset()
    covered: frozenset = frozenset()
    #: covered on another channel this turn (the Booking Flow for AVAILABILITY)
    routed: frozenset = frozenset()
    #: WILD1-C-R3: inbound bursts whose question is still awaiting semantic resolution —
    #: tuples of the burst's WAMIDs. Never counted as covered, never as missing.
    pending: tuple = ()

    @property
    def missing(self) -> frozenset:
        return frozenset(self.required - self.covered - self.routed)

    @property
    def complete(self) -> bool:
        # A pending question is an open debt: a turn that owes one is never complete.
        return not self.missing and not self.pending

    def summary(self) -> dict:
        key = lambda s: sorted(c.value for c in s)  # noqa: E731
        return {"required": key(self.required), "covered": key(self.covered),
                "routed": key(self.routed), "missing": key(self.missing),
                "pending": [list(p) for p in self.pending]}


def coverage(required: Iterable[RequiredClaim], text: str,
             canonical: Mapping[RequiredClaim, str], *,
             routed: Iterable[RequiredClaim] = (),
             pending: Iterable = ()) -> CoverageReport:
    required = frozenset(required or ())
    covered = frozenset(c for c in required
                        if c in TEXT_CHECKED and is_covered(c, text, canonical))
    return CoverageReport(required=required, covered=covered,
                          routed=frozenset(routed or ()) & required,
                          pending=tuple(tuple(p) for p in (pending or ())))


# ── WILD1-C-R3 (WC-01): the typed control boundary for "may this burst ask something?" ──
#
# The question is NOT answered by a phrase list. The engine already routes a burst to its
# general-information gate (Layer D) by its existing classifier; within that route there are
# exactly two control classes, decided structurally:
#
#   GREETING_ONLY — every segment of the burst is the engine's existing greeting form (the
#                   one anchored greeting grammar Layer D already uses), on ANY route. Nothing
#                   can be owed, so nothing is waited for: the W1F-R2 no-wait contract (R2's
#                   coverage reader waited on "hola, buenas tardes"; that is corrected too).
#   MAY_ASK       — anything else the general-information route accepted. A question may be
#                   in it, so the turn may wait (bounded) for the reading that says which, and
#                   if the reading does not come, the burst is retained as PENDING — never
#                   treated as if it had been a greeting.
#
# A burst the general-information route did not accept is NOT_INFORMATIONAL: its questions,
# if any, are read on the commercial path exactly as before.


class QuestionPotential(enum.Enum):
    NOT_INFORMATIONAL = "not_informational"
    GREETING_ONLY = "greeting_only"
    MAY_ASK = "may_ask"


_SEGMENT_SPLIT = re.compile(r"[,.;:!¡?¿\n]+")


def question_potential(texts: Iterable[str], *, is_informational: Callable[[str], bool],
                       is_greeting: Callable[[str], bool]) -> QuestionPotential:
    """Classify a burst for the semantic-wait boundary. Total; never raises.

    `is_informational` is the engine's general-information route classifier; `is_greeting`
    its anchored greeting form. A segment is a punctuation-delimited span; empty segments
    are ignored. Both callables receive normalised text."""
    try:
        segments = []
        informational = False
        for raw in texts or ():
            norm = _norm(raw)
            if not norm:
                continue
            informational = informational or bool(is_informational(norm))
            segments.extend(s.strip() for s in _SEGMENT_SPLIT.split(norm) if s.strip())
        if segments and all(is_greeting(s) for s in segments):
            return QuestionPotential.GREETING_ONLY
        if not informational:
            return QuestionPotential.NOT_INFORMATIONAL
        return QuestionPotential.MAY_ASK
    except Exception:
        # Unknown is treated as "may ask": a classifier failure may cost a bounded wait,
        # never a silently dropped question.
        return QuestionPotential.MAY_ASK


class SemanticTurnState(enum.Enum):
    """WILD1-C-R4 (R3RV-01): a burst's semantic state at the TURN BOUNDARY, route-independent.

    R3 retained a burst only when Layer D's route classifier called it MAY_ASK, so a supported
    question that classifier did not tag was lost when its reading was late. Retention is now
    decided from this state, for every burst, whichever route answered it:

      GREETING_ONLY                       — every segment is the engine's anchored greeting
                                            form: nothing can be owed, nothing is waited for;
      SEMANTICALLY_RESOLVED               — the reading arrived inside the turn deadline:
                                            supported claims are answered normally;
      SEMANTICALLY_UNRESOLVED_NONTERMINAL — a reading was dispatched and did not resolve
                                            (late, failed, never started) on a turn that
                                            continues the conversation: retained as PENDING,
                                            acknowledged, resolved on the next eligible turn;
      TERMINAL_OR_PRE_GATED               — a terminal pre-gate (motorcycle, phone, service
                                            boundary, inspectability, website form) or a
                                            person owns the turn: nothing is owed by automation;
      NOT_DISPATCHED                      — no reading exists for this turn (semantics off):
                                            nothing could resolve it later; the deterministic
                                            floors answer, as before.

    No phrase list participates: the greeting form is the one Layer D already uses, and what
    an unresolved burst asked is decided by its re-reading, not by its words here."""
    GREETING_ONLY = "greeting_only"
    SEMANTICALLY_RESOLVED = "semantically_resolved"
    SEMANTICALLY_UNRESOLVED_NONTERMINAL = "semantically_unresolved_nonterminal"
    TERMINAL_OR_PRE_GATED = "terminal_or_pre_gated"
    NOT_DISPATCHED = "not_dispatched"


#: `whatsapp_thread_evidence.status` of a pending question's two append-only rows.
PENDING_STATUS = "PENDING"
RESOLVED_STATUS = "RESOLVED"

#: Outbound ledger statuses that did NOT reach the customer (WC-07): such a row never proves
#: that a claim was answered.
UNDELIVERED_STATUSES = frozenset({"failed", "blocked"})


def delivered(status) -> bool:
    return str(status or "").strip().lower() not in UNDELIVERED_STATUSES


def unresolved_pending(rows: Iterable) -> list:
    """The PENDING `pending_question` rows no RESOLVED row has closed, oldest first.

    Append-only: a resolution is its own row carrying the pending row's `claim_id` in
    `validated_as.resolves`; nothing is ever updated or deleted."""
    pending, closed = [], set()
    for row in rows or ():
        if str(getattr(row, "claim_type", "") or "") != PENDING_QUESTION:
            continue
        status = str(getattr(row, "status", "") or "")
        if status == PENDING_STATUS:
            pending.append(row)
        elif status == RESOLVED_STATUS:
            resolves = (getattr(row, "validated_as", None) or {}).get("resolves")
            if resolves:
                closed.add(str(resolves))
    return [r for r in pending if str(getattr(r, "claim_id", "")) not in closed]


@dataclass(frozen=True)
class Ask:
    """One earlier request: the claim, and the inbound row id it was read from."""
    claim: RequiredClaim
    inbound_id: int


@dataclass
class Ledger:
    """The cross-turn view: what earlier bursts asked, and what was answered after."""
    asks: list = field(default_factory=list)
    #: (outbound row id, text), any order
    outbound: list = field(default_factory=list)

    def outstanding(self, canonical: Mapping[RequiredClaim, str]) -> frozenset:
        """Claims asked earlier whose canonical answer no LATER outbound carries."""
        open_: set = set()
        for ask in self.asks:
            if ask.claim not in TEXT_CHECKED:
                continue
            # WC-07: an outbound entry is (id, text) or (id, text, status); a failed or
            # blocked row never reached the customer and answers nothing.
            answered = any(isinstance(entry[0], int) and entry[0] > ask.inbound_id
                           and (len(entry) < 3 or delivered(entry[2]))
                           and is_covered(ask.claim, entry[1] or "", canonical)
                           for entry in self.outbound)
            if not answered:
                open_.add(ask.claim)
        return frozenset(open_)


#: `whatsapp_thread_evidence.claim_type` of the engine's own "owed and not yet answered"
#: record, written at the send boundary for each claim the final message did not cover.
#: Needed because the interpreter's per-burst rows are persisted only on turns whose
#: admission waited for it (a burst with a catalogue locality).
REQUIRED_CLAIM_ROW = "required_claim"


def asks_from_evidence_rows(rows: Iterable, inbound_id_for: Callable[[str], Optional[int]],
                            *, exclude_inbound_ids: Iterable[int] = ()) -> list:
    """Earlier asks from persisted `whatsapp_thread_evidence` rows.

    A `faq_topic` row carries the interpreter's topic as its value; a `quote_request` row is
    the interpreter's typed money question. Refused-for-admission rows are still EVIDENCE of
    what the customer asked (admission governs canonical state, not whether a question was
    put) — which is why they are read here. Rows of the current burst are excluded: the
    current burst is read directly, this turn."""
    excluded = set(exclude_inbound_ids or ())
    asks: list = []
    for row in rows or ():
        claim_type = str(getattr(row, "claim_type", "") or "")
        if claim_type == REQUIRED_CLAIM_ROW:
            try:
                claim = RequiredClaim(str(getattr(row, "value", "") or ""))
            except ValueError:
                claim = None
        elif claim_type == "faq_topic":
            claim = TOPIC_CLAIMS.get(str(getattr(row, "value", "") or ""))
        elif claim_type == "quote_request":
            value = str(getattr(row, "value", "") or "").strip().lower()
            claim = RequiredClaim.PRICE_BREAKDOWN if value in ("true", "1") else None
        else:
            claim = None
        if claim is None:
            continue
        ids = [inbound_id_for(w) for w in (getattr(row, "source_message_ids", None) or ())]
        ids = [i for i in ids if isinstance(i, int)]
        if not ids or any(i in excluded for i in ids):
            continue
        asks.append(Ask(claim=claim, inbound_id=max(ids)))
    return asks
