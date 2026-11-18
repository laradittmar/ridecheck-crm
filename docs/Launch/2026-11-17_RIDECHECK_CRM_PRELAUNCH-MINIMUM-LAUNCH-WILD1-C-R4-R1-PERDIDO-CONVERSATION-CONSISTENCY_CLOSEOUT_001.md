PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-WILD1-C-R4-R1-PERDIDO-CONVERSATION-CONSISTENCY

**VERDICT: READY FOR FOCUSED R4 INDEPENDENT REVIEW**

- **Date.** 2026-11-17 (work ran 18:25Z–19:45Z).
- **Worktree / branch.** `/opt/ridecheck-crm-wild1-correction`, `fix/prelaunch-wild1-conversation-correction`.
- **Base.** `aa2c8db08d4e3bf6a1a70acdc48b7d23f4077008` (HEAD unchanged; index empty).
- **Tree state.** The R4-R1 delta is uncommitted, on top of the uncommitted R2/R3/R4 deltas. Nothing was staged, committed, pushed, built or deployed. Meta was not touched. No tester was reset or contacted. No WILD was run. The independent review has not been started.
- **Launch gate.** L4 (ACTIVE). This is a bounded revision of R4, not a new gate.
- **Evidence.** `/opt/ridecheck-builds/crm-wild1-c-r4-r1-20261117T182550Z/` (`$E5` below). R4's evidence `$E4` = `/opt/ridecheck-builds/crm-wild1-c-r4-20261117T142430Z/` is unchanged.

## 0. Inputs read and verified

| Input | sha256 |
|---|---|
| R4 closeout | `08e996ff734300c922c3aaa7bbeeb73d6002e8095cad8e9b7568f3c7a77330fe` (byte-identical to `$E4/` copy) |
| R3 independent review | `3af608c1635ac3e6b938e175262293bea667d34c523953c136a1f60041df5607` (as recorded in R4 §0) |
| R4 candidate manifest | `$E4/cand_manifest.sha256` → worktree `sha256sum -c` OK before any edit; porcelain identical to `$E4/r4_porcelain_end.txt` |

No other Claude session had this worktree as its cwd.

## 1. Owner ruling — booking after operator PERDIDO (R4 §13, option a)

**Recorded ruling (owner, 2026-11-17).** A valid, booking-shaped Meta Flow submission for an ISSUED offer is honoured and persisted by `BookingAuthority` even if an operator previously marked the lead PERDIDO. This is a narrow, explicit exception:

- conversational acceptance does **not** override an operator's PERDIDO;
- vehicle/location fallback submissions do **not** override it;
- ordinary text with vehicle/location does **not** override it;
- **only** a successful, persisted Booking Flow completion may project the lead to ACEPTADO.

`BookingAuthority` and the offer lifecycle are **not modified**. The existing behaviour is pinned by `BookingAfterOperatorPerdido` (§6.4). The guard's booking-shaped exclusion (R4 §6.3) is what makes the exception possible.

## 2. Exact corrections

