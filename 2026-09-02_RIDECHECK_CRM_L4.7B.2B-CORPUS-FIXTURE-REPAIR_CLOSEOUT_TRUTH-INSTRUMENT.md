PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7B.2B-CORPUS-FIXTURE-REPAIR

# L4.7B.2B — repairing the truth instrument

Date: 2026-09-02
Corpus and evaluation harness only · interpreter byte-identical · no prompt, model, schema
or CE change · OUTBOUND OFF · crm_test only · production DB untouched · no Wild.

---

## 1. Verdict

**CONDITIONAL_PASS.** The ruler was bent, and straightening it moved the numbers a long way
without touching the thing being measured:

| | Before (L4.7B.2A) | After (L4.7B.2B) |
|---|---|---|
| overall precision | 0.716 | **0.885** |
| overall recall | 0.631 | **0.728** |
| unsupported inference | 0.0123 | **0.0062** |
| clean cases | 92 | **99** |
| REAL precision / recall | 0.720 / 0.621 | **0.800 / 0.667** |
| group I precision | 0.333 | **0.909** |
| group J precision | 0.250 | **0.857** |

**None of this is model improvement.** `semantic_interpreter.py` has the identical SHA-256
before and after, the prompt is still `understand/1.4` and the model is still `gpt-4o-mini`.
Every point of movement is a measurement defect that has been removed.

The quality gate **still fails** — REAL precision, REAL recall, overall recall and 7 of 12
group recalls remain short — so L4.7C does not start.

## 2. What was wrong with the instrument

Three defects, all of them punishing correct behaviour:

1. **A sentinel no interpreter can emit.** All 8 `SYN-MIX` fixtures expected the FAQ topic
   `"mixed"`, which is not in the FAQ vocabulary. Every correctly named topic scored as both
   a miss and an invention.
2. **Fixtures that omitted their own evidence.** The same 8 fixtures — the group whose whole
   purpose is proving that a FAQ must not discard business evidence — did not expect the
   vehicle, year or locality written in their own sentences. Group I (corrections) did the
   same: it asserted "a correction happened" and dropped the corrected value.
3. **A stance flattened to a boolean.** `turn-evidence/1.1` carries six acceptance signals;
   the harness reduced them to `True`/`False`, so `FUTURE_INTENT` was indistinguishable from
   `REJECT` and a **false ACCEPT could not be counted at all** — the single most
   business-dangerous error this system can make had no metric.

## 3. Part 1 & 2 — the 8 SYN-MIX fixtures, repaired and adjudicated

Raw text byte-for-byte unchanged in all 8. Each now expects exactly what its own sentence
carries, and nothing else.

| Case | Raw | FAQ topics | Vehicle | Year | Location | service_intent |
|---|---|---|---|---|---|---|
| SYN-MIX-01 | "Hola, quiero revisar un Focus 2017. ¿Aceptan débito?" | `payment` | Ford Focus | 2017 | — | **CONFIRMED** |
| SYN-MIX-02 | "¿Entregan informe? Es una Taos 2020 y está en Quilmes" | `report` | Volkswagen Taos | 2020 | Quilmes (INSPECTION) | **PROPOSED** |
| SYN-MIX-03 | "¿Tengo que estar presente? El auto está en Palermo" | `presence` | — | — | Palermo (INSPECTION) | **PROPOSED** |
| SYN-MIX-04 | "¿Cuánto tarda la revisión? Quiero coordinar una para un Onix 2021" | `duration` | Chevrolet Onix | 2021 | — | **CONFIRMED** |
| SYN-MIX-05 | "¿Qué incluye el servicio? Estoy por comprar un usado en Avellaneda" | `service_scope` | — | — | Avellaneda (INSPECTION) | **PROPOSED** |
| SYN-MIX-06 | "Hola! ¿Trabajan los sábados? Quiero revisar un Corolla 2019" | `business_hours` | Toyota Corolla | 2019 | — | **CONFIRMED** |
| SYN-MIX-07 | "¿Se paga antes o después? Es un Gol Trend 2016 en San Justo" | `payment` | Volkswagen Gol Trend | 2016 | San Justo (INSPECTION) | **PROPOSED** |
| SYN-MIX-08 | "¿Hacen a domicilio? El auto está en Belgrano" | `service_scope` | — | — | Belgrano (INSPECTION) | **PROPOSED** |

