PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-R6-C4B-R1B-DEFERRED-INTEREST-SEMANTICS

# R6-C4B-R1B — Deferred-interest semantics

Worktree: `/opt/ridecheck-crm-r5-f1-response-validation`.
Evidence: `B=/opt/ridecheck-builds/crm-r6-c4b-r1b-semantics-20261115T154556Z`.
Parent: R6-C4B-R1A, `A1=/opt/ridecheck-builds/crm-r6-c4b-r1a-deferred-20261115T025435Z`.
Timebox: 60 min, used 15:44Z → about 16:10Z.

The R1A closeout has a labelled R1B addendum. Its original evidence and verdict are unchanged.

## 1. Root cause

R1A made the M21.1.6 deferred branch complete, which it had never done before. Three things then went wrong on that turn:

1. **Appended question (R1A-O1).** `_handle_deferred_interest` sends through `_send_service_boundary`, then `_send_text_to_wa`. That send path runs the G3-6 producers (`_register_response_propositions`) before the plan composes. On a QUALIFYING thread whose focus candidate has `tipo_vehiculo` and no zone, the clarification producer registers the canonical location question. With `OUTSTANDING_QUESTION_ENABLED=true`, delivery also opens an `INSPECTION_LOCATION` outstanding question. Nothing told the producers that the turn was a terminal deferral.
2. **Wrong copy for a known car (R1A-O2).** There was a single copy, `DEFERRED_RESPONSE_ES`, written for customers with no vehicle in view, so it was sent even when canonical state already held a focus vehicle.
3. **Wrong attribution (R1A-O4).** `_process_text` sets `_turn_renderer_source = REPLY_MODEL` and `LEGACY_REPLY_MODEL_EXTRACTION` when the interpreter's reply is admitted. That happens before the deferred intercept, and nothing reset it.

## 2. Exact production diff (`B/r1a_to_r1b.prod.diff`; `conversation_engine.py` only)

```diff
@@ -2122,6 +2122,10 @@
 _CANONICAL_LOCATION_ASK = "\n\n¿En qué localidad o barrio está el auto?"
+#: R6-C4B-R1B: the deferred-interest copy when canonical state already holds a focus vehicle
+#: (the no-focus copy is `narrative_schema.DEFERRED_RESPONSE_ES`). No vehicle text is inserted.
+_DEFERRED_RESPONSE_FOCUS_ES = (
+    "Perfecto, pensalo tranquilo. Cuando quieras retomar la revisión de ese auto, escribinos.")
@@ -3482,6 +3486,9 @@   (per-turn initialisation in handle())
         self._turn_clarification_burst = None
+        # R6-C4B-R1B: this turn is a terminal deferred-interest turn (set only by
+        # `_handle_deferred_interest`); reset every turn so it cannot leak.
+        self._turn_deferred_terminal = False
@@ -15500,8 +15507,18 @@
     def _handle_deferred_interest(self, ctx, state) -> "ConversationHandleOut":
-        """NU-6: send approved deferred-interest copy; no commercial mutation."""
-        return self._send_service_boundary(ctx, DEFERRED_RESPONSE_ES)
+        """…docstring…"""
+        self._turn_deferred_terminal = True
+        self._turn_renderer_source = RendererSource.DETERMINISTIC_TEMPLATE
+        self._turn_decision_authority = DecisionAuthority.DETERMINISTIC_RULE
+        has_focus = self._focus_candidate(ctx, repair=False) is not None
+        return self._send_service_boundary(
+            ctx, _DEFERRED_RESPONSE_FOCUS_ES if has_focus else DEFERRED_RESPONSE_ES)
@@ -16959,6 +16976,10 @@   (_register_response_propositions)
         if plan is None:
             return
+        if getattr(self, "_turn_deferred_terminal", False):
+            # R6-C4B-R1B: the customer is postponing. The deferred copy is the whole turn —
+            # no clarification, missing-data, price, FAQ or vehicle proposition joins it.
+            return
```

An AST function-level comparison (`B/function_diff.txt`) finds exactly three changed functions:
- `_handle_deferred_interest`;
- `_register_response_propositions`;
- `handle`, for the per-turn reset line.

