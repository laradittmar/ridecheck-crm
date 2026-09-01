# Booking UX Contract — authoritative runtime sequence

Status: **OWNER DECISION, 2026-09-01 (L4.3-SCHEDULING-SEMANTICS)**
Supersedes the ambiguity recorded as FLOW-B in
`2026-09-01_RIDECHECK_CRM_L4-WILD-A-SCHEDULING-FORENSIC_AUDIT_TEMPORAL-FLOW.md`.

## 1. Decision

**BOOKING_FLOW is the authoritative scheduling / booking UX.**

RideCheck Booking Flow — Meta Flow ID `28104222025943520`, status PUBLISHED,
runtime setting `WHATSAPP_BOOKING_FLOW_ID` / `settings.booking_flow_id`.

The text conversation is **not** retired. It remains responsible for, and only for:

- interpreting customer scheduling intent (including multi-branch utterances);
- checking deterministic availability through `ScheduleService`;
- explaining why a requested slot is unavailable, in business terms;
- evaluating the PRIMARY preference before any FALLBACK preference;
- helping the customer reach **one concrete valid scheduling option**.

Text never collects booking data and never creates a booking.

## 2. Authoritative sequence

```
QUOTED
  │  (acceptance detected)
  ↓
SCHEDULING                       ← text conversation owns this stage
  │  customer states day/time, or several alternatives
  ↓
CE interprets ORDERED preferences        _parse_scheduling_requests()
  │  [primary, fallback, …] — each time bound to its own clause
  ↓
ScheduleService.check() on PRIMARY       _evaluate_scheduling_branch()
  ├─ available → go to "valid slot established"
  └─ unavailable
        ↓
     ScheduleService on FALLBACK (only now — never before)
        ↓
     ONE reply: primary named + real rejection reason + fallback options
        ↓
     customer picks an offered slot → re-enters check()
  ↓
VALID SLOT ESTABLISHED
  ↓
send RideCheck Booking Flow              _send_booking_flow()
  · flow_id      = settings.booking_flow_id (28104222025943520)
  · flow_token   = make_booking_token(thread_id)   ← minted once, stored on state
  · initial screen = APPOINTMENT
  · path_id      = OutboundPathId.BOOKING_FLOW
  · prerequisites validated by BookingFlowService.resolve_context() — never re-implemented in CE
  ↓
Flow collects/validates structured booking data (its own published contract)
  ↓
BookingFlowService.handle_confirm_booking()
  · REVALIDATES the slot with ScheduleService.check()
  · slot gone → BookingSlotConflictError → refreshed dates/times back into the Flow
  ↓
booking created atomically (single db.commit())
  ↓
ThreadRevision(status='booked') + Revision + Lead + agenda linkage
  ↓
flow_booking_token consumed (set to NULL)
```

## 3. Eligibility

The Booking Flow becomes eligible **only** when all of the following hold:

1. quote accepted (`lead.flag = ACEPTADO`, stage SCHEDULING);
2. an active focus candidate exists;
3. the inspection zone resolves (`_get_active_inspection_location`);
4. one concrete scheduling option has been established — i.e.
   `ScheduleService.check()` returned `valid=True` for a day+time;
5. `BookingFlowService.resolve_context()` accepts the minted token.

Quote acceptance alone is **not** a trigger. A rejected or ambiguous slot is **not** a trigger.

## 4. Invariants

- **No text booking.** The only code path that creates `ThreadRevision(status="booked")`
  is `ConversationEngine._process_flow_response`. The scheduling-escalation path creates
  `status="provisional"` with `needs_human=True` — a human handoff, not a booking.
- **No duplicated validation.** CE never re-implements Flow prerequisites, slot
  revalidation, or booking writes; it delegates to `BookingFlowService`.
- **One token per dispatch.** `make_booking_token()` is called once per Flow send; the
  token is stored on `WhatsAppThreadState.flow_booking_token` and consumed at booking.
- **Path attribution.** Every Booking Flow send passes
  `path_id=OutboundPathId.BOOKING_FLOW` through `OutboundSafetyGate` (frozen L2 path
  semantics for the other six call sites are unchanged).
- **Degraded path.** If `WHATSAPP_BOOKING_FLOW_ID` is unset, or the Flow contract
  rejects the token, CE falls back to the legacy data-collection Flow
  (`WHATSAPP_FLOW_ID`) — still a Flow, still gated, still never a chat booking. Website
  leads keep their dedicated website Flow. Both fallbacks are logged at ERROR/WARNING.
- **One business-hours authority.** Customer-facing hours come from
  `schedule.business_hours_for_weekday()`; the FAQ answer is generated, never literal.

## 5. Tests

`tests/test_l4_3_scheduling_semantics.py` — TEMP-01…07, ORDER-01…03, HOURS-01/02,
FLOW-01…08, FORENSIC-01/02, plus the full Wild A reproduction.
