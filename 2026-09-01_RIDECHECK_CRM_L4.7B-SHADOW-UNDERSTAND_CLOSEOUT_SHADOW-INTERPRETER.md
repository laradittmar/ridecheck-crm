PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7B-SHADOW-UNDERSTAND

# L4.7B — Shadow semantic UNDERSTAND pass

Date: 2026-09-01
Shadow only · ConversationEngine remains authoritative · OUTBOUND OFF · crm_test only ·
production DB untouched · no authority moved.

---

## 1. What runs now

```
RAW BURST ASSEMBLED
   ├─ SHADOW UNDERSTAND  →  TurnEvidence  →  append-only record   (new, changes nothing)
   └─ existing production CE path                                  (unchanged)
```

| Component | Path |
|---|---|
| Interpreter | `backend/app/services/semantic_interpreter.py` — `SemanticTurnInterpreter` |
| Recorder | `backend/app/services/shadow_recorder.py` |
| Hook | `ConversationEngine._run_shadow_understand()`, called immediately after burst assembly |
| Schema | `turn-evidence/1.0` · prompt `understand/1.0` · model `gpt-4o-mini`, temperature 0, JSON structured output |
| Flag | `SHADOW_UNDERSTAND_ENABLED` (default **false**; **true only in crm_test**) |

**Position.** The hook sits above Layer A and every one of the 19 early returns — asserted
in test by source position against the motorcycle gate, the FAQ bypass, WILD-02-B,
WILD-04-F1 and the SCHEDULING branch. The interpreter therefore observes turns that the
current CE exits through FAQ bypass, phone-call, clarification, fuzzy confirmation or
scheduling: exactly the turns whose evidence Wild B lost.

**Isolation.** `interpret()` never raises — a disabled flag, missing key, HTTP error,
malformed JSON or unwritable log all degrade to "do nothing". The hook wraps everything in
a catch-all. The flag is compared with `is True`, so a `MagicMock` settings object in a test
can never trigger a model call (this caught 17 suites during development).

**No authority.** The interpreter imports no ORM, no PricingService, no ScheduleService, no
OutboundSafetyGate and no ConversationEngine (AST-asserted). In a live-runtime probe the
hook ran with `db=None` and produced a record; `whatsapp_messages` stayed at 6 rows before
and after.

## 2. Prompt contract

The system prompt states the three layers explicitly and forbids the model from deciding
price, availability, bookings, lead state or candidate persistence. It requires:
don't invent what wasn't said; keep ambiguity (`AMBIGUOUS` + alternatives) and contradiction
(`conflicts`, both sides); separate `INSPECTION_LOCATION` / `CUSTOMER_ORIGIN` /
`SELLER_LOCATION` regardless of mention order; keep ordered scheduling branches with the
time bound to its own branch; distinguish ACCEPT / HESITATE / REJECT / QUESTION_ONLY; keep
multiple vehicle mentions and corrections; let FAQ and business evidence coexist; and stay
conservative (`PROPOSED`/`AMBIGUOUS`, never `CONFIRMED`) on noisy or transcribed text.

Controlled vocabularies (intent kinds, roles, FAQ topics, day expressions, priorities) are
part of the **schema**, not phrase rules — no customer phrasing appears in the prompt.

## 3. Provenance and storage

Each record carries: thread and burst reference, ordered message ids, reconstruction method
(`LIVE_DEBOUNCE`), interpreter and model version, schema version, latency, tokens,
success/failure and the full `TurnEvidence` JSON, with `shadow: true`. Source spans are left
empty rather than fabricated. **No raw message text is stored** (a test asserts it) and no
migration was required — the log is append-only JSONL at
`/run/forensics/shadow_turn_evidence.jsonl` (host `/opt/ridecheck-crm-forensics/`), mirrored
by a `CE_SHADOW_UNDERSTAND` log line.

## 4. Corpus evaluation — all 162 cases, 162/162 calls OK

