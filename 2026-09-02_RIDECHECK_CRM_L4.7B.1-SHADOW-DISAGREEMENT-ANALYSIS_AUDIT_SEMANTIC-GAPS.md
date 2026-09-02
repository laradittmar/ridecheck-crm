PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7B.1-SHADOW-DISAGREEMENT-ANALYSIS

# L4.7B.1 — Shadow disagreement analysis

Date: 2026-09-02
Analysis only · no code changed · no authority moved · no Wild · OUTBOUND OFF ·
crm_test only · production DB untouched.

---

## 0. Method

Two evidence producers were scored against the same human-authored corpus truth with the
same L4.7E harness:

* **SHADOW** — the L4.7B `SemanticTurnInterpreter` output recorded for all 162 cases.
* **CE (deterministic)** — a faithful, read-only re-execution of today's deterministic
  extractors (`lookup_vehicle` → `extract_model_del_year` → `_contextual_numeric_model_lookup`,
  `_extract_vehicle_location_zones` + `_strip_customer_origin_clauses`, `_extract_year_from_text`,
  `_parse_scheduling_requests`, `_is_acceptance`, intent detectors) with a seeded viáticos
  zone database, so location resolution is judged fairly.

Neither is treated as truth. The corpus label is truth.

**Stated limitation:** the deterministic layer has *no representation at all* for several
evidence classes (quote request, rejection/hesitation, readiness, FAQ topics, corrections,
customer origin as a stored field). Counting those as "CE wrong" is fair for *evidence
coverage* but says nothing about CE's runtime behaviour, which never needed them. Both
readings are reported.

---

## 1. Case-by-case classification (162/162)

| Label | As measured | Projected after the artifact fix (§3) |
|---|---|---|
| BOTH_CORRECT | 6 | 18 |
| SHADOW_CORRECT_CE_WRONG | 16 | 26 |
| CE_CORRECT_SHADOW_WRONG | 38 | 26 |
| BOTH_WRONG | 100 | 90 |
| OWNER_REVIEW_REQUIRED | 2 | 2 |

"Correct" is a strict, all-or-nothing bar: one missing or extra field marks the whole case
wrong. That is why BOTH_WRONG dominates — it is a coverage measure, not a quality verdict.
The per-field picture is the informative one:

| Producer | Precision | Recall | Role acc. | Unsupported | Clean cases |
|---|---|---|---|---|---|
| CE deterministic (fair baseline) | **0.679** | 0.477 | 1.000 | 0.012 | 44 |
| CE on REAL only | **0.933** | 0.467 | 1.000 | **0.000** | — |
| SHADOW as measured | 0.428 | **0.669** | 1.000 | 0.056 | 22 |
| SHADOW with artifact removed | 0.548 | **0.669** | 1.000 | **0.012** | 44 |
| **Union CE + SHADOW** (artifact removed) | 0.567 | **0.707** | 1.000 | 0.012 | 44 |

**The headline finding is complementarity, not superiority.** CE is precise and blind
(recall 0.48); shadow sees far more and is noisier. Their union beats either alone on
recall while keeping role accuracy at 1.000 — which is exactly the asymmetric architecture
L4.7C is meant to build: semantic proposes breadth, deterministic validates precision.

Representative disagreements (full per-case data in `ab_rows_fair.json`, kept with the
analysis scripts):

