PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-GATE3-G3-7F-HYBRID-EVIDENCE-SEQUENCING-IMPLEMENTATION

## §1 BASELINE — VERIFIED BEFORE WORK BEGAN

| Item | Expected | Found | Match |
|---|---|---|---|
| Branch | `fix/m21.1.1-primary-flow-regression` | same | YES |
| HEAD | `5c3894c484fff8233a3c308621b1132e1a2dda25` | same | YES |
| Parent | `66ec6c88991a7dfd781615978fde16b8e4e677b0` | same | YES |
| Remote | `161aa8d6573c8fb316922c51d9af3cf4616d0cc2` | same | YES |
| Ahead / behind | 26 / 0 | 26 / 0 | YES |
| Tracked modifications | clean | 0 | YES |
| Runtime `GIT_SHA` | `840bce0` | `840bce0` | YES |
| `OUTBOUND_ENABLED` | false | false | YES |
| Correction flags | unset | `G=[] P=[]` | YES |
| Deployed alembic | `20260918_hybrid_traces` | same | YES |
| `pending_correction` column | absent | absent (0) | YES |
| `whatsapp_clarification_attempts` | absent | absent (0) | YES |
| Business data | unchanged | 47 / 34 / 1 / 35 / 734 / 2 | YES |

Preserved material — all five SHA-256 prefixes matched the sequencing audit:
`c07bb2de…` `d888e8c5…` `ae500817…` `f7fadd84…` `47e52ad9…`.

## §2 COMMIT GRAPH

```
c797f73  feat(gate3): reconcile evidence before clarification routing   ← implementation
5e1dd20  docs(gate3): record clarification sequencing decision          ← evidence
5c3894c  docs(gate3): adopt clarification lifecycle architecture        ← prior HEAD
```

Exactly two local commits. Neither amended. Nothing pushed (ahead/behind 28/0).
The closeout you are reading is uncommitted, as instructed.

Commit 1 (`5e1dd20`) carries the two repository artifacts unchanged — the stopped
architectural-implementation closeout (419 lines) and the sequencing audit
(716 lines) — each verified to begin with its three literal artifact lines.

Commit 2 (`c797f73`): **35 files, +3,507 / −419.**

## §3 COMPLETE FILE LIST

| File | Δ | What |
|---|---|---|
| `backend/app/services/clarification_attempt.py` | +472 | recovered store + SEQ-02 typed outcomes |
| `backend/app/services/clarification_router.py` | +192 | recovered pure router + `TurnClarificationDecision` |
| `backend/migrations/versions/20261006_clarification_attempts.py` | +84 | the one additive migration |
| `backend/app/models.py` | +71 | `WhatsAppClarificationAttempt` |
| `backend/app/services/conversation_engine.py` | +850/−… | the sequencing change |
| `backend/app/ui/hybrid_trace_view.py` | +70 | Inspector 1.9 |
| `backend/app/schemas/hybrid_trace.py` | +11 | contract 1.9 |
| `backend/app/schemas/conversation.py` | +9 | two deliberate-silence outcomes as handled |
| `tests/test_l4_7w5_g3_7f_sequencing.py` | +609 | **64 new tests** |
| `tests/test_l4_7w5_g3_7f_clarification_lifecycle.py` | +740 | **61 recovered tests** (15 real PostgreSQL) |
| `tests/_clarification_store_double.py` | +124 | explicit injected attempt-store outcomes |
| 24 further test files | +−… | migrated legacy-mechanism assertions |

## §4 RECOVERED FOUNDATION MANIFEST

Recovered selectively; `implementation.patch` was **not** applied.

| Recovered unchanged | Recovered and corrected |
|---|---|
| migration, model, `cycle_key`, `idempotency_key` | `reserve` / `load` / `spent_families` / `transition` (SEQ-02) |
| `ClarificationFamily`, `AttemptStatus`, `ALLOWED_TRANSITIONS` | `_reconciler_cycle_id` (SEQ-05) |
| the pure router and its ordering | router ordering: policy 8 now outranks correction confirmation |
| `_complete_clarification_attempt` | duplicate detection (see §16) |
| the 61-test lifecycle suite | its assertions against the new typed API |

