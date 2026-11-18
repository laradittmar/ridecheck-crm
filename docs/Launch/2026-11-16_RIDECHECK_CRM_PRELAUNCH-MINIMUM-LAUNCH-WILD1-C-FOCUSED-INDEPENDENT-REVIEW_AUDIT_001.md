PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-FOCUSED-INDEPENDENT-REVIEW

# WILD1-C (R2) — focused independent review

- **Date:** 2026-11-16 (UTC).
- **Reviewed candidate:** base `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008`, worktree `/opt/ridecheck-crm-wild1-correction`, branch `fix/prelaunch-wild1-conversation-correction`, index clean, 47 changed paths (uncommitted).
- **Closeout read in full:** `docs/Launch/2026-11-16_…WILD1-CONVERSATION-COVERAGE-PRICE-BREAKDOWN-AND-QUOTE-DECLINE_CLOSEOUT_001.md` (sha256 `6c0352b78ed1113a…`).
- **Supplied evidence:** `Z=/opt/ridecheck-builds/crm-wild1-correction-20261116T181238Z`. `Z/r2/cand/backend` and `Z/r2/cand/tests` are byte-identical to the worktree (`diff -rq`).
- **Review artefacts** (scripts, logs, junit, corpus): `/tmp/claude-0/-opt-ridecheck-crm-wild1-correction/3beada8a-a6a5-42fb-b498-c1c0e72b0f04/scratchpad/` (`snap/`, `runs/`). This is a session scratchpad and is not durable.

**VERDICT: CORRECTION REQUIRED**

The price breakdown, the decline/reconsideration semantics and the real-provider interpreter behaviour (L-1) are sound. Two issues independently block a READY verdict:

1. **WC-01 (BLOCKER, L-2).** Under the live posture, an explicit inspection-scope question is silently replaced by a generic reply. The live posture is `SHADOW_UNDERSTAND_ASYNC=true`, with R2 armed in the window. It happens whenever the interpreter has not finished by the time Layer D composes. No required claim is persisted, and the next reply does not answer it.
2. **WC-02 (HIGH, L-4).** An operator-written PERDIDO is reactivated by a customer acceptance through the real kanban actions. This is a reachable violation of the owner rule.

This review does not authorize a commit, push, build, kit update, deployment or WILD.

---

## 1. Isolation

| Check | Result |
|---|---|
| HEAD / branch | `aa2c8db…` / `fix/prelaunch-wild1-conversation-correction` |
| Index | empty (`git diff --cached --quiet`) |
| Other writers | `lsof +D` on the worktree showed only this session. The other `claude` processes' cwds are other worktrees: r5-f1 (×2), deployment-prep, release-candidate, `/root`. |
| Read-only snapshot | Taken before review. Manifest sha256 `45b6b9d82574bb1b…` (47 paths); porcelain status sha256 `51939d5a456f1797…`. Tree copy kept in the scratchpad. |
| End-of-review integrity | All 47 paths byte-identical to the snapshot; porcelain status hash unchanged; HEAD unchanged; index clean. |
| Test execution | Every run used a read-only mount (`-v …:/work:ro`), `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`. The SQLite runs used `--network none`. The PostgreSQL run used a disposable `postgres:16` on an `--internal` network. Mutants ran on throwaway copies in the scratchpad. |
| Writes to the candidate | This audit file only. |

## 2. A. Manifest and patch review

### 2.1 Manifest (independently derived: `git diff --name-only aa2c8db` ∪ untracked)

The production blobs match the closeout §2 exactly.

| Path | Status | sha256 (16) |
|---|---|---|
| `backend/app/services/conversation_engine.py` | M (+759/−74 across backend) | `58c3ecc918eba8e2` (blob `df88308a…`) |
| `backend/app/services/response_validator.py` | M | `aa1acf70262610b2` |
| `backend/app/services/response_composition.py` | M | `63b0f6344e709cca` |
| `backend/app/services/semantic_interpreter.py` | M | `b2e9feb5572bbfe3` |
| `backend/app/schemas/turn_evidence.py` | M | `4f0b4ca0fc087b8c` |
| `backend/app/ui/kanban.py` | M | `b819cd2254283c3d` |
| `backend/app/ui/kanban_view.py` | M | `2e1384e7cdee9c68` |
| `backend/app/services/claim_coverage.py` | A | `cd040b76864821be` |
| `backend/app/services/quote_decision_history.py` | A | `2faea303681bec71` |
| `tests/_wild1c_harness.py`, `tests/test_wild1_c_conversation_correction.py`, `tests/test_wild1_c_h1_regression.py` | A | `5374df39…`, `cf0f73f7…`, `047e0f9d…` |
| 34 modified test files | M (+126/−58) | listed in `snap/sha256.txt` |
| WILD1-C closeout | A | `6c0352b7…` |

