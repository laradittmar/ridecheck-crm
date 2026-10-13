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

- **No text booking.** No conversational path creates `ThreadRevision(status="booked")`.
  The scheduling-escalation path creates `status="provisional"` with `needs_human=True` —
  a human handoff, not a booking.
- **Three writers, two authorities (corrected — G3-7R, 2026-10-13).** This invariant used
  to read "the only code path that creates `ThreadRevision(status="booked")` is
  `ConversationEngine._process_flow_response`". The G3-7P architecture audit measured that
  it was wrong in two ways. Customer offer acceptance has TWO writers —
  `_process_flow_response` and `BookingFlowService.handle_confirm_booking` — and a THIRD
  writer exists outside offer acceptance entirely: `PATCH /api/revisions/{id}` →
  `ThreadRevisionService.patch_revision`, behind the `/api/revisions` MACHINE prefix, which
  accepts `status='booked'` with a slot. That is an authenticated operator/machine
  authority, it is legitimate, and it is deliberately NOT routed into customer offer
  acceptance. Its bookings carry `offer_id = NULL`, which is why the one-offer/one-booking
  index is partial.
- **One offer, at most one booking — guaranteed by the database.**
  `UNIQUE (thread_revisions.offer_id) WHERE offer_id IS NOT NULL`. Both acceptance writers
  write `offer_id`; a violation is translated into the typed replay outcome and never
  surfaces as an `IntegrityError`, a SQLSTATE or a constraint name.
- **The submitted slot is revalidated by BOTH writers (G3-7R).** `offered_date` /
  `offered_time` on the offer are HISTORICAL metadata — owner policy 2026-10-13 permits the
  Flow to offer a slot other than the one the offer recorded — so what must pass
  `ScheduleService.check()`, under the same per-date advisory lock, is the SUBMITTED slot.
  Revalidation runs BEFORE acceptance, so a vanished slot leaves the offer ISSUED and the
  customer free to pick another.
- **No duplicated validation.** CE never re-implements Flow prerequisites, slot
  revalidation, or booking writes; slot revalidation is `ScheduleService`, called by both
  writers through the same authority, and the Flow's own screens remain
  `BookingFlowService`'s.
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
