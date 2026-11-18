PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R3-ASYNC-QUESTION-AND-OPERATOR-AUTHORITY-CORRECTION

**VERDICT: READY FOR FOCUSED R3 INDEPENDENT REVIEW**

- **Date.** 2026-11-16 ART (work ran 2026-11-16T23:16Z to 2026-11-17T02:16Z).
- **Worktree and branch.** `/opt/ridecheck-crm-wild1-correction`, branch `fix/prelaunch-wild1-conversation-correction`.
- **Base.** `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008`.
- **Tree state.** The R3 delta is uncommitted, on top of the uncommitted R2 delta. Nothing was staged, committed, pushed, built or deployed. Meta was not touched. No WILD was run. No tester was reset or contacted.
- **Evidence.** `/opt/ridecheck-builds/crm-wild1-c-r3-20261116T231655Z/` (`$E` below).

## Inputs read and verified

| Input | sha256 |
|---|---|
| R2 closeout `…WILD1-CONVERSATION-COVERAGE-PRICE-BREAKDOWN-AND-QUOTE-DECLINE_CLOSEOUT_001.md` | `6c0352b78ed1113a60107289919f9c3d2a8e136f68366bca72d1f29d35c5b922` (matches) |
| Audit `…WILD1-C-FOCUSED-INDEPENDENT-REVIEW_AUDIT_001.md` | `e98a0eadf77b0f69c3456b94b6860bcac8c9112670ce34067192cc2ef2fa0fbc` (matches, at the exact path) |

**Scope.** This milestone corrects WC-01, WC-02 and WC-03, and closes the WC-04 evidence gaps. WC-05 to WC-09 are recorded in §9. None of them is claimed closed except where §9 says so.

---

## 1. Manifest

The R3 delta against the R2 snapshot (`$E/r2/`) is 9 paths. The full candidate is 52 paths against base. `$E/cand/` is byte-identical to the worktree on all 52 paths, verified with `sha256sum -c` against `$E/cand_manifest_final.sha256`. That file's sha256 is `b5ace40fe28a83b918bd072d01237b57e01b3e92a9fcd7e9fbfca25d05352172`.

| Path | R3 Δ vs R2 | sha256 | git blob |
|---|---|---|---|
| `backend/app/services/conversation_engine.py` | +654 / −63 | `b4530c9f1f1e9403d8fb990cdac8d4a5d57913ee234573cb5def84999b26ab56` | `76fc97816e916112da3536600879150cbacb29cb` |
| `backend/app/services/claim_coverage.py` | +112 / −7 | `d345c9d23308d411632c23948d7c74944f1a04cad0e7bfa5ac88ccfacf25b3e1` | `3a6f8a595614ee431b9332eb8e4d6e2228b627ff` |
| `backend/app/services/quote_decision_history.py` | +79 / −5 | `d571dfcbe39342fb0d838edf2f787fb1fef990764a321fba59509b0e2fc92c86` | `5851c905ea27e4ab154e3fb506d13fb13d66784b` |
| `backend/app/ui/kanban_actions.py` | +34 / −0 | `a58d9d693d685f23399c80e2d63f09b834032958a4c46028691336fcb4a1aadc` | `ffe77fb260d05938109ee2e197921fefba8c7542` |
| `backend/app/api/leads.py` ⚑ | +13 / −0 | `1e53ef544f6e84b3d0ca544517f29ec23097a2f956598dc4865aebc77e64ed5f` | `8ec9ea4aa79774930acedd2a2f1ac7a321e51252` |
| `tests/_wild1c_harness.py` | +41 / −1 | `4e103cfec9daaa40c3b98baf7ea15379a7754bc95f8b8eb3ee3dc87867585948` | `396676cef0e679dd58f3c6b692d6d9c8957e9cee` |
| `tests/test_wild1_c_h1_regression.py` | +6 / −1 (`CONFIRM_DAYS_AHEAD`, see §6.3) | `b960aa061883a2be5b279da2975fd75d168cdf8b5f7b6677a4adaba473929d03` | — |
| `tests/test_r5_f1_template_validation_contract.py` | +1 (`_PENDING_QUESTION_ACK: FULL`) | `68815fc714919eab14b0b17baba41a05370111c72aea3ad1de1a39a81f36dc47` | `01b24e8e41d85cfbd6b01a4179876a2bbb635f86` |
| `tests/test_wild1_c_live_async_posture.py` | new | `39adee6c1116f28c54d04d3d7e873ba9345067aaef077673e746cc66fd8c1a8a` | — |
| `tests/test_wild1_c_r3_async_and_operator_authority.py` | new | `3c62200debfb3f9528164c6ae107cf2fe77006d8656f7b64eca49aed034e79ec` | — |

