PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R3-FOCUSED-INDEPENDENT-REVIEW

# WILD1-C-R3: focused independent review

- **Date.** 2026-11-17 (UTC).
- **Reviewed candidate.** Uncommitted R3 tree on base `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008`, worktree `/opt/ridecheck-crm-wild1-correction`, branch `fix/prelaunch-wild1-conversation-correction`.
- **Evidence supplied.** `$E=/opt/ridecheck-builds/crm-wild1-c-r3-20261116T231655Z`.
- **Review artefacts.** Probes, mutants, logs and junit are in `/tmp/claude-0/-opt-ridecheck-crm-wild1-correction/340aa827-46ad-4d4f-b7c8-d60afb9d507e/scratchpad/rev/`. This is a session scratchpad and is not durable; the facts needed to act on this audit are reproduced below.
- **Inputs read in full.**
  - R2 closeout: sha256 `6c0352b78ed1113a60107289919f9c3d2a8e136f68366bca72d1f29d35c5b922`.
  - Original audit: sha256 `e98a0eadf77b0f69c3456b94b6860bcac8c9112670ce34067192cc2ef2fa0fbc`.
  - R3 closeout: sha256 `f27affd84fd79fa63c019e4080086661963e059070382dc44c671f2cf7293e06`.

**VERDICT: CORRECTION REQUIRED**

WC-03 passes independently. WC-01, WC-02 and WC-04 do not. Two defects block a READY verdict:

1. **R3RV-01 (BLOCKER, WC-01 not closed, with a regression R3 introduced).** Supported FAQ questions that the general-information classifier does not tag as informational are still silently lost when the reading is late or unavailable: no answer, no acknowledgement, no PENDING row, and no answer on the next turn. Examples are "¿Cuándo me mandan el informe?", "¿Y el informe cuándo llega?", "Hola, tengo un auto para revisar. ¿Qué me entregan después?" and the voice-style "necesito revisar una camioneta que le revisan". The reviewer probe lost 16 of 28 failure-mode turns this way. For 8 of those 16 (a reading arriving at about 8 s), **R2 answered the question and R3 now drops it.** The cause is R3's "never wait twice" change, combined with PENDING retention being limited to MAY_ASK bursts.
2. **R3RV-02 (HIGH, WC-02 not closed).** The PERDIDO flush guard is armed only in `_process_text`. The vehicle-fallback and location-fallback Flow submissions reach `_process_flow_response` and write `lead.flag = "PRESUPUESTO_ENVIADO"` unguarded. Reproduced four ways with real operator actions:
   - an operator-written PERDIDO is overwritten;
   - a quote with a CTA is sent;
   - a stale `motivo_perdida` stays on a non-PERDIDO lead;
   - an engine decline is reactivated with no RECONSIDERED row.

   The closeout's claim of "one chokepoint for every engine write path" is therefore not true.

The operator-provenance mechanism itself is sound. Every kanban/API state-table scenario behaves as documented (10/10), and the five original audit inputs pass the full 40-case async matrix.

This audit does not authorize a commit, push, build, kit update, deployment, Meta change, tester contact or WILD.

---

## 1. A. Candidate and evidence integrity

| Check | Result |
|---|---|
| HEAD / branch | `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008` / `fix/prelaunch-wild1-conversation-correction` |
| Index | empty (`git diff --cached` lists 0 paths) |
| Worktree status | 53 paths: 43 `M` and 10 `??`. That is the 52 candidate paths plus the R3 closeout. Porcelain sha256 is `a855918f5b4731ef…` at start and at end of review. |
| R3 closeout hashes | The R2 closeout and the original audit match the hashes quoted in R3 §"Inputs" (`6c0352b7…`, `e98a0ead…`). |
| Final manifest | `sha256($E/cand_manifest_final.sha256)` = **`b5ace40fe28a83b918bd072d01237b57e01b3e92a9fcd7e9fbfca25d05352172`** (matches). `sha256sum -c` against the worktree: 52/52 OK. |
| `$E/cand` vs worktree | `diff -rq` over `backend/` and `tests/`: no difference. All 52 manifest paths are `cmp`-identical. |
| `$E/r2` provenance | R2 snapshot blobs match the original audit's R2 hashes: engine `58c3ecc918eba8e2`, `claim_coverage` `cd040b76864821be`, `quote_decision_history` `2faea303681bec71`. |
| Other writers | One other Claude session (pid 2858522, started 2026-11-16 20:11, almost certainly the R3 author) has this worktree as its cwd. It is idle (state `Sl+`, no child processes), and no file in the worktree changed after the closeout was written. Every other session's cwd is a different worktree. |
| Writes by this review | This audit file only. Every test, probe and mutant ran on a read-only mount (`:ro`) or on a throwaway copy in the scratchpad. |

### 1.1 Independent R3 delta (`diff -rq $E/r2 worktree`)

The delta is **10 paths, not 9**: 5 production and 5 test. The closeout text says "9 paths", but its own table lists 10. This is a counting error only; nothing is missing.

| Path | Δ vs R2 | sha256 | git blob |
|---|---|---|---|
| `backend/app/services/conversation_engine.py` | +654/−63 | `b4530c9f1f1e9403d8fb990cdac8d4a5d57913ee234573cb5def84999b26ab56` | `76fc9781…` |
| `backend/app/services/claim_coverage.py` | +112/−7 | `d345c9d23308d411632c23948d7c74944f1a04cad0e7bfa5ac88ccfacf25b3e1` | `3a6f8a59…` |
| `backend/app/services/quote_decision_history.py` | +79/−5 | `d571dfcbe39342fb0d838edf2f787fb1fef990764a321fba59509b0e2fc92c86` | `5851c905…` |
| `backend/app/ui/kanban_actions.py` | +34/−0 | `a58d9d693d685f23399c80e2d63f09b834032958a4c46028691336fcb4a1aadc` | `ffe77fb2…` |
| `backend/app/api/leads.py` | +13/−0 | `1e53ef544f6e84b3d0ca544517f29ec23097a2f956598dc4865aebc77e64ed5f` | `8ec9ea4a…` |
| `tests/_wild1c_harness.py` | +41/−1 | `4e103cfe…` | `396676ce…` |
| `tests/test_wild1_c_h1_regression.py` | +6/−1 | `b960aa06…` | `7fee4baf…` |
| `tests/test_r5_f1_template_validation_contract.py` | +1 | `68815fc7…` | `01b24e8e…` |
| `tests/test_wild1_c_live_async_posture.py` | new | `39adee6c…` | `9e315542…` |
| `tests/test_wild1_c_r3_async_and_operator_authority.py` | new | `3c62200d…` | `07025390…` |