| ID | Defect (R4 code) | Correction (R4-R1) |
|---|---|---|
| **R4-D1** | An operator-held PERDIDO lead could reach the deterministic quote force, the transactional renderer, Layer D's qualification prompt, the website form and the clarification paths on the text path. The flush guard kept the flag, but the customer got a quote and CTA, or a qualification question, and the stage moved to QUOTED. | `_process_text` now holds **before any commercial step**: after the three terminal pre-gates (motorcycle, phone, service boundary) and before admission, Layer D, the website form and every quote/re-quote/clarification/scheduling renderer, `if self._operator_lost_lead(ctx): return self._handle_perdido_hold(ctx, state)`. The hold is **exclusive**: `_register_response_propositions` now skips **every** producer on a hold turn (R4 still let the FAQ producer run), so no price, FAQ, clarification or pending acknowledgement joins the copy. The hold runs before `_turn_continues` is set, so the turn is terminal: it never waits on the model and never writes a pending row. |
| **R4-D2** | (a) Every PERDIDO fallback form got the manual-review copy, including after the engine's own decline. (b) **Found in R4-R1:** in QUOTED, the scheduling authorizer (`_authorize_scheduling_progression`, which applies the lost-lead rule, appends RECONSIDERED and restores the quote flag) ran on **every** QUOTED turn before any day was parsed. So "perdón, en realidad es un Toyota Corolla 2018", or a bare zone, *reconsidered an engine decline* (probe §4: R4 shows `[DECLINED, RECONSIDERED]` for vehicle+zone, vehicle-only, zone-only and correction). | (a) `_hold_perdido_fallback` chooses the copy by origin: engine decline gets "Recibí los datos. Si querés retomar la revisión, decime por acá y continuamos."; operator gets the manual-review copy. (b) The QUOTED block now parses the day/time request first and calls the authorizer **only when the burst carries one** (`sched_day_iso or sched_requests`). Vehicle or location data alone is never a reconsideration; an explicit ACCEPT or a day request still is. |
| **C (motorcycle)** | A motorcycle discovered on a PERDIDO lead got either the contact-data Flow (text path) or the generic hold (fallback path), which erased the classification and set no human-handoff state. | `_motorcycle_human_handoff(..., held=True)` for any PERDIDO lead (text Layer A and the vehicle fallback form): the **existing plain-text branch** (`needs_human`/`necesita_humano`, the existing `_send_fallback_human_review_notification`, approved `_MOTORCYCLE_HANDOFF_REPLY`), no Flow, no token. The MOTO candidate is still recorded. Flag and motivo are never written. |
| **Terminal-turn ack** | An earlier pending question's ack ("si me escribís nuevamente, la retomo") could ride on a terminal or human-owned reply (phone handoff, motorcycle text). | `_turn_owes_pending_ack` returns False when the turn's state is `TERMINAL_OR_PRE_GATED`. The row stays PENDING for a later continuing turn. |

No new notification architecture, no BookingAuthority/offer-lifecycle/pricing/Flow/prompt/model change, and no migration.

## 3. Manifest

R4-R1 delta against the frozen R4 candidate (`$E4/cand`): **3 paths**.

| Path | Δ vs R4 | sha256 | git blob |
|---|---|---|---|
| `backend/app/services/conversation_engine.py` | +77 / −14 | `685c2bfa8579b1239efe694f611380be5353948389a0b00cf56a7b5764a0e311` | `87e6c00f17d01f88ce24d200d826bcefe36481f4` |
| `tests/test_r5_f1_template_validation_contract.py` | +1 / −0 (`_PERDIDO_FALLBACK_DATA_REPLY: FULL`) | `bd7a468894cb7fe5d45ed2790753d19728bbbd67878ebf264cbf204a58449c19` | `9df1ea46f64795b8011a1661df83514ead92fe37` |
| `tests/test_wild1_c_r4_r1_perdido_consistency.py` | new (417 lines, 17 tests) | `023fcbc30f855d5878c79a191aa75a9613046065db79a8a805fe4bba72c43bdd` | `ec7bc8b88233684875a7b2cd7799fbaa5fbe0f69` |

The full candidate is 53 code/test paths against base. `$E5/cand/` is a full-repository copy, byte-identical on all 53: **`sha256($E5/cand_manifest.sha256)` = `d9423a962087bcaed6f4f901364b84587314ae13a1726d32532c2d94249f8ca1`**.

Byte-identical to R4: `claim_coverage.py`, `semantic_turn_evidence.py`, `semantic_interpreter.py`, `response_validator.py`, every other R4 path.

## 4. Operator PERDIDO versus engine decline — state table

Real route, `$E5/probes/out_r4.md` (R4) → `out_r4r1.md` (R4-R1). "held" means: flag/motivo unchanged, stage unchanged, 0 Flows, 0 offers, 0 bookings, no RECONSIDERED.