**Every known preserved-patch defect was avoided**, and each is proven absent by a
test in §12: router before reconciliation; catalogue re-resolution inside the
router; uncaught non-uniqueness database failures; successful dispatch not setting
the outstanding flag; dual cycle identities; the misleading placement comment;
legacy branches still able to dispatch; the Focus/Gol 2008 regression.

## §5 MIGRATION SCHEMA

`20261006_clarification_attempts`, parent `20261005_pending_correction`, additive
only, full downgrade, no row updates.

```
id, thread_id → whatsapp_threads(id), cycle_key int NOT NULL, family varchar(16),
idempotency_key varchar(36), status varchar(32), reserved_at timestamptz,
outbound_message_id → whatsapp_messages(id), wa_message_id, dispatch_result,
completion_result, resolved_family, terminal_reason, updated_at        (14 columns)

UNIQUE (thread_id, cycle_key, family)      ← the invariant
UNIQUE (idempotency_key)
CHECK  family IN ('VEHICLE','LOCATION')
CHECK  status IN (8 lifecycle states)
INDEX  ix_clarification_attempt_thread_cycle
```

## §6 TRANSACTION BOUNDARIES

The reservation commits in its **own session**, derived from `db.bind`, before any
transport call — the `OutboundSafetyGate` pattern, for the same reason: a Flow that
may already have reached the customer must never be un-reserved by a later failure
of the business transaction. A savepoint would not do; an outer rollback after
dispatch would erase it and the customer would be asked twice. Proven against real
PostgreSQL (§13, test `cl_27`).

## §7 EXACT EXECUTION SEQUENCE

Mandated order (§8 of the brief), as implemented and asserted structurally:

| # | Step | Where |
|---|---|---|
| 1 | canonical burst assembled | `_canonical_turn_burst` |
| 2 | semantic interpretation dispatched **once** | `_run_shadow_understand` |
| 3 | typed deterministic observations | `lookup_vehicle` → `pre_detected_vehicle` |
| 4 | admitted semantic evidence | `_semantic_turn_evidence` (bounded wait) |
| 5 | **reconciliation** | `_reconciled_reply_proposals` → `_cand_data` |
| 6 | reconciled proposed facts | identity / category / locality read from `_cand_data` |
| 7 | **ONE typed clarification decision** | `_turn_clarification_decision` |
| 8 | attempt reserved if required | `_execute_clarification` only |
| 9 | authorized canonical mutation | `_apply_candidate` (the existing writer) |
| 10 | compose and dispatch | certified `_dispatch_*_clarification_flow` |

Ordering is proven from executable control flow, never from a comment:
`test_seq_03` and `test_seq_04` assert `reconcile < cand < decision < apply_candidate`
by source index, and `cl_34` asserts the same from the opposite direction.

## §8 DECISION CALL COUNT

**Exactly one per turn.** The decision is memoized on
`_turn_clarification_decision_obj` and counted on
`_turn_clarification_decision_count`, which the trace carries as `decision_count`.

- `test_seq_05` — exactly one decision call site and one executor call site in the engine.
- `test_seq_06` — an AST walk proves **no method other than `_execute_clarification`
  calls `.reserve(`**. There is one reservation authority by construction.
- The correction-conflict branch no longer routes independently: owner policy 8 is
  folded into the single decision (the router returns `GENERIC_HUMAN_REQUIRED` for a
  correction competing with another live confirmable proposal), so the second call
  site the audit found is gone rather than duplicated.

## §9 RECONCILED-PROPOSAL CONTRACT

`identity_resolved`, `category_resolved` and `location_resolved` are derived from
the reconciled proposal plus already-committed canonical state. `pre_detected_vehicle`
contributes to **whether** the identity is resolved, never to **what** it is — the
reconciled proposal remains the sole source of any value the customer is told, which
is what keeps "Focus 2008" a Ford Focus.

The router does not: query the catalogue, perform numeric-model disambiguation,
re-run semantic interpretation, parse prose, create a candidate, or write canonical
state. Each is asserted on docstring-stripped source (`test_seq_12` … `test_seq_14`).

## §10 DATABASE-FAILURE HANDLING (SEQ-02)

Three outcomes, never two:

| Outcome | Meaning | Consequence |
|---|---|---|
| `WON` | this session's INSERT created the row | dispatch |
| `ALREADY_ATTEMPTED` | uniqueness conflict **and the conflicting row was found** | wait if outstanding, else a person |
| `STORE_UNAVAILABLE` | the store could not answer | no Flow, no claim, no flag, a person |

