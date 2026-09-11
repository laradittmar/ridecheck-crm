PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-META-FLOW-PREFILL-GATE-A

STATUS: CONDITIONAL_PASS
DATE: 2026-09-11
CODE COMMIT: ba22aec — NOT DEPLOYED
SCOPE: crm_test only. Production untouched. Outbound OFF. Zero Meta writes. No Wild.
CONDITION: Meta-side schema validity cannot be proven without an upload. UNPROVEN UNTIL GATE B.

---

## 1. Trace, before any change

Every response that reaches Meta, and whether it flows through `_appointment_screen_data`:

| producer | screen | via helper? |
|---|---|---|
| `handle_init` | APPOINTMENT | yes |
| `handle_date_selected` | APPOINTMENT | yes |
| revalidation conflict | APPOINTMENT | yes |
| canonical-location refusal | APPOINTMENT | yes |
| **concurrency conflict** (`:936`) | APPOINTMENT | **no — hand-built** |
| **token error** (`routes/flow_data_exchange.py`) | APPOINTMENT | **no — hand-built** |
| `handle_prepare_summary` | SUMMARY | `_summary_screen_data` |
| `handle_confirm_booking` | SUCCESS | literal |

The two hand-built responses are the ones a new required key would silently break. Both were
updated.

**The structural fact that shaped everything:** `APPOINTMENT → DETAILS` is client-side
`navigate`, carrying only `booking_token, vehicle_summary, location_summary, date, time`.
**The back end is never called between those screens.** So name and phone cannot be "sent to
DETAILS" — they must leave from APPOINTMENT's data through the navigate payload. Guessing
otherwise would have produced a candidate that silently prefills nothing.

**Agreed slot representation:** `WhatsAppThreadState.preferred_day` + `preferred_time`, set by
`_try_schedule_and_flow` on an accepted exact slot. Proven to be the same values F4 already
reads to narrow the lists — `_appointment_screen_data` reads exactly those two fields, so the
selection and the narrowing cannot disagree.

**Name source:** `Lead.nombre` + `Lead.apellido`, blank when absent. `WhatsAppContact.display_name`
is deliberately not used: it is a profile label the customer can set to anything, and no
owner-approved rule elevates it to identity.

## 2. Version control for the Flow

`meta/flows/booking/` now holds:

| file | sha256 |
|---|---|
| `PUBLISHED_28104222025943520_v7.3.json` | `274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15` |
| `CANDIDATE_v7.4-prefill.json` | `1aa60623bd7d0d5aeacb8838d85489e9d9cd7bcaf988d97e60d50b7c8c07ac54` |
| `CANDIDATE_DIFF.md` / `.patch` | human and machine diff (+47 / −12) |
| `GATE_B_PLAN.md` | both branches, not executed |

The baseline is the rollback artifact and **a test asserts it stays byte-identical**, so it
cannot drift silently. This closes a real gap: the Flow was the only artifact in the system
with no version control.

## 3. The candidate

APPOINTMENT gains `selected_date`, `selected_time`, `customer_name`, `contact_phone`,
`contact_phone_display`; both Dropdowns bind `init-value`; the Footer forwards identity.
DETAILS declares the three identity keys, binds `name`, and **the editable phone TextInput is
removed** in favour of a masked read-only caption, with the footer payload taking
`${data.contact_phone}`. Routing, screen set and SUMMARY are untouched.

## 4. Behaviour

| case | result |
|---|---|
| A — exact agreed slot, still valid | date and time both selected; both are ids in their own data-source |
| B — day known, time undecided | date selected, `selected_time` `""`, real times offered — **no earliest preselected** |
| C — open picker | neither selected |
| D — slot went stale | narrowing abandoned, full picker returns, no dead slot reselected; `confirm_booking` still revalidates |

A selection is emitted **only** when it matches an id already present in the list. That is the
invariant that keeps a binding from pointing at something that does not exist.

## 5. Phone

`contact_phone` is `wa_id`. The customer never retypes it, and **the UI is not what enforces
that**: `handle_prepare_summary` and `handle_confirm_booking` both overwrite the payload value
with `ctx.contact.wa_id`, so a tampered payload cannot change booking identity even if the
screen were altered. Only the last four digits reach a screen (`…0001`); the full value travels
as data and appears in no log, fixture or artifact.

A *coordination phone* — a different number for the appointment — is **not** invented here.
That is a business field and an owner decision: **BUSINESS DECISION REQUIRED**.

## 6. Certified tests changed, deliberately

`BF15` asserted that an empty payload phone raises "phone is required" — correct while the
phone was a required customer input. It now proves that an omitted payload phone is *not* a
customer error, plus a new case that an **absent `wa_id` still raises**, which is the guard
that now matters. `BF19` asserted `buyer_phone == "+5491155550000"` (the fixture's typed
value); it now asserts the booking records `wa_id` and explicitly **not** the payload.

## 7. Validation — reported separately, as asked

| check | result |
|---|---|
| JSON syntax (both files) | **PASS** — parse clean |
| Flow schema, locally enforceable parts | **PASS** — every `init-value` binds a key its own screen declares; routing/action names unchanged; SUMMARY still terminal; no secret-bearing field |
| Backend ↔ Flow response contract | **PASS** — every APPOINTMENT key the candidate declares is supplied by the back end, including both hand-built responses |
| Focused tests | **24 passed** |
| Booking Flow suites | **74 passed** (`test_m21_3_c_d_booking_flow` + Gate A) |
| Full regression | baseline 3897 passed / 57 failed / 9 errors → **3922 / 57 / 9**, 0 new, identical pre-existing set |

**META SERVER VALIDATION: UNPROVEN UNTIL GATE B.** Local parsing is not Meta validation, and
saying otherwise would be the mistake this programme keeps making. Meta's own validator runs
only on upload.

## 8. Gate B

Both branches are written and **not executed**; `GATE_B_PLAN.md` carries the preflight, the
operations, the smoke and the rollback for each. Branch B's rollback (restore the old
`WHATSAPP_BOOKING_FLOW_ID`) is cleaner than Branch A's, and one limitation is recorded
honestly: under Branch A, a customer holding an already-delivered Flow message may still be on
the new version until they reopen it, so rollback is not instantaneous for in-flight sessions.

## 9. Not done

No Meta write of any kind. No Flow created, cloned, published or deprecated.
`WHATSAPP_BOOKING_FLOW_ID` unchanged. Not deployed, outbound never armed, no Wild.
ConversationEngine acceptance logic, F7A rescue logic, ScheduleService, PricingService,
business hours, the same-day buffer and n8n logic are all untouched.

**Nothing here has been seen by a handset.** The prefill is real in the candidate and in the
backend contract, and invisible to customers until Gate B.