| Entry | Operator PERDIDO — R4 | **Operator PERDIDO — R4-R1** | Engine decline — R4 | **Engine decline — R4-R1** |
|---|---|---|---|---|
| text: vehicle + zone (QUALIFYING) | quote + CTA, stage → QUOTED | **hold copy**, QUALIFYING | n/a | n/a |
| text: vehicle + zone / vehicle / zone (QUOTED) | quote + CTA | **hold copy** | **RECONSIDERED** → PRESUPUESTO_ENVIADO, quote + CTA | **PERDIDO/PRECIO**, `[DECLINED]`; quote restated (§9 R1-D1) |
| text: price request | QUALIFYING: vehicle question; QUOTED: quote + CTA | **hold copy** | quote, PERDIDO | quote, PERDIDO (unchanged) |
| text: "dale, avancemos" | QUALIFYING: intro question; QUOTED: hold copy | **hold copy** | RECONSIDERED → ACEPTADO/SCHEDULING | **same** (explicit reconsideration) |
| text: day/time | QUALIFYING: intro question; QUOTED: hold copy | **hold copy** | RECONSIDERED → ACEPTADO/SCHEDULING | **same** |
| text: vehicle correction | QUALIFYING: generic handoff (`needs_human`); QUOTED: quote + CTA | **hold copy** | **RECONSIDERED** + quote | **PERDIDO/PRECIO**, `[DECLINED]` |
| text: motorcycle | contact-data Flow | **plain-text moto handoff**, `needs_human`, MOTO candidate | contact-data Flow | **plain-text moto handoff** |
| form: vehicle / location fallback | hold copy | hold copy | hold copy (manual review) | **"Recibí los datos…"**, `[DECLINED]` |
| form: motorcycle fallback | generic hold, no handoff | **plain-text moto handoff**, `needs_human` | generic hold | **plain-text moto handoff** |

Every R4-R1 operator row: `PERDIDO/<motivo>`, stage unchanged, `flows=0`, `offers/booked=0/0`, no RECONSIDERED, and no "Total:", "podemos avanzar", "¿Qué día" or ack.

## 5. Entry-path table (text / fallback / booking-shaped)

| Entry path | Router | PERDIDO protection | Result on an operator PERDIDO |
|---|---|---|---|
| text, motorcycle/quad/UTV | Layer A pre-gate | held handoff (`held=True`) | `needs_human`, MOTO candidate, approved handoff text, no Flow |
| text, phone request | Layer B pre-gate | terminal, unchanged | existing phone handoff (human) |
| text, unsupported service | Layer C pre-gate | terminal, unchanged | boundary statement |
| text, anything else | `_process_text` early hold | **exclusive hold** before admission / Layer D / website form / renderers | hold copy only |
| Flow response, motorcycle contact token | `_process_motorcycle_contact_response` | guard armed; no flag writer | unchanged |
| Flow response, `tipo_vehiculo` / `zona_general` | `_hold_perdido_fallback` | held before either handler | operator: hold copy; engine decline: data copy; moto: held handoff |
| Flow response, booking-shaped (ISSUED offer token) | `_converged_flow_book` → `BookingAuthority` | **owner exception** (guard excluded) | booked, ACEPTADO, receipt after persistence |
| Flow response, booking-shaped but invalid / withdrawn token | admission refuses | no flag writer reached | `booking_refused_handoff`, PERDIDO kept |
| Flow response with fallback keys **and** a valid token | `_is_booking_shaped_submission` → False | fallback hold | hold copy, offer stays ISSUED, no booking |

## 6. Tests

### 6.1 New suite `tests/test_wild1_c_r4_r1_perdido_consistency.py` (17 tests)