- A non-conflict integrity error (FK, CHECK, NOT NULL) returns
  `STORE_UNAVAILABLE / INTEGRITY_NOT_CONFLICT` — **never** "already attempted".
- Flush, commit, connection, missing-table and unexpected failures are classified
  into typed, credential-free faults. Only `type(exc).__name__` is ever logged; a
  driver message that could carry a DSN never escapes (`test_seq_34`).
- Nothing raises out of `reserve`, `load` or `spent_families`. An illegal transition
  still raises `ValueError`, because that is a programming error, not infrastructure.
- No automatic retry inside the turn (`test_seq_36`).
- The store is read **only when a family is both relevant and unresolved**, so an
  ordinary resolved turn performs no clarification round trip at all.

**Measured: `FlushError` raised 0 times across the full suite** (it was 94 before the
fix, every one of which destroyed a turn).

## §11 ROUTER INPUTS AND OUTCOMES

Fourteen boolean inputs, no prose (`test_seq_09`, `test_seq_10`). Outcomes:
`NO_CLARIFICATION_REQUIRED`, `CORRECTION_CONFIRMATION_REQUIRED`,
`VEHICLE_FLOW_REQUIRED`, `LOCATION_FLOW_REQUIRED`, `GENERIC_HUMAN_REQUIRED`.

Relevance rule: `admitted semantic evidence OR typed deterministic evidence OR
authorized required-next-information`. Stored incompleteness is never relevance.

The degraded-mode table (§12 of the brief) is implemented and each of its nine rows
has its own test (`test_seq_15` … `test_seq_23`): semantic silence never denies what
the deterministic engine saw, and deterministic silence never vetoes admitted
semantic evidence.

One bounded exception is recorded honestly: when semantic evidence is absent, a
typed marker set by the gate's existing FAQ/soft-close detector can **decline** to
treat a turn as service progression. The phrase set is used in one direction only —
it can never authorize asking the customer anything — and it exists to preserve owner
policy 2.8 (a payment question must not become a location form because an old
candidate lacks a zone). It is deferred debt, not architecture (§21, SEQ-09).

## §12 DISPLACED LEGACY BRANCHES

All five retired, structurally proven:

| # | Branch | State |
|---|---|---|
| 1 | `_routing_gate` Priority 2 (concern Location Flow) | removed |
| 2 | `_routing_gate` Priority 4 (standard Location Flow) | removed |
| 3 | `_routing_gate` Priority 5 (Vehicle Flow on price intent) | removed |
| 4 | `_check_fallback_flow_triggers` vehicle branch | removed |
| 5 | `_check_fallback_flow_triggers` location branch | removed |

`_check_fallback_flow_triggers` now returns `None` and nothing else; both methods
contain no dispatcher call and no flag write (`test_seq_50` … `test_seq_52`, `cl_36`).
Priority 1 (outside coverage) and Priority 3 (FAQ → reply model) survive; neither can
dispatch a clarification Flow.

Early kill-switch exits fell from 7 to 3 — four lived inside the retired two-step.
The invariant is unchanged: every surviving exit names its authorized path and derives
its transport from it.

## §13 TESTS RUN

**Sealed runner** (no network, SQLite in memory):
```
docker run --rm --network none -e DATABASE_URL="sqlite://" -e PYTHONDONTWRITEBYTECODE=1 \
  -v /opt/ridecheck-crm-release-candidate:/work:ro -w /work/backend \
  ridecheck-crm-test:l4.7b2 python -m pytest /work/tests -q -rA -p no:cacheprovider
```

**Real PostgreSQL** (disposable container, removed afterwards) — 15/15 pass, including
`cl_25` two concurrent sessions and `cl_26` ten concurrent sessions, each producing
exactly one winner. SQLite was **not** used as concurrency evidence anywhere.

New: 64 sequencing tests, 61 lifecycle tests, 1 store double. All pass.

Coverage against the brief's §21 list: sequencing 1–6 ✔; semantic/deterministic 7–14 ✔;
real-customer 15–18 ✔ (19 covered by the m20_6d2 suite, blocked — see §19);
attempt lifecycle 20–30 ✔ (real PostgreSQL); transport 31–37 ✔; authority 38–45 ✔;
observability 46–51 ✔.

