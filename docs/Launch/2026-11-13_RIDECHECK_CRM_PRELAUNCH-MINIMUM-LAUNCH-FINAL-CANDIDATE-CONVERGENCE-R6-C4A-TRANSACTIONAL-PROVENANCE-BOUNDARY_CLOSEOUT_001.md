PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C4A-TRANSACTIONAL-PROVENANCE-BOUNDARY

DATE: 2026-11-13 → 2026-11-14 (UTC)
WORKTREE: `/opt/ridecheck-crm-r5-f1-response-validation` (branch `fix/prelaunch-r5-f1-response-validation-boundary`, HEAD `f16b1f3`)
EVIDENCE: `/opt/ridecheck-builds/crm-r6-c4a-provenance-20261113T200937Z` (referred to as `X/`)
VERDICT: **READY FOR INDEPENDENT TRANSACTIONAL-PROVENANCE REVIEW**

Nothing was staged, committed, pushed, built, finalized, deployed, migrated, enabled or sent. W-3 remains OPEN. The final WILD remains NOT RUN. The R6-C4 independent review, the kit chain and the rehearsal were not started. The R6-C4 K08 correction and kit evidence are unchanged: the seal-2 anchor is still `359062f49e8c2f3b7795d6d127ef28f360050d873276df4d7c6b5caef98557ac`, and `sha256sum -c` reports OK.

---

## 0. The rule, and one disclosure

The owner's rule: transactional truth must be authorized by **provenance and structured canonical facts**, not by successfully parsing generated prose. AI_COMPOSED prose must never reach a customer on the scheduling, quotation or booking paths. FAQ and conversational AI prose stays permitted.

**Disclosure.** The text of the owner's architectural-boundary addendum was not available in this session's transcript or in any project document. This milestone treats the R6-C4A prompt's Phase 2 rules 1–10 as its authoritative statement. If the addendum says more than those rules, the reviewer should check this candidate against it.

**Answer.** After R6-C4A, transactional safety no longer depends on recognising Spanish phrases in the model's output.
- On every transactional route, the model's body is discarded at the single boundary that every reply decision passes through. This happens before any consumer reads the body, so no recogniser, parser or scrub decides whether it is sent.
- Only declared `ComposedText` built from canonical services can be sent on those routes.
- The R6-C4 temporal analyser is retained only as defence-in-depth, for non-transactional AI prose (§9).

---

## 1. Complete outbound path inventory (Phase 1)

There is exactly one call site for the model: `ConversationEngine._reply_decision`. It has two callers:
- `_process_text`, with role COMMERCIAL_TURN;
- `_handle_general_information_ai`, with role GENERAL_INFORMATION, which is Layer D's FAQ route.

Every other customer-facing reply is already deterministic.

| # | Path / handler | Text source before R6-C4A | Transactional? | R6-C4A |
|---|---|---|---|---|
| 1 | Commercial catch-all reply in QUOTED / SCHEDULING / FLOW_SENT / BOOKED (`_process_text` post-decision send) | **AI_COMPOSED**, accepted by the R6-C4 parser + `_scrub_invented_price` + `_scrub_scheduling_confirmation` | yes | **body discarded at the boundary**; typed gate → `_send_transactional_reply` |
| 2 | `_try_schedule_and_flow` text fallback (`ai_reply or "Perfecto, tenemos disponibilidad..."`) | **AI_COMPOSED** when present | yes | ai_reply no longer used; deterministic text only |
| 3 | AI-proposed-time storage block (R6/R6-C2/R6-C4 parser-gated) | **state from AI prose** | yes | **removed** |
| 4 | `_apply_extracted` of AI `preferred_time_str` / `preferred_day_iso` | **state from AI extraction** | yes | fields stripped at the boundary on **every** route |
| 5 | FAQ route (Layer D `_FAQ_PATTERNS`, incl. "costo del servicio", "consulta") | AI_COMPOSED | only when the turn carries a price/scheduling signal or the thread is booked / has a Flow pending | route checked before the reply is read; transactional → renderer |
| 6 | Quote override `_composed_quote_reply` (PRICE/VEHICLE/LOCATION claims) | deterministic | yes | unchanged; now also the QUOTATION renderer |
| 7 | M21.2 price restatement | deterministic | yes | unchanged |
| 8 | `_handle_quoted_acceptance` | deterministic | yes | unchanged |
| 9 | ScheduleService handlers (`_handle_day_only_request`, `_handle_next_available_request`, `_handle_period_request`, ordinal, ordered requests, `_composed_slot_offer` AVAILABILITY claim) | deterministic | yes | unchanged; they own any slot statement |
| 10 | Booking Flow dispatch (`_send_booking_flow`) | deterministic / Meta Flow | yes | unchanged |
| 11 | `_process_flow_response` → BookingAuthority ack (`BOOKING_FLOW_RECEIPT_TEXT`, `BOOKING_HANDOFF_TEXT`) | deterministic, ledger-owned | yes | unchanged |
| 12 | `_handle_flow_failure` | deterministic | yes | unchanged |
| 13 | Escalations / `_generic_human_handoff` | deterministic | — | unchanged |
| 14 | Motorcycle / quad / UTV ApprovedTemplateText handoff | deterministic (approved template) | — | **preserved**, verified on SQLite and PG (R5-F1 prodpath) |
| 15 | Deferred-interest reply | deterministic | — | unchanged |
| 16 | Website form path | deterministic | — | unchanged |
| 17 | QUALIFYING conversational reply (no price/scheduling signal) | AI_COMPOSED | no | **permitted**, behind the default-deny validator |
| 18 | FAQ reply with no price/scheduling signal on a non-booked, no-Flow thread | AI_COMPOSED | no | **permitted**, verbatim |