| Prompt item | Test |
|---|---|
| 1–6, 14, 15 | `test_every_text_case_is_held_on_both_paths`: 6 cases × {QUALIFYING, QUOTED} × {authority on, legacy}. Each asserts `r.sent == [HOLD]`, no Flow, no price/CTA/clarification/ack, flag/motivo/stage/decisions/notices unchanged, 0 offers/token/booking. `test_vehicle_and_zone_in_qualifying_no_longer_quotes` also asserts no admission zone write. |
| 7, 8 | `test_fallback_after_operator_perdido_gets_the_manual_review_copy`, `test_text_after_a_fallback_form_is_held` |
| 9 | `test_fallback_after_engine_decline_gets_the_neutral_data_copy` (no "manualmente", `[DECLINED]`) |
| 10 | `test_explicit_textual_reconsideration_still_reactivates`, `test_vehicle_information_alone_is_not_a_reconsideration` |
| 11 | `test_text_motorcycle_on_operator_perdido` (moto, cuatriciclo, UTV), `test_motorcycle_fallback_form_on_operator_and_engine_perdido` |
| 12 | `test_valid_submission_is_honoured_and_idempotent` (CE route: `booking_created`, 1 booking, 1 ledger row, offer ACCEPTED, ACEPTADO, receipt sent once post-persistence; a duplicate delivery and a replay with a **different payload** book nothing more and send nothing); `test_data_exchange_confirm_is_honoured_too` |
| 13 | `test_invalid_token_never_overrides_perdido`, `test_withdrawn_offer_never_overrides_perdido`, `test_fallback_payload_cannot_masquerade_as_booking_shaped` |
| other | `test_an_earlier_pending_question_is_not_acknowledged_on_a_held_turn` (held turn < 2 s, no ack), `test_held_under_the_kill_switch`, `test_phone_request_after_a_pending_question` (terminal turn, provider still down: no ack, row kept PENDING) |

### 6.2 SQLite (runner `ridecheck-crm-test:l4.7b2`, `--network none`, read-only mounts, ≤ 1.8 GB)