These files are unchanged against R2: `kanban.py`, `kanban_view.py`, `response_validator.py`, `response_composition.py`, `semantic_interpreter.py`, `schemas/turn_evidence.py` and `models.py`.

⚑ **`backend/app/api/leads.py` is outside the prompt's literal file list.** It is the existing operator/API module behind `PATCH /leads/{id}` (`update_lead`), which writes `flag` and `motivo_perdida`. The prompt authorises "an existing operator-action module if the real flag mutations live there". This file is such a module, so it is touched for WC-02 and flagged here for the reviewer.

**Migration required: NO.** Every new record uses the existing `whatsapp_thread_evidence` table and its existing columns, CHECK and UNIQUE constraints:
- `claim_type` values `pending_question` and `quote_decision`/`OPERATOR_FLAG_ACTION`;
- outcome `HOLD`/`ACCEPT`;
- `status` `PENDING`/`RESOLVED`;
- JSONB `validated_as` and `source_message_ids`.

On PostgreSQL the schema head is `20261104_vehicle_identity`, unchanged.

---

## 2. WC-01: a question asked while the reading is late is never silently lost

### Root cause

Layer D (`_handle_general_information_ai`) armed the coverage burst only when the semantic reading had already produced FAQ topics. Under the live posture (`SHADOW_UNDERSTAND_ASYNC=true`, 6.0 s), a burst with no catalogue locality never waits for that reading, because of the W1F-R2 no-wait carve-out. The turn therefore:
- composed with `required = []`;
- reported itself complete;
- persisted nothing.

The question was gone (audit L-2: 25 of 50 turns lost it). The harness ran every WILD1-C test synchronously, so no test could see this.

### Correction

**Typed control boundary, not a phrase list.** `claim_coverage.QuestionPotential` is total and never raises. It reuses Layer D's own route classifier and the one anchored greeting grammar already in `_FAQ_PATTERNS`. It has three classes:
- **GREETING_ONLY.** Every punctuation-delimited segment is a greeting, on any route. Nothing is owed and nothing is waited for.
- **MAY_ASK.** Anything else the general-information route accepts. A classifier exception also yields MAY_ASK, so a failure costs at most a bounded wait, never a lost question.
- **NOT_INFORMATIONAL.** The commercial path, unchanged.

**Bounded wait on MAY_ASK.** `_bounded_question_reading()` waits `min(4.5 s, semantic_same_turn_timeout_seconds)` for the reading. A turn never waits twice: after a timeout, `_semantic_turn_evidence` returns `None` instead of waiting again. Other turns do not wait on the model:
- greeting-only bursts;
- commercial turns without the existing admission wait;
- turns whose reading is already ready.

**On timeout or unavailability, the turn is not marked complete:**
- `CoverageReport.pending` carries the burst's WAMIDs, and `complete` is `not missing and not pending`;
- a `pending_question` row (`status=PENDING`, reason `SEMANTIC_TIMEOUT` or `SEMANTIC_UNAVAILABLE`, `source_message_ids` = the inbound WAMIDs, texts capped at 4000 chars in `validated_as`) is written in a savepoint;
- the customer gets the acknowledgement `_PENDING_QUESTION_ACK` = "Recibí tu consulta y te la respondo en el próximo mensaje.", appended to the turn's message, never replacing it.

**Next eligible turn:**
- `_dispatch_pending_resolution` re-reads the pending texts with the same `SemanticTurnInterpreter`, on its own daemon thread, at turn start;
- `_pending_resolution` adds the resolved claims to the turn's required set, so they are answered before commercial progression;
- it writes `required_claim` rows keyed to the original burst, plus an append-only `RESOLVED` row (`validated_as.resolves = <pending claim_id>`);
- claims already answered by a delivered outbound after the burst are not repeated.

**Same rule in QUALIFYING and QUOTED.** A qualification question may accompany the FAQ answer but never replaces it.

**Coverage has four states:** answered (`covered`), routed (`routed`, the Booking Flow for AVAILABILITY), pending (`pending`) and missing (`missing`). The Meta Flow body is re-recorded after validation.