**§13A** (customer-requested vehicle and location changes) arrived mid-milestone. Its
typed foundation is in place and tested — a fully resolved change routes to
`CORRECTION_CONFIRMATION_REQUIRED` and never to a Flow (`test_seq_26`); an ungrounded
target never reaches confirmation (`test_seq_28`); a competing proposal goes to a
person (`test_seq_27`); Focus/Gol 2008 are protected (`test_seq_12`, wild04r). Its
remaining 15 end-to-end scenarios (dependent-state invalidation, new-candidate focus,
the booking boundary) are **not** delivered and are listed in §22.

## §14 CHANGED TESTS AND CLASSIFICATIONS

| Group | Count | Classification | What moved |
|---|---|---|---|
| M18 two-step trigger + screens + prod regressions | 17 | legacy mechanism | flag → attempt row; two-step → single Flow |
| Owner-resolution routing | 21 | legacy mechanism | `_route_clarification` → `_turn_clarification_decision`; relevance stated |
| Trace contract pins | 17 | wording/count | 1.8 → 1.9 (equality pins only; readable lists untouched) |
| Migration / consumer counts | 4 | wording/count | +1 migration, +1 trace consumer |
| Blocked-exit inventories | 3 | wording/count | 7 → 3 |
| Harness stubs (wild03, m21_1_1, Turn, owner-resolution) | 4 files | infrastructure | stub follows the authority; explicit attempt store |
| `voiceloc` cycle identity + region boundary | 2 | legacy mechanism | string cycle → integer; "Priority 2" → "Priority 3" |
| `m20_2` RC47b, `m20_4_3` test_3 | 2 | legacy mechanism | blocked text step → blocked handoff; invariant kept |
| `_FakeRepo` signature | 1 | infrastructure | narrow fix, test not weakened |

**No real-customer test was deleted or weakened.** No customer-visible outcome was
changed to make a test pass; where one would have had to change, the milestone stopped
(§19).

## §15 REAL-CUSTOMER PRESERVATION PROOF

All five PRESERVE identities pass, unweakened:

| Identity | Status |
|---|---|
| `wild04_resolver_focus_2008_not_numeric_path` | PASS |
| `wild04_resolver_gol_2008_not_numeric_path` | PASS |
| `returning_obs_2_issue7_focus_pilar_new_candidate` | PASS |
| `rc18_outbound_blocked_candidate_persists_flag_false` | PASS |
| `rc19_successful_delivery_sets_flag_and_dedup_blocks_replay` | PASS |

"Focus 2008" stays a Ford Focus of year 2008 and "Gol 2008" a Volkswagen Gol: the
BLOCKER the audit measured (SEQ-03) is closed. The six baseline failures the preserved
work fixed remain fixed.

## §16 COMPLETION LIFECYCLE

Both handlers derive the cycle key, load the attempt, and record a typed outcome
before the outstanding flag is cleared. Three corrections to the preserved design:

1. `TERMINAL_FOR_AUTOMATION` governs whether a family may be **asked again**; it is no
   longer the duplicate test for a **completion**. `RESERVED` is terminal for asking
   (the process may have died after reserving) but a customer who submitted the form
   plainly received it — discarding their data because our dispatch record is missing
   would be the wrong way round.
2. A completion for a `RESERVED` attempt infers the dispatch
   (`dispatch_result=INFERRED_FROM_COMPLETION`) before completing.
3. A completion for `DISPATCH_BLOCKED` / `DISPATCH_FAILED` is recorded as
   `UNSOLICITED` — we have no evidence the Flow ever reached the customer, so
   rewriting a blocked dispatch as a completion would falsify outbound history — but
   the customer's data is not discarded.

Vehicle-then-Location ordering works through two independent family attempts, not a
sequencing boolean. The attempt row is never deleted; clearing the flag can never make
an attempt available again.

## §17 TRACE 1.9 AND INSPECTOR