| Run | Result |
|---|---|
| R4-R1 + R4 + R3 + WILD1-C sync/live + template inventory (`$E5/runs/suites.xml`) | **346 passed / 0 failed**; R4 matrix 56/56 (max T1 4.67 s); R3 matrix 40/40 |
| R4-R1 suite on the final tree (`a6`) | **17 / 0** |
| Focused 55-file set (R4's 54 + R4-R1 suite) | **2,095 passed, 0 failing, 12 skipped** |
| — vs base (`$E4/runs/base.xml`) | NEW 0 / GONE 0 / COMMON_changed 0 |
| — vs R4 (`$E4/runs/r4.xml`) | NEW 0 / GONE 0; one case "removed" = R4's own superseded outage test (split in R4 v3) |
| Semantic extra set (19 suites) | 646 passed / 1 failing — identical to R4 (NEW 0); the common failure is the pre-existing hard-coded-date `test_caseC_requested_day` |

The suites and focused runs used candidate v1. The engine is byte-identical to the final candidate (v4); only the R4-R1 test file changed after v1 (one terminal-turn test sharpened, §7), and it was re-run on the final tree.

### 6.3 PostgreSQL 16 (disposable `w1cr4r1-pg`, tmpfs, `--internal`, schema = pre-schema record + stamp + `upgrade head` → `20261104_vehicle_identity`)

**315 passed / 0 failed** on the final candidate: WILD1-C sync 63, live-async 64, R3 74, R4 97, R4-R1 17. That includes the booking characterization on real separate connections. Dialect counts:
- `quote_decision` 138 (operator 76);
- duplicate `(thread_id, claim_id)` **0**;
- `validated_as` `jsonb`.

Torn down by exact name; 0 `w1cr4r1-*` containers or networks remain.

### 6.4 Booking characterization (owner ruling)

On an operator-PERDIDO/PRECIO lead with an ISSUED offer:
- **The CE Flow-response route.** It returns `booking_created`, with exactly 1 booked ThreadRevision and 1 `BookingCommandRecord`. The offer is ACCEPTED, the lead flag is ACEPTADO, and the only outbound is `("BOOKING_FLOW", "sent", <receipt>)`. A redelivery gives `booking_replayed` or `skipped_dedup`. A replay with a different payload does not create a booking. Neither sends anything.
- **The Data Exchange confirm.** It also books, on a different day so the two never contend for the one launch professional.
- **Refused submissions.** An unknown token, or an offer withdrawn first, gives 0 bookings and the lead stays PERDIDO/PRECIO.
- **A fallback payload carrying a valid token.** It is not booking-shaped, so it is held; the offer stays ISSUED.

## 7. Final-tree mutation results

Method: same as R4.
- Each mutant is exact-string hunks, each applied once to a throwaway copy of `$E5/cand`, then compiled; a mutant that doesn't compile counts as INVALID, not killed.
- Test group: the R4-R1, R4 and R3 suites, WILD1-C sync, template inventory and the structured contract, with the matrices limited to c1/q1.
- KILLED means a failing test identity absent from the control.
- Control: 254 tests, 0 failing.

**15 / 15 KILLED** (`$E5/mut/results_pass1.txt`, `results_pass2.txt`).

| ID | Mutant | Killed by (one of) |
|---|---|---|
| R1-01 | early operator hold removed | 4, e.g. `test_every_text_case_is_held_on_both_paths` |
| R1-02 | hold bypassed when the burst names a vehicle (**deterministic quote force**) | `test_vehicle_and_zone_in_qualifying_no_longer_quotes` + 2 |
| R1-03 | hold bypassed after a quote (**transactional / re-query renderer**) | `test_every_text_case_is_held_on_both_paths` |
| R1-04 | engine-declined fallback uses the operator/manual-review copy | `test_fallback_after_engine_decline_gets_the_neutral_data_copy` |
| R1-05 | fallback information alone appends RECONSIDERED | 2 |
| R1-06 | QUOTED scheduling authorizer runs eagerly | `test_vehicle_information_alone_is_not_a_reconsideration` |
| R1-07 | ordinary fallback classified booking-shaped | `test_fallback_payload_cannot_masquerade_as_booking_shaped` |
| R1-08 | booking-shaped submission held too (owner exception violated) | `test_valid_submission_is_honoured_and_idempotent` |
| R1-09 | held motorcycle takes the Flow branch | 2 motorcycle tests |
| R1-10 | motorcycle fallback swallowed by the generic hold | `test_motorcycle_fallback_form_on_operator_and_engine_perdido` |
| R1-11 | held motorcycle loses its classification | 2 motorcycle tests |
| R1-12 | hold not exclusive | 4 |
| R1-13 | terminal-ack check removed (as first written, this also removed the state evaluation) | 2 (pass 2) |
| R1-13b | terminal-ack suppression removed, state still evaluated | `test_phone_request_after_a_pending_question` (pass 2) |
| R1-14 | an engine decline held like an operator's | 9, e.g. `test_explicit_acceptance_reactivates_engine_decline` |

In pass 1, R1-13 was killed only by `test_clarification_flow_body_carries_the_acknowledgement`. Its `False and …` hunk also skipped the state evaluation, so that kill duplicated R4-02. The terminal-turn test did not discriminate either, because the earlier re-read happened to resolve.
- Fix: the test now keeps the provider down on the terminal turn, and R1-13b was added.
- Pass 2 (control 254, 0 failing) killed both, with the intended test.

## 8. Rendered copies

| Situation | Copy |
|---|---|
| Operator-held PERDIDO, any non-terminal text or fallback form | "Gracias. Para retomar esta consulta, nuestro equipo tiene que revisarla manualmente." (alone) |
| Fallback form after an engine decline | "Recibí los datos. Si querés retomar la revisión, decime por acá y continuamos." (alone) — new constant `_PERDIDO_FALLBACK_DATA_REPLY`, inventory FULL |
| Motorcycle/quad/UTV on a PERDIDO lead | "Las motos las revisamos de forma manual. Un asesor de Ridecheck se va a contactar con vos para continuar." (existing approved template; the existing review notification runs) |
| Booking after operator PERDIDO | the existing receipt "Gracias por completarlo. Un representante de RideCheck se estará comunicando con vos." |
| Invalid booking submission | the existing "No pudimos confirmar el turno. Un asesor se contacta a la brevedad." |

## 9. Protected-path proof and live check

- **Unchanged against base.** `git diff --stat aa2c8db` is empty over:
  - `backend/alembic`, `models.py`, `settings.py`, `routes/`, `repositories/`;
  - `pricing.py`, `outbound_safety_gate.py`, `offer_lifecycle.py`, `flow_contract.py`, `booking_*` (BookingAuthority untouched);
  - `meta/`, `data/`, `docker-compose*.yml`.
- **No `*.csv`/`*.xlsx` path touched.**
- **Unchanged against R4.** `semantic_interpreter.py` (model `gpt-4o-mini`, prompt), `claim_coverage.py` and `semantic_turn_evidence.py` are byte-identical to R4. The deadline and retention design is untouched apart from the two mechanical integration points in §2.
- **Live read at 2026-11-17T19:06:40Z** (`docker inspect`, names and values only):
  - backend image `sha256:4952d5d5…6903`, `GIT_SHA=aa2c8db…`;
  - `OUTBOUND`, `SMART_BOOKING_CE`, `OPERATOR_WHATSAPP_NOTIFY`, `BOOKING_FLOW_DETAILS_PREFILL`, `CONVERSATION_ENGINE_DIRECT_WEBHOOK`, `EVIDENCE_ADMISSION`, `OUTSTANDING_QUESTION` and `LOCALITY_RESOLUTION_SAFETY` are all `false`, unchanged.

## 10. Remaining debt

| ID | Item | Classification |
|---|---|---|
| R1-D1 | **Engine-declined lead + text with vehicle/zone or a price request.** It keeps PERDIDO and no RECONSIDERED is added, but the restated quote still ends "Si te parece bien, podemos avanzar." The customer can reconsider with "dale". The prompt required no hold here. | owner copy decision |
| R1-D2 | **Owner-exception booking leaves `motivo_perdida` set.** After a booking on an operator-PERDIDO/PRECIO lead, the lead is `ACEPTADO/PRECIO` (probe §4): `BookingAuthority`'s projection writes only the flag. Clearing it needs a BookingAuthority/projection change, which is out of scope. | owner decision / later booking milestone |
| R1-D3 | **The exclusive hold answers no FAQ** (e.g. "¿qué revisan?") on an operator-held lead. The burst is not retained as pending, because the hold turn is terminal; the copy tells the customer the team will review. | owner review |
| R1-D4 | **A terminal turn can resolve an earlier pending row** when its re-read has already finished (R3 rule, unchanged). The resolved claims become owed for a later continuing turn. | accepted |
| R4-D3…D6 and carried items | unchanged from the R4 closeout §12 (R4-D1/R4-D2 are closed here) | as before |

## 11. Exact next authorized step

The next step is the **focused R4 independent review**, now covering R4 plus this R4-R1 revision. It should use:
- the candidate `$E5/cand` (manifest sha256 `d9423a96…f8ca1`);
- the R4 closeout and this closeout;
- `$E4/mut` + `$E5/mut` (`run.sh control`, `run.sh only <idx…>`);
- `$E4/pg/pg_r4.sh` + `$E5/pg/pg_r4r1.sh`;
- `$E4/probes` + `$E5/probes`.

It should look specifically at:
- the early hold's placement after the three terminal pre-gates;
- the scheduling-authorizer reordering (§2, R4-D2 b);
- the booking-shaped exclusion under the owner ruling;
- R1-D1 to R1-D3.

No commit, push, build, kit update, deployment, Meta change, tester contact or WILD comes before that review's verdict.

**VERDICT: READY FOR FOCUSED R4 INDEPENDENT REVIEW**