**Phase 1 answers.**
- **Q1:** AI words could reach customers on rows 1, 2 and 5. AI-derived state could be written on rows 3 and 4.
- **Q2:** two new deterministic renderers were needed: the BOOKED status and the FLOW_PENDING reminder. Every other transactional reply already had a canonical renderer.
- **Q3:** the boundary can be centralised. Every model reply passes through `_reply_decision`.
- **Q4:** an owner STOP was not required. The addendum targets exactly the QUOTED-stage counterexamples that this boundary closes. The behavior change is conservative: replies become more deterministic, and no booking authority changes. It is disclosed in §5.
- **Q5:** moto/quad/UTV keeps its approved handoff template, and FAQ prose keeps its AI path on non-transactional turns.

---

## 2. The boundary

**Classification (`backend/app/services/transactional_boundary.py`, new, 102 lines, blob `cb89b2d9…`).** This file contains only pure functions and closed enums. It reads no text, writes no state and sends nothing.

`TransactionalRoute` has five values: NON_TRANSACTIONAL, QUOTATION, SCHEDULING, FLOW_PENDING and BOOKED. `classify_route(role, *, stage, has_flow_token, has_committed_revision, price_signal, scheduling_signal)` applies these rules in order:
1. A committed revision, or stage BOOKED → **BOOKED**.
2. A live booking token, or stage FLOW_SENT → **FLOW_PENDING**.
3. Role GENERAL_INFORMATION (FAQ) → QUOTATION on a price signal, SCHEDULING on a scheduling signal, otherwise NON_TRANSACTIONAL.
4. Stage QUOTED → **QUOTATION**; stage SCHEDULING → **SCHEDULING**.
5. Otherwise a price signal → QUOTATION, a scheduling signal → SCHEDULING.
6. Otherwise role COMMERCIAL_TURN → NON_TRANSACTIONAL.
7. An unknown role → **SCHEDULING** (fail closed).

The inputs are canonical thread state plus the engine's existing deterministic readings of the **customer's** words:
- price signal: `_detect_price_question` or `\bcostos?\b`;
- scheduling signal: a `_parse_scheduling_text` day or time, or `_detect_time_period`;
- an FAQ-role-only exemption for general hours questions that give no time (`_HOURS_STRONG_MARKERS`).

The **model's words are never an input**. If classification raises an exception, the route is SCHEDULING.

**Enforcement (`conversation_engine.py`).**
1. `handle` resets the per-turn `_turn_route` and `_discarded_ai_reply`.
2. `_reply_decision` returns `_apply_transactional_boundary(decision, role, ctx)`:
   - `AI_SCHEDULE_FIELDS` (`preferred_time_str`, `preferred_day_iso`) are stripped on **every** route;
   - on a transactional route the body is moved to `_discarded_ai_reply` and `decision["reply"] = None`.
   No consumer ever sees the body.
