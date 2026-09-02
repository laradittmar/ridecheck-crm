PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7B.2A-CORPUS-TRUTH-REVIEW

# L4.7B.2A — Corpus truth review under the owner intent rule

Date: 2026-09-02
Corpus labels only · interpreter untouched · no prompt, model, schema or CE change ·
OUTBOUND OFF · crm_test only · production DB untouched · no Wild.

---

## 1. Verdict

**CONDITIONAL_PASS.** The owner rule is recorded and machine-applied; 10 labels contradicted
it and were corrected (1 REAL, 9 SYNTHETIC). The quality gate **still fails**, and the
measurement now answers the question it was run to answer:

> **Incorrect corpus expectations explain almost none of the failed gate as it stands.**
> Correcting every label that contradicted the owner rule moved recall by **+0.016**
> (0.6157 → 0.6314) and left precision **exactly unchanged** (0.7163). Only **8 of 93
> missing items** were wrong expectations.

But the review surfaced a second, larger measurement defect that is **not** an intent-scope
question and was therefore flagged rather than silently corrected: **40.7 % of every false
positive in the corpus comes from 8 synthetic fixtures whose labels omit the very evidence
they exist to protect** (§4). That is instrument error, not interpreter error, and it must be
repaired before any further interpreter tuning is meaningful.

## 2. Owner intent rule — RECORDED

A first inbound to RideCheck does **not** imply active `PREPURCHASE_INSPECTION` intent merely
because the customer wrote to an inspection business. Service intent must come from the
**wording**: naming the service, or the act of checking a vehicle's condition, or asking for
it / its price / its scheduling by name. Contacting us, politeness, saving the contact,
intending to write again, and searching for a car are **not** service intent.

Service intent and commercial readiness stay **separate**: a customer can be semantically
interested in an inspection and still be neither quote-ready nor scheduling-ready.

The rule is encoded once, as a function, in `tests/semantic_corpus/build_corpus.py`
(`names_the_service()`), and applied by the generators rather than by hand-listing case ids —
so it can be re-applied and audited, and it cannot drift into a phrase whitelist for one
sentence.

## 3. Part 1 — label re-audit

54 cases carried a `service_intent` expectation. Every one was re-read against the rule.

| Classification | Cases |
|---|---|
| LABEL_CORRECT | 41 |
| LABEL_TOO_AGGRESSIVE (corrected) | 8 |
| LABEL_TOO_WEAK (corrected) | 2 |
| OWNER_REVIEW_REQUIRED (flagged, unchanged) | 5 |

### Corrected — LABEL_TOO_AGGRESSIVE (intent asserted from channel, not wording)

| Case | Kind | Text | Before → After |
|---|---|---|---|
| REAL-001 | REAL | "…por ahora estoy buscando un auto agende esto para no perderlo … una vez q decida aviso" | `service_intent` + `readiness` + `vehicle` + `inspection_location` → **`readiness` + `vehicle` + `inspection_location`** |
| SYN-FUT-01 | SYNTHETIC | "Todavía estoy buscando auto, cuando encuentre te aviso" | `service_intent` + `readiness` → **`readiness`** |
| SYN-FUT-02 | SYNTHETIC | "Por ahora solo consulto, después vuelvo" | idem |
| SYN-FUT-03 | SYNTHETIC | "Estoy mirando, cualquier cosa te escribo" | idem |
| SYN-FUT-04 | SYNTHETIC | "Guardo el contacto y te hablo cuando decida" | idem |
| SYN-FUT-05 | SYNTHETIC | "Aún no elegí el auto" | idem |
| SYN-FUT-06 | SYNTHETIC | "Te consulto más adelante cuando tenga el auto" | idem |
| SYN-FUT-07 | SYNTHETIC | "Estoy en la búsqueda todavía" | idem |

REAL-001 is the owner's own worked example. Its engagement is preserved by `readiness =
SEARCHING_NOT_READY` (CONFIRMED), and its `vehicle` / `inspection_location` stay AMBIGUOUS.

### Corrected — LABEL_TOO_WEAK (service named, intent not labelled)

| Case | Kind | Text | Before → After |
|---|---|---|---|
| SYN-QUOTE-01 | SYNTHETIC | "¿Cuánto sale la revisión?" | `quote_request` → **`quote_request` + `service_intent` (PROPOSED)** |
| SYN-QUOTE-04 | SYNTHETIC | "Cuánto me cobran por revisar el auto?" | idem |

Bare price questions that never name the service (`"Me pasás precio?"`, `"Cuánto estarían
cobrando?"`, `"Me tirás un presupuesto?"`) were deliberately **not** given intent: the channel
is not evidence.