| Case | Kind | Group | Raw (abbrev.) | Expected | Shadow | CE | Label | Error class |
|---|---|---|---|---|---|---|---|---|
| WILD-B-02 | REAL | C | "Está en Berazategui, pero yo soy de Tigre." | Berazategui=INSPECTION, Tigre=ORIGIN | both roles correct | Berazategui correct; origin not represented | SHADOW_CORRECT_CE_WRONG (coverage) | — |
| WILD-B-01 | REAL | A,B,J | "…para revisar un 2008 del 2014…" | Peugeot 2008 / 2014 | vehicle ✓, **year lost** | vehicle ✓, year ✓ | CE_CORRECT_SHADOW_WRONG | G year contract |
| WILD-A-04 | REAL | G,H,K | "Mñ 15hs? O nose jueves que tenes" | TOMORROW 15:00 → THURSDAY open | order ✓, **day = MONDAY** | order ✓, day ✓ | CE_CORRECT_SHADOW_WRONG | C missing date context |
| WILD-01-01 | REAL | A,B | "…un 2008 del 2015…" | Peugeot 2008 / 2015 | value `"2008"` (make not inferred), year lost | vehicle ✓, year ✓ | CE_CORRECT_SHADOW_WRONG | F catalog inference |
| REAL-003 | REAL | A,B,K,L | "Quiero comprar un fox…" | VW Fox PROPOSED, no acceptance | **make inferred**, plus `customer_origin="breves"` and `acceptance=true` | vehicle ✓ | OWNER_REVIEW / overreach | E + F |
| REAL-004 | REAL | A,C,D | "…cotización … a La Plata…" | quote_request, La Plata | both ✓, nothing invented | location ✓ only | OWNER_REVIEW | B intent scope |
| SYN-LOC-02 | SYN | C | "Está en Berazategui" | inspection location | emitted as `customer_origin` | correct | CE_CORRECT_SHADOW_WRONG | E role overreach |
| SYN-ACC-02…12 | SYN | E | "Sí, avancemos" etc. | acceptance only | acceptance ✓ **plus empty scheduling item** | acceptance ✓ | CE_CORRECT_SHADOW_WRONG | A artifact |

---

## 2. Root-cause taxonomy (all shadow error notes, n = 266)

| Class | Count | What it is |
|---|---|---|
| **A prompt/schema artifact** | **84** | the empty scheduling placeholder echoed from the JSON template (§3) |
| **B intent emission scope** | 82 | `service_intent` emitted where the corpus expects none, or omitted where it expects one — the prompt never states *when* intent should be emitted |
| D under-emission (other) | 20 | a field the corpus expects was simply not produced |
| G readiness under-emission | 18 | `readiness` (SEARCHING_NOT_READY etc.) rarely emitted |
| I normalization / topic vocabulary | 13 | FAQ topic sets differ by one or two items ("presence" missed) |
| G vehicle-year contract | 10 | year dropped although stated (§5) |
| F catalog inference | 9 | make inferred, or model returned bare (§6) |
| E model overreach — other | 9 | mostly location invented on correction turns |
| E model overreach — location | 6 | a non-location token read as a place ("breves") |
| E model overreach — acceptance | 5 | future intent read as ACCEPT (§7) |
| C missing date context | 5 | relative days resolved without knowing "today" (§4) |
| J other | 5 | day-expression vocabulary drift on single-branch cases |

Classes A + B alone account for **62 %** of all error notes, and neither is a model-quality
problem: both are contract gaps in the prompt/schema.

---

## 3. SHADOW-DISAGREE-01 — the empty scheduling artifact

**Exact cause.** The system prompt shows the expected JSON with a *populated* example row
inside every array, including `scheduling_requests`. The model treats the shape as a
template and returns `[{"day": null, "time": null, "rank": 1}]` when no scheduling was
mentioned; the mapper then faithfully converts it into a `SchedulingRequestEvidence`, and
the harness counts a scheduling claim that the customer never made. Nine of the nine
unsupported inferences come from this, all on turns whose `must_not_infer` forbids a
scheduling preference.

**General fix (three layers, no phrase logic):**

1. **PROMPT** — show empty arrays in the response template and state that arrays are
   omitted or empty unless the customer actually expressed that evidence class.
2. **MAPPER** — drop semantically empty items before constructing `TurnEvidence`: an item
   whose every meaningful field is null carries no evidence. This is a general sanitation
   rule (applies to vehicles, locations, scheduling alike), not a scheduling special case.
3. **SCHEMA** — optional: a validator that rejects an evidence item with no non-null
   payload, so the artifact can never reappear from a future interpreter.

**Measured impact (simulated by removing empty items from the recorded output):**