Contract `hybrid-decision-trace/1.9`; 1.8 remains in `READABLE_VERSIONS` and renders.
Added inside the existing `clarification` block (additive — no 1.8 field changed
meaning): `attempt_id`, `cycle_key`, `family`, `attempt_status`, `reservation`,
`already_attempted` (three states), `store_available`, `store_fault`,
`dispatch_result`, `outbound_message_id`, `completion_result`, `resolved_family`,
`prevented_second`, `decision_count`, and four producer-attribution fields.

Dispatch turns, completion turns and prevented-second turns join on `attempt_id`, which
is what makes completion rate measurable rather than merely visible. `planned` is not
`dispatched`; `BLOCKED`, `FAILED` and `DISPATCHED` are distinct. No phone number, no raw
prose, no secret, and no full WAMID reaches the block (`test_seq_61`).

The Inspector separates semantic evidence, deterministic evidence, reconciliation, the
clarification decision, the transport result and the completion result into labelled
Spanish rows. The 1.9 rows render only when the payload carries them, so a stored 1.8
trace shows what it has rather than a row of em-dashes that reads as lost data.

## §18 MIGRATION PROOF

Disposable PostgreSQL 16 container, removed afterwards. **The deployed `crm_test`
database was not touched** — it remains at `20260918_hybrid_traces` with neither object.

| Step | Result |
|---|---|
| 1. state immediately before `20261005_pending_correction` | reproduced (schema built, both objects dropped, stamped) |
| 2. apply `20261005_pending_correction` | OK |
| 3. apply `20261006_clarification_attempts` | OK, head |
| 4. verify columns / constraints / indexes | 14 columns, 7 constraints, 4 indexes — all as specified |
| 5. reservation + concurrency tests | 15/15 pass, including 2- and 10-session races |
| 6. downgrade clarification only | OK |
| 7. `pending_correction` survives | **yes** (column present, head = `20261005_pending_correction`) |
| 8. re-upgrade | OK |
| 9. schema identical | **280 columns identical**; all 7 constraints restored |

## §19 REGRESSION IDENTITY COMPARISON

Identities, not counts. Baseline at `5c3894c` / `66ec6c8`: **67** (58 FAILED + 9 ERROR).

| | Count |
|---|---|
| **Added** | **9** |
| **Removed (baseline failures now fixed)** | **6** |
| `FlushError` raised | **0** |

**Removed — all six retained:** `test_rejection_clears_pending_key`,
`test_rejection_clears_pending_text`, `test_rejection_no_candidate_created`,
`test_rejection_zone_not_promoted`, `test_customer_origin_not_written_to_state_home`,
`test_rejection_and_new_vehicle`.

**Added — all nine, and all one cause:**

```
test_m18_business_logic.py::TestProdRegressionLocationWording::test_location_clarification_wording_covers_gba
test_m20_6d2_customer_reality.py::TestRC04ConcernDirectFlow::test_rc04_concern_dispatches_location_flow_directly
test_m20_6d2_customer_reality.py::TestRC06GenericAutoDirectFlow::test_rc06_generic_auto_price_question_dispatches_vehicle_flow_directly
test_m20_6d2_customer_reality.py::TestRC07RangerSanFranciscoSolano::test_rc07_ranger_known_no_zone_dispatches_location_flow
test_m20_6d2_customer_reality.py::TestRC08GarbledDirectFlow::test_rc08_garbled_vehicle_dispatches_vehicle_flow_directly
test_m20_6d2_customer_reality.py::TestRC12aBenavidesDirect::test_rc12a_benavides_typo_dispatches_location_flow_directly
test_m20_6d2_customer_reality.py::TestRC14FAQWithKnownVehicle::test_rc14_price_question_after_faq_dispatches_location_flow
test_m20_6d2_customer_reality.py::TestRC16PriceWithKnownVehicleDirectFlow::test_rc16_bare_vehicle_message_dispatches_location_flow
test_m20_6d2_customer_reality.py::TestRC16PriceWithKnownVehicleDirectFlow::test_rc16_known_vehicle_price_question_dispatches_standard_location_flow
```

In all nine the clarification **decision, reservation and dispatch are correct** —
verified in the logs: route chosen, attempt reserved, Flow dispatched. What differs is
the **body the Flow carries**.

### STOP-01 (§24) — OWNER WORDING DECISION REQUIRED

Two owner-authored bodies exist for the same Flow:

