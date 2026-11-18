PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R4-ROUTE-COMPLETE-QUESTION-RETENTION-AND-PERDIDO-GUARD

**VERDICT: READY FOR FOCUSED R4 INDEPENDENT REVIEW**

- **Date.** 2026-11-17 (work ran 14:24Z–17:45Z).
- **Worktree / branch.** `/opt/ridecheck-crm-wild1-correction`, `fix/prelaunch-wild1-conversation-correction`.
- **Base.** `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008` (HEAD unchanged; index empty).
- **Tree state.** The R4 delta is uncommitted, on top of the uncommitted R2 and R3 deltas. Nothing was staged, committed, pushed, built or deployed. Meta was not touched. No WILD was run. No tester was reset or contacted. Booking 11, the viáticos seeds and table, the website and the Flow JSON were not touched.
- **Launch gate.** L4 (Runtime + Wild certification, ACTIVE). L1–L3 stay FROZEN; nothing here reopens them.
- **Evidence.** `/opt/ridecheck-builds/crm-wild1-c-r4-20261117T142430Z/` (`$E4` below). Nothing final lives in a session scratchpad.

## 0. Inputs read and verified

| Input | sha256 | Check |
|---|---|---|
| R3 closeout `…WILD1-C-R3-ASYNC-QUESTION-AND-OPERATOR-AUTHORITY-CORRECTION_CLOSEOUT_001.md` | `f27affd84fd79fa63c019e4080086661963e059070382dc44c671f2cf7293e06` | equals the hash the R3 review recorded |
| R3 review `…WILD1-C-R3-FOCUSED-INDEPENDENT-REVIEW_AUDIT_001.md` | `3af608c1635ac3e6b938e175262293bea667d34c523953c136a1f60041df5607` | recorded here (no earlier record exists) |
| R3 candidate manifest `$E/cand_manifest_final.sha256` | `b5ace40fe28a83b918bd072d01237b57e01b3e92a9fcd7e9fbfca25d05352172` | matches the review; `sha256sum -c` against the worktree 52/52 OK before any edit |

`$E` = `/opt/ridecheck-builds/crm-wild1-c-r3-20261116T231655Z`. My snapshot of the R3 tree (`$E4/r3`) passes `sha256sum -c` against the 50 code/test paths of that manifest. No other Claude session had this worktree as its cwd.

---

## 1. Manifest

### 1.1 R4 delta against the reviewed R3 tree (`$E/cand`) — 9 paths

| Path | Δ vs R3 | sha256 | git blob |
|---|---|---|---|
| `backend/app/services/conversation_engine.py` | +531 / −167 ¹ | `acbd22d0efb0ea46c1b8ff7e0549159fc0d91dbe42431ccbd206f265127869f7` | `41b288506f87852b8bc23f03bc8a2eaf3e933e1d` |
| `backend/app/services/claim_coverage.py` | +31 / −0 | `90fc0ae55292dc1f50183328be3a024077f4573b38b03df85503f638ed22d453` | `d61a32f5d6fe96534f4ad4ae45cc9e4e6869f76f` |
| `backend/app/services/semantic_turn_evidence.py` ² | +7 / −0 | `3eebf0c95e2d963605943d5c29ee3b6fe3d1040094999347699ae075d7309b2c` | `28968584adc4375e65e8538c83bbaedfba8577c9` |
| `tests/test_wild1_c_r4_retention_and_perdido_guard.py` | new (1075 lines, 97 tests) | `edf16057ef312699b6ec53149d8e4bdb7dbe2c58b868e65d2c7ea34daba39b2b` | `981f64e28e8eed6e14e8bb2f8933e51aba8990a8` |
| `tests/_wild1c_harness.py` | +19 / −0 | `0dd904681c9b360caced95def32667380ecc1960277fe098722a94838265f1bc` | `0700042a33323264b3e80edf4e5e257088f9aaf3` |
| `tests/test_r5_f1_template_validation_contract.py` | +4 / −1 | `2d484ee7144dbf369d1ef08401f02dcb7d264a5309a158bacde2bf997083d5c9` | `9dfcb569118ba317f73aa020ff492ece768b32ad` |
| `tests/test_l4_7c_3b_acceptance_authority_cutover.py` ³ | +6 / −2 | `1ce55c1ee36958664b6d405fdc62a8c6f1e16f8b384ab06361dd030dee8d0939` | `26e1d7ebec213e2538ed26360e22fdde9d34d152` |
| `tests/test_l4_7w5_g3_6_bounded_correction.py` ³ | +6 / −1 | `d00b4622337ebb23cdb733be12c7c1fd87f13fb272a3fba63899b9332859efac` | `b06fa80ff516fa4aa472219f03ed84c4543a5a78` |
| `tests/test_l4_7w5_g3_5e_response_attribution_foundation.py` ³ | +6 / −2 | `89feafc4e2df53705996d01ea546880f3420da535923745036d2f1a09e8dd197` | `469a4036963b65a76c25227a2f8cbc8c8d610bbf` |

¹ Includes re-indenting `handle()`'s body one level to place it inside a `try/finally` (§5); the logic delta is much smaller.
² **Outside the obvious file list — flagged for the reviewer.** It adds one read-only property, `TurnSemanticEvidence.dispatched`, so a deadline-bound caller can refuse the inline fallback (§3.4). `get()` is unchanged.
³ **Existing tests whose assertions encoded the behaviour R4 deliberately changes** (each change is explained in §7.4). None was weakened to hide a regression.

