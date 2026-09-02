PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7B.2-SHADOW-INTERPRETER-QUALITY

# L4.7B.2 — Shadow interpreter quality

Date: 2026-09-02
Shadow only · ConversationEngine remains authoritative · OUTBOUND OFF · crm_test only ·
production DB untouched · no semantic authority moved · L1/L2/L3 FROZEN.

---

## 1. Verdict

**The quality gate defined in L4.7B.1 §11 is NOT passed. L4.7C does not start.**

Every disagreement class named in L4.7B.1 was remediated as a general contract. The
interpreter improved substantially — precision 0.428 → 0.716, unsupported inference
0.056 → 0.012 (REAL 0.000), clean cases 22 → 93 — but four gate lines remain short, and
8 of the 12 equivalence groups are still below the 0.70 recall floor.

---

## 2. What changed (all general, none phrase-specific)

| Phase | Change | Where |
|---|---|---|
| A | Response template shows `[]` for every optional array; semantically empty rows are dropped in the mapper; the emptiness contract lives in the schema | `semantic_interpreter.py`, `turn_evidence.py` (`is_semantically_empty`, `without_empty_items`) |
| B | Current local date, weekday and timezone supplied; the interpreter names a day expression and **never** an ISO date; a model-proposed `resolved_date` is dropped | prompt rules 11/15, mapper |
| C | Vehicle number pair kept whole: model **and** year retained; undecidable pairs stay `AMBIGUOUS` with alternatives; a year given as a string is no longer lost | prompt rule 12, `_coerce_year`, `_year_candidates` |
| D | Catalog identity the customer did not state literally is capped at `PROPOSED` and mirrored into `catalog_candidate` | prompt rule 13, `_cap`, `_said_literally` |
| E | `AcceptanceSignal.FUTURE_INTENT` — "I'll tell you when I've bought it" is neither acceptance nor rejection | `turn-evidence/1.1` |
| F | Intent emission scope and FAQ coexistence stated as rules; every topic asked is returned | prompt rules 8/14 |
| G | Bounded context: date, stage, pending clarification, offered slots, previous customer turn **of the current cycle only**; the burst itself is never fed back | `TurnContext`, `ConversationEngine._build_shadow_context` |
| H | The shadow record stores **which** context slots were supplied, never their values | `shadow-record/1.1` |
| I | Provenance captured synchronously; the model call and the append-only write moved to one bounded worker thread with an explicit drop on overflow | `shadow_worker.py` |
| J | `confidence` recorded when offered, clamped to [0,1], and never able to raise or lower a status | `_confidence` |
| K | **Model unchanged** — `gpt-4o-mini`, temperature 0 | — |
| L | Three SYNTHETIC labels corrected under the stated intent rule; **no REAL label touched** | `build_corpus.py`, `real_world_turns.jsonl` |

Additional hygiene found while measuring: placeholder strings (`"UNKNOWN"`, `"N/A"`, …) are
absences, not values, and a `day_expression` outside the controlled vocabulary is dropped.

`turn-evidence/1.1` is **additive**: 1.0 records still load under the major-version guard.

## 3. Corpus results — 162 cases, 162/162 calls OK

Five full-corpus draws were run across four prompt revisions. All numbers are measured, none
projected.

| Draw | Overall P | Overall R | Unsup. | Clean | REAL P | REAL R |
|---|---|---|---|---|---|---|
| understand/1.1 | 0.714 | 0.579 | 0.006 | 88 | 0.750 | 0.500 |
| understand/1.2 | 0.710 | 0.616 | 0.006 | 91 | 0.760 | 0.633 |
| understand/1.3 | 0.696 | 0.595 | 0.006 | 91 | 0.636 | 0.467 |
| understand/1.3 (repeat) | 0.700 | 0.599 | 0.006 | 91 | 0.609 | 0.467 |
| **understand/1.4 (shipped)** | **0.716** | **0.616** | 0.012 | **93** | **0.720** | **0.600** |

Baseline for comparison — `understand/1.0` (L4.7B): P 0.428, R 0.669, unsupported 0.056,
clean 22, REAL P 0.486 / R 0.567.

The repeat draw quantifies run-to-run variance at temperature 0: **≈ ±0.005 overall,
≈ ±0.03 on the 12 REAL cases**. Differences smaller than that are not evidence.

### By equivalence group (understand/1.4)

| Group | Cases | P | R | Clean | Recall ≥ 0.70 |
|---|---|---|---|---|---|
| A intent | 27 | 0.829 | 0.690 | 17 | ❌ (marginal) |
| B vehicle | 24 | 0.906 | 0.857 | 17 | ✅ |
| C location role | 23 | 0.853 | 0.806 | 17 | ✅ |
| D quote request | 10 | 0.909 | 0.714 | 7 | ✅ |
| E acceptance | 21 | 1.000 | 0.818 | 17 | ✅ |
| F rejection/hesitation | 10 | 1.000 | 0.300 | 0 | ❌ |
| G scheduling | 13 | 0.538 | 0.538 | 7 | ❌ |
| H ordered alternatives | 9 | 0.667 | 0.667 | 6 | ❌ |
| I corrections | 8 | 0.333 | 0.286 | 1 | ❌ |
| J FAQ + business | 11 | 0.250 | 0.360 | 1 | ❌ |
| K noisy/ASR | 12 | 0.500 | 0.312 | 3 | ❌ |
| L future/not ready | 11 | 0.300 | 0.130 | 0 | ❌ |

B, C, D and E — the groups that carry vehicle identity, location roles, quoting and
acceptance — are the ones that improved most and are now the strongest. The weak groups are
corrections (I), FAQ topic sets (J), rejection/hesitation (F) and future intent (L).

### Gate evaluation