| Metric | As measured | After fix | Δ |
|---|---|---|---|
| field precision | 0.428 | **0.548** | +28 % |
| unsupported-inference rate | 0.056 | **0.012** | −79 % |
| clean cases | 22 | **44** | ×2 |
| field recall | 0.669 | 0.669 | unchanged |

---

## 4. Temporal context

`"Mñ 15hs? O nose jueves que tenes"` was interpreted as **MONDAY** 15:00 + THURSDAY. The
interpreter has no idea what day it is; "mñ" was mapped to a weekday by guesswork. Two of
21 scheduling cases mismatch, both this way (WILD-A-04, SYN-ORDER-01).

**Recommended contract — the LLM never does calendar arithmetic:**

```
CONTEXT GIVEN TO THE INTERPRETER          SEMANTIC OUTPUT              DETERMINISTIC OWNS
current_local_date  2026-09-01            relative_day = TOMORROW      resolved_date = 2026-09-02
current_weekday     TUESDAY               (or THURSDAY, THIS_AFTERNOON) weekday arithmetic
timezone            America/Argentina/…   time = "15:00"               business calendar
                                          time_period = AFTERNOON      availability
```

The interpreter receives date, weekday and timezone **only so that relative expressions can
be named correctly** (TODAY / TOMORROW / DAY_AFTER_TOMORROW / <WEEKDAY>). It must not emit
`resolved_date`; `SchedulingRequestEvidence.resolved_date` stays reserved for the
deterministic reconciler, which already owns date arithmetic (L4.3) and availability
(ScheduleService). Business-calendar context is deliberately **not** given to the model.

---

## 5. Vehicle year loss

| Producer | Cases expecting a year | Correct |
|---|---|---|
| CE deterministic | 22 | **22/22** |
| Shadow | 22 | 15/22 |

Misses: WILD-A-01, WILD-B-01, WILD-01-01, SYN-VEH-04/06/12/14 — i.e. **exactly the
"<model> del <year>" and "una <model> del <year>" shapes**, never the "Make Model Year"
shape. Cause is not truncation (responses were well under the token cap), not the schema
(`VehicleEvidence.year` exists and round-trips), and not the mapper (it copies an int when
present). It is **prompt/model reasoning**: with two numbers in one phrase the model treats
one as the model name and drops the other rather than assigning both roles.

**General rule to state in the prompt (not a 2008 rule):** when a vehicle mention contains
two number-like tokens, one may be the model name and the other the manufacturing year;
emit both fields, and if the assignment is not decidable emit `status=AMBIGUOUS` with both
readings as alternatives. Deterministic catalog reconciliation then decides. `CE` already
solves this case, so the reconciler can also simply prefer the deterministic year — which is
the asymmetric-authority answer.

---

## 6. Catalog inference authority

REAL-003 ("Quiero comprar un fox…") produced `vehicle = "Volkswagen Fox"` with make
inferred and status CONFIRMED. WILD-01-01 did the opposite, returning a bare `"2008"`.
Both behaviours are wrong for the same reason: the layer boundary is unstated.

**Recommended boundary — three field classes:**

| Class | Owner | Example | Status ceiling |
|---|---|---|---|
| **raw-mentioned** | interpreter | `model = "fox"`, `year = 2014` as spoken | may be CONFIRMED |
| **semantic-suggested** | interpreter | `catalog_candidate = "Volkswagen Fox"` | **never above PROPOSED** |
| **catalog-confirmed** | deterministic catalog | `make = Volkswagen`, `tipo_vehiculo = AUTO` | CONFIRMED only after catalog validation |

So the interpreter should have returned `model="Fox"`, `make=null`,
`catalog_candidate="Volkswagen Fox"`, `status=PROPOSED`. The schema already carries
`catalog_candidate` and `alternatives`; only the prompt and the mapper's status ceiling need
to enforce it. This preserves the existing WILD-04R-F6 rule that the catalog — never the
model — owns `tipo_vehiculo`.

---

## 7. Acceptance over-read

