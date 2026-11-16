PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C4B-VERIFIED-GENERATIVE-OUTPUT

Date: 2026-11-14 (UTC)
Worktree: `/opt/ridecheck-crm-r5-f1-response-validation` (branch `fix/prelaunch-r5-f1-response-validation-boundary`, HEAD `f16b1f3`). This builds on the R6-C4A candidate; no new worktree was created.
Evidence: `Y=/opt/ridecheck-builds/crm-r6-c4b-verified-gen-20261114T032904Z`
Baseline: the sealed and unchanged R6-C4A results in `X=/opt/ridecheck-builds/crm-r6-c4a-provenance-20261113T200937Z`. The baseline was not rerun; the only exception is the same-day recheck of NEW ids in §8.
Nothing was staged, committed, pushed, built, finalized, deployed, migrated, enabled or sent. W-3 remains OPEN. The final WILD remains NOT RUN.

---

## 0. Seal (final, round 2)

| Item | Value |
|---|---|
| worktree == sealed `Y/cand` | yes |
| cand manifest (741 files) | `f62240b857179c1b9d5f9b222840fa4c6c500156fd5e3fd1d31200f6cfb7389b` |
| tree pin (689 files, excludes docs/Launch) | `a1735dbe64470028faaa6ba8edeb501f085da762327b353da31cbadd251a1531` |
| app pin (`backend/app`, 139 files) | `79e784a974266481c158e0271d2d72eee25eb9db0792a62c3886f272ed00db89` |
| files changed vs R6-C4A seal | 17 (3 production, 13 test, 1 doc) |
| `response_validator.py` blob (SB1 sentinel) | `d4421ab2682423564d7caa044b272edf160573b3`, matching the sentinel in `tests/test_sb1_booking_representation.py:360` |

What round 2 changed, and what it inherits from round 1:
- Round 1 (tree `a4933e2e…`) ran the mutants, focused SQLite, full SQLite and PostgreSQL.
- Full SQLite found 3 genuine NEW failures, all stale expectations (§8).
- Round 2 changes only two test files, so the **app pin is identical** across the rounds.
- Neither of those files is in the PG list or the mutant suites, so the mutant and PG results carry over unchanged.
- The two edited files were rerun on the round-2 seal and on `X/cand`: 92/92 each, NEW 0.

## 1. Final architecture

```
customer turn
  └─ interpreter call (_call_openai → _reply_decision → _apply_transactional_boundary)
        extracted fields kept (minus model-read schedule fields, R6-C4A);
        decision["reply"] := None on EVERY route  (body kept only in _discarded_ai_reply for the tripwire)
  └─ deterministic routing / ScheduleService / quote / CRM state (unchanged owners of every fact)
  └─ route label  _vg_route_label
        QUALIFYING · GENERAL_INFORMATION · QUOTED(QUOTATION) · SCHEDULING  → verified generation
        BOOKED · FLOW_PENDING · needs_human                                → R6-C4A deterministic renderer (owner decision)
  └─ _build_fact_ledger  (canonical facts only; FactSource never = reply model)
  └─ run_verified_generation(ledger, context, generate, verify, budget)
        1. GENERATOR   (ledger + allowed actions + forbidden kinds + recent msgs) → {message, declared_claims}
        2. VERIFIER    (ONLY the message + claim schema + business date/weekday)  → {claims_found, uncertain}
        3. COMPARATOR  compare(ledger, declared, verifier) — deterministic, no prose parsing
             any mismatch → ONE regeneration (with previous_attempt_rejected) → else fallback
        timeouts / errors / malformed → fallback, no retry
  └─ VerifiedGeneratedText._mint(...)   only on APPROVED
  └─ defence-in-depth: validate_envelope(VERIFIED_GENERATED) — R6-C1..C4 analysis unchanged
        refusal OR any edit → DEFENCE_IN_DEPTH_REFUSED → fallback
  └─ send (OutboundSafetyGate unchanged); tripwire refuses any plain str containing the discarded
     interpreter body or any rejected generated draft
```

