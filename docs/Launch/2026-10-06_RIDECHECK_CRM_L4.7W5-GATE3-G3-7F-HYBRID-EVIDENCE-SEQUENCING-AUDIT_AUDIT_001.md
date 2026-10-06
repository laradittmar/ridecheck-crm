PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W5-GATE3-G3-7F-HYBRID-EVIDENCE-SEQUENCING-AUDIT

## §0 SCOPE AND POSTURE

Read-only audit. No source or test was modified in the main worktree, nothing was
committed, amended, pushed, built, deployed or restarted, no flag or outbound was
enabled, no model, Meta or WhatsApp call was made, the tester was not reset, no
business data was mutated and no Wild was run. No implementation is proposed as
executed work; nothing in §17/§18 has been built.

All executable evidence was produced in two disposable archive trees created with
`git archive HEAD | tar -x`, which leave no git metadata and no tracked change.
No model call was made: every semantic claim below is read from executable source,
recorded test output or the deployed database.

The preserved work was read only. Its five distinct artifacts carry the same
SHA-256 before and after this audit (§2, §20).

## §1 BASELINE VERIFICATION — BEFORE

| Item | Value | Required | Match |
|---|---|---|---|
| Branch | `fix/m21.1.1-primary-flow-regression` | same | YES |
| HEAD | `5c3894c484fff8233a3c308621b1132e1a2dda25` | `5c3894c` | YES |
| HEAD~1 | `66ec6c88991a7dfd781615978fde16b8e4e677b0` | `66ec6c8` | YES |
| Remote tip | `161aa8d6573c8fb316922c51d9af3cf4616d0cc2` | unchanged | YES |
| Ahead / behind | 26 / 0 | 26 / 0 | YES |
| Tracked modifications | 0 | 0 | YES |
| Stash entries | 0 | 0 | YES |
| `5c3894c` content | `1 file changed, 709 insertions(+)` — the adopted architectural audit only | doc only | YES |
| Runtime `GIT_SHA` | `840bce0` | unchanged | YES |
| `OUTBOUND_ENABLED` | `false` | false | YES |
| Correction flags | `G=[] P=[]` (both unset) | unset | YES |
| Containers | `2c06976a4034` backend / `43879bd4be63` n8n / `7afa22b8a0a0` postgres | same ids | YES |
| Restart counts | 0 / 0 / 0 | 0 | YES |
| Deployed alembic head | `20260918_hybrid_traces` | unchanged | YES |
| `whatsapp_clarification_attempts` | absent (0) | absent | YES |
| `whatsapp_thread_states.pending_correction` | absent (0) | absent | YES |
| Business data | 47 leads / 34 threads / 1 candidate / 35 outbound / 734 security events / 2 traces | unchanged | YES |