REAL-003 ends "…en breves te voy a estar hablando si todo marcha bien, muchas gracias".
The interpreter returned `acceptance = ACCEPT (PROPOSED)`. Group E recall is high (0.91) but
group L (future/not-ready) recall is only 0.35, and five notes are acceptance overreach:
courtesy and future intent are being read as agreement.

**Cause:** the vocabulary has no value for "will act later". `AcceptanceSignal` offers
ACCEPT / REJECT / HESITATE / QUESTION_ONLY / UNKNOWN; the closest honest answer for
"te aviso cuando decida" is none of them, so the model reaches for ACCEPT.

**Recommendation:** add `FUTURE_INTENT` to `AcceptanceSignal` — an additive, backward
compatible **minor** schema bump (`turn-evidence/1.1`) under the L4.7A versioning rules; no
existing value changes meaning. The prompt then states: agreement to *this* proposal =
ACCEPT; intention to return later = FUTURE_INTENT; doubt = HESITATE; a question alone =
QUESTION_ONLY. Acceptance must never be inferred from politeness or gratitude.

---

## 8. Bounded semantic context

Today the interpreter sees only the current burst. That is right for isolation but too thin
for references and corrections (group I: P 0.10 / R 0.14 — the weakest group by far, because
"no, es un Kuga" is meaningless without knowing what was said before).

**Proposed bounded context contract** — small, current-cycle only, never raw history dumps:

| Field | Why | Bound |
|---|---|---|
| `current_local_date`, `weekday`, `timezone` | relative day naming (§4) | 3 scalars |
| `previous_customer_turn` | resolve "no, es…", "ese", "el otro" | 1 turn, current cycle only |
| `active_candidate_summary` | make/model/year of the focus candidate | 1 line, no history |
| `stage` | QUALIFYING / QUOTED / SCHEDULING | 1 token |
| `pending_clarification` | what the bot last asked | 1 line |
| `offered_slots` | so "el de las 13" resolves | ≤ 12 strings |

Explicitly **excluded**: prior-cycle candidates, past quotes, past bookings, full message
history. The L1 rule stands — stale history must never contaminate current-turn evidence —
and everything supplied is labelled as context, never as customer statement.

---

## 9. Confidence policy

The model was never asked for numeric confidence and none was recorded; `confidence` is
`None` on every item. That is the correct state today: an uncalibrated numeric score would
invite exactly the "highest confidence wins" conflict resolution that Part 11A forbids.

**Recommendation:** keep `confidence` in the schema as **advisory metadata only, allowed to
stay null**, and let the four-value `status` carry certainty. Reconciliation stays rule
based: no threshold on a model-produced number may ever grant business authority. Revisit
only if a future model provides calibrated log-probabilities, and even then it may inform
clarification ordering, never mutation.

---

## 10. Which layer to change

| Error class | Layer to change |
|---|---|
| A empty-item artifact (84) | **PROMPT + MAPPER** (schema validator optional) |
| B intent emission scope (82) | **PROMPT** (state when to emit) + **CORPUS LABEL** review for intent-on-location-only turns |
| C missing date context (5) | **CONTEXT** |
| D/G under-emission incl. readiness (38) | **PROMPT** |
| E overreach — acceptance (5) | **SCHEMA** (`FUTURE_INTENT`, minor bump) + **PROMPT** |
| E overreach — location/other (15) | **PROMPT** (a token is a place only if the customer names a place) |
| F catalog inference (9) | **PROMPT** (status ceiling) + **DETERMINISTIC RECONCILER** (catalog owns make/type) |
| G vehicle-year contract (10) | **PROMPT**; reconciler may prefer the deterministic year |
| I FAQ topic vocabulary (13) | **PROMPT** vocabulary + **CORPUS LABEL** alignment |
| J day-expression drift (5) | **PROMPT** vocabulary |

**MODEL CHANGE: NO.** Every dominant class is a prompt, schema, context, mapper or corpus
issue. Swapping the model now would hide contract defects rather than fix them.

---

## 11. Quality gate before L4.7C