Authorization comes from three things together: the canonical ledger, the independent verifier's claims and the deterministic comparator. Only the provenance type `VerifiedGeneratedText` can carry an approved message to the send path. The C4 temporal vocabulary and phrase corpus are **unchanged** and run only as defence-in-depth after authorization. They can refuse text, but they never authorize it.

## 2. Provenance and construction rules

- **`VerifiedGeneratedText(str)`** (`backend/app/services/verified_generation.py`)
  - `__new__` raises; the only constructor is `_mint(_MINT, …)`, which `run_verified_generation` calls on APPROVED.
  - The value is immutable and refuses pickle, copy and deepcopy; `is_verified()` is the type gate.
  - Mutant V11 bypasses the type gate and is killed.
- **Ledger rules**
  - `build_ledger` raises on a duplicate ref.
  - It also raises when a fact's kind is in the forbidden set.
  - Facts come from 8 `FactSource`s (vehicle, zone, quote, ScheduleService, booking state, handoff state, payment policy, scope/hours). The reply model is not a source.
  - PRICE is present only on QUOTATION and SCHEDULING, and `required` on the first quote.
  - AVAILABILITY_DATE is present only with a current ScheduleService evaluation.
  - BOOKING_DATE and BOOKING_TIME are always forbidden on generated routes.
- **Comparator mismatch codes**
  - UNSUPPORTED_CLAIM, FORBIDDEN_KIND, UNDECLARED_CLAIM, DECLARED_NOT_FOUND, BAD_FACT_REF, REQUIRED_MISSING, UNCERTAIN, INCONSISTENT_VERIFIER.
  - Any one of them rejects the attempt. Verifier uncertainty is a rejection, never a pass.
- **Verifier isolation:** `verifier_messages` sends only the message, the schema and the business date and weekday. It sends no ledger, no declarations and no customer text (U07; mutant V03 is killed).
- **State:** nothing is written from generated prose. There is no preferred time, no revision and no offer from text (P-suite "no state from prose"; V09 is killed).
- **Interpreter body:** it is discarded on every route; V17 keeps it and is killed. The send tripwire also refuses rejected drafts.
- **Raw text:** a plain `str` can never be labelled VERIFIED_GENERATED (V18 is killed).

## 3. Route coverage

| Route | Writer | Fallback | Evidence |
|---|---|---|---|
| QUALIFYING | verified generation | validator fallback (VALIDATION_FALLBACK) | ex. 1, 2 |
| GENERAL_INFORMATION / FAQ | verified generation (answer_source FAQ_RULE path) | validator fallback | ex. 3, 4, 5, 13 |
| QUOTED (QUOTATION) | verified generation; first quote PRICE `required` | composed quote / R6-C4A renderer | ex. 6, 7, 10, 11 |
| SCHEDULING | verified generation | R6-C4A transactional renderer | ex. 8, 9, 12 |
| BOOKED | R6-C4A deterministic text + human handoff (owner decision) | — | ex. 14 (0 generator calls) |
| FLOW_PENDING | deterministic (owner clarification) | — | P-suite, V16 killed |

Scheduling offers themselves (concrete slot lists, Flow dispatch) are still produced by the ScheduleService handlers and the BOOKING_FLOW. Generated SCHEDULING text states slots only when it restates a current evaluation (ex. 7 and 9) or on the FAQ route with an evaluation present.

## 4. Production and test changes

**Production (3 files):**
- `backend/app/services/verified_generation.py` (new, 656 lines; pure, no I/O) contains:
  - claim kinds, fact sources and formats, and `normalize_value` (format only);
  - the ledger, strict parsers and comparator;
  - the provenance type, prompts and their versions;
  - the budget, `FallbackReason`, `Outcome` and `run_verified_generation`.
- `backend/app/services/response_validator.py`:
  - adds `ResponseProvenance.VERIFIED_GENERATED`, which gets the full defence-in-depth analysis;
  - adds `VerificationFallbackText`, which maps to `_fallback_envelope` with the refusal findings.