### Owner adjudication of the five flagged cases

The owner rule as extended for this milestone: *asking about the inspection service while
supplying the vehicle and/or where it is* **does** establish service intent, even when the
customer is not yet quote- or scheduling-ready; a generic question with no inspection purpose
does not.

| Case | Owner-rule application | service_intent | readiness / stance | Reason |
|---|---|---|---|---|
| SYN-MIX-02 | asks about the deliverable **and** supplies car + locality | `PREPURCHASE_INSPECTION` (PROPOSED) | none stated | the car is offered *for* the service |
| SYN-MIX-03 | asks about presence **at the inspection** + where the car is | `PREPURCHASE_INSPECTION` (PROPOSED) | none stated | "do I have to be there" presupposes the service |
| SYN-MIX-05 | asks what the service includes + states the purchase and its place | `PREPURCHASE_INSPECTION` (PROPOSED) | none stated | purchase context + service question |
| SYN-MIX-07 | asks when payment happens + supplies car and locality | `PREPURCHASE_INSPECTION` (PROPOSED) | none stated | payment *for the service*, car offered |
| SYN-MIX-08 | asks whether the service is performed where the car is | `PREPURCHASE_INSPECTION` (PROPOSED) | none stated | "a domicilio" is about performing it |

`PROPOSED`, not `CONFIRMED`: the intent is implied by offering the vehicle, not stated in
words. **No OWNER_REVIEW_REQUIRED remains for these five.** `"¿Hacen a domicilio?"` is
labelled `service_scope`, not `coverage`, because the CE FAQ ontology groups
"salen a domicilio" / "van al lugar" with the service-scope questions
(`conversation_engine.py:395–403`) — the business's own ontology, not a guess.

## 4. Part 3 & 4 — one stance, one field

**Engagement ontology (decided and documented).** Conversational/commercial stance is
represented **once**, in `acceptance`, using the `turn-evidence/1.1` vocabulary:
`ACCEPT · REJECT · HESITATE · FUTURE_INTENT · QUESTION_ONLY · UNKNOWN`.

`readiness` keeps only what a stance cannot express: `SEARCHING_NOT_READY` — a **fact** the
customer states about their own purchase process ("I haven't chosen a car yet"). The other
two legacy readiness values were duplicate truth and are retired:

```
FUTURE_CONTACT_INTENDED  ->  acceptance = FUTURE_INTENT
HESITANT_OR_DEFERRED     ->  acceptance = HESITATE
```

The boundary: **the semantic object carries the customer's stance and stated facts; business
readiness (quote-ready, scheduling-ready, bookable) is derived deterministically downstream
and stays out of TurnEvidence.** A customer can be semantically interested in an inspection
and commercially not ready at all — those are two different questions, answered by two
different layers.

No schema change was needed: `turn-evidence/1.1` already carries all six signals, and the
interpreter was not touched. The harness **canonicalises** both spellings before scoring, so
an interpreter that still emits the legacy readiness value is scored on meaning, not wording.

**New metrics** (reported separately, never folded into precision/recall):

| Metric | Definition |
|---|---|
| stance exact accuracy | correct signal ÷ cases where a stance is expected |
| **false ACCEPT rate** | cases producing `ACCEPT` where the customer did not accept ÷ all cases |
| FUTURE_INTENT recall | FUTURE_INTENT correctly read ÷ FUTURE_INTENT expected |
| HESITATE recall | HESITATE correctly read ÷ HESITATE expected |