The production paths are exactly the five the prompt names.

- `kanban_actions.py` and `leads.py` were byte-identical to base in R2 (`0df48cf5…`, `8fe74a1f…`). R3 is their first change.
- `kanban.py`, `kanban_view.py`, `response_validator.py`, `response_composition.py`, `semantic_interpreter.py`, `schemas/turn_evidence.py` and `models.py` are identical to R2.

### 1.2 Protected scope

Every file below is byte-identical to base (`git diff --quiet aa2c8db`):
- `models.py`;
- `settings.py`;
- `pricing.py`;
- `data/pricing_base.csv`, `data/viaticos_zones.csv`;
- `booking_authority.py`, `offer_lifecycle.py`, `flow_contract.py`, `outbound_safety_gate.py`;
- `routes/flow_data_exchange.py`, `routes/whatsapp.py`.

The tree also contains:
- no `alembic`/`migrations` or `meta/` path;
- no gate, compose or deployment file;
- no interpreter-prompt change in R3 (`semantic_interpreter.py` is identical to R2).

The model remains `gpt-4o-mini`. The default is at `semantic_interpreter.py:727/737` (same lines as base) and `settings.py:159/277`; the live backend has no `OPENAI_CHAT_MODEL`.

---

## 2. B. WC-01: asynchronous question coverage

### 2.1 Implementation review

- **Typed boundary, no phrase encyclopedia.**
  - `claim_coverage.question_potential` reuses two things that already exist: Layer D's route classifier (`_detect_general_information`) and the single anchored greeting entry of `_FAQ_PATTERNS`. `_GREETING_ONLY_RE` takes that entry by identity, not as a copy.
  - Segments are split on punctuation.
  - The function is total: an exception yields MAY_ASK.
  - No new Spanish phrase list was added. This part is sound.
- **GREETING_ONLY is genuinely limited.** The real route was run with a 3.5 s reading, and the class was read back from `_question_potential` (18 inputs):

| Input | Class | Turn time | Outcome |
|---|---|---|---|
| "hola", "Hola!", "buenas", "Buenas tardes!", "hola, buenas tardes", "buen dia" | GREETING_ONLY | 0.06–0.19 s | no wait, nothing pending |
| "Hola, ¿qué revisan?" | MAY_ASK | 3.54 s | scope answered |
| "Buenas, quería consultar qué incluye" | MAY_ASK | 3.54 s | scope answered |
| "hola buenas tardes queria saber que revisan" (voice style) | MAY_ASK | 3.56 s | scope answered |
| "hola, consulta" | MAY_ASK | 3.55 s | nothing to answer; not pending |
| "Hola, tengo un auto para revisar" | NOT_INFORMATIONAL | 3.53 s | commercial path (scope answered from the reading) |
| "hola buenas", "hola buenas tardes", "Holaa", "hola hola", "Hola 👋", "buenas, como va?" | NOT_INFORMATIONAL | 3.53–3.55 s | commercial path waited for the reading |

No question was ever classified GREETING_ONLY; the grammar is anchored, so that is true by construction.

The opposite error does exist. Unpunctuated voice greetings ("hola buenas", "hola buenas tardes") are **not** GREETING_ONLY, so they wait on the commercial path for the reading (up to the 6.0 s timeout). That is a latency cost, not a loss; it is listed under R3RV-03.

- **MAY_ASK wait (`_bounded_question_reading`).**
  - Budget is `min(4.5, semantic_same_turn_timeout_seconds)`. A 2.0 s configured timeout gave a turn of 2.12 s; 6.0 s gave 4.58 s. The wait never exceeds the configured timeout.
  - The result is memoised in `_turn_cov_evidence`.
  - `_semantic_turn_evidence` refuses a second wait on the same provider once it has timed out.
  - Interpreter calls per burst: exactly 1 when the reading arrives, and 1 + 1 re-read when it is pending. There are **no duplicate interpreter requests**.
  - Terminal pre-gates and greeting-only bursts never wait.
- **But the turn-level budget is not 4.5 s** (R3RV-03, §2.5). The 4.5 s holds for QUALIFYING Layer D turns only.
  - In QUOTED, an earlier consumer waits the full 6.0 s before Layer D's bounded read (matrix: 6.06–6.10 s).
  - A turn that also re-reads an earlier PENDING question waits on **two providers in sequence**: measured 8.04 s and 9.05 s.

  "A turn never waits twice" is true per provider, not per turn.

### 2.2 Async matrix, reproduced independently (real route, `SHADOW_UNDERSTAND_ASYNC=true`, 6.0 s)

Setup:
- Reviewer-written interpreter seam (`rev/p_async.py`), not the R3 test's `FirstReadingInterpreter`.
- Each case has a third, neutral turn added ("Perfecto gracias").
- Between turns, the shadow worker is drained and every `pending-question-resolution` thread is joined.

Result: **40/40 correct, 0 silently lost.**

| Mode | QUALIFYING T1 | QUOTED T1 | T1 outcome (all 5 inputs) | T2 outcome | T3 |
|---|---|---|---|---|---|
| immediate | 0.07–0.25 s | 0.07–0.21 s | answered, complete, no ack, nothing pending | — | no repeat, no ack |
| ~3.5 s | 3.54–3.60 s | 3.54–3.55 s | answered same turn | — | no repeat, no ack |
| timeout (8 s reading) | 4.59–4.68 s | **6.06–6.10 s** | ack; not complete; one `PENDING` row, reason `SEMANTIC_TIMEOUT`; `source_message_ids` ⊆ the thread's inbound WAMIDs | answered (QUALIFYING: with the 140.000 quote block in the same message); rows `PENDING, RESOLVED`; no ack | no repeat, no ack; scope sent exactly once overall |
| unavailable (raises) | 0.07–0.12 s | 0.07–0.14 s | same as timeout, reason `SEMANTIC_UNAVAILABLE` | same | same |

**Ordering.** In QUALIFYING T2 the resolved answer travels in the same message as the commercial step. It sits after the quote and its CTA, which is the plan order recorded as R2 L-7. On the Flow channel the preamble precedes the CTA. In no case was the answer displaced by the progression.

### 2.3 Consecutive failures, then recovery (`rev/p_async.py outage`)