The authoritative database is `crm_test` (the backend's `DATABASE_URL`), not `crm`.
`crm` holds 7 thread-state rows and is not the certification database; this is
recorded because an earlier query against `crm` produced a misleading
"column does not exist" result that is not true of the live schema.

## §2 PRESERVED-WORK MANIFEST (SHA-256, first 32 hex)

| File | Bytes | SHA-256 (32) |
|---|---|---|
| `implementation.patch` | 119,439 | `c07bb2de64ad03533dea0b90274a9fa7` |
| `clarification_attempt.py` | 16,322 | `d888e8c5eb7e691c799aa8cfdc8255ed` |
| `clarification_router.py` | 8,178 | `ae500817266f85e341a147dcdf918398` |
| `20261006_clarification_attempts.py` | 4,516 | `f7fadd84a44ac54721563fa7602d5f68` |
| `test_l4_7w5_g3_7f_clarification_lifecycle.py` | 34,649 | `47e52ad9d2c282296bee9fa95a9a2a7f` |

Nine files are present; four are byte-identical `.moved` duplicates of the above
(same hashes), an artefact of the preservation step, not separate work.
Total 246,769 bytes. The patch touches 22 files: 5 production
(`models.py`, `schemas/hybrid_trace.py`, `services/conversation_engine.py`,
`services/pending_correction.py`, `ui/hybrid_trace_view.py`) and 17 test files.

## §3 WHAT THIS AUDIT OVERTURNS

The milestone brief carries the sequencing blocker as "the router consumes
reconciled current-turn evidence 218 lines before that evidence exists", with the
decision point at line 7502 and the candidate write at 7720. Both numbers are
correct — **of the patched tree**. They are not the numbers of the committed tree,
and the difference is not cosmetic: it changes what the blocker *is*.

Measured at HEAD (`5c3894c`; its source is byte-identical to `66ec6c8` — the commit
added only the audit document):

| Boundary | HEAD | Patched tree |
|---|---|---|
| Semantic dispatch (`_run_shadow_understand`) | 6554 | 6742 |
| Deterministic vehicle detection (`lookup_vehicle`) | 7059 | — |
| New clarification decision (`_clarification_decision`) | — | **7502** |
| `_routing_gate` | **7305** | 7513 |
| `_check_fallback_flow_triggers` | 7311 | 7519 |
| Reconciliation (`_reconciled_reply_proposals`) | 7379 | 7587 |
| Canonical write (`_apply_candidate`) | **7512** | **7720** |

The true gap at HEAD is **207** lines, and the decisive finding is the ordering,
not the distance: **semantic evidence is dispatched 751 lines before the gate and
is awaitable throughout.** The blocker is therefore not "the evidence does not
exist yet". It is three separate facts, which §6 separates.

## §4 ARCHITECTURAL SHAPE

`handle()` [3308..3454] → `_handle()` [3458..3525], which is 68 lines and delegates.
Every clarification authority lives inside **`_process_text` [6438..7900], a single
1463-line method** — the longest in the engine by a factor of five over the next
(`_availability_authority`, 267 lines). This is the proximate cause of the
sequencing problem: there are no phase boundaries to place a decision between.

## §5 ORDERED HANDLER EXECUTION MAP

Reconstructed from executable source at HEAD by AST enclosure, not from names or
comments. Columns: line, operation, tier produced, whether it writes canonical
state, and whether it is reachable with both correction flags off.

| # | Line | Operation | Produces | Writes canonical | Flags-off reachable |
|---|---|---|---|---|---|
| 1 | 3462 | thread lookup | — | no | yes |
| 2 | 3467 | inbound dedup | — | no | yes |
| 3 | 3490 | lead resolution | — | no | yes |
| 4 | 3494 | `_execute_cycle_reset` (operator flag only) | cycle identity | **yes** | yes |
| 5 | 3518 | human-ownership exit | — | no | yes |
| 6 | 6447 | `last_stage = STAGE_QUALIFYING` | — | **yes** | yes |
| 7 | 6460 | `_fetch_burst_messages` | raw burst | no | yes |
| 8 | 6531 | `_canonical_turn_burst` assembled | canonical burst | no | yes |
| 9 | **6554** | `_run_shadow_understand` — **semantic dispatch** | interpreted (semantic), pending | no | yes |
| 10 | 6644 | `_focus_candidate` | committed read | no | yes |
| 11 | 6706 | `_reconciled_scheduling_requests` | **reconciled (scheduling)** | no | yes |
| 12 | 6813 | `_semantic_handoff_requested` | interpreted read | no | yes |
| 13 | 6916 | `_reconciled_scheduling_requests` (second window) | reconciled (scheduling) | no | yes |
| 14 | 6947 | `_extract_zone_from_text` | interpreted (deterministic) | no | yes |
| 15 | 6956 | `_apply_inspection_zone` | — | **yes** | yes |
| 16 | 6995 | `_create_candidate_from_catalog` (writer 2) | — | **yes** | yes |
| 17 | **7059** | `lookup_vehicle` → `pre_detected_vehicle` | **interpreted (deterministic, typed)** | no | yes |
| 18 | 7072 / 7092 / 7225 | `_create_candidate_from_catalog` (writers 3–4) | — | **yes** | yes |
| 19 | 7231 / 7682 / 7693 | `_apply_vehicle_identity` (writer 5) | — | **yes** | yes |
| 20 | 7288 | `_apply_zone_from_text` | — | **yes** | yes |
| 21 | **7305** | `_routing_gate` — clarification authority 1 | decision | **yes** (on dispatch) | yes |
| 22 | **7311** | `_check_fallback_flow_triggers` — authority 2 | decision | **yes** (flags at 6363/6412 pre-dispatch) | yes |
| 23 | 7328 | `resolve_field_evidence(ctx, state)` — no turn text | committed snapshot | no | yes |
| 24 | **7379** | `_reconciled_reply_proposals` → `_resolve_candidate` → `_resolution_to_candidate_data` | **reconciled (candidate)** | no | yes |
| 25 | 7384 | `_apply_extracted` | — | **yes** | yes |
| 26 | **7512** | `_apply_candidate` — **canonical commit** | **committed** | **yes** | yes |
| 27 | 7547 | `_enforce_catalog_vehicle` | — | **yes** | yes |

Five distinct candidate/identity writers exist (steps 16, 18, 19, 26, 27). The
typed clarification router added by the owner resolution is unreachable at
flags-off, so the live clarification decision today is steps 21 and 22 only.

## §6 THE TWELVE EVIDENCE-BOUNDARY QUESTIONS

Three tiers must be kept apart: **interpreted evidence** (what an engine says about
this turn), **reconciled proposed state** (what the two engines jointly propose),
**committed canonical state** (what is written).

| # | Question | Answer | Evidence |
|---|---|---|---|
| 1 | When does semantic interpreted evidence exist? | Dispatched line 6554; awaitable from then on | `_run_shadow_understand` is a **sync** method [8407..8555] submitting `ShadowJob` to a worker |
| 2 | Is it available at the clarification decision point? | **YES** — 751 lines earlier | ordering in §5 |
| 3 | How is it consumed? | `_semantic_turn_evidence()` [8576..8593] — bounded wait, one call per burst, `None` on timeout/failure/unparseable | source |
| 4 | Does the live gate read it? | **NO** | `_routing_gate` [6119..6215] contains no semantic accessor |
| 5 | When does typed deterministic current-turn evidence exist? | Line 7059 (`pre_detected_vehicle`) | source |
| 6 | Does the gate read it? | Partially — only to compute `vehicle_known`; never for domain relevance | 6158–6160 |
| 7 | Can the deterministic engine emit more typed current-turn evidence? | **YES, already built and unused** | `resolve_field_evidence(ctx, state, current_turn_text)`; 5 of 8 resolvers do real-time detection from `turn` |
| 8 | Do CE call sites use it? | **NO** — all four pass no turn text | 6154, 6324, 7328, 8461 |
| 9 | When does reconciled proposed state exist? | Line 7379 (candidate); 6706/6916 (scheduling) | source |
| 10 | Is reconciled candidate state available at the decision point? | **NO** — 74 lines later | §5 |
| 11 | When does committed canonical state exist? | Line 7512 | source |
| 12 | Is it available at the decision point? | **NO** — 207 lines later | §5 |

**The gate's complete evidence diet** (`_routing_gate`, 97 lines):
1. `combined = " ".join(ai_input_messages)` — **raw customer text**, fed to five
   substring predicates;
2. `resolve_field_evidence(ctx, state)` with turn text deliberately omitted —
   committed state only;
3. `pre_detected_vehicle` — one typed current-turn value, used only for `vehicle_known`;
4. `state.location_fallback_flow_sent` / `vehicle_fallback_flow_sent` — mutable flags.

So the blocker decomposes into three findings, and only the third is a sequencing
problem at all:
- **S-1 (not sequencing):** interpreted evidence from *both* engines is available
  at the decision point and simply is not consulted for relevance.
- **S-2 (not sequencing):** a typed deterministic current-turn channel exists and
  is disabled at every call site.
- **S-3 (sequencing):** reconciled proposed state (7379) and committed canonical
  state (7512) genuinely do not exist at 7305.

## §7 DETERMINISTIC ENGINE CONTRACT

Already capable of typed current-turn evidence: `_resolve_vehicle`,
`_resolve_vehicle_category`, `_resolve_vehicle_year`, `_resolve_inspection_location`
and `_resolve_inspectability` all accept and use `turn`. `_resolve_customer_origin`
is turn-only. `_resolve_service_intent` and `_resolve_scheduling` are state-only.

The audit must distinguish two uses of phrase lists, and the project's own stop
condition only forbids one:

**Bounded interpreter (acceptable).** `lookup_vehicle` and the catalogue resolve an
alias to a typed `VehicleMatch` with `marca`, `modelo`, `tipo_vehiculo` and a
confidence. The phrases are inputs to a typed output that downstream authority can
inspect, override and re-resolve.

**Routing authority (forbidden).** Four frozensets decide the customer's route
directly, with no typed intermediate:

| Constant | Line | Entries | Consumer |
|---|---|---|---|
| `_OUTSIDE_COVERAGE_CITIES` | 756 | 23 | `_is_outside_coverage` → Priority 1 |
| `_VEHICLE_CONCERN_WORDS` | 765 | 19 | `_has_vehicle_concern` → Priority 2, suppresses 3 |
| `_PRICE_INTENT_WORDS` | 775 | 9 | `_wants_price_quote` → Priority 5 |
| `_FAQ_OR_SOFT_CLOSE_PHRASES` | 810 | 38 | `_is_general_faq_or_soft_close` → Priority 3 |

**89 phrases carry live routing authority.** `_is_generic_vehicle_text` adds two
inline literals (`"auto"` as a whole word, `"vehiculo"` as a substring). Every one
is a plain `in` test over normalised text, so each is a false-positive surface with
no typed record of why the route was taken — which is precisely the untraceability
the G3-1 live-trace FAIL recorded.

## §8 SEMANTIC ENGINE CONTRACT

| Property | Value |
|---|---|
| Call timing | dispatched at 6554, immediately after the canonical burst is assembled |
| Dispatch mechanism | sync method submitting `ShadowJob` to a worker; `self._turn_semantic` holds the provider |
| Calls per burst | exactly one — `_turn_semantic_calls` is initialised to 0 and incremented inside the single `interpreter.interpret` |
| Burst preservation | `self._turn_semantic_texts = list(burst_texts)`, keyed by `burst.fingerprint`; the burst is not re-split |
| Consumption | `_semantic_turn_evidence()` — bounded wait, no second call, no guessing |
| Admission | `_evidence_admitted` requires a non-`None` value, a resolved status and no supersession; `_admitted_location_evidence` additionally requires a `locality` and a non-`CUSTOMER_ORIGIN` role |
| Degraded representation | `None`, meaning *no semantic evidence* — never a denial and never a veto |
| Fail-closed arming | `_turn_semantic_dispatched = True` arms the G3-5C check |

This contract already satisfies the hybrid premise. It is not the obstacle.

## §9 REGRESSION REGISTER — MEASURED, NOT INHERITED

Method: sorted FAILED/ERROR/SUBFAIL **identities**, never counts, from the sealed
runner on two archive trees.

```
docker run --rm --network none -e DATABASE_URL="sqlite://" -e PYTHONDONTWRITEBYTECODE=1 \
  -v <tree>:/work:ro -w /work/backend ridecheck-crm-test:l4.7b2 \
  python -m pytest /work/tests -q -rA -p no:cacheprovider
```

| Tree | Reported | Identities |
|---|---|---|
| HEAD (baseline) | 58 failed, 5744 passed, 76 skipped, 9 errors, 3894 subtests passed | **67** (58 F + 9 E + 0 SUBFAIL) |
| HEAD + preserved work | 104 failed, 5745 passed, 91 skipped, 9 errors, 3974 subtests passed | **112** (103 F + 9 E) |

The baseline reproduces the established 67 identities exactly.

**Added identities: 51. Removed identities: 6.** The new 60-test lifecycle suite
contributes **zero** failures.

**FINDING SEQ-01 — the "41" in the brief is not the preserved state.** The
preserved work measures **51** added identities, not 41. 41 was an intermediate
attempt that was reverted and is not what was preserved; the per-file breakdown
carried forward (19 / 11 / 5 / 5 / 3 / ~8) sums to 51, not 41. No register of 41
rows can be produced, because no 41-identity artefact exists. Every row below is
measured from the preserved patch as preserved.

### Composition of the 51

| Family | Count | Files |
|---|---|---|
| A — legacy two-step fallback removed | 19 | `test_m18_business_logic.py` |
| B — substring direct-dispatch envelope removed | 9 | `test_m20_6d2_customer_reality.py` (RC04, RC06, RC07, RC08, RC12a, RC14, RC16×2, RC17) |
| C — infrastructure defect (`FlushError`) | 6 | `test_br1_intent_gate.py` ×4, `test_m21_1_1_service_intent_gate.py` ×2 |
| D — intent-gate behaviour | 4 | `test_m21_1_1_service_intent_gate.py` ×3, `test_br1_intent_gate.py` ×1 |
| E — real-customer safety regression | 5 | `test_wild04r_phase2_observability.py` ×3, `test_m20_6d2_customer_reality.py` RC18, RC19 |
| F — governance counts and freeze invariants | 8 | `g3_5f_structured` ×2, `g3_1_hybrid_site_trace` ×2, `trace_row_semantics`, `g3_7f_owner_resolution`, `g3_5f_bounded_correction`, `g3_5e2_attribution_and_binding` |

### The 6 removed identities — real defects the preserved work fixes

All six are in `test_m21_2_c_cross_turn_continuity.py` and were failing at HEAD:
`test_rejection_clears_pending_key`, `test_rejection_clears_pending_text`,
`test_rejection_no_candidate_created`, `test_rejection_zone_not_promoted`,
`test_customer_origin_not_written_to_state_home`, `test_rejection_and_new_vehicle`.
Fuzzy-rejection evidence clearing and customer-origin leakage are genuinely
repaired. This is positive evidence for the design and must not be discarded.

### FINDING SEQ-02 (BLOCKER) — a mandatory durable write on the primary text path

Six added identities fail with:

```
sqlalchemy.orm.exc.FlushError: Instance <WhatsAppClarificationAttempt at 0x…>
has a NULL identity key.
```

Traceback: `_process_text:7508 → _execute_clarification:4789 → clarification_attempt.py:211`,
which is `sess.commit()` inside `reserve()`.

Root cause, established structurally:
- `_clarification_decision` [4586..4679] calls
  `self._attempt_store().spent_families(...)` at 4657 **unconditionally on every
  qualifying turn**, and `_execute_clarification` calls `reserve()` whenever a
  route is required.
- These tests construct the engine with `eng.db = MagicMock()`
  (`_make_engine`, `test_br1_intent_gate.py:143`). The store derives its session
  from `db.bind`, so a flush occurs against a mock bind and SQLAlchemy cannot
  obtain a generated primary key.
- A *read* tolerates the mock (`ClarificationFamily.VEHICLE in MagicMock()` is
  False), so the defect is invisible until a route is actually required.
- `reserve()` catches only `IntegrityError`. `FlushError` is not caught, so it
  propagates and **destroys the turn**.

The model declaration is not at fault: `id: Mapped[int] = mapped_column(primary_key=True)`
is exactly the style used by every other model in `models.py`.

This is not a test-harness inconvenience. It is an architectural fact: the preserved
design converts clarification from an in-process decision into a **mandatory
durable database write on the main conversational path**, and the only failure mode
it handles is a uniqueness conflict. Any other database fault — a transient
connection loss, a missing table during a partial migration, a read-only replica —
arrives as an uncaught exception on a live customer turn.

**Order dependence.** In the full sealed run, six added identities show `FlushError`.
In a focused run of the same files, `test_r1_06_vehicle_catalog_hit_passes_through`
and `test_r1_15_kill_switch_catalog_hit_ai_path` also raise it. The incidence is
therefore **order-dependent (≥6, up to 8)**, which makes the finding more serious,
not less: the same turn succeeds or dies depending on prior state.

A seventh test, `TestRC24ConcernCaseQuote.test_rc24_concern_path_quilmes_quote`, was
already failing at baseline and now fails with `FlushError` instead of its original
reason — a pre-existing failure whose cause is masked.

### FINDING SEQ-03 (BLOCKER) — a closed real-customer Wild defect is reopened

`test_wild04r_phase2_observability.py` fails as follows:

```
AssertionError: 'Peugeot' unexpectedly found in
'Revisamos el Peugeot 2008 2014 con gusto. ¿En qué zona está?'
WILD-04-R2: 'Focus 2008' must NOT trigger Peugeot 2008 confirmation
WILD-04-R3: 'Gol 2008' must NOT trigger Peugeot 2008 confirmation
```

A customer who says **"Focus 2008"** (a Ford Focus, model year 2008) is answered
with a confirmation of a **Peugeot 2008** — a different vehicle, where `2008` is
the model name rather than the year. The same happens for "Gol 2008". This is the
WILD-04-R2/R3 defect, found in a real Wild and closed; the preserved work reopens it.

Mechanism: `_clarification_decision` derives `identity_resolved` and
`category_resolved` at 4637–4651 directly from admitted evidence and
`ce_vehicle`, calling `self._catalog_tipo_for(_ev_make, _ev_model)` itself. Because
it runs **before** reconciliation (7502 vs 7587) and before the writer (7720), it
bypasses the numeric-model disambiguation that the committed tree performs on the
candidate path at 7168–7236. The router then reports the identity resolved, the
turn skips the disambiguation branch, and the catalogue confirms the wrong car.

This is the sequencing blocker causing a priced, customer-visible error — not an
abstract ordering concern.

### FINDING SEQ-04 (HIGH) — the one-attempt flag is no longer set on dispatch

```
RC19: AssertionError: location_fallback_flow_sent must be True after successful delivery
RC18: Standard Location Flow body expected in blocked audit
```

The preserved patch gutted `_check_fallback_flow_triggers` (hunk
`@@ -6315,126 +6603,26 @@` — 126 lines removed, 26 added), removing both Flow
dispatches and the pre-dispatch flag writes at 6363/6412. That correctly closes
**CL-04**. But the replacement dispatch does not set `location_fallback_flow_sent`
on successful delivery, while the patch's own model docstring states the flag keeps
exactly one meaning — "a Flow proposal is currently outstanding".

Consequence: a Location Flow is delivered to the customer and **nothing records that
it is outstanding**. The surviving `_routing_gate` Priority 4 is still gated on that
flag, so it can dispatch a second Location Flow for the same need. The reservation
row prevents a second *attempt*, but outstanding-ness and attempt history are now
split across two stores and only one is written. RC18 further shows the blocked-audit
record no longer carries the certified Location Flow body, so transport attribution
for a blocked dispatch is wrong.

### FINDING SEQ-05 (MEDIUM) — two cycle identities coexist

`cycle_key()` implements the sound key, and its docstring explicitly names the
unsound one it refuses to use. Both remain in the tree:

| Function | Key | Sound |
|---|---|---|
| `cycle_key(state)` (store) | `COALESCE(current_cycle_start_message_db_id, 0)` | yes |
| `_reconciler_cycle_id(state)` [9056..9059] | `current_cycle_start_message_db_id` **or** `current_cycle_started_at`, else `None` | **no** — mixes a message id with a timestamp |

The reconciler was not corrected. The same cycle can be keyed two ways by two
subsystems in one turn.

### FINDING SEQ-06 (MEDIUM) — the placement rationale in the patch is factually wrong

The inserted comment at 7494–7497 reads:

> "Placed here deliberately. It runs on the ordinary conversational path, after
> semantic and deterministic evidence have been reconciled into canonical state …
> and BEFORE `_routing_gate`, whose substring-driven clarification branches it replaces."

The first clause is false. In the patched tree the decision is at **7502**;
reconciliation (`_reconciled_reply_proposals`) is at **7587** and the canonical
write (`_apply_candidate`) at **7720**. The decision runs **85 lines before
reconciliation and 218 lines before canonical state**. SEQ-03 is the direct
consequence. The second clause is true and was verified.

### FINDING SEQ-07 (LOW) — governance counts, not behaviour

Eight identities are arithmetic or freeze assertions that moved because the work
legitimately added a consumer and a migration: `47 != 46`, `8 != 7`
("five pre-existing consumers, the R2 reader, and the G3-7 derive"),
`test_site_41_this_milestone_introduced_no_migration`,
`test_row_29b_no_migration_and_no_schema_change`. `test_or_20_the_next_unresolved_turn_routes_to_a_human`
expects `('GENERIC_HUMAN…', …)` and receives `('NO_CLARIFICATION_REQUIRED', 'RESOLVED')`,
which is a behavioural disagreement with the owner-resolution contract and is
classified with family D, not here.

### FINDING SEQ-08 (LOW) — a pre-existing harness fault masks one result

`test_returning_obs_2_issue7_focus_pilar_new_candidate` reports
`TypeError: _FakeRepo.find_zone_by_group_and_detail() got an unexpected keyword
argument 'zone_group'`. That stub mismatch is independent of this work and hides
whatever the real outcome would be.

## §10 BEHAVIOUR FAMILIES

| Family | Count | Shared behavioural claim | Legitimately invalidated by the selected architecture? |
|---|---|---|---|
| A | 19 | the legacy two-step fallback asks once in text, then dispatches a Flow, and sets the sent-flag before dispatch | **YES** — this is one of the deliberately removed clarification paths |
| B | 9 | `_routing_gate` dispatches a Flow directly from substring predicates over raw text | **YES** — removing phrase lists as routing authority is the point of the milestone |
| C | 6 | CE can be exercised with a mocked session | **NO** — architectural defect SEQ-02 |
| D | 4 | the intent gate reaches AI with `last_intent` persisted for a catalogue hit | **NO** — needs per-test adjudication; `or_20` contradicts the owner-resolution contract |
| E | 5 | real customers: "Focus 2008" is a Ford; a delivered Flow is recorded as outstanding | **NO** — SEQ-03 and SEQ-04 are real defects |
| F | 8 | counts and freeze inventories | wording/count only |

19 + 9 = **28 of 51** are legitimate test migrations under the brief's own rule.
**23 of 51 are not**, and 11 of those are blockers or high-severity defects.

## §11 OPTIONS

| | Option | Decision point | Evidence available | Cost | Verdict |
|---|---|---|---|---|---|
| A | Feed the existing gate interpreted evidence in place | 7305 | interpreted both engines; **no** reconciled, **no** committed | small, bounded | insufficient — SEQ-03 persists; identity still resolved pre-reconciliation |
| B | **Move the one decision between reconciliation and mutation** | after 7379, before 7512 | interpreted both engines, reconciled candidate, pre-mutation canonical | bounded, one file | **SELECTED** |
| C | Decompose `_process_text` into explicit phases | new phase boundary | all tiers, by construction | 1463-line rewrite; §19 stop condition "a broad handler rewrite with no bounded file set" | rejected for this gate; revisit as a separate programme |
| D | Keep the preserved placement at 7502 | pre-reconciliation | interpreted only | none | rejected — ships SEQ-03 |

## §12 REQUIRED ARCHITECTURAL SELECTION — OPTION B

One clarification decision, evaluated once per turn, at a point where all three
tiers exist. The exact target sequence:

1. assemble the canonical burst (6531);
2. dispatch semantic interpretation once (6554);
3. run deterministic detectors, producing `pre_detected_vehicle` (7059);
4. call `resolve_field_evidence(ctx, state, current_turn_text)` **with the turn
   text**, enabling the typed deterministic channel that exists today;
5. await semantic evidence through `_semantic_turn_evidence()` — bounded, once;
6. admit evidence through the existing admission predicates;
7. reconcile into the candidate proposal (`_reconciled_reply_proposals`, 7379);
8. **evaluate `_clarification_decision` exactly once, here** — after step 7, before
   step 11 — with reconciled proposed state, both interpreted channels and the
   pre-mutation canonical snapshot;
9. derive `identity_resolved` / `category_resolved` / `location_resolved` **from the
   reconciled proposal**, never by re-resolving the catalogue inside the router
   (this is what closes SEQ-03);
10. read `spent_families` for the cycle from `whatsapp_clarification_attempts`;
11. apply the canonical mutation (`_apply_candidate`, 7512) when no clarification
    is required;
12. when clarification *is* required, reserve the attempt (INSERT-as-reservation)
    **before** dispatch;
13. dispatch through `OutboundSafetyGate` with the existing `path_id`;
14. on proven dispatch, set the outstanding flag **and** advance the attempt row to
    `DISPATCHED` (closes SEQ-04);
15. on blocked or failed dispatch, advance to `DISPATCH_BLOCKED` / `DISPATCH_FAILED`
    and leave the outstanding flag false;
16. keep `*_attempt_spent` (immutable history) and `*_flow_outstanding` (mutable)
    in their separate stores, never substituted;
17. retire `_routing_gate` Priorities 2, 4 and 5 and the gutted
    `_check_fallback_flow_triggers` clarification branches;
18. retain Priority 1 (outside coverage) and Priority 3 (FAQ → AI) as
    **non-clarification** routing until a typed replacement exists, and record that
    they remain phrase-driven;
19. keep `cycle_key()` as the single cycle identity and correct
    `_reconciler_cycle_id` to the same key (closes SEQ-05);
20. persist the decision into `hybrid-decision-trace` so the chosen route and its
    reason are traceable without reading container logs.

Neither engine holds unilateral authority at step 8: `vehicle_relevant` and
`location_relevant` are already `semantic OR deterministic`, so CE silence cannot
veto admitted semantic evidence and semantic silence cannot remove safe
deterministic behaviour. That part of the preserved router is correct and is kept.

**The preserved design is not defective. Its insertion point is.** The correction is
to move one call site from 7502 to after 7587 (patched numbering) and to source
resolution from the reconciled proposal rather than re-deriving it.

## §13 WHAT IS NOT IN SCOPE

G3-8, the `ThreadRevision` / CRM `Revision` staleness after a post-booking
correction (R2R-07), `reply_renderer_demotion_enabled` defaulting True (G37ER-05),
and `G37BR-02` remain open and are not touched here.

## §14 DEGRADED-MODE DECISION TABLE

Behaviour required of step 8 when evidence is partial. "Relevant" means the family
was mentioned this turn by either engine or is required-next-information.

| # | Semantic | Deterministic | Reconciled proposal | Attempt spent | Flow outstanding | Required route |
|---|---|---|---|---|---|---|
| 1 | present, resolves family | any | resolved | — | — | NO_CLARIFICATION_REQUIRED |
| 2 | `None` (timeout/fail) | resolves family | resolved | — | — | NO_CLARIFICATION_REQUIRED — semantic silence is not a denial |
| 3 | present, family unresolved | silent | unresolved | no | no | FAMILY_FLOW_REQUIRED |
| 4 | `None` | silent, but required-next authorized | unresolved | no | no | FAMILY_FLOW_REQUIRED — deterministic silence is not a veto |
| 5 | any | any | unresolved | **yes** | no | GENERIC_HUMAN_REQUIRED — the one attempt is spent |
| 6 | any | any | unresolved | no | **yes** | NO_CLARIFICATION_REQUIRED (wait) — a proposal is already with the customer |
| 7 | any | any | unresolved | no | no, but another confirmable proposal conflicts | GENERIC_HUMAN_REQUIRED — owner policy 8 |
| 8 | any | any | — | — | — | human owns turn → NO_CLARIFICATION_REQUIRED, no dispatch |
| 9 | `None` | silent | unresolved, **not relevant this turn** | — | — | NO_CLARIFICATION_REQUIRED — stored incomplete state is not relevance |

Row 9 is the owner-policy-2.8 case that an earlier attempt broke by escalating a
payment FAQ to a human; row 2 and row 4 are the two halves of the hybrid premise.
No row permits a mutation before authority is established, and no row permits a
dispatch without a reservation.

## §15 CUSTOMER-BEHAVIOUR CLASSIFICATION

| Class | Count | Identities |
|---|---|---|
| **PRESERVE** — real-customer evidence; the code must change, not the test | 5 | `wild04_resolver_focus_2008_not_numeric_path`, `wild04_resolver_gol_2008_not_numeric_path`, `returning_obs_2_issue7_focus_pilar_new_candidate`, `rc18_outbound_blocked_candidate_persists_flag_false`, `rc19_successful_delivery_sets_flag_and_dedup_blocks_replay` |
| **CORRECT** — the old assertion encoded a deliberately removed clarification path | 28 | all 19 family-A `m18` identities; 9 family-B `m20_6d2` identities (RC04, RC06, RC07, RC08, RC12a, RC14, RC16×2, RC17) |
| **WORDING / COUNT ONLY** | 8 | family F |
| **ADJUDICATE INDIVIDUALLY** — not yet classifiable | 4 | `r1_06`, `r1_15`, `r2_12`, `or_20` |
| **NOT CREDIBLE** | **0** | — |
| **BLOCKED BY INFRASTRUCTURE** — cannot be classified until SEQ-02 is fixed | 6 | family C |

No real-customer test is classified obsolete because its internal branch is being
removed. The 28 CORRECT identities are migrations of mechanism, not of customer
behaviour: in each case the customer must still be asked exactly once, through the
same certified Vehicle or Location Flow, and the assertion moves from the legacy
flag/phrase mechanism to the reservation and the typed route.

## §16 PRESERVED DATABASE FOUNDATION — COMPATIBILITY

| # | Check | Result |
|---|---|---|
| 1 | Migration present and well-formed | yes — `revision="20261006_clarification_attempts"` |
| 2 | `down_revision` resolvable | yes — `20261005_pending_correction`, which exists and is committed at HEAD |
| 3 | Chain reaches the deployed head | yes, but **two** migrations are pending: `20261005_pending_correction` then `20261006_clarification_attempts`; deployed head is `20260918_hybrid_traces` |
| 4 | Clarification table deployed | **no** (0) — as required by this posture |
| 5 | `pending_correction` column deployed | **no** (0) |
| 6 | Cycle column exists in the live schema | **yes** — `current_cycle_start_message_db_id`, added by `20260824_wild04r_cycle_boundary` |
| 7 | Cycle column populated | **no** — NULL on all 33 live thread states |
| 8 | `cycle_reset_pending` set anywhere | **no** — 0 rows |
| 9 | Effective cycle key today | `0` for every live thread |
| 10 | Practical meaning of `UNIQUE(thread_id, cycle_key, family)` | one vehicle attempt and one location attempt **per thread for the whole thread lifetime**, since no automatic cycle boundary exists and only an operator-set flag creates a new key |
| 11 | Idempotency key width | 36 chars (UUIDv5), fits `varchar(36)` on `whatsapp_messages.correlation_id` and `security_events.correlation_id` |
| 12 | Independent-session pattern | follows `OutboundSafetyGate._gate_db` — factory from `db.bind`, `SessionLocal` fallback, context manager rolling back on exception |

Items 9 and 10 are sound and are the intended bound, but they are **owner-visible**:
with no automatic cycle boundary, "one attempt per cycle" is in practice "one
attempt ever, until an operator resets the thread". That is a deliberate consequence
of the selected architecture, not a defect, and it should be acknowledged
explicitly rather than discovered in production.

Item 12 is correct in pattern but incomplete in fault handling — see SEQ-02.

## §17 BOUNDED IMPLEMENTATION BOUNDARY

Exact permitted file set for the implementation milestone:

| File | Change |
|---|---|
| `backend/app/services/conversation_engine.py` | move the single `_clarification_decision` call to after `_reconciled_reply_proposals` and before `_apply_candidate`; source resolution from the reconciled proposal; pass `current_turn_text` to `resolve_field_evidence`; set the outstanding flag on proven dispatch; correct `_reconciler_cycle_id`; retire gate Priorities 2/4/5 |
| `backend/app/services/clarification_attempt.py` | handle non-`IntegrityError` database faults in `reserve()` without claiming the customer was asked |
| `backend/app/services/clarification_router.py` | unchanged if the §14 table is already satisfied; otherwise pure-predicate changes only |
| `backend/app/models.py` | the clarification model, as preserved |
| `backend/migrations/versions/20261006_clarification_attempts.py` | as preserved |
| `backend/app/services/pending_correction.py` | remove the superseded `route_clarification` only |
| `backend/app/ui/hybrid_trace_view.py` | clarification card, as preserved |
| `tests/test_l4_7w5_g3_7f_clarification_lifecycle.py` | as preserved |
| the 28 CORRECT test identities | migrate assertions to the reservation and typed route |
| the 8 WORDING/COUNT identities | update counts and freeze inventories |

Out of bounds: the semantic prompt, the LLM, the published Meta Flows, any second
reconciler, any second candidate writer, any new free-text clarification workflow,
any phrase addition, and the decomposition of `_process_text` (Option C).

## §18 ACCEPTANCE CONTRACT

1. Exactly one `_clarification_decision` evaluation per turn on the qualifying path.
2. It is called after `_reconciled_reply_proposals` and before `_apply_candidate`,
   proven by executable line order, not by comment.
3. `identity_resolved` / `category_resolved` / `location_resolved` are derived from
   the reconciled proposal; the router does not call the catalogue itself.
4. "Focus 2008" and "Gol 2008" never produce a Peugeot 2008 confirmation —
   `test_wild04r_phase2_observability.py` passes unmodified.
5. `resolve_field_evidence` is called with the current turn text wherever the
   decision depends on this turn.
6. No clarification route is decided from `_PRICE_INTENT_WORDS`,
   `_VEHICLE_CONCERN_WORDS`, `_FAQ_OR_SOFT_CLOSE_PHRASES` or
   `_is_generic_vehicle_text`.
7. Raw customer text does not enter the clarification router.
8. Semantic `None` never denies a route that deterministic evidence supports (row 2).
9. Deterministic silence never vetoes admitted semantic evidence (row 4).
10. All nine §14 rows are covered by executable tests.
11. A delivered clarification Flow sets its outstanding flag on proven dispatch —
    RC19 passes unmodified.
12. A blocked dispatch records the certified Flow body — RC18 passes unmodified.
13. Attempt history and outstanding-ness remain in separate stores, never
    substituted.
14. The reservation INSERT precedes dispatch; no dispatch without a reservation.
15. `reserve()` distinguishes a uniqueness conflict from every other database
    fault and never reports a non-conflict as "already attempted".
16. A database fault during reservation does not raise an uncaught exception on a
    customer turn.
17. No `FlushError` appears anywhere in the sealed suite.
18. `cycle_key()` is the only cycle identity; `_reconciler_cycle_id` agrees with it.
19. One candidate writer and one reconciler; no new instances of either.
20. No mutation occurs before authority is established.
21. The chosen route and its reason are persisted in the hybrid decision trace.
22. Both correction flags remain off and the decision is reachable with them off.
23. Identity diff against the 67-identity baseline shows the 6 fixed identities
    still fixed, zero PRESERVE-class identities failing, and no identity outside
    the CORRECT and WORDING/COUNT sets.
24. Git, runtime, flags, containers and business data unchanged;
    `OUTBOUND_ENABLED=false` throughout.

## §19 STOP CONDITIONS — NOT TRIGGERED, WITH ONE QUALIFICATION

No stop condition was triggered by this audit, which performed no implementation.
Three were evaluated against the **preserved work** and are recorded:
- "mutation before authority can be established" — not triggered by the preserved
  design itself, but SEQ-03 shows resolution being *asserted* before reconciliation,
  which is the same error one tier earlier.
- "duplicating the semantic interpreter" / "a second reconciler" — not triggered.
  The two `_clarification_decision` call sites (7502, 7672) are the same authority
  used for two distinct decisions (turn clarification; correction-conflict
  routing), passing different inputs. They are not duplicate interpreters. Both
  can reserve, so both must respect item 14.
- "changing dozens of tests without behaviour-family justification" — the 51 are
  justified by family in §10 and classified in §15; 23 of them are **not** test
  migrations and must be fixed in code.

## §20 SAFETY REVERIFICATION — AFTER

| Item | Before | After | Delta |
|---|---|---|---|
| HEAD | `5c3894c4…` | `5c3894c4…` | none |
| HEAD~1 | `66ec6c88…` | `66ec6c88…` | none |
| Remote tip | `161aa8d6…` | `161aa8d6…` | none |
| Ahead / behind | 26 / 0 | 26 / 0 | none |
| Tracked modifications | 0 | 0 | none |
| Stash entries | 0 | 0 | none |
| Preserved SHA-256 ×5 | `c07bb2de…` `d888e8c5…` `ae500817…` `f7fadd84…` `47e52ad9…` | identical | none |
| Containers | `2c06976a4034` / `43879bd4be63` / `7afa22b8a0a0` | identical | none |
| Restart counts | 0 / 0 / 0 | 0 / 0 / 0 | none |
| `GIT_SHA` | `840bce0` | `840bce0` | none |
| `OUTBOUND_ENABLED` | false | false | none |
| Correction flags | `G=[] P=[]` | `G=[] P=[]` | none |
| Deployed alembic head | `20260918_hybrid_traces` | `20260918_hybrid_traces` | none |
| Clarification table | absent | absent | none |
| `pending_correction` column | absent | absent | none |
| Business data | 47 / 34 / 1 / 35 / 734 / 2 | 47 / 34 / 1 / 35 / 734 / 2 | none |

Disposable artefacts: two archive trees under the session scratchpad. No git
worktree was registered, no container persisted, no image built.

## §21 FINDINGS REGISTER

| ID | Severity | Finding | Owner |
|---|---|---|---|
| SEQ-01 | HIGH | The preserved work adds **51** regression identities, not 41; no 41-identity artefact exists | this audit, recorded |
| SEQ-02 | **BLOCKER** | Clarification became a mandatory durable write on the primary text path; `reserve()` handles only `IntegrityError`, so other faults raise an uncaught `FlushError` and destroy the turn; incidence is order-dependent (≥6, up to 8 identities) | implementation milestone |
| SEQ-03 | **BLOCKER** | The decision runs 85 lines before reconciliation and 218 before canonical state, bypassing numeric-model disambiguation; "Focus 2008" is answered as a Peugeot 2008, reopening closed Wild defect WILD-04-R2/R3 | implementation milestone |
| SEQ-04 | HIGH | A delivered clarification Flow no longer sets its outstanding flag; the surviving gate Priority 4 can dispatch a second Location Flow; blocked-audit body attribution is wrong | implementation milestone |
| SEQ-05 | MEDIUM | Two cycle identities coexist; `_reconciler_cycle_id` still falls back to a timestamp | implementation milestone |
| SEQ-06 | MEDIUM | The patch's own placement comment misstates the evidence available at its insertion point | implementation milestone |
| SEQ-07 | LOW | 8 identities are count/freeze arithmetic | implementation milestone |
| SEQ-08 | LOW | A pre-existing `_FakeRepo` stub mismatch masks one wild04r result | implementation milestone |
| SEQ-09 | MEDIUM | 89 phrases across four frozensets still carry live routing authority; Priorities 1 and 3 remain phrase-driven after the selected change | recorded; typed replacement not in this gate |
| SEQ-10 | LOW | "One attempt per cycle" is in practice "one attempt per thread, ever" because no automatic cycle boundary exists | owner acknowledgement |

Positive findings, recorded so they are not lost: the preserved data layer is
sound in pattern and its 60-test suite passes with zero failures; the work genuinely
fixes 6 pre-existing failures (fuzzy-rejection evidence clearing, customer-origin
leakage); CL-04 is correctly closed; and the hybrid relevance rule
(`semantic OR deterministic`) is correctly implemented.

## §22 SEPARATED VERDICTS

| # | Subject | Verdict |
|---|---|---|
| V-01 | Baseline matches the required state before the audit | **PASS** |
| V-02 | Preserved-work manifest produced; files unmodified | **PASS** |
| V-03 | Handler execution map reconstructed from executable source | **PASS** |
| V-04 | Blocker arithmetic as stated in the brief (7502 / 7720, 218 lines) | **CORRECTED** — true of the patched tree; at HEAD it is 7305 / 7512, 207 lines |
| V-05 | "Reconciled current-turn evidence does not exist at the decision point" | **PARTIALLY FALSE** — interpreted evidence from both engines is available 751 lines earlier; only reconciled and committed state are genuinely absent |
| V-06 | Twelve evidence-boundary questions answered | **PASS** |
| V-07 | Three tiers distinguished with executable line evidence | **PASS** |
| V-08 | Deterministic engine can already emit typed current-turn evidence | **CONFIRMED** — built and unused at all four call sites |
| V-09 | Phrase list as bounded interpreter vs routing authority separated | **PASS** — 89 phrases hold live routing authority |
| V-10 | Semantic contract: one call per burst, sync consumption, bounded wait | **PASS** |
| V-11 | Degraded semantic evidence is represented as absence, never denial | **PASS** |
| V-12 | Regression register reproduced by identity | **PASS** |
| V-13 | The register contains 41 identities | **FAIL** — it contains 51 (SEQ-01) |
| V-14 | Behaviour families clustered with a shared behavioural claim | **PASS** — 6 families |
| V-15 | 28 of 51 are legitimate test migrations under the brief's rule | **PASS** |
| V-16 | The remaining 23 are legitimate test migrations | **FAIL** — 11 are defects, 6 are infrastructure-blocked, 4 need adjudication, 8 are count-only |
| V-17 | Preserved implementation is safe to apply as preserved | **FAIL** — SEQ-02 and SEQ-03 are blockers |
| V-18 | Preserved **design** is defective | **NO** — the design is sound; the insertion point is wrong |
| V-19 | A real-customer behaviour is regressed | **YES** — WILD-04-R2/R3 reopened (SEQ-03) |
| V-20 | One-flow-attempt lifecycle is intact end to end | **FAIL** — outstanding flag not set on dispatch (SEQ-04) |
| V-21 | Architectural options compared with a single recommendation | **PASS** — Option B selected |
| V-22 | Degraded-mode decision table specified (9 rows) | **PASS** |
| V-23 | Preserved database foundation is compatible | **CONDITIONAL PASS** — chain sound, two migrations pending, cycle key sound today; SEQ-05 and SEQ-10 recorded |
| V-24 | Bounded implementation boundary and exact file set defined | **PASS** |
| V-25 | 24-item acceptance contract defined | **PASS** |
| V-26 | No source, test, flag, container, deployment or business data was changed | **PASS** |

**AUDIT RESULT:** the hybrid-evidence-sequencing blocker is real but narrower than
recorded, and the selected architecture (Option B) resolves it by moving one call
site rather than redesigning the conversation engine. A phase redesign is not
required to proceed. The preserved work must not be applied as preserved: SEQ-02
and SEQ-03 are blockers, and SEQ-03 reopens a closed real-customer Wild defect.
No implementation commit exists, so no final independent implementation review is
recommended at this time.

NEXT: L4.7W5-GATE3-G3-7F-HYBRID-EVIDENCE-SEQUENCING-IMPLEMENTATION — OWNER AUTHORIZATION REQUIRED

STOP.
