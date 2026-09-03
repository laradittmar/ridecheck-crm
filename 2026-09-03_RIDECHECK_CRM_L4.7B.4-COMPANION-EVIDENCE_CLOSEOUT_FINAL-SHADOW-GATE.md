PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7B.4-COMPANION-EVIDENCE

# L4.7B.4 — companion evidence, and the end of prompt work

Date: 2026-09-03
Shadow only · zero canonical authority · no CE behaviour change · model unchanged ·
OUTBOUND OFF · crm_test only · production DB untouched · no Wild.

---

## 1. Verdict

**CONDITIONAL_PASS. The gate still fails on the same single line, and prompt work on it is
now exhausted.**

| Gate line | Threshold | Draw 1 | Draw 2 | |
|---|---|---|---|---|
| unsupported inference, REAL | 0.000 | 0.000 | 0.000 | ✅ |
| unsupported inference, overall | ≤ 0.01 | 0.000 | 0.000 | ✅ |
| role accuracy | 1.000 | 1.000 | 1.000 | ✅ |
| ambiguity/conflict handling | ≥ 0.98 | 1.000 | 1.000 | ✅ |
| field precision, REAL | ≥ 0.85 | **0.897** | **0.897** | ✅ |
| field recall, REAL | ≥ 0.85 | **0.867** | **0.867** | ✅ |
| field precision, overall | ≥ 0.80 | **0.950** | 0.942 | ✅ |
| field recall, overall | ≥ 0.85 | **0.898** | 0.890 | ✅ |
| group I precision | ≥ 0.70 | **0.933** | 0.923 | ✅ |
| **every group A–L recall** | **≥ 0.70** | I 0.737 · **L 0.636** | I 0.684 · **L 0.636** | ❌ |

Group I moved from 0.632 to 0.737/0.684 — it now straddles the floor rather than sitting
well below it. Group L moved 0.591 → 0.636 and stayed there in both draws.

**L4.7C does not start.** Per Part 14 no further broad semantic milestone is proposed; the
residual is handed back for an explicit decision (§7).

## 2. What was built

Three of the four changes are **deterministic** and live in the schema and mapper, where a
guarantee can actually be made. The prompt carries only the general rule.

| Change | Where | Effect |
|---|---|---|
| A correction whose **relation** is real is meaningful on its own — "he replaced the car" is a fact even when the discarded car was never named | `turn_evidence.py` | a relation-only correction can no longer be pruned as "empty" |
| A **named** superseded vehicle yields the replacement relation when the interpreter omits it | `semantic_interpreter._derive_companions` | group I recall 0.632 → 0.737 |
| A correction that **moves a year** yields the corrected year as vehicle evidence | same | the year-only correction case stops losing its value |
| The process fact is accepted from its own `readiness` slot as well as from the intents array, and never emitted twice | mapper | tolerant to either spelling |
| The general companion rule: *a value travels with the relation that explains it*; the correction item does not replace the corrected value; HESITATE is doubt about the proposal, not a promise | prompt (`understand/1.18`) | supports the above |

**Nothing is inferred about the customer.** Both derivations are strictly downstream of what
the interpreter itself produced: with no named superseded vehicle and no year-moving
correction, both lists come back untouched. A template echo — an empty row carrying only
`is_superseded: true` — derives nothing, a guard added after it manufactured a false
correction on two Wild cases in an intermediate revision.

## 3. Iteration record

Six revisions, each measured on the full 162-case corpus. Part 8's rejection rules were
applied literally.

| Version | P | R | REAL P | REAL R | Unsup | I recall | L recall | Decision |
|---|---|---|---|---|---|---|---|---|
| 1.12 (inherited) | 0.938 | 0.890 | 0.871 | 0.900 | 0.000 | 0.632 | 0.591 | baseline |
| 1.13 prompt state-test + derivations | 0.922 | 0.882 | 0.800 | 0.800 | 0.000 | **0.842** | 0.455 | rejected — REAL P/R < 0.85, L regressed |
| 1.14 state-test reverted | ~0.93 | 0.898 | 0.806 | 0.833 | 0.000 | 0.789 | 0.591 | rejected — REAL P/R < 0.85 |
| 1.15 dedicated `readiness` slot | 0.932 | 0.909 | 0.828 | 0.800 | 0.000 | 0.842 | **0.682** | rejected — REAL P/R < 0.85, a false ACCEPT appeared |
| 1.16 = 1.12 prompt + derivations | 0.923 | 0.894 | 0.839 | 0.867 | 0.000 | 0.737 | 0.682 | rejected — REAL P < 0.85 (a derived correction on two Wild cases) |
| 1.17 derivation guard v1 | 0.934 | 0.894 | 0.839 | 0.867 | 0.000 | 0.737 | 0.636 | superseded — guard incomplete |
| **1.18 (shipped)** | **0.950** | **0.898** | **0.897** | **0.867** | **0.000** | **0.737** | 0.636 | **shipped** |
| 1.18 (second draw) | 0.942 | 0.890 | 0.897 | 0.867 | 0.000 | 0.684 | 0.636 | variance check |

Four revisions were rejected for exactly the reasons Part 8 lists. The instructive one is
**1.15**: giving the process fact its own response slot was the single most effective change
for group L (0.591 → 0.682) and it still had to be rejected, because it pulled attention away
from the stance slot and cost REAL precision and a false ACCEPT. Raising L by making the
model look somewhere else does not come free.

### Groups A–L (shipped draw)