Unchanged against R3 (byte-identical): `semantic_interpreter.py`, `response_validator.py`, `response_composition.py`, `quote_decision_history.py`, `kanban_actions.py`, `kanban.py`, `kanban_view.py`, `api/leads.py`, `schemas/turn_evidence.py`, `models.py`, `settings.py`.

### 1.2 Full candidate

52 code/test paths against base (R3's 50 + `semantic_turn_evidence.py` + the R4 suite; R3's own manifest also listed two docs). `$E4/cand/` is a full-repository copy of the worktree (excluding `.git`), byte-identical on all 52 paths: `sha256sum -c $E4/cand_manifest.sha256` OK. **`sha256($E4/cand_manifest.sha256)` = `2ba471eaeba9c7f9e416f2e123b9e6681ed77ed2b380024d35eae757f8a9b15c`.**

**Migration required: NO.** All new records reuse `whatsapp_thread_evidence` (`pending_question` PENDING/RESOLVED, `required_claim`, `quote_decision`) and its existing constraints. PostgreSQL head stays `20261104_vehicle_identity`.

---

## 2. Root causes and corrections

| ID | Exact root cause (R3 code) | Correction (R4) |
|---|---|---|
| **R3RV-01** | `_note_semantic_unavailable` returned unless `question_potential(...) is MAY_ASK`, i.e. unless Layer D's route classifier tagged the burst informational. R3's "never wait twice" early return in `_semantic_turn_evidence` then threw away a late reading that R2's second wait used to catch. A NOT_INFORMATIONAL burst ("¿Cuándo me mandan el informe?") whose reading was late or failed got no answer, no ack, no row. | Retention is decided from a **typed per-turn semantic state** (`claim_coverage.SemanticTurnState`), never from the route classifier. Every non-terminal, non-greeting burst whose dispatched reading does not resolve inside the turn deadline is retained, acknowledged and re-read next turn (§3). |
| **R3RV-02** | `_arm_perdido_guard` was called only inside `_process_text`. `_process_flow_response` → `_process_vehicle_fallback_response` / `_process_location_fallback_response` wrote `lead.flag = "PRESUPUESTO_ENVIADO"`, quoted with a CTA, and kept a stale `motivo_perdida`. | (a) The guard is armed in `_handle` **before dispatch by message type** (text and every Flow-response handler; booking-shaped submissions excluded, §6.3). (b) A vehicle/location fallback form on a PERDIDO lead is **held** before either handler runs (`_hold_perdido_fallback`): nothing commercial changes and the owner-approved hold copy is sent. (c) The two fallback quote writes also clear `motivo_perdida`, so a loss reason never outlives PERDIDO. |
| **R3RV-03** | Each consumer had its own budget: the QUOTED decline probe waited the full 6.0 s, Layer D 4.5 s, the pending re-read another 4.5 s sequentially (measured 6.06–9.05 s). A `Thread.start()` failure fell back to an inline interpretation bounded only by the 30 s HTTP timeout. The re-read concatenated every unresolved row. | **One monotonic deadline per turn**, `min(4.5 s, SEMANTIC_SAME_TURN_TIMEOUT_SECONDS)`, started as the readings are dispatched and shared by every consumer through `_await_reading()`. An undispatched reading is never run inline. The re-read is capped at 3 rows and 4,000 characters (§4). |
| **R3RV-04** | `_disarm_perdido_guard()` was the last statement of `handle()`; `_revoke_undelivered_slot_claim`, `_turn_plan` and `_write_hybrid_trace` ran outside the `try`, so an exception there left the listener attached. | `handle()`'s whole lifecycle sits in one `try/finally` whose `finally` disarms; the per-turn reset also disarms any leftover listener on a reused engine (§5). |
| **Guard staleness (found in R4)** | R3's guard restored a pair **captured at turn start**. After any mid-turn `commit()` (expire-on-commit) the lead reloads; a concurrent operator commit would then be overwritten by the stale captured pair. Measured by mutant R4-14. | The guard acts only on **this session's own pending write** to `flag`/`motivo_perdida`, re-reads the row's **current** values on the session's connection, and — only if the database row is still PERDIDO — restores them with `set_committed_value` (no UPDATE emitted). |
| **§3.5 `TypeError`** | `_apply_lost_lead_rule` called `dataclasses.replace` on `_LEGACY_ALLOW` (a `SimpleNamespace`) → `TypeError`, turn `action=error`, no reply. The legacy callers also ignored the returned verdict. | A non-dataclass or malformed decision returns a typed **HOLD** (`allows=False`, blocker `operator_lost`); both legacy callers now honour the verdict (a HOLD never accepts). The authority rule itself is unchanged. |
| **WC-08** | An operator-held PERDIDO that accepted got the quote again with "Si te parece bien, podemos avanzar." | `_handle_perdido_hold` sends exactly the owner copy; the turn is exclusive (no price/vehicle/clarification/correction proposition joins it). |
| **WC-09** | "Me parece caro…" got "Genial! Te paso la cotización…" + CTA (price re-query path), or Layer D's generic closing when it also asked a FAQ. | QUOTED + resolved semantic **HESITATE** → the neutral lead + canonical block, declared as the 3-tuple PRICE claim, no CTA; other owed FAQ answers still travel with it. Layer D no longer intercepts a QUOTED price concern. |
| **Ack copy** | "…te la respondo en el próximo mensaje." implied a proactive message. | The owner-authorized text, alone when the only other content would be the generic closing (§8). |
| **R3-19 / R3-23b / R3-25** | Test gaps (the production code was right). | Killing tests added (§9). |

---

## 3. Route-independent retention (R3RV-01)

### 3.1 The typed state

`claim_coverage.SemanticTurnState` (no phrase list; the greeting form is the one anchored entry Layer D already uses):

