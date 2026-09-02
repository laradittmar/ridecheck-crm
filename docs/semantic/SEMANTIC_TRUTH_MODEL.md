# RideCheck Semantic Truth Model

Status: **canonical engineering source of truth for real-language interpretation**
Introduced by: L4.7E-SEMANTIC-EQUIVALENCE-CORPUS (2026-09-01)
Companion data: [`tests/semantic_corpus/real_world_turns.jsonl`](../../tests/semantic_corpus/real_world_turns.jsonl)
Harness: [`tests/semantic_corpus/evaluation.py`](../../tests/semantic_corpus/evaluation.py)

This document defines *what counts as true* in RideCheck's conversation pipeline, and how a
future semantic interpreter will be evaluated before it is allowed to change runtime
behaviour. It changes no runtime behaviour by itself.

---

## 1. Three layers of truth

| Layer | What it is | Who writes it | Mutability |
|---|---|---|---|
| **RAW EVIDENCE** | exactly what the customer sent — text, transcript, media reference, order, WAMID | transport (Meta → n8n → backend) | **immutable**; never edited, never normalised in place |
| **TURN EVIDENCE** | a semantic *interpretation* of what the customer appears to mean, per field, with role, status, confidence and provenance | the semantic interpreter | proposal only; auditable; never business truth |
| **CANONICAL STATE** | the operational truth RideCheck acts on: candidate, zone, quote, stage, appointment | deterministic reconciliation | authoritative; records how each value was accepted |

Hard rules:

1. **Never silently replace one layer with another.** An interpretation is not a fact; a
   fact is not the customer's words.
2. **RAW must remain reconstructable** for any historical conversation (see §6).
3. **TURN EVIDENCE must remain auditable** — every item carries where it came from.
4. **CANONICAL STATE must record how and why** evidence was accepted, rejected, clarified
   or left unresolved.
5. The interpreter has **interpretation authority only**. It holds no DB or business
   authority — that boundary was verified in L4.7 Part 7 and must stay verified.

---

## 2. Evidence status model

| Status | Meaning | Reconciliation behaviour |
|---|---|---|
| **CONFIRMED** | evidence is sufficiently supported and deterministic reconciliation accepts it | write canonical value |
| **PROPOSED** | a plausible interpretation exists but needs validation or more context | do **not** write canonical value; may drive a clarification |
| **AMBIGUOUS** | several valid interpretations remain | never force a value; clarify |
| **CONFLICT** | two or more sources or assertions contradict each other | never force a value; clarify or escalate |

Corollary: **AMBIGUOUS and CONFLICT never produce canonical values** unless a deterministic
business rule genuinely resolves them (e.g. catalog uniqueness, LR-2/LR-3 location roles).
The expected next action for those statuses is usually a clarification question.

---

## 3. Provenance contract

Each meaningful TurnEvidence item should eventually carry:

```
field                 e.g. vehicle, inspection_location, scheduling_preference
value                 interpreted value (or list for ordered alternatives)
role                  e.g. INSPECTION_LOCATION vs CUSTOMER_ORIGIN
source_message_id     WAMID or DB message id
source_burst_id       burst/correlation id
source_span           character span in the raw text, when available
source_interpreter    "semantic" | "deterministic" | "flow" | "human"
confidence            model or rule confidence, when available
status                CONFIRMED | PROPOSED | AMBIGUOUS | CONFLICT
schema_version        TurnEvidence schema version
model_version         interpreter model/prompt version
reconciliation        ACCEPTED | REJECTED | DEFERRED | CLARIFY, with a reason
```

Corpus fixtures carry the subset that is meaningful without a live runtime: field, value,
role, status and notes. **The full contract is implemented as of L4.7A** — see §3.1.

---

### 3.1 Implemented schema (L4.7A)

`backend/app/schemas/turn_evidence.py` — schema version **`turn-evidence/1.0`**. Pure
pydantic: it imports only `json`, `enum`, `typing` and `pydantic` (asserted by test), so it
can never reach a database, a service or the conversation engine.

| Container | Purpose |
|---|---|
| `TurnEvidence` | everything one interpreter proposed about one burst; **frozen** |
| `EvidenceItem` | base contract: `field, value, normalized_value, role, status, confidence, alternatives, catalog_candidate, reason, provenance` |
| `ServiceIntentEvidence` | inspection interest, quote request, readiness, logistics offer (`kind`) |
| `VehicleEvidence` | `make, model, year, year_status, category_suggestion, is_superseded, mention_index` |
| `LocationEvidence` | `locality, zone_hint` + **mandatory `role`** |
| `FaqIntentEvidence` | one per topic — FAQs never erase business evidence |
| `AcceptanceEvidence` | `signal` ∈ ACCEPT / REJECT / HESITATE / QUESTION_ONLY / UNKNOWN |
| `SchedulingRequestEvidence` | ordered branches: `priority, day_expression, resolved_date, time, flexible_time, rank` |
| `CorrectionEvidence` | `relation` ∈ CORRECT_EXISTING / REPLACE_CANDIDATE / SWITCH_TO_PRIOR_CANDIDATE / ADD_SECOND_CANDIDATE / UNKNOWN_RELATION, with `from_value`/`to_value` |
| `IdentityEvidence`, `HandoffEvidence` | customer/seller identity, human-handoff signal |
| `AmbiguityNote`, `ConflictNote` | alternatives and both sides preserved — no winner chosen |
| `Provenance`, `SourceSpan`, `TurnRef` | source kind, interpreter, model version, schema version, source message ids, spans, and how the burst was reconstructed |