Business acceptance authority is unchanged and still deterministic downstream: nothing here
lets the interpreter create a booking, a quote or a lead state.

Also fixed: **FAQ topic sets are compared as sets**, not ordered lists. The order in which a
customer asks two questions carries no meaning.

## 5. Part 5 — corpus integrity audit (all 162 cases)

A new read-only module, `tests/semantic_corpus/integrity.py`, checks the corpus against
itself and against the schema. It found 7 defects; 4 were objective synthetic errors and were
repaired, 3 were detector false positives on catalog-canonical vehicle values and the
detector was corrected instead.

| Finding | Cases | Action |
|---|---|---|
| `RAW_EVIDENCE_OMITTED` — year stated, not expected | SYN-CORR-02 | repaired (`vehicle_year=2020`) |
| `RAW_EVIDENCE_OMITTED` — locality stated, not expected | SYN-CORR-03, SYN-CORR-04 | repaired (`inspection_location=Belgrano` / `Berazategui`, superseded locality forbidden) |
| `RAW_EVIDENCE_OMITTED` — locality inside noisy text | SYN-NOISE-07 | repaired: the fixture said "kieren revisar un auto **en quilmes**?" while its own contract forbade any location. It now expects `inspection_location=Quilmes` (PROPOSED). **This fixture was the source of the persistent unsupported-inference finding in every run since L4.7B** |
| `EXPECTATION_NOT_IN_RAW` — "Volkswagen Fox" | REAL-003, SYN-VEH-18, SYN-VEH-19 | **no corpus change** — the detector was wrong: labelling the catalog-canonical identity for a model-only mention is the corpus convention |

Group I was repaired in the same pass: `SYN-CORR-01…08` now expect the corrected **value**
(year, locality, day) and forbid the superseded one, instead of only asserting "a correction
happened". Final audit: **0 findings.**

REAL cases were audited, not auto-corrected. Three REAL labels changed and every one is a
mechanical ontology migration or an explicit owner instruction, never a new judgement:

| Case | Before | After | Basis |
|---|---|---|---|
| REAL-001 | `readiness=SEARCHING_NOT_READY` | + `acceptance=FUTURE_INTENT` | the owner's own L4.7B.2A worked example |
| REAL-002 | `readiness=FUTURE_CONTACT_INTENDED` | `acceptance=FUTURE_INTENT` | retired vocabulary → canonical field, same truth |
| REAL-003 | `readiness=FUTURE_CONTACT_INTENDED` | `acceptance=FUTURE_INTENT` | owner example: "engagement: FUTURE_INTENT" |
| WILD-A-03 | `acceptance=True` | `acceptance=ACCEPT` | boolean → signal, same truth |

**No REAL raw text changed anywhere.** Owner REAL-001…004 remain byte-verbatim (lengths 93,
62, 115, 200, asserted in test), and all 8 failed-Wild raw examples are untouched.

## 6. Part 6 — the interpreter did not move

| | SHA-256 |
|---|---|
| `backend/app/services/semantic_interpreter.py` **before** | `00cb78d15aa83885a56b1f17ba6ef9af593b8a10f45c5926dda66c9adce40f86` |
| `backend/app/services/semantic_interpreter.py` **after** | `00cb78d15aa83885a56b1f17ba6ef9af593b8a10f45c5926dda66c9adce40f86` |

Prompt version `understand/1.4` (the prompt is `_SYSTEM_PROMPT` inside that module — there is
no separate template file). Model `gpt-4o-mini`. `turn_evidence.py` and
`conversation_engine.py` also unchanged. FIXTURE-10 and FIXTURE-11 assert the hash and the
version in CI, so a future milestone cannot quietly edit the measured object.

## 7. Part 7 — the same interpreter, measured with the repaired ruler

162/162 calls OK.

