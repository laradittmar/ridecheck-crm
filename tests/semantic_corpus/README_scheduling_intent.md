# `scheduling_intent.jsonl` — appointment-availability enquiry

## What this corpus is

A **capability corpus** for one canonical meaning: *the customer is asking what inspection
appointment availability exists*. It exists because G3-2 proved the evidence schema could
not represent that meaning at all — `SchedulingRequestEvidence` models a proposed
appointment and defines a row with no day and no time as empty, so an availability question
had nowhere to go and was lost (DEF-01).

It follows the precedent of `offered_options_rejected.jsonl`: a small, scoped corpus with
its own README, rather than an addition to `real_world_turns.jsonl`. The reason is
structural — `corpus_mapping.py` validates that every case in the general corpus is
representable in `TurnEvidence`, and at the moment this corpus was designed (G3-2) the
headline case deliberately was **not**. A capability corpus can hold a case that fails
before a fix and passes after it. That is the whole point of it.

## What it is NOT

* **Not a phrase catalogue.** No production module reads this file, and none may. The
  `text` fields are evidence of meaning, never matching rules. `SI-L01`'s spelling in
  particular must never appear in production code — a test asserts that it does not.
* **Not a held-out evaluation set.** The authored cases were written alongside the
  `understand/1.19` prompt in the same milestone, so any score a model obtains on them is
  contaminated in the same way `offered_options_rejected.jsonl` describes. They are
  regression fixtures for the *representation*, not a measurement of the interpreter.
* **Not a scoring instrument.** Scoring belongs to `evaluation.py` against a genuinely
  held-out set, which does not exist for this capability yet.

## Provenance: real versus authored

`origin` separates them, and the distinction is load-bearing:

* `origin: "live"` — **SI-L01 only.** Real text from the designated RideCheck test handset,
  captured during the controlled verification of 2026-09-24 under
  `L4.7W5-GATE3-G3-1-LIVE-TRACE-VERIFICATION`. Its `provenance` object carries the message,
  thread, lead, AiEvent, burst, trace and deployment identifiers, the stored text's md5 and
  the normalised burst hash, so the case can be tied back to the database rows that still
  hold it. The text is stored exactly as the handset delivered it, autocorrect and typo
  included; it was never normalised.
* `origin: "authored"` — written for this corpus to mark a semantic boundary. Each one is
  there to be *contrasted* with SI-L01, not to enumerate ways of saying the same thing.

## The boundaries each case marks

| id | group | what it proves |
|----|-------|----------------|
| SI-L01 | availability_enquiry_noisy | the live failure: an availability enquiry survives noise |
| SI-A01 | availability_enquiry_clean | the same meaning in clean language |
| SI-A02 | availability_enquiry_noisy | abbreviation and missing accents do not change the meaning |
| SI-A03 | business_hours | opening hours stay a FAQ topic — the distinction DEF-04 turned on |
| SI-A04 | concrete_proposal | a named day and time stay `SchedulingRequestEvidence` |
| SI-A05 | offered_slot_rejection | rejection stays in its own family |
| SI-A06 | duration | "how long does it take" is a FAQ topic, not availability |
| SI-A07 | time_without_request | a time mentioned in passing requests nothing |
| SI-A08 | ambiguous | a bare "¿Horarios?" must not be promoted to a confirmed intent |

## Rules

* Cases are never deleted because an interpreter misses them.
* Additions require a note saying which boundary they mark. "Another way of phrasing SI-A01"
  is not a boundary.
* `expected_intent: "AMBIGUOUS"` means the correct outcome is an unresolved status plus an
  ambiguity note — not a confirmed reading of either kind.
* `expects_day` / `expects_time` are false for every availability case, and a test asserts
  the representation cannot carry them even if a model tried.

## What this corpus does not yet cover

G3-2 catalogued five enquiry meanings the schema could not express. G3-3 implements one.
`ALTERNATIVE_ENQUIRY` ("anything else?"), `SOONNESS_ENQUIRY` ("anything sooner?"),
`OPEN_ENQUIRY` ("when could this be done at all?") and `RESCHEDULE_REQUEST` remain future
work, and this corpus deliberately contains no case for them: a fixture for a meaning the
schema cannot represent would be an assertion about work nobody has scoped.
