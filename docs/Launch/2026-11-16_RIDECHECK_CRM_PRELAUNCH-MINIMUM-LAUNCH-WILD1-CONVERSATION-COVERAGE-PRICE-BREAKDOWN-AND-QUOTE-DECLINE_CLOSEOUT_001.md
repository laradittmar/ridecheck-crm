PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-CONVERSATION-COVERAGE-PRICE-BREAKDOWN-AND-QUOTE-DECLINE

# WILD1-C — conversation coverage, price breakdown and quote decline

- **Date:** 2026-11-16 (UTC).
- **Base:** `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008`, tree `ed78a9a374f688a76f7c8ef6787fcc6ce1f25ecd` (verified before editing).
- **Worktree / branch:** `/opt/ridecheck-crm-wild1-correction`, `fix/prelaunch-wild1-conversation-correction` (new, created from the base; clean before editing; no other session's cwd).
- **Evidence:** `Z=/opt/ridecheck-builds/crm-wild1-correction-20261116T181238Z` (base snapshot, three candidate snapshots, junit, comparisons, mutants, PG stage).
- **Regression fixture:** window H-1, thread 2078, messages 6269–6288 (`P/window-H-1/evidence/collected/private/messages.json`, `window_backend_*.log`). Message 6272 (sent by hand from the CRM inbox) is excluded from the conversational replay.
- **Revision:** R1 — `PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R1-REPORT-TIMING-ALIGNMENT` (report-timing copy only; §10).
- **Revision:** R2 — `PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R2-FLOW-CTA-COPY` (pre-persistence Flow / booking-request copy only; §11).
- **Owner ruling recorded (2026-11-16, in session, R2):** before successful persisted Flow completion, customer-facing text describes selecting, completing or **requesting** a booking; it must not say or imply that the appointment is already confirmed. No "confirmado", "confirmar el turno" or "confirmá el turno" before persisted Flow success.
- **Owner ruling recorded (2026-11-16, in session, R1):** the detailed inspection report is sent **during the same day** as the inspection; the inspector may explain the principal findings at the end of the on-site inspection. The customer-facing promise is "the same day" — not "immediately at the end" and not "within 24–48 hours". This ruling **supersedes** the public FAQ's 24–48 h wording.
- **Owner ruling recorded (2026-11-16, in session):** option 1 — the engine may write `flag=PERDIDO` only on an explicit semantic decision not to proceed; `motivo_perdida` PRECIO/OTRO; append-only DECLINED history with source ENGINE; stop the quote follow-up; visible on the kanban; only an engine-written PERDIDO may be reactivated by an explicit reconsideration; history kept; an operator-written PERDIDO is never overwritten or reactivated; focused tests for concern vs refusal; the relaxation documented.

Nothing was staged, committed, pushed, built or deployed. No live database write, Meta action, outbound message or tester contact. The live backend container was read once, read-only (`docker exec … env`), to record the H-1 switch posture. The deployment-preparation worktree and every sealed kit are untouched.

**VERDICT: READY FOR ONE FOCUSED INDEPENDENT REVIEW**

All five corrections are implemented without a migration and without touching booking, Flow, offer, gate or pricing code. Full SQLite suite vs base: NEW 0 / GONE 0. PostgreSQL stage vs base: NEW 0 / GONE 0. 24/24 mutants at the new boundaries killed. R1 changed only the canonical report copy; R2 changed only pre-persistence Flow / booking-request copy (production diffs: string literals, a docstring and comments in `conversation_engine.py`). The material residual is L-1: the interpreter prompt change (`understand/1.22`) is evidenced only with scripted payloads.

---

## 1. Root cause of each finding

| # | H-1 finding | Root cause (code) |
|---|---|---|
| F1 | Turn 1 (6269/6270: scope + cash + debit) answered only "¿qué vehículo…?" (6271) | Since R6-C4B the model body is discarded and FAQ answers come only from the plan's FAQ producer, which reads `_turn_faq_burst`. Layer D (`_handle_general_information_ai`) returns **before** the commercial path arms it, so the producer had nothing to answer. |
| F2 | Only a total was ever quoted ("…es de $240.000") | `_composed_quote_reply` / `_build_quote_reply` received `precio_total` only; every one of the five call sites passed `quote.precio_total`. The structured `PricingQuote` (base + viáticos) was available and discarded. The validator's PRICE claim accepted a 1-tuple. |
| F3 | Inspection scope "not answered" (asked in 6270, 6276, 6281) | (a) the canonical scope answer named nothing that is inspected ("Vamos hasta donde está el vehículo… informe con todo lo que encontramos"); (b) a re-ask was suppressed as "already answered this cycle" (log `already_answered=['service_scope']`) because only explicit literal asks bypass the cycle dedup and scope has no literal detector. |
| F4 | Viáticos never answered (6277, 6280) | "cuánto hay de viático" matched no `_PRICE_QUESTION_PATTERNS`; the turn went to Layer D (6278) and the money question was dropped. 6280 matched "cuánto es", but the restatement was total-only (F2). |
| F5 | Generic closing on the price objection (6278 "Gracias por tu mensaje…") | Layer D intercepted "¿de qué consta?" + "me parece caro, ¿cuánto hay de viático?" before the commercial path; the canonical segment was the NO_QUESTION fallback and there was no money/FAQ coverage. |
| F6 | "Para mañana tenés horarios?" + report question dropped (6279–6282) | The deterministic price re-query branch ran first and returned the total alone; the day request and the report question never reached a handler. |
| F7 | CRM not updated on rejection | No engine transition for a quote decline existed; the engine contract forbade `flag=PERDIDO`. |
| F8 | Premature "Ese horario está disponible" (6286) | `_send_booking_flow` hard-coded the line in every Booking Flow body; the Flow-first day request prepends "…tengo 5 horarios disponibles…" before any time is chosen. |

Note on F7 against the owner ruling: in H-1, "Me parece caro, ¿cuánto hay de viático ahí?" is a concern plus a question, not an explicit refusal. Under the ruling it is **not** a decline; the corrected candidate answers it (breakdown + scope) and writes no PERDIDO. The decline transition is exercised by explicit refusals in the focused tests.

## 2. Exact production files changed

| Path | Status | git blob | sha256 (16) |
|---|---|---|---|
| `backend/app/services/conversation_engine.py` | M | `df88308a42e5a3c9cad8070700a8f39d4cc0ebb5` | `58c3ecc918eba8e2` |
| `backend/app/services/response_validator.py` | M | `be05839e4833ac3e4843998f39eca4bf0809dde5` | `aa1acf70262610b2` |
| `backend/app/services/response_composition.py` | M | `7e9349e75cbe09a22db01fe9be9122d7b4df3552` | `63b0f6344e709cca` |
| `backend/app/services/semantic_interpreter.py` | M | `120ed02d8e0fe866ff50fb4a39c78c1979de5fdb` | `b2e9feb5572bbfe3` |
| `backend/app/schemas/turn_evidence.py` | M | `aaae370f417c38ea3edb2a8f2c939945a3124b84` | `4f0b4ca0fc087b8c` |
| `backend/app/ui/kanban.py` | M | `4d25fda5cfbd30413d08f5a613cc24bcde6c82f0` | `b819cd2254283c3d` |
| `backend/app/ui/kanban_view.py` | M | `6ede1e86f0c273486c505de8825069941652a796` | `2e1384e7cdee9c68` |
| `backend/app/services/claim_coverage.py` | A | `fd4d70ce5b73f547d807782c225e2cf9b226202b` | `cd040b76864821be` |
| `backend/app/services/quote_decision_history.py` | A | `3a238d12dcdd019ef716b4180fb0d9ea4a558550` | `2faea303681bec71` |

7 modified and 2 added (239 lines). `Z/cand3` … `Z/cand5` carry byte-identical `backend/` (`diff -rq`); R1 (`Z/r1/cand`) differs from `Z/cand5` in `conversation_engine.py` only — the report constant and its comment (§10). R2 (`Z/r2/cand`) differs from R1 in `conversation_engine.py` only — Flow / booking-request copy (§11). Engine blobs: pre-R1 `c53c3f968f6b20e4aa13ce4b107e19aa84f3952e` (sha256 `05b5608f24c109a0`); R1 `81273c8aaf001dc2bff197d9c2fc9db82a10ac2c` (`8217ba1f08f01ab4`); R2 (current) `df88308a42e5a3c9cad8070700a8f39d4cc0ebb5` (`58c3ecc918eba8e2`).

No migration. No change to `models.py`, `backend/migrations`, `meta/`, booking / Flow / offer / gate / pricing modules, `pricing_base.csv`, `viaticos_zones.csv`, API or route modules (`git diff --quiet aa2c8db -- …` over all of them: no diff).

## 3. Migration

**Not required.** Current state uses existing `leads.flag` / `leads.motivo_perdida` (existing vocabulary `PERDIDO`, `PRECIO`, `OTRO`). History uses existing `whatsapp_thread_evidence` rows (`claim_type` is free `String(48)`; `outcome='ACCEPT'` satisfies the existing CHECK). The cross-turn coverage ledger uses the same table (`claim_type='required_claim'`, `outcome='HOLD'`). The new interpreter field `acceptance.decline_reason` lives in the in-memory/JSON `TurnEvidence` schema only.

## 4. Design

### 4.1 Price composition (canonical)
- `response_validator.render_price_breakdown(base, viaticos, total)` — the one rendering:
  ```
  Revisión: $150.000
  Viáticos: $90.000
  Total: $240.000
  ```
  Refuses (`PriceBreakdownError`) unless all three are exact non-negative `int`s (not `bool`/float/str) and `base + viaticos == total`. Viáticos is always printed, including `$0`. Argentine formatting (`$` + dot thousands) identical to `_format_price_ars`.
- `_build_quote_reply(…, quote, …)` takes the structured `PricingQuote` (a bare amount raises `TypeError`); `_composed_quote_reply` declares `StructuredClaim(PRICE, (base, viaticos, total))`.
- Validator (`_validate_structured_claim`, `validate_envelope`): a declared PRICE must be the 3-tuple, reconcile, equal `(quote_base, quote_viaticos, quote_total)` position by position, **and** the canonical block must appear verbatim in the rendered text. Total-only, swapped, mismatched or unrendered declarations refuse the whole value.
- `validate_response` returns text **as written** when every sentence survived unedited (previously re-joined with single spaces, which would flatten the block). Any drop/rewrite still takes the old path.
- Every reachable quote uses the composer: first quote, deterministic force, AI-flag acceptance path, location-fallback, vehicle-fallback (previously sent `_build_quote_reply` as undeclared plain text — now a declared composed quote), the price re-query, the transactional renderer and the plan's `CANONICAL_PRICE` proposition (`delivers_value` = the block).
- No amount is computed by the engine or any model; PricingService rules and amounts unchanged.

### 4.2 Required-claim coverage (`services/claim_coverage.py`)
- `RequiredClaim`: SERVICE_SCOPE, REPORT, PAYMENT (incl. debit), PRICE_BREAKDOWN (revisión/viáticos/total), AVAILABILITY.
- **Detection is typed, not lexical**: this burst's interpreter FAQ topics and `QUOTE_REQUEST`, the engine's existing deterministic floors (`_explicit_faq_topics`, `_detect_price_question` + one domain lexeme `viátic`), and claims owed from earlier bursts. No sentence list.
- **Coverage is by canonical identity**: a claim is covered only when its canonical answer (FAQ constant / PricingService block) is in the final message. An AI bridge can neither cover nor suppress a claim (FAQ redundancy is now judged on the canonical segment, not the bridged text).
- **Producers**: the FAQ producer adds owed topics (re-asks are answered; scope carries the report sentence so report is not duplicated); the price producer registers the breakdown when owed (secondary lead "Te paso el detalle de la cotización:", no CTA).
- **Final check** at `_send_text_to_wa` (after composition, before the gate) and on the Booking Flow body; logged `WILD1-C COVERAGE …`.
- **Cross-turn**: a claim the final message did not cover is persisted as an owed `required_claim` row keyed to the burst's inbound WAMIDs; the next reply owes it until an outbound with a higher id carries its canonical answer.
- **Booking Flow**: the Flow is the CTA, so owed answers precede it in the same body (`_coverage_preamble`), bounded by Meta's 1024-char body; if they would not fit, the Flow goes without them, the gap is logged and the claims stay owed.
- **Routing fixes**: Layer D arms the FAQ burst when there is something to answer without waiting (explicit question, or a finished reading); Layer D no longer swallows a money question or an explicit decline after a quote; the price re-query no longer swallows a day request.
- **Latency contract preserved**: coverage never makes a terminal pre-gate or a bare greeting wait on the interpreter (W1F-R1/R2 tests pass); it may wait (bounded, memoised once per turn) only past the commercial arming point.

### 4.3 Inspection FAQ answer — authoritative source
Constants `_FAQ_SCOPE_LEAD`, `_FAQ_SCOPE_ANSWER` (= scope lead + report answer), `_FAQ_REPORT_ANSWER` in `conversation_engine.py`.

**Report timing and wording — owner ruling (R1), which supersedes the website:**
> "Al terminar la revisión, el inspector te comenta los principales hallazgos. El informe detallado se envía durante el mismo día, con los datos del vehículo, los hallazgos por sistema, fotos, una conclusión sobre el estado general y recomendaciones técnicas."

**Scope facts and report contents** come from RideCheck's published FAQ (unchanged by R1):
- `/var/www/ridecheck/faq.html` (sha256 `31bdcab4f3ce4fc7692cf545d86c9447d90b8629f4bd6a0fb43f141c64af8bfd`): "¿Qué cubre una revisión de Ride Check?" (mechanical, electrical, structure and solidity inspection; electronic scan; real-mileage verification), "¿Hacen test drive?" (road test is part of the inspection), "¿Qué incluye el informe PDF?" (vehicle data, findings by system — engine, electrical, safety —, photos of points of interest, conclusions on the general state, technical recommendations).
- Corroborated by `/var/www/ridecheck/revisiones.html` (sha256 `cf71fc14…502310`) and the legacy n8n planner knowledge block ("scanner report of all modules… real mileage… structure / solidity report").
- No item was invented. Not restated: "250 puntos de control" (digit-bearing, marketing).
- **Content inconsistency (must be corrected before launch, not edited here):** `faq.html` "¿Cuándo recibo el informe?" says the PDF is delivered "dentro de 24 a 48 horas". It contradicts the owner ruling. The live website was **not** modified in this milestone.

### 4.4 Quote decline / reconsideration
- **Interpreter** (`understand/1.22`): REJECT now means an explicit decision not to proceed; a concern or price question without refusal is HESITATE/QUESTION_ONLY. New `acceptance.decline_reason` ∈ {PRICE, AVAILABILITY, OTHER}, kept only beside REJECT.
- **Decline** (`_explicit_quote_decline` → `_handle_quote_decline`): stage QUOTED, not needs_human, lead not already PERDIDO, semantic REJECT resolved (not AMBIGUOUS/CONFLICT), no scheduling request in the burst, quote delivered (ledger proof). Writes `flag=PERDIDO`, `motivo_perdida` PRECIO (PRICE) or OTRO, appends DECLINED (producer ENGINE, idempotent per inbound). No needs_human, no operator notification, no Flow, no offer, no booking; stage stays QUOTED. Reply: `_QUOTE_DECLINE_ACK` ("Entendido, gracias por avisarnos. Si más adelante querés retomar la revisión, escribinos por acá.") plus any owed answer (e.g. the breakdown when viáticos were asked). No semantic reading → never a decline.
- **Reconsideration** (`_apply_lost_lead_rule`, inside both authorizer wrappers and the legacy branches): when progression is authorized on a PERDIDO lead whose newest decision row is the engine's DECLINED with the same motivo → append RECONSIDERED, flag back to PRESUPUESTO_ENVIADO, clear motivo; the normal path then advances to ACEPTADO / Booking Flow. Otherwise (operator PERDIDO, or motivo changed by an operator) → HOLD (`operator_lost`): never overwritten, never reactivated.
- **Quote follow-up** selects only `flag='PRESUPUESTO_ENVIADO'`, so a PERDIDO lead receives none.
- **Operator view**: kanban card shows the flag (`flag-PERDIDO`), "Motivo pérdida: PRECIO", and the history lines "Presupuesto rechazado por el cliente (PRECIO, automático) · dd/mm hh:mm" / "Presupuesto retomado por el cliente …" (`services/quote_decision_history.py`, read only, escaped once).
- **Contract** documented in the engine module docstring (transitions G/H and the narrow PERDIDO exception).

### 4.5 Flow intro copy (WILD1-C, corrected in R2)
`_booking_flow_body(time_verified=…)` — "Ese horario está disponible 🎉" only when the customer named a time and ScheduleService confirmed it (`_try_schedule_and_flow`, `time_verified=bool(preferred_time_str)`); the legacy Flow body and the website text fallback follow the same condition. R2 removed the duplicated "elegí / confirmá / confirmar" construction and every pre-persistence confirmation wording:
- no selected time (Flow-first day request): "Para mañana martes 17/11 tengo 5 horarios disponibles. Elegí el horario que te sirva y completá tus datos para solicitar el turno."
- verified selected time: "Ese horario está disponible 🎉 Completá tus datos para solicitar el turno."
- the CTA after stated availability: "…te envío el formulario de reserva para elegirlo y solicitar el turno."; Flow pending: "Te envié el formulario para solicitar el turno. Completalo con tus datos y terminamos la reserva."
- The only "turno está confirmado" text remains the BOOKED render, which requires a persisted booked revision (unchanged).

## 5. Before / after (rendered; H-1 words; scripted interpreter labels; claim-free scripted bridge)

**Before** (live H-1, candidate `aa2c8db`, from `messages.json`):

| Customer | System |
|---|---|
| 6269/6270 (voice) Tigre / Berazategui; ¿de qué consta? ¿efectivo o débito? | 6271 "Gracias por tu consulta… ¿Me contás qué vehículo querés revisar?" — no scope, no payment |
| 6273/6274 Jeep Renegade 2024; "no me contestaste" | 6275 "…es de $240.000…" + payment + thin scope — total only |
| 6276/6277 ¿de qué consta? Me parece caro, ¿cuánto hay de viático? | 6278 "Entiendo tus dudas… Gracias por tu mensaje. Cualquier otra consulta…" — nothing answered, CRM unchanged |
| 6279–6281 están en precio, ¿mañana?; ¿cuánto es de viático?; ¿qué informan? | 6282 "…es de $240.000…" — total only; day and report dropped |
| 6283 Si / 6285 Mañana | 6284 "¿Qué día…?" / 6286 Flow "…tengo 5 horarios disponibles… **Ese horario está disponible 🎉**…" |

**After** (`tests/test_wild1_c_h1_regression.py`, same customer words; the bridge line is the scripted claim-free bridge):

```
===== T1 action=replied flag=PRESUPUESTANDO stage=QUALIFYING cov={'required': ['payment', 'service_scope'], 'covered': ['payment', 'service_scope'], 'routed': [], 'missing': []}
TEXT> ¡Hola! Gracias por escribirnos.

Para poder avanzar necesito confirmar algunos datos. ¿Me contás qué vehículo querés revisar?

Aceptamos efectivo, transferencia bancaria y Mercado Pago. Con débito no estamos trabajando por el momento. Vamos hasta donde está el vehículo y hacemos la revisión pre-compra en el lugar: inspección mecánica, eléctrica, de estructura y solidez, prueba de marcha, escaneo electrónico profesional y verificación del kilometraje real. Al terminar la revisión, el inspector te comenta los principales hallazgos. El informe detallado se envía durante el mismo día, con los datos del vehículo, los hallazgos por sistema, fotos, una conclusión sobre el estado general y recomendaciones técnicas.

===== T2 action=replied flag=PRESUPUESTO_ENVIADO stage=QUOTED cov={'required': [], 'covered': [], 'routed': [], 'missing': []}
TEXT> ¡Hola! Gracias por escribirnos.

Genial! Te paso la cotización para la revisión del Jeep Renegade 2024 en Berazategui:

Revisión: $150.000
Viáticos: $90.000
Total: $240.000

Si te parece bien, podemos avanzar.

===== T3 action=replied flag=PRESUPUESTO_ENVIADO stage=QUOTED cov={'required': ['price_breakdown', 'service_scope'], 'covered': ['price_breakdown', 'service_scope'], 'routed': [], 'missing': []}
TEXT> Genial! Te paso la cotización para la revisión del Jeep Renegade 2024 en Berazategui:

Revisión: $150.000
Viáticos: $90.000
Total: $240.000

Si te parece bien, podemos avanzar.

Vamos hasta donde está el vehículo y hacemos la revisión pre-compra en el lugar: inspección mecánica, eléctrica, de estructura y solidez, prueba de marcha, escaneo electrónico profesional y verificación del kilometraje real. Al terminar la revisión, el inspector te comenta los principales hallazgos. El informe detallado se envía durante el mismo día, con los datos del vehículo, los hallazgos por sistema, fotos, una conclusión sobre el estado general y recomendaciones técnicas.

===== T4 action=flow_button_sent flag=ACEPTADO stage=SCHEDULING cov={'required': ['availability', 'price_breakdown', 'report'], 'covered': ['price_breakdown', 'report'], 'routed': ['availability'], 'missing': []}
FLOW> Te paso el detalle de la cotización:

Revisión: $150.000
Viáticos: $90.000
Total: $240.000

Al terminar la revisión, el inspector te comenta los principales hallazgos. El informe detallado se envía durante el mismo día, con los datos del vehículo, los hallazgos por sistema, fotos, una conclusión sobre el estado general y recomendaciones técnicas.

Para mañana martes 17/11 tengo 5 horarios disponibles. Elegí el horario que te sirva y completá tus datos para solicitar el turno.

===== T5 action=replied flag=ACEPTADO stage=SCHEDULING cov={'required': [], 'covered': [], 'routed': [], 'missing': []}
TEXT> Te envié el formulario para solicitar el turno. Completalo con tus datos y terminamos la reserva.

===== T6 action=replied flag=ACEPTADO stage=SCHEDULING cov={'required': ['availability'], 'covered': [], 'routed': ['availability'], 'missing': []}
TEXT> Te envié el formulario para solicitar el turno. Completalo con tus datos y terminamos la reserva.
```

Decline examples (focused tests):
```
Customer: no la hago, es caro. ¿cuánto era el viático igual?   (REJECT, PRICE, QUOTE_REQUEST)
→ Entendido, gracias por avisarnos. Si más adelante querés retomar la revisión, escribinos por acá.

  Te paso el detalle de la cotización:

  Revisión: $150.000
  Viáticos: $90.000
  Total: $240.000
  CRM: flag PERDIDO, motivo PRECIO, history [DECLINED PRECIO ENGINE]
Customer: bueno dale, avancemos                                  (ACCEPT)
→ ¡Perfecto! ¿Qué día y horario te viene mejor para la revisión?
  CRM: flag ACEPTADO, stage SCHEDULING, history [DECLINED, RECONSIDERED]
```
Natural bridge with owed answer (scripted generator "¡Buena pregunta! Te cuento.", verifier clean): the bridge opens the message, the canonical report answer follows it; a bridge that *mentions* the report or *looks like* the presence answer never suppresses the canonical one (tests).

## 6. CRM state transitions

| From | Event | To | History |
|---|---|---|---|
| PRESUPUESTO_ENVIADO / QUOTED | price question, breakdown request, concern without refusal | unchanged | none |
| PRESUPUESTO_ENVIADO / QUOTED | explicit refusal (REJECT, resolved, no day request) | **PERDIDO** + motivo PRECIO/OTRO, stage QUOTED, needs_human false | DECLINED (ENGINE) |
| PERDIDO (engine) | explicit acceptance / authorized day request | PRESUPUESTO_ENVIADO → ACEPTADO / SCHEDULING (→ Flow) | + RECONSIDERED (ENGINE); DECLINED kept |
| PERDIDO (operator, or motivo changed) | any | unchanged (progression HELD) | none |
| PERDIDO | quote follow-up job | never selected | — |

## 7. Tests

### 7.1 New (63 tests, 13 subtests)
`tests/test_wild1_c_h1_regression.py` (13) and `tests/test_wild1_c_conversation_correction.py` (50, incl. 3 R1 `ReportTimingRuling` and 3 R2 Flow-copy guards), harness `tests/_wild1c_harness.py` (live H-1 posture; real route; interpreter scripted at its transport seam so parser/admission/persistence run).

| Required | Test(s) |
|---|---|
| 1 H-1 sequence, 6272 excluded | `H1Replay` (6 bursts) + `H1BookingCompletes` |
| 2 scope + payment first message | `test_t1_…`, `test_multi_question_first_message_scope_and_payment` |
| 3 scope repeatedly | `test_t3_…`, `test_scope_asked_three_times_is_answered_each_time` |
| 4 viáticos + rejection | `test_refusal_with_viaticos_question_answers_it` |
| 5 / 6 nonzero / zero viáticos | `test_first_quote_nonzero_viaticos`, `test_first_quote_zero_viaticos`, unit blocks |
| 7 arithmetic | `test_arithmetic_must_reconcile`, `PriceClaimValidation` (total-only, mismatch, swapped, unrendered refused), `test_all_amounts_reconcile` |
| 8 repeated quote | `test_repeated_quote_retains_breakdown`, `test_t3_…` |
| 9 rejection → CRM state/history | `test_explicit_price_refusal_marks_perdido_precio`, `…_otro`, kanban tests |
| 10 not needs_human | same + `test_no_operator_notification_and_no_handoff` |
| 11 reconsideration | `test_explicit_acceptance_reactivates_engine_decline`, `test_day_request_after_decline_reactivates_and_opens_the_flow` |
| 12 no Flow / booking at rejection | `test_explicit_price_refusal_marks_perdido_precio` |
| 13 all questions covered | `coverage.complete` asserted per turn |
| 14 generator omits a claim | `test_bridge_omitting_or_mentioning_a_claim_never_covers_it`, `test_bridge_that_looks_like_an_answer_does_not_suppress_it`, `test_bridge_with_a_false_payment_claim_is_rejected` |
| 15 paraphrase / voice | `test_reordered_and_paraphrased_questions` (3 incl. unpunctuated audio), `test_paraphrased_viaticos_reaches_breakdown_semantically` |
| 16 "Ese horario" / R2 no confirmation before persistence | `test_t4_no_premature_ese_horario`, `FlowIntroCopy` (verified time keeps it; R2: exact variants, source guard, H-1 Flow-first sentence) |
| 17 Booking Flow / persistence | `H1BookingCompletes` (Data Exchange confirm books once, one ledger row); full booking suites in §7.2 |
| 18 motorcycle handoff | `test_r5_f1_motorcycle_handoff_production_path.py` in the full run (unchanged) |
| owner: concern vs refusal | `test_price_concern_with_question_is_not_a_decline`, `test_breakdown_request_is_not_a_decline`, `test_concern_without_refusal_is_not_a_decline`, `test_ambiguous_reject_is_not_a_decline`, `test_reject_beside_a_day_request_is_not_a_decline`, `test_no_semantic_reading_never_declines` |
| owner: operator PERDIDO | `test_operator_perdido_is_never_reactivated`, `test_operator_changed_motivo_is_not_reversed` |
| owner: follow-up stopped | `test_decline_stops_the_quote_followup` |
| cross-turn ledger | `test_unanswered_question_is_owed_on_the_next_turn` |

### 7.2 Full SQLite suite (same runner as R1B: `ridecheck-crm-test:l4.7b2`, `--network none`, read-only tree, one process)
| Tree | Cases | Passed | Failing | Skipped | Errors | Time |
|---|---|---|---|---|---|---|
| base `aa2c8db` (`Z/base`) | 8 798 | 7 923 | 89 (378 incl. subtests) | 786 | 9 | 21 m 08 s |
| candidate (`Z/cand3`) | 8 852 | 7 977 | 89 (378) | 786 | 9 | 20 m 21 s |

`cmp.py junit_base junit_cand3`: **NEW 0, GONE 0, COMMON 89**, 54 cases added (the WILD1-C suites), 0 removed. COMMON_changed 12 — all in `test_m20_6d2_customer_reality` (pre-existing failures pinning pre-R6 prices/copy; only the quoted text inside their message changed). The 89 common failures include the known R1A-O3 clock-skew set (T04/P04) and are identical on base.

Two intermediate full runs are kept for transparency: `cand1` (NEW 64 → classified in §7.3 and two real defects fixed: terminal pre-gates waiting on the interpreter; Layer D waiting on "buenas"), `cand2` (NEW 35 → a coverage helper not total on minimal states; fixed). `cand4`/`cand5` differ from `cand3` only in the two WILD1-C test files (3 tests added, 1 assertion strengthened); 57/57 pass on SQLite and PostgreSQL.

### 7.3 Expectation edits to existing tests (each annotated `WILD1-C` in place)
All edits keep the test's intent; none removes a check against false confirmation, generated state, duplicate Flow or the handoff.
- **Prompt-version / freeze pins (moved by this milestone):** 22 one-line `understand/1.20 → 1.22` assertions in 20 suites; `INTERPRETER_SHA256` (`test_l4_7b_2b`); SB1 frozen blobs for `semantic_interpreter.py`, `response_composition.py`, `response_validator.py` (previous blobs recorded in place).
- **Total-only quote fixtures → structured quote:** `test_l3_dirty_history` (3 calls), `test_l4_7w5_g3_6_bounded_correction` / `test_l4_7w5_g3_7_reply_renderer_demotion` (bench quote = base + 15 000 viáticos; ordinary-quote primaries built by the production composer; copy casing), `test_r6_structured_response_contract` (PRICE claims are 3-tuples rendered as the block; **3 new refusal cases**: a reconciling-but-wrong breakdown, a total-only declaration of the right amount, swapped components).
- **Copy pins:** `_QUOTE` prefix (`test_r6_c4a_transactional_provenance`), report literal (`test_wild04r_f3_faq_preservation`), `test_l4_7w1_f4` primary = the canonical constant; `test_r6_c4b P06` "price once" = the block once (with viáticos $0 the amount is on two lines).
- **Superseded rules (owner requirement):** `test_m20_6b1` asserted "no viáticos breakdown" — inverted to require Revisión / Viáticos / Total. `test_l4_7w5_f1 FAQ-DEDUP-02` — a paraphrase in the primary no longer counts as the answer; asserts the canonical answer exactly once (both with paraphrase and with the canonical primary).
- **Inventories / structure:** `test_r5_f1` inventory +3 FULL constants (each then validated by the real CE validator under every focus); `test_l4_7c_3b cut_06` legacy-branch source pin (now passes the operator-PERDIDO guard); `test_l4_7w5_g3_1` reader count 9 → 11 with the two WILD1-C readers named and pinned.

### 7.4 PostgreSQL
Runner `Z/pgstage.sh` (the R6-C4B stage, adapted): disposable `postgres:16` on tmpfs, `--internal w1c-net` (no route to live), schema templates per tree from the pre-schema record + `alembic stamp` + `upgrade head`, fresh clone per suite, base and candidate both run. Torn down: 0 containers, 0 networks.
- 29 PG suites (`pg.list`): base 713 / cand 713 cases, **NEW 0, GONE 0**, COMMON 2 (pre-existing).
- SB2B with clean DB: **NEW 0, GONE 0**, COMMON 1 (pre-existing).
- Production-path suites on PG, base vs candidate: c4bvg, c4app, c4pp, c3pp, c2pp, c1pp — **NEW 0, GONE 0** each (identical failure counts; the PG counts include the known clock-dependent fixtures).
- WILD1-C suites on PG (candidate): **57 passed, 0 failed**; dialect check: 31 evidence rows incl. 14 `quote_decision`, 1 `required_claim` written on PostgreSQL (JSONB `source_message_ids`).
- R5-F1 production path on PG: 26 ran, 0 failures.

### 7.5 Mutations
`Z/mut/run.sh` + `mutants.py`: each mutant one exact-string change applied to a throwaway copy (applies exactly once), WILD1-C suites run read-only.
- Round 1 (`results_round1.txt`): 18 killed, 5 survived (M01, M08, M09, M11 — test gaps; M10 not applied after a refactor).
- Tests added for each gap: total-only refusal finding asserted (M01); bridge that looks like the presence answer (M08); Layer D answers an untracked FAQ (M09); refusal beside a Layer-D-shaped FAQ (M11); M10 target string updated.
- Round 2 on `cand4`: **23/24 killed** (M11 survived); round 3: M11 with the Layer-D-shaped decline wording — candidate passes, mutant fails → **KILLED**. Final: **24/24 killed**.

| Mutant | Boundary |
|---|---|
| M01–M05 | breakdown contract: total-only accepted, block not required, arithmetic, canonical equality, total-only copy |
| M06–M11 | coverage/routing: viáticos floor, owed topics, bridge redundancy, Layer D arming, re-query precedence, Layer D swallow |
| M12–M15, M20, M21, M24 | decline: HESITATE as decline, ambiguous REJECT, operator PERDIDO reactivated, motivo equality, reason ignored, no history, needs_human |
| M16, M17 | Flow copy / preamble |
| M18, M19, M22 | cross-turn ledger, owed persistence, identity coverage |
| M23 | kanban history |

## 8. Proof that booking / Flow authority did not change
- `git diff --quiet aa2c8db --` over `booking_*.py`, `offer_lifecycle.py`, `flow_contract.py`, `outbound_safety_gate.py`, `pricing.py`, `repositories/`, `models.py`, `data/`, `migrations/`, `meta/`, `api/`, `routes/`: **no diff**.
- No WILD1-C function calls `_send_booking_flow(`, `_dispatch_booking_flow_for_day(` or `.issue(`; the only Flow change is the body string and the preamble passed to the existing `_send_flow_button`.
- The decline writes no offer, token, revision or ledger row (asserted). Reconsideration only restores the flag; the Flow is still dispatched by the existing ScheduleService/authorizer path, and booking is still `BookingAuthority` via the Flow (H-1 completion test: 1 booked revision, 1 ledger row, none before the Data Exchange confirm).
- Availability ≠ booking and confirmation-after-persisted-success tests (R6-C4A/C4B P02–P04, T01–T06, SB2B, convergence) unchanged in outcome (§7.2).
- Motorcycle/quad/UTV handoff path untouched (no diff on those branches; production-path suite passes).

## 9. Known limitations
- **L-1 Real-provider behaviour not measured.** The interpreter prompt changed (`understand/1.22`: REJECT definition + `decline_reason`). All evidence uses scripted payloads; how gpt-4o-mini separates "me parece caro" (HESITATE) from "es caro, no la hago" (REJECT) is unmeasured. A false REJECT would write PERDIDO (reversible by the customer's explicit reconsideration, visible to the operator); a missed REJECT leaves the lead PRESUPUESTO_ENVIADO (status quo).
- **L-2 Semantic-only questions on Layer D when the reading is not yet finished.** To keep "buenas" free of model waits (W1F-R2), Layer D answers semantic-only topics only if the interpretation already finished; otherwise the explicit floor answers (payment, presence, hours, report literals). Scope has no literal floor. In H-1 the reading was finished (admission had waited for the locality).
- **L-3 Report timing — resolved in the engine by owner ruling (R1), open on the website.** The canonical answer now promises the detailed report the same day, after the on-site explanation of the principal findings. The public `faq.html` still says "24 a 48 horas": a pre-launch content correction outside this milestone. Until it is corrected, a customer may read two different promises. The legacy reply-model system prompt text in `_build_ai_messages` (item 17 "SERVICIO — QUÉ INCLUYE") is not customer-facing since R6-C4B (the body is discarded) and was not changed.
- **L-4 Operator-vs-engine PERDIDO.** If an operator re-marks an engine-declined lead PERDIDO with the SAME motivo, the engine cannot tell (no lead update timestamp); an explicit customer reconsideration would then reactivate it. Re-marking with a different motivo, or any operator PERDIDO without an engine DECLINED row, is protected (tested).
- **L-5 Cycle reset** (pre-existing) still clears `flag`/`motivo_perdida` for a new inquiry cycle, including PERDIDO.
- **L-6 Re-ask policy.** A tracked topic the interpreter labels in the current burst is answered even if answered earlier in the cycle (the H-1 requirement). If the interpreter mislabels an unrelated burst, the answer repeats.
- **L-7 Ordering.** Owed FAQ answers follow the primary (e.g. after "¿Me contás qué vehículo…?") — plan order unchanged.
- **L-8 Copy for owner review:** `_QUOTE_DECLINE_ACK`, `_COVERAGE_PRICE_LEAD` and the quote lead "Te paso la cotización para…:". The report answer (R1) and the Flow / booking-request copy (R2) are owner-ruled. Unchanged and outside R2's forbidden list: the no-Flow-ID misconfiguration fallback "Perfecto, tenemos disponibilidad. ¡Ya te confirmo!" (a promise of a later confirmation, not a claim of one) and the human handoff "…Julián… te confirma el turno por acá" (a future human action).
- **L-9 Clock-dependent fixtures** (pre-existing R1A-O3, T04/P04) fail identically on base.
- **L-10 H-1 viáticos.** The live total $240.000 = SUV_4X4_DEPORTIVO base 150.000 (`pricing_base.csv`) + 90.000 viáticos inferred for Berazategui in the live `viaticos_zones` table (the repo CSV seed says 60.000; the live DB was not read). Fixtures use 90.000.

## 10. R1 — report-timing alignment

**Change (production):** `_FAQ_REPORT_ANSWER` only — from "Al finalizar la revisión te enviamos un informe detallado con los datos del vehículo, los hallazgos por sistema (motor, eléctrico, seguridad, etc.), fotos de los puntos de interés, una conclusión sobre el estado general y recomendaciones técnicas." to the owner wording (§4.3). `_FAQ_SCOPE_ANSWER` follows by construction; `_FAQ_SCOPE_LEAD` (the inspection-scope facts) is byte-identical.

**Diff proof** (`diff Z/cand5/backend Z/r1/cand/backend`): exactly two hunks in `conversation_engine.py` — the comment above the constant (records the ruling and the website inconsistency) and the constant's four string lines. No other backend file differs. Pricing, coverage routing, decline state, Flow, booking and persistence code are byte-identical to the reviewed WILD1-C candidate.

**Test expectations derived from the report copy:** `test_wild04r_f3_faq_preservation._REPORT_ANSWER` (literal) updated. Every other suite reads the constant. `test_messy_turn_reconciliation._REPORT_ANSWER` is a pre-WILD1-C literal that is defined but never used — left unchanged. Added `ReportTimingRuling` (exact owner wording; no "24", "48", "hora", "inmediat", "en el momento", "PDF"; "durante el mismo día" present; scope lead unchanged and carried by the scope answer).

**Runs (copy-only correction; full SQLite suite, full PG matrix and mutation campaign not repeated, per the R1 prompt):**
- SQLite, 19 suites — both WILD1-C suites and every FAQ/copy suite that uses the report/scope answers (`test_wild04r_f3_faq_preservation`, `test_l4_7w1_f4_pre_wild_closure`, `test_l4_7w5_f1_faq_authority_observability`, `test_l4_7w5_f3_booking_closure_faq_dedup`, `test_l4_7w5_faq_hours_truth`, `…_r2`, `test_r5_f1_template_validation_contract` (validates the new constant through the real CE validator under every focus), `test_minimum_launch`, `test_messy_turn_reconciliation`, `test_l4_7w5_g3_7h_prerequisites`, `test_l4_7w3_f1_semantic_authority_flow_first`, `test_l4_7w2_f1_voice_location_recovery`, `test_l4_7w4_f1_booking_flow_data_exchange`, `test_wild04r_f5_1_required_location_gate`, `test_r6_full_engine_prose_corpus`, `test_l4_7w5_g3_6_proposition_migration`, `test_wild04r_f2_reset_faq`): **598 passed, 0 failed**.
- PostgreSQL (`Z/r1/pg_w1c.sh`: disposable `postgres:16`, tmpfs, `--internal` network, schema at `20261104_vehicle_identity`): both WILD1-C suites **60 passed, 0 failed**; 14 `quote_decision` and 1 `required_claim` rows; 16 delivered outbound messages carry "durante el mismo día". Torn down: 0 containers, 0 networks.

## 11. R2 — Flow / booking-request copy

**Change (production, copy only — `diff Z/r1/cand/backend Z/r2/cand/backend`: `conversation_engine.py` only):**

| Site | R1 | R2 |
|---|---|---|
| `_booking_flow_body(time_verified=False)` | "Para confirmar el turno, elegí el horario y completá tus datos." | "Elegí el horario que te sirva y completá tus datos para solicitar el turno." |
| `_booking_flow_body(time_verified=True)` | "Ese horario está disponible 🎉 Para confirmar el turno, elegí el horario y completá tus datos." | "Ese horario está disponible 🎉 Completá tus datos para solicitar el turno." |
| Flow-first prefix (`_dispatch_booking_flow_for_day`) | "Para {día} tengo N horarios disponibles. Elegí el que te sirva y confirmá el turno acá:" | "Para {día} tengo N horarios disponibles." |
| `_TX_FLOW_CTA_TEXT` | "…para elegirlo y confirmar el turno." | "…para elegirlo y solicitar el turno." |
| `_TX_FLOW_PENDING_TEXT` | "Te envié el formulario para confirmar el turno. …" | "Te envié el formulario para solicitar el turno. …" |
| legacy data-collection Flow body | "…Para confirmar el turno, completá el formulario con tus datos." | "…Completá el formulario con tus datos para solicitar el turno." |
| website-lead text fallback | "…Para confirmar el turno necesito dos datos más: …" | "…Para solicitar el turno necesito dos datos más: …" |
| AI-confirmation scrub replacement | "Para confirmar el turno necesitamos que completes el formulario…" | "Para solicitar el turno necesitamos que completes el formulario…" |

The hunks are string literals, one docstring and two comments. In `_booking_flow_body` the shared `lead` variable became two return branches with the same `time_verified` condition. Flow dispatch, slot lists (the five real options are still listed and counted), the Flow button (`cta_label="Reservar turno"`), the Meta Flow JSON (`meta/` untouched), availability, booking, persistence, pricing, coverage and decline logic are byte-identical to R1.

**Tests:** exact-copy expectations updated: `test_l4_7d_response_validator` (the Flow body is not a booking claim), `test_r6_c4_default_deny` (corpus line = the copy as sent), and `test_r6_c4a_transactional_provenance` (FLOW_PENDING). `test_m19_r1_2_pg_concurrency` uses an arbitrary dedup string, not engine copy, so it is unchanged. New guards in `FlowIntroCopy`:
- `test_flow_body_variants_request_never_confirm`: both body variants exactly, and the CTA and pending texts, checked against "confirmad / confirmar el turno / confirmá el turno / confirmamos / quedó agendado / te esperamos / reservado" and the engine's own `_SCHEDULING_FORBIDDEN_PHRASES`, plus the validator's booking-claim detector;
- `test_source_guard_pre_persistence_flow_copy`: an AST scan of every customer string literal in `_booking_flow_body`, `_send_booking_flow`, `_dispatch_booking_flow_for_day` and `_try_schedule_and_flow`, excluding docstrings and log calls; it also checks the button label is unchanged;
- `test_h1_flow_first_body_is_the_required_sentence`.

H-1 T4 and the verified-time test assert the exact required sentences.

**Runs (copy-only; full SQLite, full PG matrix and mutation campaign not repeated):**
- **SQLite, 32 suites, R1 vs R2 (same runner):** both WILD1-C suites; Flow-intro/copy (`test_l4_7d_response_validator`, `test_r5_f1_template_validation_contract`, `test_l4_7w3_f1_semantic_authority_flow_first`, `test_l4_7w5_f3_booking_closure_faq_dedup`, `test_r6_c4_default_deny`, `…_production_path`, `test_r6_c4b_verified_generation`, R1A/R1B); booking authority and confirmation-after-persistence (`test_r6_c4a_transactional_provenance`, `test_sb1_*`, `test_sb2a_*` (7), `test_sb2b_option_b`, `test_convergence_*` (7), `test_l4_7w4_f1_booking_flow_data_exchange`, `test_m21_3_c_d_booking_flow`, `test_wild1_booking_flow_gate_and_contract`, `test_l4_7w5_g3_7p_offer_authority`).
  - R1: 847 cases, 840 passed, 7 failing. R2: 850 cases, 843 passed, 7 failing.
  - **NEW 0, GONE 0, COMMON 7, COMMON_changed 0**; the 3 added cases are the R2 guards.
  - The 7 common failures (P02 ×2, P04, P05, T04 ×3) also fail on the original base `aa2c8db`. They are the known clock-dependent "lunes 16/11" fixtures (R1A-O3 / L-9).
- **PostgreSQL (`Z/r2/pg_w1c.sh`, disposable, internal network):** both WILD1-C suites, **63 passed, 0 failed**; 9 delivered outbound rows carry "para solicitar el turno". Torn down: 0 containers, 0 networks.

## 12. Verdict
**READY FOR ONE FOCUSED INDEPENDENT REVIEW**

Recommended review focus: (1) L-1 — whether a bounded real-provider check of `understand/1.22` REJECT vs HESITATE is required before WILD; (2) the narrow PERDIDO relaxation and L-4; (3) the Layer D arming rule (L-2) against the W1F-R2 no-wait contract; (4) owner copy review (L-8).

Not performed: stage, commit, push, build, deploy, migration, Meta change, tester contact, WILD reopen.

STOP