| Sequence | Per-turn result | Recovery turn | After |
|---|---|---|---|
| 2 × raise | T1 ack + PENDING. T2 "¿Y aceptan débito?": payment floor answered + ack, second PENDING. | scope (+ report) answered, payment **not** repeated, 2 RESOLVED, no ack | 2 PENDING / 2 RESOLVED, distinct bursts, 2 owed rows (unique), scope ×1, payment ×1, ack ×2 |
| 2 × 8 s hang | T1 4.57 s, ack + PENDING. T2 **8.04 s**: the T1 re-read arrived inside the second wait, so the T1 question was resolved and answered, and T2's own reading timed out (ack, new PENDING). | answered, RESOLVED | 2/2, no duplicates |
| 4 × raise | ack on all 4. T3 ("¿Cuándo me mandan el informe?") wrote **no** PENDING row (§2.4). | scope + report answered (the report only because the scope answer carries it) | 3 PENDING / 3 RESOLVED, ack ×4 |

- **Duplicates and contradictions.** No duplicate PENDING rows, no duplicate owed rows, and no contradictory replies.
- **Bounded accumulation.** Accumulation is bounded by the number of failing MAY_ASK bursts in the cycle. The stored `texts` are capped at 4000 characters per row.
- **No cap on re-read size.** The re-read concatenates every unresolved row's texts into one interpretation, with no row cap. During a long outage this grows linearly; see §8.

### 2.4 R3RV-01 (BLOCKER): supported questions outside MAY_ASK are still silently lost, and a late reading now loses them where R2 answered

`_note_semantic_unavailable` retains a burst only when `question_potential` returns MAY_ASK, which requires the Layer D route classifier to accept it. A supported question on any other route therefore gets none of R3's protection:
- the bounded wait is replaced by the commercial path's 6.0 s wait;
- there is no acknowledgement;
- no PENDING row is written;
- nothing is answered on the next turn.

Closeout §2 ("the commercial path waited and got nothing, a burst that may ask something is retained as pending") claims otherwise. Its only test, `test_commercial_path_question_is_retained_too`, passes because "Es un Toyota Corolla 2018, ¿qué revisan?" happens to be classified MAY_ASK.

**Probe** (`rev/p_commercial.py`). 7 supported inputs × {QUALIFYING, QUOTED} × {raise, 8 s reading, ok}, with a fresh thread per case. The follow-up turn is the same as in the matrix. The identical script was also run on the **R2 backend** (`$E/r2/backend`, same tests and harness).

| Input (claim) | Class (R3) | raise: R2 → R3 | 8 s reading: R2 → R3 |
|---|---|---|---|
| c1 "¿Cuándo me mandan el informe?" (report) | NOT_INFORMATIONAL | lost → **lost** | T1 answered (8.0 s) → **lost silently (6.1 s)** |
| c2 "Tengo un Corolla 2018 en Palermo, ¿qué revisan?" (scope) | MAY_ASK | lost → T2 | T1 → T2 |
| c3 "Quiero revisar un auto antes de comprarlo, ¿qué incluye?" (scope) | MAY_ASK | lost → T2 | T1 → T2 |
| c4 "Hola, tengo un auto para revisar. ¿Qué me entregan después?" (report) | NOT_INFORMATIONAL | lost → **lost** | T1 → **lost silently** |
| c5 "¿Qué miran en la revisión?" (scope) | MAY_ASK | lost → T2 | lost → T2 |
| c6 "necesito revisar una camioneta que le revisan" (scope, voice) | NOT_INFORMATIONAL | lost → **lost** | T1 → **lost silently** |
| c7 "¿Y el informe cuándo llega?" (report) | NOT_INFORMATIONAL | lost → **lost** | T1 → **lost silently** |

The table shows QUALIFYING; QUOTED gives the same outcome on every row. In the lost cases the reply was "¿Estás pensando en comprar un vehículo y querés que lo revisemos antes de decidir?" (QUALIFYING) or the repeated quote with "Si te parece bien, podemos avanzar." (QUOTED). In every lost case: no ack, no row, nothing on T2.

Totals over 28 failure-mode turns:
- **R3: 16 lost, all 16 silent.** That is 8 lost on both trees and **8 regressions**.
- **R3 fixed 8.** These are the MAY_ASK rows c2, c3 and c5 under raise, plus c5 under a late reading.

**Cause of the regression.** On R2 a second consumer waited again on the same timed-out provider. That stacked waits (8.0 s turns), but it received the late reading and answered. R3 added an early return in `_semantic_turn_evidence` (the "never wait twice" fix, R3-08), but only MAY_ASK bursts get the compensating PENDING retention. For NOT_INFORMATIONAL bursts the late reading is now discarded.

**Causation verified.** A copy of the candidate with only that early return reverted answers c1, c4, c6 and c7 again on T1, at 8.03–8.11 s.

**Reachability.** These are ordinary customer phrasings of two supported claims (REPORT, SERVICE_SCOPE). The real provider's latency, measured in the original audit (n=72), was p95 3.3 s, max 8.2 s, with 2/72 calls over 6.0 s. Provider failures add to that. This is the same defect class as the original BLOCKER (L-2), on a different route.

**Required correction.** Every supported-claim burst whose reading is late or unavailable, on any non-terminal route, must either be retained as PENDING (with ack, persisted, resolved next turn) or wait within the bound. Retention cannot be keyed to the Layer D classifier alone. Tests must include the c1/c4/c6/c7 shapes in both stages under raise and under a late (6–12 s) reading.

### 2.5 R3RV-03 (MEDIUM): the turn-level semantic wait is not bounded at 4.5 s

Measured on the final tree:
- QUOTED MAY_ASK timeout: **6.06–6.10 s**. An earlier consumer waits the full `semantic_same_turn_timeout_seconds` before Layer D's bounded read.
- Turn with a pending re-read and a slow provider: **8.04 s** and **9.05 s**. `_bounded_question_reading` waits 4.5 s, then `_pending_resolution` waits up to another 4.5 s on its own provider.
- Unpunctuated voice greetings wait on the commercial path, up to 6.0 s.

Every individual wait is bounded and none reaches the provider's 30 s timeout. The n8n "Call Backend Engine (M18)" node sets no explicit timeout in the 2026-09-04 export.

Classification: not a loss, but the closeout's "4.5 s, never twice" contract is not what the code does. It should be fixed together with R3RV-01 by giving the whole turn one deadline.

### 2.6 Daemon thread, session lifetime and transactions