### 2.2 Patch review

- **Protected paths.** `git diff --quiet aa2c8db` reports no diff for `migrations/`, `models.py`, `data/` (pricing CSVs), `meta/`, `api/`, `routes/`, `pricing.py`, `outbound_safety_gate.py`, `offer_lifecycle.py`, `flow_contract.py` or `repositories/`, and no `booking_*.py` is in the manifest. There is no migration and no pricing-rule or pricing-data change.
- **R1 and R2.** Since the evidence trees are identical to the worktree, the closeout's R1/R2 hunk claims hold. R1 touches the `_FAQ_REPORT_ANSWER` constant and its comment. R2 touches string literals in `_booking_flow_body`, the Flow-first prefix, `_TX_FLOW_CTA_TEXT`, `_TX_FLOW_PENDING_TEXT`, the legacy Flow body, the website-lead fallback and the scrub replacement, plus one docstring and comments.
- **Flow authority.** No WILD1-C function calls `_send_booking_flow`, `_dispatch_booking_flow_for_day` or `.issue(`. The only Flow change is the body string, plus `_with_coverage_preamble` in front of the existing `_send_flow_button`.
- **Edits to existing tests: none weakened.** Every changed expectation is one of:
  - an intentional pin move (`understand/1.20→1.22`, `INTERPRETER_SHA256`, SB1 frozen blobs, each with the previous value recorded in place);
  - structured-quote fixtures;
  - owner-ruled copy (R1 report, R2 Flow wording);
  - superseded rules (M20.6B1 "no breakdown" inverted to *require* the breakdown; FAQ-DEDUP-02 now counts only the canonical answer);
  - a reader-count pin (9→11, with both new readers named);
  - three new *refusal* cases added to `test_r6_structured_response_contract`.

  No assertion against false confirmation, a duplicate Flow, a generated state or the handoff was removed.
- **Secrets and artefacts.** I scanned every candidate path for API/Meta tokens, the second tester number, the booking-11 plate, live WAMIDs and the server address: none found. The tester number `549…8330` appears in two edited test files, but at the same count as in base (2 and 8); the candidate adds no occurrence. "2078" appears only as the documented H-1 thread reference. There are no caches, logs, junit or DB files among the untracked paths.
- **Closeout claim correction.** §4.1 says "every reachable quote uses the composer". That is not accurate: the website-form path still writes its own total sentence (WC-03).

## 3. B. Price breakdown

### 3.1 Contract (independent unit probe, `runs/price_unit.py`)

| Input | Result |
|---|---|
| `render_price_breakdown(150000, 90000, 240000)` | `Revisión: $150.000\nViáticos: $90.000\nTotal: $240.000` |
| zero viáticos `(140000, 0, 140000)` | `Viáticos: $0` is printed |
| `(1_000_000, 250_000, 1_250_000)` | `$1.000.000` / `$250.000` / `$1.250.000` |
| mismatch, float, `bool`, `str`, negative, `None` | all REFUSED (`PriceBreakdownError`) |
| validator claim `(240000,)` total-only | refused: "must declare the breakdown" |
| swapped `(90000,150000,240000)`; wrong-but-reconciling `(160000,80000,240000)` | refused: "differs from the canonical quote" |
| `(150000, 90000, 240000.0)` | refused: "does not reconcile" |
| unedited message through `validate_response` | block returned verbatim |

**Quote paths.** The first quote, the deterministic force, the AI-flag acceptance, the location and vehicle fallbacks, the price re-query, the transactional renderer and the `CANONICAL_PRICE` proposition all take the structured `PricingQuote`. `_quote_breakdown` raises on a bare number.

**No model supplies money.** Amounts come only from `PricingService.quote`: the base from `pricing_base.csv` in the image (sha256 `5f254ad1…`, identical to the repo), and viáticos from the `viaticos_zones` table. The only model-facing price text is the legacy reply-prompt hint, and that reply body is discarded (R6-C4B).

**Exception (WC-03).** On the website-form path, the canonical block does reach the customer (the plan appends it), but the handler's own sentence "El precio de la revisión es $240.000." precedes it. One message therefore labels both $240.000 and $150.000 as "la revisión".

**Validator flattening (WC-06).** When any *other* sentence of a plain message is rewritten, `validate_response` takes the re-join path. The block is then flattened onto one line ("…cotización: Revisión: $150.000 Viáticos: $90.000 Total: $240.000 La revisión sale $240.000"), and the rewritten sentence calls the total "la revisión". I found no live composition that triggers this today, so it is LOW. Strictly, though, the validator *can* flatten the block.

