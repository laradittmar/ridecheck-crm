"""Typed-evidence setup for the G3-7 cutover migration (L4.7W5-GATE3-G3-7B).

The certified messy-turn and Wild suites used to state the canonical answer through the
reply model — a `candidate` dict carrying a database action and a CRM status — which is
exactly the authority the cutover removes. Migrating them means stating what the CUSTOMER
said, as typed `TurnEvidence` in the shape `understand/1.19` is specified to produce, and
letting the reconciler decide which row it affects.

Evidence built here therefore never carries a candidate id, a CRM status, a focus pointer
or a zone group. The business assertions in the migrated suites do not move.

`armed_evidence` supplies the interpretation only while the demotion is armed: with the flag
off the legacy channel is still the authority and must stay exercised, so one test certifies
both sides of the cutover and the rollback stays meaningful.

NOT a test module and NOT a conftest — pytest neither collects nor auto-imports it.
"""
from __future__ import annotations

import contextlib

from app.schemas import turn_evidence as te
from app.services.conversation_engine import ConversationEngine

C = te.EvidenceStatus.CONFIRMED
INSPECTION = te.LocationRole.INSPECTION_LOCATION.value
ORIGIN = te.LocationRole.CUSTOMER_ORIGIN.value
R = te.CorrectionRelation


@contextlib.contextmanager
def armed_evidence(eng, evidence):
    """Supply `evidence` through the production accessor while the demotion is armed."""
    armed = evidence is not None and eng._reply_renderer_demotion_on()
    original = ConversationEngine._semantic_turn_evidence
    if armed:
        ConversationEngine._semantic_turn_evidence = lambda self: evidence
        eng.settings.semantic_same_turn_enabled = True
        eng.settings.shadow_understand_enabled = True
    try:
        yield armed
    finally:
        ConversationEngine._semantic_turn_evidence = original


def vehicle(*, make=None, model=None, year=None, value=None, tipo=None,
            superseded=False, index=0, status=C):
    return te.VehicleEvidence(
        value=(value if value is not None else
               " ".join(p for p in (make, model) if p) or None),
        make=make, model=model, year=year,
        year_status=(status if year is not None else None),
        category_suggestion=tipo, is_superseded=superseded,
        mention_index=index, status=status)


def corrected_year(year, *, was=None, index=0, status=C):
    """The identity-less row the interpreter synthesizes for a corrected year (rule C4)."""
    return te.VehicleEvidence(value=None, year=year, year_status=status, status=status,
                              mention_index=index,
                              reason="corrected year carried by a correction")


def place(name, *, role=INSPECTION, status=C):
    return te.LocationEvidence(value=name, locality=name, role=role, status=status)


def fix(relation=R.CORRECT_EXISTING, *, was=None, now=None, ref=None, status=C):
    return te.CorrectionEvidence(value=True, relation=relation, from_value=was,
                                 to_value=now, target_ref=ref, status=status)


def intent(kind="PREPURCHASE_INSPECTION", status=C):
    return te.ServiceIntentEvidence(value=kind, status=status)


def accepts(signal="ACCEPT", status=C):
    return te.AcceptanceEvidence(value=signal, signal=signal, status=status)


def turn(*, vehicles=(), places=(), fixes=(), intents=(), acceptance=None,
         faq_topics=(), scheduling=()):
    """A `TurnEvidence` describing one customer turn."""
    kwargs = {}
    if vehicles:
        kwargs["vehicle_mentions"] = tuple(vehicles)
    if places:
        kwargs["location_mentions"] = tuple(places)
    if fixes:
        kwargs["corrections"] = tuple(fixes)
    if intents:
        kwargs["service_intents"] = tuple(intents)
    if acceptance is not None:
        kwargs["acceptance"] = acceptance
    if faq_topics:
        kwargs["faq_topics"] = tuple(faq_topics)
    if scheduling:
        kwargs["scheduling_requests"] = tuple(scheduling)
    return te.TurnEvidence(**kwargs)