**Roles.** Location roles are `INSPECTION_LOCATION`, `CUSTOMER_ORIGIN`, `SELLER_LOCATION`,
`UNKNOWN_LOCATION_ROLE`. Role assignment never depends on field order:
`"Está en Berazategui, pero yo soy de Tigre."` and
`"Yo soy de Tigre pero el auto está en Berazategui"` produce the same two roles.

**Burst reconstruction is recorded, not assumed.** `TurnRef.reconstruction` ∈
`LIVE_DEBOUNCE`, `REPLAY_CHRONOLOGICAL`, `REPLAY_CAUSAL_MARKER`, `CORPUS_FIXTURE`,
`UNKNOWN` — because L4.7E found historical burst grouping is only PARTIAL, every
interpretation says how its input was assembled.

### 3.2 Versioning rules

* `schema_version` is always serialized (`turn-evidence/<major>.<minor>`).
* Additive, optional fields are a **minor** bump; anything that changes the meaning of an
  existing field, or removes one, is a **major** bump.
* `TurnEvidence.from_json` rejects an unknown prefix and any different **major** version,
  so an old record can never be silently reinterpreted by a newer build.
* `extra="forbid"`: unknown keys fail loudly instead of being dropped.
* `to_canonical_json()` is deterministic (sorted keys, compact separators, unicode kept),
  so shadow-replay records can be compared byte-for-byte.

### 3.2.1 Interpretation hygiene (L4.7B.2, `turn-evidence/1.1`)

Three rules bind any interpreter, not just today's model:

* **No empty evidence.** An item whose every meaningful field is empty carries nothing and
  must never reach reconciliation. `EvidenceItem.is_semantically_empty()` defines it and
  `TurnEvidence.without_empty_items()` prunes it, across *every* array. Partial evidence is
  not empty: an `AMBIGUOUS` item with alternatives, or a day without a time, survives.
* **Time is deterministic.** The interpreter is given the current local date, weekday and
  timezone, and may only name a day expression from the controlled vocabulary. It never
  returns an ISO date; `resolved_date` is computed by the deterministic layer, and a date
  proposed by a model is dropped rather than trusted.
* **Nothing the customer said is discarded to make room.** When two numbers can be a model
  and a year, both are kept; if the assignment is undecidable the item stays `AMBIGUOUS`
  with the alternatives listed, and no reading is silently preferred.

`1.1` is additive: `AcceptanceSignal.FUTURE_INTENT` ("I'll come back when I've bought it" —
neither acceptance nor rejection) and the emptiness contract. Every `1.0` record still
loads.

### 3.2.2 Bounded context and advisory confidence

The interpreter receives a small, explicitly bounded context: current date/weekday/timezone,
the conversation stage, the clarification we are waiting on, slots already offered, and the
previous customer turn **of the current cycle only**. Prior-cycle history is excluded by
construction, so a vehicle or locality from a finished inspection cannot leak into a new
one. The context block is labelled in the prompt as *not* customer evidence, and only the
*names* of the supplied slots are recorded — never their values.

Catalog identity the customer did not state literally (a make deduced from a model, a
category, a normalized model) is capped at `PROPOSED` and mirrored into
`catalog_candidate`: the deterministic catalog decides. `confidence` is advisory — it is
recorded when offered, clamped to [0, 1], and never raises or lowers a status.

### 3.3 Reconciliation boundary

`TurnEvidence` has **no** reconciliation field and no apply/commit/save API. Dispositions
live in `ReconciliationRecord` / `ReconciliationLog` (append-only; `append()` returns a new
log), each referencing a stable item ref such as `location_mentions[0]`, with a status of
`ACCEPTED`, `REJECTED`, `DEFERRED`, `NEEDS_CLARIFICATION`, `CONFLICT_UNRESOLVED` or
`SUPERSEDED`. Historical interpretation is never rewritten to match a later canonical truth.

### 3.4 Worked examples

