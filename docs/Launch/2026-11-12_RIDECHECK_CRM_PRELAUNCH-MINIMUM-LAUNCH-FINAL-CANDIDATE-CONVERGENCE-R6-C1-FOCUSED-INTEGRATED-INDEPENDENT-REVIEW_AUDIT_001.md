PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C1-FOCUSED-INTEGRATED-INDEPENDENT-REVIEW
# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — FINAL CANDIDATE CONVERGENCE R6-C1 — FOCUSED INTEGRATED INDEPENDENT REVIEW — AUDIT 001

Date: 2026-11-12 (review runs 00:57Z–03:10Z)
Reviewer: fresh independent reviewer. I did not implement R6 or R6-C1.
Source candidate: `/opt/ridecheck-crm-r5-f1-response-validation`, branch `fix/prelaunch-r5-f1-response-validation-boundary`, base = HEAD `f16b1f328f3fbf1050afdcec583d252d528c72bf`.
R6-C1 kit: `/opt/ridecheck-builds/crm-minimum-launch-enablement-kit-R6-C1-20261111T204950Z`, provisional anchor `641dac2ec9473db19b9f3bea2e80149d93296c545f312441b476444d0a596184`.
R6-C1 private runtime: `/opt/ridecheck-builds/crm-minimum-launch-enablement-PRIVATE-R6-C1-20261111T204950Z`.
R6-C1 evidence (read only): `/opt/ridecheck-builds/crm-final-convergence-r6-c1-20261111T203547Z`.
Reviewer evidence (disposable, mode 0700): `/opt/ridecheck-builds/crm-r6-c1-focused-review-20261112T005700Z/`.
Rehearsal directory (the only write into a private runtime): `PRIVATE-R6-C1-20261111T204950Z/run-r6c1-independent-review-20261112T024326Z/`.

Authoritative inputs, read completely:
- the R6-C1 closeout (`…R6-C1_CLOSEOUT_001.md`);
- the R6 integrated review audit (`…R6-INTEGRATED-INDEPENDENT-REVIEW_AUDIT_001.md`).

# VERDICT: CORRECTION REQUIRED

R6-C1 repairs what it set out to repair, with one exception. That exception is the day/date dimension of availability claims. The prompt names it as the critical test, and it reproduces.

**R6C1RV-S01 (HIGH, blocking, source).** The validator holds no canonical date at all. Its availability check compares only `HH:MM` endpoints and single times against the evaluated slot times.

So when ScheduleService evaluated one day, an AI reply offering the same times on a different weekday or date is **delivered verbatim**:
- "Te esperamos el jueves de 10:00 a 11:00." is delivered while Monday was evaluated.
- "Te esperamos el 20 de noviembre de 15:00 a 15:30." is delivered while 14 November was evaluated.
- "Podemos ir mañana entre 10:00 y 11:00." is delivered while today was evaluated.
- The same holds for a numeric date, a single time, a question form and "pasado mañana".

This happens in all four postures, on SQLite and on PostgreSQL, with byte-identical output. The closeout disclosed this residual (§3.6) without treating it as blocking. Per the review rule it is **not** accepted debt.

Everything else checked holds:
- S02 and P01 are closed, including clarification questions and clause boundaries.
- T01 is closed.
- The sentinel is green and effective.
- K01 finalization is genuinely transactional against real filesystem faults and real signals.
- K02 deep VERIFY binds the commit, tree and image.
- Regression is NEW 0, with only the authorized sentinel as GONE.
- Properly staged PostgreSQL is NEW 0 / GONE 0.
- The independent rehearsal passes 94/0.
- Live is unchanged.

The non-blocking items are listed in §13.

Nothing was staged, committed, pushed, built, finalized, deployed or migrated. No flag, allowlist or live data changed. Meta was not contacted and nothing was sent. The real R6-C1 kit remains PROVISIONAL and byte-identical. **W-3 remains OPEN. The final WILD remains NOT RUN.**

---

## 0. Summary

| Area | Result |
|---|---|
| Identity | HEAD = merge-base = f16b1f3, 0 commits, 0 staged, 0 ignored. 13 changed code/test paths with every sha256 as in the closeout. Worktree = sealed `cand/` + the post-seal R6-C1 closeout. Tree / changed / app pins reproduce (`3560ea14…` / `2b927a5d…` / `8aaa8d28…`). Kit verifies; CHECKSUMS = PROVISIONAL_CHECKSUMS = anchor `641dac2e…6184` (121 entries); file set = listing + 2 manifests |
| **Day/date vs canonical availability** | **REPRODUCED — 7 distinct mismatched replies delivered in 4/4 postures, SQLite and PG (R6C1RV-S01, blocking)** |
| S01 (other than day/date) | Closed. Concrete windows with day anchors and transactional frames are refused; general-hours information survives; a one-endpoint mismatch is refused |
| S02 | Closed. Lowercase possessive and dislocated-topic wrong vehicles are refused; the canonical vehicle is kept; ordinary prose and clause boundaries are safe |
| P01 | Closed. Question-form price, slot, window, vehicle and location proposals are refused; clarification questions survive; no state is created |
| T01 | Closed (my X18/X19 isinstance mutants are killed) |
| Sentinel | Green; red on drift in each of the 4 re-recorded files and in unchanged frozen files; the assertion code is unchanged |
| Regression | Focused SQLite NEW 0 / GONE 1 (sentinel). Full SQLite NEW 0 / GONE 1 (sentinel). Changed 0, skip changes 0 |
| PostgreSQL (staged) | 29 suites, 713 = 713, **NEW 0 / GONE 0**. SB2B clean identical. R6-C1 production path + R6 corpus/contract on PG: 71 passed / 1467 subtests. R5-F1 26/26, 0 outstanding questions |
| Source mutants (mine) | 21/22 killed. X05 is an equivalent mutant. The "day ignored" mutant cannot be constructed because the candidate already ignores the day |
| Kit K01/K02 (mine) | 351/353 checks. The 2 misses are presence-only evidence fields (R6C1RV-K03, LOW). The pin gate refuses 8/8 gated operations after a failed finalization, with 0 docker calls |
| Kit suites | R6-C1 84/0; R3 270/0; R5 242/0; R6 112/0; `mutate_r6c1.py` 29/29 |
| Rehearsal | **Independent full run 94/0** |
| Live | before = after, except the R6-C1 private runtime gaining exactly the rehearsal directory (882 → 1313 files) |

---

## 1. Identity and scope

**Git.** `rev-parse HEAD` = `merge-base` = f16b1f3. `rev-list --count f16b1f3..HEAD` = 0, `diff --cached` is empty, and `ls-files -o -i --exclude-standard` is empty. Tracked `docs/Launch` modified vs base: 0.

