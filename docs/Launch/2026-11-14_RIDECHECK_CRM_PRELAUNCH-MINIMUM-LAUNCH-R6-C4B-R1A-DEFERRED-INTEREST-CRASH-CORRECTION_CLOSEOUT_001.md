PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-R6-C4B-R1A-DEFERRED-INTEREST-CRASH-CORRECTION

# R6-C4B-R1A — Deferred-interest crash correction

Worktree: `/opt/ridecheck-crm-r5-f1-response-validation`.
Evidence: `B=/opt/ridecheck-builds/crm-r6-c4b-r1a-deferred-20261115T025435Z`.
Parent seal: R6-C4B-R1, `Z=/opt/ridecheck-builds/crm-r6-c4b-r1-bridge-20261114T225256Z`.
Timebox: 90 min, used 02:50Z → about 03:15Z.

Owner rulings 1–3 are recorded as Addendum A of the R1 closeout. The R1 verdict and evidence there are preserved unchanged.

## 1. Root cause

`ConversationEngine._process_text` is declared `-> ConversationHandleOut`. The M21.1.6 deferred-interest intercept returned a 2-tuple:

```python
if _narr is not None and _narr.is_effectively_deferred():
    return self._handle_deferred_interest(ctx, state), True
```

`_handle()` returns that value unchanged: `return self._process_text(ctx, event)`. `handle()` then runs `self._revoke_undelivered_slot_claim(event, out)` and `out.latency_ce_ms = …` outside its `try`. It raised `AttributeError: 'tuple' object has no attribute 'latency_ce_ms'`, and the exception escaped the route.

**History.** `git log -S` shows exactly one commit introducing the line: `af694be` (M21.1.6). The line was already inside `_process_text` at that commit, with the same return annotation, and `_handle_deferred_interest` returned `ConversationHandleOut`. Later commits only reworded the handler, from `_send_text` to `_send_service_boundary`.
- No caller ever consumed a second element.
- No earlier helper returned `(out, handled)`.

The `, True` was a defect from the start, not a contract that drifted. No test before R1A drove a whole turn through this branch: the M21.1.6/M21.1.7 tests exercise `parse_narrative_interpretation` only. So the branch had never completed successfully on any build.

This is exactly the simple return-contract mismatch the owner described. Nothing outside this path needed to change.

## 2. Return-contract analysis

| Item | Finding |
|---|---|
| `_process_text` signature | `(self, ctx, event) -> ConversationHandleOut` |
| Callers of `_process_text` | exactly one: `_handle()` at line 3784, `return self._process_text(ctx, event)` |
| Callers of `_handle()` | `handle()`, which wraps it in `try`, then post-processes `out`: slot-claim revocation, `latency_ce_ms`, `ai_invoked`, `answer_source` |
| `_process_text` own `return` statements (AST, nested defs excluded) | all return a single expression; after R1A **none is a tuple** and none is bare (test_05) |
| `_handle_deferred_interest` | `-> "ConversationHandleOut"`, `return self._send_service_boundary(ctx, DEFERRED_RESPONSE_ES)` |
| `_send_service_boundary` | `_answer_source = DETERMINISTIC_RULE`; sends through `_send_text_to_wa` (gate path `CE_TEXT`; commits on success); returns `_out("replied", wa_message_id=…)`; on `OutboundBlockedError` commits and returns `_out("service_gate_blocked", detail="outbound_disabled")` |
| Adjacent early returns (`_send_service_boundary`, `_send_inspectability_reply`, `_escalate_inspectability_to_human`, motorcycle / handoff branches) | all return the `ConversationHandleOut` directly, with the same shape the fixed line now has |
| Idempotency | the dedup cursor is set at turn entry in `_handle()` and committed with the send. A retry of the same `wa_message_id` returns `skipped_dedup` before `_process_text` |
| needs_human | `_handle()` returns `skipped_human` before `_process_text`, so the deferred branch is unreachable while `needs_human` is active |

So deleting `, True` is the correct and complete change. The handler's result already has the shape every other branch returns.

## 3. Exact production diff (`B/r1_to_r1a.prod.diff`)

```diff
@@ -9897,7 +9897,7 @@ class ConversationEngine:
         # M21.1.6 deferred-interest intercept (NU-6, NU-7): must run before any
         # commercial mutation. Only fires when no active vehicle overrides deferred signal.
         if _narr is not None and _narr.is_effectively_deferred():
-            return self._handle_deferred_interest(ctx, state), True
+            return self._handle_deferred_interest(ctx, state)
```

That is the only production change. The test change is one new file, `tests/test_r6_c4b_r1a_deferred_interest.py`, with 6 tests.

## 4. Before / after regression