- `backend/app/services/conversation_engine.py`:
  - the body is discarded on all routes, and the tripwire also covers rejected drafts;
  - new methods `_vg_route_label`, `_build_fact_ledger`, `_vg_context`, `_vg_fallback_value`, `_send_generated_reply`, `_send_declared`, `_call_generator`, `_call_verifier` and `_call_provider_json`;
  - provider calls use an explicit urlopen timeout and `forbid_external_io` (temperature 0.5 for the generator, 0.0 for the verifier); the verifier model setting is optional;
  - the `_DELAY_LINE` engine literals are wrapped in `composed_text`;
  - the commercial tail and the FAQ route are rewired;
  - **the `_scrub_invented_price` and `_scrub_scheduling_confirmation` call sites were removed from the tail**, because model prose no longer reaches it. The functions still exist.

**Tests:**
- New `tests/_c4b_model_fakes.py`, the **one shared adapter** for the historical fakes, installed from `tests/conftest.py`.
- New `tests/test_r6_c4b_verified_generation.py` (1,002 lines, 45 tests / 141 subtests): U01–U07, P01–P08 in 4 postures, S01–S02.
- Expectation edits in 11 existing files (the list is in the seal's `c4a_to_c4b.diff.txt`). Each one changes delivered provenance AI_COMPOSED → VERIFIED_GENERATED, or "body held in `_discarded_ai_reply`, reply None", or empties the BUG-2 upstream-guard list. Round 2 added:
  - `test_l4_7w5_g3_5e2…::test_g35f_13b`: reply None, body held;
  - `test_wild04r_phase2_observability::OBS-04/04b`: answer_source VERIFIED_GENERATED. OBS-04 previously skipped its check when the value was None; it now asserts it.

**90-minute timebox (owner clarification):** it started at 03:20Z, and the shared adapter was working at about 03:50Z. No per-test rewrite of the historical fakes was needed.
**Disclosure:** the adapter's `replay_verify` is a parser-based test stand-in that uses `analyze_temporal` and `$` amounts. It lets about 85 historical suites keep asserting what they always asserted. **Production authorization does not use it.** It is the independent LLM verifier plus the comparator, and the U-suite ("authorization never consults the parser") and the P-suite ("novel language with the parser disabled") prove, with parser-free scripted stubs, that authorization never consults the parser.

## 5. Natural response examples (`Y/probe2/style.py` on the sealed tree, read-only mount, `--network none`, scripted generator/verifier, business date Wed 11 Nov 2026 12:00 ART)

Full JSON per example: `Y/probe2/style.out`. All 7 hours facts are in each ledger: MON 13:00-18:00, TUE 09:30-14:00, WED 09:00-18:00, THU 09:00-14:00, FRI 09:00-18:00, SAT 09:00-15:00, SUN CLOSED. Payment and scope facts are also always present. Each row below shows only the facts used.

| # | Route | Customer | Sent (verbatim) | Ledger facts used | Verifier claims | Comparator | Provenance |
|---|---|---|---|---|---|---|---|
| 1 | QUALIFYING | "Dale, gracias por responder" | ¡Genial! Un Toyota Corolla es una linda compra. Para pasarte el valor exacto, ¿en qué barrio o localidad está el auto? | vehicle=VEHICLE:toyota\|corolla; forbidden PRICE, slots, dates | VEHICLE toyota\|corolla | APPROVED | VERIFIED_GENERATED |
| 2 | QUALIFYING | same | ¡De nada! Contame cuando puedas en qué zona lo tenés así coordinamos la visita. | — | none | APPROVED | VERIFIED_GENERATED |
| 3 | FAQ | "Hola, ¿cómo funciona el servicio?" | Vamos nosotros hasta donde está el auto y le hacemos la revisión pre-compra ahí mismo; vos no hace falta que estés. Al terminar te mandamos el informe. | 4 scope facts | 4 × SERVICE_SCOPE | APPROVED | VERIFIED_GENERATED |
| 4 | FAQ | same | Es muy simple: coordinamos y vamos hasta el auto. Podés pagar en efectivo, por transferencia o con Mercado Pago; con débito por ahora no trabajamos.⏎⏎¿En qué localidad o barrio está el auto? | pay_* ×4, scope_on_site | ON_SITE + 4 × PAYMENT_METHOD | APPROVED | VERIFIED_GENERATED |
| 5 | FAQ | same | Sí, coordinamos de lunes en adelante; los lunes por ejemplo atendemos de 13:00 a 18:00. ¿Querés que veamos un día para la revisión?⏎⏎¿En qué localidad o barrio está el auto? | hours_mon=MON 13:00-18:00 | BUSINESS_HOURS MON 13:00-18:00 | APPROVED | VERIFIED_GENERATED |
| 6 | QUOTED | "Perfecto, gracias. Lo voy a pensar y te aviso" | Dale, sin apuro. Te recuerdo que la revisión del Corolla en Palermo te queda en $140.000; cuando quieras seguimos. | price_total=140000, vehicle, zone_detail=palermo | PRICE 140000, VEHICLE, LOCATION palermo | APPROVED | VERIFIED_GENERATED |
| 7 | QUOTED (evaluation present) | same | Perfecto, pensalo tranquilo. Si te sirve, el lunes 16 todavía tengo lugar a las 10:00 o a las 11:00. | date_1=2026-11-16, slot_1=10:00, slot_2=11:00 | AVAILABILITY_DATE 2026-11-16, SLOT 10:00, 11:00 | APPROVED | VERIFIED_GENERATED |
| 8 | SCHEDULING (no evaluation) | same | ¡Buenísimo! ¿Qué día y en qué horario te queda cómodo? Los lunes, por ejemplo, atendemos de 13:00 a 18:00. | hours_mon; AVAILABILITY_DATE forbidden | BUSINESS_HOURS MON 13:00-18:00 | APPROVED | VERIFIED_GENERATED |
| 9 | SCHEDULING (evaluated) | "Consulta, ¿hay lugar el lunes 16 a las 10?" | ¡Sí! El lunes 16 a las 10:00 está libre. Si te viene bien, avanzamos con eso. | date_1=2026-11-16, slot_1=10:00 | DATE 2026-11-16, SLOT 10:00 | APPROVED | VERIFIED_GENERATED |
| 10 | first quote | "Es un Toyota Corolla 2018, está en Palermo" | ¡Listo! Para el Toyota Corolla en Palermo, la revisión pre-compra completa sale $140.000. ¿Te parece bien así avanzamos? | price_total=140000 **(required)**, vehicle, palermo, scope_pre_purchase | PRICE, VEHICLE, LOCATION, SCOPE | APPROVED | VERIFIED_GENERATED |
| 11 | QUOTED (regeneration) | "…lo voy a pensar…" | draft 1 "Dale. Igual te cuento que sale unos 150 mil, más o menos." → **rejected**; sent: Dale, sin problema. El valor exacto es $140.000, cuando quieras lo coordinamos. | price_total=140000 | draft 1: PRICE 150000 + uncertain "unos … más o menos"; draft 2: PRICE 140000 | [BAD_FACT_REF, UNCERTAIN, UNSUPPORTED_CLAIM] → [APPROVED] | VERIFIED_GENERATED |
| 12 | **fallback** SCHEDULING | "…lo voy a pensar…" | draft ×2 "¡Listo! Te dejo reservado el martes 17 a las 9." → **both rejected**; sent: ¿Qué día y horario te viene mejor para la revisión? | BOOKING_STATUS=NOT_BOOKED; AVAILABILITY_DATE forbidden | BOOKING_STATUS CONFIRMED, DATE 2026-11-17, SLOT 09:00 | [BAD_FACT_REF, FORBIDDEN_KIND, UNDECLARED_CLAIM, UNSUPPORTED_CLAIM] ×2 → REJECTED_TWICE | DETERMINISTIC_COMPOSED (R6-C4A renderer) |
| 13 | **fallback** FAQ | "Hola, ¿cómo funciona el servicio?" | draft ×2 "Revisamos más de 250 puntos y te damos garantía de un año." → rejected; sent: Para poder avanzar necesito confirmar algunos datos. ¿En qué localidad o barrio está el auto? | no warranty or "250 points" fact | SERVICE_SCOPE OTHER + uncertain "garantía de un año" | [UNCERTAIN, UNDECLARED_CLAIM, UNSUPPORTED_CLAIM] ×2 → REJECTED_TWICE | VALIDATION_FALLBACK |
| 14 | BOOKED (deterministic) | "…lo voy a pensar…" | Tu turno está confirmado para el lunes 16/11 a las 10:00.⏎⏎Para no equivocarme con tu caso, lo sigue un asesor de RideCheck. Te contactamos a la brevedad. | — | — | generator calls = 0 | DETERMINISTIC_COMPOSED, needs_human=true |

In every example, state after the turn is preferred_time=null, revisions=0 and offers=0: nothing was written from prose.

The suffix "¿En qué localidad o barrio está el auto?" in ex. 4 and 5 is the engine's own composed outstanding-question line (W1F-R2), appended after the verified text. It is not generated. The verifier claims shown above cover the generated part only, and the combined text then passes defence-in-depth.

These examples were scripted by the author to show style and the pipeline's mechanics. They are evidence, not templates, and they make **no claim about real-provider phrasing quality**.

## 6. Retry and fallback behaviour

- **Comparator rejection:** one regeneration with `previous_attempt_rejected`. A second rejection gives REJECTED_TWICE and the fallback. `max_attempts` is clamped to at most 2; V08 (unlimited retries) is killed.
- **Generator or verifier timeout, error or malformed output:** fallback with no retry. Treating a timeout as success (V10) is killed.
- **Budget:** generator 8 s, verifier 6 s, total 20 s, min_call 1 s. A call that cannot get min_call gives BUDGET_EXHAUSTED.
- **Ledger build error:** gives LEDGER_ERROR and the fallback.
- **Defence-in-depth refusal or edit:** gives DEFENCE_IN_DEPTH_REFUSED and the fallback. V15 (removing it) is killed.
- **No API key:** a generator error, then the fallback (P07).
- **Fallback targets:** on transactional routes, the R6-C4A `_send_transactional_reply`; on QUALIFYING and FAQ, the validator's own fallback envelope; for a first quote, the composed quote.
- **Observability:** one summary log line per turn (route, attempt results, fallback reason, latency) with no customer text, no full phone and no secrets (P-suite observability test).

## 7. Mutations (`Y/mut/results.txt`; 18 ≤ 20; run on the round-1 seal with the same app pin)

The control run passed: 129 tests / 664 subtests.

| Mutant | Change | Result |
|---|---|---|
| V01 | bypass the `_mint` factory | KILLED |
| V02 | trust generator declarations | KILLED |
| V03 | verifier receives declarations | KILLED |
| V04 | ignore a verifier claim | KILLED (7) |
| V05 | accept an unauthorized claim | KILLED (4) |
| V06 | accept uncertainty | KILLED |
| V07 | send the first rejected draft | KILLED |
| V08 | unlimited retries | KILLED |
| V09 | write state from prose | KILLED (16) |
| V10 | treat a timeout as success | KILLED (2) |
| V11 | bypass the type gate / tripwire | KILLED |
| V12 | ignore REQUIRED_MISSING | KILLED |
| V13 | ignore DECLARED_NOT_FOUND | KILLED |
| V14 | ignore UNDECLARED_CLAIM | KILLED (3) |
| V15 | remove defence-in-depth | KILLED (4) |
| V16 | generate on BOOKED | KILLED (8) |
| V17 | keep the interpreter body | KILLED |
| V18 | raw text gets VERIFIED_GENERATED | KILLED |

**18 / 18 killed.**

## 8. SQLite results (in the sandbox `c4b-sq`, `--network none`, TZ America/Argentina/Buenos_Aires)

**Focused** (78 files plus the c4b suite) vs the sealed C4A `focused_cand.xml`:
- C4A: 2,998 cases, 2,620 passed, 17 failing.
- C4B: 3,045 cases, 2,666 passed, 18 failing.
- **NEW 1, GONE 0**, COMMON 17 unchanged; 47 cases added, 0 removed; skipped unchanged.

**Full** (one run, as specified) vs the sealed C4A `full_cand.xml`:
- C4A: 8,740 cases, 7,884 passed, 70 failing.
- C4B: 8,787 cases, 7,927 passed, 74 failing.
- NEW 4, GONE 0, COMMON 70 unchanged; 47 cases added.

The NEW ids were run the same day on the unchanged `X/cand`:

| NEW id | on `X/cand` today | Disposition |
|---|---|---|
| `test_r6_c1_claim_frames::S01_ConcreteWindows::test_s01_02_general_hours_remain_information` | **fails identically** (5 subfailures, all worlds) | calendar-dependent (business date Sat 14 Nov), pre-existing, not C4B; disclosed |
| `test_l4_7w5_g3_5e2…::test_g35f_13b_a_substituted_provider_cannot_present_as_bound` | passes | stale expectation (body now discarded on every route), fixed in round 2 |
| `test_wild04r_phase2_observability::test_obs_04…` | passes | stale expectation (answer_source now VERIFIED_GENERATED), fixed in round 2 |
| `test_wild04r_phase2_observability::test_obs_04b…` | passes | same, fixed in round 2 |

Round 2 rerun of the two edited files: on the seal and on `X/cand`, 92 / 35 subtests each, NEW 0 GONE 0.
**Net full SQLite vs C4A: NEW 1, the date-dependent one that fails identically on C4A today. Real NEW 0, GONE 0.**

## 9. Relevant PostgreSQL results (`Y/pgstage.txt`; candidate only, compared with the sealed C4A PG results)

Setup:
- disposable `postgres:16` on tmpfs, on an `--internal` network `c4b-net`, `TimeZone=Etc/UTC`;
- the candidate template is at head `20261104_vehicle_identity`;
- **schema (columns + constraints) equals the C4A tree's template: yes.**

Results:
- **29 PG suites, one fresh clone per suite:** 713 cases, 598 passed, 2 failing, against C4A's 713/598/2. **NEW 0, GONE 0**, COMMON 2 unchanged (one in `convergence_p2_authority_pg`, one in `g37r_rv03_tester_reset_pg`; both pre-existing).
- **SB2B with its clean DB:** 48/46/1 against 48/46/1. **NEW 0, GONE 0.**
- **Production-path suites on PG, vs C4A** (all NEW 0, GONE 0):

| Suite | Result |
|---|---|
| c4bvg (new) | 45 passed / 141 subtests |
| c4app | 33/33 (C4A had 31; 2 added) |
| c4pp | 64/64, 1,798 subtests |
| c3pp | 68/68 |
| c2pp | 67/67 |
| c1pp (+ corpus + contract) | 72/72, 1,471 subtests |

- PG torn down: 0 containers, 0 networks.

## 10. Performance evidence (stubbed only; no real-provider claim)

- Budgets per turn:
  - generator 8 s, verifier 6 s, total 20 s;
  - at most 2 attempts, so **up to 4 extra provider calls** per generated turn;
  - all bounded by the 20 s total, after which the deterministic fallback is sent.
- P08 measured pipeline latency with instant stubs (`R6C4B-PERF`, round-2 seal):

| Case | pipeline latency_ms | wall ms (whole engine turn, SQLite) | generator calls | verifier calls |
|---|---|---|---|---|
| success | 0 | 395.9 (first-turn warm-up) | 1 | 1 |
| regen-success | 1 | 217.9 | 2 | 2 |
| fallback | 1 | 211.1 | 2 | 2 |

- **Real provider latency was not measured.** No provider was contacted in any automated test. Production latency will be dominated by the 2–4 extra model calls; this is a residual (§11).

## 11. Residual risks

1. **Real model behaviour is unmeasured.** Phrasing quality, verifier recall and the fallback rate under the real provider have not been measured. The design fails closed: verifier uncertainty or a miss caught by defence-in-depth gives the fallback. A verifier that misses a claim *and* defence-in-depth missing it too would let a wrong fact through, so defence-in-depth stays mandatory.
2. **Latency and cost:** up to 4 extra calls per generated turn, bounded at 20 s. Real latency must be measured before any WILD.
3. **Availability cost of defence-in-depth:** it can refuse correct generated text (false positives in the unchanged C4 vocabulary). Those turns get the deterministic fallback, which is safe but less natural.
4. **Test stand-in:** the historical suites authorize through a parser-based verifier stand-in (§4). Their passing proves wiring and expectations, not real-verifier quality.
5. **Removed scrub call sites:** `_scrub_invented_price` and `_scrub_scheduling_confirmation` no longer run in the tail. Their protection is replaced by ledger + verifier + comparator + defence-in-depth (mutants V04/V05/V15 are killed).
6. **Engine-appended lines:** the W1F-R2 outstanding question (e.g. INSPECTION_LOCATION) is appended after verified text and is attributed to the reply model in `asked_by` (REPLY_MODEL for VERIFIED_GENERATED). This attribution is carried over and was not re-examined.
7. **Scheduling slots:** generated SCHEDULING text states slots only as restatements of a current evaluation or on the FAQ route. ScheduleService handlers and the BOOKING_FLOW still own offers.
8. **Calendar-dependent test:** `test_s01_02_general_hours_remain_information` fails on both trees on Saturday business dates (pre-existing).
9. **Kit:** the final-WILD kit is **not re-pinned** to this candidate. R6-C4 seal-2 (`359062f4…`) still verifies, but it pins the R6-C4 tree, not C4B. Re-pinning, the kit chain and the rehearsal were out of scope ("no kit chain or rehearsal yet").

## 12. Live isolation

- `Y/live/before.txt` (about 04:50Z) and `Y/live/after.txt` (after all runs) are **identical except `worktree_status_sha16`**. The only cause is the round-2 test-file edit in the worktree.
- In both probes:
  - `OUTBOUND_ENABLED=false`;
  - `db_fingerprint=fbae2abdc3371f9146f0ac51a2ffa427`;
  - HEAD `f16b1f3`; staged = 0;
  - every kit checksum verifies OK.
- The live system was probed read-only only: no live container, database, n8n or tester phone was touched.
- Every test container ran with `--network none`, or on the `--internal` `c4b-net` for PG.
- After the runs: 0 `c4b-*` containers and 0 `c4b-*` networks. No host-scope prune was used.
- R6-C4 kit seal-2: `CHECKSUMS.sha256` sha256 `359062f49e8c2f3b…`, and `sha256sum -c` gives OK.
- No PII, full phone numbers, secrets or credentials appear in logs, evidence or this document. No provider was contacted.

## 13. Verdict

Conditions:
- [x] The LLM writes the replies on QUALIFYING, GENERAL_INFORMATION/FAQ, QUOTED and SCHEDULING, and canonical services own every fact.
- [x] An independent verifier call (message + date only) and a deterministic comparator authorize sending; generated prose is never parsed to authorize anything.
- [x] `VerifiedGeneratedText` is unforgeable, and the interpreter body is discarded on every route.
- [x] One regeneration, then fallback; timeouts and errors fall back without retry; the budget is bounded.
- [x] BOOKED and FLOW_PENDING are deterministic, per the owner clarification.
- [x] The C4 vocabulary and corpus are unchanged and serve as defence-in-depth only.
- [x] There is one shared fake adapter, delivered inside the timebox.
- [x] 18/18 mutants are killed.
- [x] Full SQLite has real NEW 0 / GONE 0; the one calendar-dependent NEW fails identically on C4A today and is disclosed.
- [x] Relevant PG has NEW 0 / GONE 0, and the schema is equal.
- [x] There are 14 examples with ledgers, verifier claims, comparator results and two fallbacks.
- [x] Live isolation holds, there are no leftovers, staged = 0, and nothing was sent.

**VERDICT: READY FOR FINAL INDEPENDENT GROUNDED-GENERATION REVIEW**

If the independent review does not accept this architecture, no R6-C4C and no phrase correction will be created automatically. An explicit owner decision is required.

W-3 remains OPEN. The final WILD remains NOT RUN.

STOP