**Changed paths (13, outside docs/Launch), sha256 reproduced independently. All equal the closeout §2.**

| St | Path | sha256 |
|---|---|---|
| M | backend/app/services/conversation_engine.py | `7bcabe6af3fbbebffa6bb2d4c815cc1622e32005bf9015b9710225887ad75c4c` |
| A | backend/app/services/reply_claim_shape.py | `b2f3c1080794c440b352e02876859616a4d2f57312caf7e9e34a7e5a67c58eec` |
| M | backend/app/services/response_validator.py | `634fc0f7ad8bba9c8c659e37e0af33be95bcfb72e9bc250dc754a2a79429207c` (blob `5e5690ee…230b0a`) |
| A | tests/fixtures/r6_zone_names.txt | `5994ade38077d769f67fc509c3c46a48a7cfc075d7bdd054c8c6b867d9dd52fd` |
| M | tests/test_l4_7d_response_validator.py | `ff379913cca5effcfc77a9513ebc3e262c69eb7ed58a47cd9dbd59175ed886a5` |
| M | tests/test_l4_7w5_g3_6_proposition_migration.py | `9b0a3beda0aa1f2475fc86150e0c569c59e6a9384a4957789ec36a672596dbdb` |
| A | tests/test_r5_f1_motorcycle_handoff_production_path.py | `ed191b470e41a19e910b3028a9ebf16c2c7512b9bdeae53e0595c9932fc87da9` |
| A | tests/test_r5_f1_template_validation_contract.py | `d4da3808a53923f19cc27ff45f387263eda32cd2e0a60631f8432b2c40fd059a` |
| A | tests/test_r6_c1_claim_frames.py | `8bddb52b12684fb8ebf79155b83c8c7d0692ad39d659b77ad89cf948698b13d8` |
| A | tests/test_r6_c1_claim_frames_production_path.py | `dfee9d7302e95299325719f6b35e8e3c09d8ac3b7c908d6fd466a6291e94b44e` |
| A | tests/test_r6_full_engine_prose_corpus.py | `c59fe1512ed1c4a3fa7fad50c27d092c7987bab9041c0a6fa2609ddc2e8bc466` |
| A | tests/test_r6_structured_response_contract.py | `17d18b175a9b5cc8c42871547706c4fa251785bfda3bacbfd6419fd330f0ae63` |
| M | tests/test_sb1_booking_representation.py | `e3fe4041745ca1c24d54184375585741eab10eda1afc3768ce208361271358c0` |

**docs/Launch.** It contains only untracked `.md` additions: the two R5-F1 documents (`66f33765…`, `c24d3fee…`), the R6 integrated audit (`816322d2…`, not rewritten), the R6 closeout (`603e37ab…`, = closeout §6) and the R6-C1 closeout. This audit is the only file I wrote.

**Seal.** I extracted `git ls-files -co --exclude-standard` (723 files). It equals the sealed `cand/` (722 files) plus the R6-C1 closeout, which was written after the seal. My `git archive f16b1f3` manifest reproduces `7282f596…` and is byte-equal to the sealed `base/`.

**Pins recomputed from the worktree.**
- Tree outside docs/Launch, 678 files: `3560ea14a91fef9d4cda5123e4df229d082bdec330d03db634805f6cb9e8ab38`.
- `backend/app`, 137 files: `8aaa8d2805757649877ceda0f61df439fb066318f441abe772111895b753f07d`.
- `changed_paths.manifest`: `2b927a5d21af308fe9ba8396922169d79ac21fcccb45c6b046aaf845b90d40ca`, every row's blob reproducing.

All three are byte-equal to the kit's `pins/*.manifest` and to `config/pins.conf`.

**Kit.** `sha256sum -c` passes. `sha256(CHECKSUMS.sha256)` = `sha256(pins/PROVISIONAL_CHECKSUMS.sha256)` = `641dac2ec9473db19b9f3bea2e80149d93296c545f312441b476444d0a596184`, with 121 entries, and the two files are `cmp`-identical. The file set equals the listing plus the two manifests. There are no links and no hidden files besides the sealed set. `PIN_STATE=PROVISIONAL`, and the three keys read `UNFINALIZED`.

**Predecessor kits and runtimes.**
- The R3/R4/R5/R6 kits verify (CHECKSUMS `dd9686c6` / `d884015e` / `3c228f71` / `99c3e161`).
- The R3/R4/R5/R6 private runtimes verify.
- Whole-tree hashes are identical before and after this review (§12).

**Tests.** No test was deleted, skipped, xfailed or weakened:
- In the R6-C1 changes, the only modified test is `test_sb1_booking_representation.py`. Its diff changes exactly four dictionary values and adds authorization comments; the assertion code is unchanged.
- The only removed assert in the whole diff vs base is R6's reviewed tightening of `test_validation_failure_never_breaks_a_turn`, carried unchanged (same sha256 as the R6 audit).
- The two "skip" hits in `test_r6_c1_claim_frames.py` are prose (a docstring and a test name), not markers.

**Secrets (count only).** The added and new content is 4,047 lines. It contains 0 `.env` values of 8 or more characters, 0 EAA / sk- / PEM / Bearer shapes, 0 `549…` numbers and 0 non-`example.invalid` emails. The R6-C1 kit contains 0 `.env` values.

**Identity: no drift.**

---

## 2. R6 architectural invariants (re-checked where R6-C1 touched them)

| Invariant | Ruling | Evidence |
|---|---|---|
| Exact-type provenance | **Holds** | `validate_envelope` uses `type(value) is ApprovedTemplateText / ComposedText`. Mutants X18 and X19 (`isinstance`) are KILLED |
| Structured claims vs canonical facts | **Holds**, except that availability has no date fact (§3) | `CanonicalFacts.offered_slots` is a tuple of `HH:MM` only |
| AI text cannot acquire template/composed trust | **Holds** | T01 assertions; X18/X19 |
| Safe exact-`str` failure output; no raw-text delivery after validator failure | **Holds** | R6 contract suite 216-test targeted run green; unchanged code paths |
| No state from assistant prose; no outstanding question from words alone | **Holds** | 692 + 52 full-path cases: 0 pending fuzzy, 0 revisions, 0 `preferred_time`, only DETERMINISTIC_TEMPLATE questions or a genuine missing-place question. X20 (the producer reads the reply) and X21 (AI time unchecked) are KILLED |
| Plan-appropriate fallbacks | **Holds** | every refused case: `VALIDATION_FALLBACK`, no re-asking of a known vehicle or place |
| Truthful motorcycle handoff | **Holds** | R5-F1 production path 26/26 on PG with 0 outstanding questions; rehearsal RR10 W2-6 MET |
| Ordinary prose (uno por uno, buena idea, punto por punto, a las 10 en punto, pilares, norteamericano…) | **Holds** | delivered verbatim in every world and posture (S02h–S02r below) |
| Real false vehicle, location, price, availability, booking and acceptance propositions refused | **Holds for all except a time stated on a different day** (§3) | §§3–5 |

