PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-F3-BOOKING-CLOSURE-FAQ-DEDUP

STATUS: PASS
DATE: 2026-09-10
CODE COMMIT: dbc9b19
IMAGE: ridecheck-crm-backend:w5f3-booking-closure-dbc9b19
SCOPE: crm_test only. Production untouched. OUTBOUND OFF. No Wild started.

---

## 1. Part 1 — why a successful booking said nothing

```
Flow confirm_booking
  → /integrations/whatsapp/flows/booking/data-exchange
  → BookingFlowService.handle_confirm_booking
       advisory lock → ScheduleService.check → ThreadRevision + Revision
       → lead/state mutation, INCLUDING state.needs_human = True
       → token consumed → commit → SUCCESS screen
  → Meta posts the flow_response webhook
  → CE.handle → ... → `if state.needs_human: return _out("skipped_human")`   ← stops here
  → _process_flow_response  (never reached)
```

**ROOT CAUSE: the booking flags the thread for human approval, and CE's human-takeover
guard runs before flow_response routing.** So the flow_response is suppressed as
`skipped_human` and `_process_flow_response` — which holds the receipt wording — is never
reached for an endpoint-backed Flow.

Confirmed against the Wild's own record: `ai_event 163, action=skipped_human`, immediately
after `BOOKING_CREATED`.

The wording was never missing. It existed at `conversation_engine.py:2206-2213` and was
stranded in a path that no longer runs, because the booking is now written by
`BookingFlowService`, not by CE.

**The guard is correct and stays.** Loosening it so flow responses bypass `needs_human`
would let automation resume after a genuine human rescue handoff — directly against Part 6.
The acknowledgement moves to where the booking actually happens instead.

## 2. Parts 2/3 — the canonical receipt

Existing RideCheck wording was reused rather than replaced, per Part 2. It now lives in
`build_booking_receipt_message()` in `booking_flow_service.py`, and CE imports it, so the
two paths cannot drift into competing copy.

```
¡Listo, Lara! Recibimos tu solicitud para el sábado 12 de septiembre a las 13:30 🎉

Un asesor va a revisar los datos y te confirma el turno a la brevedad.
```

Deliberately **"solicitud"** and **"te confirma el turno"**, never *"turno confirmado"*:
`appointment_approval_status` is `PENDING` and an operator still has to approve. Telling a
customer their appointment is confirmed when it is not would be exactly the class of false
business fact this programme keeps closing.

Nothing about the message is decided by the model: whether a booking succeeded, whether
approval is pending, whether a human will follow up — all come from canonical booking state,
and the message is emitted only after a successful `confirm_booking` write.

## 3. Part 4 — exactly once, structurally

Not a flag and not a lock. `state.flow_booking_token = None` is set **inside the same
transaction** that creates the booking, and `resolve_context()` raises `BookingTokenError`
on a token that no longer matches. So:

| event | outcome |
|---|---|
| Meta retries the webhook | `BookingTokenError` before any write or send |
| duplicate `confirm_booking` | same — no second booking, no second message |
| customer reopens the Flow | token already consumed; nothing sent |
| revalidation failure | `BookingSlotConflictError` raised before the write |
| booking transaction failure | exception propagates before the send |

The gate's dedup window is a second line of defence, not the mechanism.

## 4. Part 5 — attribution

`OutboundPathId.BOOKING_FLOW` — the same registered path that dispatched the Flow, so the
acknowledgement is attributed to the booking that caused it. Routed through
`OutboundSafetyGate` with `deployment_id`. It can never appear as `MANUAL_CRM`, `UNKNOWN`
or `UNATTRIBUTED`. No new path was invented.

Delivery is **best-effort by design**: a Meta failure marks the gate record failed and logs,
but never rolls back or casts doubt on a booking that already exists.

## 5. Part 6 — the two states stay distinguishable

| state | condition | message |
|---|---|---|
| normal booking | booking exists, approval PENDING | "Recibimos tu solicitud … un asesor va a revisar los datos" |
| scheduling rescue | no booking, needs_human | "Lo paso con Julián para ver si puede acomodarlo…" |

Asserted in both directions: the receipt contains no rescue wording, and the escalation
contains no receipt wording.

## 6. Part 7 — service-scope dedup, and my own miss

The previous predicate enumerated the verbs it expected — `vamos|nos acercamos|se realiza|la
hacemos` — and the live Wild wrote **"Revisamos el auto en el lugar donde está"**, matching
none. The canonical paragraph was then appended on top of prose that had already said it.

That is a miss in my own W5-F1 fix: I fitted the predicates to my test fixture rather than to
the space of natural phrasing — the same brittleness that produced the original presence
defect. Adding "Revisamos" to a list would have repeated the mistake.

Scope is now matched by **shape**: a first-person-plural service verb (`\w+(amos|imos)`) near
any expression of the vehicle's location, in either word order, plus the domicile forms. One
further correction found by test: my first version only allowed `está`, so *"donde **esté** el
auto"* (subjunctive) still failed — broadened to `est[aáeé]`.

Contradiction handling is unchanged and still wins: a reply that says we will not be present
still has that sentence stripped and the canonical presence answer emitted.

## 7. Part 8 — the exact live Wild, on the deployed image

```
canonical scope paragraph appended : False      ← was True in the Wild
"en el lugar" occurrences          : 1          ← was 2
presence answer intact             : True
claims a confirmed appointment     : False
says request received              : True
says advisor will confirm          : True
```

Price handling is untouched, so the fixture's 150000 + 90000 = 240000 continues to come from
`PricingService` — no amount is asserted or hardcoded by this milestone.

## 8. BF30 — a certified test changed, deliberately

`TestBF30_OutboundOff` asserted "handle_confirm_booking creates no WhatsAppMessage". That was
an accurate proxy for "nothing was sent" only while booking was purely DB-side. A successful
booking now emits one acknowledgement through the gate, and the gate records every blocked
attempt as a `status='blocked'` row — under the M2 forensic model that row is **required**, it
is how an operator later proves the kill switch held.

The assertion moved to the invariant that actually matters, and two were added:

- nothing is **sent** while outbound is off, and no blocked row carries a Meta message id
- the blocked attempt is recorded with `path_id=BOOKING_FLOW` and a `KILL_SWITCH` reason
- the booking survives a blocked acknowledgement

## 9. Tests and regression

**17 new tests**: BOOK-END-01…10, SCOPE-DEDUP-01…05, plus single-canonical-wording and
root-cause-preserved checks. Three additional assertions in BF30.

**Regression: 3809 passed / 57 failed / 9 errors** — identical to the W5-F2 baseline,
**0 new**, +19 passing.

One test-authoring note worth recording: my first attribution assertion failed because my
own docstring contains the words "can never appear as MANUAL_CRM". A docstring naming a
forbidden value is prose about the guarantee, not a violation of it — this project has hit
that false positive repeatedly, so the suite now strips docstrings before asserting over
source.

## 10. What remains unproven

The receipt has not been seen by a handset. It is proven deterministically, and rendered from
the deployed image for the exact Wild booking, but outbound stayed off and no Wild was run.
The human-rescue handoff is likewise still test-only — the last Wild ended in a booking, so
its preconditions, message, CRM state and operator email have never fired live.
