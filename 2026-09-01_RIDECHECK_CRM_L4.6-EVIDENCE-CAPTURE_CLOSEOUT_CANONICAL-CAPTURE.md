PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.6-EVIDENCE-CAPTURE

# L4.6 — Evidence-Driven Vehicle + Location Capture

Date: 2026-09-01
Scope: the finite remediation of the L4-WILD-B findings VEH-A, VEH-B, LOC-A, LOC-B, OBS-A.
Constraints honoured: crm_test only · OUTBOUND OFF throughout · no live WhatsApp send ·
no Meta changes · no n8n business-logic changes · no production DB mutation ·
no hardcoded alias for "2008" · L1/L2/L3 not reopened.

---

## 1. Owner expectation, as implemented

Natural customer language is now sufficient. Intent wording still steers routing and tone;
it no longer decides whether deterministic evidence may become canonical state.

| Customer says | Before L4.6 | After L4.6 |
|---|---|---|
| "para revisar un 2008 del 2014" | nothing persisted | **Peugeot 2008 / 2014 / SUV_4X4_DEPORTIVO persisted** |
| "Quería revisar un 2008 del 2014" | persisted | persisted (unchanged) |
| "una 2008 del 2014" | nothing persisted | persisted |
| "una Taos 2020" · "un Focus 2017" | persisted | persisted (unchanged) |
| "quiero revisar un 2008" | confirmation asked | confirmation asked, now always with pending state armed |
| "2008 o 2014" | no candidate | no candidate (ambiguity guard intact) |

---

## 2. Phase A — evidence-driven capture

Three gates had to open, not one:

1. **`_numeric_model_ctx` → `_evidence_capture_ctx`.** Deterministic extraction now runs
   whenever the thread has no candidate and the stage is QUALIFYING/NULL (or the intent is
   already established). The old condition additionally demanded a phrase from
   `_PREPURCHASE_SIGNALS` / `_INSPECTION_REQUEST_PATTERNS`; "para revisar un 2008 del 2014"
   matched neither, which is precisely why Wild B persisted nothing.
2. **Layer D (FAQ bypass).** Its guard already refused to intercept a turn that *names a
   vehicle* — but `lookup_vehicle` cannot see numeric models, so a FAQ-dominant burst
   ("…¿Se puede pagar con débito?") was answered as pure FAQ and the vehicle evidence was
   discarded before any extraction ran. The guard now also consults
   `extract_model_del_year`. This was the second, hidden half of VEH-A: bisecting the live
   burst showed the candidate appeared for every prefix and vanished only when the payment
   FAQ was appended.
3. **Bare numeric models keep the certified behaviour** (see §7).

No phrase-specific regex was added; `_PREPURCHASE_SIGNALS` and
`_INSPECTION_REQUEST_PATTERNS` are unchanged. Existing ambiguity guards
(multi-year tokens, generic vehicle words, brand contradiction) are untouched.

---

## 3. Phase B — response must match canonical state

`ConversationEngine._enforce_canonical_vehicle_claim()` runs on the single outbound text
path, after `_apply_required_next_question`:

- a candidate exists → the reply is returned untouched;
- no candidate, QUALIFYING/NULL, and the reply names a vehicle resolvable by
  `lookup_vehicle` / `extract_model_del_year` / `_contextual_numeric_model_lookup` →
  the pending confirmation state is armed and the reply is closed with
  *"Todavía no tengo confirmado el vehículo, así que te confirmo: ¿Es un Peugeot 2008?"*;
- the reply names no vehicle → untouched.

The Wild B reply that started this milestone is now impossible: an AI composition can no
longer imply certainty the canonical state does not hold.

---

## 4. Phase C — location evidence with an origin clause

`_strip_customer_origin_clauses()` splits on clause boundaries (`,` `.` `;` `pero`
`aunque`) and drops the clauses containing `_CUSTOMER_ORIGIN_RE` ("soy de", "vivo en",
"estoy en", "vengo de", "me encuentro en"). In `_apply_zone_from_text` the origin clause
now suppresses **only itself**:

```
"Está en Berazategui, pero yo soy de Tigre."
  vehicle-location clause     → []           (no explicit subject)
  origin clauses stripped     → "Está en Berazategui"
  re-read                     → Sur / Berazategui   → candidate, or state buffer
  "Tigre"                     → customer origin, never the inspection location
```

`"El auto está en Berazategui"` and `"Está en Berazategui"` behave exactly as before, and
`"Yo soy de Tigre pero el auto está en Berazategui"` still resolves Berazategui through the
original explicit-subject path. LR-2 / LR-3 / SC17 semantics are preserved.

---

## 5. Phases D + E — deterministic confirmation and buffered replay