---

## 3. R6IRV-S01 — concrete appointment windows

### 3.1 Critical day/date test — **REPRODUCED (R6C1RV-S01, blocking)**

**Probe.** `probe/daydate_probe.py` (sha16 `11c5f13f16c480f4`) is reviewer-authored.
- It drives the candidate's own full-path harness (`run_case` of `test_r6_c1_claim_frames_production_path`): `handle_message` → CE → reply model (canned) → admission → `_send_text_to_wa` → envelope → `plan.compose` → OutboundSafetyGate → Meta recorder.
- It runs all four postures (direct webhook false/true × Smart Booking false/true) with the interlock armed.
- Canonical state: the `slots` world (Corolla, CABA/Palermo, QUOTED), with `last_offered_slots` = `last_visible_slots` = `["10:00","11:00","15:00","15:30"]` and `active_requested_date` = the evaluated date. "Today" (business timezone) was Wednesday 2026-11-11.

| Id | Canonical evaluated date | Reply | Expected | Result (4/4 postures) |
|---|---|---|---|---|
| DD01 | Mon 2026-11-16 | Te esperamos el jueves de 10:00 a 11:00. | refuse | **DELIVERED verbatim**, AI_COMPOSED, no finding |
| DD02 | Mon 2026-11-16 | Te esperamos el lunes de 10:00 a 11:00. | keep | delivered |
| DD03 | Sat 2026-11-14 | Te esperamos el 20 de noviembre de 15:00 a 15:30. | refuse | **DELIVERED** |
| DD04 | Sat 2026-11-14 | Te esperamos el 14 de noviembre de 15:00 a 15:30. | keep | delivered |
| DD05 | today 2026-11-11 | Podemos ir mañana entre 10:00 y 11:00. | refuse | **DELIVERED** |
| DD06 | today | Podemos ir hoy entre 10:00 y 11:00. | keep | delivered |
| DD07 | Mon 2026-11-16 | Te esperamos el 19/11 de 10:00 a 11:00. | refuse | **DELIVERED** |
| DD08 | Mon 2026-11-16 | Te esperamos el lunes de 10:00 a 12:00. (one unsupported endpoint) | refuse | refused (AVAILABILITY → fallback) |
| DD09 | Mon 2026-11-16 | Te ofrezco el lunes a las 10:00. (supported single time) | keep | delivered |
| DD10 | Mon 2026-11-16 | Te ofrezco el jueves a las 10:00. (single time, wrong day) | refuse | **DELIVERED** |
| DD11 | Mon 2026-11-16 | ¿Te reservo el jueves de 10:00 a 11:00? | refuse | **DELIVERED** |
| DD12 | Mon 2026-11-16 | Pasado mañana te esperamos de 15:00 a 15:30. (= Fri 13) | refuse | **DELIVERED** |
| DD13 | Mon 2026-11-16, stage BOOKED | Te confirmamos: te esperamos el lunes de 10:00 a 11:00. | keep | delivered |

SQLite: 52 cases, 28 wrong (7 cases × 4 postures). PostgreSQL 16.13 on a staged schema: **byte-identical output** (`9981fd12a35acd87` both). No mismatched case wrote state: 0 outstanding questions, 0 revisions, `preferred_time` None. The harm is a false availability statement delivered to the customer, who is told a day the business never evaluated.

**Root cause (read).**
- `ConversationEngine._canonical_facts_for_reply` (CE ~12090–12160) builds `offered_slots` from `last_visible_slots` / `last_offered_slots`, which are JSON lists of `"HH:MM"`. `CanonicalFacts` has **no date field**.
- `_check_availability` (validator ~830–880) uses the sentence's day anchor only to classify a range as concrete. It then checks `range_times` and `times` for membership in `offered_slots`. The stated day is never compared with anything.
- The implementer's own positive control `test_s01_04_an_evaluated_window_is_supported` keeps "Te esperamos el jueves de 15:00 a 15:30." in a `slots` world that has no canonical date at all. It pins the day-blind acceptance as expected behaviour.
- The single-time case (DD10) has the same defect, inherited since L4.7D. R6-C1 makes it reachable for ranges and questions.

### 3.2 Remaining S01 behaviour (full path, my variant probe)

`probe/frames_probe.py` (sha16 `be84d4c7dc5b41d0`) covers 692 cases over 4 worlds × 4 postures, with **0 failures**, and its SQLite and PG outputs are byte-identical (`a91e752376c2ab31`).
- **Refused** (no evaluated slots), including the exact audit examples:
  - "Te esperamos el jueves de 15:00 a 15:30.", "Podemos ir mañana entre 10:00 y 11:00.";
  - explicit dates ("El 18/11 te esperamos de 14:00 a 14:30.", "El 21 de noviembre podemos ir entre 9:00 y 10:00.");
  - hoy / pasado mañana / "Este viernes" plus ranges;
  - transactional frames ("Agendamos la inspección de 11:00 a 12:00.", "La visita sería entre 13:00 y 13:30.", "Te reservo el turno de 10:00 a 10:30.").
- **Kept verbatim:**
  - "Trabajamos de lunes a viernes de 9:00 a 18:00.";
  - "De 9:00 a 18:00 atendemos consultas.";
  - "Nuestro horario de atención es de 9:00 a 18:00.";
  - "Atendemos todos los días de 9:00 a 18:00.";
  - "Los sábados abrimos de 9:00 a 13:00."
- **Supported:** a matching day with matching endpoints (DD02/DD04/DD06), and a supported single time (DD09). A confirmed booking restating its window (DD13) is kept.

---

## 4. R6IRV-S02 — vehicle claim frames

These run in the same 692-case probe, in worlds new / corolla / located / both (wrong vehicle) and gol (canonical VW Gol).

| Class | Cases | Result |
|---|---|---|
| Wrong vehicle refused | "Perfecto, revisamos tu gol mañana."; "Gol, perfecto, lo revisamos."; "Dale, coordinamos la revisión de tu gol."; "Te reservo el turno para tu gol."; "Gol, buenísimo, lo inspeccionamos el jueves." | refused (VEHICLE → fallback), 16/16 cells each |
| Same frames, canonical Gol | the two audit examples | kept verbatim |
| Ordinary prose | "Uno nunca sabe."; "Revisamos todo uno por uno."; "…uno de los dos autos…"; "Lo vemos punto por punto."; "¡Buena idea!…"; "Me gusta tu idea, coordinamos la revisión cuando quieras."; "Revisamos el auto; tu idea nos parece buena."; "Lo revisamos todo, y tu punto de vista nos sirve."; "Te llegamos a las 10 en punto."; "Revisamos los pilares del auto, es una marca norteamericano o europea." | kept verbatim, no state |
| Clause boundary | "Gol de media cancha, como dicen, lo importante es revisar bien." (sentence-initial model; the action clause has no object clitic), plus the `tu idea` / `tu punto` sentences with an action in another clause | kept verbatim |