| Group | P | R | | Group | P | R |
|---|---|---|---|---|---|---|
| A intent | 0.929 | 0.929 | | G scheduling | 0.846 | 0.846 |
| B vehicle | 0.948 | 0.982 | | H ordered alternatives | 0.889 | 0.889 |
| C location role | **1.000** | **1.000** | | I corrections | 0.933 | **0.737** |
| D quote request | 1.000 | 0.750 | | J FAQ + business | 0.907 | 0.975 |
| E acceptance | 1.000 | 0.909 | | K noisy/ASR | 0.938 | 0.882 |
| F rejection/hesitation | 0.778 | 0.700 | | L future/not ready | 1.000 | **0.636** |

## 4. Critical cases (Part 10), both draws

| Case | Result |
|---|---|
| **REAL-001, REAL-003, REAL-004** | **CLEAN** in both draws |
| REAL-002 (heavy ASR-like noise) | 2 misses, **zero unsupported inference** |
| **WILD-A-04** | **CLEAN** — PRIMARY TOMORROW 15:00, FALLBACK THURSDAY flexible |
| **WILD-B-02** | **CLEAN** — Berazategui INSPECTION_LOCATION, Tigre CUSTOMER_ORIGIN |
| **WILD-B-01** | **Peugeot 2008 + 2014 preserved**; one false positive, an unrequested `quote_request` |

**Zero unsupported inference on every owner example and every Wild case, in both draws.**
False ACCEPT rate 0.000 in both.

## 5. Live runtime probe

On the deployed image, one two-message burst — *"Pensaba comprar un Focus pero al final es un
Corolla 2020"* + *"Todavía estoy buscando, cuando encuentre te aviso"*:

```
vehicles      Toyota Corolla (2020)  ·  Ford Focus (superseded)
corrections   REPLACE_CANDIDATE  Focus → Corolla
readiness     SEARCHING_NOT_READY        acceptance  FUTURE_INTENT
dispatch      async  ·  turn returned with the job still queued
```

Both companion pairs produced in one live turn. `crm_test.whatsapp_messages` stayed at
**6 rows**.

## 6. Tests and regression

`tests/test_l4_7b_4_companion_evidence.py` — **16/16 PASS** (COMP-01…12 plus no-duplicate-year,
slot-equals-array, and the explicit check that Phase-A empty-item sanitation is **not**
weakened). Stub transport: no network.

A reload-identity defect was found and fixed inside this milestone: comparing
`CorrectionRelation` by identity made every empty correction look meaningful after a module
reload in another suite, which would have quietly disabled empty-item sanitation in the full
run. It is now compared by value, like `AcceptanceSignal` since L4.7B.2.

Version and hash pins in three earlier suites were realigned — deliberately, as in L4.7B.3,
because this milestone's job is to change the interpreter.

Full regression: **3 392 passed / 60 failed / 9 errors** — failure set identical to the
baseline, **zero new failures**. Launch-relevant failures: **0 new**; the pre-existing
`test_l4_2_clean_slate` allowlist case and its 9 companion errors are environment-dependent
(`CLOSED_BETA_ALLOWED_WA_IDS` is unset in the test process, set in the runtime container).
Unknown: **0**.

## 7. Residual — and the decision it needs

| Residual | Cases | Classification | Evidence |
|---|---|---|---|
| **Group L: the process fact is not emitted beside a stance** on very short bursts | SYN-FUT-02, -03, -04, -06, -08 | **MODEL_LIMIT** (prompt exhausted) — with a **CORPUS_LIMIT** alternative | Six formulations across two milestones — imperative, ordered state test, sharpened definition, dedicated response slot, checklist — moved it between 0.455 and 0.682 and never held ≥ 0.70. The one formulation that reached 0.682 cost REAL precision and a false ACCEPT. The alternative reading: whether "Estoy mirando, cualquier cosa te escribo" carries *two* evidence items or one is a labelling decision, and it is yours, not the model's. |
| **Group I: the relation on bursts naming neither side** | SYN-CORR-05, -06, -08 | **PROMPT_LIMIT** | The deterministic derivation covers every burst where a car is named on the discarded side; when the model names neither side there is nothing to derive without inventing. |
| REAL-002 produces no evidence | 1 | **MODEL_LIMIT** | Heavy ASR-like noise; no prompt formulation recovered it, and no unsupported inference was traded for it |
| `quote_request` from a service question | WILD-A-01, WILD-B-01 | **PROMPT_LIMIT** | Three explicit negative rules already in the prompt; low impact, no unsupported inference |
| HESITATE recall 0.500 | 3 | **PROMPT_LIMIT** | Improved to 0.667 in two intermediate revisions, both rejected on other grounds |

**MODEL CHANGE REQUIRED: NO** — not because escalation is forbidden, but because it has not
been authorised. Part 15's conditions are now arguably met for the group-L class alone
(instrument clean, prompt work exhausted, gate materially short on that one line). Testing an
alternate model **in shadow, on the same corpus, with the same metrics, as a separate
comparison** is the natural next experiment, and it needs your explicit go-ahead.

Three options, and each is a decision rather than a milestone:

1. **Accept the gate as met in substance** — nine of ten lines pass on both draws, the tenth
   fails by one to three evidence items in one group — and proceed to
   L4.7C-SEMANTIC-RECONCILER-DESIGN with the residual recorded.
2. **Authorise one shadow model comparison** for the companion-emission class only.
3. **Revisit the two group-L labels** that assert a second evidence item on a five-word
   burst, if on reflection the single stance is the whole truth there.

## 8. Safety

No runtime semantic behaviour change for customers, no ConversationEngine change, no
canonical mutation, no outbound, no authority migration, no Wild. The interpreter still
imports no ORM, no PricingService, no ScheduleService, no OutboundSafetyGate and no
ConversationEngine. Image `ridecheck-crm-backend:l4.7b4-companion-c33ab79` on **crm_test
only**, `OUTBOUND_ENABLED=false`, async shadow active, source/runtime parity verified by
sha256 on all five touched modules. Production DB untouched.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.