| Metric | Overall | REAL (12) | SYNTHETIC (150) |
|---|---|---|---|
| field precision | **0.428** | 0.486 | 0.422 |
| field recall | **0.669** | 0.567 | 0.684 |
| role accuracy | **1.000** (55/55) | 1.000 | 1.000 |
| unsupported-inference rate | **0.056** (9 cases) | 0.083 | 0.053 |
| ambiguity/conflict handling | **1.000** (49/49) | 1.000 | 1.000 |
| missing-field accuracy | **1.000** (55/55) | — | — |
| fully clean cases | 22 | 2 | 20 |

By equivalence group (precision / recall / unsupported):

| Group | Cases | P | R | Unsup |
|---|---|---|---|---|
| A intent | 27 | 0.517 | 0.738 | 0.000 |
| B vehicle | 24 | 0.438 | 0.792 | 0.000 |
| C location role | 23 | 0.455 | 0.833 | 0.000 |
| D quote request | 10 | 0.625 | 0.357 | 0.100 |
| E acceptance | 21 | 0.571 | 0.909 | 0.286 |
| F rejection/hesitation | 10 | 1.000 | 0.450 | 0.000 |
| G scheduling | 13 | 0.320 | 0.615 | 0.000 |
| H ordered alternatives | 9 | 0.278 | 0.556 | 0.000 |
| **I corrections** | 8 | **0.077** | **0.143** | 0.125 |
| J FAQ + business | 11 | 0.286 | 0.480 | 0.000 |
| K noisy/ASR | 12 | 0.462 | 0.750 | 0.083 |
| L future/not ready | 11 | 0.500 | 0.348 | 0.091 |

**No tuning was performed.** These are first-run numbers for `understand/1.0`; the analysis
belongs to L4.7B.1.

### Dominant disagreement (for L4.7B.1)

