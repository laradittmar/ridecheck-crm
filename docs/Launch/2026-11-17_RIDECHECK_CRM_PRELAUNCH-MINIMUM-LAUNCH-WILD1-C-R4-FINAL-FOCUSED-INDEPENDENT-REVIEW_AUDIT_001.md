PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R4-FINAL-FOCUSED-INDEPENDENT-REVIEW

# WILD1-C R4 + R4-R1 + R4-R2: final combined focused independent review

- **Date.** 2026-11-17, 21:35Z–22:15Z.
- **Candidate.** Uncommitted tree on base `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008`.
  - Worktree: `/opt/ridecheck-crm-wild1-correction`.
  - Branch: `fix/prelaunch-wild1-conversation-correction`.
- **Evidence supplied.** `$E6=/opt/ridecheck-builds/crm-wild1-c-r4-r2-20261117T200926Z`. The earlier evidence sets are `$E3` (R3, `…crm-wild1-c-r3-20261116T231655Z`), `$E4` (R4, `…crm-wild1-c-r4-20261117T142430Z`) and `$E5` (R4-R1, `…crm-wild1-c-r4-r1-20261117T182550Z`).
- **Reviewer artefacts (durable).** `/opt/ridecheck-builds/crm-wild1-c-r4-final-review-20261117T2215Z/`. It holds the probes, mutants, runners, logs, junit and JSON results. Its file manifest `rev_manifest.sha256` has sha256 `85bc1ba0ad9a82d2bfc38faf0c417d04d42bf7093a54a5660f31d0876244be1e`.
- **Read in full.** The five documents the prompt names (hashes in §1).

**VERDICT: CORRECTION REQUIRED**

Every item the WILD1-C R4 series set out to close is **independently closed** on the final candidate, with no regression and no new defect introduced by the series:

- WC-01 / R3RV-01;
- WC-02 / R3RV-02;
- R3RV-03;
- R3RV-04;
- WC-03;
- WC-08 / WC-09;
- R1-D2.

The verdict is driven by **one newly discovered, pre-existing launch blocker**. The prompt's §B.5 requires me to confirm it as protected behaviour, and I cannot:

- **RV-F-01 (BLOCKER, pre-existing on base `aa2c8db`).** A motorcycle/quad/UTV enquiry on an ordinary lead receives the contact-data Flow. That path sets no `needs_human`.
  - If the customer then writes the zone and a price question instead of completing the form, the engine **auto-quotes the motorcycle**. The customer gets the "Genial! Te paso la cotización…" block with the MOTO tariff and "Si te parece bien, podemos avanzar.", and the stage moves to QUOTED.
  - A following "dale, ¿mañana puede ser?" moves the lead to ACEPTADO/SCHEDULING and **dispatches the Booking Flow with an ISSUED offer**.
  - Live has `WHATSAPP_FLOW_ID` set, so this is the live motorcycle path.
  - It is identical on base and candidate; the candidate only changes the quote rendering (§2.6).

This audit authorizes no commit, push, build, kit update, deployment, Meta change, tester contact or WILD.

---

## 1. A. Integrity and manifest

### 1.1 Candidate and evidence integrity

| Check | Result |
|---|---|
| HEAD / branch | `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008` / `fix/prelaunch-wild1-conversation-correction` |
| Index | empty (`git diff --cached` lists 0 paths), at start and at end |
| Porcelain | 64 entries: 47 `M` and 17 `??`. The 57 manifest paths plus the 7 WILD1-C docs. Byte-identical to `$E6/porcelain_end.txt` at the end of the review; porcelain sha256 (16) `cb2bcc960cf0ec38`. |
| Other writers | The only process with this worktree as its cwd is this session's own (tmux `ridecheck-wild1-r4-final-review`, `claude` pid 3678493, plus its shells). One idle tmux `bash` (pid 2821627, started 2026-11-16 19:19 ART, state `Ss+`, no children) also has this cwd. No file in the worktree changed after 18:35 ART, when this session started. |
| Final manifest | `sha256($E6/cand_manifest.sha256)` = **`1f71403028203ab2f89a0b894efd52aadad8ae1a3e1143c77c2f7422e2f665b3`** (exact match). `sha256sum -c` against the worktree passes, at start and at end. Recomputed inside `$E6/cand`, it is identical. |
| `$E6/cand` vs worktree | `diff -rq --exclude=.git --exclude=__pycache__` shows exactly one difference: the R4-R2 closeout, which lives in `$E6/` beside `cand` and is byte-identical there. `cand` is a full-repository copy. |
| Earlier candidates | `$E3/cand` passes `sha256sum -c` against the R3-reviewed manifest `b5ace40f…` (rc 0). `$E4/cand` passes against `2ba471ea…` (rc 0), and `$E5/cand` against `d9423a96…` (rc 0). |
| `__pycache__` in the worktree | There are 3 directories (`backend/app`, `backend/app/services`, `tests`). The newest file is 17:29 ART, before this session; they come from the implementer. They are gitignored (`.gitignore:2`) and are not in the manifest. **Consolidation must build from the manifest, not from a directory copy.** |
| Writes by this review | This audit file only. Every test, probe and mutant ran with a `:ro` mount of `$E6/cand` (or a throwaway copy), with `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`. |

**Closeout hashes (sha256):**

| Document | sha256 |
|---|---|
| Original WILD1-C audit | `e98a0eadf77b0f69c3456b94b6860bcac8c9112670ce34067192cc2ef2fa0fbc` |
| R3 review | `3af608c1635ac3e6b938e175262293bea667d34c523953c136a1f60041df5607` |
| R4 closeout | `08e996ff734300c922c3aaa7bbeeb73d6002e8095cad8e9b7568f3c7a77330fe` |
| R4-R1 closeout | `8ef323ea2a3c692c9487b816899d4fa1298a34c9da96377f55b7f8ed6583992d` |
| R4-R2 closeout | `f6498c6b55bff00a34f5b08af29296a4d957410185d465caf3a4fca6af4abcf9` (identical to the `$E6/` copy) |
| R2 closeout | `6c0352b78ed1113a60107289919f9c3d2a8e136f68366bca72d1f29d35c5b922` |
| R3 closeout | `f27affd84fd79fa63c019e4080086661963e059070382dc44c671f2cf7293e06` |

Each matches every value the later documents quote.

### 1.2 Independently derived deltas (`diff -rq` over `backend/` and `tests/`; the rest of the tree is identical apart from `docs/Launch`)