### 3.2 L-10: live price source (read-only, `BEGIN TRANSACTION READ ONLY`, `transaction_read_only=on`)

| Source | Berazategui viáticos | Notes |
|---|---|---|
| **live `crm_test.viaticos_zones`** (row id 198, `Sur|Berazategui`) | **90 000** | row md5 `30d0bb950972d7a4…`; Sur group default (id 285) 30 000; 211 rows, sum 10 970 000, table md5 `8197308eb31aa180…`; alembic `20261104_vehicle_identity` |
| repo `backend/app/data/viaticos_zones.csv` (sha256 `83ad374f…`) | 60 000 | 91 rows |
| repo XLSX `Pricing - tabla de viaticos/Zonas - viaticos Actualizado xls.xlsx` (sha256 `f28ec412…`), read through `seed_viaticos_zones._load_zone_rows` | 60 000 | 88 rows |
| base `SUV_4X4_DEPORTIVO` (`pricing_base.csv`, image and repo identical) | — | 150 000 |

**Conclusion.** PricingService reads viáticos from the live table, so H-1's $240.000 = 150 000 + **90 000** is the correct runtime value, and the fixtures (90 000) match it. Neither repository seed matches the live table. No migration sets Berazategui. The live table has 211 rows versus 88/91 in the seeds (per group: Sur 56 vs 15, Oeste 61 vs 15, Norte 42 vs 12, CABA 52 vs 49), so the live data was loaded or edited outside the repository. This is data drift, not a code defect. Re-running `seed_viaticos_zones` (`DELETE` + insert) would silently change customer prices. That is recorded as a hard pre-launch data action (§12). No customer data was read or printed.

## 4. C. Explicit-question coverage

- **Architecture.** Required claims (`SERVICE_SCOPE`, `REPORT`, `PAYMENT`, `PRICE_BREAKDOWN`, `AVAILABILITY`) are typed. Coverage is canonical-identity containment, whitespace- and case-normalised. A bridge cannot cover a claim: redundancy is judged on `ComposedBridgedMessage.canonical`. The FAQ producer adds the owed topics. The final check runs at `_send_text_to_wa` and on the Flow body. Mutants MI-1 (total-only restatement counted as the breakdown) and MI-2 (semantic topics never required) are **killed**.
- **Persistence.** Owed rows are `whatsapp_thread_evidence` (`claim_type=required_claim`, `outcome=HOLD`). Each `claim_id` is a sha256 over (thread, cycle, claim, sorted burst WAMIDs), backed by the DB constraint `UNIQUE (thread_id, claim_id)` (`uq_thread_evidence_claim`). Decision rows are keyed per (thread, cycle, decision, inbound WAMID). On PostgreSQL there are no duplicate claim IDs (§9).
- **Flow body limit.** A preamble that would exceed 1024 characters is omitted, logged, and the claims stay owed through `_record_coverage` → `_persist_owed_claims`. The H-1 preamble is about 700 characters. MI-7 (limit not enforced) **survives**: no test exercises an over-limit preamble.
- **H-1 transcript (6272 excluded).** I checked the fixture's customer words against `…PRIVATE-R6-C4B…/window-H-1/…/messages.json` (6269–6285): identical. I rendered H-1 three ways: the synchronous harness, live-async with an interpreter at p50 1.4 s, and live-async at p95 3.4 s. In all three, every turn reports `missing: []`, there are no owed rows, the decision list is empty, nothing is booked, there are no notices, and the texts are identical across postures (§8).
- **But:** H-1 escapes L-2 only because its first burst names a catalogue locality ("Berazategui"), so admission waits for the reading. Bursts without a locality do not wait (§5).
- **Gaps (LOW):**
  - the cross-turn ledger only sees `_query_active_messages` (the newest 20 messages), so an owed ask older than the window is dropped (WC-05);
  - a failed or blocked outbound row counts as having answered a claim (WC-07);
  - `_persist_owed_claims` swallows a flush `IntegrityError` without `rollback()` (WC-07).

## 5. D. L-2 delayed interpreter: **BLOCKER**

**Probe.** `runs/l2_probe.py`. Real route, production code unmodified, posture = the harness's live H-1 posture **plus the live `SHADOW_UNDERSTAND_ASYNC=true`** and the live 6.0 s timeout. Latencies were added at each model seam: reply 0.6 s, bridge generator 0.4 s, verifier 0.4 s. The interpreter is scripted at its transport seam with `faq_topics=["service_scope"]` (plus `report` for q4).

