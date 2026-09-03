PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7B.3-SHADOW-SEMANTIC-QUALITY

# L4.7B.3 — the interpreter pass against a trustworthy ruler

Date: 2026-09-03
Shadow only · zero canonical authority · no CE behaviour change · model unchanged ·
OUTBOUND OFF · crm_test only · production DB untouched · no Wild.

---

## 1. Verdict

**CONDITIONAL_PASS. Nine of the ten quality-gate lines now pass; one does not.**

| Gate line | Threshold | Measured | |
|---|---|---|---|
| unsupported inference, REAL | 0.000 | **0.000** | ✅ |
| unsupported inference, overall | ≤ 0.01 | **0.000** | ✅ |
| role accuracy | 1.000 | **1.000** | ✅ |
| ambiguity/conflict handling | ≥ 0.98 | **1.000** | ✅ |
| field precision, REAL | ≥ 0.85 | **0.871** | ✅ |
| field recall, REAL | ≥ 0.85 | **0.900** | ✅ |
| field precision, overall | ≥ 0.80 | **0.938** | ✅ |
| field recall, overall | ≥ 0.85 | **0.890** | ✅ |
| group I precision | ≥ 0.70 | **0.857** | ✅ |
| **every group A–L recall** | **≥ 0.70** | **I 0.632 · L 0.591** | ❌ |

The single failing line is the per-group recall floor, in two groups, and both failures are
**one behavioural class**: the interpreter emits the value but omits its companion item — the
`corrections[]` entry beside a corrected value (group I), and the `SEARCHING_NOT_READY` fact
beside a stance (group L). Everything else the gate asks for is met.

**L4.7C does not start.**

## 2. Where the interpreter began and ended

Same corpus, same instrument, same model:

| | L4.7B.2B (understand/1.4) | **L4.7B.3 (understand/1.12)** |
|---|---|---|
| field precision, overall | 0.885 | **0.938** |
| field recall, overall | 0.728 | **0.890** |
| field precision, REAL | 0.800 | **0.871** |
| field recall, REAL | 0.667 | **0.900** |
| unsupported inference | 0.0062 | **0.000** |
| clean cases | 99 | **131** of 162 |
| stance exact accuracy | 0.725 | **0.875** |
| false ACCEPT rate | 0.000 | **0.000** |
| FUTURE_INTENT recall | 0.727 | **0.909** |
| HESITATE recall | 0.167 | **0.500** |

`MODEL CHANGE: NO` — `gpt-4o-mini`, temperature 0, unchanged. Every point above came from
the prompt, the context contract and one mapper fix.

### Groups A–L

| Group | Cases | P before | P after | R before | R after | Clean |
|---|---|---|---|---|---|---|
| A intent | 27 | 0.886 | 0.886 | 0.738 | 0.929 | 21 |
| B vehicle | 24 | 0.925 | 0.932 | 0.875 | 0.982 | 20 |
| C location role | 23 | 0.853 | 0.973 | 0.806 | **1.000** | 22 |
| D quote request | 10 | 0.909 | **1.000** | 0.625 | 0.812 | 7 |
| E acceptance | 21 | 1.000 | 1.000 | 0.818 | 0.955 | 20 |
| F rejection/hesitation | 10 | 0.833 | 0.778 | 0.500 | 0.700 | 7 |
| G scheduling | 13 | 0.615 | 0.923 | 0.615 | 0.923 | 12 |
| H ordered alternatives | 9 | 0.667 | 0.889 | 0.667 | 0.889 | 8 |
| **I corrections** | 8 | 0.909 | 0.857 | 0.526 | **0.632** | 3 |
| J FAQ + business | 11 | 0.857 | 0.905 | 0.750 | 0.950 | 6 |
| K noisy/ASR | 12 | 0.750 | 0.882 | 0.529 | 0.882 | 9 |
| **L future/not ready** | 11 | 0.909 | 0.929 | 0.455 | **0.591** | 3 |

Group C reached 1.000 recall with 1.000 role accuracy — the Wild B failure class is closed at
corpus scale, not just on its own sentence.

## 3. What changed in the interpreter

The prompt was rewritten as **semantic rules with contrastive and negative examples**. No
surface-form lists, no case ids, no per-sentence patches. Section by section:

| Area | Rule now stated |
|---|---|
| **Intent scope** | Four kinds with explicit triggers and constant values. Writing to us, politeness, promising to return and searching for a car are **not** service intent — "el canal no es evidencia". A car offered inside a service question **is** intent (PROPOSED). |
| **Money** | A price question is `QUOTE_REQUEST`, never a FAQ topic; `payment` means *how* one pays, never *how much*. "Cuánto tarda" is duration, not money. |
| **Stance** | ACCEPT only for conformity with a proposal; courtesy is not acceptance; choosing a day is not a stance; doubt beats a promise (HESITATE); FUTURE_INTENT requires an explicit promise to return. |
| **Process fact** | `SEARCHING_NOT_READY` is a fact about the customer's purchase, emitted **alongside** a stance — unless a concrete car was already named. |
| **Scheduling** | A relative day stays relative: the temporal context is for understanding, not converting. Order preserved (PRIMARY/FALLBACK), a time never migrates between clauses, a day band is not a time, asking *when we can* is not proposing a day. |
| **Locations** | A locality said about the car is `INSPECTION_LOCATION`; origin only when the sentence is about the customer; a temporal expression after "en" is never a place. |
| **Corrections** | The `corrections[]` item **and** the corrected value; the vehicle in force carries `is_superseded=false`, the discarded one `true`. |
| **Catalog** | A numeric model name still gets its brand, capped at `PROPOSED` and mirrored into `catalog_candidate`. The deterministic reconciler still owns confirmation. |

One mapper fix, not a phrase patch: **an intent whose kind is named but whose value is
omitted is no longer dropped as "semantically empty"**. Each kind carries a controlled
constant, so `QUOTE_REQUEST` with a missing value used to vanish silently — a loss of
evidence disguised as an interpretation.

## 4. Iteration record (Part 12 discipline)

Eight prompt revisions, each measured on the full 162-case corpus. Nothing was kept on
aggregate score alone.

| Version | P | R | REAL P | REAL R | Unsup | Clean | Decision |
|---|---|---|---|---|---|---|---|
| 1.4 (inherited) | 0.885 | 0.728 | 0.800 | 0.667 | 0.0062 | 99 | baseline |
| 1.5 | 0.845 | 0.752 | 0.808 | 0.700 | 0.0123 | 109 | rejected — unsupported worsened |
| 1.6 | 0.852 | 0.724 | 0.880 | 0.733 | **REAL 0.083** | 103 | rejected — REAL unsupported, ambiguity 0.980, a false ACCEPT |
| 1.7 | 0.921 | 0.823 | 0.741 | 0.667 | 0.000 | 118 | kept as base — REAL precision too low |
| 1.8 | 0.951 | 0.831 | 0.923 | 0.800 | 0.0062 | 119 | kept as base — group recalls short |
| 1.9 | 0.936 | 0.870 | 0.885 | 0.767 | **REAL 0.083** | 126 | rejected — REAL unsupported returned |
| 1.10 | 0.933 | 0.870 | 0.889 | 0.800 | 0.000 | 127 | kept as base |
| 1.11 | 0.946 | 0.894 | 0.867 | 0.867 | 0.000 | 133 | close runner-up |
| **1.12 (shipped)** | **0.938** | **0.890** | **0.871** | **0.900** | **0.000** | **131** | **shipped** |
| 1.12 (second draw) | 0.937 | 0.882 | 0.871 | 0.900 | 0.000 | 129 | variance check |

1.11 and 1.12 are within measurement noise on the aggregate (±0.005 overall, ±0.03 on the 12
REAL cases). **1.12 was chosen because REAL recall is reproducibly higher — 0.900 in both
draws against 0.867 — and REAL is real customer language.**

Two revisions were rejected for exactly the reasons Part 12 names: 1.6 and 1.9 raised
aggregate recall while re-introducing an unsupported inference on a REAL case (a time
expression read as a locality). A rule that looks right and measures worse does not ship.

## 5. Critical cases (Part 13), both draws

| Case | Result |
|---|---|
| **WILD-A-04** "Mñ 15hs? O nose jueves que tenes" | **CLEAN** — PRIMARY TOMORROW 15:00, FALLBACK THURSDAY flexible. The Wild A scheduling class is closed. |
| **WILD-B-02** "Está en Berazategui, pero yo soy de Tigre." | **CLEAN** — both roles correct. |
| **WILD-B-01** "para revisar un 2008 del 2014…" | **Peugeot 2008 + 2014 preserved.** One false positive: an unrequested `quote_request`. |
| **REAL-001**, **REAL-004** | **CLEAN** in both draws |
| REAL-002 (heavy ASR-like noise) | 2 misses, **zero unsupported inference** |
| REAL-003 | 1 false positive (a `readiness` that should not fire once a car is named), **zero unsupported inference** |

**No unsupported inference on any owner example or any Wild case, in either draw.**

## 6. Residual error classes