**New tests:**
- Fixed candidate (`B/junit/r1a_new.xml`): **6 / 6 pass**.
- Negative control (`B/negctl`, the same tree with the pre-fix line restored; diff shown in `B` and verified by `diff`): **5 fail, 1 passes** (`B/junit/r1a_negctl.xml`).
  - test_01, 02 and 04 fail with exactly `AttributeError: 'tuple' object has no attribute 'latency_ce_ms'` at `conversation_engine.py:3563`.
  - test_05 fails: "Lists differ: ['return (self._handle_deferred_interest(ctx, state), True)'] != []".
  - test_06 fails on the source pin.
  - test_03 (needs_human) passes on both, as it should: that turn never reaches the branch.

**Pre-fix behaviour measured (`B/negctl_behaviour.txt`):**

| Outbound | Result | Ledger | Cursor | `ai_events` | Retry |
|---|---|---|---|---|---|
| ON | `AttributeError` escapes the route | 1 × `sent` `CE_TEXT` | committed | stays `triggered` | `skipped_dedup`, 0 sends |
| OFF | `AttributeError` escapes the route | 1 × `blocked` `CE_TEXT` | committed | stays `triggered` | `skipped_dedup`, 0 sends |

This corrects the R1 closeout's R-1 statement, "the customer gets **no reply**", in R1 Addendum A. With outbound ON the deferred reply was delivered. What failed was the turn result: n8n received an error instead of `handled`, and the `ai_events` telemetry write-back never happened. The R1 evaluation reported *no reply* because the harness lost its recorded sends when the exception escaped.

**After the fix:** for the exact message "no se bien todavia, lo estoy pensando", `handle()` returns `ConversationHandleOut(action="replied")`, with `latency_ce_ms` an int, `answer_source=DETERMINISTIC_RULE` and `ai_invoked=True`. `ai_events` leaves `triggered`.

## 5. Focused test results

Set (`B/set.list`, 42 files, plus the new file):
- every existing suite that references `deferred_interest` / `DEFERRED_RESPONSE_ES` (29; the PostgreSQL smoke `smoke_f4_postgres.py` is excluded);
- the directly affected CE contract suites: `test_wild04r_phase2_observability` (latency/observability), `test_l4_7w5_g3_5e_response_attribution_foundation`, `test_m21_2_f_bug4_null_safety`;
- the R1 bridge suite `test_r6_c4b_verified_generation`;
- `test_r6_c4a_transactional_provenance`;
- the R1 Booking Flow authority list (`Z/booking.list`, 11 files).

| Run | Tree | Cases | Failing | Subtests passed |
|---|---|---|---|---|
| `set_base.xml` | R1 seal `Z/cand`, before 00:00 ART | 1442 | 6 | 1681 |
| `set_base2.xml` | R1 seal `Z/cand`, after 00:00 ART | 1442 | 10 | 1597 |
| `set_cand.xml` | R1A worktree | 1448 | 10 | 1597 |

**Same-clock comparison (`B/set2.cmp.txt`): NEW 0, GONE 0, COMMON 10, COMMON_changed 0; 6 cases added (the R1A file).**

The first comparison (`B/set.cmp.txt`) showed 4 "NEW" failures, all in `T04_CommittedBooking` and `P04_ConfirmationRequiresPersistedFlowSuccess`. They are **not R1A**: the unmodified R1 seal fails the identical 4 cases and the same 84 subtests once the Buenos Aires date reaches 2026-11-15 (see R1A-O3).

The 10 COMMON failures:
- the 6 that also fail before midnight: BF30 ×2, `w4f1_12`, `caseC_requested_day`, `obs_04`, `obs_04b`;
- the 4 date-dependent BOOKED cases above.

The 6 are the same identities as in the R1 baseline lineage.

The Booking Flow list is inside the set, with NEW 0. The R1 bridge suite and the c4a suite are unchanged (byte-identical files) and show NEW 0 on the same clock.

Not run, as instructed: full SQLite, the full PostgreSQL matrix, the kit chain, rehearsal, WILD, mutation campaign.

## 6. State and Flow-dispatch evidence (test_01 to test_04, real route, synthetic SQLite)

The world is a QUALIFYING thread, flag PRESUPUESTANDO, with focus vehicle Toyota Corolla 2018 (AUTO) and no zone. The interpreter is canned with the real-provider Q3 shape: `deferred_interest: true`. Its `reply` contains "Te espero el lunes a las 10:00, ya quedó reservado." and its `extracted` holds `preferred_day_iso 2026-11-16` and `preferred_time_str 10:00`, all of which must not take effect.

