PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-F2-NEXT-AVAILABLE-SCHEDULING

STATUS: PASS
DATE: 2026-09-09
CODE COMMIT: 3e97654
IMAGE: ridecheck-crm-backend:w5f2-next-available-3e97654
SCOPE: crm_test only. Production untouched. OUTBOUND OFF. Wild not resumed.

---

## 1. Part 1 — the traced path

```
burst → _parse_scheduling_text(texts, today)   → (day_iso | None, time_str | None)
      → _detect_time_period()                  → 'manana' | 'tarde' | None
      → branch 1  escalation (insistence on an unavailable slot)
      → branch 2  period-only, using active_requested_date
      → branch 2b ordinal selection from last_visible_slots
      → branch 3  day / day+period            → _handle_day_only_request
      → exact day+time                        → _try_schedule_and_flow
```

| case | handler |
|---|---|
| day-only | `_handle_day_only_request` — requires a named day |
| day+period | same, `period=` |
| exact time | `_try_schedule_and_flow` — requires day **and** time |
| no-slots day | same handler, "¿Tenés otro día preferido?" |
| **delegated choice** | **none existed** |
| **urgency** | **none existed** |

**ROOT CAUSE: every scheduling handler required a customer-named day.** "decime vos cuando
pueden" parses to no day, no time and no period, so it fell past every branch with nothing to
catch it. The customer's Friday mention *was* parsed, the day was empty, and the reply asked
for another day — ignoring the half of the sentence that had handed us the decision.

And because `_dispatch_booking_flow_for_day` only fires once a **named** day has slots
(`display_slots=0` on both turns), the Booking Flow could never open. That is exactly the
symptom reported: *"the bot never sends the flow."*

The scheduler was never wrong. Berazategui genuinely had nothing on Thursday or Friday. The
answer the customer needed — Saturday, 13:30 and 14:00 — was one query away and nobody made it.

## 2. Parts 2/3/4 — intent and deterministic forward search

`ScheduleService.find_next_available(zone_group, zone_detail, start_day, horizon_days)`
walks days in order and returns the first with capacity. Availability still comes from
`list_slots`, so travel validity, occupancy, business hours and Sunday closure apply
unchanged — **the search adds no availability rules of its own; it only searches in order.**

Horizon is **14 days**, chosen as the existing product rule rather than a new number: it is
`BOOKING_HORIZON_DAYS`, the Flow date picker's own range. Offering a day the Flow cannot
display would be a dead end, and a test asserts the two stay equal.

Intent detection is by meaning, in three separate concepts:

- `_NEXT_AVAILABLE_PATTERNS` — delegated choice *or* earliest availability
- `_URGENCY_PATTERNS` — a reason, never a permission
- `_EARLIEST_REJECTED_PATTERNS` — the option we produced does not solve the problem

Two entry points, deliberately narrow:

1. a scheduling turn with no named day where the customer delegated;
2. a named day that turns out **empty** *and* the customer also delegated — the exact Wild
   sentence, "no se viernes? decime vos cuando pueden".

A plain "¿el viernes?" on an empty day still gets the plain answer. Nothing about the
named-day path changed.

## 3. Part 5 — urgency

Urgency changes **only the ordering objective**: earliest valid slot first. It does not relax
travel rules, occupancy, business hours, quote authority or booking revalidation, and it can
never surface a day the scheduler rejected. Tested directly: "que sea lo antes posible porque
me lo venden" returns the same Saturday the non-urgent phrasing returns.

## 4. Parts 6/7 — Flow-first, and the exact Wild case

Once a day is found the Booking Flow is dispatched; prose slot-listing exists only as a
fallback if the Flow fails to open, and a test asserts that ordering. The customer is never
asked "¿qué día preferís?".

Live, on the deployed image, against the real seeded agenda:

```
Sur/Berazategui  → 2026-09-12 Sat  slots ['13:30','14:00']  checked 3, skipped Thu, Fri
Norte/San Isidro → 2026-09-10 Thu  slots ['12:30','13:00']  checked 1
Oeste/Ramos Mejía→ 2026-09-11 Fri  slots ['16:30','17:00']  checked 2, skipped Thu
```

The first line is the Wild, answered.

## 5. Part 8B — human handoff

**The escalation mechanism already existed and is reused, not reimplemented** — so its
guarantees are inherited rather than duplicated: `needs_human`, `necesita_humano`,
`estado=ATENCION_HUMANA`, `STAGE_HUMAN`, a provisional ThreadRevision + CRM Revision priced
through the same `PricingService`, and the operator email via
`_send_scheduling_handoff_email`. No Flow dispatch and no booking exist in that path.

Two new routes into it:

- **earliest option rejected** — gated on `state.active_requested_date`, i.e. a date must
  actually have been offered. Urgency alone never escalates: wanting it soon is not the same
  as rejecting what we offered, and escalating before automation has tried would waste the
  operator's time.
- **no capacity inside the horizon** — a human is told, rather than the customer being asked
  to keep naming days against capacity that does not exist.

The canonical message promises a person will look; it never implies an earlier slot exists.

## 6. Part 11 — the audit-setup finding, mine

`scripts/verify_agenda_capacity.sh` reports capacity **per representative zone**. The
L4.7W5-PREP gate accepted a week because *some* zone had slots each day — and Sur was already
empty on Tue/Thu/Fri in that very output, unflagged. That made the owner's natural location
unbookable for the first three days of the Wild.

Scarcity is **not** removed; it is legitimate test pressure. It is now simply visible first:

```
2026-09-10 Oeste/Ramos Mejía · 2026-09-10 Sur/Berazategui
2026-09-11 Norte/San Isidro  · 2026-09-11 Sur/Berazategui
```

## 7. Part 12 — burst_message_count

**Observability only; safe; fixed in scope.** `_fetch_burst_messages` returns `[]` when there
is no previous cursor — a thread's first turn — so a 3-voice-note opening burst recorded 1.
Nothing behavioural reads the value; it is written to `ai_events` telemetry alone, and the
evidence list already held all three messages, which is why all three questions were answered.
Now counted from the evidence when the burst query has no cursor to work from. No debounce
change.

## 8. Tests and regression

**26 tests**: NEXT-01…15 (intent, forward search, Sunday, travel/occupancy, ordering, bound,
named-day and hesitation untouched), HANDOFF-01…15 (preconditions, reuse of the existing
mechanism, no promise, no automation after handoff), plus authority-contract and
no-regression checks.

**Regression: 3790 passed / 57 failed / 9 errors** — identical to the W5-F1 baseline,
**0 new**, +26 passing.

## 9. State

Wild session evidence preserved before reset:
`/opt/ridecheck-crm-forensics/L4.7W5-WILD-SESSION_20260909T191114Z.tar.gz`
sha256 `6c3e0a041f27d08381d399bad61d89489a8a9da229792f448a5aec1611121afe` (14 messages).

Tester reset with the W5-F1 safe reset, which **archived 5 real outbound WAMIDs** rather than
destroying them — that fix exercised on live data for the first time. Tester at zero, agenda
intact (14 appointments), outbound OFF, deployment identity `3e97654` verified.

## 10. What remains unproven

The forward search, the Flow-first dispatch on a searched day, and the handoff routes are
proven deterministically and against the live agenda, but **no handset has seen any of them**.
Outbound stayed off and the Wild was not resumed. The next Wild is where they earn live
evidence.