- Every deterministic clarification arms `pending_fuzzy_catalog_key` **and**
  `pending_turn_evidence_text`; the Phase B finalizer arms the same state whenever a reply
  would otherwise ask an unbacked confirmation (rule B of the prompt: an AI-authored
  confirmation cannot exist without deterministic backing).
- `_attach_buffered_location()` gives a newly created candidate the inspection location the
  customer already provided in this cycle — on the fuzzy-acceptance path and on both
  deterministic capture paths. **This does not reopen L1 RISK-03:** `_execute_cycle_reset()`
  clears `home_zone_*` at every cycle boundary, so a surviving buffer is by construction
  current-cycle evidence, and an explicit per-candidate zone is never overwritten.
- Net effect: the customer never repeats a location because the vehicle was confirmed
  afterwards.

---

## 6. Phase F — CE decision logging

`_decision_log()` emits one structured, secret-free record per decision:

```
CE_DECISION event=intent_gate thread_id=2037 stage=QUALIFYING prepurchase=False
            inspection_request=False evidence_capture=True
CE_DECISION event=vehicle_candidate_persisted thread_id=2037 source=model_del_year
            marca=Peugeot modelo=2008 tipo=SUV_4X4_DEPORTIVO anio=2014
CE_DECISION event=location_extracted thread_id=2037 source=origin_clause_stripped
            zone_group=Sur zone_detail=Berazategui target=state_buffer
CE_DECISION event=location_buffer_attached / vehicle_clarification_armed /
            response_state_reconciled …
```

Events cover: intent gate result, vehicle extraction and persistence, clarification
arming, location extraction and buffering, buffer attachment, and response reconciliation.
No message bodies, tokens, phone numbers or Meta payloads are logged. `main.py` attaches a
stream handler to the `app` logger tree (level via `LOG_LEVEL`, default INFO) so the records
reach container logs — Wild B could only be reconstructed by re-executing code (OBS-A).

---

## 7. Owner-rule conflict (surfaced, not silently resolved)

The L4.6 prompt lists **VEH-03** "quiero revisar un 2008" → *candidate persisted*.
The certified WILD-02-B owner rules **W02-O08…O12** require the opposite for a bare numeric
model: send the "¿Es un Peugeot 2008?" clarification, arm pending state, and create **no**
candidate before confirmation.

Resolution applied, conservative: a bare number is the genuinely ambiguous case
(model vs year) that WILD-02-B was built for, and the prompt itself allows clarification
when ambiguous. So:

- `"un 2008 del 2014"` (model **plus** year — unambiguous) → **persisted immediately**;
- `"un 2008"` (bare) → **resolved deterministically + confirmation armed**, exactly as
  certified.

VEH-03 is implemented as "resolves and arms confirmation". **If the owner wants a bare
numeric model to persist without confirmation, W02-O08…O12 must be retired explicitly** —
that is an owner decision, not one to take inside a remediation milestone.

---

## 8. Tests

`tests/test_l4_6_evidence_capture.py` — **28/28 PASS**

VEH-01…08 (incl. "intent detectors both False → candidate still persisted"),
STATE-01/02 (+2), LOC-01…06 (+2: explicit candidate zone never overwritten, origin-clause
stripper), CONF-01…04, the **full Wild B reproduction** (turn 1 burst → single candidate
Peugeot 2008 / 2014 / SUV_4X4_DEPORTIVO; location turn → Sur/Berazategui with Tigre excluded;
no redundant confirmation; pricing 150 000 + 90 000 = **240 000**), and 3 decision-logging
tests.

Relevant gates (L1, L2, L3, L4.1, WILD-01, L4.3, L4.4, L4.6, M21.3 Booking Flow, kill
switch/safety gate, WILD-02, WILD-04R-F1, M21.1.4 ASR, M21.2 location suites, M21.1.7
consolidated): **558 passed, 1 skipped, 0 failed.**

Full regression: **3 128 passed, 55 failed, 9 errors, 72 skipped** — the identical
pre-existing failure set; **0 new failures, 0 unknown**.

---

## 9. Runtime

| Item | Value |
|---|---|
| Image | `ridecheck-crm-backend:l4.6-evidence-<sha>` (built from this source) |
| Target | crm_test only |
| OUTBOUND | OFF |
| CE decision logs | present in container logs |
| Source/runtime parity | verified for `conversation_engine.py`, `main.py`, `schedule.py`, `booking_flow_service.py` |

---

## 10. Status

- Wild B: **FAIL** (vehicle + location evidence capture) — remediated here, not yet proven live.
- Clean-Wild counter: **0/3**.
- L4: still **FAIL**. Wild C requires a fresh tester zero state (L4.4 procedure) and owner
  outbound authorization.