| Gate line | Threshold | Measured (1.4) | |
|---|---|---|---|
| unsupported inference, REAL | 0.000 | 0.000 | ✅ |
| unsupported inference, overall | ≤ 0.01 | 0.012 (2 cases) | ❌ |
| role accuracy | 1.000 | 1.000 | ✅ |
| ambiguity/conflict handling | ≥ 0.98 | 1.000 | ✅ |
| field precision, REAL | ≥ 0.85 | 0.720 | ❌ |
| field recall, REAL | ≥ 0.85 | 0.600 | ❌ |
| field precision, overall | ≥ 0.80 | 0.716 | ❌ |
| field recall, overall | ≥ 0.85 | 0.616 | ❌ |
| every group recall | ≥ 0.70 | 8 of 12 below | ❌ |
| group I precision | ≥ 0.70 | 0.333 | ❌ |

Case-level: WILD-B-02 (the location-role failure that broke Wild B) is **fully clean**; the
four owner examples produce **zero unsupported inferences**; WILD-A-04 still resolves the
abbreviated relative day to the wrong weekday; WILD-B-01 keeps vehicle **and** year but adds
one unrequested quote-request item.

**Falling short of any line means L4.7C does not start. It does not start.**

## 4. A rule that measurement rejected

`understand/1.3` added what looks like an obviously correct rule — *acceptance only exists in
answer to something we proposed*. It removed the intended false positive, and in two
independent draws it also **cost REAL precision (0.760 → 0.636 / 0.609) and REAL recall
(0.633 → 0.467)** by making the model reticent about unrelated evidence in the same message.
The rule was therefore **withdrawn** in `understand/1.4`.

The finding is architectural, not cosmetic: whether a prior proposal exists is *known
deterministically* from canonical state, so "acceptance without a proposal" is
reconciliation's rejection to make, not the interpreter's silence to keep. Asking the
interpreter to police it degraded interpretation itself.

## 5. Cost and latency

| Metric | L4.7B (inline) | L4.7B.2 (worker) |
|---|---|---|
| mean latency | 2 390 ms | 1 668 ms |
| p95 latency | 3 722 ms | 2 311 ms |
| mean tokens per burst | 1 256 | 1 856 |
| estimated cost per burst | ≈ $0.00028 | ≈ $0.00040 |

The latency figure is now **off the customer turn**: the model call runs on a bounded
in-process worker (queue 32, one thread, explicit drop and `CE_SHADOW_DROPPED` log on
overflow). The turn itself only captures provenance and enqueues. Longer prompts cost ~48 %
more tokens per burst — roughly **$0.16 per 100 conversations**.

## 6. Tests

`tests/test_l4_7b_2_shadow_quality.py` — **33/33 PASS** (QUALITY-01…21 plus placeholder,
context-absence, worker-isolation, single-thread and recorder-path checks), stub transport,
no network.

One earlier assertion was deliberately amended and is disclosed here:
`test_l4_7a_turn_evidence_schema.py::test_acceptance_signals_are_distinct` pinned the 1.0
signal vocabulary and now expects `FUTURE_INTENT` as well. Every 1.0 signal is unchanged.

Full regression, same runner and image for both trees:

| Tree | Passed | Failed | Errors |
|---|---|---|---|
| baseline (HEAD `064dd0d`) | 3 295 | 60 | 9 |
| L4.7B.2 | 3 328 | 60 | 9 |

**Zero new failures**; the +33 are this milestone's own suite. The 60 failures and 9 errors
are the same pre-existing set carried since L2.

### A defect found while measuring

L4.7B's recorder accepted whatever `shadow_evidence_path` it was handed. When a test passed a
`MagicMock` settings object, `str()` of the mock became a real filesystem path and the test
run created a `backend/MagicMock/mock.shadow_evidence_path.strip()/…` tree of shadow records
inside the repository (never committed). The recorder and the CE hook now require a real
string and otherwise fall back to the canonical path; a test asserts it. **The stray
directory still exists on disk in the worktree and is left for the owner to delete** — it is
untracked and was not committed.

## 7. Safety

* OUTBOUND stayed **OFF** for the whole milestone; no WhatsApp message was sent.
* No production DB write. The corpus runs are pure API calls with no DB and no ORM import.
* Shadow remains shadow: `TurnEvidence` feeds nothing, no deterministic parser was removed,
  no business decision was reordered, no customer-visible text changed.
* No secret was printed or committed; the API key was read from the running container's
  environment and never echoed.
* Wild clean count remains **0/3**. No Wild was run.

## 8. Runtime

Image `ridecheck-crm-backend:l4.7b2-quality-<sha>` deployed to **crm_test only**, with
`OUTBOUND_ENABLED=false`, `SHADOW_UNDERSTAND_ENABLED=true`, `SHADOW_UNDERSTAND_ASYNC=true`.
Source/runtime parity verified for `semantic_interpreter.py`, `shadow_worker.py`,
`shadow_recorder.py`, `conversation_engine.py` and `turn_evidence.py`.

## 9. What comes next

The gate governs, not the calendar. The remaining distance is concentrated in four groups
(I corrections, J FAQ sets, F rejection/hesitation, L future intent) and in REAL precision.
None of them is a model-capacity argument yet: every failure inspected is a contract,
context or label question, and **MODEL CHANGE remains: NO**.

Two things should be decided by the owner before more prompt iterations:

1. whether the corpus is right that a first inbound message to an inspection service carries
   `service_intent` even when the customer only says they are still looking — that single
   labelling premise accounts for most of the REAL recall gap;
2. whether abbreviated relative days (the WILD-A-04 class) should be resolved by the
   interpreter at all, or handed to the deterministic layer as an ambiguity.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3** · OUTBOUND OFF.