```
"para revisar un 2008 del 2014"
  VehicleEvidence(make="Peugeot", model="2008", year=2014,
                  category_suggestion="SUV_4X4_DEPORTIVO",   # a suggestion, not authority
                  status=CONFIRMED)
  → still interpreted evidence until catalog reconciliation accepts it

"Está en Berazategui, pero yo soy de Tigre."
  LocationEvidence(locality="Berazategui", role=INSPECTION_LOCATION, status=CONFIRMED)
  LocationEvidence(locality="Tigre",       role=CUSTOMER_ORIGIN,     status=CONFIRMED)

"Mñ 15hs? O nose jueves que tenes"
  SchedulingRequestEvidence(priority=PRIMARY,  day_expression="TOMORROW", time="15:00", rank=1)
  SchedulingRequestEvidence(priority=FALLBACK, day_expression="THURSDAY", time=None,
                            flexible_time=True, rank=2)
  → the 15:00 can never migrate to the Thursday branch

bare "2008" with no year
  VehicleEvidence(status=AMBIGUOUS, value=None,
                  alternatives=[Alternative("Peugeot 2008"), Alternative(2008)])
  AmbiguityNote(field="vehicle", reason="model vs manufacturing year")
```

## 4. Canonical expectations in the corpus

Every corpus case may declare:

| Key | Meaning |
|---|---|
| `expected_turn_evidence` | what a correct interpreter should propose, with status |
| `expected_canonical_state` | what deterministic reconciliation should accept (often *nothing*) |
| `expected_missing_fields` | what must remain unknown after this turn |
| `expected_next_action` | the business-correct next move (ask, quote, offer slots, escalate, none) |
| `must_not_infer` | values that must **never** appear — the anti-hallucination contract |

Worked example (real, Wild B):

```
RAW              "Está en Berazategui, pero yo soy de Tigre."
TURN EVIDENCE    inspection_location = Berazategui   (CONFIRMED, role INSPECTION_LOCATION)
                 customer_origin     = Tigre         (CONFIRMED, role CUSTOMER_ORIGIN)
CANONICAL        inspection_location = Berazategui
MUST NOT INFER   inspection_location = Tigre
```

---

## 5. Truth is business-defined, never model-defined

The corpus **tests** interpreters; interpreters do **not** define the corpus. Labels are
written by engineering/owner judgement. Where the business reading is genuinely uncertain
the case is marked `owner_review_required: true` and must be resolved by the owner before
it is used to gate a migration. No label may be produced by asking the current model and
accepting its answer.

---

## 6. Semantic replay contract

The intended future capability — **offline, non-mutating**:

```
historical raw messages (whatsapp_messages, direction='in', ordered by timestamp)
   → reconstruct the burst exactly as the debounce assembled it
   → run semantic interpreter version N (shadow, no DB writes)
   → produce TurnEvidence
   → compare against corpus / human truth
   → report per-field metrics and disagreements
```

Explicit non-goals:

- **No online self-learning.** The system never adjusts itself from live traffic.
- **No autonomous retraining.** No model weights, prompts or thresholds change without a
  human-reviewed corpus delta and a certification run.

The permitted learning loop is:

```
real conversation → anonymised, human-labelled corpus entry → offline evaluation
   → prompt/schema/model improvement → regression certification → controlled deployment
```

---

## 7. Privacy

Corpus fixtures carry no customer PII beyond what a semantic test needs. Phone numbers,
emails, WhatsApp IDs, full street addresses and personal names are removed or masked;
localities remain because location roles are the thing under test. **Natural spelling,
typos, ASR artefacts and punctuation are preserved verbatim** — they are the signal.

---

## 8. Evaluation metrics

Reported separately, never as a single opaque score
(see `tests/semantic_corpus/evaluation.py`):

| Metric | Definition |
|---|---|
| **field precision** | proposed evidence items that are correct ÷ all proposed items |
| **field recall** | expected evidence items correctly proposed ÷ all expected items |
| **role accuracy** | items with the correct role (e.g. inspection location vs customer origin) ÷ items where a role is expected |
| **unsupported-inference rate** | cases producing at least one `must_not_infer` value ÷ all cases — **the safety metric; target 0** |
| **ambiguity handling accuracy** | cases whose AMBIGUOUS/CONFLICT expectations were honoured (not forced into a value) ÷ all such cases |
| **missing-field accuracy** | cases where fields expected to stay unknown did stay unknown |

A migration may proceed only when unsupported-inference rate is 0 and the other metrics do
not regress against the previous interpreter version. The concrete gate that governs the
move of semantic authority into canonical state is defined in the L4.7B.1 audit §11 and
evaluated on a full-corpus rerun; failing any line of it means L4.7C does not start.

---

## 9. Governance

- No production fix may consist solely of adding a phrase, regex or alias so that one Wild
  sentence passes, unless it implements a documented general semantic invariant and is
  accepted by the corpus (`LAUNCH_TRUTH_ROADMAP.md §6.1`).
- Corpus entries are append-only in spirit: correcting a label requires a note explaining
  why the earlier reading was wrong.
- Every Wild failure must contribute its raw utterances to the corpus.