- **Threads.** `_dispatch_pending_resolution` starts one `threading.Thread(daemon=True)` per turn that has unresolved rows. The thread runs only `SemanticTurnInterpreter.interpret` (HTTP + parse). I read it and found no DB, Session or ORM access. `_build_shadow_context` runs on the request thread before dispatch. The result is read only through `TurnSemanticEvidence.get()` on the request thread, so **no closed Session is used and nothing mutates CRM state after the turn ends.**
- **Leaks.** After every probe turn the reviewer joined the threads: 0 `pending-question-resolution` threads were alive at turn end in 40/40 matrix cases and in all outage runs, and at process exit only `MainThread` and `shadow-understand` remained. A thread's lifetime is bounded by the interpreter's own HTTP timeout (30 s). There is no concurrency cap.
- **Failed thread start.** If `Thread.start()` fails, `TurnSemanticEvidence` marks the provider undispatched. `get()` then runs the interpretation **inline** on the request thread, bounded only by the 30 s HTTP timeout and not by the 4.5 s budget. This is an edge case (thread exhaustion); see §8.
- **Test teardown.** The harness's `W.run` and the R3 tests' `drain()` drain only the shadow worker. Neither joins the raw pending-resolution threads. In the current tests the re-read is immediate and finishes inside the turn's own wait, so no thread outlived a patch scope in the reviewer's runs. This is a test-hygiene gap, not a product defect.
- **Savepoints.**
  - `_persist_pending_question`, `_pending_resolution` and `_persist_owed_claims` all write inside `db.begin_nested()` and catch exceptions after the savepoint has rolled back.
  - The R3 test `test_owed_claim_duplicate_rolls_back_savepoint_only` forces a real `UNIQUE(thread_id, claim_id)` collision. It shows that the turn still completes and its other writes commit, on SQLite and on PostgreSQL (§5.4).
  - Removing either savepoint is a reviewer mutant (R3-23, R3-23b); see §6.