**WC-07 closed with it:**
- `Ledger.outstanding` ignores `failed`/`blocked` outbound rows (`claim_coverage.delivered`);
- `_persist_owed_claims` and the pending writes run inside `db.begin_nested()`, so an `IntegrityError` rolls back only its savepoint, never the turn.

**A defect R2 and base already had, found and fixed here.** "hola, buenas tardes" waited about 3 s on base and R2: the FAQ producer's availability authority read the semantic evidence. GREETING_ONLY is now classified before the informational check, and the greeting-only burst skips that read.

### Async timing and timeout behaviour

All timings below are measured in the live-posture harness.

| Interpreter reading | Turn 1 | Turn 2 |
|---|---|---|
| immediate | answered same turn, ≈0.1–0.3 s | — |
| ready at ~3.5 s | answered same turn, 3.5 s (inside the 4.5 s budget) | — |
| timeout (> 6 s) | waits 4.5 s once (T1 4.8 s); ack + `PENDING` row; never complete | resolved, answered, `RESOLVED` row |
| raises (unavailable) | no wait (the provider already failed); ack + `PENDING` row | resolved, answered |
| bare greeting ("hola", "buenas", "Buenas tardes!", "hola, buenas tardes") | < 2.0 s, nothing pending | — |

**Residual.** If the provider stays down on every turn, the ack repeats each turn and the pending row stays open. That is bounded, truthful and visible, but it is a copy and operations item for the owner.

### Before and after

**Before** (audit L-2 probe, R2): q1 "¿Qué revisan?" in QUALIFYING with a 3.5 s reading:
- reply: "¿Me contás qué vehículo querés revisar y en qué zona está?";
- coverage `required: []`, 0 owed rows;
- the next reply also omits it.

In QUOTED with a timeout, the reply is only "Gracias por tu mensaje. Cualquier otra consulta, escribime por acá."

**After** (`$E/probes/after_examples.txt`):
- **QUALIFYING, timeout, T1** "¿Qué revisan y qué me mandan en el informe?" (4.8 s):
  - message: "…¿Me contás qué vehículo querés revisar y en qué zona está?" followed by "Recibí tu consulta y te la respondo en el próximo mensaje.";
  - coverage `pending: [[wamid…0002]]`, row `PENDING`.
- **QUALIFYING, T2** "Es un Toyota Corolla 2018 y está en Palermo":
  - quote block (Revisión $140.000 / Viáticos $0 / Total $140.000) + the service-scope answer + the report answer;
  - coverage `covered: [report, service_scope]`, rows `PENDING, RESOLVED`.
- **QUOTED, unavailable, T1:** the generic closing + ack, `PENDING`.
- **QUOTED, T2** "Otra cosa: ¿aceptan transferencia?": the payment answer + scope + report, all covered, `RESOLVED`.
- **3.5 s reading:** answered on the same turn.

---

## 3. WC-02: operator authority over PERDIDO

### Root cause

R2's lost-lead rule inferred "the engine wrote this PERDIDO" from the absence of any newer engine row plus an equal motivo. Operator actions left no record, so an engine decline followed by an operator re-confirmation of the same PERDIDO/PRECIO was indistinguishable from the engine's own decline. The customer's "dale" therefore reactivated a lead the operator had closed (audit S3 / MI-4).

Two further escape routes were found during R3:
- **The legacy branch** (acceptance authority off): the reply model's `lead_flag=ACEPTADO` proposal reached `lead.flag = new_flag` unguarded. This was traced with a SQLAlchemy set-event.
- **Engine side doors:** the vehicle/zone re-quote resets write `flag=PRESUPUESTANDO` on any QUOTED lead.

### Correction

**Append-only operator provenance, in the same transaction as the mutation.** `quote_decision_history.record_operator_flag_action(db, lead, action, route, producer, flag_before, motivo_before)` writes one `whatsapp_thread_evidence` row per thread of the lead:
- `claim_type=quote_decision`, `value=OPERATOR_FLAG_ACTION`;
- `producer=OPERATOR_UI | OPERATOR_API`, `evidence_class=OPERATOR`;
- `reason` = the motivo after the action;
- `validated_as` = `{action, route, flag_before, flag_after, motivo_before, motivo_after}`.

It flushes and never commits.

**Every production write path was searched.** An AST inventory test enumerates every assignment to `flag`/`motivo_perdida` outside the engine. The write paths are:

| Write path | Route | Recording rule |
|---|---|---|
| `ui_perdido` | `/ui/perdido` | `always` (a re-confirmation is an intervention) |
| `ui_lead_flag_set` | `/ui/lead_flag_set` | `always` |
| `ui_lead_flag_clear` | `/ui/lead_flag_clear` | `always` |
| `ui_move`, `ui_move_lead`, `ui_lead_move` | estado moves | recorded only when flag or motivo actually changed |
| `api/leads.update_lead` | `PATCH /leads/{id}` | whenever the payload carries `flag`/`motivo_perdida` or either changed |

All of them write before `commit` / `commit_or_400`. Provenance and mutation therefore commit or roll back together; the tests prove it.

**Reconsideration rule (`_engine_written_decline`).** The lead may reactivate only when all of these hold:
- the newest `quote_decision` row is the engine's `DECLINED`;
- no non-ENGINE row has a higher id;
- the lead's motivo equals that decline's motivo.

Otherwise the PERDIDO holds. No implicit return-of-control rule was invented, and history is never rewritten.

**One chokepoint for every engine write path.** `_arm_perdido_guard` installs a per-turn `before_flush` listener for a lead that is PERDIDO when the turn starts. It restores `(flag, motivo)` unless the turn left PERDIDO through one of the two authorised exits:
- the reconsideration (`_turn_perdido_left_by = "RECONSIDERED"`);
- an operator-triggered cycle reset (`"CYCLE_RESET"`).

It logs `WILD1-C-R3 PERDIDO_PROTECTED`. It is disarmed at the very end of `handle()`, after the kill-switch blocked-dispatch commit.

**Legacy and AI-proposal path.** On a PERDIDO lead, a model flag proposal other than ACEPTADO is dropped. With the authority off, ACEPTADO on an operator-held PERDIDO is held, and on an engine decline it goes through the same rule.

### Operator-provenance state machine

```
                    engine DECLINED (customer refusal, motivo M)
  ACTIVE/QUOTED ───────────────────────────────────────────────▶ PERDIDO[engine, M]
        ▲                                                              │
        │  customer reconsiders (ACCEPT / day request)                 │ any operator flag/motivo
        │  ⇐ only if newest row = ENGINE DECLINED(M), no newer         │ action (perdido, flag_set,
        │    operator row, lead.motivo == M                            │ flag_clear, move that
        │  → appends RECONSIDERED; flag = PRESUPUESTO_ENVIADO          │ changes flag/motivo,
        └──────────────────────────────────────────────────────────────┤ PATCH /leads)
                                                                       ▼
                                                        PERDIDO[operator]  (or another flag)
                                                        • engine never reactivates
                                                        • flush guard restores flag/motivo
                                                        • only a new engine DECLINED (after the
                                                          operator reopens the lead) makes it
                                                          reversible again
```

**Proved by tests with real operator actions (`OperatorAuthority`, 18 cases):**
- decline → reconsideration reactivates;
- operator PERDIDO alone never reactivates;
- clear → PERDIDO with the same motivo never reactivates;
- flag change → PERDIDO never reactivates;
- re-confirmation never reactivates (MI-4);
- `flag_set PERDIDO` keeps the motivo and holds;
- a different motivo never reactivates;
- API PATCH re-confirmation holds;
- an estado move that touches neither flag nor motivo is not an intervention;
- engine re-decline after a reconsideration is reversible again (MI-4, newest row not oldest);
- the legacy path holds an operator PERDIDO, and the turn is still answered, stays QUOTED and sends no Flow (MI-4b);
- the legacy path reactivates an engine decline;
- a side-door re-quote write is blocked;
- the side door is blocked under the kill switch too;
- provenance and mutation roll back together;
- the writer never commits on its own;
- the AST inventory of all writers;
- the kanban history line ("Flag modificado por un operador").

**After-example.** Engine decline PERDIDO/PRECIO → operator re-confirms PERDIDO/PRECIO → customer "dale, avancemos". The lead stays PERDIDO/PRECIO and QUOTED, with no token, offer or booking. Decisions: `[DECLINED ENGINE, OPERATOR_FLAG_ACTION OPERATOR_UI]`. In R2 this lead reactivated (audit S3).

---

## 4. WC-03: website-form quote

### Root cause

`_handle_website_form` wrote its own sentence, "El precio de la revisión es $240.000.", before the plan appended the canonical block. "La revisión" was therefore labelled with two amounts, and the message carried two CTAs.