| Constant | Text | Written for |
|---|---|---|
| `_LOC_FLOW_BODY_STANDARD` | "Para calcular los viáticos de la revisión, completá dónde está el auto." | the legacy priority ladder |
| `_LOCATION_CLARIFICATION_INTRO` | "Para calcular correctamente el traslado, te dejo un formulario breve para indicarnos dónde está el vehículo." | the typed router (owner resolution, Part E) |

The legacy dispatcher's own comment states the split: *"the composer owns the wording
when the router dispatches; the legacy priority ladder keeps its own certified bodies."*
Retiring the ladder (§14, mandated) makes the router's body universal — which changes
the sentence every clarified customer receives. Two owner-certified suites pin different
bodies, and `_LOC_FLOW_BODY_CONCERN` (the concern-aware variant, Priority 2) now has no
successor at all.

Separately and independently: the **GBA coverage signal** ("Tigre", "Lomas de Zamora")
existed **only** in the retired free-text clarification. Neither Flow body mentions the
coverage area, so a customer in Greater Buenos Aires is no longer told they are covered.
This is a content gap created by retiring the text step, not by choosing a body.

§24 forbids improvising customer-visible behaviour, so no third wording was invented and
no assertion was weakened. The owner decides: (a) which body the single clarification
Flow carries, (b) whether the concern-aware variant survives, (c) whether the GBA
coverage signal is restored into that body. All nine identities close on that one answer.

## §20 SAFETY / NO-DELTA PROOF

| Item | Before | After | Delta |
|---|---|---|---|
| Branch / ahead-behind | `fix/m21.1.1…` 26/0 | same, 28/0 (two new local commits) | intended |
| Uncommitted tracked changes | 0 | 0 | none |
| Stash entries | 0 | 0 | none |
| Runtime `GIT_SHA` | `840bce0` | `840bce0` | none |
| `OUTBOUND_ENABLED` | false | false | none |
| Correction flags | `G=[] P=[]` | `G=[] P=[]` | none |
| Container ids | `2c06976a4034` / `43879bd4be63` / `7afa22b8a0a0` | identical | none |
| Restart counts | 0 / 0 / 0 | 0 / 0 / 0 | none |
| Deployed alembic | `20260918_hybrid_traces` | `20260918_hybrid_traces` | none |
| Clarification table deployed | absent | absent | none |
| `pending_correction` deployed | absent | absent | none |
| Business data | 47 / 34 / 1 / 35 / 734 / 2 | 47 / 34 / 1 / 35 / 734 / 2 | none |

No model call, no Meta call, no WhatsApp send, no tester reset, no n8n mutation, no
deployed migration, no business-data write, no build, no deployment, no container
recreation, no Wild, no push. The disposable PostgreSQL container was removed. No
credential, phone number, secret or full WAMID appears in any artifact.

## §21 SEPARATED VERDICTS