It also finds one new constant, `_DEFERRED_RESPONSE_FOCUS_ES`. These are byte-identical to R1A:
- `_send_booking_flow`, `_process_flow_response`, `_converged_flow_book`;
- `_send_with_bridge`, `_approved_bridge`;
- `_register_clarification_proposition`, `_focus_candidate`;
- `_send_service_boundary`, `_send_text_to_wa`.

Test-side changes:
- new `tests/test_r6_c4b_r1b_deferred_semantics.py`, 7 tests;
- `tests/test_r6_c4b_r1a_deferred_interest.py`: two assertions updated, test_01's expected copy and test_06's source pin;
- `tests/test_r5_f1_template_validation_contract.py`: the new constant is registered in the customer-facing inventory as `FULL`, so it is validated in full by that suite.

## 3. Focus / no-focus behaviour

| Canonical state | Delivered (exactly one message) |
|---|---|
| A focus vehicle exists (`state.current_focus_candidate_id`, or the documented single-candidate fallback) | `Perfecto, pensalo tranquilo. Cuando quieras retomar la revisión de ese auto, escribinos.` |
| No focus vehicle | `Perfecto, cuando tengas algún auto en vista escribinos y te ayudamos con la revisión.` |

The decision is `_focus_candidate(ctx, repair=False)`, the existing canonical mechanism, called read-only so the CL-05 repair write cannot run. It never reads customer prose. Neither copy contains vehicle text, a brand, model, year, location, price, date, time or any question.

**Pre-existing routing, not changed.** On a thread with **no** candidate, the exact Q3 text "no se bien todavia, lo estoy pensando" never reaches the interpreter. It matches no Layer F step-8.5 deferred pattern, so the pre-existing step-9 service greeting answers it. Matrix B therefore uses a message that does reach the deferred branch: "por ahora estoy mirando, todavía no tengo ningún auto en vista".

## 4. Question-suppression mechanism

`_turn_deferred_terminal` is an engine attribute with these properties:
- It is initialised to `False` in the per-turn block of `handle()`, beside the other `_turn_clarification_*` fields that run before anything can dispatch.
- It is set to `True` only by `_handle_deferred_interest`.
- It is read only by `_register_response_propositions`, which then registers no proposition, so the plan composes the deferred copy alone.

Because the flag is reset at every turn entry, it cannot leak into a later turn. Matrix C proves a normal turn on the same world still asks, and asserts the flag is `False`.

Since no producer runs, no outstanding question is opened under either switch posture. The vehicle producer's pending-vehicle arming is also not reached, so no state changes. The general clarification engine itself (`_register_clarification_proposition` and the producers) is unchanged.

## 5. Attribution correction

On the deferred turn:
- `answer_source = DETERMINISTIC_RULE`, unchanged, set by `_send_service_boundary`;
- `_turn_renderer_source = DETERMINISTIC_TEMPLATE`; R1A left it `REPLY_MODEL`;
- `_turn_decision_authority = DETERMINISTIC_RULE`.

`ai_invoked` stays `True` because the interpreter really was called once, and its general meaning is unchanged. The interpreter's reply body is never delivered. The test body contains "Te espero el lunes a las 10:00, ya quedó reservado." and "¿En qué barrio está?", and neither appears.

## 6. Test matrix (`tests/test_r6_c4b_r1b_deferred_semantics.py`)

Every turn runs on a **frozen Buenos Aires business date**, Wed 2026-11-11 12:00 ART. The freeze covers the engine's `date.today()` and `datetime.now()` as well as the validator's `_utcnow`, so R1A-O3 cannot contaminate the result. Each test drives the real route, `POST /api/conversation/handle`, on in-memory SQLite with recorded Meta.