| Delta | Paths | Content |
|---|---|---|
| R4 vs reviewed R3 (`$E3/cand` → `$E4/cand`) | **9** | Production: `conversation_engine.py`, `claim_coverage.py`, `semantic_turn_evidence.py`. Tests: `_wild1c_harness.py`, `test_l4_7c_3b…`, `test_l4_7w5_g3_5e…`, `test_l4_7w5_g3_6_bounded_correction.py`, `test_r5_f1_template_validation_contract.py`, plus the new `test_wild1_c_r4_retention_and_perdido_guard.py`. |
| R4-R1 vs R4 (`$E4` → `$E5`) | **3** | `conversation_engine.py`, `test_r5_f1_template_validation_contract.py`, new `test_wild1_c_r4_r1_perdido_consistency.py` |
| R4-R2 vs R4-R1 (`$E5` → `$E6`) | **5** | `booking_authority.py` (+8/−1). Tests: `test_sb1_booking_representation.py`, `test_l4_7w5_g3_7f_owner_resolution.py`, `test_l4_7w5_g3_6_proposition_migration.py`, new `test_wild1_c_r4_r2_booking_projection.py`. |

Each delta equals its closeout's manifest table. The `semantic_turn_evidence.py` blob is `28968584adc4375e…` (base `50118bf6…`). That matches the re-recorded SB1 pin.

### 1.3 Final production manifest against base (`git diff --stat aa2c8db -- backend` ∪ untracked)

| Path | Δ vs base | sha256 (16) |
|---|---|---|
| `backend/app/services/conversation_engine.py` | +1979 lines (net of 176 deletions across backend) | `685c2bfa8579b123` |
| `backend/app/services/booking_authority.py` | +8/−1 | `f1580e80c9473a44` |
| `backend/app/services/response_validator.py` | 70 changed | (R2, unchanged since) |
| `backend/app/services/semantic_interpreter.py` | 20 changed | identical to R2 and R3 (`cmp`) |
| `backend/app/services/response_composition.py` | 12 changed | R2 |
| `backend/app/services/semantic_turn_evidence.py` | +7 | `3eebf0c95e2d9636` |
| `backend/app/api/leads.py` | +13 | `1e53ef544f6e84b3` |
| `backend/app/ui/kanban_actions.py` | +34 | R3 |
| `backend/app/ui/kanban.py` | +8 | R2 |
| `backend/app/ui/kanban_view.py` | 10 changed | R2 |
| `backend/app/schemas/turn_evidence.py` | +2 | `4f0b4ca0fc087b8c` |
| `backend/app/services/claim_coverage.py` | new, 314 lines | `90fc0ae55292dc1f` |
| `backend/app/services/quote_decision_history.py` | new, 135 lines | R3 |

The full per-path hashes are in `$E6/cand_manifest.sha256`. There are 57 paths: 13 production and 44 test.

### 1.4 Protected scope

`git diff --stat aa2c8db` is **empty** for all of the following:

- `backend/migrations`, `alembic.ini`, `Dockerfile`, `requirements.txt`;
- `models.py`, `settings.py`, `routes/`, `repositories/`, `main.py`, `db.py`, `auth.py`, `templates/`, `static/`;
- `pricing.py`, `outbound_safety_gate.py`, `outbound_guard.py`, `outbound_path_registry.py`;
- `offer_lifecycle.py`, `flow_contract.py`, `booking_flow_service.py`, `booking_ce_adapter.py`, `booking_acknowledgement.py`, `booking_slot_recovery.py`, `quote_followup.py`, `buscando_followup.py`;
- `meta/`, `backend/app/data/`, `Pricing - tabla de viaticos/`, `client_authoritative_data/`, `N8N workflows/`, `scripts/`;
- `docker-compose.yml`, `docker-compose.beta.yml`.

The outcomes:

- **No migration.** PostgreSQL head stays `20261104_vehicle_identity`.
- **No pricing rule or data change.**
- **No Meta change.**
- **No outbound-gate or deployment change.**
- **No OpenAI model or prompt change after R2.** `semantic_interpreter.py` is byte-identical to `$E3/r2`. The default is `gpt-4o-mini` (`semantic_interpreter.py:737`), and live has no `OPENAI_CHAT_MODEL`.

**Secret/PII scan** over the 57 paths:

- I searched for Meta/OpenAI/GitHub token shapes, the server IP, private keys and literal `SMTP_PASSWORD`/`WHATSAPP_TOKEN` values: none found.
- The tester-number fragment `8330` appears in each file exactly as often as on base.
- Every manifest path is a `.py` file: no cache, database, log or generated artefact.

---

## 2. B. Route-independent question retention — **PASS on B.1–B.4; FAIL on B.5 (RV-F-01, pre-existing)**

### 2.1 Implementation (read, not inferred from tests)

- **No route or phrase gate decides retention.**
  - `_note_semantic_unavailable` (`conversation_engine.py:18099`) no longer consults `QuestionPotential.MAY_ASK`.
  - Its only gates are: no reason recorded yet, a reading was dispatched, `_turn_continues`, and the burst is not `GREETING_ONLY`. `GREETING_ONLY` is the single anchored greeting entry that Layer D already uses.
  - Nothing reads Layer D's route classifier or a new Spanish phrase list.
- **The state is typed.** `_turn_semantic_state` (`:18130`) yields one of the `claim_coverage.SemanticTurnState` values: `NOT_DISPATCHED`, `GREETING_ONLY`, `TERMINAL_OR_PRE_GATED`, `SEMANTICALLY_RESOLVED` or `SEMANTICALLY_UNRESOLVED_NONTERMINAL`. It is evaluated at every send boundary (plan, Booking Flow preamble, clarification Flow bodies via `_with_pending_ack`). A turn-boundary safety net, `_finalize_question_retention` (`:18459`), runs inside `handle()`'s `try`.
- **The PENDING row is written before composition.** It goes in a savepoint (`_persist_pending_question`, `:18381`). The ack is sent only when `_turn_pending_persisted`.

### 2.2 Reproduced matrix (reviewer probe `p_ret.py`, real `/api/conversation/handle` route, live async posture `SHADOW_UNDERSTAND_ASYNC=true`, 6.0 s)

Setup:

