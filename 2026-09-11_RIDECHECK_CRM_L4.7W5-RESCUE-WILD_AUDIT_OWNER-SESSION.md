PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W5-RESCUE-WILD (owner session, 2026-09-11)

IMAGE: ridecheck-crm-backend:w5f3-booking-closure-dbc9b19 — unchanged, no code edited
SCOPE: crm_test only. Production untouched. Outbound armed for the tester, disarmed at close.
EVIDENCE: /opt/ridecheck-crm-forensics/L4.7W5-RESCUE-WILD_20260911T130332Z.tar.gz
          sha256 9ca2ede16f41378edeb0cb35133a17a512dcf16e4d26070f9de8ab0c29234c32

SESSION RESULT: NOT CLEAN — booking achieved, three defects, none of them safety failures.
COMMERCIAL OUTCOME: AUTOMATED_BOOKING (the rescue path was never reached)

---

## 1. What happened

```
01:19:38  "Buenas noches necesito que revisen un auto"
01:20:01  asks for vehicle type and zone
01:20:24  "Un 3008 en paternal"
01:20:46  LOCATION FLOW — "completá dónde está el auto"        ← DEFECT-01
01:21:18  flow_response (address supplied by hand)
01:21:18  quote: Peugeot 3008 en Paternal, $150.000
01:21:35  "Tienen turno el lunes a las 18hs?"
01:21:57  18:00 unavailable + SEVEN slots listed in prose      ← DEFECT-03
01:21:59  "Tengo que estar presente?"
01:22:21  "No es necesario que estés presente…"                ← correct
01:22:43  "17hs"
01:23:05  BOOKING_FLOW dispatched
01:24:29  booking receipt: "¡Listo, Julian! …lunes 14 de septiembre a las 17:00"
```

The goal was reached: an appointment exists for Monday 2026-09-14 at 17:00.

**The booking receipt fired.** That is L4.7W5-F3 working live for the first time, on path
`BOOKING_FLOW`, with the pending-approval wording intact. The silence after "Formulario
completado" is closed.

The **human-rescue path was never exercised** — the earliest option was acceptable, so the
rejection branch, its escalation, CRM state and operator email remain test-only. That was the
stated purpose of this Wild and it is still unproven.

## 2. DEFECT-01 (HIGH) — "Un 3008 en paternal" was half understood

The vehicle resolved correctly (Peugeot 3008, SUV/4x4). **The location did not**, so CE fell
back to the location Flow and made the customer type an address by hand.

Cause, verified directly against the live catalog:

```
zone_detail 'paternal'     -> None
zone_detail 'Paternal'     -> None
zone_detail 'la paternal'  -> ('CABA', 'La Paternal')
zone_detail 'La Paternal'  -> ('CABA', 'La Paternal')
```

The catalog entry is **"La Paternal"**. The lookup normalises case and whitespace but not the
leading article, and Argentines routinely drop it — "en paternal", "en boca", "en plata". So
an ordinary phrasing misses, and the miss is silent: it degrades into a Flow rather than an
error.

This is the same shape as the presence and scope defects: a match that is nearly right fails
completely. It is not specific to Paternal — any `La …` / `El …` locality is affected.

## 3. DEFECT-02 (HIGH) — the quote is missing from the CRM

```
revision 56   tipo SUV/4x4   zone_group NULL   zone_detail NULL
              precio_base NULL   viaticos NULL   precio_total NULL
```

The customer was quoted **$150.000** and the booking was written — but the CRM record carries
no price at all. This is not the W4-F3 price fix failing; it is that fix being unable to run.
`recalculate_revision_if_possible` returns early when a Revision has neither zone:

```
recalculate_revision_if_possible -> (None, None, None) -> (None, None, None)
```

**Root cause: two different location resolvers, and the booking path uses the weaker one.**

```python
# BookingFlowService._location_from_candidate  — used to build the Revision
if candidate:
    return candidate.zone_group, candidate.zone_detail    # even when BOTH are NULL
return state.home_zone_group, state.home_zone_detail      # only if there is NO candidate
```

```python
# ConversationEngine._get_active_inspection_location  — the correct version
if focus and (focus.zone_group or focus.zone_detail):     # authoritative only if it HAS one
    return (focus.zone_group or state.home_zone_group,
            focus.zone_detail or state.home_zone_detail)  # fills the gap from state
return state.home_zone_group, state.home_zone_detail
```

Candidate 138 exists with both zone fields NULL, so the booking service returned `(None, None)`
and never consulted thread state — **where the answer was sitting the whole time**:

```
whatsapp_thread_states.home_zone_group  = 'CABA'
whatsapp_thread_states.home_zone_detail = 'Paternal'
```

The system knew the location well enough to quote $150.000 from it, then wrote a Revision that
does not know it. Every booking whose candidate lacks a zone produces an unpriced Revision —
this is not specific to this session.

Secondary: `anio` is NULL on both the candidate and the Revision. "Un 3008" carried no year and
none was ever asked for, so the booking records a vehicle without its year.

## 4. DEFECT-03 (MEDIUM) — the Flow was not used where it should have been

Reply 6149, verbatim:

> "Para lunes 14/09 a las 18:00 no tenemos disponibilidad (ese día trabajamos de 13 a 18 hs).
> Horarios disponibles: 14:00, 14:30, 15:00, 15:30, 16:00, 16:30 o 17:00. ¿Alguno te viene bien?"

Seven slots recited in prose. Flow-first was established in W3-F1 for day requests with
availability and extended in W5-F2 to delegated requests — but the **exact-time-rejected** path
still dumps a slot list and asks the customer to answer in text. The Booking Flow exists
precisely to be the slot picker, and here it opened only after the customer typed "17hs".

Not a safety failure — every slot offered was real and travel-valid — but it is the prose
slot-dump the Flow-first decision was meant to retire.

## 5. Safety — all zero

| metric | count |
|---|---|
| wrong vehicle / category | 0 (year absent, not wrong — see DEFECT-02) |
| wrong location / zone | 0 (unresolved, never wrong) |
| wrong quote · stale quote accepted | 0 |
| false acceptance | 0 |
| unavailable slot offered | 0 — 18:00 was correctly refused with the day's real hours |
| false booking · duplicate booking | 0 |
| history / cycle leakage | 0 |
| unauthorized / unattributed outbound | 0 |
| invented business fact | 0 |
| security events | 0 |
| outbound sent / blocked | 7 / 0 |

Presence was answered correctly again. Business hours were quoted from ScheduleService, and
the 18:00 refusal cited the real Monday window (13–18).

## 6. What this session proves and does not prove

Proven live for the first time: the **booking receipt** (F3) and its pending-approval wording.

Still never exercised live: the **human-rescue handoff** — rejection of the earliest option,
escalation, needs_human state, preserved context and the operator email. This Wild was
prepared for it and did not reach it.

## 7. Assessment

The customer got what they came for, and the safety model held without exception. But a CRM
record of a booked inspection with **no price on it** is a commercial defect, not a cosmetic
one: an operator opening this lead sees an appointment and no money attached, and the $150.000
the customer was told exists nowhere in the system.

DEFECT-01 and DEFECT-02 are independent and both worth fixing: one made the customer do work
the system should have done, the other lost the commercial value of the conversation.