TurnEvidence may feed canonical reconciliation only when **all** of the following hold on a
rerun of the full corpus. Thresholds are set for production consequence, not for what today
can pass — today passes only two of them.

| Metric | Threshold | Today (artifact-fixed) |
|---|---|---|
| unsupported-inference rate, REAL | **0.000** | 0.083 → projected ~0.00 |
| unsupported-inference rate, overall | **≤ 0.01** | 0.012 |
| role accuracy | **1.000** | **1.000 ✅** |
| ambiguity/conflict handling | **≥ 0.98** | **1.000 ✅** |
| field precision, REAL | **≥ 0.85** | 0.486 |
| field recall, REAL | **≥ 0.85** | 0.567 |
| field precision, overall | **≥ 0.80** | 0.548 |
| field recall, overall | **≥ 0.85** | 0.669 |
| every group A–L, recall | **≥ 0.70** | 5 of 12 below |
| group I (corrections), precision | **≥ 0.70** | 0.10 |

Case-level requirements, all mandatory:

* the 4 owner examples: **zero unsupported inferences**, and REAL-001/002 keep vehicle and
  location unresolved;
* the 8 Wild cases: WILD-B-01 (vehicle **and** year), WILD-B-02 (both roles) and WILD-A-04
  (both branches, correct relative day) fully clean;
* critical groups B (vehicle), C (location role), H (ordered scheduling) with **zero**
  unsupported inferences.

Falling short on any line means L4.7C does not start.

---

## 12. Async shadow

Shadow currently blocks the turn for a mean of 2 390 ms (p95 3 722 ms). No customer should
wait for an evaluation that changes nothing.

**Recommended architecture — enqueue, then interpret after the reply:**

1. At burst assembly, capture a small immutable job: ordered message texts, message ids,
   thread and burst reference, reconstruction method, deployment and correlation id.
2. Hand it to a bounded in-process worker (a single background thread with a small queue and
   a hard drop-on-full policy) **after** the CE turn has produced its response — the payload
   is captured synchronously so provenance can never be lost, only the model call is
   deferred.
3. The worker performs the one model call and appends the same shadow record. Failures and
   drops are logged with a reason; a full queue degrades to "not recorded", never to a
   delayed customer.
4. No new infrastructure (no Redis, no Celery) for shadow scale: one worker, queue depth in
   the tens, is enough for closed-beta traffic.

When semantic interpretation later becomes authoritative, latency must be re-assessed as a
turn-latency budget — that is a separate decision, not this one.

---

## 13. Recommended remediation — L4.7B.2-SHADOW-INTERPRETER-QUALITY

Scope, in dependency order (implementation is **not** part of this audit):

1. remove the empty-item artifact (prompt template + mapper sanitation) — biggest single win;
2. supply bounded temporal context (date, weekday, timezone) and forbid `resolved_date`;
3. state the vehicle number-pair rule and the catalog status ceiling
   (`catalog_candidate` ≤ PROPOSED);
4. add `FUTURE_INTENT` to `AcceptanceSignal` (`turn-evidence/1.1`, additive) and define the
   acceptance vocabulary in the prompt;
5. state intent-emission scope and the FAQ topic vocabulary; review the corpus labels for
   intent-on-location-only turns (class B is partly a labelling question);
6. add the bounded context contract of §8 (previous turn, active candidate, stage, pending
   clarification, offered slots);
7. move shadow execution off the request path (§12);
8. rerun the full corpus and publish the same metric table.

Only if the §11 gate passes does **L4.7C** authority migration begin.

---

## 14. Verdict

The shadow interpreter is behaving the way a first-generation contract should: perfect on
the two properties that protect customers (role accuracy 1.000, ambiguity/conflict handling
1.000), broad on recall, and noisy in ways that trace to the contract rather than to the
model. It already solves the class that broke Wild B, and the deterministic layer already
solves the classes shadow is weakest at. Neither replaces the other yet — and nothing in
this audit moves authority.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3** · OUTBOUND OFF · no code changed.