3. A **typed gate** sits before the commercial send. On a transactional route anything that is not exactly `ComposedText` goes to `_send_transactional_reply`.
4. The **FAQ route checks the route before it reads the reply**.
5. **Tripwire `_refuse_discarded_model_body`** sits in `_send_text_to_wa`, before validation. It refuses any undeclared value that contains the discarded body (whitespace- and case-normalised) and sends the safe-failure text instead.
6. **Renderer `_render_transactional_reply`** builds only from canonical sources, and each fact it states is a StructuredClaim validated against CanonicalFacts:
   - **BOOKED:** "Tu turno está confirmado para el {date_human} a las {HH:MM}." The date and time come from `_committed_booking_slot` (the committed revision), with a BOOKING claim. A person then takes the thread over (`post_booking_message`, with the approved handoff line). If there is no committed slot, the reply is the handoff only and states no slot.
   - **FLOW_PENDING:** `_TX_FLOW_PENDING_TEXT`. It restates no date or time.
   - **Stage already SCHEDULING at render time** (an acceptance advanced it this turn): `_TX_SCHEDULING_ASK_TEXT`. Added in round 2, see §7.
   - **QUOTATION**, or **SCHEDULING** before a quote: PricingService's `_composed_quote_reply`, or `_missing_quote_facts_reply`.
   - **SCHEDULING** after a quote: `_TX_SCHEDULING_ASK_TEXT` ("¿Qué día y horario te viene mejor para la revisión?"). Offering a slot remains the job of the ScheduleService handlers, which run on the customer's own words.
7. **Validator (`response_validator.py`, blob `24843e33…`).** If a BOOKING claim carries values, it must declare exactly one ISO date equal to `facts.booked_date` and one HH:MM equal to `facts.booked_times[0]`. Any other value is refused.

---

## 3. Production changes (against the sealed R6-C4 candidate `c4ref/`)

| File | Change | Blob |
|---|---|---|
| `backend/app/services/transactional_boundary.py` | **new**, 102 lines | `cb89b2d9a12d7bef4e52cf5454ddc81cbac31d64` |
| `backend/app/services/conversation_engine.py` | +211 / −35 | `ed1dc2fdff66df62eb2cc9174429b5f908c4b1d0` |
| `backend/app/services/response_validator.py` | +17 / −1 (BOOKING claim value check) | `24843e33f5eda2b5beab6eaaf0a80d2d09a13143` |

New engine constants:
- `_HANDOFF_POST_BOOKING_MESSAGE = "post_booking_message"`;
- `_HANDOFF_TRANSACTIONAL_FACTS_INCOMPLETE`;
- `_TX_SCHEDULING_ASK_TEXT`;
- `_TX_FLOW_PENDING_TEXT = "Te envié el formulario para confirmar el turno. Completalo con tus datos y terminamos la reserva."`.

Both customer-facing texts are registered in `CUSTOMER_FACING` in `test_r5_f1_template_validation_contract.py`.

**Seal (round 2, final).**
- `X/cand`: the worktree equals the sealed copy, with 737 files.
- Full manifest `dd7eada8c6b161720687827cb166317d14fc3df799f4956f3b49b4337311990b`.
- Tree pin `f1616dcef11eeabfcfaec9a2a5601f58473d26e56ccd22f3de4a9405de355e73` (686 files).
- App pin `fb2c8717799c2ca7061a99074780969cfeb04b9a574bd0c5b619b368a20e352d` (138 files).
- 16 paths changed against R6-C4 (`X/c4_to_c4a.diff.txt`).
- The round-1 seal is superseded and kept in `X/r1/`.

---

## 4. Test changes