| Case | Proven |
|---|---|
| **A** focus vehicle exists, no zone, Q3; `OUTSTANDING_QUESTION_ENABLED` false and true | 1 reply; the exact focus copy; no "cuando tengas algún auto en vista"; no "?" and no location question; 0 outstanding questions in both postures; generator 0 and verifier 0 calls, no bridge; 0 revisions/offers/Flows, token None; stage, flag, estado, candidates, focus, zone and needs_human unchanged; preferred day/time None; attribution as in §5; retry `skipped_dedup` with state byte-equal |
| **B** no candidate, no focus, deferred no-vehicle message; both postures | 1 reply; the exact no-focus copy; nothing appended; 0 outstanding questions; no mutation or Flow; deterministic attribution; dedup |
| **C** the same incomplete world, `deferred_interest=false` (the R1 real-provider ordinary QUALIFYING turn, "Dale, gracias por responder") | the canonical location question is still composed; with the switch true, 1 outstanding question is still recorded; switch false, 0; the flag is `False`; no Flow |
| **D** needs_human active, focus and no-focus | `skipped_human`; 0 sends; interpreter not called; state unchanged except the cursor |
| **E** R1A contract (`tests/test_r6_c4b_r1a_deferred_interest.py`, 6) | `ConversationHandleOut`, no crash, latency fields, outbound gate `CE_TEXT`, all eight switches false, no generated state, dedup: all pass |
| **F** architecture pins | §8 |

Candidate result: **13 / 13 pass** (R1B 7, R1A 6), recorded in `B/junit/set_cand2.xml`.

## 7. Negative control (`B/negctl`, `B/junit/r1b_negctl.xml`, `B/negctl.txt`)

`B/negctl` is a scratch copy whose `conversation_engine.py` is the exact R1A engine, sha256 `bc1f33c7…` = the R1A pin, rebuilt from the R1 seal plus the R1A line. The candidate was not altered. Running the R1B tests on it gives **4 failed, 3 passed**:
- **A, both postures:** they deliver the old contradictory message, `'Perfecto, cuando tengas algún auto en vista escribi[75 chars]to?'`. That is the no-focus copy plus "⏎⏎¿En qué localidad o barrio está el auto?", so a known car gets the wrong copy and the location question.
- **B, both postures:** `'REPLY_MODEL' != 'DETERMINISTIC_TEMPLATE'`, the attribution defect.
- **C and D pass on R1A as well.** So the normal clarification and the needs_human short-circuit are unchanged by R1B.

## 8. Focused results and architecture hashes

The focused set (`B/set.list`, 48 files) is:
- the R1A set of 42 files: the deferred-interest suites, the CE contract and observability suites, the R1 bridge suite, c4a and the Booking Flow authority list;
- the 6 direct clarification and outstanding-question composition suites: `g3_5f_bounded_correction`, `g3_5f_structured_response_composition`, `g3_6_bounded_correction`, `g3_6_proposition_migration`, `g3_7f_clarification_lifecycle`, `g3_7l_clarification_activation`;
- the R1A and R1B files on the candidate.

`w1f_w01_r2_outstanding_question` was already in the R1A set. Motorcycle/handoff code is not imported by the changed path; the Booking Flow list covers handoff dispatch.

| Run | Tree | Cases | Failing |
|---|---|---|---|
| `set_base_full.xml` | full-tree R1A copy (`B/r1a_full`: worktree + R1A engine + R1A inventory test) | 1742 | 10 |
| `set_cand2.xml` | R1B worktree | 1755 | 10 |

**`B/set2.cmp.txt`: NEW 0, GONE 0, COMMON 10, COMMON_changed 0; 13 cases added.** The 10 COMMON failures are the same identities as R1A §5: BF30 ×2, `w4f1_12`, `caseC_requested_day`, `obs_04`, `obs_04b`, and the 4 clock-dependent T04/P04 cases.

**Superseded first comparison.** `B/set.cmp.txt` was run against a scratch copy containing only `backend/` and `tests/`. It showed GONE 5 and NEW 1:
- the GONE 5 were doc-reading tests failing in that copy for lack of `docs/` and `CLAUDE.md`;
- the NEW 1 was the inventory test before the new constant was registered.

Both were resolved: the full-tree baseline replaced the partial copy, and the constant was registered. It is kept for the record.

**App manifest** (`B/r1b.app.manifest`, 139 files, the same file set): it differs from R1A in one path only, `services/conversation_engine.py`. Identical to R1A (sha256 prefix):