| # | Requirement | Evidence |
|---|---|---|
| 1 | no crash | `action="replied"`; `_process_text` returned `ConversationHandleOut`, not a tuple |
| 2 | exactly one safe reply | `len(sent)==1`; the text starts with `DEFERRED_RESPONSE_ES`; no "reservado", no "10:00"; no bridge (`_turn_bridge_sent` falsy); exactly +1 out row |
| 3 | deferred transition | stage stays QUALIFYING; flag and estado unchanged; candidates and focus unchanged; needs_human false (NU-6); cursor = inbound wamid |
| 4 | no quote/offer/revision/booking | revisions 0, offers 0, `current_revision_id` None |
| 5 | no Flow | `_send_whatsapp_cloud_flow` calls 0; `flow_booking_token` None |
| 6 | not BOOKED | stage ≠ BOOKED |
| 7 | no preferred date/time from generated prose | `preferred_day` and `preferred_time` None, despite the model's extraction |
| 8 | needs_human authoritative | test_03: `skipped_human`, interpreter 0 calls, `_process_text` not entered, 0 sends, state unchanged except the cursor |
| 9 | idempotent retry | test_02: second delivery `skipped_dedup`, 0 sends, 0 interpreter calls, state byte-equal |
| 10 | latency/observability | `latency_ce_ms` int ≥ 0 on replied, deduped, human and blocked turns; `ai_invoked` True; `answer_source` DETERMINISTIC_RULE; `ai_events` written back |
| 11 | through the outbound gate | out row `path_id=CE_TEXT`, status not blocked; network egress 0 |
| 12 | all eight switches false | test_04: `OUTBOUND_ENABLED=false` and the seven settings switches false (SMART_BOOKING_CE, OPERATOR_WHATSAPP_NOTIFY, DIRECT_WEBHOOK, EVIDENCE_ADMISSION, OUTSTANDING_QUESTION, LOCALITY_RESOLUTION_SAFETY, BOOKING_FLOW_DETAILS_PREFILL). Result: `service_gate_blocked`; exactly 1 `blocked` `CE_TEXT` ledger row; 0 Meta sends; 0 Flows; cursor committed; the retry is `skipped_dedup` |

## 7. Bridge prompts and architecture are byte-identical

`B/r1a.app.manifest` (139 files, the same file set as `Z/pins.candidate_app.manifest`) differs from the R1 seal in **one line only**: `services/conversation_engine.py`.
- `verified_generation.py` is identical: generator-5 / verifier-4 prompts, acceptance policy, budget and format rule.
- `response_validator.py` is identical: SB1 blob `a1e40945…`.
- The canonical fact blocks, `_send_with_bridge`, the Booking Flow code, scheduling, pricing and booking authority are all identical.

Inside `conversation_engine.py` the only change is the line in §3 (`git diff --no-index` against `Z/cand`).

## 8. Manifest and hashes

| Item | R1 seal | R1A |
|---|---|---|
| tree manifest (690 files, docs/Launch excluded) | `4366d3dc366acd429aeab67e1a93444f8a2d2a202d596626005afe67a48cb533` (689) | `6e594ffe9f27c0cdf1e6675be8cbacb9d5a734fe9aa9d458e3dda93d8871c93c` |
| app manifest (`backend/app`, 139) | `c9b0be8b24daa7be400a3dca3d0201413e80f806322044321d7eccaff213b6ae` | `8567e15a3abd817da1c8cde730b146816585aee1bb05b46a09df549f52329641` |
| full manifest (744, before these docs edits) | `4f118ddf…` (743) | `0f2a7103edab07744c80d995bae1362c2b9ef2bba0e808087304158c9d397075` |
| `conversation_engine.py` sha256 | `b627b2cd48fb9d98…` | `bc1f33c70a5aef6d8378f7ee01323ec9d14e5fd3e60ebf7178da7c9daeb83fec` |
| `tests/test_r6_c4b_r1a_deferred_interest.py` sha256 | — | `4d8771071f6a696250aaf90333684caffc9e0d83b028c27bae37cb27e10b40f7` |

The tree diff against the R1 seal is exactly those two paths. The hashing scheme is the R1 one: `sha256sum` of the manifest file, and the R1 hashes were re-derived first. The full manifest was taken before the two docs edits of this milestone, so it excludes them.

## 9. Live isolation

`B/live/after.txt` was produced by the same read-only probe (`B/live/probe.sh`, copied from `Z/live`). It is **byte-identical** to `Z/live/after.txt`:
- all eight flags `false`; `booking_dispatch_allowed=False`;
- `db_fingerprint=fbae2abdc3371f9146f0ac51a2ffa427`; max ids unchanged; `pending_outbound=0`;
- backend/postgres/n8n containers unchanged; HEAD `f16b1f3`; staged 0.