| State | When | Consequence |
|---|---|---|
| `GREETING_ONLY` | every punctuation segment is the greeting form | no wait, nothing owed |
| `SEMANTICALLY_RESOLVED` | the reading arrived (`ok`) inside the deadline | supported claims answered normally |
| `SEMANTICALLY_UNRESOLVED_NONTERMINAL` | a reading was dispatched and did not resolve (late, failed, never started) on a continuing turn | one PENDING row (WAMIDs + interpreter texts) written **before composition** in a savepoint; acknowledgement; re-read next eligible turn |
| `TERMINAL_OR_PRE_GATED` | motorcycle / phone / service / inspectability / website-form pre-gates, `needs_human`, or a plan the human owns | nothing owed by automation; never waits |
| `NOT_DISPATCHED` | no reading exists (semantics off) | deterministic floors only, as before |

### 3.2 Where it is decided

`_turn_semantic_state(ctx)` is evaluated at **every** customer-message boundary, whichever route produced the primary: the plan's FAQ producer (every text send), the Booking Flow preamble, the Vehicle/Location clarification Flow bodies (`_with_pending_ack`), and a final turn-boundary safety net (`_finalize_question_retention`, after `_handle`) that retains an unresolved burst even if no send path consulted it. `_note_semantic_unavailable` no longer consults `QuestionPotential.MAY_ASK`; it refuses only greeting-only bursts and terminal turns.

### 3.3 Next turn

Unchanged R3 mechanism, now deadline-bound: the re-read runs concurrently with the current reading from turn start; resolved claims become `required_claim` rows keyed to the original burst plus one append-only RESOLVED row; claims already carried by a delivered outbound after the burst are not repeated; a re-read that finds no supported claim writes RESOLVED and adds no answer.

### 3.4 Behaviour changes a reviewer should know

1. Any continuing non-greeting burst with a failed/late reading is acknowledged — including one that turns out to ask nothing (it then resolves silently next turn). This is the requirement, and it is what made two older tests change (§7.4).
2. **Shadow-worker backlog:** when the worker declines the job (queue full), the current reading is now *unavailable* (retained + ack) instead of being run inline on the request thread. Same rule as a failed `Thread.start()`.
3. **Persistence-failure policy (R3-23b):** if the PENDING write fails, its savepoint is rolled back whole, the turn continues, the acknowledgement is **not** sent (nothing could be resumed), the burst is not reported pending, and `WILD1-C-R4 PENDING_QUESTION_PERSIST_FAILED` is logged at ERROR.

### 3.5 Regression matrix (prompt §A) — 7 inputs × {QUALIFYING, QUOTED} × {immediate, 3.5 s, 8 s, raises}

`RouteIndependentRetention`, live async posture (6.0 s configured), real route. **56/56 answered, 0 silently lost, max T1 4.61 s.** For immediate/3.5 s the claim is answered on the same turn with no ack and no row; for 8 s/raises T1 carries the ack, writes exactly one PENDING row whose WAMIDs ⊆ the thread's inbound ids, T2 answers it with rows `[PENDING, RESOLVED]` and no ack, every canonical answer appears exactly once in the thread, and no duplicate PENDING/RESOLVED/`required_claim` rows exist. The R3 matrix (q1–q5, 40 cases) still passes 40/40.

Before/after, same probe, same harness (`$E4/probes/out_r3.md` vs `out_r4.md`; T1/T2 in seconds):

| input | stage | reading | R3 T1 | R3 answered by T2 | R4 T1 | R4 rows after T2 | R4 answered by T2 |
|---|---|---|---|---|---|---|---|
| c1 "¿Cuándo me mandan el informe?" | QUALIFYING | 8 s | 6.13 | **no** | 4.53 | PENDING, RESOLVED | yes |
| c1 | QUOTED | raises | 0.10 | **no** | 0.10 | PENDING, RESOLVED | yes |
| c3 "Hola, tengo un auto… ¿Qué me entregan después?" | QUOTED | 8 s | 6.11 | **no** | 4.59 | PENDING, RESOLVED | yes |
| c4 "necesito revisar una camioneta que le revisan" | QUALIFYING | raises | 0.14 | **no** | 0.14 | PENDING, RESOLVED | yes |
| c7 "¿Qué miran en la revisión?" | QUOTED | 8 s | 6.06 | yes | 4.54 | PENDING, RESOLVED | yes |

Of the 16 failure-mode probe turns (8 s / raises), R3 lost all 12 for c1, c3 and c4 and answered the 4 for c7 (which its classifier calls MAY_ASK); R4 lost 0 of 16. Motorcycle/quad bursts ("tengo una moto Honda CB 500, ¿qué revisan?", "es un cuatriciclo, ¿cuándo me mandan el informe?") under a failing reading still go to the motorcycle handoff, write no row and carry no ack.

---

## 4. One semantic deadline per turn (R3RV-03)

**Budget.** `_TURN_SEMANTIC_BUDGET_SECONDS = 4.5`; effective `min(4.5, SEMANTIC_SAME_TURN_TIMEOUT_SECONDS)` (a configured 2.0 s gives 2.0 s). The R3 4.5 s value is kept: it covers the measured real-provider p95 (3.3 s) and the code needed no other value. `_LAYER_D_SEMANTIC_WAIT_SECONDS` remains as an alias only.

**Mechanism.** `_start_semantic_deadline()` runs just before the current reading and the pending re-read are dispatched (concurrently). `_await_reading(provider)` is the only wait primitive: a finished reading is read at once; an undispatched one is unavailable; otherwise it waits `max(0, deadline − now)`. Every consumer — `_semantic_turn_evidence` (admission, scheduling, decline probe, locality recovery), Layer D's `_bounded_question_reading`, `_pending_resolution`, the boundary state, the WC-09 concern read (through the memoised coverage read) — gets only the remainder.