| File | sha256 prefix |
|---|---|
| `services/verified_generation.py` (bridge generator-5 / verifier-4 prompt constants, policy, budget) | `c7a5b1827db16779` |
| `services/response_validator.py` | `e3a060afe07973b6` |
| `services/booking_flow_service.py` | `43eba1d9233fd436` |
| `services/booking_authority.py` | `f4977657a0e318c4` |
| `services/booking_dispatch_gate.py` | `68ecf4e5147d769b` |
| `services/booking_flow_details.py` | `81efb3479e393b58` |
| `routes/flow_data_exchange.py` | `8eb9756d16019053` |
| `services/pricing.py` | `73bff8bcca8c933d` |
| `services/schedule.py` | `bb5e23a1c77ae913` |

The CE-side Flow dispatch and completion code is identical at function level (§2).

| Item | R1A | R1B |
|---|---|---|
| tree manifest | `6e594ffe9f27c0cd…` (690) | `a8c166bc07e7383700f41ee5d47b983fce24dad91853c4e71c350cbd0c719e0e` (691) |
| app manifest | `8567e15a3abd817d…` | `99aeba99b483b1616e82f6ce99d34a7ec46f6677f2033dfb36301be1fdb492db` |
| `conversation_engine.py` | `bc1f33c70a5aef6d…` | `514d5adf0514c8ec1348cc340ccd86530700640761fab5d22c2b98c28e57a733` |
| `test_r6_c4b_r1a_deferred_interest.py` | `4d8771071f6a6962…` | `a3a7be578841dd06e1cbe28cdad9d3c929358e3d8ed88fbfda7e53a48a62dbdf` |
| `test_r6_c4b_r1b_deferred_semantics.py` | — | `eed5eebddeb165b02fa7eae56b7ce9a57aa7c891f02b9bc10d5a5b0111a967f1` |
| `test_r5_f1_template_validation_contract.py` | `5cfbc9d157a8612b…` | `7183eccc62f75a5d5974d2e3a0f59625afb141269f1549e3ebe222a49bda0c84` |

The tree diff against R1A is exactly those four paths.

Not run, as instructed: full SQLite, the full PostgreSQL matrix, the kit chain, rehearsal, WILD, Meta handset tests, mutation campaign.

## 9. Live isolation

`B/live/after.txt` was produced by the same read-only probe. It is **byte-identical** to the R1A after-probe:
- all eight flags `false`; `booking_dispatch_allowed=False`;
- `db_fingerprint=fbae2abdc3371f9146f0ac51a2ffa427`; max ids unchanged; `pending_outbound=0`;
- containers unchanged; HEAD `f16b1f3`; staged 0.

There are 0 `r1b-*` containers or networks; every run used `--rm --network none`. No host-scope prune. The R6-C4 kit seal-2 anchor `359062f49e8c2f3b…` verifies with `sha256sum -c` OK, and the kit is not re-pinned. The live image still has the pre-R1A line, and nothing reaches live without an authorized build.

## 10. Residuals

- R1A-O3 (clock-dependent T04/P04 fixtures) is unchanged and out of scope. This milestone's own tests freeze the clock.
- **Q3 on a thread with no candidate.** It is answered by the pre-existing Layer F service greeting, not by the deferred branch (§3). That is existing routing, not changed here.

## 11. Verdict

R1B implements the authorized correction inside the deferred path. The deferred turn is now terminal, deterministic, focus-aware and correctly attributed. A turn-scoped flag that cannot leak suppresses every appended proposition and outstanding question on that turn only. Matrix C, together with the negative control, proves normal clarification is unchanged.

The focused set shows NEW 0 / GONE 0 against a full-tree R1A baseline on the same clock. The bridge, validator, Booking Flow, pricing and scheduling code are byte-identical, and live is untouched.

**VERDICT: READY FOR FINAL FOCUSED INDEPENDENT REVIEW**

Nothing was staged, committed, pushed, built, deployed, migrated, enabled or published, and no customer was contacted. W-3 and the WILD were not started: W-3 remains OPEN and the final WILD remains NOT RUN.

STOP
