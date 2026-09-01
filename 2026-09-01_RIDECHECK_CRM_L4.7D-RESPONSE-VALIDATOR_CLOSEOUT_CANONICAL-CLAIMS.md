PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7D-RESPONSE-VALIDATOR

# L4.7D — Canonical Response Validator

Date: 2026-09-01
Scope: a general deterministic validation layer between composition and SEND. Additive
only — the semantic pipeline order is unchanged, AI authority boundaries are untouched.
Constraints honoured: crm_test only · OUTBOUND OFF · no live sends · no Meta or n8n
changes · no production DB mutation.

---

## 1. Runtime position (Phase H)

```
COMPOSE  → AI reply or deterministic template
   ↓  _compose_secondary_answers        (FAQ reconciliation)
   ↓  _apply_required_next_question     (required location question)
   ↓  _enforce_canonical_vehicle_claim  (L4.6 vehicle finalizer)
   ↓  _validate_outbound_text           ← L4.7D CANONICAL RESPONSE VALIDATION
OutboundSafetyGate.attempt(...)         (path_id, deployment_id, correlation_id)
   ↓
SEND (Meta)
```

Both CE senders validate before the gate — `_send_text_to_wa` (customer text) and
`_send_flow_button` (Flow body text). Source-level assertions in the suite prove
(a) each sender calls the validator, (b) the call precedes `gate.attempt`, and
(c) `_send_whatsapp_cloud_text` / `_send_whatsapp_cloud_flow` appear exactly once each in
the whole engine, so no alternate text path can bypass validation.

---

## 2. Claim classes and their canonical proof (Phase A)

New module `backend/app/services/response_validator.py` (pure: no DB, no ORM, no service
calls — it reads a `CanonicalFacts` snapshot assembled by CE).

| Claim | Proof that licenses it | Unresolved means | Behaviour when unresolved |
|---|---|---|---|
| **VEHICLE** | current-focus candidate marca/modelo | no candidate, or a different vehicle named | sentence dropped (L4.6 finalizer already offers the clarification) |
| **LOCATION** | candidate zone, or cycle-scoped `state.home_zone_*` | no zone, or a zone that is only customer origin | sentence dropped |
| **PRICE** | a PricingService quote for the active candidate + zone, or an amount already sent in this cycle | no quote and no prior amount | amount rewritten to the canonical total, or sentence dropped when no quote exists |
| **AVAILABILITY** | a ScheduleService evaluation this turn, the slots it produced, or a confirmed booking | never evaluated, or a slot that was not offered | sentence dropped |
| **BOOKING** | booked ThreadRevision (`current_revision_id`) or stage BOOKED | Flow merely sent | sentence dropped |
| **ACCEPTANCE** | `lead.flag = ACEPTADO` or stage at/after SCHEDULING | conversational tone alone | sentence dropped |

Two design rules keep this general rather than phrase-specific:

1. **Only assertive sentences carry claims.** A question can never be a claim, so
   "¿Es un Peugeot 2008?" and "¿En qué zona está el auto?" always survive.
2. **Every claim class names its canonical proof.** Absence of proof means unsupported —
   never assumed.

---

## 3. Per-phase notes

**B — Vehicle.** Generalises the L4.6 finalizer to the send path: a named vehicle must
match the current-focus candidate. A *different* vehicle is blocked too, not only a
missing one. Clarification language and pending-state arming remain with L4.6.

**C — Location.** A sentence is an inspection-location claim when it names a known zone
*and* carries an inspection subject (auto / vehículo / revisión / inspección / turno /
cotización). An explicit customer-origin sentence ("vos vivís en Tigre") is allowed as
what it is. Canonical zone → allowed; different zone → blocked, with the finding naming
"customer origin stated as inspection location" when the zone is the known origin.

**D — Price.** Allowed amounts are the canonical total, base and viáticos, plus amounts
already sent to this customer in the active cycle (restating a quote the customer has
already received — that amount itself passed validation when first sent, so the AI cannot
introduce a new one). A wrong amount is **rewritten** to the canonical total rather than
dropping the sentence; with no quote at all the sentence is dropped. `_scrub_invented_price`
is unchanged and still runs earlier on the AI path — the two are compatible.