**Measured (real route, live posture):**

| Turn | R3 | R4 |
|---|---|---|
| QUALIFYING, reading 8–12 s | 4.6 s (Layer D) / 6.1 s (commercial path) | 4.53–4.61 s |
| QUOTED, reading 8–12 s | 6.06–6.10 s | 4.54–4.59 s |
| pending re-read 8 s **and** current reading 8 s on the same turn | 8.04 / 9.05 s (review) | **4.54 s** |
| `Thread.start()` fails for the re-read | inline, ≤ 30 s | bounded (< 6.0 s in test), re-read not called, rows stay PENDING, resolved on the next turn |
| greeting-only ("hola", "Buenas tardes!", "hola, buenas tardes") with a 3 s reading | < 0.2 s | < 2.0 s asserted, < 0.2 s measured |
| immediate / 3.5 s readings | 0.07–0.25 / 3.54–3.60 s | 0.06–0.24 / 3.53–3.61 s |

Tests assert every first and second turn of the matrix `< 4.5 + 1.5 s` and the timeout turns `≥ 4.2 s`.

---

## 5. Pending batching, caps and guard lifecycle

**Re-read caps.** `_PENDING_REREAD_MAX_ROWS = 3`, `_PENDING_REREAD_MAX_CHARS = 4000` (each row is already capped at 4,000 characters when written, so the first row always fits). Rows are taken oldest first; a row that would exceed either cap is **deferred** — left PENDING, never deleted — and is re-read on a later eligible turn. Deferred rows still count as pending, so each recovery turn re-acknowledges while any remain.

Tests: 7 short failing bursts → recovery re-reads carry at most 3 rows → 3 batches (3+3+1); 3 bursts of ~2,480 characters → one row per re-read (two would exceed 4,000) → 3 batches; in both, every PENDING row ends with exactly one RESOLVED row, none deleted, and the ack appears on a recovery turn exactly when rows remain.