### Correction

The handler now composes one declared message from the same `PricingQuote`, with no model pricing:
- `intro` + "Te paso la cotización:";
- the canonical block (`_rv_render_price_breakdown(base, viaticos, total)`), declared as `StructuredClaim(PRICE, (base, viaticos, total))`;
- exactly one CTA, "¿Qué día y horario te viene mejor?".

No sentence names the total as the revision price.

**After:**

```
Perfecto, Prueba! Recibí tu solicitud para revisar el Jeep Renegade en Berazategui. Te paso la cotización:

Revisión: $150.000
Viáticos: $90.000
Total: $240.000

¿Qué día y horario te viene mejor?
```

**R2 claim corrected.** R2 §4.1 said "every reachable quote uses the composer", which was inaccurate (the audit found the website form). **It is now true:** the website form renders and declares the same block as the conversation quote, and the validator checks the declared claim.

---

## 5. WC-04: evidence gaps

### 5.1 Live-async posture

`_wild1c_harness.LIVE_ASYNC` and `cfg_live_async()` model the live posture: `SHADOW_UNDERSTAND_ASYNC=true`, a 6.0 s same-turn timeout, the interpreter on the shadow worker, and the worker drained inside the patch scope.

`test_wild1_c_live_async_posture.py` adds a `*LiveAsync` twin of every TestCase in both WILD1-C suites, plus `LivePostureIsReallyAsync`. The synchronous suites are unchanged and still run.

### 5.2 Async matrix

5 inputs × {QUALIFYING, QUOTED} × {immediate, ~3.5 s, timeout, raises} = 40 cases. The inputs are the audit's q1–q5. Result: **`ran=40 answered=40 silently_lost=0`**, on SQLite and on PostgreSQL.

### 5.3 Meta Flow body limit (MI-7)

A real test patches `_booking_flow_body` to a ~530-character body so that owed content exceeds 1024 characters. Results:
- the dispatched body is ≤ 1024;
- segments are fitted whole and greedily;
- omitted answers stay owed (`required_claim` rows) and are answered on the next turn;
- nothing is marked delivered that was not sent;
- the existing `BOOKING_FLOW` authority, token and button are used.

### 5.4 Validator block preservation

Mechanism:
- `_restore_price_blocks(composed, validated)` restores a canonical block flattened by an unrelated rewrite, changing whitespace only;
- if validation corrupted the block (a message-wide temporal finding can drop the "Revisión" line), the engine logs `PRICE_BLOCK_CORRUPTED_BY_VALIDATION` and sends the safe line instead, never a corrupted price;
- the validator itself is not redesigned.

Tests:
- an unrelated rewrite does not flatten the block;
- an untouched quote is unchanged;
- a corrupted block is never sent.

### 5.5 Review mutants

MI-3b, MI-4, MI-4b, MI-5b and MI-7 now have killing tests (§7).

---

## 6. Test results

The runner image is `ridecheck-crm-test:l4.7b2`, with `--network none`, read-only mounts and TZ America/Argentina/Buenos_Aires.

### 6.1 Focused set, base vs candidate (SQLite, 53 files; base has 49 of them)

| Tree | cases | passed | failing | skipped |
|---|---|---|---|---|
| base `aa2c8db` | 1793 | 1774 | 7 | 12 |
| R2 | 1856 | 1837 | 7 | 12 |
| R3 final candidate | 1994 | 1975 | 7 | 12 |

- **base → candidate:** NEW 0 / GONE 0 / COMMON 7 / COMMON_changed 0; +201 cases, 0 removed.
- **R2 → candidate:** NEW 0 / GONE 0; +138 cases.

The 7 common failures are the known clock-dependent fixtures:
- C4A T04 ("lunes 16/11 10:00");
- C4-PP P02 and P05;
- C4B P04.

They are identical on all three trees, and all runs took place on the same ART business date (16/11).

The focused candidate run was taken before the test-only `CONFIRM_DAYS_AHEAD` change (§6.3). The four WILD1-C/R3 files were then re-run on the final tree: 201/201 passed.

### 6.2 WILD1-C and R3 suites, final tree

| Suite | cases | SQLite | PostgreSQL 16 |
|---|---|---|---|
| WILD1-C sync (`conversation_correction` + `h1_regression`) | 63 | 63 pass | 63 pass |
| WILD1-C live-async twins + posture check | 64 | 64 pass | 64 pass |
| R3 `async_and_operator_authority` | 74 | 74 pass | 74 pass |
| **Total** | **201** | **201 / 0 failed** | **201 / 0 failed** |