There are 0 `r1a-*` containers or networks; every run used `--rm --network none`. No host-scope prune.

The R6-C4 kit seal-2 anchor `CHECKSUMS.sha256` = `359062f49e8c2f3b…`, and `sha256sum -c` is OK. The kit is not re-pinned.

The live backend image still contains the pre-fix line. R1A changes nothing live; the fix reaches live only through a future authorized build.

## 10. Observations (recorded, not changed: outside the authorized path)

- **R1A-O1. The deferred copy composes with the G3-6 location clarification.**
  - On a QUALIFYING thread whose focus candidate has `tipo_vehiculo` and no zone, `_register_clarification_proposition` (pre-existing, in HEAD) appends the canonical location question. The single delivered message is "Perfecto, cuando tengas algún auto en vista escribinos y te ayudamos con la revisión.⏎⏎¿En qué localidad o barrio está el auto?".
  - With `OUTSTANDING_QUESTION_ENABLED=true` (test config) it also opens one `INSPECTION_LOCATION` outstanding question. With all eight switches false it opens none.
  - The message is deterministic, contains no model prose and makes no false claim. It does contradict the M21.1.6 NU-6 spec line "no vehicle/location question".
  - It is newly reachable only because the branch now completes. Changing it would touch the G3-6 clarification gate or the deferred handler, so it needs an owner ruling: accept it, or assign it.
- **R1A-O2. Copy versus existing vehicle.** `is_effectively_deferred()` looks only at this burst's narrative vehicle evidence, not at the thread's focus candidate. A customer who already named a car can therefore receive "cuando tengas algún auto en vista…". This is NU-6/NU-7 semantics as designed in M21.1.6 and is not changed here.
- **R1A-O3. Clock-dependent R6-C4A/C4B tests.**
  - The BOOKED status renderer formats with the real `date.today()` (`_format_date_human`). From 2026-11-15 in Buenos Aires, the Monday-16 fixture therefore renders "mañana lunes 16/11", while those tests pin the validator clock to Wed 11 Nov (`RV._utcnow`).
  - The validator then refuses "mañana", fails closed, and the tests see the generic reply. 4 cases (T04 ×3, P04 ×1) and 84 subtests fail on the unmodified R1 seal as well.
  - It is a fixture clock skew, not an R1A change, and it will affect any reviewer running these suites from now on.
- **R1A-O4.** On the deferred turn `_turn_renderer_source` remains `REPLY_MODEL`, set before the intercept, although the deterministic copy is sent. `answer_source` is `DETERMINISTIC_RULE`. This trace label is pre-existing and is not changed.

## 11. Verdict

R1A implements the one authorized correction as a one-line return-contract fix. It meets all twelve required behaviours, and the negative control proves the defect on the pre-fix line. The focused set shows NEW 0 / GONE 0 against the R1 seal on the same clock. The bridge, the policy, the canonical blocks and the Booking Flow are byte-identical, and live is untouched. The observations in §10 are pre-existing, outside the path, and listed for the reviewer and the owner.

**VERDICT: READY FOR FINAL FOCUSED INDEPENDENT REVIEW**

Nothing was staged, committed, pushed, built, deployed, migrated, enabled or published, and no customer was contacted. W-3 and the WILD were not started: W-3 remains OPEN and the final WILD remains NOT RUN.

STOP

---

## ADDENDUM R1B — Deferred-interest semantics (added 2026-11-15; the R1A text, evidence and verdict above are unchanged)

The owner authorized a narrow correction of observations R1A-O1, R1A-O2 and R1A-O4: milestone **R6-C4B-R1B**. R1A-O3 (clock-dependent tests) is not in scope and remains as recorded.

What R1B changes, in `conversation_engine.py` only:
- The deferred-interest turn is now **terminal**. A turn-scoped flag, reset at every turn start, makes `_register_response_propositions` register nothing on that turn. So no G3-6 location question, no other proposition and no outstanding question join the deferred copy.
- The copy now depends on whether canonical state holds a focus vehicle (`_focus_candidate(ctx, repair=False)`):
  - with a focus vehicle: "Perfecto, pensalo tranquilo. Cuando quieras retomar la revisión de ese auto, escribinos.";
  - without one: the existing `DEFERRED_RESPONSE_ES`.
- The turn is attributed `DETERMINISTIC_TEMPLATE` / `DETERMINISTIC_RULE`.

The R1A tests were kept and rerun. Two R1A assertions were updated to the R1B behaviour:
- test_01 now expects the focus copy, because its world holds a focus vehicle;
- test_06 pins the new handler line.

Full evidence: `docs/Launch/2026-11-15_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-R6-C4B-R1B-DEFERRED-INTEREST-SEMANTICS_CLOSEOUT_001.md`.