| # | Subject | Verdict |
|---|---|---|
| 1 | Documentation adoption | **PASS** — both artifacts committed unchanged in `5e1dd20` |
| 2 | Preserved-foundation recovery | **PASS** — selective; patch not applied; all five hashes verified |
| 3 | Migration correctness | **PASS** — additive, parented, full downgrade, no row updates |
| 4 | Atomic reservation | **PASS** — INSERT-as-reservation, own transaction, committed before transport |
| 5 | Database-failure safety | **PASS** — three outcomes; 0 raised `FlushError`; fail-closed |
| 6 | Cycle identity | **PASS** — one definition; timestamp fallback removed |
| 7 | Correlation-key schema fit | **PASS** — 36 chars, fits both `correlation_id` columns |
| 8 | Semantic evidence availability | **PASS** — dispatched once, consumed with a bounded wait |
| 9 | Deterministic evidence availability | **PASS** — typed, and turn text now passed where it matters |
| 10 | Reconciliation-before-routing | **PASS** — proven by source index, both directions |
| 11 | Reconciled-proposal resolution | **PASS** — no catalogue call inside the router |
| 12 | Exactly-one decision per turn | **PASS** — memoized, counted, traced; one reserving authority by AST proof |
| 13 | Flag independence | **PASS** — spent (row) and outstanding (flag) never substituted |
| 14 | Semantic-only behavior | **PASS** |
| 15 | CE-only behavior | **PASS** |
| 16 | Degraded semantic mode | **PASS** — all nine rows tested |
| 17 | Conflict / HOLD behavior | **PASS** — policy 8 outranks correction confirmation |
| 18 | Domain relevance | **CONDITIONAL PASS** — typed, except the one-directional FAQ suppressor (SEQ-09 debt) |
| 19 | Vehicle Flow selection | **PASS** (selection); body **BLOCKED** — STOP-01 |
| 20 | Location Flow selection | **PASS** (selection); body **BLOCKED** — STOP-01 |
| 21 | Vehicle-then-Location ordering | **PASS** |
| 22 | Outstanding / attempt separation | **PASS** |
| 23 | Successful-dispatch state | **PASS** — flag only after a proven `wa_message_id` |
| 24 | Blocked / failed-dispatch state | **PASS** — distinct statuses, flag stays false |
| 25 | Completion lifecycle | **PASS** — with the three corrections in §16 |
| 26 | Generic human handoff | **PASS** |
| 27 | No-false-MOTO invariant | **PASS** — neither decision nor executor mentions MOTO |
| 28 | Focus / Gol 2008 protection | **PASS** — both unweakened |
| 29 | Exactly-one response | **PASS** — the Flow body is the explanation; no duplicate text |
| 30 | Legacy clarification authority removal | **PASS** — all five branches, structurally proven |
| 31 | Trace 1.9 truth | **PASS** — additive; lifecycle joinable; 1.8 renders |
| 32 | Inspector auditability | **PASS** — six readings separated |
| 33 | Test-migration credibility | **PASS** — every change classified in §14; none weakened |
| 34 | Regression identity comparison | **CONDITIONAL PASS** — 9 added, all one cause; 6 fixed retained |
| 35 | Single-writer integrity | **PASS** — one reconciler, one candidate writer on this path |
| 36 | No-phrase-catalogue integrity | **CONDITIONAL PASS** — no phrase added; 89 inherited phrases remain, none able to ask |
| 37 | Implementation commit | **PASS** — exactly one, not amended, not pushed |
| 38 | Accumulated G3-7F range | **CONDITIONAL PASS** — SEQ-02/03/04/05/06 closed; STOP-01 open |
| 39 | Readiness for final independent review | **READY**, with STOP-01 declared |
| 40 | Readiness for G3-8 | **NOT READY** — R2R-07 untouched by design |
| 41 | Gate 3 | **NOT CLOSED** — STOP-01 outstanding |
| 42 | Deployment readiness | **NOT READY** — two migrations pending, no image built |
| 43 | Wild readiness | **NOT READY** — outbound off, nothing deployed |

## §22 LIMITATIONS AND DEFERRED FINDINGS

1. **STOP-01** (§19) — the clarification Flow body and the lost GBA coverage signal.
   Nine identities. Owner decision.
2. **§13A end-to-end scenarios** — the typed foundation is in place and unit-proven, but
   the 15 real-handler scenarios (dependent quote/viáticos invalidation, new-candidate
   focus, ambiguous-target HOLD, the existing-booking boundary) are **not** delivered.
   §13A arrived after implementation was under way and is larger than this milestone's
   bounded file set.
3. **SEQ-09 debt** — Priority 1 and Priority 3 remain phrase-driven. Neither can ask the
   customer anything, and the FAQ detector is used only to decline; replacing both with
   typed evidence is not in this milestone.
4. **SEQ-10** — with no automatic cycle boundary, "one attempt per cycle" is in practice
   one attempt per thread until an operator reset. Every live thread is cycle `0`.
5. **R2R-07 / G3-8** — a correction after a booking can still leave `ThreadRevision` or
   CRM `Revision` stale. Untouched, as instructed; `g3_8_enforced` is pinned `False`.
6. **`pending_correction.route_clarification`** — the superseded pure router is still in
   the tree. CE no longer calls it (`test_seq_54`); removing it would break owner-certified
   tests of its own vocabulary and add identities outside the sanctioned set.
7. **Two migrations pending deployment** — `20261005_pending_correction` then
   `20261006_clarification_attempts`. Neither applied to `crm_test`.
8. Pre-existing untracked probe scripts at the repository root were left untouched and
   deliberately not committed.

NEXT: FINAL INDEPENDENT REVIEW OF G3-7F HYBRID-EVIDENCE SEQUENCING IMPLEMENTATION

STOP.
