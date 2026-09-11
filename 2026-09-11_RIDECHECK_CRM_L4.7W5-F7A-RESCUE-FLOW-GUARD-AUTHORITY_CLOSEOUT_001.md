PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-F7A-RESCUE-FLOW-GUARD-AUTHORITY

STATUS: PASS
DATE: 2026-09-11
CODE COMMIT: 740e942 (tested from source; NOT DEPLOYED)
SCOPE: crm_test only. Production untouched. Outbound OFF. No Wild run. No image built.

---

## 1. The live trace, before any change

Entry point: `POST /api/conversation/handle` → `ConversationEngine.handle()` →
`_handle()`. State as recorded in the Wild:

```
last_stage = SCHEDULING   needs_human = false   flow_booking_token = SET
active_requested_date = 2026-09-12   last_offered_slots = ["13:00","13:30","14:00"]
```

Burst: `"no me sirve mañana me lo venden"`.

| stage | result |
|---|---|
| semantic / evidence | `authorize.scheduling_progression@v1` = ALLOW (all four prerequisites satisfied) |
| rejection evidence | `_earliest_option_rejected` = **True** |
| urgency evidence | `_urgency_signalled` = **True** |
| escalation evidence | `"no me sirve"` present in `_ESCALATION_KEYWORDS` = **True** |
| guard at `:3327` | `if state.flow_booking_token and _is_flow_failure(...)` — False (not a form failure) |
| **guard at `:3331`** | `last_stage == SCHEDULING` ✓ · `not needs_human` ✓ · **`not flow_booking_token` ✗** |
| consequence | the whole block — rescue branch 1a and escalation branch 1 — skipped |
| fallback | fell through to the AI path (`answer_source=CE_AI`), which produced a sympathetic sentence and no state change |

**Proven:** all three signals fired; the guard at 3331 evaluated False solely because of the
token; nothing downstream of it executed. **The only branch reachable with a token set was
`_is_flow_failure`** — "I can't open the form" — which does not cover rejection, urgency,
cancellation or a request for a person.

**Inferred, not proven:** nothing. The trace is complete from state to fallback.

**Adjacent guards of the same shape** (`:4209`, `:4235`): both gate *scheduling progression* —
parsing a day/time and storing an AI-proposed time. Blocking those while a Flow is live is
defensible, and neither is a rescue path. **Reported, not modified** (§7).

## 2. Root cause

> `flow_booking_token`, a technical lifecycle value written when the offer was sent, was
> load-bearing for whether the customer's rejection of that offer could be interpreted at all.

The offer made the rejection of the offer inaudible.

## 3. The repair — responsibilities made explicit

```
MAY_INTERPRET / MAY_CONSUME_RESCUE  → hoisted out of the guard; token-independent
MAY_DISPATCH_NEW_FLOW               → still `not flow_booking_token`; duplicate Flow blocked
MAY_CREATE_OR_CONFIRM_BOOKING       → BookingFlowService only; untouched
```

Rescue consumption now sits in its own branch gated on
`last_stage == SCHEDULING and not needs_human`. `needs_human` still stops everything
afterwards: the pre-existing `skipped_human` return owns the thread once a human has it.

**One explicit lifecycle transition**, and the reasoning for it rather than convenience: on
handoff the rejected offer's token is consumed. Leaving it live would let a tap on the old
Flow book the very slot the customer had just rejected, *after* a human took over. A late
submission now fails token validation in `resolve_context`. Confirmed bookings are untouched —
this invalidates only an **outstanding** offer. Logged as `FLOW_OFFER_WITHDRAWN`.

## 4. A second gap, found by RESCUE-04

An explicit request for a person was covered only by `"hablá con julián"` — a customer who
happens to know the owner's name. `_is_human_request` now matches by shape (a want/need verb
plus a person word, or "pasame con…"), and escalates independently of whether an option was
ever offered. Found because the required test case failed, not by inspection.

## 5. Evidence — the exact regression

Commit `740e942`, entry point `ConversationEngine.handle()` with the live state shape.

| | expected | actual |
|---|---|---|
| token before | SET | SET |
| rejection evidence | actionable | actionable |
| urgency evidence | actionable | actionable |
| rescue decision | executed once | executed once |
| `needs_human` | false → **true** | false → **true** |
| `lead.necesita_humano` / `estado` | true / ATENCION_HUMANA | true / ATENCION_HUMANA |
| lead context (vehicle, zone, quote) | retained | retained |
| booking writes | 0 | **0** |
| new Flow dispatches | 0 | **0** |
| operator alerts | 1 | **1** |
| customer rescue replies | 1 | **1** (canonical copy) |
| automation after handoff | 0 | **0** (`skipped_human`) |
| token after | withdrawn | **None** |

## 6. Tests

**17 tests, all through `handle()`** — the boundary n8n calls. Driving the edited helper
would have proven nothing: the helpers were already correct; the defect was that nothing
called them.

RESCUE-01 exact Wild sentence · RESCUE-02 rejection+urgency · RESCUE-03 rejection without
invention · RESCUE-04 human request · RESCUE-05 cancellation · plus token withdrawal.
SAFE-01 FAQ with token set · SAFE-02 "mañana me sirve" not a rejection · SAFE-03 ambiguity
invents nothing · SAFE-04 repeated urgency, no duplicate alert · SAFE-06 redelivered WAMID
rescues once · urgency-before-any-offer does not escalate. Authority boundaries asserted:
dispatch still guarded, booking writers still only `handle_confirm_booking`/`_create_booking`,
ScheduleService still the availability authority.

RESCUE-06/07 (normal Flow completion, reopen) are covered by the untouched
`test_m21_3_c_d_booking_flow.py` suite — 49 tests, all passing — rather than duplicated here.
SAFE-05/07/08 are **not** newly proven: stale-token recovery and post-booking rejection are
existing behaviour this milestone did not change, and a same-burst accept/reject conflict is
acceptance-producer territory, which is F7B. Stated rather than claimed.

## 7. Adjacent guard class — reported, not patched

`conversation_engine.py:4209` and `:4235` carry the same
`not state.flow_booking_token` shape. Both gate scheduling *progression*, not rescue, so the
same reasoning does not condemn them: not re-parsing a day while a Flow is live is coherent.
**Severity: LOW, informational.** No evidence of a suppressed customer signal there, and no
change made. Recommend a bounded follow-up only if a Wild produces one.

## 8. Regression

```
BASELINE (W5-F6) : 3880 passed · 57 failed · 9 errors
FINAL   (W5-F7A) : 3897 passed · 57 failed · 9 errors
new failures: 0      fixed: 0      pre-existing carried forward: 57 (identical set)
```

Five F2 handoff tests failed mid-milestone because they sliced CE source on a comment this
refactor removed; re-anchored with their assertions unchanged. That is a test-anchor
fragility worth noting: assertions keyed to comment text break on refactors that change
nothing they test.

Preserved: vehicle and location authority, PricingService, same-day and next-available
scheduling, exact-time Flow-first, Booking Flow completion and slot revalidation, quote and
booking price provenance, FAQ coexistence, WAMID idempotency and attribution, outbound safety.

## 9. Not done, deliberately

F7B — the duplicate quote-acceptance producer (`_authorize_acceptance` using `turn_modality`
while `claim_projection` uses the scoped `acceptance_modality`) — is **open and unmodified**.

Nothing here has been seen by a handset. Not deployed, outbound never armed, no Wild run,
and no forensic evidence reset or deleted.