The R2 tree on PostgreSQL passed 63/63.

**PostgreSQL dialect evidence after the candidate run** (counted before teardown):
- `quote_decision` rows: 61, of which operator rows: 14;
- `required_claim`: 30;
- `pending_question`: PENDING 23, RESOLVED 22. Not every test makes a follow-up turn; the unresolved row was not attributed to a specific test;
- duplicate `(thread_id, claim_id)`: 0;
- `pg_typeof(validated_as)`: `jsonb`;
- outbound messages carrying the ack: 23.

The PostgreSQL container and the internal network `w1cr3-pg` / `w1cr3-net` were torn down by exact name. 0 `w1cr3-*` containers or networks remain.

### 6.3 One PostgreSQL-only fixture failure, found and fixed (test-only)

The first PostgreSQL candidate run failed one test, `H1BookingCompletesLiveAsync::test_flow_completion_books_once_and_acknowledges`, with `BookingSlotConflictError`. The cause is in the fixture:
- the PostgreSQL database is shared by all tests and not reset;
- there is one launch professional;
- the new twin confirmed the same "tomorrow 11:00" slot that its synchronous original had just booked.

SQLite gives every `W.engine()` a fresh database, so the conflict never shows there. The failure does not depend on the clock: both trees pass on SQLite at the same time.

Fix: the parent test reads `CONFIRM_DAYS_AHEAD = 1`, and each live twin uses one more day. No production code changed. After the fix PostgreSQL passed 201/201.

Caveat: on a Saturday the synchronous original's "tomorrow" is a Sunday. That is pre-existing behaviour, unchanged.

---

## 7. Mutation campaign (final tree)

**Method:** 61 mutants. Each is an exact-string change applied exactly once to a throwaway copy of `$E/cand`; every anchor was verified to apply once, and none was NOT-APPLIED. KILLED means a failing test identity that is absent from the same group's control.

| Group | Tests | Control |
|---|---|---|
| CORE | 9 suites, `-k "not AsyncQuestionCoverage"` | 80 failed (subtests of the 3 C4A T04 clock cases), 322 passed |
| ASYNC | CORE + live posture, `-k "not AsyncQuestionCoverage or q1_ or q4_"` | 80 failed, 402 passed |

**Pass 1** killed 58 and 3 survived:
- **m29, MI-4b.** Bypassing the legacy hold reaches `_apply_lost_lead_rule(…, _LEGACY_ALLOW)`. That calls `dataclasses.replace` on a `SimpleNamespace` and raises `TypeError`, so the turn ends `action=error` and the customer gets no reply. The flush guard kept the flag, and `assertHeld` checked only flag, token and offers.
- **m47, R3-12.** Same root.
- **m45, R3-10.** Only `AsyncQuestionCoverage` asserted it, and CORE deselects that class.

Test-only additions:
- `assertLegacyHoldAnswered`: the turn is handled, one delivered reply, stage QUOTED, no Flow;
- `test_a_pending_question_is_never_complete_coverage`.

The `TypeError` line is unreachable in production, because the hold gates it. It is recorded as a defensive observation.

**Pass 2** (controls re-run on the final R3 test file): **m29, m45 and m47 are all KILLED.** Final result: **61 / 61 KILLED**.

The later `CONFIRM_DAYS_AHEAD` change touches one booking twin only. Every ASYNC-group mutant's kill list names other tests, so the verdicts stand.

### Supplied set: 24 / 24 KILLED

M01–M24 were re-anchored on the final tree, and all 24 apply exactly once. M15, M16 and M17 were re-anchored for R3 (their text moved); each keeps its semantics.