**New: `tests/test_r6_c4a_transactional_provenance.py` (664 lines).** It uses REVIEWER bodies (the review's counterexamples), NOVEL nonsense bodies and CANONICAL (correct-looking) bodies, each across 4 postures (Smart Booking on/off × direct/n8n).

| Class | Proves |
|---|---|
| T01 Quotation | QUOTED: the body is discarded and the PricingService quote is sent with a validated PRICE claim. A price question with no zone asks for the zone, or the clarification authority hands off; no `$` is sent |
| T02 Scheduling | The body is discarded and the ask is sent. With an evaluated day, the ScheduleService handler or Flow owns the reply |
| T03 FlowPending | The body is discarded and the Flow reminder is sent |
| T04 CommittedBooking | The committed slot is stated, not the requested or offered one. Cancellation or recovery goes to a person. Missing facts → handoff with no slot |
| T05 | Model-extracted day/time are never written to state, on any route |
| T06 FAQ | Ordinary FAQ prose is delivered verbatim. A price question on the FAQ route is transactional |
| T07 | Route matrix; an unknown role fails closed; classification reads no text; a classification exception is transactional |
| T08 | The BOOKING claim validates only the committed date and start time |
| T09 | The tripwire refuses an undeclared value carrying the discarded body and passes declared values |
| T10 | Structure (AST): one model call site, which crosses the boundary; only the boundary and the tripwire read the discarded body; the discard happens before any consumer; no state comes from prose; the FAQ route checks the route first; the renderer uses canonical sources only; every rendered value is `ComposedText`; the tripwire runs before validation |
| T11 | The hours exemption is FAQ-only (added to kill B11) |
| T12 | A stage advanced this turn → the scheduling ask, with no claims (added in round 2, kills B12) |

The suite contains 30 tests and about 370 subtests, and all pass.

**Expectation changes in existing tests (each is a direct consequence of the rule):**
- **R6-C2 day/date production path** (+57/−5): adds the `route` and `discarded` fields, `assert_ai_body_discarded`, and `BOOKED_STATUS_TEXT`. needs_human now equals (route == BOOKED). The refused/kept assertions branch on route.
- **R6-C1 production path** (+10), **R6-C3** (+7/−4), **R6-C4** (+3) and the **R6 prose corpus** (+2): result-dict and route plumbing. The R6 full-engine prose corpus (QUALIFYING) behaves identically.
- **`test_r6_c2_day_date.py`** (+6/−5), **`test_r6_c4_default_deny.py` C4_09** (+3/−1) and **`test_r6_structured_response_contract.py` i04** (+6/−5): an AI-proposed time is now asserted **not stored**. Before, it was "stored when the parser accepted".
- **`test_r5_f1_template_validation_contract.py`** (+2): the two new customer-facing constants.
- **`test_sb1_booking_representation.py`** (+3/−1): the SB1 validator sentinel is re-recorded to `24843e33…`, with an R6-C4A comment.
- **`test_wild04r_phase2_observability.py`** (+24/−2), round 2:
  - OBS-04 now uses a non-price message, so its intent holds: a non-transactional AI turn defaults to `CE_AI`.
  - New OBS-04b: a price question is reported as `TRANSACTIONAL_RENDERER`.

---

## 5. Conservative behavior changes (disclosed)

1. In QUOTED, SCHEDULING, FLOW_SENT and BOOKED, **every** reply that would have been AI prose is now deterministic, including ordinary small talk. Expect the replies to sound more mechanical after a quote.
2. **Any free-text message after booking goes to a person.** The customer gets their committed date and time, plus the approved handoff line. The model never answers post-booking messages.
3. On the FAQ route, a price or scheduling signal makes the turn deterministic. The FAQ answer itself still comes from the deterministic FAQ reconciliation where it applies.
4. An AI-proposed time is **no longer stored** in any case, and AI-extracted day/time are never state. A slot is evaluated only from the customer's own words through ScheduleService.
5. Observability: replies on transactional routes are reported with `answer_source = TRANSACTIONAL_RENDERER`.

---

## 6. Mutations (`X/mut/`)

The runner bind-mounts each mutant read-only over the tree and runs ten suites with `-x`. KILLED means at least one failure or error. INVALID would mean an old text was not present exactly once; none occurred.

- **Runs 1 and 2 are void.** Their control runs failed on two pre-existing failures, `test_si05_revisar_ford_ka_antes_de_comprarlo` and `test_si_h1_prior_moto_mention_not_in_current_evidence`, which also fail on R6-C4. `--deselect` did not match those tests. Run 3 used `-k 'not …'`.
  - Files: `results.run1_invalid_control.txt`, `results.run2_invalid_control.txt`.
- **Run 3** (`results.run3.txt`, control 447 passed / 3,775 subtests): 20 of 21 killed. **B11 SURVIVED**: the hours exemption, applied to every role, was not caught. T11 was added. In the rerun (`results_b11.txt`, control 448 passed) **B11 was KILLED**, with 8 failures.
- **Round 2** (`results_b12.txt`, control 545 passed / 3,793 subtests; the F3 and observability suites were added to the set):
  - **B12** (the renderer ignores a stage advanced this turn) was **KILLED**, with 2 failures;
  - B11 and M01 were re-checked and are **KILLED**.

Final count: **22 of 22 mutants killed.** M01–M05b cover the owner's five required mutation classes:

| ID | Mutation | Result |
|---|---|---|
| M01 | allow AI_COMPOSED through (no discard, no typed gate, no FAQ check) | KILLED |
| M01a | boundary discard disabled only | KILLED |
| M02 | trust AI text after parser approval: the body is kept when the R6-C4 temporal analysis accepts it, and the gate lets undeclared text through | KILLED |
| M03 | store the AI-proposed time: the boundary stores the time read from the discarded body | KILLED |
| M03b | model-extracted scheduling fields are not stripped | KILLED |
| M04 | omit StructuredClaim validation: a BOOKING claim's values are never compared with the committed revision | KILLED |
| M04b | the booking-status renderer declares no claim | KILLED |
| M04c | a BOOKING claim accepts any committed time (end time too) | KILLED |
| M05 | render from noncanonical facts: booking status from the requested date and visible slots | KILLED |
| M05b | the quote renders an amount that is not PricingService's | KILLED |
| B01 | the send-side tripwire passes everything | KILLED |
| B02 | the FAQ route ignores price/scheduling signals | KILLED |
| B03 | a classification error is non-transactional (fail open) | KILLED |
| B04 | a live booking token / FLOW_SENT is not FLOW_PENDING | KILLED |
| B05 | a committed revision alone is not BOOKED | KILLED |
| B06 | QUOTED is not a transactional stage | KILLED |
| B07 | an unknown role is treated as FAQ (fail open) | KILLED |
| B08 | BOOKED no longer hands the thread to a person | KILLED |
| B09 | the FAQ route sends the model body without checking the route | KILLED |
| B10 | the commercial send gate checks emptiness instead of the declared type | KILLED |
| B11 | hours exemption applied to every role | SURVIVED in run 3 → KILLED after T11 |
| B12 | stale route at render | KILLED |

---

## 7. Results

### SQLite (image `ridecheck-crm-test:l4.7b2`, `--network none`, base = sealed R6-C4 `c4ref/`)

| Run | Base | Candidate | NEW | GONE |
|---|---|---|---|---|
| Focused (78 files), round 2 | 2,967 cases, 17 failing | 2,998 cases, 17 failing | **0** | **0** |
| Full suite, round 1 | 8,708 cases, 70 failing | 8,738 cases, 73 failing | **3** | 0 |
| Full suite, **round 2 (final)** | 8,708 cases, 70 failing | 8,740 cases, 70 failing | **0** | **0** |

All 70 remaining failures also fail on R6-C4 (COMMON, none changed), so they predate this milestone. 32 cases were added.

**Round 1 found three NEW failures, all outside the focused list:**
- **`test_wild04r_f3_faq_preservation` F3-01 and F3-03: a real defect in the R6-C4A renderer.**
  - The customer, in the quoted stage, accepts ("Okay !" / "Dale") and asks a question in the same burst.
  - The acceptance advances the canonical stage to SCHEDULING after the route was classified, so the stale QUOTATION route rendered "Para avanzar necesito saber en qué zona está el auto…" instead of the scheduling question.
  - **Corrected:** the renderer now follows the canonical stage at render time and sends the scheduling ask, which states no facts. T12 and mutant B12 cover it.
- **`test_wild04r_phase2_observability` OBS-04: a stale expectation.** Its message was a price question, which the rule now makes transactional. The test was updated and OBS-04b added (§4).

After the correction, the three suites passed on the worktree (127 tests / 370 subtests). The candidate was re-sealed and the candidate side of every run was repeated. Round-1 outputs are kept in `X/r1/`.

### PostgreSQL 16.13 (`X/pgstage.sh`, isolated `c4a-pg` / `c4a-net`, round 2)

- The schema (columns and constraints) is equal between base and candidate.
- PG list: 713 cases, 2 failing on both base and candidate → **NEW 0 / GONE 0**.
- SB2B clean: 48 cases, 1 failing on both → **NEW 0 / GONE 0**.
- Candidate production-path suites on PG:

| Suite | Tests | Subtests |
|---|---|---|
| R6-C4A provenance | 31 passed | 364 |
| R6-C4 default deny | 64 passed | 1,798 |
| R6-C3 | 68 passed | 1,025 |
| R6-C2 | 67 passed | 354 |
| R6-C1 | 72 passed | 1,471 |

- R5-F1 moto production path on PG: outstanding_questions 0, 26 threads.
- Teardown left 0 containers and 0 networks.

### Required checks

| Requirement | Result |
|---|---|
| SQLite NEW 0 | **0** (round 2) |
| PG NEW 0 / GONE 0 | **0 / 0** |
| No transactional AI words delivered | asserted for every body × posture, including word-level checks for words of six or more letters (T01–T04, production-path suites), and structurally by T10 and the tripwire |
| No state from discarded prose | T05, T10 and the `discarded()` helper: preferred time/day, pending fuzzy request, revisions, offers and outstanding questions opened by the reply model are all absent |
| Live before = after, byte-identical | yes (§10) |

Memory: no aborts. The lowest reading across all heavy jobs was 1,077 MiB (round-1 full candidate run). One heavy job ran at a time; see `X/memory.log`.

---

## 8. Residual risks

1. **Non-transactional AI prose can still state a fact.** On QUALIFYING and FAQ turns with no price or scheduling signal, the model could still write a date or price. That prose has no authority and writes no transactional state. It is guarded only by the R6-C4 default-deny validator, which is a phrase-recognition guard, so the R6-C4 residuals apply to it, e.g. "el 3008" passing as inert.
2. **The route is decided by recognising the customer's words.** The price and scheduling signals are the engine's deterministic recognisers of customer text. If a signal is missed on a QUALIFYING or FAQ turn, the reply stays on the non-transactional path (residual 1). It does not grant the model any transactional authority, and stage, token and revision always take precedence.
3. **More mechanical post-quote conversation, and more human handoffs after booking** (§5). This is a product cost the owner should accept knowingly.
4. **The addendum text was unavailable** (§0).
5. The R6-C4 kit and rehearsal were **not** re-run against this candidate, as instructed. Any kit check that pins the R6-C4 validator blob or tree must be re-pinned in a later milestone before the final WILD.
6. The pre-existing failures (70 in the full SQLite suite, 2 in the PG list, 1 in SB2B clean) are unchanged. They are not addressed here.

---

## 9. What the parser still does

The R6-C4 temporal analyser (`analyze_temporal` / `temporal_verdict`) and the scrubs are **kept unchanged**. On transactional routes they no longer grant anything: the body is gone before they could run, and mutant M02 (parser trust) is killed. They remain as **defence-in-depth** for non-transactional AI prose (residual 1) and as an extra check on any text on its way to be sent.

---

## 10. Live isolation

The live system was probed read-only (`X/live/probe.sh`, `default_transaction_read_only=on`) with only the three live containers running.
- Before: 2026-11-14T02:31:11Z. After: 2026-11-14T02:31:19Z. **The two outputs are byte-identical.**
- Compared with the R6-C4 after-probe of 2026-11-13T19:46Z, every live line is identical: containers, image ids, restarts, config hash, flags (OUTBOUND_ENABLED=false), alembic, `db_fingerprint=fbae2abdc3371f9146f0ac51a2ffa427`, max ids (msg 6268, offers 5, security 735, …), pending_outbound 0, file and flow hashes, deploy-prep HEAD, the R5 kit verify and the schema md5s.
  - The only difference is `worktree_status_sha16`. Round 2 newly modified one tracked file, `tests/test_wild04r_phase2_observability.py`. HEAD is unchanged and staged = 0.
- An earlier before-probe at 2026-11-13T22:13:06Z showed `running_containers=4` while a test container was running. It was retaken while the system was idle and is kept as `before.attempt1_busy.txt`.
- At the end there are 0 `c4a-` containers and 0 `c4a-` networks, staged = 0, and the R6-C4 kit seal-2 anchor is unchanged and verifies OK.

---

## 11. Verdict

**READY FOR INDEPENDENT TRANSACTIONAL-PROVENANCE REVIEW.** Transactional truth is now authorized by route provenance (canonical state) and by declared, claim-validated ComposedText from PricingService, ScheduleService and committed booking facts. On the quotation, scheduling, Flow-pending and booked routes, the model's body is discarded at the single boundary before anything reads it.

Suggested focus for the reviewer:
- the classification inputs (residual 2);
- the conservative behavior changes (§5);
- the round-2 renderer correction (§7).

W-3 remains OPEN. The final WILD remains NOT RUN.

STOP