- My own interpreter seam (`Probe`, not the candidate's `SlowFirst`).
- 7 inputs × {QUALIFYING, QUOTED} × {immediate, 3.5 s, late, raise} = **56 cases**.
- Late readings were spread over **6.5 / 7.5 / 8.5 / 9.5 / 10.5 / 11.5 / 11.9 s** (one value per input).
- Each case ran 3 turns. T2 was "Perfecto, gracias." and T3 "Dale, buenísimo." These are neutral and carry no claim, so any answer on T2 can only come from the retained burst.
- Between turns the shadow worker was drained and every `pending-question-resolution` thread joined.

| Stage | Mode | T1 (s) | Outcome on T1 | T2 (s) | Outcome after T3 |
|---|---|---|---|---|---|
| QUALIFYING | immediate | 0.09–0.36 | answered, no ack, no row | 0.09–0.23 | no repeat, no ack |
| QUALIFYING | 3.5 s | 3.53–3.59 | answered, no ack, no row | 0.09–0.17 | no repeat, no ack |
| QUALIFYING | late 6.5–11.9 s | **4.53–4.57** | ack, 1 PENDING (`SEMANTIC_TIMEOUT`), not answered | 0.09–0.19 | answered on T2; rows `[PENDING, RESOLVED]`; no ack on T2/T3 |
| QUALIFYING | raise | 0.08–0.21 | ack, 1 PENDING (`SEMANTIC_UNAVAILABLE`) | 0.09–0.16 | same as late |
| QUOTED | immediate | 0.07–0.18 | answered | 0.08–0.13 | no repeat |
| QUOTED | 3.5 s | 3.54–3.59 | answered | 0.08–0.13 | no repeat |
| QUOTED | late 6.5–11.9 s | **4.53–4.59** | ack, 1 PENDING | 0.09–0.13 | answered on T2; `[PENDING, RESOLVED]` |
| QUOTED | raise | 0.07–0.15 | ack, 1 PENDING | 0.09–0.27 | answered on T2 |

The inputs were c1–c7:

- "¿Cuándo me mandan el informe?"
- "¿Y el informe cuándo llega?"
- "Hola, tengo un auto para revisar. ¿Qué me entregan después?"
- "necesito revisar una camioneta que le revisan"
- "Tengo un Corolla 2018 en Palermo, ¿qué revisan?"
- "Quiero revisar un auto antes de comprarlo, ¿qué incluye?"
- "¿Qué miran en la revisión?"

**Result: 56/56 correct, 0 silent losses.** In every case:

- the canonical answer appears exactly once in the thread's outbound;
- the ack appears 0 times (immediate/3.5 s) or exactly once (late/raise);
- the PENDING `source_message_ids` are a subset of the thread's inbound WAMIDs;
- there are no duplicate PENDING / RESOLVED / `required_claim` rows (by key and by `claim_id`);
- the generic closing is never beside the ack.

Provider calls per burst: exactly 1 (immediate/3.5 s), or 1 + the one designed re-read (late/raise). There were no duplicate requests.

**No supported claim** ("Bueno, lo voy a pensar con calma", provider failing), in both stages: T1 gets the ack and a PENDING row. T2 gives `[PENDING, RESOLVED]`, 0 owed rows, and no scope, report or payment answer. It resolves with no irrelevant FAQ.

### 2.3 Terminal / pre-gated turns (an earlier burst is PENDING; the provider is still down on the terminal turn)

| Case (both stages) | Action | Ack on terminal turn | Earlier row | Other state |
|---|---|---|---|---|
| "tengo una moto Honda CB 500, ¿qué revisan?" / cuatriciclo / UTV | `flow_button_sent` (contact-data Flow) | no | stays PENDING | no quote, MOTO candidate, 0 offers |
| "¿me pueden llamar por teléfono?" | phone handoff, `needs_human=True` | no | stays PENDING | — |
| "necesito el formulario 12 para el auto" | F12 boundary text | no | stays PENDING | — |
| `needs_human` already set | `skipped_human` | no | stays PENDING | — |
| operator-held PERDIDO + "¿qué revisan exactamente?" | exact hold copy | no | stays PENDING | PERDIDO/PRECIO unchanged |

Every terminal turn took ≤ 0.05 s. None received or consumed an acknowledgement.

### 2.4 B.5 Motorcycle/quad/UTV — **FAIL (RV-F-01, pre-existing)**

**What holds.**

- On any PERDIDO lead, a motorcycle/quad/UTV by text or by vehicle-fallback form gets the held plain-text handoff (§4.4).
- On an ordinary lead, the first turn gets the existing contact-data Flow, with no quote and no booking offer.

**What does not hold** (reviewer probes `p_moto.py`, `p_moto2.py`; identical on **base `aa2c8db`** (git archive) and on the candidate, sync and live async):

| T1 | T2 | Result on T2 | T3 "dale, ¿mañana puede ser?" |
|---|---|---|---|
| "tengo una moto Honda CB 500" → contact-data Flow, `needs_human=False` | "está en Palermo, ¿cuánto sale?" | **auto-quote**: "Genial! Te paso la cotización para la revisión del Honda CB en Palermo:\n\nRevisión: $120.000\nViáticos: $0\nTotal: $120.000\n\nSi te parece bien, podemos avanzar." (base: "…es de $120.000. Si te parece bien, podemos avanzar."), flag PRESUPUESTO_ENVIADO, stage QUOTED | **ACEPTADO / SCHEDULING; Booking Flow dispatched; offer ISSUED** ("Para mañana miércoles 18/11 tengo 5 horarios disponibles…") |
| "es un cuatriciclo Yamaha" | same T2 | same auto-quote ($120.000) | — |
| "UTV Polaris RZR" | same T2 | same auto-quote | — |
| any of the three | "¿cuánto sale?" (no zone) | generic handoff, `needs_human=True` (safe) | — |
| any of the three | "en Palermo" | generic qualification question (no quote) | — |

The amount is the real `MOTO,120000` row of `pricing_base.csv`, so no money is invented. But the owner's rule is that a motorcycle/quad/UTV always goes to a person and is never auto-quoted or auto-scheduled, and that rule is broken. Live has `WHATSAPP_FLOW_ID` set (16 characters; value not printed), so the contact-Flow path, and with it this defect, is the live path.

Why it happens:

- The contact-Flow branch of `_motorcycle_human_handoff` deliberately defers `needs_human` until the form is submitted.
- Layer A (`_is_motorcycle_enquiry(current_turn_text)`) reads only the current turn.
- A later text turn therefore reaches the ordinary quote path with the `mentioned` MOTO candidate as its focus.

The defect is **not introduced by WILD1-C**: no candidate hunk touches the moto routing, and the base behaviour is the same. But it was not found by any earlier review, because every motorcycle test and probe was single-turn.

---

## 3. C. One semantic deadline — **PASS**

**Implementation.**

- `_start_semantic_deadline()` is called once, just before the current reading and the pending re-read are dispatched (`:9360`, concurrently). The deadline is `monotonic + min(4.5, SEMANTIC_SAME_TURN_TIMEOUT_SECONDS)`.
- `_await_reading()` (`:11681`) is the only bounded wait primitive. It reads a finished reading at once, returns None for an undispatched reading, and otherwise waits the remainder.
- Every consumer goes through it: `_semantic_turn_evidence` (admission, scheduling, the QUOTED decline probe, locality), `_bounded_question_reading`, `_pending_resolution` and `_turn_semantic_state`.
- The two other `.get()` calls are safe. The worker's own `_work` runs off the request thread, and the coverage read at `:18040` uses `get(timeout=0)` only when `ready`.

**Measured (`p_ret.py deadline`, real route, live posture):**

| Case | Turn (s) | Notes |
|---|---|---|
| QUALIFYING, current reading 10 s | **4.61** | 1 provider call; ack + PENDING |
| QUOTED, current reading 10 s | **4.55** | 1 call |
| QUALIFYING, slow pending re-read (10 s) + slow current reading (10 s) | **4.54** | concurrent; both rows stay PENDING (no 4.5 + 4.5) |
| QUOTED, same | **4.54** | |
| provider exception (both stages) | 0.10–0.12 | ack + PENDING |
| greeting only ("hola", "Buenas tardes!", "hola, buenas tardes") with a 3 s reading | 0.09–0.14 | no ack, 0 rows |
| `Thread.start()` fails for the re-read (re-read scripted at 20 s) | **0.11** | **0 re-read calls** (never inline); row stays PENDING; next turn resolves it (`[PENDING, RESOLVED]`, report answered) |
| shadow worker declines the current reading (backlog; reading scripted at 20 s) | **0.10** | **0 calls** (never inline); ack + PENDING (R4-D6) |

R3 measured the same cases at 6.06–6.10 s (QUOTED) and 8.04/9.05 s (sequential re-read). Neither shape recurs.

**Batching** (`p_batch.py`, `p_ret.py deadline`):

- Seven short failing bursts, then recovery: the re-reads during the outage carried 1, 2, 3, 3, 3, 3 rows; the recovery re-reads carried **3, 3, 1**.
- The recovery acks were `[yes, yes, no, no]`: an ack only while rows remained.
- Final state: 7 PENDING, 7 RESOLVED, 0 unresolved, none deleted.
- Three bursts of 2,663 characters each: every re-read carried **1 row** (two would exceed 4,000), and all 3 resolved.

**Threads and sessions.**

- The re-read closure (`:18225`) captures only lists of `str`, an `int` thread id, a string burst id, and the `TurnContext` returned by `_build_shadow_context`. That context is plain strings and tuples, built on the request thread.
- `semantic_interpreter.py` and `semantic_turn_evidence.py` import no SQLAlchemy, Session or DB module.
- There were 0 `pending-question-resolution` threads alive at the end of every probe process.

---

## 4. D. Operator PERDIDO and engine decline — **PASS**

### 4.1 Independent writer inventory

I ran an AST walk of `conversation_engine.py` (attribute targets, `setattr`, keyword arguments) and found 23 engine writes to `lead.flag` / `lead.motivo_perdida`:

- `_converged_flow_book`'s `LeadUpdate(flag="ACEPTADO")`, delegated to BookingAuthority;
- `_process_vehicle_fallback_response` (3);
- `_process_location_fallback_response` (2);
- `_process_text` (8);
- `_apply_lost_lead_rule` (2);
- `_handle_quote_decline` (2);
- `_handle_quoted_acceptance`;
- `_handle_website_form`;
- `_handle_scheduling_escalation`;
- `_execute_cycle_reset` (2).

This matches closeout §6.1 (lines shifted by R4-R1). There is no raw `UPDATE leads` that touches flag or motivo; the only raw lead `UPDATE` sets `telefono`.

Outside the engine, the writers are:

- `kanban_actions` `ui_perdido`, `ui_lead_flag_set`, `ui_lead_flag_clear` and `ui_move*` (motivo);
- `api/leads.update_lead`;
- `api/leads.create_lead` (initial flag);
- `booking_flow_service`'s DX `LeadUpdate`;
- `BookingAuthority._apply_canonical_state`.

`quote_followup.py:60` and `buscando_followup.py:62` are SQL `WHERE` reads, not writes.

### 4.2 Operator PERDIDO through real routes (`p_perdido.py`; operator writes via the real `ui_perdido` and, for the price case, the real `PATCH /leads/{id}` function)

The matrix was 7 text entries × {QUALIFYING, QUOTED} × {authority on, legacy} × {with, without an earlier PENDING row} = **56**. The text entries were:

- vehicle + zone;
- vehicle only;
- zone only;
- a price request;
- "dale, avancemos";
- "¿el jueves a las 10 puede ser?";
- "¿qué revisan?".

To these I added 4 Flow-response entries × 2 stages = **8**: solicited vehicle fallback, solicited location fallback, unsolicited vehicle, unsolicited location.

Every case was required to show all of the following:

- `sent == [exact hold copy]`;
- flag, motivo and stage unchanged;
- no "Total:", "$" or CTA;
- 0 Flows, no token, 0 offers, 0 bookings, 0 revisions;
- no RECONSIDERED row;
- no acknowledgement;
- notices unchanged.

**Result: 64/64 on SQLite, 64/64 on PostgreSQL.**

### 4.3 Engine-written decline (a real "no gracias, es muy caro…" REJECT/PRICE turn first)

| Entry | Result |
|---|---|
| vehicle + zone / vehicle / zone / price / "perdón, en realidad es un Toyota Corolla 2018" | PERDIDO/PRECIO kept, `[DECLINED]` only; the quote is restated with its CTA (R1-D1, owner-accepted) |
| vehicle fallback / location fallback | `sent == ["Recibí los datos. Si querés retomar la revisión, decime por acá y continuamos."]`, no "manualmente", PERDIDO/PRECIO, `[DECLINED]` |
| then "dale, avancemos" | ACEPTADO/None, SCHEDULING, `[DECLINED, RECONSIDERED]` |
| explicit ACCEPT | ACEPTADO/None/SCHEDULING, `[DECLINED, RECONSIDERED]` |
| day request (with or without ACCEPT) | ACEPTADO/None/SCHEDULING, `[DECLINED, RECONSIDERED]`; proceeds to the normal availability answer |

Identical on PostgreSQL.

### 4.4 Motorcycle/quad/UTV discovered while PERDIDO

The cases were: operator PERDIDO and engine decline × {text moto, text cuatriciclo, text UTV, vehicle-fallback form `tipo_vehiculo=MOTO`}, 8 cases. Each one gave:

- the approved plain-text handoff "Las motos las revisamos de forma manual…";
- `needs_human=True`, `necesita_humano=True`, and 1 MOTO candidate;
- flag and motivo unchanged;
- 0 Flows, no token, no quote, no ack.

The same 8/8 held on PostgreSQL.

### 4.5 Kill switch

All 64 operator-PERDIDO cases were repeated with `OUTBOUND_ENABLED=false`. Every one ended `blocked_dispatch` and satisfied the same database checks: flag, motivo and stage unchanged, no token, no offer, no booking, no RECONSIDERED. **64/64.**

---

## 5. E. PERDIDO guard lifecycle — **PASS** (one LOW test-pin gap, RV-F-02)

| Requirement | Finding (code) |
|---|---|
| Armed before every relevant dispatch | `_handle` arms the guard (`:4002–4003`) after the dedup, no-lead, cycle-reset and `needs_human` exits, and before routing by message type. That covers `_process_text` and every `_process_flow_response` handler. |
| The one exclusion | `_is_booking_shaped_submission`, as documented. Its writer is BookingAuthority, on its own connection. |
| True `finally` | `handle()` wraps everything after the per-turn reset in `try: … finally: self._disarm_perdido_guard()` (`:3722–3828`). That covers `_revoke_undelivered_slot_claim`, `_finalize_question_retention`, `_turn_plan` and `_write_hybrid_trace`. The per-turn reset also disarms (`:3714`). |
| No listener leaks to another Session, lead or request | The listener is attached to `self.db` only. `_guard` returns unless `inspect(lead).session is sess` and the captured lead's own `flag`/`motivo_perdida` history has changes. Mutant J09 is killed by three lifecycle tests, including "another session or lead". |
| No recursive flush | The only DB access is a Core `select` on `sess.connection()`, with no autoflush. The restore uses `set_committed_value`. |
| Current DB state, not a stale turn-start pair | The guard re-reads `(flag, motivo)` on the session's connection and restores with `set_committed_value`, so **no UPDATE is emitted**. A concurrent operator commit therefore always survives; `test_guard_never_undoes_a_concurrent_operator_commit` passes on SQLite and PostgreSQL. |

**RV-F-02 (LOW, test-pin and closeout precision).** My mutant J10 makes the guard restore the pair captured at arm time. It keeps `set_committed_value`, and it **survives** every test. The candidate's R4-14 changed two things at once (the captured pair *and* a plain assignment, which emits an UPDATE). So the R4-14 kill demonstrates the no-UPDATE restore, not the DB-current read.

J10's only effects are in memory:

- the stale in-memory value after the restore;
- blocking an engine write after a concurrent operator reopen, which the original lets through.

The closeout's statement that the DB-current read is what protects a concurrent operator commit is imprecise: `set_committed_value` is. This is not a safety defect. It is a pin worth adding.

---

## 6. F. Booking exception and projection — **PASS**

**Transaction order (read directly, `booking_authority.py`).** The steps of `execute()`, in one transaction, are:

1. K1/K2 locks;
2. admission/claim;
3. replay check;
4. calendar/overlap/availability;
5. `_write` (`:1462`);
6. `_apply_attachments` (`:1467`, ThreadRevision);
7. `_apply_canonical_state` (`:1472`). It takes K8 on the lead (`FOR NO KEY UPDATE` on PostgreSQL), applies `estado/flag/necesita_humano`, then `motivo_perdida = None` when `u.flag == "ACEPTADO"` (`:1893–1898`), then the thread and offer projection;
8. `flush`;
9. `_record` (ledger);
10. `_verify_before_commit`;
11. `_verify_offer_projection`;
12. `commit`.

Every `except` branch calls `_safe_rollback()`. `_REQUIRED_COLUMNS_LISTED["leads"]` includes `motivo_perdida` (`:500–501`), and `("leads","motivo_perdida") in _required_columns()` is True.

**A reviewer call-order spy wrapped `_write`, `_apply_attachments`, `_apply_canonical_state`, `_record` and `_verify_offer_projection`.** In every success case it showed:

- motivo `PRECIO`/`OTRO` through `_write` and `_apply_attachments`;
- `None` only after `_apply_canonical_state`;
- `None` at `_record`.

**Successes (`p_book.py`, CE Flow-response route and real Data Exchange `handle_confirm_booking`):**

| Scenario | Result (SQLite and PostgreSQL) |
|---|---|
| operator PERDIDO/PRECIO + valid booking (CE) | `booking_created`; ACEPTADO/None; 1 booked, 1 ledger, offer ACCEPTED; 1 receipt, and at the moment it was transported a separate session already saw the committed state |
| operator PERDIDO/OTRO + valid booking | same |
| engine-declined PERDIDO/PRECIO + valid booking | same; decision history unchanged (`[DECLINED/ENGINE]`) |
| normal booking | same |
| DX confirm on operator PERDIDO/PRECIO | SUCCESS screen; ACEPTADO/None; 1 booked, 1 ledger, ACCEPTED |
| CE vs DX projection | identical `(flag, motivo, estado, necesita_humano)` = `(ACEPTADO, None, COORDINAR_DISPONIBILIDAD, True)` |
| duplicate delivery / same payload new id / different payload / DX after CE | `skipped_dedup` / `booking_replayed` / `booking_replayed` / SUCCESS replay; sends `(1, 0, 0, 0)`; state byte-identical to after the first booking; exactly 1 receipt row in the outbound ledger |

**Failure paths (operator PERDIDO, SQLite and PostgreSQL):**

| Case | flag/motivo after | booked | ledger |
|---|---|---|---|
| invalid token | PERDIDO/PRECIO | 0 | 0 |
| expired offer | PERDIDO/OTRO | 0 | 0 |
| withdrawn offer | PERDIDO/PRECIO | 0 | 0 |
| occupied slot (CE) | PERDIDO/PRECIO | 0 | 1 `REQUEST_MANUAL_SCHEDULING` (see below) |
| occupied slot (DX) | PERDIDO/OTRO | 0 | 0 |
| `_record` raises (ledger) | PERDIDO/PRECIO | 0 | 0 |
| `_verify_offer_projection` raises (final verification) | PERDIDO/OTRO | 0 | 0 |
| `_apply_canonical_state` **applies the clear, then raises** (CE and DX) | PERDIDO/PRECIO | 0 | 0 |
| `_verify_before_commit` → `_InvariantBreach` (DX) | PERDIDO/OTRO | 0 | 0 |

- **Atomicity.** The clear rolls back atomically. The "applied, then raise" case proves it directly: the motivo had already been set to `None` in-session and is restored by the rollback.
- **The faults are really reached.** Every injected fault fired: `reached=1` on the shared non-booking slot the R4-R2 tests use and on a free slot (`p_book2.py`). The R4-R2 failure tests are therefore not vacuous.
- **Occupied-slot residue on CE.** The CE refusal on an occupied slot writes the existing truthful handoff:
  - a non-occupying `REQUEST_MANUAL_SCHEDULING` ledger row;
  - a `REPROGRAMAR` Revision with no professional;
  - a `provisional` ThreadRevision.

  It is not a booking, and the PERDIDO pair is preserved. It is pre-existing behaviour (BookingAuthority's only R4-R2 change is the motivo clear).

---

## 7. G. Price, copy and Flow authority — **PASS** (G.5 unchanged; see RV-F-01)

1. **Website form** (`p_copy.py`). Jeep/Berazategui and Corolla/Palermo, sync and live async, with hostile form totals `$999.999` and `$1`. Each render had:
   - exactly 1 block and 1 "Revisión: $" line;
   - exactly 1 "?" (one CTA);
   - no "podemos avanzar";
   - no `$` amount outside the block;
   - no "revisión sale/es/cuesta…" or "precio de la revisión";
   - the form total never echoed (the one "$1" hit is a substring of "$140.000");
   - flag PRESUPUESTO_ENVIADO.

   **The declared PRICE claim is validated.** With the handler's declared split corrupted to a reconciling but wrong (160.000, 80.000, 240.000), the engine logged `PRICE_BLOCK_CORRUPTED_BY_VALIDATION — safe line sent, breakdown withheld`.

   The text validator also flags a wrong split (`allowed=False`), but its rewrite gives "Revisión: $240.000 Viáticos: $240.000 Total: $240.000". The engine's corruption guard withholds that text. This is the accepted WC-06, with no live trigger.
2. **Price concern.** I tested four HESITATE phrasings, sync and with a 3.5 s async reading:
   - "Me parece caro, ¿cuánto hay de viático?";
   - "uff, me parece carísimo eh";
   - "Me parece caro. ¿Y qué revisan?";
   - "es mucha plata para mí, no sé".

   Each got "Entiendo. Te detallo cómo se compone la cotización:" plus exactly 1 canonical block, with no "Genial", no "podemos avanzar" and no "¿Qué día". The scope answer was added where asked. The lead stayed PRESUPUESTO_ENVIADO, motivo None, with no decision row (no false PERDIDO). An explicit REJECT still declines (PERDIDO/PRECIO, `[DECLINED/ENGINE]`).
3. **Pre-persistence Flow copy.** Three QUOTED acceptances dispatched the Booking Flow ("Para mañana miércoles 18/11 tengo 5 horarios disponibles. Elegí el horario que te sirva y completá tus datos para solicitar el turno."; one 606-character body with the scope preamble). The scan for `confirm|reservad|agendad|quedó listo|tu turno está` found 0 matches in every outbound.
4. **Booking still requires BookingAuthority and a persisted Flow completion.** After the Flow dispatch the offer is ISSUED, the token is set, and there are 0 bookings and 0 revisions. Bookings exist only after the Flow completion through BookingAuthority (§6).
5. **Motorcycle handoff authority.** It is unchanged against base: no hunk touches the routing (§2.4). The unchanged authority is itself defective (RV-F-01).

---

## 8. H. Test-pin review — **PASS**

| Pin (R4-R2) | Verdict |
|---|---|
| `test_sb1_booking_representation::test_sb1_20` | Legitimate. The blob is re-recorded `50118bf6…` → `28968584…`, and `git hash-object` of the worktree file confirms `28968584adc4375e65e8538c83bbaedfba8577c9`. The only content change is the read-only `dispatched` property (§1.2 diff, 7 added lines). The previous blob is recorded in the comment, following the file's established convention. |
| `test_l4_7w5_g3_7f_owner_resolution::test_or_43` | Legitimate. The count goes 5 → 6. The sixth caller, `_hold_perdido_fallback`, is gated on `tipo == "MOTO" or _is_motorcycle_enquiry(...)`. The test's per-site gating check still runs on all 6 sites and passes, so the safety property (only proven moto callers) is intact. |
| `test_l4_7w5_g3_6_proposition_migration::test_g36_48` | Legitimate, and stronger. It expects 2 → 3 items with the ack, and adds `assertIn(_PENDING_QUESTION_ACK, t.sent[0])` and `len(t.sent) == 1`. `evidence=None` is a failed reading, so retention is the intended R3RV-01 behaviour. The hours answer is still asserted. |

The earlier R4 changes:

- `test_l4_7c_3b…::test_cut_06`: the source pin now requires the legacy branch to honour the rule's verdict. That is stronger.
- `bc36_34` / `g35e_24`: the same reasoning as `g36_48`, still in one message.
- `test_r5_f1_template_validation_contract`: it only adds FULL inventory entries, for `_PRICE_CONCERN_LEAD`, `_PERDIDO_HOLD_REPLY`, `_GENERIC_NO_QUESTION_TEXT` and (R4-R1) `_PERDIDO_FALLBACK_DATA_REPLY`.
- No assertion against false confirmation, a duplicate Flow, premature booking or the handoff was removed.

**RV-F-03 (LOW, owner copy).** These pins also show that a burst whose question is answered by a deterministic floor (business hours), but whose reading failed, gets the answer *and* "Recibí tu consulta, pero no pude procesarla en este momento. Si me escribís nuevamente, la retomo y te respondo." in the same message. It is truthful about the reading, but it reads as contradictory. It belongs to the R4-D4 class and goes to owner L-8 review.

---

## 9. I. Focused tests

### 9.1 SQLite (runner `ridecheck-crm-test:l4.7b2`, `--network none`, read-only mount, TZ America/Argentina/Buenos_Aires)

The set is 36 files (`focused.txt`):

| Group | Files |
|---|---|
| WILD1-C sync, H1, live-async | 3 |
| R3, R4, R4-R1, R4-R2 | 4 |
| Booking authority / DX / idempotency / rollback | SB1, SB2A, SB2A-R3, convergence P3 cutover, P3-R1 ack, L4.7W4-F1 Data Exchange, M21.3-C-D, WILD1 booking-flow gate, minimum-launch ×3 |
| Motorcycle | R5-F1 production path, M21.2-MH, M21.2-MF |
| Price / website | R6 structured contract, L4.7D validator, M20.6B1 |
| Flow and copy authority | R5-F1 template contract, R6-C4, R6-C4A, R6-C4B |
| Kill switch | M20.2, M19-F2.2, M20.4.3 |
| Pins | G3-7F owner resolution, G3-6 proposition migration, L4.7C.3B, G3-6 bounded correction, G3-5E |

| Tree | Cases | Passed | Failing |
|---|---|---|---|
| base `aa2c8db` (git archive; the files present) | 990 | 989 | 1 |
| final candidate | **1,320** | **1,319** | **1** |

Comparison (the `$E6/cmp.py` comparator):

- **NEW 0, GONE 0, COMMON 1, COMMON_changed 0.**
- 330 cases added, 0 removed.

The common failure is `test_l4_7w5_g3_7f_owner_resolution::J_AccountingAndArchitecture::test_or_73_no_booking_revision_is_modified`, subtest `fn='_withdraw_dependent_proposals'`. It is pre-existing on base with the same message.

### 9.2 PostgreSQL 16 (reviewer `pg.sh`)

Setup:

- disposable `fr4rev-pg` on tmpfs, on an `--internal` network;
- two databases, `tests` and `probes`;
- schema = pre-schema record + `alembic stamp 20261006_clarification_attempts` + `upgrade head` → `20261104_vehicle_identity` on both.

| Run | Result |
|---|---|
| R4 + R4-R1 + R4-R2 + R3 suites | **203 passed, 0 failed** (73 subtests) |
| `p_book.py` (booking projection, rollback, duplicate booking, CE ≡ DX) | identical to SQLite (§6) |
| `p_perdido.py` (operator provenance, fallback side doors, engine decline, moto) | identical to SQLite: 64/64 holds, 8/8 moto, engine-decline rows as §4.3 |
| `tests` DB dialect checks | `quote_decision` 125 (operator 90); `required_claim` 62; `pending_question` PENDING 73 / RESOLVED 65 (the 8 unresolved = R4's 7 plus R3's 1 single-turn tests that end pending); duplicate `(thread_id, claim_id)` **0**; duplicate bookings per offer **0**; ACEPTADO with a motivo **0**; motivo outside PERDIDO **0**; `validated_as` `jsonb` |
| `probes` DB dialect checks | duplicate claim ids **0**; duplicate bookings per offer **0**; booked 8; ACEPTADO with a motivo **0**; motivo outside PERDIDO **0** |
| Teardown | by exact name: **0** `fr4rev-*` containers, **0** networks |

**Reviewer fixture artefact, recorded for transparency.** A third probe process (`p_ret.py no_claim terminal`) on the shared `probes` database failed with `UniqueViolation (whatsapp_contacts.wa_id)`. The harness's `wa_id` counter restarts per process, so it reused contacts `p_perdido.py` had created. This is my fixture, not the product. The retention and terminal behaviour on PostgreSQL is covered by the 203-test suite run (the R4 retention matrix, pending batching and outage tests, and the R4-R1 terminal-ack tests).

---

## 10. J. Independent mutation sample (reviewer-owned, own anchors)

**Method** (`mut/rev_mutants.py`, `mut/run.py`):

- Each mutant was applied exactly once (each hunk's count = 1) to a fresh `rsync` copy of `$E6/cand` and compiled with `py_compile`.
- Each ran against a targeted subset of the R4 / R4-R1 / R4-R2 / R3 / WILD1-C suites.
- KILLED = a failing test identity that passes in the union control.
- **Control: 136 tests, 136 passed, 0 failing.**
- The candidate is untouched: the hash of the 57 manifest paths was `f00e8082886fb015` before and after the campaign.
- A first pass reported all 16 as INVALID. That was a runner bug (`py_compile` refuses `/dev/null` as output), not a compile failure. It was fixed and re-run with the recorded control (`results_invalid_run1.json` is kept).

| # | Mutant | Verdict | Killed by (one of) |
|---|---|---|---|
| J01 | retention re-gated to Layer D `MAY_ASK` only | **KILLED** (8) | `RetentionBoundaries::test_turn_boundary_retains_a_burst_no_send_path_recorded` |
| J02 | every semantic consumer gets a fresh full budget | **KILLED** (4) | `OneTurnDeadline::test_pending_reread_and_slow_current_reading_do_not_double_the_turn` |
| J03 | undispatched reading (thread-start failure) runs inline | **KILLED** (1) | `test_thread_start_failure_is_bounded_and_never_runs_inline` |
| J04 | fallback Flow on PERDIDO bypasses the hold | **KILLED** (8) | `test_fallback_after_engine_decline_gets_the_neutral_data_copy` |
| J05 | text quote force (a burst naming a model year) bypasses the operator hold | **KILLED** (3) | `test_vehicle_and_zone_in_qualifying_no_longer_quotes` |
| J06 | exclusive hold lets the price and FAQ producers join | **KILLED** (1) | `test_every_text_case_is_held_on_both_paths` |
| J07 | engine-decline fallback uses the operator manual-review copy | **KILLED** (1) | `test_fallback_after_engine_decline_gets_the_neutral_data_copy` |
| J08 | information-only QUOTED turn runs the authorizer (RECONSIDERED) | **KILLED** (1) | `test_vehicle_information_alone_is_not_a_reconsideration` |
| J09 | guard not disarmed in `handle()`'s `finally` | **KILLED** (3) | `GuardLifecycle::test_no_listener_leaks_to_another_session_or_lead` |
| J10 | guard restores the stale turn-start pair (keeps `set_committed_value`) | **SURVIVED** | — (RV-F-02, §5) |
| J11 | successful booking does not clear motivo | **KILLED** (6) | `test_a_operator_perdido_precio_cleared_only_inside_the_booking_transaction` |
| J12 | motivo cleared before the booking rows are written | **KILLED** (1) | the same spy test |
| J13 | a failed (rolled-back) booking clears motivo in its own commit | **KILLED** (1) | `FailurePaths::test_forced_persistence_exception_inside_the_transaction` |
| J14 | CE and Data Exchange project differently (DX keeps motivo) | **KILLED** (1) | `test_data_exchange_projects_the_same_state` |
| J15 | website form adds "La revisión cuesta $TOTAL." | **KILLED** (1) | `ReviewGapKillers::test_r3_19_website_form_states_amounts_only_inside_the_block` |
| J16 | Flow preamble checked per segment, not accumulated (> 1,024) | **KILLED** (1) | `ReviewGapKillers::test_r3_25_accumulated_preamble_never_exceeds_the_meta_limit` |

**15/16 KILLED.** The survivor is a test-pin gap whose production behaviour is safe (§5).

---

## 11. K. Remaining-debt classification

| ID | Item | Classification |
|---|---|---|
| **RV-F-01** (new) | Motorcycle/quad/UTV after an unsubmitted contact-data Flow is auto-quoted, then auto-scheduled (Booking Flow + ISSUED offer). Pre-existing on base `aa2c8db`; reachable live (`WHATSAPP_FLOW_ID` set) (§2.4). | **Launch blocker.** Gate L4 (runtime certification, ACTIVE). Contract: M21.1.1 Layer A / M21.2-MF motorcycle handoff. Not introduced by WILD1-C. |
| RV-F-02 (new) | The guard's DB-current re-read is not pinned (J10 survives). The protection actually comes from `set_committed_value`; closeout §2 is imprecise (§5). | Post-launch debt (test pin); optional in the next correction |
| RV-F-03 (new) | A failed reading plus a deterministic answer gives "no pude procesarla" beside the answer in one message (§8). | Owner L-8 copy (R4-D4 class); not blocking |
| R1-D1 | An engine-declined lead with vehicle/zone/price text gets the restated quote with its CTA, PERDIDO kept, no RECONSIDERED. Reproduced (§4.3). | **Accepted** (owner 2026-11-17); no contradiction found |
| R1-D3 | An operator-held turn answers no FAQ and retains nothing. Reproduced (§2.3, §4.2: "¿qué revisan?" → hold copy only; the earlier PENDING row is untouched). | **Accepted** (owner); no contradiction found |
| R1-D4 | A terminal turn can resolve an earlier row whose re-read has already finished. With the provider down the row stays PENDING (§2.3). | Accepted |
| R4-D3 | Concern copy requires a resolved HESITATE in QUOTED. Works with a 3.5 s async reading (§7). | Accepted minimum-launch debt |
| R4-D4 | An ack on a failing non-question burst, repeated per failing turn. The re-read stays bounded (§3). | Accepted (ops alert on `PENDING_QUESTION` / `PERSIST_FAILED`) |
| R4-D5 | The turn-boundary net excludes `skipped_dedup` (Layer D's `faq_no_reply`). | Post-launch debt |
| R4-D6 | Shadow-worker backlog gives "unavailable" (ack + PENDING), never inline. Measured: 0 calls, 0.10 s (§3). | Intended behaviour change |
| R3RV-05 | An outbound row stuck in `pending` counts as delivered. | Post-launch debt (unchanged) |
| WC-05 | 20-message horizon for ordinary owed claims. | Accepted minimum-launch debt |
| WC-06 | `_rewrite_price` can render a wrong split as "$240.000 ×3". The engine's `PRICE_BLOCK_CORRUPTED_BY_VALIDATION` guard withholds it; no live trigger (§7). | Accepted minimum-launch debt |
| Calendar-dependent fixtures | `test_caseC_requested_day` (hard-coded 2026-09-01), the PostgreSQL `CONFIRM_DAYS_AHEAD` twin, and the R4-R2 slot allocator (today + 3…13). All pass today; date exposure remains. `or_73` is a pre-existing source-shape failure, identical on base. | Post-launch debt (test quality) |
| Info | The CE occupied-slot refusal writes a non-occupying `REQUEST_MANUAL_SCHEDULING` work item on an operator-PERDIDO lead; the PERDIDO pair is preserved (§6). DX reports internal failures as a typed `BookingSlotConflictError`. | Pre-existing; no action required by this milestone |
| Info | Worktree `__pycache__` (gitignored, pre-review) | Build from the manifest at consolidation |

No accepted owner decision is reopened: none of R1-D1, R1-D3 or the booking ruling (a) is contradicted by safety or launch evidence. RV-F-01 is a separate, pre-existing contract violation.

---

## 12. L. Live OFF proof (read-only)

There were two reads, at **2026-11-17T21:53:49Z** and **2026-11-17T22:12:10Z**, with identical values:

- `docker inspect` printed variable names and values for the selected flags only. Secrets were reported only as set or unset, with their lengths.
- Every SQL statement ran in `BEGIN TRANSACTION READ ONLY` (`transaction_read_only=on`) and ended in `ROLLBACK`.

| Check | Value |
|---|---|
| Backend image | `sha256:4952d5d5d289348811027c3a22c440e436ff83d46eb3a0d1e84f76123ae56903`, tag `ridecheck-crm-backend:minlaunch-candidate-aa2c8db`, started 2026-11-16T17:03:57Z, `GIT_SHA=aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008` |
| Eight launch switches | `OUTBOUND`, `SMART_BOOKING_CE`, `OPERATOR_WHATSAPP_NOTIFY`, `BOOKING_FLOW_DETAILS_PREFILL`, `CONVERSATION_ENGINE_DIRECT_WEBHOOK`, `EVIDENCE_ADMISSION`, `OUTSTANDING_QUESTION`, `LOCALITY_RESOLUTION_SAFETY`: all **false** |
| Dispatch | `SMART_BOOKING_CE_ENABLED=false` and `OUTBOUND_ENABLED=false`, so no booking dispatch and no send |
| Posture | `SHADOW_UNDERSTAND_ASYNC=true`, `SEMANTIC_SAME_TURN_TIMEOUT_SECONDS=6.0`, `RECONCILER_ACCEPTANCE_AUTHORITY_ENABLED=true`, no `OPENAI_CHAT_MODEL`; `WHATSAPP_FLOW_ID` set (16), `WHATSAPP_BOOKING_FLOW_ID` set (17) |
| Queues | pending outbound 0; sendable operator notices (`PENDING`/`SENDING`) 0; non-terminal `slot_recovery_jobs` 0; `pending_question` rows 0 |
| Database `crm_test` | alembic `20261104_vehicle_identity`; max message id 6288; max evidence id 36; max `ai_events` id 256; 0 inbound and 0 outbound messages in the last 24 h. Unchanged from the R3 review. |
| Booking 11 | `booking_commands` id 11 (CREATE, WHATSAPP_FLOW, occupying), md5 `a05fe9812afe751a2702e95655be2257`. It maps to revision 83 (2026-11-17 11:00, PENDIENTE, occupying), md5 `9ecfb1ade994fe195936a0a4934f1eae`, and thread_revision 10 (booked, 2026-11-17 11:00), md5 `d565b92303fa043e3fc6d0faf99194b0`. All identical at both reads. `booking_commands` 11/11, revisions 83/61, thread_revisions 10/1 (as in the R3 review). **Not touched.** The R3 review's `revisions` id 11 md5 refers to a different row (an unscheduled 2026-08-31 PENDIENTE record) with an unrecorded method, so it is not compared here. |
| WILD processes | none. The only matching process is this review's own tmux session. |
| Contact | No tester or customer was contacted; Meta was not called. Every reviewer container ran with `--network none` or on an `--internal` network. |
| Website | `/var/www/ridecheck/faq.html` mtime 2026-08-18 (not edited) |
| Candidate | Still uncommitted and unstaged (index 0; HEAD `aa2c8db`); manifest `1f714030…` verified at the end |

---

## 13. Section verdicts

| Section | Verdict |
|---|---|
| A. Integrity and manifest | **PASS** |
| B. Route-independent retention | **B.1–B.4 PASS** (56/56, 0 silent losses; terminal turns clean). **B.5 FAIL**: RV-F-01, pre-existing. |
| C. One semantic deadline | **PASS** (every turn ≤ 4.61 s; never inline; batching 3 rows / 4,000 characters) |
| D. Operator PERDIDO / engine decline | **PASS** (64/64 + 64/64 kill switch; engine-decline semantics as ruled) |
| E. Guard lifecycle | **PASS** (RV-F-02 LOW pin gap) |
| F. Booking exception and projection | **PASS** (CE ≡ DX; atomic rollback proven; idempotent; preflight covered) |
| G. Price, copy and Flow authority | **PASS** (G.5 unchanged against base; the unchanged authority is RV-F-01) |
| H. Test pins | **PASS** |
| I. Focused tests | **PASS** (SQLite NEW 0 / GONE 0; PostgreSQL 203/203) |
| J. Mutations | 15/16 killed; the survivor is a safe-behaviour pin gap |

## 14. Exact next authorized step

A bounded correction milestone on this branch, assigned to **L4 (runtime certification)** under the governance rule. It is limited to **RV-F-01**:

- Once a motorcycle/quad/UTV has been identified on a thread, no later turn may auto-quote, accept or schedule it while the motorcycle handoff is unresolved. "Identified" means a MOTO candidate is recorded, or the motorcycle contact-Flow token is pending.
- Any such turn must take the existing approved human handoff. That is the existing `_motorcycle_human_handoff` plain-text branch: `needs_human`, the existing review notification, `_MOTORCYCLE_HANDOFF_REPLY`.
- No new authority, pricing, Flow or copy.
- The tests must fail on the current candidate. They must cover the three phrasings × the follow-ups "está en Palermo, ¿cuánto sale?", "¿cuánto sale?", "en Palermo" and "dale, ¿mañana puede ser?", in the sync and live async postures, on SQLite and PostgreSQL. They must include the vehicle-fallback and website-form entries.

RV-F-02 (pin the DB-current re-read) may ride along as test-only.

After that, a **focused delta-only independent review** of that correction is needed. The WILD1-C R4 / R4-R1 / R4-R2 items closed here need only regression evidence in that review, not a re-review.

Only that review can open the controlled consolidation, image/kit update and dark deployment. **No commit, push, build, kit update, deployment, Meta change, tester contact, booking-11 action or WILD is authorized by this audit.**

**VERDICT: CORRECTION REQUIRED**

STOP
