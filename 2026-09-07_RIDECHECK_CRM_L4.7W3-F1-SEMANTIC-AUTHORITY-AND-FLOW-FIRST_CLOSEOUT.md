PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W3-F1-SEMANTIC-AUTHORITY-AND-FLOW-FIRST

# The interpreter finally gets a vote on acceptance, and a day opens the Flow

crm_test only · OUTBOUND OFF · production untouched · no pricing, viáticos, business-hours
or BookingFlowService change · no phrase patch · C5 not started

---

## 1. Verdict

**PASS.** Wild W3 was right in outcome and wrong in reasoning three separate times. All
three are closed, and each was closed at the level where the reasoning was wrong.

## 2. Acceptance — the interpreter had it all along

W3: `"Bueno dale avancemos"` arrived in a burst that also asked an FAQ. `_is_acceptance`
requires the turn to be acceptance **throughout**, so it returned `False` and C3B logged:

```
AUTHORIZE result=HOLD stance=None failed=['stance_is_accept']
          reason=no acceptance evidence in this turn
```

The lead still reached `ACEPTADO` — through `authorize.scheduling_progression`. Right state,
wrong reason, and a ledger that recorded no stance for a turn where the customer said yes.

`_semantic_acceptance_claims()` now contributes the stance by **reusing
`claims_from_turn_evidence`** rather than interpreting stance a second time, so the
ACCEPT / REJECT / HESITATE / FUTURE_INTENT distinctions are the ones the corpus already
measures. It contributes evidence and nothing else — every prerequisite still decides.

Verified on the deployed image with the real model:

```
"Bueno dale avancemos" + "Que horarios tienen?"   ->  stance = ACCEPT
```

and in tests: a **stale** quote (zone moved to Quilmes) and an **undelivered** quote each
deny that same semantic yes, exactly as they deny a deterministic one.

**A stale docstring corrected, not worked around.** `authorize_quote_acceptance` claimed
*"acceptance evidence is not SEMANTIC_INFERRED alone"*. The executable rule only ever
blocked `DERIVED` — the L4.7C.3A restatement. The prose had drifted from the code; the code
was right. Fixed the prose.

## 3. FAQ — a label is a proposal, not an answer

W3: `"¿Qué tenés mañana?"` in `stage=SCHEDULING` was labelled `business_hours`, and the full
weekday table went out **again**, one turn after it had already been sent.

Reproduced live on the deployed image — the over-classification is real, not incidental:

```
"Nose que tenes mañana ?"  ->  faq_intents = ['business_hours']
```

Three reconciliations, none of them a phrase blocklist:

1. **an explicit literal ask always wins** and is never suppressed at any stage;
2. a semantic `business_hours` proposal **loses to a concurrent scheduling request** — the
   customer is asking what is *free*, not when we *open*;
3. a topic **already answered in this cycle** is not repeated unless asked again.

De-duplication moved from one probe word inside the message being composed to **topic
identity across bounded recent outbound**, cycle-scoped. That is what the W3 case needed:
the hours were in the *previous* message, which the old probe could not see.

All four cases verified on the live image:

```
scheduling evidence only   -> []                  SCHEDULING stage only -> []
already answered this cycle-> []                  explicit ask          -> ['business_hours']
```

## 4. Flow-first scheduling (owner decision)

A day or bounded period with real availability now opens the Booking Flow as the slot
picker, instead of reciting five times and asking "¿a qué hora te viene bien?" — a picker
rendered as a sentence. Wired into **both** the day-only and the period handlers.

**Three things I added that the brief did not ask for, because the change needed them:**

* **A commercial gate.** `_dispatch_booking_flow_for_day` first asks
  `authorize_scheduling_progression`. Without it, a bare "¿qué tenés mañana?" before any
  quote would open a *booking* picker. A booking picker is a commercial step, not a calendar
  widget.
* **Zero slots → no Flow.** An empty picker is a dead end; the existing conversational
  alternatives still run. Nothing invents availability.
* **A text fallback.** If the Flow cannot be armed (no configured id, no zone, no candidate,
  dispatch failure) the prose path still answers. Losing the Flow must not lose the turn.

`ScheduleService` remains the sole availability authority — the dispatcher receives slots and
contains no `list_slots`, no `ScheduleCheckIn`, no `self._schedule`, asserted by test.
REQUESTED / AVAILABLE / BOOKED stay three things: opening the Flow shows availability,
`BookingFlowService` revalidates, and `_process_flow_response` is still the only writer of a
booked revision — also asserted.

## 5. Two certified tests realigned, deliberately

Both are consequences of the owner's decision, and both keep asserting the *guarantee* while
accepting that the *medium* changed:

* **RC29 full slot visibility** existed to prove no slot is hidden. It now asserts that on
  the offered slot set (`last_visible_slots`), and additionally asserts the list is **not**
  recited in prose.
* **M7 scheduling correction** asserted that text was sent. Its harness fakes only the text
  sender, so a Flow dispatch reaches the real gate and is stopped by the kill switch — which
  is correct with outbound off. It now asserts the outcome (`replied` or `blocked_dispatch`,
  and `handled`) rather than the medium.

## 6. Three defects of my own, found and fixed during the work

* Four booking-authority AST tests failed because my new **docstring** quoted
  `status="booked"` — `ast.unparse` keeps docstrings. Same false-positive class as before;
  reworded.
* The cross-turn de-duplication skipped cycle-bounded messages with `if cycle_start and
  message.id and ...`, so a legitimate **id 0** was never excluded and a string cycle marker
  would have compared against an int. Now compares only when both are integers.
* My own test fixture used a cycle id that did not match the engine's, producing a `DENY`
  I briefly mistook for a code defect.

## 7. Tests and regression

`tests/test_l4_7w3_f1_semantic_authority_flow_first.py` — **26/26** (SEM-AUTH-01…08,
FAQ-CTX-01…06, FLOWFIRST-01…09, W3-REPRO-01…04, plus projection-reuse and
inbound-is-not-an-answer).

Full regression: **3 682 passed / 57 failed / 9 errors**, failure set identical to the
OPS-CONTROL baseline, **0 new**.

Runtime `ridecheck-crm-backend:w3f1-semauth-d5f89b3`, restarts 0, parity MATCH,
`OUTBOUND_ENABLED=false`, all authority flags on, `/login` 200, external → CE 401,
n8n → CE 422.

## 8. One gate line I cannot claim from automated tests

**CONTROL PATH ATTRIBUTION** is asserted structurally — the Flow dispatch goes through
`_send_booking_flow`, which is the certified `BOOKING_FLOW`/`CE_FLOW` path — but it has not
been observed end-to-end on the dashboard, because that needs a live Wild and outbound is
off. It will be visible in the next controlled session, which is what the new path column
was built for.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.