`SHADOW-DISAGREE-01` — the model frequently echoes the JSON *template* by returning an empty
scheduling branch `[{"day": null, "time": null, "rank": 1}]`. The mapper faithfully converts
it into a scheduling item, which produces most false positives and **all nine** unsupported
inferences (group E's 0.286 is entirely this). It is a schema/prompt artifact, not a
hallucination about the customer — and it was deliberately **not** fixed here, because this
milestone measures rather than tunes.

## 5. Owner real examples (4/4 evaluated, raw text unchanged)

| Case | Shadow proposed | Match | Notes |
|---|---|---|---|
| **REAL-001** "…estoy buscando un auto…" | `readiness=SEARCHING_NOT_READY` (CONFIRMED) | tp 1 / fp 0 / fn 1 | missed `service_intent`; invented nothing; ambiguity honoured |
| **REAL-002** noisy ASR-like | `service_intent=PREPURCHASE_INSPECTION`, vehicle null/PROPOSED, year AMBIGUOUS | tp 1 / fp 1 / fn 1 | correctly refused to resolve a vehicle; missed `readiness`; emitted the empty scheduling template |
| **REAL-003** "Quiero comprar un fox…" | `vehicle="Volkswagen Fox"` (CONFIRMED), year AMBIGUOUS | tp 2 / fp 2 / fn 1 | **make inferred correctly from a model-only mention**; read "en breves" as a location (`customer_origin="breves"`, PROPOSED/UNKNOWN role — not applied as inspection location) and over-read acceptance |
| **REAL-004** "…cotización … a La Plata…" | `quote_request=true`, `inspection_location="La Plata"` (CONFIRMED/INSPECTION_LOCATION) | tp 2 / fp 0 / fn 2 | missed `service_intent` and the logistics offer; **zero unsupported inferences** — no vehicle and no price invented |

## 6. Failed Wild cases (8/8 evaluated)

| Case | Result |
|---|---|
| **WILD-B-02** "Está en Berazategui, pero yo soy de Tigre." | **perfect** — Berazategui INSPECTION_LOCATION, Tigre CUSTOMER_ORIGIN, tp 2 / fp 0 / fn 0. The exact class that broke Wild B. |
| **WILD-B-01** "…para revisar un 2008 del 2014…" | vehicle **Peugeot 2008 CONFIRMED** (the evidence Wild B discarded) + FAQ topics; **year lost** (expected 2014) and one FAQ topic missed |
| **WILD-A-04** "Mñ 15hs? O nose jueves que tenes" | both branches kept in order with the time bound to the first branch — but "mñ" resolved to **MONDAY** instead of TOMORROW (the interpreter has no current-date context) |
| WILD-A-01 | vehicle Peugeot 2008 CONFIRMED, FAQ topics; year lost |
| WILD-A-02 | Berazategui/Tigre roles correct |
| WILD-A-03 | acceptance captured; emitted the empty scheduling template (its single unsupported inference) |
| WILD-01-01 | vehicle read as bare `"2008"` (make not inferred here), year lost |
| WILD-01-02 | payment FAQ captured, clean |

## 7. Current CE vs shadow (non-mutating comparison)

| Category | Cases |
|---|---|
| **shadow correct / current CE wrong** | WILD-B-02 (CE dropped the location entirely), WILD-B-01 (CE persisted no candidate; shadow resolved the vehicle) |
| **both structurally wrong, differently** | WILD-A-04 — CE collapsed to a single Thursday 15:00 request; shadow preserved both branches but mis-resolved the relative day |
| **current CE correct / shadow weaker** | WILD-A-01, WILD-A-02 (CE produced candidate + zone + quote live; shadow lost the year) |
| **both correct** | WILD-01-02 |
| **ambiguous / owner review** | REAL-002, REAL-004 (already flagged `owner_review_required`) |

No winner is declared. Truth stays with the corpus labels, and the numbers above are the
input to L4.7B.1, not a promotion argument.

## 8. Cost and latency (measured, not optimised)

| Metric | Value |
|---|---|
| mean latency | **2 390 ms** |
| p95 latency | **3 722 ms** (max 5 471 ms) |
| mean tokens per burst | **1 256** (203 468 total across 162 cases) |
| estimated cost | ≈ **$0.00028 per burst** → ~$0.0011 per 4-burst conversation → **≈ $0.11 per 100 conversations** (gpt-4o-mini list pricing) |

The added latency lands on every crm_test turn while shadow is on. That is acceptable now
(OUTBOUND OFF, no Wild running) but is an explicit decision point before any Wild with
shadow enabled — running it asynchronously is the obvious mitigation and belongs to a later
milestone.

## 9. Tests and regression

`tests/test_l4_7b_shadow_understand.py` — **29/29 PASS** (SHADOW-01…15) with a stub
transport: no network, no cost. Covers one-call-per-burst, source position ahead of every
gate, failure isolation, schema validation and round-trip, no canonical/DB mutation, no
outbound effect, location roles, ordered scheduling, ambiguity, conflict, FAQ coexistence,
owner and Wild case evaluability, and append-only auditable recording.

Full regression: **3 270 passed / 55 failed / 9 errors** — the same pre-existing failure
set, **zero new failures**.

## 10. Runtime

Image `ridecheck-crm-backend:l4.7b-shadow-a7ddddb` (deployment `a7ddddbb9028`) deployed to
**crm_test only**, `OUTBOUND_ENABLED=false`, `SHADOW_UNDERSTAND_ENABLED=true`. Source/runtime
parity verified for `semantic_interpreter.py`, `shadow_recorder.py`,
`conversation_engine.py` and `turn_evidence.py`. A live runtime probe produced records with
`shadow: true` and correct location roles; `crm_test.whatsapp_messages` stayed at 6 rows.

## 11. Status

Authority did **not** move: TurnEvidence feeds nothing, no deterministic parser was removed,
no business decision was reordered, no customer-visible text changed.

Next: **L4.7B.1-SHADOW-DISAGREEMENT-ANALYSIS**. Do not proceed to L4.7C before that review.
L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.