Inputs:
- q1 "¿Qué revisan?"
- q2 "¿Qué incluye la revisión?"
- q3 "hola queria saber que es lo que revisan cuando van a ver un auto" (voice-style)
- q4 "¿Qué revisan y qué me mandan en el informe?"
- q5 "Hola, buenas tardes! ¿Qué revisan?"

Each input ran in two stages: QUALIFYING first contact, and QUOTED after a delivered quote.

| Interpreter | QUALIFYING (q1–q5) | QUOTED (q1–q5) |
|---|---|---|
| complete (sync) | 5/5 answered | 5/5 answered |
| complete (async, instant) | 5/5 answered (1.5 s) | 5/5 answered |
| **pending** (async, 3.5 s) | **0/5 answered**, 1.5 s, reply "¿Me contás qué vehículo querés revisar y en qué zona está?", `required: []`, **0 owed rows** | 5/5 answered (≈5.0 s; the decline probe in the Layer D gate waits) |
| **timed out** (9 s > 6 s) | **0/5**, no owed rows | **0/5**, 7.5 s, reply "Gracias por tu mensaje. Cualquier otra consulta, escribime por acá.", no owed rows |
| **unavailable** (raises) | **0/5**, no owed rows | **0/5**, generic closing, no owed rows |

**Total: 25 of 50 turns lost the explicit scope question.** In every lost case:
- the coverage report was `{'required': [], …}`, so the loss was not even logged as missing;
- no `required_claim` row was written;
- the follow-up turn did not answer it.

The engine neither waited within its bound nor acknowledged the question. Both of the review's safe outcomes fail.

**Cause.**
1. In `_handle_general_information_ai`, Layer D arms `_turn_faq_burst` only on an explicit deterministic floor or a reading that is *already* `ready`, and scope has no floor.
2. `_coverage_evidence()` before the commercial arming point reads only a finished reading and never waits.
3. `_record_coverage` can only require what it knows, so nothing is persisted.
4. When R2 is armed, `_admit_turn_evidence` skips the semantic wait for any burst without a catalogue locality (W1F-R2).
5. The WILD1-C harness runs the interpreter synchronously (`shadow_understand_async=False`; its docstring omits the live async value), so every WILD1-C test sees a finished reading.

**Reachability.** Live: `SHADOW_UNDERSTAND_ASYNC=true`. The real-provider p50 is 1.4 s and p95 3.3 s (§6), against a Layer D turn of roughly 1.5 s. A first message without a locality ("Hola, ¿qué revisan?") is a common opening.

## 6. E. Real-provider check (L-1): **PASSES**

- **Contract.** `understand/1.22`, system prompt sha256 `cd3594624627883c…`, model `gpt-4o-mini`. The live backend has no `OPENAI_CHAT_MODEL`, so this is the default; `temperature=0`, the interpreter's own 30 s HTTP timeout.
- **Isolation.** I called `SemanticTurnInterpreter.interpret` directly in a separate container on the read-only candidate tree, with `DATABASE_URL=sqlite://`. No ConversationEngine, DB or outbound code ran. The key was passed only through the environment and never printed.
- **Context.** `stage=QUOTED` (quote delivered). Reconsiderations also carried the prior refusal as `previous_customer_turn`.
- **Corpus.** `runs/provider_corpus.json`, sha256 `da80f549408ed33eabe69167230f387b13aabfa1026b578f30e0fa8d4d346efa`, fixed before the first call. 24 synthetic inputs (the 18 given plus 6 more variants), 3 runs each = **72 calls, 72/72 ok** (valid JSON, parsed through the production parser).