- **Failed/blocked rows never answer.**
  - `claim_coverage.delivered()` excludes `failed` and `blocked` in both `Ledger.outstanding` and `_answered_after`. The killing test is the ledger unit test.
  - The outbound gate writes only `pending`, `sent`, `failed` and `blocked`.
  - Residual: a row stuck in `pending` (a process crash between the gate's insert and the Meta response) counts as delivered. LOW, §8.
- **PENDING 23 / RESOLVED 22 on PostgreSQL.** The unresolved row comes from `AsyncBoundaries::test_wait_is_bounded_by_the_route_budget`. That test sends "¿Qué revisan?" with a 12 s reading, asserts the ack and the budget, and makes no second turn. The arithmetic matches:
  - the matrix gives 20 PENDING + 20 RESOLVED;
  - `test_commercial_path_question_is_retained_too` gives 1 + 1;
  - `test_resolved_question_never_repeats_what_was_already_answered` gives 1 + 1;
  - the budget test gives 1 + 0.

  The reviewer's own PostgreSQL run confirms it by row content (§5.4). It is isolated test data on a test-only thread, not a lost production question.

**WC-01 verdict: FAIL.** The original five inputs are closed in all 40 combinations. But the same defect class persists on non-Layer-D phrasings, and R3 introduced a regression for late readings (R3RV-01).

---

## 3. C. WC-02: operator authority

### 3.1 Independent inventory of production writes to `lead.flag` / `lead.motivo_perdida`

Method:
- grep for attribute assignment, `setattr`, ORM `update(Lead)`, raw `UPDATE leads` and keyword construction across `backend/app`;
- then read every hit.

SQL `WHERE l.flag = …` filters in `quote_followup.py` and `buscando_followup.py` are reads, not writes.

| Writer | Fields | Provenance (R3) | Reviewer note |
|---|---|---|---|
| `kanban_actions.ui_perdido` (`/ui/perdido`) | flag, motivo | always | ✔ |
| `kanban_actions.ui_lead_flag_set` | flag, motivo (cleared unless PERDIDO), `buscando_auto_set_at` | always | ✔ |
| `kanban_actions.ui_lead_flag_clear` | flag, motivo | always | ✔ |
| `kanban_actions.ui_move`, `ui_move_lead`, `ui_lead_move` | motivo (cleared if not PERDIDO), estado | only if flag/motivo changed | ✔ (0 rows on a no-change move) |
| `api/leads.update_lead` (`PATCH /leads/{id}`) | flag, motivo, estado | when the payload carries flag/motivo, or they changed | ✔ (0 rows on estado-only or `nombre`-only PATCH) |
| `api/leads.create_lead` | initial flag | none | new lead, no thread, nothing to revoke: acceptable |
| `booking_authority._project` via `LeadUpdate(flag="ACEPTADO")` (Data Exchange booking and `_converged_flow_book`) | flag | none | booking authority, not operator. A persisted booking on a PERDIDO lead sets ACEPTADO. This predates R3 and is outside this review; see §8. |
| `conversation_engine` | many | n/a | engine (guarded only in `_process_text`, §3.4) |

There is no other non-engine writer. The provenance writer:
- appends one row per thread of the lead, with a fresh `uuid4` `claim_id`;
- flushes and never commits;
- runs before the caller's `commit` / `commit_or_400`.

The R3 test `test_provenance_and_mutation_roll_back_together` and mutant R3-16 (§6) confirm both-or-neither.

**Recorded values** (PostgreSQL and SQLite, reviewer probe):

- `flag_set`, then `perdido`:
  - `{action: flag_set, route: /ui/lead_flag_set, flag_before: PERDIDO, flag_after: PRESUPUESTO_ENVIADO, motivo_before: PRECIO, motivo_after: null}`;
  - `{action: perdido, flag_before: PRESUPUESTO_ENVIADO, flag_after: PERDIDO, motivo_before: null, motivo_after: PRECIO}`.
- Re-confirmation: `{flag_before: PERDIDO, flag_after: PERDIDO, motivo_before: PRECIO, motivo_after: PRECIO}`, producer `OPERATOR_UI`.
- API: producer `OPERATOR_API`, route `PATCH /leads/{id}`.

### 3.2 State table (real kanban/API actions on real Sessions; customer turn "dale, avancemos" ACCEPT through the real route)

| # | Scenario | Result | Decision history |
|---|---|---|---|
| 1 | engine decline → reconsideration | **ACEPTADO / SCHEDULING** (reactivates) | DECLINED, RECONSIDERED |
| 2 | operator PERDIDO, no engine decline | PERDIDO/PRECIO, QUOTED, held | OPERATOR |
| 3 | decline → flag_clear → perdido(PRECIO) | held | DECLINED, OP, OP |
| 4 | decline → flag_set(PRESUPUESTO_ENVIADO) → perdido(PRECIO) | held | DECLINED, OP, OP |
| 5 | decline → operator re-confirmation (`/ui/perdido` PRECIO) | held | DECLINED, OP |
| 6 | decline → API PATCH flag=PERDIDO motivo=PRECIO | held | DECLINED, OP(API) |
| 7 | decline → operator motivo OTRO | held (PERDIDO/OTRO) | DECLINED, OP |
| 8 | legacy authority off + operator PERDIDO | held, `action=replied`, exactly 1 delivered reply, no Flow | OP |
| 9 | legacy authority off + decline + re-confirmation | held, replied | DECLINED, OP |
| 10 | decline → operator reopen (flag_set) → new customer refusal → reconsideration | **reactivates** (the newest row is the engine's own new DECLINED/OTRO, as documented) | DECLINED, OP, DECLINED, RECONSIDERED |

10/10 match the documented owner rule. No held case issued a token, offer or booking.

The legacy hold reply in case 8 is the quote again with "Si te parece bien, podemos avanzar." This is WC-08 (§8).

### 3.3 `_arm_perdido_guard`: lifecycle analysis

| Question | Finding |
|---|---|
| Installed once per relevant turn | Armed once at `_process_text` start, and only if the lead is PERDIDO; it first disarms any previous guard on the instance. The engine is per request. |
| Removed in a `finally` | **No.** `_disarm_perdido_guard()` is the last statement of `handle()`, after the `try/except Exception` that wraps `_handle`. That covers in-turn exceptions (probe: `action=error`, guard ref cleared, 0 listeners). But `_revoke_undelivered_slot_claim`, `_turn_plan` and `_write_hybrid_trace` run outside the `try`. With `_turn_plan` forced to raise, the exception propagated and the listener **stayed attached** to that request's Session (R3RV-04, LOW). |
| Leak into another lead/session/test | Production sessions are per request (`get_db` → `SessionLocal()` → `close()`). A leaked listener can only see the rest of that request: the route's `ai_events` telemetry write, which does not touch the lead. The closure compares the *captured* lead instance, so a later load of the lead in a new Session cannot trigger it. |
| Can it undo an authorized operator mutation? | Not in-process: operator actions use their own Session. For a concurrent operator commit during a CE turn, the restore sets the CE instance back to its loaded value. SQLAlchemy then sees no net change and emits no `UPDATE` of `flag`, so the operator's committed value stands. Reasoned from the code, not raced live. |
| Kill switch | The guard stays armed through the blocked-dispatch commit. The R3 side-door kill-switch test passes, and the focused run includes it. |
| Recursion during flush | The listener only assigns two scalar attributes. A lazy reload inside `before_flush` cannot re-enter flush, because SQLAlchemy suppresses autoflush while flushing. Side-door runs logged one `PERDIDO_PROTECTED` per attempt. |
| SQLAlchemy history | After a restore the attribute equals its committed value, so no column is written. The DB row read back afterwards equals the held values. |

### 3.4 R3RV-02 (HIGH): Flow-response side doors bypass the guard and the lost-lead rule

`_process_flow_response` routes `tipo_vehiculo` and `zona_general` payloads to two handlers before any booking logic:
- `_process_vehicle_fallback_response`, which writes `lead.flag = "PRESUPUESTO_ENVIADO"` at `conversation_engine.py:8684`;
- `_process_location_fallback_response`, which writes it at `:8874`.

Both commit and send a quote. Neither checks PERDIDO, and the guard is never armed on this route.

Reproduced through the real route (`message_type=flow_response`), on SQLite and on PostgreSQL:

| Scenario | Before | After | Sent |
|---|---|---|---|
| operator PERDIDO in QUALIFYING, location-fallback Flow submitted | PERDIDO / OTRO | **PRESUPUESTO_ENVIADO / OTRO**, QUOTED | "Genial! Te paso la cotización…" + block + CTA |
| operator PERDIDO in QUALIFYING, vehicle-fallback Flow submitted | PERDIDO / OTRO | **PRESUPUESTO_ENVIADO / OTRO** | quote + CTA |
| engine decline → operator re-confirms → location-fallback submitted | PERDIDO / PRECIO | **PRESUPUESTO_ENVIADO / PRECIO** | quote + CTA |
| engine decline, then location-fallback submitted | PERDIDO / PRECIO | **PRESUPUESTO_ENVIADO / PRECIO**, no RECONSIDERED row | quote + CTA |

Consequences:
- An operator-written PERDIDO is overwritten, which breaks the owner rule directly.
- The lead re-enters `quote_followup`'s `flag='PRESUPUESTO_ENVIADO'` selection, so the follow-up job will chase it.
- A non-PERDIDO lead keeps a `motivo_perdida`.
- An engine decline is reversed with no history entry.

Reachability: a fallback Flow left unsubmitted while the operator marks the lead lost, followed by a late submission. The handler also accepts unsolicited submissions, which it records as such.

This code is the same as base and R2, so it is not an R3 regression. But WC-02's scope is "an operator-written PERDIDO is never reactivated", and R3 claims one chokepoint "for every engine write path". **WC-02 verdict: FAIL.** The fix belongs at the engine's flag writers or at `handle()` level, not in n8n.

### 3.5 `_apply_lost_lead_rule` `TypeError`

Both `_LEGACY_ALLOW` callers (`:9420` and `:10508`) gate on `_operator_lost_lead`, which is `flag == PERDIDO and _engine_written_decline is None`, the same predicate the rule uses. A non-dataclass decision can therefore reach `dataclasses.replace` only in one way: an operator row has to commit between the two `_engine_written_decline` reads of one turn, on the authority-off branch.

Live runs `RECONCILER_ACCEPTANCE_AUTHORITY_ENABLED=true`, read-only, at both live reads. The code default is `False`, so the branch is not unreachable by construction; it relies on the live setting.

If it fires: the turn ends `action=error`, it is rolled back, the PERDIDO holds, and the customer gets no reply.

Not reachable by ordinary or legacy input under the production contract. **Classification: post-launch debt.** Recommended for the same correction, since it is one line.

### 3.6 Newest-event selection

`thread_evidence.for_thread` orders by `id` ascending, per thread, across cycles. `_engine_written_decline` takes `rows[-1]` and additionally refuses if any non-ENGINE row has a higher id. Operator rows are written to every thread of the lead.

Old engine DECLINED rows never outrank a later operator row: state-table cases 3–7 pass, and mutants MI-4 and R3-18 (§6).

---

## 4. D. WC-03: website-form quote, rendered independently (`rev/p_web.py`, real route, sync and live-async)

```
Perfecto, Prueba! Recibí tu solicitud para revisar el Jeep Renegade en Berazategui. Te paso la cotización:

Revisión: $150.000
Viáticos: $90.000
Total: $240.000

¿Qué día y horario te viene mejor?
```
```
Perfecto, Prueba! Recibí tu solicitud para revisar el Toyota Corolla en Palermo. Te paso la cotización:

Revisión: $140.000
Viáticos: $0
Total: $140.000

¿Qué día y horario te viene mejor?
```
```
Perfecto, Prueba! Recibí tu solicitud para revisar el Jeep Renegade en Pilar. Te paso la cotización:

Revisión: $150.000
Viáticos: $1.234.567
Total: $1.384.567

¿Qué día y horario te viene mejor?
```

All 6 renders (3 cases × 2 postures) satisfy every check:
- exactly 1 block and 1 "Revisión: $" line;
- the arithmetic reconciles;
- exactly 1 `?`, so one CTA;
- no "podemos avanzar";
- no sentence calls a total "la revisión";
- the reply model's hostile `$99.999` is absent;
- the form's own "Total estimado" is never echoed (the zero case's form said $999.999, the large case's said $1);
- the flag is PRESUPUESTO_ENVIADO.

**The declared PRICE claim is really validated**, not just present in the source:
- Replacing the declared tuple at runtime with a reconciling but wrong split (160.000/80.000/240.000) makes the validator refuse the handler's message, and the safe line is sent.
- A total-only tuple is refused the same way.

The amounts come only from `PricingQuote` (`_quote_breakdown`). **WC-03 verdict: PASS.**

---

## 5. E. WC-04: evidence fidelity

### 5.1 Live-async twins

`test_wild1_c_live_async_posture.py` subclasses every TestCase of both WILD1-C suites. With `LIVE_ASYNC=True` per class and per test:
- `cfg()` is `cfg_live_async()`: `shadow_understand_async=True` and `semantic_same_turn_timeout_seconds=6.0`. `LivePostureIsReallyAsync` asserts both values, and that the sync default is restored afterwards.
- `W.run` goes through the real route (`_sb2b_harness.run_turn` → `app.api.conversation.handle_message`) and drains the shadow worker inside the patch scope.
- SQLite gets a fresh database per class (`W.engine()`).

Two limitations:
- The twins' interpreter is **immediate**. They prove the async plumbing, not delay behaviour; delay behaviour lives only in the R3 matrix, which covers the q1–q5 shapes.
- Neither harness joins the pending-resolution threads (§2.6).

These limitations are why the R3 suite did not see R3RV-01.

### 5.2 PostgreSQL booking-fixture change (`CONFIRM_DAYS_AHEAD`)

The change is test-only: the twin books `today + 2` instead of `today + 1`. The conflict it avoids, `BookingSlotConflictError` for a second thread on the single launch professional's slot, is the product working correctly (single-resource occupancy). It does not hide a product conflict.

Residual: the fixture is calendar-dependent. On a Friday the twin's day is a Sunday, the same class of exposure as the Saturday case the closeout records. Note only.

### 5.3 Focused controls on the final tree (SQLite, read-only mount, `--network none`)

20 files:
- WILD1-C sync (2), live-async twins (1), R3 (1);
- motorcycle (2);
- booking/Flow authority (5: `wild1_booking_flow_gate_and_contract`, `l4_7w4_f1`, `m21_3_c_d`, `sb2b_option_b`, `convergence_p3_cutover`);
- price contract (3: `r6_structured_response_contract`, `l4_7d_response_validator`, `m20_6b1`);
- template / pre-persistence copy (`r5_f1_template_validation_contract`);
- `r6_c4a_transactional_provenance`;
- kill switch (3);
- `w1f_w01_r2_outstanding_question`.

The website-form and pre-persistence-copy guards run inside the WILD1-C/R3 files.

**Result: 778 passed, 12 skipped, 0 failed, 1666 subtests passed (304.6 s).**

### 5.4 PostgreSQL 16

Setup:
- disposable `rev3*-pg` on tmpfs, on an `--internal` network;
- schema = the pre-schema record + `alembic stamp` + `upgrade head` → `20261104_vehicle_identity`;
- every container and network torn down by exact name (0 left).

| Run | Result |
|---|---|
| R3 suite `test_wild1_c_r3_async_and_operator_authority.py` | **74 passed** (152.9 s). `quote_decision` 33, of which operator 14. PENDING 23 / RESOLVED 22. Duplicate `(thread_id, claim_id)` 0. `validated_as` is `jsonb`. |
| Unresolved PENDING attribution | exactly 1 row: thread 48, `SEMANTIC_TIMEOUT`, texts `["¿Qué revisan?"]`, 0 later inbound messages. That is `test_wait_is_bounded_by_the_route_budget` (§2.6). |
| Reviewer WC-02 probe (`p_auth`) | identical to SQLite: 10/10 state-table cases as documented; all 4 side doors **reproduce** (R3RV-02) |
| Reviewer WC-03 probe (`p_web`) | 6/6 renders correct (§4) |
| Reviewer outage probe on a fresh DB | PENDING → RESOLVED append-only; 0 duplicate rows; scope ×1 and payment ×1 per thread; ack ×(failing turns) |
| Motorcycle probe (`p_moto`) | §9 |

**Reviewer fixture artefact, recorded for transparency.** One PostgreSQL run executed two probe processes against one database. In that run the outage sequence repeated already-answered claims on the turn after resolution. The cause was my fixture, not the product: the Meta mock's outbound wamid counter restarts per process, so the second process reused `wamid.SB2B.OUT.000001…` already written by the first. On a fresh database the same sequence is clean (above), and so is a single-process diagnostic run. Production wamids are unique, so this has no product consequence. It does show that the ledger relies on outbound wamid uniqueness.

---

## 6. Independent mutation sample (final tree)

Method:
- **Own anchors.** The reviewer wrote the 20 mutants (`rev/mut/mutants.py`). Most use different anchors from the supplied `$E/mut/r3_mutants.py`. R3-18 takes two hunks.
- **Exactly once.** Each hunk was checked to apply exactly once to a fresh throwaway copy of the final tree (`rev/mut/slot`): 20/20 OK.
- **Test groups.** CORE is the R3 suite with the matrix limited to `q1_`, plus `test_wild1_c_conversation_correction`, `test_wild1_c_h1_regression` and `test_r6_structured_response_contract`. ASYNC is CORE plus the live-async twins and `q4_`.
- **Controls** on the unmutated copy: **CORE 156 tests, 0 failing; ASYNC 228 tests, 0 failing.**
- **KILLED** means a test identity that fails in the mutant and passes in the control.
- **Candidate untouched.** Porcelain hash, manifest and `$E` final manifest are all unchanged after the campaign.

| Mutant (reviewer anchor) | Verdict | Killed by (one of) |
|---|---|---|
| R3-01 bounded read `get(timeout=0)` (never waits) | KILLED (3) | `test_wait_is_bounded_by_the_route_budget`, `test_qualifying_q1_after_3_5s` |
| R3-04 `_persist_pending_question` returns early | KILLED (10) | `test_commercial_path_question_is_retained_too`, … |
| R3-05 `_dispatch_pending_resolution` sees no rows | KILLED (10) | `test_resolved_question_never_repeats_what_was_already_answered`, … |
| R3-07 `is_greeting` always False | KILLED (2) | `test_bare_greeting_never_waits` |
| R3-08 bounded read waits again after a timeout | KILLED (2) | `test_quoted_q1_timeout`, `test_quoted_q4_timeout` |
| R3-11 `before_flush` listener never registered | KILLED (2) | both side-door tests |
| R3-12 legacy AI ACEPTADO hold keeps the proposal | KILLED (1) | `test_legacy_authority_off_holds_operator_perdido` |
| R3-13 UI provenance ignores `always` | KILLED (4) | `test_decline_then_operator_reconfirms_perdido_never_reactivates`, … |
| R3-16 API provenance call replaced by a no-op | KILLED (2) | `test_api_patch_reconfirmation_never_reactivates` |
| R3-18 R2 rule (newest DECLINED + motivo, defensive check removed) | KILLED (6) | `test_decline_clear_then_operator_perdido_same_motivo_never_reactivates`, … |
| **R3-19** website form appends "La revisión sale $TOTAL." before the CTA | **SURVIVED** | — |
| R3-23 owed-claim write outside a savepoint | KILLED (1) | `test_owed_claim_duplicate_rolls_back_savepoint_only` |
| **R3-23b** pending-question write outside a savepoint | **SURVIVED** | — |
| R3-24 `delivered()` always True | KILLED (1) | `test_blocked_outbound_never_answers_an_owed_claim` |
| **R3-25** per-segment check ignores the accumulated preamble | **SURVIVED** | — |
| MI-3b delivered-quote guard → `True` | KILLED (1) | `test_mi3b_no_decline_without_a_delivered_quote` |
| MI-4 `rows[0]` (oldest row) | KILLED (1) | `test_engine_redecline_after_reconsideration_is_reversible_again` |
| MI-4b legacy deterministic branch never holds | KILLED (1) | `test_legacy_authority_off_holds_operator_perdido` |
| MI-5b scrub says "confirmar el turno" | KILLED (1) | `test_mi5b_ai_confirmation_scrub_requests_never_confirms` |
| MI-7 `_FLOW_BODY_MAX = 1_000_000` | KILLED (1) | `test_mi7_flow_body_never_exceeds_meta_limit` |

**17/20 KILLED.** The supplied "61/61" holds only for the supplied anchors. Each survivor is a **test gap**; the production code at that point is correct, as the reviewer probes show:

- **R3-19.** `test_one_block_one_cta_no_total_called_revision` matches only `precio de la revisi[oó]n es`. "La revisión sale $1.384.567." passes it, and the validator does not refuse a total labelled as the revisión, because the amount equals `quote_total` (the same root as WC-06). So nothing guards against this wording coming back.
- **R3-23b.** No test forces a failure of the pending-question write.
- **R3-25.** The MI-7 test uses one oversize segment, so trimming several segments by their accumulated length is untested.

The R4 correction should add killers for all three. That is test-only and blocks nothing by itself.

---

## 7. Protected behaviour

| Item | Evidence | Status |
|---|---|---|
| Motorcycle/quad/UTV always go to human handoff, with no quote or scheduling | Focused: `test_r5_f1_motorcycle_handoff_production_path`, `test_m21_2_mh_motorcycle_handoff` pass. Reviewer PG probe: "moto Honda CB 500", "cuatriciclo", "UTV Can-Am Maverick", each with and without an earlier PENDING question → the existing motorcycle contact-data Flow (`flow_button_sent`, "Perfecto, para avanzar necesito que completes estos datos."). No quote, no booking token, no ack appended; stage QUALIFYING. No R3 hunk touches those branches. | unchanged |
| Model `gpt-4o-mini` | defaults identical to base; live has no `OPENAI_CHAT_MODEL` | unchanged |
| L-1 | `semantic_interpreter.py` is byte-identical to R2 (prompt `understand/1.22`), so the original audit's real-provider result stands | closed |
| Pricing rules and values | `pricing.py`, `pricing_base.csv`, `viaticos_zones.csv` byte-identical to base | unchanged |
| Booking | `BookingAuthority` and the Flow Data Exchange are untouched; the booking/Flow suites pass; the R3 hunks never call `_send_booking_flow`, `.issue(` or the authority | unchanged |
| No pre-persistence confirmation wording | `FlowIntroCopy` guards and `test_r5_f1_template_validation_contract` pass. The new `_PENDING_QUESTION_ACK` is registered FULL and contains no confirmation wording. | holds |
| Launch switches / outbound gate | no settings, gate or compose change; the eight switches are false live (§9) | untouched |
| Migration | none | none |
| Live write | none (§9) | none |

---

## 8. Remaining findings and classifications

| ID | Finding | Classification |
|---|---|---|
| **R3RV-01** | Supported questions outside MAY_ASK are silently lost on a late or unavailable reading. 8 of the 16 losses are an R3 regression for late readings (§2.4). | **Launch blocker** |
| **R3RV-02** | Fallback-Flow side doors overwrite an operator PERDIDO and bypass the lost-lead rule (§3.4). | **Launch blocker** (WC-02 not closed) |
| R3RV-03 | The turn-level semantic wait reaches 6.0 s (QUOTED) and 8–9 s (a pending re-read plus the current reading); "4.5 s, never twice" holds only per provider. | Mandatory correction before public enablement (fold into the R3RV-01 fix: one turn deadline) |
| R3RV-04 | `_disarm_perdido_guard` is not in a `finally`; an exception in post-processing leaves the listener on that request's Session. | Post-launch debt, bounded by the per-request Session. Recommended in the same correction (one `try/finally`). |
| R3RV-05 | `claim_coverage.delivered()` counts an outbound row stuck in `pending` (crash between the gate's insert and the Meta response) as delivered. | Post-launch debt |
| R3RV-06 | Evidence and test hygiene: the delta is 10 paths, not 9. The harness does not join pending-resolution threads. R3-20 (undeclared website quote) is killed only by an `inspect.getsource` test; the reviewer's runtime check (§4) shows the behaviour is right. | Post-launch debt (test quality) |
| WC-05 | The cross-turn ledger still reads the newest 20 messages. R3's pending path uses `_answered_after` (no horizon), but ordinary owed claims do not. | Accepted minimum-launch debt |
| WC-06 | The flattening is fixed and a corrupted block is never sent. `_rewrite_price` can still call a total "la revisión"; no live trigger was found. | Accepted minimum-launch debt |
| WC-08 | An operator-held PERDIDO that says "dale" receives the quote again with "Si te parece bien, podemos avanzar." (reproduced, §3.2 case 8). This invites an acceptance that will be held. | Mandatory correction before public enablement (owner L-8 copy) |
| WC-09 | "Me parece caro…" → "Genial! Te paso la cotización…" plus a CTA | Mandatory correction before public enablement (owner L-8 copy) |
| Ack during a prolonged outage | The ack repeats once per failing turn (4 failing turns → ack ×4), with no contradiction and no duplicate rows. The re-read concatenates every unresolved burst with no row cap. | Accepted minimum-launch debt (ops: alert on `PENDING_QUESTION` WARNINGs). Cap the re-read in the correction. |
| Generic closing followed by the ack | QUOTED/unavailable sends "Gracias por tu mensaje. Cualquier otra consulta, escribime por acá." and then "Recibí tu consulta y te la respondo en el próximo mensaje." "En el próximo mensaje" implies a proactive follow-up the system does not send: the answer arrives only after the customer writes again. | Mandatory correction before public enablement (truthfulness; owner L-8) |
| `_apply_lost_lead_rule` `TypeError` | Reachable only with acceptance authority off plus a concurrent operator commit between two reads (§3.5). | Post-launch debt (one-line fix recommended in the same correction) |
| Daemon-thread risk | One thread per turn that has pending rows. No DB access. Lifetime ≤ 30 s (the interpreter's HTTP timeout). No concurrency cap. If `Thread.start` fails, the interpretation runs inline, unbounded by the 4.5 s budget. | Accepted minimum-launch debt (prefer the bounded shadow worker in the correction) |
| Booking on a PERDIDO lead | A persisted Flow booking sets ACEPTADO through `BookingAuthority` whatever the operator flag. This predates R3 and is outside this review's scope. | Owner decision (not classified here) |

---

## 9. Live OFF proof (read-only)

Two reads, at 2026-11-17T13:24:27Z and 2026-11-17T14:03:07Z (identical values):
- Every SQL statement ran in `BEGIN TRANSACTION READ ONLY` (`transaction_read_only=on`), followed by `ROLLBACK`.
- The env read printed only the switch names and values listed below; no secrets and no PII were printed.

| Check | Value |
|---|---|
| Backend image | `sha256:4952d5d5d289348811027c3a22c440e436ff83d46eb3a0d1e84f76123ae56903`, tag `ridecheck-crm-backend:minlaunch-candidate-aa2c8db`, started 2026-11-16T17:03:57Z, `GIT_SHA=aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008` |
| Eight launch switches | `OUTBOUND`, `SMART_BOOKING_CE`, `OPERATOR_WHATSAPP_NOTIFY`, `BOOKING_FLOW_DETAILS_PREFILL`, `CONVERSATION_ENGINE_DIRECT_WEBHOOK`, `EVIDENCE_ADMISSION`, `OUTSTANDING_QUESTION`, `LOCALITY_RESOLUTION_SAFETY`: all `false` |
| Posture | `SHADOW_UNDERSTAND_ASYNC=true`, `SEMANTIC_SAME_TURN_TIMEOUT_SECONDS=6.0`, `RECONCILER_ACCEPTANCE_AUTHORITY_ENABLED=true` |
| Queues | pending outbound 0; sendable operator notices (`PENDING`/`SENDING`) 0; non-terminal slot-recovery jobs 0 |
| Database `crm_test` | alembic `20261104_vehicle_identity`; max message id 6288; max evidence id 36 (the same as the original audit); 0 `pending_question` rows |
| Booking 11 | Not touched. `revisions` id 11 md5 is `d69f5ee9aa1e4740d5d6627b31bc3ddc` at both reads (max/count 83/61). `thread_revisions` max/count is 10/1 at both reads; row md5 at the second read is `8a0a7e93d6adc160d659224a5ee5e862`. Max message and evidence ids are unchanged, so nothing was written. |
| WILD processes | none (the only "wild1-r3" match is this review's own tmux client) |
| Contact | no tester or customer was contacted; Meta was not called |

---

## 10. Exact next authorized step

A bounded correction milestone on this branch, **WILD1-C-R4**, limited to:

1. **R3RV-01.**
   - Every non-terminal turn whose burst may carry a supported claim, and whose reading timed out or failed, is retained as PENDING (ack, persisted, resolved on the next eligible turn), or is answered within the bound. Retention must not depend on the Layer D route classifier alone.
   - Restore the R2 behaviour for late readings, or give an equivalent guarantee.
   - Tests: the c1/c4/c6/c7 shapes, in QUALIFYING and QUOTED, under raise and under a 6–12 s reading. The tests must fail on the current candidate.
2. **R3RV-02.**
   - Every engine write path that can move `lead.flag` off PERDIDO obeys the lost-lead rule, including `_process_vehicle_fallback_response`, `_process_location_fallback_response` and every other `_process_flow_response` branch. Either arm the guard in `handle()` before dispatch, or guard each writer.
   - Tests: the four side doors of §3.4, on SQLite and PostgreSQL.
3. Fold in R3RV-03 (one per-turn semantic deadline) and R3RV-04 (`finally`).
4. Optionally, the one-line `TypeError` fix and a cap on the pending re-read.

The ack/closing copy, WC-08 and WC-09 need the owner's L-8 copy decision. Copy changes are not authorized by this audit.

After R4, a focused delta-only independent review of R4. Only that review can open the controlled commit and the consolidated image/kit update. **No commit, push, build, deployment, kit update, Meta change, tester contact or WILD is authorized by this audit.**

**VERDICT: CORRECTION REQUIRED**

STOP