### Flagged — OWNER_REVIEW_REQUIRED (unchanged, awaiting your call)

| Case | Text | Question |
|---|---|---|
| SYN-MIX-02 | "¿Entregan informe? Es una Taos 2020 y está en Quilmes" | Does asking *about* the service, plus giving the car and its location, establish service intent? |
| SYN-MIX-03 | "¿Tengo que estar presente? El auto está en Palermo" | idem |
| SYN-MIX-05 | "¿Qué incluye el servicio? Estoy por comprar un usado en Avellaneda" | idem |
| SYN-MIX-07 | "¿Se paga antes o después? Es un Gol Trend 2016 en San Justo" | idem |
| SYN-MIX-08 | "¿Hacen a domicilio? El auto está en Belgrano" | idem |

These are not covered by either side of the rule: the wording names no service, but the
question is *about* performing it, and the customer supplies the car and the place. They were
left exactly as they are. The corpus `owner_review_required` flag was **not** set on them,
because that flag is asserted REAL-only by `test_l4_7e_semantic_corpus.py`; the flag for these
five lives here, in this closeout.

REAL raw text was not touched anywhere. `SYN-FUT-08` ("Cuando lo vaya a ver te aviso para que
lo revisen") keeps its intent label — the wording names the service — and gained only a note.

## 4. Part 2 — metrics after truth correction (interpreter unchanged)

Two independent measurements, both against **understand/1.4** exactly as shipped in L4.7B.2:

* **primary (label-isolating):** the *same* saved interpreter outputs, rescored against the
  corrected labels — the only variable is the labels;
* **confirmation (live):** a fresh 162/162 run of the unchanged interpreter.

| Metric | Before truth review | **After (primary)** | After (live draw) |
|---|---|---|---|
| field precision, overall | 0.7163 | **0.7163** | 0.7136 |
| field recall, overall | 0.6157 | **0.6314** | 0.6229 |
| field precision, REAL | 0.7200 | **0.7200** | 0.7200 |
| field recall, REAL | 0.6000 | **0.6207** | 0.6207 |
| unsupported inference, overall | 0.0123 | **0.0123** | 0.0062 |
| unsupported inference, REAL | 0.000 | **0.000** | 0.000 |
| role accuracy | 1.000 | **1.000** | 1.000 |
| ambiguity/conflict accuracy | 1.000 | **1.000** | 1.000 |
| missing-field accuracy | 1.000 | **1.000** | 1.000 |
| clean cases | 93 | **92** | 92 |
| counts | tp 149 / fp 59 / fn 93 | **tp 149 / fp 59 / fn 87** | — |

### A (interpreter weakness) versus B (incorrect corpus expectations)

**Precision did not move at all.** The interpreter was never emitting `service_intent` on the
eight over-labelled cases, so removing those expectations created no false positives — it only
retired 8 false negatives, while the 2 new expectations added 2 back. Net: **6 of 93 missing
items (6.5 %) were corpus error; 93.5 % is interpreter behaviour.**

Measured against the gate distance, the intent-scope premise was **not** the reason the gate
failed. It was worth resolving — the rule is now explicit and machine-checked — but it does
not move the verdict.

### Group metrics A–L (after truth review)

| Group | Cases | P (primary) | R (primary) | P (live) | R (live) | Clean |
|---|---|---|---|---|---|---|
| A intent | 27 | 0.829 | 0.707 | 0.824 | 0.683 | 17 |
| B vehicle | 24 | 0.906 | 0.857 | 0.906 | 0.857 | 17 |
| C location role | 23 | 0.853 | 0.806 | 0.824 | 0.778 | 17 |
| D quote request | 10 | 0.909 | 0.625 | 0.909 | 0.625 | 5 |
| E acceptance | 21 | 1.000 | 0.818 | 1.000 | 0.818 | 17 |
| F rejection/hesitation | 10 | 1.000 | 0.300 | 1.000 | 0.250 | 0 |
| G scheduling | 13 | 0.538 | 0.538 | 0.538 | 0.538 | 7 |
| H ordered alternatives | 9 | 0.667 | 0.667 | 0.667 | 0.667 | 6 |
| I corrections | 8 | 0.333 | 0.286 | 0.400 | 0.286 | 1 |
| J FAQ + business | 11 | 0.250 | 0.360 | 0.229 | 0.320 | 1 |
| K noisy/ASR | 12 | 0.500 | 0.333 | 0.545 | 0.400 | 3 |
| L future/not ready | 11 | 0.300 | 0.200 | 0.333 | 0.267 | 1 |

Group D recall fell (0.714 → 0.625) as a direct consequence of the two TOO_WEAK corrections:
the interpreter does not emit intent for those two, so a correct label creates a true miss.
Group A and L recall rose. Nothing else moved.

### The instrument defect found while measuring (NOT corrected here)

Eight synthetic fixtures — `SYN-MIX-01…08`, the group whose entire purpose is proving that a
FAQ must not discard business evidence (the Wild B defect) — carry labels that **omit the
business evidence in their own text** and use a sentinel FAQ topic:

```
SYN-MIX-02  "¿Entregan informe? Es una Taos 2020 y está en Quilmes"
   expected : faq_topics=['mixed'], service_intent
   produced : vehicle='Volkswagen Taos', vehicle_year=2020,
              inspection_location='Quilmes', faq_topics=['report']
   scored   : tp 0, fp 4, fn 2
```

Every correct extraction is counted as a false positive, and the literal topic `"mixed"` — a
label sentinel that is not in the schema's FAQ vocabulary — is counted as a miss against the
real topic the interpreter names. Across the 8 fixtures: **tp 1, fp 24, fn 15**, i.e.
**40.7 % of all false positives** and **17.2 % of all false negatives** in the entire corpus.

Excluding those 8 fixtures entirely, the same unchanged interpreter measures **P 0.810 /
R 0.674** — precision above the gate's 0.80 line. That number is an *illustration of the
instrument's error*, not a claim of achieved quality, and it is not used anywhere in the gate
evaluation below.

This was left uncorrected on purpose: it is a different question from the owner's intent rule,
it changes 8 fixtures' expectations substantially, and the corpus rule is that a label
correction requires a stated reason and review. It is the first item of the recommended next
milestone.

## 5. Part 3 — remaining error classes (labels corrected)

| Error class | Cases | REAL | SYN | Groups | Likely fix surface |
|---|---|---|---|---|---|
| `service_intent` not emitted though the wording names the service | 25 | 4 | 21 | A7 K7 J7 B5 D3 | **PROMPT** (5 of the J cases are CORPUS/owner-review) |
| `readiness` not emitted | 19 | 2 | 17 | F10 L9 | **SCHEMA + CORPUS** — engagement is expressible twice (`readiness` vs `AcceptanceSignal.FUTURE_INTENT`), and the harness flattens acceptance to True/False |
| FAQ topic set incomplete or absent | 10 | 1 | 9 | J8 D2 | **CORPUS** (the `"mixed"` sentinel) + PROMPT for genuinely missed topics |
| scheduling value mismatch (relative day, branch order) | 8 | 1 | 7 | G6 H3 | **PROMPT + CONTEXT** |
| `acceptance` emitted where not expected | 7 | 2 | 5 | L7 A2 K2 | **SCHEMA/CORPUS** (same engagement overlap) |
| `acceptance` not emitted where expected | 7 | 0 | 7 | F4 E3 | **PROMPT** |
| `inspection_location` emitted where not expected | 7 | 0 | 7 | J4 I2 K1 | **CORPUS** (J fixtures) + PROMPT (noise) |
| `customer_origin` emitted where not expected | 6 | 0 | 6 | C5 J1 | **PROMPT** (role over-assignment) |
| `correction` not emitted | 6 | 0 | 6 | I6 | **PROMPT** (corrections are the weakest contract) |
| `vehicle_year` emitted where not expected | 6 | 0 | 6 | J5 I2 | **CORPUS** (J fixtures) |
| `inspection_location` not emitted | 5 | 0 | 5 | C5 | **PROMPT** |
| `vehicle` emitted where not expected | 5 | 0 | 5 | J5 | **CORPUS** (J fixtures) |
| `quote_request` emitted where not expected | 4 | 2 | 2 | J3 A2 B2 | **PROMPT** |
| vehicle value mismatch (make inferred or not) | 3 | 2 | 1 | A2 B2 | **RECONCILER** — catalog identity is deterministic by design; the interpreter's make is only a `catalog_candidate` |
| superseded-vehicle value | 2 | 0 | 2 | I2 | **PROMPT** |
| unsupported inference | 2 | 0 | 2 | I1 K1 | **PROMPT** |
| `customer_logistics_offer` not emitted | 1 | 1 | 0 | A1 | **PROMPT** |

No fix was implemented. `MODEL` appears nowhere: every class is a prompt, schema, context,
mapper, corpus or reconciler question, so **MODEL CHANGE remains: NO**.

## 6. Part 4 — quality gate, thresholds unchanged

| Gate line | Threshold | Measured (primary) | |
|---|---|---|---|
| unsupported inference, REAL | 0.000 | 0.000 | ✅ |
| unsupported inference, overall | ≤ 0.01 | 0.0123 (live draw 0.0062) | ❌ |
| role accuracy | 1.000 | 1.000 | ✅ |
| ambiguity/conflict handling | ≥ 0.98 | 1.000 | ✅ |
| field precision, REAL | ≥ 0.85 | 0.720 | ❌ |
| field recall, REAL | ≥ 0.85 | 0.621 | ❌ |
| field precision, overall | ≥ 0.80 | 0.716 | ❌ |
| field recall, overall | ≥ 0.85 | 0.631 | ❌ |
| every group recall | ≥ 0.70 | 8 of 12 below (D, F, G, H, I, J, K, L) | ❌ |
| group I precision | ≥ 0.70 | 0.333 | ❌ |

**GATE: FAIL.** No threshold was lowered. L4.7C does not start.

### Smallest finite remaining shadow-quality milestone

**L4.7B.2B-CORPUS-FIXTURE-REPAIR** — corpus only, no interpreter change, finite and fully
enumerable:

1. repair the 8 `SYN-MIX` fixtures: real FAQ topic sets instead of the `"mixed"` sentinel, and
   the vehicle / location / year evidence their own text contains;
2. adjudicate the 5 flagged `SYN-MIX` cases under the owner rule (owner decision);
3. resolve the engagement ontology: `readiness` vs `AcceptanceSignal.FUTURE_INTENT`, including
   the harness mapping that currently flattens acceptance to True/False and cannot express
   FUTURE_INTENT at all;
4. re-run the unchanged interpreter and re-evaluate the gate.

Only after the instrument is correct does an interpreter milestone (**L4.7B.3**) make sense,
targeting the classes that are genuinely the interpreter's: intent not emitted when the
service is named inside a question (A, K), corrections (I), relative-day resolution and branch
order (G, H), and location role over-assignment (C).

## 7. Part 5 — `backend/MagicMock` cleanup

| Check | Result |
|---|---|
| absolute path | `/opt/ridecheck-crm-release-candidate/backend/MagicMock` |
| `git status` | `?? backend/MagicMock/` (untracked) |
| `git ls-files` | **0 tracked files**; `git log --all` touching the path: **0 commits** |
| file count | **811 files**, 2 directories, 3.2 MB |
| tree | `backend/MagicMock/mock.shadow_evidence_path.strip()/<pid-like digits>` — one file per name, no other shape |
| production imports/references | **0.** The only two occurrences of "MagicMock" in `backend/app` are *comments* (`shadow_recorder.py:93`, `conversation_engine.py:4172`); nothing reads, writes or imports the path |
| content | **839 JSONL lines, 100 % `shadow-record/1.0` records with `shadow: true`** — accidental shadow-recorder output, zero non-conforming files |

All three conditions held, so the tree was removed. The cause was fixed in L4.7B.2: the
recorder and the CE hook now require a real string path and otherwise fall back to the
canonical forensics path, with a test asserting it.

## 8. Part 6 — artifact-contract noncompliance

Recorded: a previous artifact returned `TYPE: AUDIT` / `MILESTONE: L4.7B.2_Shadow` instead of
the canonical header requested in its prompt. No rerun of L4.7B.2 was performed and no
technical conclusion of that milestone is affected. All future artifacts use the exact
PROJECT / TYPE / MILESTONE values as requested, verbatim, with no abbreviation or renaming.

## 9. Safety

No production behaviour change. No `ConversationEngine` change. No semantic prompt change. No
model change. No schema change. No authority migration. No Wild. OUTBOUND OFF. Production DB
untouched — the only database contact in this milestone was none at all; the corpus runs are
API calls with no DB. Corpus raw text unchanged. L1/L2/L3 FROZEN. Wild clean count 0/3.

Regression after the label change: **3 328 passed / 60 failed / 9 errors**, identical to the
L4.7B.2 baseline — zero new failures.