**Confusion matrix** (signal per category; "admitted" = the engine's `_explicit_quote_decline` predicate: REJECT ∧ resolved status ∧ no scheduling request):

| Category (n) | ACCEPT | REJECT | HESITATE | QUESTION_ONLY | FUTURE_INTENT | none | admitted as decline |
|---|---|---|---|---|---|---|---|
| concern/question (21) | 0 | **0** | 11 | 1 | 0 | 9 | **0** |
| explicit refusal (21) | 0 | **21** | 0 | 0 | 0 | 0 | 21 |
| ambiguous/non-decline (18) | 0 | **0** | 3 | 0 | 3 | 12 | **0** |
| reconsideration (12) | **12** | 0 | 0 | 0 | 0 | 0 | 0 |

- **Refusals: 21/21 = 100% REJECT.** `decline_reason` was PRICE on all clear price refusals: R02 "Es muy caro, no sigo.", R03, R05, R06 and R07 ("ya conseguí otro … más barata"). It was OTHER on R01 "No, gracias, no voy a hacerlo." and R04 "Prefiero no avanzar.", which give no reason. Both are correct.
- **False REJECT: 0/39.** A01 "No mañana, ¿puede ser otro día?" → no acceptance signal. A06 "Lo hablo con mi señora y te aviso." → FUTURE_INTENT. C01 "Me parece caro, ¿cuánto hay de viático?" → HESITATE plus QUOTE_REQUEST.
- **Reconsiderations: 12/12 ACCEPT.**
- **Consistency.** 23/24 inputs were identical across all three runs. C07 ("me parece caro la verdad cuanto es sin el viatico") gave HESITATE / QUESTION_ONLY / HESITATE, which are both non-decline.
- **Latency** (n=72): min 959 ms, p50 1436, p90 2113, p95 3345, max 8219, mean 1728. Two calls exceeded the live 6.0 s same-turn bound. A timeout yields "no semantic evidence" and therefore never a decline (fail-safe; see the "unavailable" row in §5). This latency distribution is also what makes WC-01 reachable.

## 7. F. Decline and reconsideration

| Requirement | Result |
|---|---|
| Only an explicit resolved REJECT after a delivered quote writes PERDIDO | Holds (`_explicit_quote_decline`). The concern, question, ambiguous, day-request and no-reading cases are covered by tests. **MI-3b (delivered-quote guard removed) survives**: the guard is real but untested. |
| `motivo_perdida` from the existing vocabulary | PRICE → `PRECIO`, else `OTRO` (`MOTIVOS_PERDIDA_VALIDOS` = PRECIO/DISPONIBILIDAD/OTRO) |
| DECLINED history | Append-only, producer ENGINE, idempotent per (thread, cycle, decision, inbound), DB-unique |
| Transactional coherence (`runs/decline_tx_probe.py`) | Outbound on: flag + motivo + row + acknowledgement together. Kill switch off: `blocked_dispatch`, and flag, motivo and DECLINED row are committed together with the blocked outbound row, as the engine does elsewhere. |
| No needs_human, notice, offer, Flow or booking on decline | needs_human false, 0 notices, 0 offers, no token, 0 Flows |
| Quote follow-up stops | `quote_followup.py` (unchanged) selects only `flag='PRESUPUESTO_ENVIADO'` |
| Reconsideration | DECLINED kept, RECONSIDERED appended, PRESUPUESTO_ENVIADO → ACEPTADO/SCHEDULING through the normal path |
| Operator PERDIDO never reactivated | **Violated: WC-02** |

**L-4 (`runs/l4_probe.py`).** Operator actions are the real `app.ui.kanban_actions` functions on a real Session; the customer turn ("dale, avancemos", ACCEPT) uses the real route.

| Scenario | Operator writes (HTTP 303) | Lead before | After customer ACCEPT |
|---|---|---|---|
| S1 engine decline → `ui_lead_flag_set(PRESUPUESTO_ENVIADO)` → later `ui_perdido(PRECIO)` | yes | PERDIDO/PRECIO **written by the operator** | **ACEPTADO / SCHEDULING**, RECONSIDERED appended |
| S2 engine decline → `ui_lead_flag_clear` → later `ui_perdido(PRECIO)` | yes | PERDIDO/PRECIO **written by the operator** | **ACEPTADO / SCHEDULING** |
| S3 engine decline → operator re-confirms via the card's PERDIDO action | yes | PERDIDO/PRECIO | **ACEPTADO / SCHEDULING** |
| S4 control: operator PERDIDO, no engine row | yes | PERDIDO/PRECIO | held (PERDIDO/QUOTED) |

The card renders the PERDIDO action on every non-RECOMPRA card, including cards already PERDIDO, and its select's first (default) option is `PRECIO` (`render_lead_card`). S1 and S2 are unambiguous: the PERDIDO that was reactivated was written by an operator, after the engine's PERDIDO had been removed by an operator. The engine cannot tell, because `_engine_written_decline` compares only the newest decision row and the motivo, and the kanban writes leave no trace. Mutants MI-4 (oldest row read) and MI-4b (legacy path never holds an operator PERDIDO) **survive**, so the boundary is under-tested. **Classification: HIGH, blocking.** It is a reachable violation of an explicit owner rule. The impact is bounded: it fires only on a customer's explicit acceptance, and the history shows it.

## 8. G. Copy and booking authority (rendered independently)

H-1 corrected rendering, async p95 posture. Bridge lines are the harness's scripted claim-free bridge.

```
T1 (voice, scope + payment)  → vehicle question + payment answer + scope + same-day report
T2 Jeep Renegade 2024        → "Genial! Te paso la cotización para la revisión del Jeep Renegade 2024 en Berazategui:
                                 Revisión: $150.000 / Viáticos: $90.000 / Total: $240.000 / Si te parece bien, podemos avanzar."
T3 ¿de qué consta? + caro, ¿viático?  → same block + scope answer (not a decline: flag PRESUPUESTO_ENVIADO)
T4 en precio, ¿mañana? + ¿viático? + ¿qué informan?  → FLOW: "Te paso el detalle de la cotización:" block,
                                 report answer, "Para mañana martes 17/11 tengo 5 horarios disponibles. Elegí el horario
                                 que te sirva y completá tus datos para solicitar el turno."
T5 Si / T6 Mañana            → "Te envié el formulario para solicitar el turno. Completalo con tus datos y terminamos la reserva."
```

- **Scope.** Factually supported by the published `faq.html` (sha256 `31bdcab4…`; scope, test drive, report contents). The report timing says "durante el mismo día" (owner ruling R1).
- **Decline acknowledgement.** "Entendido, gracias por avisarnos. Si más adelante querés retomar la revisión, escribinos por acá." It is respectful and carries no pressure.
- **No pre-persistence confirmation.** An AST scan of every engine string literal shows confirmation wording only in: the BOOKED render "Tu turno está confirmado para el …", which requires `_committed_booking_slot`; the forbidden-phrase list; the slot-taken notice; and the L-8 strings ("¡Ya te confirmo!", "te confirmamos a la brevedad", "Te confirmamos si podemos hacerlo"). "Ese horario está disponible" appears only behind `time_verified` / `preferred_time_str`. The Flow-first body names the count of available options and never "that time".
- **Mutants.** MI-5a (pending text), MI-5c (website fallback) and MI-8 (M16 re-anchored on R2) are **killed**. **MI-5b survives**: the AI-confirmation scrub replacement ("Para solicitar el turno necesitamos…") is outside the R2 source guard, so reverting it to "Para confirmar el turno" passes every suite.
- **Motorcycle/quad/UTV handoff.** No diff on those branches; `test_r5_f1_motorcycle_handoff_production_path` and `test_m21_2_mh_motorcycle_handoff` pass (§9).
- **Copy observations (LOW, owner L-8 review):**
  - T3 answers "Me parece caro…" with "Genial! Te paso la cotización…" plus the CTA "Si te parece bien, podemos avanzar." (WC-09);
  - on an operator-held PERDIDO (S4), "dale, avancemos" gets the quote again with the same CTA, inviting an acceptance the engine will hold again (WC-08).
- **Website.** `/var/www/ridecheck/faq.html` still says "…luego dentro de 24 a 48 horas te entregamos el informe en formato PDF". This contradicts the owner's same-day ruling and is a hard pre-launch content action (§12). The website was not edited.

## 9. H. Test evidence

**Supplied evidence, reconciled.** `Z/cand3.cmp.txt` (full SQLite: base 8 798 / candidate 8 852, NEW 0, GONE 0, COMMON 89), `Z/pg.cmp.txt` (713/713, NEW 0, GONE 0, COMMON 2), `Z/pg_sb2b.cmp.txt`, `Z/r2/focused_r2.log` (850 cases, 843 passed) and `Z/r2/pg_w1c.txt` (63 passed) all agree with the closeout. The tree hashes reproduce and I found no discrepancy that would require the full historical matrix, so it was not repeated.

**Mutation evidence correction.** The "24/24 killed" figure was measured on `cand4`, before R1 and R2. On the final tree, M16's anchor (`lead = "Ese horario…" if time_verified else ""`) no longer exists (count 0). 23 of the 24 supplied mutants still apply. I re-anchored M16 as MI-8, and it is killed.

**Independent reruns:**

| Run | Result |
|---|---|
| Focused SQLite set: 42 files. Covers both WILD1-C suites; the FAQ/coverage suites (7); price contract (7, incl. `test_r6_structured_response_contract`, `test_m20_6b1`, `test_l4_7d`, `test_l3`, G3-6/G3-7); decline/acceptance (6, incl. `test_l4_7c_3b`, F7b/d/e/f/g); Flow copy and booking authority (13, incl. R6-C4, C4A, C4B (+R1A/R1B), `test_wild1_booking_flow_gate_and_contract`, `test_l4_7w4_f1`, `test_m21_3_c_d`, `test_sb2b_option_b`, `test_convergence_p3_cutover`, `test_l4_7w3_f1`); motorcycle (2); W1F R1/R2/R3 + operator signal (4). Base `aa2c8db` comes from `git archive` (40 files: same set minus the WILD1-C suites). | base 1 465 cases / candidate 1 528. **NEW 0, GONE 0, COMMON 7, COMMON_changed 0.** 63 added cases, all passing. The 7 common failures are the known clock-dependent "lunes 16/11" fixtures (C4-PP ×3, C4A ×3, C4B P04 ×1), identical on base. |
| PostgreSQL 16, disposable, `--internal`, tmpfs; schema = pre-schema record + `alembic stamp` + `upgrade head` (`20261104_vehicle_identity`); both WILD1-C suites | **63 passed, 0 failed.** 14 `quote_decision` and 1 `required_claim` rows; `source_message_ids` is `jsonb`; duplicate `(thread_id, claim_id)` = 0. Torn down: 0 containers, 0 networks. |
| L-2, L-4, H-1 async, website-form, decline-transaction and price-unit probes | §3–§8 |

**Independent mutation sample** (`runs/rev_mutants.py`, distinct from M01–M24). Each mutant ran on a throwaway copy against 7 suites; KILLED = a failing test identity not present in control.

| Mutant | Boundary | Result |
|---|---|---|
| MI-1 coverage accepts a total-only restatement as the breakdown | total-only acceptance | KILLED (`test_t4_flow_carries_every_owed_answer_first`) |
| MI-2 semantic FAQ topics never become required claims | missing required FAQ claim | KILLED (5 tests) |
| MI-3 REJECT beside a day request admitted | concern/ambiguity treated as rejection | KILLED |
| MI-3b decline without a delivered quote | decline admission | **SURVIVED** |
| MI-4 lost-lead rule reads the oldest decision row | operator PERDIDO reactivation | **SURVIVED** |
| MI-4b legacy path never holds an operator PERDIDO | operator PERDIDO reactivation (authority-off) | **SURVIVED** |
| MI-5a Flow-pending text says "confirmar el turno" | pre-persistence confirmation | KILLED |
| MI-5b scrub replacement says "confirmar el turno" | pre-persistence confirmation | **SURVIVED** |
| MI-5c website-lead fallback says "confirmar el turno" | pre-persistence confirmation | KILLED |
| MI-6 validator returns the original text after a rewrite | validator flattening/safety | KILLED (`test_val_price_02_wrong_amount_rewritten`) |
| MI-7 Flow body limit not enforced | Meta 1024 bound | **SURVIVED** |
| MI-8 (M16 on R2) Flow body always "Ese horario" | premature availability | KILLED (4 tests) |

**7/12 killed.** The five survivors are test gaps at boundaries this milestone introduced.

## 10. I. Live isolation (read-only; taken twice, values identical)

| Check | Value |
|---|---|
| Backend image | `sha256:4952d5d5d289…6903`, tag `ridecheck-crm-backend:minlaunch-candidate-aa2c8db`, `GIT_SHA=aa2c8db…`, started 2026-11-16T17:03:57Z (unchanged) |
| Eight switches | OUTBOUND, SMART_BOOKING_CE, OPERATOR_WHATSAPP_NOTIFY, BOOKING_FLOW_DETAILS_PREFILL, DIRECT_WEBHOOK, EVIDENCE_ADMISSION, OUTSTANDING_QUESTION, LOCALITY_RESOLUTION_SAFETY: all `false` |
| Dispatch | `booking_dispatch_allowed=False` |
| Readiness | `GET /login` 200 |
| Queues | pending outbound 0 / non-terminal slot-recovery jobs 0 / sendable operator notices 0 |
| WILD window processes | none (WATCH/ENABLE/DEADMAN/KILL/oneoff) |
| DB fingerprint (kit `db_fingerprint.sql`, `default_transaction_read_only=on`) | `afce3d30ca1d7098409c68d5ba730e4e`, identical at both reads; max message id 6288, max evidence id 36 at both |
| Writes | none. Every live SQL statement ran in a `READ ONLY` transaction; the env reads printed switch values only, never secret values. Booking 11 was not touched. |

## 11. Findings by severity

| ID | Severity | Finding | Gate / evidence |
|---|---|---|---|
| **WC-01** | **BLOCKER** | L-2: under the live async posture, an explicit scope question on a Layer D turn is silently replaced by a generic reply when the interpretation is pending, timed out or unavailable. No owed claim is persisted and the next reply does not answer it (25/50 probe turns lost). | §5. The W1F-R2 no-wait carve-out must remain for bare greetings, so the fix must either bounded-wait when the burst carries an explicit question, or persist the burst as owed-pending and resolve it from the finished reading on the next reply (or both). |
| **WC-02** | **HIGH** | L-4: an operator-written PERDIDO (operator reopens/clears and later marks PERDIDO, or re-confirms it, with PRECIO) is reactivated by a customer acceptance. This is a reachable owner-rule violation. | §7 S1–S3; MI-4/MI-4b survive. The engine needs evidence of operator flag writes newer than its DECLINED row (e.g. an append-only operator decision row from the kanban/API PERDIDO and flag actions). |
| **WC-03** | MEDIUM | The website-form quote (live `solicitar-revision.html` → wa.me prefilled form) still writes "El precio de la revisión es $240.000." before the canonical block, so "revisión" is labelled with two amounts, and the message carries two CTAs. Closeout §4.1's "every reachable quote uses the composer" is inaccurate. | `runs/webform_probe.py`; `_handle_website_form` |
| **WC-04** | MEDIUM | Evidence fidelity: the WILD1-C harness runs the interpreter synchronously, while live is async, so no WILD1-C test can see WC-01. The "24/24" mutation figure predates R1/R2 (M16 no longer applies). Five independent mutants survive (MI-3b, MI-4, MI-4b, MI-5b, MI-7). | §9 |
| WC-05 | LOW | The cross-turn ledger horizon is the newest 20 messages; an owed ask older than the window is silently dropped. | `_query_active_messages` LIMIT 20 |
| WC-06 | LOW | The validator's edited path flattens the canonical block onto one line, and `_rewrite_price` labels the total as "la revisión"; no live trigger found. | §3.1 |
| WC-07 | LOW | `_persist_owed_claims` swallows a flush error without `rollback()` (concurrent same-burst duplicate only; the DB-unique constraint prevents duplicate rows). The ledger counts failed or blocked outbound rows as answers. | §4 |
| WC-08 | LOW | An operator-held PERDIDO lead that says "dale" receives the quote again with "Si te parece bien, podemos avanzar". | §7 S4 |
| WC-09 | LOW (copy) | A price concern ("Me parece caro…") is answered with "Genial! Te paso la cotización…" plus a CTA; owner L-8 copy review. | §8 |
| L-1 | closed | The real provider separates concern from refusal: 0 false REJECT, 21/21 refusals, correct PRICE, 12/12 reconsiderations, 72/72 valid. | §6 |
| L-10 | settled | Live Berazategui viáticos is 90 000 (repo seeds 60 000): data drift, not code. | §3.2 |

## 12. Hard pre-launch actions (independent of the code correction)

1. **Website timing.** Correct `/var/www/ridecheck/faq.html` "¿Cuándo recibo el informe?" from "dentro de 24 a 48 horas" to the owner's same-day wording before launch. It was not edited here.
2. **Viáticos source of truth.** The owner should confirm the live `viaticos_zones` table (211 rows; Berazategui 90 000; table md5 `8197308eb31aa180…`) as authoritative, then bring `viaticos_zones.csv` and the XLSX seed in line with it (or retire them). `seed_viaticos_zones` deletes and re-inserts, so running it today would silently change prices.
3. **Owner copy review (L-8).** `_QUOTE_DECLINE_ACK`, `_COVERAGE_PRICE_LEAD`, the quote lead, the concern response (WC-09) and the held-lead response (WC-08).
4. **Kit/WILD posture.** Any WILD or test harness used as evidence for conversation coverage must run `SHADOW_UNDERSTAND_ASYNC=true` with realistic model latency.
5. **Booking 11.** It is still occupying the agenda until 2026-11-17 11:45 ART, and was not touched by this review. Its handling belongs to the owner's existing decision.

## 13. Exact next authorized step

A bounded correction milestone on this branch: **WILD1-C-R3**. Its scope:

- **WC-01:** no silent loss of explicit supported questions on Layer D under pending, timed-out or unavailable interpretation; the bare-greeting no-wait contract is preserved.
- **WC-02:** an operator-written PERDIDO is never reactivated, including after an operator reopen and re-mark.
- **WC-03:** a single coherent breakdown on the website-form quote.
- **WC-04:** run the WILD1-C suites in the live async posture as well, and add tests that kill MI-3b, MI-4, MI-4b, MI-5b and MI-7.

WC-05 to WC-09 may ride along only if they stay inside those touch points; otherwise record them.

After that, a focused delta-only independent review of R3. Only that review can open the controlled commit and the consolidated image/kit update. No commit, push, build, deployment, migration, Meta change, tester contact or WILD is authorized by this audit.

**VERDICT: CORRECTION REQUIRED**

STOP