**E — Availability.** Any availability lexicon or `HH:MM` token in an assertive sentence
is a claim. It requires a ScheduleService evaluation this turn (or stored slots from one).
Named times must be in the offered set. Two deliberate exemptions, both canonical:
a **negative** statement ("a las 15:00 no tenemos disponibilidad") names a time precisely
because it is unavailable; and a **confirmed booking** licenses restating its own
appointment time.

**F — Booking.** A booking claim needs a booking noun (turno/revisión/inspección/reserva/
cita/visita) **and** a completed-state participle (confirmad*/reservad*/agendad*) in the
same sentence. Infinitives are invitations, not claims — so the Booking Flow body
("Para confirmar el turno, elegí el horario…") is untouched. Sending the Flow is not
booking: only a booked ThreadRevision licenses "tu turno está confirmado".

**G — Acceptance.** Commercial-state assertions ("presupuesto aceptado", "ya aceptaste",
"avanzamos con la reserva") require `lead.flag=ACEPTADO` or stage ≥ SCHEDULING.

**I — Failure behaviour.** Sentence-level surgery: unsupported sentences are rewritten or
removed and everything else is preserved — FAQ answers, the required next question, and any
question at all. Only when *nothing* survives is a deterministic fallback sent
("Para poder avanzar necesito confirmar algunos datos…"). Every decision is logged as
`CE_RESPONSE_VALIDATION thread_id=… claim=… allowed=… proof=… action=… detail=…`
(no PII, no message bodies, no secrets). A validator exception never breaks a turn — the
original text is returned and the error is logged.

---

## 4. Tests (Phase J)

`tests/test_l4_7d_response_validator.py` — **33/33 PASS**

VAL-VEH-01/02 (+ wrong-vehicle, question-is-never-a-claim), VAL-LOC-01/02 (+ origin-zone
with canonical elsewhere, legitimate origin statement), VAL-PRICE-01/02/03 (+ base/viáticos
components), VAL-AVAIL-01/02 (+ unoffered slot, negative availability), VAL-BOOK-01/02
(+ Flow body is not a booking claim), VAL-ACC-01/02, VAL-MIX-01 (+ required next question
survives, fallback when nothing survives, clean text untouched, empty text safe),
VAL-PATH-01 (+ no other direct sender, gate receives the validated text, canonical-facts
assembly, validator-never-breaks-a-turn, `CE_RESPONSE_VALIDATION` logging).

**Relevant gates: 926 passed, 1 skipped, 0 failed** (L1, L2, L3, L4.1, WILD-01, L4.3,
L4.4, L4.6, L4.7D, M21.3 Booking Flow + scheduler, kill switch/safety gate, FAQ
preservation, messy-turn reconciliation, M18 business logic, WILD-02 owner rules).

**Full regression: 3 161 passed, 55 failed, 9 errors, 72 skipped — zero new failures**
against the differential baseline (20 tests that were failing before L4.3 now pass).

**One certified test superseded, deliberately and disclosed:**
`test_m20_6d2_customer_reality.py::TestRC05Requote::test_rc05_price_requote` asserted that
an AI-stated $140.000 reached the customer, although no PricingService quote and no prior
message supported that amount (the fixture stores no quote; the canonical price for that
candidate + zone is $150.000). The validator now rewrites it to the canonical amount. The
test was updated to assert the canonical price **and** that the invented amount never
reaches the customer. This is exactly the defect class L4.7D exists to prevent.

---

## 5. Runtime

| Item | Value |
|---|---|
| Image | `ridecheck-crm-backend:l4.7d-validator-<sha>` |
| Target | crm_test only |
| OUTBOUND | OFF |
| Source/runtime parity | verified for `conversation_engine.py`, `response_validator.py`, `main.py`, `schedule.py`, `booking_flow_service.py` |
| AI authority | unchanged — the validator only removes or corrects claims; it grants nothing |

---

## 6. Status

- L4 remains **ACTIVE**; clean-Wild counter stays **0/3**; Wild C not run.
- Response consistency moves from **PARTIAL** (1 of 6 claim classes state-checked) to
  **all six classes state-checked on every CE outbound text path**.
- Next milestone: **L4.7E-SEMANTIC-EQUIVALENCE-CORPUS**.