Mutants X06–X10 are all KILLED: possessive frame off, any possessive a claim, an action in another clause, a topic without the clitic+action frame, and the topic frame off.

**S02: closed.**

---

## 5. R6IRV-P01 — question-form propositions

| Class | Cases | Result |
|---|---|---|
| Wrong price | "¿Te parece bien $12.345 por la revisión?", "¿Te parece $99.000 la inspección completa?" (new / located / both) | refused, never rewritten. In `corolla` the earlier BUG-2 scrub removes it (disclosed, §12D) |
| Unsupported slot | "¿Te reservo el jueves a las 11:30?", "¿Te viene bien mañana a las 9:30?" | refused |
| Unsupported window | "¿Podemos ir mañana entre 10:00 y 11:00?" | refused |
| Wrong vehicle | "¿Coordinamos la revisión de tu Fiat Uno 2015?", "¿Agendamos la inspección de tu Peugeot 208?" | refused |
| Wrong location | "¿Vamos hasta Pilar mañana?", "¿Coordinamos la revisión en Pilar?" (located / both) | refused |
| Clarifications kept | "¿Es un Peugeot 2008?", "¿Qué auto es, marca y modelo?" (vehicle missing); "¿En qué zona está el auto?" (place missing); "¿Qué día te queda mejor para la revisión?", "¿Hay algún día de la semana que te quede cómodo?" (no concrete slot); "¿Ya te llegó la confirmación del turno?" (asks; asserts no booking); "¿Querés que coordinemos la revisión?" | kept verbatim. Outstanding questions are only the genuine missing-place one |

False content was never delivered and created no state. Mutants X11–X17 and X22 are all KILLED: blanket bypass restored, price rewritten, question availability skipped, every vehicle question a claim, every vehicle question a clarification, every location question a clarification, nouns as proposal verbs, and question windows unchecked.

**P01: closed**, apart from the question form of the day/date defect (DD11), which is part of R6C1RV-S01.

---

## 6. Provenance and accepted debt