| Class | Cases | Groups | Surface |
|---|---|---|---|
| the companion item omitted next to its value — `corrections[]` beside a corrected value, `SEARCHING_NOT_READY` beside a stance | ~9 | I, L | PROMPT |
| `quote_request` emitted from a service question | 2 | J, B | PROMPT |
| a `readiness` emitted although a car was already named | 1 | L | PROMPT |
| heavy-noise bursts producing nothing at all | 1 | K, L | PROMPT / MODEL (only class where model capacity is plausibly implicated) |
| stance granularity — HESITATE recall 0.500 | 3 | F | PROMPT |

`MODEL CHANGE REQUIRED: NO.` Part 15's escalation condition is not met: prompt and context
work still has an obvious next target (the companion-item class), and the instrument is
clean.

## 7. Context, authority and async — unchanged contracts

Bounded context is as L4.7B.2 left it: current date/weekday/timezone, stage, pending
clarification, offered slots, and the previous customer turn **of the current cycle only**.
Prior cycles, old candidates, old quotes and full thread history are excluded by
construction, and the prompt repeats the rule for corrections.

The shadow remains asynchronous: the turn captures provenance and enqueues; the model call
runs on the bounded worker. A live probe on the deployed image returned with the job still
queued, then completed on the worker.

Authority is untouched. The interpreter imports no ORM, no PricingService, no ScheduleService,
no OutboundSafetyGate and no ConversationEngine (asserted in test); it mutates no candidate,
location, quote, booking or lead state, and sends nothing.

## 8. Runtime

Image **`ridecheck-crm-backend:l4.7b3-semantic-29538be`**, deployed to **crm_test only**
(`OUTBOUND_ENABLED=false`, `SHADOW_UNDERSTAND_ENABLED=true`, `SHADOW_UNDERSTAND_ASYNC=true`).
Source/runtime parity verified by sha256 on all five touched modules; runtime reports
`understand/1.12` and `turn-evidence/1.1`.

Live probe on a two-message burst — "para revisar un 2008 del 2014, está en Berazategui pero
yo soy de Tigre" + "Mñ 15hs? O nose jueves que tenes":

```
vehicles     Peugeot 2008 (2014, PROPOSED)
locations    Berazategui INSPECTION_LOCATION · Tigre CUSTOMER_ORIGIN
scheduling   PRIMARY TOMORROW 15:00 · FALLBACK THURSDAY flexible
dispatch     async   ·   raw burst text stored: no
```

`crm_test.whatsapp_messages` stayed at **6 rows** before and after. Production untouched.

## 9. Tests and regression

`tests/test_l4_7b_3_shadow_semantic_quality.py` — **29/29 PASS**, covering INTENT-01…03,
STANCE-01…04, SCHED-01…03, LOC-01…02, CORR-01…03, QUOTE-01…02, FAQ-01, CAT-01, ASYNC-01,
CORPUS-01, plus the no-business-authority AST check. Stub transport: no network, no cost.

Three assertions in earlier suites were realigned, deliberately and disclosed:

* `test_l4_7b_2b_corpus_instrument.py` pinned the interpreter's SHA-256 and `understand/1.4`
  — that pin existed to prove L4.7B.2B changed **only** the ruler. This milestone's stated job
  is to change the interpreter, so the pin moved with it.
* `test_l4_7b_2_shadow_quality.py` asserted three prompt phrases that the rewrite reworded;
  they now assert the same contracts against the current wording and behaviour.
* the no-phrase-patch check now excludes the prompt literal itself. A prompt that defines
  "money question" in words is instruction; a branch on customer wording in code is a patch,
  and executable code is still asserted clean.

Launch-gate suites (L1, L2, L2.1, L3, L4.1, L4.2, L4.3, L4.4, L4.6, L4.7A/B/B.2/B.2B/B.3,
L4.7D, L4.7E, Wild-01 repro): **427 passed**, 1 failure and 9 errors, all in
`test_l4_2_clean_slate.py`, all pre-existing and environment-dependent (the test process has
no `CLOSED_BETA_ALLOWED_WA_IDS`; the runtime container does).

Full regression: **3 376 passed / 60 failed / 9 errors** — failure set identical to the
baseline at HEAD, **zero new failures**, verified by diffing the lists.

## 10. What comes next

The gate fails on one line, from one class, in two groups. The next milestone is finite:
**L4.7B.4-COMPANION-EVIDENCE** — make the interpreter emit the companion item wherever a
value implies one (`corrections[]` with a corrected value; the process fact with a stance),
and lift groups I and L above 0.70 recall without disturbing the nine lines that now pass.
It is prompt-and-mapper work; the model stays `gpt-4o-mini`.

L4.7C-SEMANTIC-RECONCILER-DESIGN remains **not started**, and must be an audit/design pass —
how TurnEvidence reconciles with deterministic evidence — before any implementation.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3** · OUTBOUND OFF.