| Metric | Value |
|---|---|
| field precision, overall | **0.8852** |
| field recall, overall | **0.7283** |
| field precision, REAL | **0.8000** |
| field recall, REAL | **0.6667** |
| unsupported inference, overall | **0.0062** (1 case) |
| unsupported inference, REAL | **0.0000** |
| role accuracy | **1.0000** (67/67) |
| ambiguity/conflict accuracy | **1.0000** (49/49) |
| missing-field accuracy | **1.0000** (61/61) |
| clean cases | **99** |
| counts | tp 185 · fp 24 · fn 69 |
| **stance exact accuracy** | **0.725** (40 expected) |
| **false ACCEPT rate** | **0.000** (0 of 162) |
| **FUTURE_INTENT recall** | **0.727** (8/11) |
| **HESITATE recall** | **0.167** (1/6) |

The false-ACCEPT rate of zero is the most important line on this page: across 162 cases the
interpreter never once claimed a customer had agreed when they had not.

### Groups A–L

| Group | Cases | P before | P after | R before | R after | Clean |
|---|---|---|---|---|---|---|
| A intent | 27 | 0.829 | 0.886 | 0.707 | 0.738 | 18 |
| B vehicle | 24 | 0.906 | 0.925 | 0.857 | 0.875 | 17 |
| C location role | 23 | 0.853 | 0.853 | 0.806 | 0.806 | 17 |
| D quote request | 10 | 0.909 | 0.909 | 0.625 | 0.625 | 5 |
| E acceptance | 21 | 1.000 | 1.000 | 0.818 | 0.818 | 17 |
| F rejection/hesitation | 10 | 1.000 | 0.833 | 0.300 | 0.500 | 5 |
| G scheduling | 13 | 0.538 | 0.615 | 0.538 | 0.615 | 8 |
| H ordered alternatives | 9 | 0.667 | 0.667 | 0.667 | 0.667 | 6 |
| **I corrections** | 8 | 0.333 | **0.909** | 0.286 | **0.526** | 1 |
| **J FAQ + business** | 11 | 0.250 | **0.857** | 0.360 | **0.750** | 1 |
| K noisy/ASR | 12 | 0.500 | 0.750 | 0.333 | 0.529 | 4 |
| L future/not ready | 11 | 0.300 | 0.909 | 0.200 | 0.455 | 2 |

## 8. Part 8 — what the repair actually explains

Three measurements, so the causes cannot be conflated:

| Measurement | P | R | Unsup | Clean | REAL P / R |
|---|---|---|---|---|---|
| **BEFORE** — L4.7B.2A labels, old harness | 0.7163 | 0.6314 | 0.0123 | 92 | 0.720 / 0.621 |
| **SAME OUTPUTS, repaired ruler** — the *identical* saved interpreter outputs rescored | 0.8309 | 0.6772 | 0.0123 | 94 | 0.720 / 0.600 |
| **AFTER** — live rerun of the unchanged interpreter | 0.8852 | 0.7283 | 0.0062 | 99 | 0.800 / 0.667 |

* **+0.115 precision and +0.046 recall are the corpus repair alone** — same bytes in, same
  bytes out, only the ruler changed.
* The remaining **+0.054 precision / +0.051 recall** in the live run comes from the harness
  now transporting the stance signal instead of a boolean (acceptance items can finally
  match) plus run-to-run variance, measured at ±0.005 overall and ±0.03 on the 12 REAL cases
  in L4.7B.2.
* **Nothing is attributable to model quality.** The interpreter's hash did not change.

Group-level: **J 0.250 → 0.857 precision** and **I 0.333 → 0.909** are almost entirely
instrument. **F recall 0.300 → 0.500** and **L 0.200 → 0.455** are the stance ontology being
representable at last. **C, D, E, H did not move at all** — those are genuinely the
interpreter's remaining ground.

## 9. Part 9 — quality gate, thresholds unchanged