| ID | Mutant | Killed by (one of) |
|---|---|---|
| M01 | total-only PRICE claim accepted | `test_total_only_declaration_is_refused` |
| M02 | rendered-block check skipped | `test_declared_but_not_rendered_is_refused` |
| M03 | arithmetic not reconciled | `test_arithmetic_must_reconcile` |
| M04 | breakdown not compared to canonical | `test_mismatched_component_is_refused` |
| M05 | quote renders total only | 15 tests |
| M06 | viáticos lexeme removed | `test_t3_answers_viaticos_and_repeated_scope` |
| M07 | FAQ producer ignores owed claims | 6 tests |
| M08 | redundancy judged on bridged text | `test_bridge_that_looks_like_an_answer_does_not_suppress_it` |
| M09 | Layer D burst not armed | 2 (async) |
| M10 | price re-query swallows a day request | 7 tests |
| M11 | Layer D swallows price/decline after a quote | 2 tests |
| M12 | HESITATE as decline | 3 tests |
| M13 | ambiguous REJECT accepted | `test_ambiguous_reject_is_not_a_decline` |
| M14 | operator PERDIDO reactivated | 12 tests |
| M15 | motivo equality not required [re-anchored] | `test_operator_changed_motivo_is_not_reversed` |
| M16 | Flow body always "Ese horario" [re-anchored] | 4 tests |
| M17 | Flow preamble dropped [re-anchored] | `test_t4_flow_carries_every_owed_answer_first` |
| M18 | cross-turn ledger disabled | 2 tests |
| M19 | owed claims never persisted | 2 tests |
| M20 | decline reason always OTRO | 20 tests |
| M21 | decline history not recorded | 14 tests |
| M22 | coverage by single-word substring | 4 tests |
| M23 | kanban history not rendered | `test_kanban_card_shows_state_and_history` |
| M24 | decline writes needs_human | 8 tests |

### Review mutants: 12 / 12 KILLED

| ID | Killed by |
|---|---|
| MI-1 | 2 tests |
| MI-2 | 19 tests (async) |
| MI-3 | `test_reject_beside_a_day_request_is_not_a_decline` |
| **MI-3b** | `test_mi3b_no_decline_without_a_delivered_quote` |
| **MI-4** | `test_engine_redecline_after_reconsideration_is_reversible_again` |
| **MI-4b** | `test_legacy_authority_off_holds_operator_perdido` |
| MI-5a | 2 tests |
| **MI-5b** | `test_mi5b_ai_confirmation_scrub_requests_never_confirms` |
| MI-5c | `test_source_guard_pre_persistence_flow_copy` |
| MI-6 | `test_val_price_02_wrong_amount_rewritten`, `test_unrelated_rewrite_does_not_flatten_the_block` |
| **MI-7** | `test_mi7_flow_body_never_exceeds_meta_limit` |
| MI-8 | 4 tests |

### R3 mutants: 25 / 25 KILLED

| ID | Mutant | Killed by (one of) |
|---|---|---|
| R3-01 | Layer D never waits (R2 behaviour) | `test_wait_is_bounded_by_the_route_budget` + 9 |
| R3-02 | missing reading never marked pending | 11 tests |
| R3-03 | pending not acknowledged | 11 tests |
| R3-04 | pending not persisted | 10 tests |
| R3-05 | pending never re-read | 10 tests |
| R3-06 | resolution re-answers | `test_resolved_question_never_repeats_what_was_already_answered` |
| R3-07 | greeting-only burst waits | `test_bare_greeting_never_waits` |
| R3-08 | turn waits twice | 5 tests |
| R3-09 | Layer D wait unbounded | 3 tests |
| R3-10 | pending counts as complete | `test_a_pending_question_is_never_complete_coverage` (pass 2) |
| R3-11 | PERDIDO flush guard disabled | side-door tests (both) |
| R3-12 | legacy AI ACEPTADO not held | `test_legacy_authority_off_holds_operator_perdido` (pass 2) |
| R3-13 | re-confirmation not recorded | 3 tests |
| R3-14 | flag clear not recorded | 3 tests |
| R3-15 | flag set not recorded | 2 tests |
| R3-16 | API PATCH not recorded | `test_api_patch_reconfirmation_never_reactivates` |
| R3-17 | provenance in a separate transaction | `test_provenance_writer_never_commits_on_its_own` |
| R3-18 | engine reads motivo only (R2 rule) | 6 tests |
| R3-19 | website form calls the total the revision price | `test_one_block_one_cta_no_total_called_revision` |
| R3-20 | website quote undeclared | `test_website_form_declares_the_breakdown` |
| R3-21 | corrupted block sent | `test_corrupted_block_is_never_sent` |
| R3-22 | flattened block not restored | `test_unrelated_rewrite_does_not_flatten_the_block` |
| R3-23 | owed write without a savepoint | `test_owed_claim_duplicate_rolls_back_savepoint_only` |
| R3-24 | blocked/failed outbound counts as an answer | `test_blocked_outbound_never_answers_an_owed_claim` |
| R3-25 | Flow preamble ignores Meta's limit | `test_mi7_flow_body_never_exceeds_meta_limit` |

