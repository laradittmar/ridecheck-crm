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
role, status and notes. The remaining provenance fields become mandatory at L4.7A when the
schema is implemented.

---

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
not regress against the previous interpreter version.

---

## 9. Governance

- No production fix may consist solely of adding a phrase, regex or alias so that one Wild
  sentence passes, unless it implements a documented general semantic invariant and is
  accepted by the corpus (`LAUNCH_TRUTH_ROADMAP.md §6.1`).
- Corpus entries are append-only in spirit: correcting a label requires a note explaining
  why the earlier reading was wrong.
- Every Wild failure must contribute its raw utterances to the corpus.