**Guard lifecycle (R3RV-04).** `handle()` = per-turn reset (which also disarms a leftover guard) → `try: [_handle + its except branches] + post-processing … return out` → `finally: _disarm_perdido_guard()`. Proven with a recorded listener reference: an exception in `_revoke_undelivered_slot_claim`, `_turn_plan`, `_write_hybrid_trace` or `_finalize_question_retention` (propagates) and in `_process_text` / `_process_flow_response` (caught → `action=error`) always leaves `event.contains(session, "before_flush", guard)` False; the same engine on a new Session adds no listener; another lead's fallback quote is not restored; a before/after-flush depth recorder never sees nesting > 1 and the side-door turn completes; and a concurrent operator commit after a mid-turn commit (motivo PRECIO → DISPONIBILIDAD) survives the engine's side-door write (R3's captured-pair guard overwrote it: mutant R4-14). On PostgreSQL these run on real separate connections.

---

## 6. PERDIDO entry-path inventory (R3RV-02)

### 6.1 Every engine assignment to `lead.flag` / `lead.motivo_perdida`

An AST test (`test_every_engine_flag_writer_is_inventoried`) pins this list; a new writer fails it.

| Writer (function, line) | Writes | Reached on a PERDIDO lead? | Protection |
|---|---|---|---|
| `_process_vehicle_fallback_response` 8792–8793 / 8808 | `PRESUPUESTO_ENVIADO` (+ motivo cleared) / `PRESUPUESTANDO` | no — held first | `_hold_perdido_fallback` + guard |
| `_process_location_fallback_response` 8983–8984 | `PRESUPUESTO_ENVIADO` (+ motivo cleared) | no — held first | `_hold_perdido_fallback` + guard |
| `_process_text` 9224 | `PRESUPUESTANDO` when flag empty | no (flag set) | — |
| `_process_text` 9610 / 9620 / 9631 (QUOTED day proposal) | `ACEPTADO` | only if the lost-lead rule allows | `_progression_allowed`; operator-held → WC-08 hold |
| `_process_text` 10534 / 10569 (vehicle / zone re-quote resets) | `PRESUPUESTANDO` | yes | flush guard (R3 test + R4 lifecycle tests) |
| `_process_text` 10749 (reply-model flag proposal) | proposal | yes | R3: dropped unless ACEPTADO via the rule; R4: legacy branch honours a HOLD |
| `_process_text` 10787 (deterministic quote force) | `PRESUPUESTO_ENVIADO` | yes (QUALIFYING) | flush guard keeps the flag — see §12 R4-D1 |
| `_apply_lost_lead_rule` 13853–13854 | `PRESUPUESTO_ENVIADO` + motivo `None` | only for the engine's own decline | the authorized RECONSIDERED exit |
| `_handle_quote_decline` 13971–13972 | `PERDIDO` + motivo | lead not PERDIDO (probe requires it) | — |
| `_handle_quoted_acceptance` 13995 | `ACEPTADO` | only after the authorizer / rule allows | rule; WC-08 hold otherwise |
| `_handle_website_form` 14171 | `PRESUPUESTO_ENVIADO` | QUALIFYING website form | flush guard |
| `_handle_scheduling_escalation` 15208 | `ACEPTADO` | SCHEDULING only | flush guard |
| `_execute_cycle_reset` 16167 / 16169 | `None` + motivo `None` | operator-triggered reset | the authorized CYCLE_RESET exit (before the guard is armed) |

Outside the engine: the operator writers (R3 provenance, unchanged) and `BookingAuthority`'s projection (`LeadUpdate(flag="ACEPTADO")`, own connection), §13.

### 6.2 Where the guard is armed

`_handle`, after the dedup, cycle-reset and `needs_human` exits and **before** routing by message type, for every non-booking-shaped turn: `_process_text`, `_process_flow_response` and every handler it calls (motorcycle contact response, vehicle/location fallbacks). `_process_text` re-arms only for a direct caller.

### 6.3 The one exclusion

A booking-shaped Flow submission (`_is_booking_shaped_submission`) is not guarded: its writer is `BookingAuthority` on its own connection, and the guard could otherwise turn a CE-side refresh of an authority-committed `ACEPTADO` into a write of PERDIDO, changing persisted-booking behaviour, which this milestone may not touch. R3 did not guard it either. See §13.

---

## 7. Flow fallback before/after and test results

### 7.1 State table (real `flow_response` route, `$E4/probes/out_r3.md` → `out_r4.md`)

| Scenario | R3 after (flag/motivo) | R3 sent | R4 after | R4 sent |
|---|---|---|---|---|
| operator PERDIDO + vehicle fallback | PRESUPUESTO_ENVIADO/OTRO, QUOTED | quote + CTA | **PERDIDO/OTRO**, QUALIFYING | hold copy |
| operator PERDIDO + location fallback | PRESUPUESTO_ENVIADO/OTRO, QUOTED | quote + CTA | **PERDIDO/OTRO** | hold copy |
| engine decline → operator re-confirms + vehicle / location | PRESUPUESTO_ENVIADO/PRECIO | quote + CTA | **PERDIDO/PRECIO**, QUOTED | hold copy |
| engine decline + vehicle / location | PRESUPUESTO_ENVIADO/PRECIO, no RECONSIDERED | quote + CTA | **PERDIDO/PRECIO**, `[DECLINED]` | hold copy |
| unsolicited fallback, operator PERDIDO (both) | PRESUPUESTO_ENVIADO/OTRO | quote + CTA | **PERDIDO/OTRO** | hold copy |
| kill switch, operator PERDIDO (both) | PRESUPUESTO_ENVIADO/OTRO (committed) | blocked | **PERDIDO/OTRO** | blocked |
| stale motivo on a non-PERDIDO lead + location fallback | PRESUPUESTO_ENVIADO/**PRECIO** | quote | PRESUPUESTO_ENVIADO/**None** | quote |

In every held case: 0 Flows, token `None`, 0 offers, 0 bookings, 0 revisions, 0 operator notices, no "Total:", no "podemos avanzar", flag ≠ `PRESUPUESTO_ENVIADO` (so `quote_followup` never selects it). After an engine decline + fallback hold, "dale, avancemos" by text still reconsiders: ACEPTADO, motivo `None`, `[DECLINED, RECONSIDERED]`.

### 7.2 SQLite (runner `ridecheck-crm-test:l4.7b2`, `--network none`, read-only mount, TZ America/Argentina/Buenos_Aires)

| Run | Result |
|---|---|
| WILD1-C sync + live-async + R3 + R4 + template inventory (frozen candidate) | **327 passed / 0 failed** (v2), then R4 suite on the final tree **97 / 0** |
| R4 retention matrix | 56/56 answered, 0 silently lost, max T1 4.61 s |
| R3 async matrix | 40/40, 0 silently lost |

### 7.3 Focused base / R3 / R4 comparison (54-file set = R3's 53 + the R4 suite; all three runs today, 17/11 ART)

| Tree | cases | passed | failing | skipped |
|---|---|---|---|---|
| base `aa2c8db` (`$E/base`) | 1793 | 1781 | 0 | 12 |
| R3 (`$E/cand`) | 1994 | 1982 | 0 | 12 |
| R4 (`$E4/cand`) | 2088 | 2076 | 0 | 12 |

base → R4 and R3 → R4: **NEW 0 / GONE 0 / COMMON_changed 0**; 0 cases removed. (Yesterday's 7 clock-dependent failures pass on today's date on all three trees.) The R4 focused run was taken on candidate v2; v3 differs only by one comment (AST-identical engine) and the R4 suite, which was re-run in full on v3 (97/97).

**Semantic extra set** (19 more suites that enable the interpreter or use the failing-reading harnesses, not in the focused set): R3 646 passed / 1 failing; R4 646 / 1 — **NEW 0**. The common failure, `test_wild04r_f3_exact_cases::test_caseC_requested_day`, expects a hard-coded 2026-09-01 and fails identically on R3; its file is identical to base.

### 7.4 Existing tests updated (and why)

| Test | Change | Reason |
|---|---|---|
| `test_l4_7c_3b…::test_cut_06_legacy_helper_cannot_progress_alone` | source pin updated to the new legacy branch | the legacy branch now honours the rule's HOLD (§3.5 fix); the property the test protects is strengthened |
| `test_l4_7w5_g3_6…::test_bc36_34`, `test_l4_7w5_g3_5e…::test_g35e_24` | expected kinds `[TEXT, FAQ_ANSWER]` → `[TEXT, FAQ_ANSWER, FAQ_ANSWER]` + assert the ack is in the one message | their harness's `evidence=None` makes the provider return `ok=False`; R3 dropped that burst ("¿Cuál es el horario de atención?") because its classifier called it NOT_INFORMATIONAL — exactly R3RV-01. The one-message invariant they protect is unchanged |
| `test_r5_f1_template_validation_contract` | 3 constants classified FULL | new copy inventory (§8) |

Two `handle()` source pins (`test_g37_42`, `test_site_33`) failed against an intermediate refactor; the refactor was redone so `handle()` keeps `out = self._handle(event)` and `self._write_hybrid_trace(` — those tests are unmodified and pass.

### 7.5 PostgreSQL 16 (disposable `w1cr4-pg` on tmpfs, `--internal` network, schema = pre-schema record + `alembic stamp` + `upgrade head`)

**298 passed / 0 failed** (WILD1-C sync 63, live-async 64, R3 74, R4 97) — including every PERDIDO side-door test, the guard lifecycle and concurrent-operator tests on real separate connections, the pending resolution, batching and outage tests. Dialect counts before teardown: `quote_decision` 93 (operator 37), `required_claim` 62, `pending_question` PENDING 71 / RESOLVED 64, duplicate `(thread_id, claim_id)` **0**, `validated_as` `jsonb`. The 7 unresolved rows match, by count, the 7 single-turn tests that end on a pending burst (attributed by test design, not by row content). Container and network torn down by exact name: 0 `w1cr4-*` left.

---

## 8. Acknowledgement and owner-copy examples (R4 probe, real route)

Constants (all classified FULL in the template inventory):

- `_PENDING_QUESTION_ACK` = "Recibí tu consulta, pero no pude procesarla en este momento. Si me escribís nuevamente, la retomo y te respondo."
- `_PRICE_CONCERN_LEAD` = "Entiendo. Te detallo cómo se compone la cotización:"
- `_PERDIDO_HOLD_REPLY` = "Gracias. Para retomar esta consulta, nuestro equipo tiene que revisarla manualmente."
- `_GENERIC_NO_QUESTION_TEXT` is an alias of the validator's existing NO_QUESTION fallback (no new copy).

Examples:

- **QUOTED, reading unavailable, "¿Qué revisan?"**
  - R3: "¡Hola! Gracias por escribirnos.⏎Gracias por tu mensaje. Cualquier otra consulta, escribime por acá.⏎Recibí tu consulta y te la respondo en el próximo mensaje."
  - R4: "Recibí tu consulta, pero no pude procesarla en este momento. Si me escribís nuevamente, la retomo y te respondo." (alone)
- **QUOTED, reading unavailable, "¿Y el informe cuándo llega?"**
  - R3: the restated quote with its CTA, no ack (silently lost).
  - R4: the same restated quote, followed by the ack. The generic closing is never sent beside the ack.
- **WC-09, "Me parece caro, ¿cuánto hay de viático?"** (HESITATE):
  - R4: "Entiendo. Te detallo cómo se compone la cotización:⏎⏎Revisión: $150.000⏎Viáticos: $90.000⏎Total: $240.000"
  - flag stays PRESUPUESTO_ENVIADO, no decision row.
- **WC-09, "Me parece caro. ¿Y qué revisan?"**: the same block, followed by the scope answer (R3 sent Layer D's generic closing).
- **WC-08, operator PERDIDO + "dale, avancemos"**, authority on and legacy:
  - R4 sends exactly the hold copy.
  - The lead stays PERDIDO/PRECIO in QUOTED, with no token, offers, Flows, bookings or notices.
  - The copy contains no notification, confirmation, booking or turn wording (asserted).
- A **day proposal** on an operator-held PERDIDO ("¿el martes a las 10 puede ser?") is held the same way.

---

## 9. Final-tree mutation results

**Method.**
- Each mutant is one or more exact-string hunks, each verified to apply exactly once to a throwaway rsync copy of `$E4/cand`, and compiled. A mutant that does not compile is reported INVALID, never KILLED.
- The test group is the R4 suite, the R3 suite, WILD1-C sync, the template inventory and the structured-response contract, with the matrices limited to c1/q1.
- Read-only mount, `--network none`, SQLite.
- KILLED means a failing test identity absent from the control.
- Controls: pass 1 had 234 tests, 0 failing; pass 2 had 237 tests, 0 failing.

**Pass 1** (`$E4/mut/results_pass1.txt`): 25 killed, 3 survived (R4-05, R4-06, R4-25), and one invalid. R4-22's anchor matched only part of a line that the `handle()` re-inline had indented deeper, producing an `IndentationError`; its 6 "failures" were import errors, so the kill is not counted.

- R4-05 and R4-06 survived because each re-read cap masked the other in the single outage test, and the test read the mutable constants. That test was split into a row-cap test and a character-cap test, both asserting the documented literals 3 / 4000.
- R4-25 survived because that check is defence-in-depth; every caller returns earlier on a terminal turn. A direct unit test of the contract was added.
- R4-22 was re-anchored on the exact line.

**Pass 2** (`$E4/mut/results_pass2.txt`, final tree): R4-05, R4-06, R4-07 (its pass-1 killer was the test that was split), R4-22 and R4-25 are all KILLED.

**Final: 29 / 29 KILLED.**

| ID | Mutant | Killed by (one of) |
|---|---|---|
| R4-01 | retention re-gated on Layer D's MAY_ASK (R3 rule) | 10 tests, e.g. `test_pending_reread_and_slow_current_reading_do_not_double_the_turn` |
| R4-02 | boundary never decides the state | `test_clarification_flow_body_carries_the_acknowledgement` |
| R4-03 | every consumer gets the full budget | 7, e.g. `test_wait_is_bounded_by_the_route_budget` |
| R4-04 | undispatched reading runs inline | `test_thread_start_failure_is_bounded_and_never_runs_inline` |
| R4-05 | re-read row cap removed | `test_prolonged_outage_of_short_bursts_is_bounded_by_rows` (pass 2) |
| R4-06 | re-read character cap removed | `test_prolonged_outage_of_long_bursts_is_bounded_by_characters` (pass 2) |
| R4-07 | deferred rows not counted as pending | both outage tests (pass 2) |
| R4-08 | ack copy reverted to R3 | `test_acknowledgement_copy_is_the_owner_authorized_text` |
| R4-09 | generic closing kept beside the ack | `test_quoted_unavailable_sends_the_acknowledgement_alone` |
| R4-10 | ack sent although the row was not retained | `test_pending_persistence_failure_rolls_back_its_savepoint_only` |
| R4-11 | guard not armed before dispatch | `test_dispatch_exception_removes_the_listener` |
| R4-12 | fallback on PERDIDO not held | 5 side-door tests |
| R4-13 | guard not disarmed in `finally` | 3 lifecycle tests |
| R4-14 | guard restores the turn-start pair (R3) | `test_guard_never_undoes_a_concurrent_operator_commit` |
| R4-15 | non-dataclass decision passes through | `test_non_dataclass_decision_holds_instead_of_raising` |
| R4-16 | legacy caller ignores the verdict | `test_legacy_caller_honours_a_hold_verdict` |
| R4-17 | WC-08 hold removed (authority on) | `test_wc08_operator_perdido_acceptance_is_held_with_the_owner_copy` |
| R4-18 | hold not exclusive | `test_operator_perdido_plus_each_fallback_is_held` |
| R4-19 | WC-09 branch disabled | `test_wc09_price_concern_gets_the_neutral_breakdown` |
| R4-20 | Layer D swallows a QUOTED concern | `test_wc09_price_concern_gets_the_neutral_breakdown` |
| R4-21 | stale motivo kept on the fallback quote | `test_no_stale_motivo_on_a_non_perdido_lead` |
| R4-22 | no turn-boundary retention (re-anchored) | `test_turn_boundary_retains_a_burst_no_send_path_recorded` (pass 2) |
| R4-23 | clarification Flow body never acknowledges | `test_clarification_flow_body_carries_the_acknowledgement` |
| R4-24 | `dispatched` always True | `test_thread_start_failure_is_bounded_and_never_runs_inline` |
| R4-25 | terminal turns retained too | `test_a_terminal_turn_is_never_retained_even_if_asked_directly` (pass 2) |
| **R3-19a** | website form adds "La revisión sale $TOTAL." | `test_r3_19_website_form_states_amounts_only_inside_the_block` |
| **R3-19b** | website form adds "El precio de la revisión es $TOTAL." | the same, plus the R3 test |
| **R3-23b** | pending-question write outside its savepoint | `test_pending_persistence_failure_rolls_back_its_savepoint_only` |
| **R3-25** | per-segment check ignores the accumulated preamble | `test_r3_25_accumulated_preamble_never_exceeds_the_meta_limit` |

**Coverage of the gap tests:**

- **R3-25.** At least 3 preamble segments, each fitting with the Flow body on its own, but together over 1,024. The sent body is ≤ 1,024 and is exactly `kept + body`, whole segments in order. Omitted claims are not covered and stay owed. The token is issued, with 0 bookings.
- **R3-19.** No `$` amount, and no "revisión sale/es/cuesta…" or "precio de la revisión", outside the canonical block, in both postures.
- **R3-23b.** The failing add has flushed a row inside the savepoint and then raises. Afterwards there is no partial row, the outer transaction commits the cursor, there is one reply and no ack.

The R3 campaign's 61 mutants were not re-run; their production anchors are unchanged except where R4 replaced the code (for example R3-08, whose "never wait twice" line no longer exists; R4-03 covers that property now).

---

## 10. Protected-path proof and live read-only check

- **Unchanged against base.** `git diff --stat aa2c8db` over all of these is empty:
  - `backend/alembic`, `models.py`, `settings.py`, `routes/`, `repositories/`;
  - `pricing.py`, `outbound_safety_gate.py`, `offer_lifecycle.py`, `flow_contract.py`, `booking_*`;
  - `meta/`, `data/`, `docker-compose*.yml`;
  - no `*.csv` / `*.xlsx` path is touched.
- **OpenAI model and semantic prompt unchanged.** `semantic_interpreter.py` is byte-identical to R3 (default `gpt-4o-mini`).
- **Booking.** `BookingAuthority` is not modified.
- **Website.** `/var/www/ridecheck/faq.html` mtime is 2026-08-18.
- **Live read at 2026-11-17T16:31:48Z** (`docker inspect`, selected flag names only, no secrets):
  - backend image `sha256:4952d5d5…6903`, `minlaunch-candidate-aa2c8db`, `GIT_SHA=aa2c8db…`;
  - `OUTBOUND`, `SMART_BOOKING_CE`, `OPERATOR_WHATSAPP_NOTIFY`, `BOOKING_FLOW_DETAILS_PREFILL`, `CONVERSATION_ENGINE_DIRECT_WEBHOOK`, `EVIDENCE_ADMISSION`, `OUTSTANDING_QUESTION` and `LOCALITY_RESOLUTION_SAFETY` are all `false`;
  - `SHADOW_UNDERSTAND_ASYNC=true`, `SEMANTIC_SAME_TURN_TIMEOUT_SECONDS=6.0`, `RECONCILER_ACCEPTANCE_AUTHORITY_ENABLED=true`.
  - The same values as the R3 review.
- **Containers.** The 2-CPU host is shared with the live stack. The first four ad-hoc runs used a 2.5 GB cap; from then on every run was capped at ≤ 1.8 GB (mutants 1.0 GB × 2 slots), sequential otherwise. No `w1cr4-*` container or network remains.

---

## 11. Evidence

| Path | Content |
|---|---|
| `$E4/inputs.sha256`, `$E4/r3_porcelain_start.txt` | input hashes and the starting porcelain |
| `$E4/r3/` | backend+tests snapshot of the R3 tree (verified against R3's manifest) |
| `$E4/cand/`, `$E4/cand_manifest.sha256`, `$E4/cand_paths.txt` | the final full-tree candidate (v3) |
| `$E4/runs/` | junit and logs: `base`, `r3`, `r4` focused; `suites_final`; `r4_suite_v2`; `extra_r3`, `extra_r4_final`; `r4matrix`. `cmp.py` (R3's comparator) |
| `$E4/pg/` | `pg_r4.sh`, `pg_r4_final.txt`, junit |
| `$E4/probes/` | `p_r4.py`, `run_probe.sh`, `out_r3.md`, `out_r4.md` |
| `$E4/mut/` | `r4_mutants.py`, `run.sh`, `results_pass1.txt`, `results_pass2.txt`, `out/` |

Superseded or invalid artefacts are kept, named as such:
- **`cand_partial_invalid`, `*_partialtree_invalid*`.** My first snapshots held only `backend/` and `tests/`. Suites that read repository artefacts (the Flow JSON, the n8n export) failed on them: 65 failures and 11 errors, all environmental. They were discarded, and every comparison above was re-run on full-repository trees.
- **`cand_v1_superseded`, `cand_v2_superseded`, `r4_v1_superseded`, `pg_r4_v2*`.** Earlier freezes, kept for the record.

---

## 12. Remaining debt

| ID | Item | Classification |
|---|---|---|
| **R4-D1** | **Text path, operator PERDIDO in QUALIFYING plus a burst naming vehicle and zone.** The deterministic quote force and the transactional renderer still send the quote with its CTA and move the stage to QUOTED. The flush guard keeps PERDIDO/OTRO, and a following acceptance gets the WC-08 hold, so nothing is reactivated, issued or booked. The message still invites progression, and stage QUOTED with PERDIDO is incoherent. This is the same as R3, and the prompt scoped D.5's "no quote CTA" to the fallback cases. Extending the hold to every text-path quote renderer is broader than this milestone. | owner decision / next correction |
| R4-D2 | The hold copy is used for **every** PERDIDO fallback hold: operator-held, re-confirmed, and the engine's own decline. For an engine decline the customer can still reconsider by text, which "nuestro equipo tiene que revisarla manualmente" does not say. A motorcycle `tipo_vehiculo` submitted on a PERDIDO lead's vehicle fallback is also held, with no contact Flow. | owner copy review (L-8) |
| R4-D3 | WC-09 concern copy needs a **resolved** HESITATE in **QUOTED**. With no reading, "Me parece caro…" still gets the restated quote ("Genial!…" + CTA) plus the pending ack. SCHEDULING is excluded, so that its deterministic floors never wait on the model. | accepted minimum-launch debt |
| R4-D4 | A continuing burst with no question whose reading fails is acknowledged as "Recibí tu consulta…", then resolved silently. During an outage the ack repeats every failing turn. Re-reads stay bounded (3 rows / 4,000 characters), and rows are never dropped. | accepted (ops: alert on `PENDING_QUESTION` / `PERSIST_FAILED`) |
| R4-D5 | The turn-boundary safety net excludes `skipped_dedup`, which includes Layer D's rare `faq_no_reply` exit. | post-launch debt |
| R4-D6 | Shadow-worker backlog now yields "unavailable": retained plus ack, never inline. | behaviour change, intended (§3.4) |
| carried | R3RV-05 (an outbound row stuck in `pending` counts as delivered), WC-05 (the 20-message horizon for ordinary owed claims), WC-06 (`_rewrite_price` wording), the PostgreSQL fixture calendar dependence | unchanged from the R3 review |

## 13. Unresolved owner decision: a booking submission received after an operator's PERDIDO

**Not decided here, and the behaviour is deliberately unchanged.** Suppose a Booking Flow submission (booking-shaped: a token for an ISSUED offer) arrives for a lead an operator has marked PERDIDO. It is admitted and booked by `BookingAuthority` as today, and its projection sets `flag=ACEPTADO`. R4 excludes booking-shaped turns from the PERDIDO guard so as not to alter persisted-booking behaviour. The owner must choose one of:

- **(a)** honour the booking, so the customer's completed Flow wins; the current behaviour;
- **(b)** refuse it at admission and hand off, which needs an offer-withdrawal-on-operator-PERDIDO rule in the operator actions or in `BookingAuthority`;
- **(c)** book it but keep PERDIDO and flag it for review.

(b) and (c) touch `BookingAuthority` or the offer lifecycle, so they are out of this milestone's scope.

## 14. Exact next authorized step

The next step is a **focused R4 independent review** of the 9-path R4 delta (§1.1) against R3RV-01 to R3RV-04, §3.5, WC-08, WC-09 and R3-19/23b/25. It should use:
- `$E4/cand` (manifest sha256 `2ba471ea…9b15c`);
- `$E4/mut/` (rerunnable: `run.sh control`, `run.sh only <idx…>`);
- `$E4/pg/pg_r4.sh`;
- `$E4/probes/`.

It should look specifically at:
- `semantic_turn_evidence.py` (outside the core list);
- the guard's DB-current restore (§2, mutant R4-14);
- the three updated older tests (§7.4);
- the owner items R4-D1 and R4-D2 and §13.

No commit, push, build, kit update, deployment, Meta change, tester contact or WILD comes before that review's verdict.

**VERDICT: READY FOR FOCUSED R4 INDEPENDENT REVIEW**