Totals: 24 + 12 + 25 = **61 / 61 KILLED**, with the 3 pass-1 survivors killed in pass 2. Evidence: `$E/mut/results_pass1.txt`, `$E/mut/results_rerun.txt`, `$E/mut/out/`.

---

## 8. Protected-path proof, model and live posture

**Protected paths.** `git diff --stat aa2c8db` over all of these is empty:
- `backend/alembic`;
- `backend/app/models.py`;
- `backend/app/routes`;
- `services/pricing.py`, `outbound_safety_gate.py`, `offer_lifecycle.py`, `flow_contract.py` and `booking_*`;
- `backend/app/repositories`;
- `meta`, `data`, `*.csv`, `*.xlsx`, `docker-compose*.yml`.

Booking, Flow authority and pricing rules and values are unchanged. `/var/www/ridecheck/faq.html`, `viaticos_zones.csv`, the XLSX and the live `viaticos_zones` table were not touched.

**Model unchanged (L-1 remains closed).** `semantic_interpreter.py` is byte-identical to R2; R3 does not touch it. Its only difference from base is R2's reviewed prompt change. The model strings are identical to base (`gpt-4o-mini` default via `openai_chat_model`); only their line numbers differ. R2's real-provider evidence stands unchanged:
- 72/72 valid calls;
- 0 false REJECT among 39 concern/ambiguous cases;
- 21/21 refusals classified REJECT;
- 12/12 reconsiderations classified ACCEPT.

**Read-only live checks** at 2026-11-16T23:50:07Z and 2026-11-17T02:16:15Z returned identical results:
- image `sha256:4952d5d5…6903` (`minlaunch-candidate-aa2c8db`), `GIT_SHA=aa2c8db`;
- `SHADOW_UNDERSTAND_ASYNC=true`, `SEMANTIC_SAME_TURN_TIMEOUT_SECONDS=6.0`;
- all launch switches false, including `OUTBOUND_ENABLED=false`.

Only these flag names were read; no secrets or PII were printed.

---

## 9. Remaining WC-05 to WC-09

| ID | State after R3 |
|---|---|
| WC-05 | **Open, narrowed.** The cross-turn ledger still reads `_query_active_messages` (newest 20). R3's `pending_question` and `required_claim` rows are keyed to WAMIDs and read from evidence, but an owed ask older than the message window can still fall out of the ledger. A bounded-horizon change is needed. |
| WC-06 | **Partly closed.** Flattening is fixed by restoring the whitespace-only block, and a corrupted block is never sent (§5.4). `_rewrite_price` still labels a total as "la revisión" when it rewrites a wrong amount; the validator was not redesigned, per the prompt. |
| WC-07 | **Closed:** savepoint writes, and undelivered rows never answer. |
| WC-08 | **Open (copy).** An operator-held PERDIDO that says "dale" receives the quote again with "Si te parece bien, podemos avanzar." It is now truthfully held, but the copy invites an acceptance; owner L-8. |
| WC-09 | **Open (copy):** "Me parece caro…" → "Genial! Te paso la cotización…" + CTA; owner L-8. |

**New copy for owner review (L-8):**
- `_PENDING_QUESTION_ACK`;
- the QUOTED pending turn pairs the generic closing ("Gracias por tu mensaje…") with the ack.

**Observations for the reviewer:**
- `_pending_resolution` adds resolved claims directly and also writes `required_claim` rows. This is redundant with the ledger by design, to survive WC-05's horizon, and was not separately mutated.
- An unbounded provider outage repeats the ack every turn.
- `_apply_lost_lead_rule` raises `TypeError` on a non-dataclass decision; this is unreachable today (§7).

---

## 10. Exact next step

A **focused R3 independent review** of the 9-path R3 delta in §1, against the audit's WC-01 to WC-04, using:
- `$E/cand` (manifest sha256 `b5ace40f…2172`);
- the mutation scripts `$E/mut/`;
- `$E/pg/pg_r3.sh`;
- the probes in `$E/probes/`.

Specifically:
- the `api/leads.py` touch;
- the flush-guard chokepoint;
- the async budget (4.5 s, never twice, greeting-only never waits).

No commit, build, deployment or WILD comes before that review's verdict.

**VERDICT: READY FOR FOCUSED R3 INDEPENDENT REVIEW**