- **T01: closed.** `T01_SubclassProvenance` passes in the targeted run. My X18 (`isinstance` for ApprovedTemplateText = the R6 audit's M03) and X19 (for ComposedText) are both KILLED.
- **Owner rulings, recorded and not expanded:** C01 fallback copy approved (pinned); P02 accepted launch debt; P03 accepted low launch debt; Q01 accepted for launch.
- **P02.** `test_p02_01..04` pass. The probes confirm that a refused sentence is never delivered and creates no state.
- **P03.** `test_p03_01` passes and pins exactly the documented narrow form: "Te lo revisamos en Pilar mañana." to a Palermo customer, with no inspection noun and no presence/motion token. I did not widen it.

---

## 7. SB1 frozen sentinel

- **Green on the candidate.** It passes in the targeted run and alone (`1 passed`).
- **Authorization comments** are present for each of the four re-recorded blobs.
  - `claim_projection.py` `a420ef32…`, `flow_data_exchange.py` `4a2310c5…` and `whatsapp.py` `4e979d37…` equal the f16b1f3 blobs (`git rev-parse f16b1f3:<path>`).
  - `response_validator.py` `5e5690ee…` equals the worktree blob.
- **The assertion logic is unchanged** (the diff shows four values plus comments).
- **Effective.** I ran it on a scratch copy of the sealed tree, applying a one-byte drift to one file per run:

  | Drift in | Result |
  |---|---|
  | claim_projection.py | 1 failed |
  | response_validator.py | 1 failed |
  | flow_data_exchange.py | 1 failed |
  | whatsapp.py | 1 failed |
  | schedule.py (unchanged frozen) | 1 failed |
  | pricing.py (unchanged frozen) | 1 failed |

  Restored, it passes again, and the scratch copy is byte-equal to the sealed tree.

**Ruling: the sentinel is an effective guard.** The correction in §14 will change `response_validator.py` again, so its blob must be re-recorded again with an authorization comment.

---

## 8. Regression and PostgreSQL

All runs used `ridecheck-crm-test:l4.7b2`, `--network none`, read-only trees (my base archive and my copy of the sealed `cand/`) and junit-identity comparison by my own `cmp.py`.

| Run | Base f16b1f3 | Candidate | Comparison |
|---|---|---|---|
| Targeted R6-C1 / R6 / R5-F1 / l4_7d / SB1 suites (8 files) | — | **216 passed, 2033 subtests** | — |
| Focused SQLite (the closeout's 69-file list; 63 exist on base) | 18 F / 2149 P / 361 S | 17 F / 2310 P / 361 S | **NEW 0, GONE 1, COMMON 17, changed 0, +160, −0, skip changes 0** |
| Full SQLite `../tests` | 62 F + 9 E / 7490 P / 786 S (8346) | 61 F + 9 E / 7652 P / 786 S (8507) | **NEW 0, GONE 1, COMMON 70, changed 0, +161, −0, skip changes 0** |

In both SQLite comparisons the single GONE identity is `test_sb1_20_frozen_files_unchanged` (red on base, green on the candidate), which is the authorized repair.

**PostgreSQL, properly staged.** This reproduces the R6 reviewer's approach (`pgstage.sh`, sha16 in §15):
- a disposable `postgres:16` (16.13) on tmpfs, on an `--internal` network, `TimeZone=Etc/UTC`;
- one template per tree built by `create_all` from the pre-schema record, then `alembic stamp 20261006_clarification_attempts` and `upgrade head`, giving `head = 20261104_vehicle_identity` on both;
- each of the 29 PG suites on its **own fresh clone** of its tree's template, with all 19 `*_PG_URL` variables wired.

| Item | Base | Candidate | Comparison |
|---|---|---|---|
| 29 PG suites | 2 F / 598 P / 113 S (713) | 2 F / 598 P / 113 S (713) | **NEW 0, GONE 0, COMMON 2, changed 0, skip changes 0** (= the R6 audit's 713) |
| SB2B with its clean DB | 46 P / 1 F / 1 S | identical | NEW 0 / GONE 0 |
| R6-C1 frames production path + R6 corpus + contract (cand) | — | **71 passed, 1467 subtests** | real PG rows written (1314 threads) |
| R5-F1 production path (cand) | — | **26/26**, outstanding questions 0 | — |
| Smart Booking PG (`test_sb2a_*`, `test_sb2b_option_b_pg`) | as base | identical per file | NEW 0 / GONE 0 |
| My day/date and frames probes (cand) | — | byte-identical to SQLite | the day/date defect reproduces on PG |

Harness and environment results, reported separately (none is product evidence):
- `test_p2_pg_00_the_schema_is_the_migrated_head` asserts the stale head `20261022_operator_notifications`. It is pre-existing and identical on both trees.
- `test_rv03_pg_13_every_blocking_referent_is_handled`: an old tester-reset helper does not handle `whatsapp_outstanding_questions` / `whatsapp_thread_evidence`. It is pre-existing and identical on both trees; the R6-C1 kit's own resets are proven isolating in the rehearsal.
- SB2B `test_sb2b_m_02_clean_install_matches_the_model`: `alembic_version` is missing in the clean DB on both trees, which is a staging artifact.
- `schema equal base/cand: NO` is a harness artifact. `backend/migrations/` and `app/models.py` are byte-identical, and the only `backend/` difference is the new `reply_claim_shape.py`. pg_dump 16.13 writes a per-run random `\restrict` token, so two dumps never hash equal.
- `test_m19_r1_2_pg_concurrency.py` is not run (outside the R6 reviewer's list); this is disclosed.

**Skips: 113, identical on both trees.** Every one reads "…_URL not set — no disposable PostgreSQL; nothing proven". The variables are `CONV_SSA_PARENT_URL` (31), `CONV_SSA_PARENT_URL/CONV_SSC_PGBIN` (23), `VEHICLE_PG_MIG_URL` (13), `SBN_PG_BASE_URL` (9), `CONV_PNRN_*` (7), `SBNA_PG_MIG_URL` (5), `MINLAUNCH_PG_MIG_URL` (4), and others ≤ 3. They are migration-lineage suites that need parent-schema databases, and none touches the changed code.

---

## 9. Source mutations (reviewer-authored, 22)

Each mutant is one exact replacement in a private copy of one file, bind-mounted read-only over the sealed tree. The run covers seven suites (the five closeout suites plus the R6 corpus and the R5-F1 production path) with `-x`. The unmutated control passes 193, with 2033 subtests.

| Id | Weakens | Result |
|---|---|---|
| X01 | concrete range treated as general hours | KILLED |
| X02 | day/date anchor ignored for range classification | KILLED |
| — | **only endpoints checked while the day is ignored** | **not constructible: this IS the candidate (R6C1RV-S01)** |
| X03 | the second (unsupported) endpoint accepted | KILLED |
| X04 | transactional frame ignored | KILLED |
| X05 | empty evaluation supports a window | SURVIVED — **equivalent**: when `offered_slots` is empty, every endpoint is in `unknown`, so the dropped disjunct is redundant |
| X06 | lowercase possessive frame disabled | KILLED |
| X07 | unrelated possessive turned into a vehicle claim | KILLED |
| X08 | action in another clause counts | KILLED |
| X09 | sentence-initial topic accepted without the clitic+action frame | KILLED |
| X10 | dislocated-topic frame disabled | KILLED |
| X11 | blanket question bypass restored | KILLED |
| X12 | question price rewritten instead of refused | KILLED |
| X13 | question availability skipped | KILLED |
| X14 | every vehicle question treated as a claim | KILLED |
| X15 | every vehicle question treated as clarification | KILLED |
| X16 | every location question treated as clarification | KILLED |
| X17 | transactional nouns count as proposal verbs | KILLED |
| X18 | template provenance by `isinstance` (the old M03) | KILLED |
| X19 | composed provenance by `isinstance` | KILLED |
| X20 | vehicle producer takes the assistant reply as evidence | KILLED |
| X21 | AI-proposed time stored unchecked | KILLED |
| X22 | question-form concrete window not a proposition | KILLED |

The supplied mutation evidence (`mut/results.txt`: 27/27, control 160) is consistent with mine. **No surviving high-risk mutant**, except that the day-blind check is the shipped behaviour itself.

---

## 10. R6IRV-K01 — transactional finalization

My harness is `fin/fin.sh` and `fin/gate.sh`. It uses:
- scratch `cp -a` copies of the sealed kit;
- a scratch `git clone --shared --no-checkout` of `/opt/ridecheck-crm`, so the real f16b1f3 can be the parent and new objects stay in scratch (the `/opt/ridecheck-crm` refs are unchanged, §12);
- real scratch commits built with `commit-tree`;
- my own fake docker, which models image identity only and logs every call.

Faults are **natural, not the supplied test hook**:
- immutable files and directories (`chattr +i`) make renames and creations fail;
- an image whose `/app/app` content changes between FINALIZE's check, the stage VERIFY and the post-publish VERIFY;
- real SIGTERM / SIGINT / SIGHUP delivered to the FINALIZE process mid-run.

**Refused before any write.** Each case exits 2, the whole kit is byte-identical (names, types, modes and bytes), `PIN_STATE=PROVISIONAL`, there is no evidence, and the gate is CLOSED:
- an added file at the root;
- an added file under `scripts/`;
- a missing sealed file;
- a rename;
- an unlisted directory;
- a hidden file at the root;
- a hidden file in `config/`;
- a file symlink;
- a directory symlink;
- a FIFO;
- the wrong external anchor;
- an edited listed file.

**Failure at each stage.** After every non-zero exit, each scenario shows:
- `PIN_STATE` PROVISIONAL;
- pins.conf and CHECKSUMS byte-identical;
- no `FINAL_PIN_EVIDENCE*`;
- no `verdict=PIN_FINALIZATION_VERIFIED` line outside the 6 sealed files that legitimately contain the string;
- whole kit = pre-run;
- the live pin gate CLOSED.

| Scenario | Exit |
|---|---|
| T1 stage VERIFY fails (image content changed after step 3) | 2 |
| T2 post-publish VERIFY fails (FINAL was published, then restored) | 3 |
| T3 pins.conf rename refused (immutable target) | 3 |
| T4 CHECKSUMS rename refused (evidence already published, then removed) | 3 |
| T5 evidence cannot be created (`pins/` immutable) | 3 |
| T6 `config/` immutable (pins.conf.tmp cannot be created) | 3 |
| T7 SIGTERM / SIGINT / SIGHUP after FINAL pins.conf is published | 3 each |
| T8 SIGTERM during the stage VERIFY | 5, nothing written |

After each failure the kit then finalized correctly: exit 0, exactly 3 files differ, deep VERIFY passes, the gate is OPEN, a second FINALIZE is refused, and an unknown option exits 64.

**The real gated scripts after a failed finalization.** I used a relocated, re-sealed copy of the kit (all 11 `config/*.conf` absolute paths rewritten, scratch anchor `e3169e0c…`) and a scratch private-runtime copy. After FINALIZE exited 3:
- ENABLE H / W1A / W1B / W2, RESET_BETWEEN AB execute / BC dry-run, and reset_testers W1A execute / W2 inventory **all refuse with `R6 pins: pins are 'PROVISIONAL'`**;
- **0 docker calls** reached the failing recording shim.

Once that kit was finalized correctly, RESET_BETWEEN's first refusal became the ledger rule ("H has never opened"), not the pin gate, which proves the earlier refusals came from the pins.

**Robustness assessment.**
- The transaction survives ordinary shell failures (every publish step is `|| exit 3`) and every catchable signal: the restore runs from the EXIT trap with signals ignored, pins.conf first.
- **SIGKILL** cannot be trapped. Killing FINALIZE during step 6 leaves a published FINAL kit that deep-VERIFIES (exit 0) and opens the gate. That is acceptable, because the published bytes are exactly the stage that was deep-verified and promoted.
- By construction, a SIGKILL between the evidence/CHECKSUMS renames and the pins.conf rename leaves pins.conf PROVISIONAL, so the gate stays closed. FINALIZE then refuses ("CHECKSUMS is not the provisional manifest") until the owner restores the two provisional files by hand.
- That is fail-closed, but it is not covered by a recovery instruction (INFO).

**My independent kit mutants (15)** were each applied to a sealed-kit copy, re-sealed with its own anchor and scored by my harness. The harness baseline is the 2 K03 misses, so "killed" means more than 2 failures.
- **8 killed:** PHASE set late; restore leaves pins.conf / evidence / CHECKSUMS; no signal trap; post-publish VERIFY ignored; stage VERIFY ignored; VERIFY ignores the image `/app/app`.
- **6 masked by another independent layer** with the outcome still fail-closed: file-set preflight off, links allowed, VERIFY blob loop off, whole-tree off, docs/Launch rule off, parent off.
  - The redundancy is real: the stage deep VERIFY re-checks the file set and links, and a blob change also changes the path set or a pinned blob.
  - For the docs and parent cases, my tampered commits had no matching image.
  - The implementer's `mutate_r6c1.py` kills all six at the layer level (29/29, independent run).
- **1 survived:** pins.conf published first. Order matters only against an untrappable SIGKILL between renames, which my harness observes but does not assert.

**K01: closed.**

---

## 11. R6IRV-K02 — deep verification

On a genuinely finalized scratch kit, each tamper was applied with CHECKSUMS regenerated where needed:
- **NOT VERIFIED:**
  - same path set but a different **test** blob, even with the image re-labelled to match;
  - a different production blob; a different whole-tree blob outside the changed paths;
  - a modified existing docs/Launch document; a non-.md docs addition;
  - wrong parent; merge commit;
  - FINAL_TAG resolving elsewhere; wrong `git_commit` label; wrong GIT_SHA; wrong image `/app/app`; wrong `schema_head` label;
  - evidence `provisional_checksums_anchor`, `git_sha`, `parent`, `image`, `final_tag`, `candidate_tree_manifest`, `candidate_changed_paths`, `candidate_app_manifest` or `verdict` altered; a duplicated evidence field;
  - an edited script; an added file; an edited non-pin key; an added hidden file; an added symlink.
- **VERIFIED:** the untampered kit. A valid single-parent commit adding a docs/Launch `.md` also finalizes.
- FINALIZE also refuses each of the commit and image variants before writing: test, production, tree and root blobs; docs modified; docs non-.md; wrong parent; merge; root commit; the base itself; tag elsewhere; label; GIT_SHA; app; schema; missing image.

**R6C1RV-K03 (LOW, kit precision, non-blocking).** Evidence `finalized_at` and `source_repo` are checked for presence ("exactly once") only, not value. Altering either value after finalization still VERIFIES. Neither field binds a pin or anything VERIFY relies on (VERIFY uses `PIN_SOURCE_REPO`, not the evidence's `source_repo`). The closeout's "every one of its 11 fields … naming the sealed pins" overstates this: 9 are value-bound and 2 are presence-only.

**The real sealed R6-C1 kit stayed PROVISIONAL and byte-identical throughout.** Its tree hash is `be600fafc5d977f1` before and after, CHECKSUMS `641dac2e…`, and there is no evidence file.

**K02: closed** (K03 is a documentation/precision follow-up).

---

## 12. Disclosures requiring a ruling

**A. Refusal-evidence umask — NON-BLOCKING HARDENING DEBT.**
- Running the real ENABLE / RESET_BETWEEN / reset_testers under umask 022 (relocated scratch kit) created 22 evidence files. The 3 created in **new** directories (`reset-BC-1/…/timeline.log`, `window-W1B-1/…/.r4_overlay_why`, `…/timeline.log`) are **0644**; files appended to existing ones keep 0600.
- All 17 enclosing directories are 0700, and the private-runtime root is 0700, so non-root users cannot traverse to them.
- Content (counts and redacted sample only): refusal text, ISO timestamps, run ids and PIDs. There are 0 `549…` numbers and 0 token, password or secret shapes. The refusal precedes any read of private inputs, the database or the container.
- `private_runtime.sh verify` then **fails closed** (rc 1), so the mode drift is detected before any real window.
- Ruling: no private data can be exposed, so this is not blocking. Recommendation: `umask 077` at the top of ENABLE, RESET_BETWEEN and reset_testers, before the first evidence write.

**B. R5 K52 flake — FAIL-CLOSED harness/production pipe race, NOT an admission race. Non-blocking.**
- Bounded reproduction: K52 alone, 30 runs, **1 failure** (run 20), "the re-check passes on an unchanged OFF state" with reason `dispatch allowed (re-check under lock)`.
- Mechanism (read, then demonstrated deterministically in `logs/k52_mechanism.sh`):
  - `interlock | grep -q '^booking_dispatch_allowed=False$'` runs under `set -o pipefail`;
  - `interlock` prints `booking_dispatch_allowed=` first and five more lines after it;
  - `grep -q` exits on the first match, so a later write by the producer can die of SIGPIPE (141), and pipefail turns that into a refusal;
  - a producer with a delayed tail gives `REFUSED rc=141 0`; capture-then-grep passes; a `True` value is refused either way.
- The pattern is in production kit code: `kit_r5.sh:287` (ENABLE premutation re-check), `RESET_BETWEEN.sh:94` and `reset_testers.sh:89`.
- Effect: at worst a spurious refusal, requiring a rerun. It can never admit, because `grep -q` succeeds only on the `False` line and pipefail can only add failures.
- Recommendation: `o="$(interlock)"; grep -qx 'booking_dispatch_allowed=False' <<<"$o"`.

**C. PostgreSQL staging.** Reproduced with per-suite schema-staged clones (§8). The fully wired empty-database run in the closeout is not used as proof.

**D. Residual frame disclosures (closeout §3.6).**
1. **Definite weekday plus general hours treated as concrete** ("El sábado atendemos de 9:00 a 13:00." is refused unless both endpoints are evaluated): **accepted**. This is the strict direction, and the canonical FAQ hours answer has no `H:MM` token.
2. **Sentence-level day-anchor scope**: **accepted** as the strict direction.
3. **Day/date versus endpoint-only availability matching**: **REJECTED. Blocking** (R6C1RV-S01, §3.1).
4. **Upstream price scrub** (`corolla`, focus vehicle without a zone): **accepted**. The invented amount is never delivered; BUG-2 replaces it with its own missing-zone request, and no state comes from the amount.

---

## 13. Kit suites and rehearsal

R3/R5/R6 ran from a relocated, re-sealed copy of the R6-C1 kit pointing at a scratch private-runtime copy, so that R3's K25 `private_runtime.sh verify` cannot touch the real runtime.

| Job | Result |
|---|---|
| R6-C1 `tests/test_kit_r6c1.sh` (K76–K81) | **84 passed, 0 failed** |
| R6-C1 `tests/mutate_r6c1.py` | **29/29 KILLED** |
| R3 `tests/test_kit.sh` | **270/0** |
| R5 `tests/test_kit_r5.sh` | **242/0** |
| R6 `tests/test_kit_r6.sh` (gated; `R6_CANDIDATE_TREE` = sealed cand) | **112/0** (a first ungated run reported only K68 not run below 1536 MiB and K74 missing its variable) |
| High-risk R6 cases affected by the new layers | K74 inside the R6 suite green; my KM01–KM15 above |

**Independent rehearsal.** I ran `tests/rehearse_r6.sh` from the real sealed kit (read-only) with `R6_CANDIDATE_TREE` set to my copy of the sealed `cand/` and OUTDIR `PRIVATE-R6-C1/run-r6c1-independent-review-20261112T024326Z` (log sha16 `6563b81928881b98`). Result: **94 passed, 0 failed.** It ran on its own `--internal` network, with PG restored under `TimeZone=Etc/UTC`, a real uvicorn and the daemons, and the candidate `backend/app` mounted read-only.

What it showed:
- H → H2 SKIPPED → W1A → RESET_AB → W1B → RESET_BC → W2 → final KILL, with the ledger chain verified (RR11).
- Exactly one OPENED/CLOSED pair per executed kind, each CLOSED with `criteria_not_met=0` before ACCEPT.
- Reset isolation (RR6/RR8): refused before RESET and out of scope; dry run rolled back; executed once; non-tester rows identical; testers fresh.
- RR13 local-wall-clock booking: the local now+1 h booking is missed by the R5 predicate and counted by the helper, the ENABLE watermark, the breach counter and preflight.
- RR10 W2: contact Flow only; needs_human false → true; no offer, booking, notice or job; nothing pending. The reply **is** the truthful handoff constant, **no outstanding question** was opened, W2-6 is MET, and W2 was ACCEPTED.
- RR11 final posture: all eight switches literally false and `booking_dispatch_allowed()` False (env and a fresh parser process); no bytecode written; every other egress refused.
- RR12: live containers and the live fingerprint are unchanged.
- No `rcr6-*` or reviewer container or network remains.

---

## 14. Live isolation (read-only)

Before was taken at 00:57:00Z and after at 02:58:45Z. My probe (`live/probe.sh`, sha16 `f54833b274e92ce0`) is R6-C1's probe plus my read-only additions: R6 and R6-C1 kit and runtime verification with whole-tree hashes; R3/R4/R5 tree hashes; the image set; volumes; every `/opt/ridecheck-crm` worktree's HEAD, staged count and diff hash; and the refs hash. It printed no customer value.

| Item | Before = after |
|---|---|
| backend / postgres / n8n | `04e14e5770f9` (image `sha256:1b8ed611…`, restarts 0), `5246fd86d829`, `6ea80570b67b`; start times unchanged |
| GIT_SHA | f16b1f3 |
| schema head | `20261104_vehicle_identity`; column and constraint md5 unchanged |
| switches | all eight literally false; `booking_dispatch_allowed=False` |
| allowlist | sha16 `ad98b5c31a4e8ed8` |
| database fingerprint | `fbae2abdc3371f9146f0ac51a2ffa427` (= the R6 and R6-C1 closeouts) |
| max ids, pending outbound (0), notices, recovery jobs | unchanged |
| compose, `.env`, beta, probe-off, pin-candidate, 9 + 9 Flow files | hashes unchanged |
| R3/R4/R5/R6/R6-C1 kits | verify OK; tree hashes unchanged (R6-C1 `be600fafc5d977f1`) |
| R3/R4/R5/R6 private runtimes | verify OK; unchanged |
| worktrees, refs, images, volumes | unchanged |

**Single difference:** the R6-C1 private runtime grew from 882 to 1313 files. All 431 new files are inside the rehearsal directory, 0 are outside it, and `PRIVATE_MANIFEST` still verifies.

**Reviewer disclosures.**
1. A harness omission in my first `gate.sh` run left my image shim off PATH. FINALIZE's step-3 `docker image inspect` of a **synthetic, non-existent** image id therefore reached the real daemon twice (read-only). FINALIZE correctly refused, and nothing changed. I fixed it and re-ran.
2. My first K01 run had two harness bugs (a nested image directory, and an absolute VERIFIED-string count). They are fixed, and every result above comes from the corrected run.
3. Two scratch copies of the private runtime used for relocated-kit tests remain under the reviewer directory (`fin/privreloc`, `scratch/priv3r`). Their deletion was refused by the session's permission policy. They sit inside 0700 directories under a 0700 reviewer root, and the owner may remove them by exact path.
4. **Resources.** Heavy jobs ran one at a time, each started at ≥ 1604 MiB through a gate requiring ≥ 1536 MiB. The full SQLite run was deferred by the gate until memory recovered. The lowest reading during a job was 532 MiB, and 0 ABORTs occurred. Shell-only kit tests and the K52 reruns ran while no heavy job was running, except the R6-C1 kit mutations, which overlapped the queued wait.

---

## 15. Disposition of findings

| ID | Severity | Disposition |
|---|---|---|
| **R6C1RV-S01** | **HIGH — blocking (source)** | A stated day/date is never compared with the canonical evaluated availability; matching endpoints alone deliver a false day (§3.1). It covers ranges, single times, questions, explicit dates, numeric dates, weekdays, hoy/mañana/pasado mañana; 4/4 postures; SQLite and PG |
| R6C1RV-K03 | LOW — non-blocking (kit precision) | Evidence `finalized_at` / `source_repo` are presence-only; the closeout overstates "every field" (§11) |
| R6C1RV-K04 | LOW — non-blocking (fail-closed) | The `interlock \| grep -q` pipefail SIGPIPE race (the K52 flake) is in ENABLE, RESET_BETWEEN and reset_testers (§12B) |
| R6C1RV-K05 | LOW — non-blocking hardening | Refusal evidence in new directories is 0644 under umask 022; it holds no private data, the directories are 0700, and verify fails closed (§12A) |
| R6C1RV-K06 | INFO | No recovery instruction for a SIGKILL between the evidence/CHECKSUMS renames and pins.conf (fail-closed) (§10) |
| R6IRV-S01 | — | **Range classification FIXED; NOT CLOSED** because of R6C1RV-S01 |
| R6IRV-S02 | — | **CLOSED** |
| R6IRV-P01 | — | **CLOSED** (the question-form day mismatch is part of R6C1RV-S01) |
| R6IRV-T01 | — | **CLOSED** |
| R6IRV-K01 | — | **CLOSED** |
| R6IRV-K02 | — | **CLOSED** (K03 follow-up) |
| R6IRV-H01 | — | **CLOSED** (green and effective) |
| R6IRV-D01 | — | CLOSED (the R6 closeout §1 rows now name the sealed values) |
| R6IRV-C01 / P02 / P03 / Q01 | — | Owner rulings recorded; behaviour pinned as documented; not expanded |
| R5-F1 | — | Remains FIXED (PG 26/26; rehearsal W2-6 MET) |

---

## 16. Smallest precise correction (R6-C2)

**Source (required).**
1. Give `CanonicalFacts` the canonical evaluated **date** of the offered slots. Read it in `_canonical_facts_for_reply` from the same ScheduleService evaluation state that produced `last_visible_slots` / `last_offered_slots` (e.g. `active_requested_date`, ISO, business timezone). Also give it the booked date when `booking_confirmed`.
2. In `_check_availability`, for statements and questions alike, a sentence carrying a day anchor must resolve to the same calendar date (`BUSINESS_TIMEZONE`). Anchors are a date, `14/11`, `20 de noviembre`, `el día N`, `hoy`, `mañana`, `pasado mañana`, or a definite or bare weekday. The rule then is:
   - the anchor must resolve to the evaluated date, and both endpoints (or every single time) must be evaluated slots;
   - an unresolvable or mismatched anchor, or an anchor with no canonical date, makes the claim **unsupported**, so the existing plan-appropriate fallback applies;
   - a sentence with no anchor keeps today's endpoint rule;
   - a confirmed booking may restate only its own date.
3. Tests:
   - full-path DD01–DD13 equivalents in all four postures on SQLite and PG, including mismatched weekday, date, numeric date, hoy→mañana, pasado mañana, single time, question form and booked restatement;
   - change `test_s01_04` to give the `slots` world a canonical date and keep only the matching-day reply.
4. Mutants: a "day ignored" mutant and an "evaluated date unread" mutant, both of which must be killed.
5. Re-record the `response_validator.py` blob in the SB1 sentinel, with an authorization comment naming the correction milestone.

**Kit (required only as a consequence).** Re-pin and re-seal (new tree, changed-path and app manifests; a new provisional anchor). Re-run the kit suites and mutations and one rehearsal.

**Recommended in the same milestone (non-blocking):** K03 (value-check `source_repo` against `PIN_SOURCE_REPO`, and `finalized_at` as an ISO timestamp, or correct the documentation); K04 (capture-then-grep); K05 (`umask 077`); K06 (a recovery line in the runbook).

---

## 17. W-3 and WILD status

**W-3 remains OPEN. The final WILD remains NOT RUN.** No handset test, window, tester reset or live reset was performed. The R6-C1 kit is **PROVISIONAL**, and every live ENABLE, RESET_BETWEEN and tester reset refuses. **The R5 kit remains the live emergency instrument** for the f16b1f3 deployment.

## 18. Next milestone

**PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C2** (the narrow day/date correction in §16, with re-pin and re-seal), then **…-R6-C2-FOCUSED-INTEGRATED-INDEPENDENT-REVIEW**.

Until that review is READY, commit, push, image build, pin finalization, dark deployment, windows and the WILD remain **NOT AUTHORIZED**.

## 19. Reviewer evidence (sha256 prefix; under `/opt/ridecheck-builds/crm-r6-c1-focused-review-20261112T005700Z/`)

| Item | sha16 |
|---|---|
| `probe/daydate_probe.py`; `daydate_sqlite.jsonl` = `daydate_pg.jsonl` | `11c5f13f16c480f4`; `9981fd12a35acd87` |
| `probe/frames_probe.py`; `frames_sqlite.jsonl` = `frames_pg.jsonl` | `be84d4c7dc5b41d0`; `a91e752376c2ab31` |
| `fin/fin.sh`, `logs/fin.txt`; `fin/gate.sh`, `logs/gate.txt` | `65f007076b73db01`, `9dc07dcac146faff`; `c0a4b0fd2fc81860`, `a3011eefd76c6d1b` |
| `fin/kmut.py`, `fin/kmut_results.txt` | `21bee88c38cefefd`, `39360ef142febff6` |
| `mut/mutants.py`, `mut/results.txt` | `308c306b26f96c3f`, `c1c898d13f5a70ae` |
| `logs/focused.cmp.txt`, `logs/full.cmp.txt` | `91742773f3269f42`, `293aea58cae5b01a` |
| `pgstage.sh` run log `logs/pgstage.txt`; `logs/pg.cmp.txt` | `62d7069f8290bad1`; `9ba56f8ef61957ef` |
| `logs/kit_r6c1.txt`, `logs/kitmut_c1.txt` | `7ee5abf5dbb9fe0e`, `c84fd0548d046c6c` |
| `logs/k52.txt`, `logs/k52_mechanism.sh` | `ae457cdd5ed91ebd`, `4f1d8d0feb896640` |
| `logs/rehearsal_stdout.txt`; rehearsal.log | `93ddf88929893fb9`; `6563b81928881b98` |
| `live/probe.sh`, `live/before.txt`, `live/after.txt`; `logs/memory.log` | `f54833b274e92ce0`, `a2c5f15787d61b2f`, `c820c417b5152141`; `e755577ed8012074` |

**VERDICT: CORRECTION REQUIRED.** STOP. This audit is not staged or committed.