| Gate line | Threshold | Measured | |
|---|---|---|---|
| unsupported inference, REAL | 0.000 | 0.000 | ✅ |
| unsupported inference, overall | ≤ 0.01 | 0.0062 | ✅ |
| role accuracy | 1.000 | 1.000 | ✅ |
| ambiguity/conflict handling | ≥ 0.98 | 1.000 | ✅ |
| field precision, overall | ≥ 0.80 | 0.885 | ✅ |
| group I precision | ≥ 0.70 | 0.909 | ✅ |
| field precision, REAL | ≥ 0.85 | 0.800 | ❌ |
| field recall, REAL | ≥ 0.85 | 0.667 | ❌ |
| field recall, overall | ≥ 0.85 | 0.728 | ❌ |
| every group recall | ≥ 0.70 | 7 of 12 below (D 0.625, F 0.500, G 0.615, H 0.667, I 0.526, K 0.529, L 0.455) | ❌ |

Six of ten lines now pass, up from four. **GATE: FAIL.** No threshold was lowered. L4.7C does
not start.

### Remaining genuine interpreter-side error classes

| Error class | Cases | REAL | Groups | Fix surface |
|---|---|---|---|---|
| `service_intent` not emitted though the wording names the service or the car is offered for it | 25 | 4 | J8 A7 K6 | PROMPT |
| stance misread (mostly HESITATE read as something else) | 11 | 1 | F5 L3 E3 | PROMPT |
| `acceptance` not emitted at all where a stance is expected | 10 | 1 | F4 L3 E3 | PROMPT |
| scheduling value wrong (relative day, branch order) | 7 | 1 | G5 H3 | PROMPT + CONTEXT |
| `inspection_location` missed / `customer_origin` invented — role over-assignment | 6 + 6 | 0 | C5 J1 | PROMPT |
| `correction` not emitted | 6 | 0 | I6 | PROMPT |
| `readiness` (`SEARCHING_NOT_READY`) not emitted | 6 | 0 | L6 | PROMPT |
| `quote_request` emitted where not expected | 4 | 2 | J3 A2 | PROMPT |
| FAQ topic set incomplete | 3 | 1 | D2 E1 | PROMPT |
| vehicle value mismatch (make inferred or not) | 2 | 2 | A2 B2 | RECONCILER (catalog is deterministic by design) |

MODEL appears in no class. **MODEL CHANGE: still NO.**

## 10. Part 11 — tests

`tests/test_l4_7b_2b_corpus_instrument.py` — **19/19 PASS**, covering FIXTURE-01…12 plus
legacy-spelling canonicalisation, explicit-acceptance precedence, the Wild B invariant, and
order-free FAQ comparison. No network, no model, no database.

Full regression: **3 347 passed / 60 failed / 9 errors** — byte-identical failure set to the
baseline at HEAD, **zero new failures**, verified by diffing the two failure lists.

Two instrument bugs were found by that regression and fixed inside this milestone: the
corpus→TurnEvidence round trip did not understand the new stance strings (every acceptance
case round-tripped to `UNKNOWN`), and a year stated without a vehicle ("Es del 2015 no del
2014") was dropped entirely. Both are harness-side; neither touches the interpreter.

## 11. Safety

No runtime semantic behaviour change. No ConversationEngine change. No model or prompt
change. No schema change. No authority migration. No Wild. OUTBOUND OFF (verified on the
running crm_test backend). Production DB untouched — this milestone made no database contact
at all. L1/L2/L3 FROZEN. Wild clean count 0/3.

## 12. What comes next

**L4.7B.3-SHADOW-SEMANTIC-QUALITY** — now that the ruler is straight, the remaining distance
is genuinely the interpreter's, and it is concentrated in four questions: intent when the
service is named inside a question, stance granularity (HESITATE in particular), relative-day
resolution, and location-role assignment. Do not start L4.7C first, and do not start L4.7B.3
before this closeout is reviewed.
