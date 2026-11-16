PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C2
# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — FINAL CANDIDATE CONVERGENCE R6-C2 — CLOSEOUT 001

Date: 2026-11-12 (work 03:57Z–09:05Z)
Source worktree: `/opt/ridecheck-crm-r5-f1-response-validation`, branch `fix/prelaunch-r5-f1-response-validation-boundary`, base = HEAD `f16b1f328f3fbf1050afdcec583d252d528c72bf` (0 commits, 0 staged).
Authoritative review: `docs/Launch/2026-11-12_…-R6-C1-FOCUSED-INTEGRATED-INDEPENDENT-REVIEW_AUDIT_001.md` (CORRECTION REQUIRED, R6C1RV-S01), read completely; not rewritten.
R6-C2 kit: `/opt/ridecheck-builds/crm-minimum-launch-enablement-kit-R6-C2-20261112T041634Z`
R6-C2 private runtime: `/opt/ridecheck-builds/crm-minimum-launch-enablement-PRIVATE-R6-C2-20261112T041634Z`
R6-C2 evidence: `/opt/ridecheck-builds/crm-final-convergence-r6-c2-20261112T035715Z` (sealed candidate `cand/`, base archive `base/`)

**New provisional anchor** (sha256 of `pins/PROVISIONAL_CHECKSUMS.sha256` = of `CHECKSUMS.sha256`, 125 entries):

> **`8e2254a65c2106e90eb1d71b44a8af4c07a44598d34e4b8d70315ed770af7c27`**

# VERDICT: READY FOR FINAL FOCUSED INDEPENDENT REVIEW

Every condition of the prompt's verdict rule holds:
- Every stated day/date in an availability proposition is bound to the canonical evaluated date.
- A matching time on a different date is refused, and correct-date availability is delivered.
- Statements and questions share one date rule.
- A confirmed booking restates only its committed date and times.
- The timezone-rollover tests pass.
- S02, P01, T01 and every R6 invariant stay closed.
- K03–K06 are completed.
- The sentinel is green and effective.
- Regression is NEW 0 with only the authorized sentinel GONE.
- Properly staged PostgreSQL is NEW 0 / GONE 0.
- The complete rehearsal passes 94/0.
- Live is byte-identical before and after.

Nothing was staged, committed, pushed, built, finalized, deployed or migrated. No flag, allowlist or live data changed; Meta was not contacted; nothing was sent. No final pin was applied to any kit. The R3, R4, R5, R6 and R6-C1 kits and their private runtimes were not modified (§13). **W-3 remains OPEN. The final WILD remains NOT RUN.**

---

## 0. Summary

| Area | Result |
|---|---|
| R6C1RV-S01 (day/date) | **FIXED.** `CanonicalFacts.availability_date` (typed `date`) is read from the same evaluation as the slots. Every stated day/date resolves in America/Argentina/Buenos_Aires and must equal it. Times must be that date's evaluated slots (§3) |
| Mismatched-date production path | **13 tests / 92 posture cases, all refused**, SQLite and PostgreSQL. 0 delivered, 0 state written (§3.7) |
| Whole day/date production suite | 31 tests / 268 subtests pass on SQLite **and** on PostgreSQL |
| Red control (one harness, 4 trees) | Wrong day delivered: f16b1f3 32/32, R6 32/32, R6-C1 32/32, **R6-C2 0/32**. Correct day altered: 0/24 on every tree. SQLite = PostgreSQL byte-for-byte (§8) |
| Timezone | Rollover across the UTC midnight and the Buenos Aires midnight is pinned at unit and full-path level. The DB session (`TimeZone=Etc/UTC`) is never the business-date authority (§4) |
| Regression, SQLite | Focused NEW 0 / GONE 1 (sentinel); full NEW 0 / GONE 1 (sentinel); changed 0; skip changes 0 |
| PostgreSQL (staged) | 29 suites 713 = 713, NEW 0 / GONE 0. SB2B clean identical. R5-F1 PG 26/26 with 0 outstanding questions |
| Source mutants | **50/50 killed** (the C1 list C01–C27 plus D01–D18/D13b) |
| SB1 sentinel | Green. Red on a one-line drift in each of 6 frozen files. Assertion code unchanged |
| K03 / K04 / K05 / K06 | All done (§11) |
| Kit suites | R6-C2 81/0 · R6-C1 84/0 · R3 271/0 · R5 242/0 · R6 112/0 |
| Kit mutants | `mutate_r6c2` 24/24 · `mutate_r6c1` 28/29 (Q21 masked in K78, killed by K82) · `mutate_r6` 29/34 (R25/26/27/29/31 masked as in R6-C1, killed as Q25–Q29) |
| Real-config negatives | 5/5 refused at the pin gate, 0 docker calls. Under caller umask 022, 14 new files are all 0600 and 17 new dirs all 0700; the private runtime VERIFIES |
| Rehearsal | **94 passed, 0 failed** (`run-r6c2-20261112T084445Z`) |
| Live | before (03:57:22Z) = after (09:00:17Z), byte-identical; fingerprint `fbae2abdc3371f9146f0ac51a2ffa427` |

---

## 1. Inputs and disposition

| ID | Review severity | Disposition in R6-C2 |
|---|---|---|
| **R6C1RV-S01** | HIGH, blocking | **FIXED** (§3–§10) |
| R6C1RV-K03 | LOW | **DONE.** `source_repo` = `PIN_SOURCE_REPO` exactly; `finalized_at` is one real ISO-8601 UTC instant, not in the future; no duplicate, unknown or stray line. Docs say exactly this (§11.1) |
| R6C1RV-K04 | LOW | **DONE.** Capture-then-validate in the ENABLE premutation re-check, RESET_BETWEEN and reset_testers; ENABLE's saved pre-check uses the same exact rule (§11.2) |
| R6C1RV-K05 | LOW | **DONE.** `umask 077` before the first write in ENABLE, RESET_BETWEEN and reset_testers (§11.3) |
| R6C1RV-K06 | INFO | **DONE.** Release runbook §4a holds a self-checking recovery block. No automatic recovery exists (§11.4) |
| R6IRV-S01/S02/P01/T01/K01/K02/H01/D01 | closed by the review | Unchanged and re-proven: red control §8, mutants C01–C27 §9, kit suites §11 |
| Owner rulings C01 / P02 / P03 / Q01 | recorded | Unchanged. The pinned tests still pass |

---

## 2. Manifest and hashes

**Sealed candidate** `cand/` (every tracked and untracked non-ignored worktree file before this closeout): 726 files, manifest sha256 `2e80097fda72f7eea345c8cb3b355d8f4d7657dd0e16509fc62253555238b7d3`. The worktree equals the seal (re-checked at 09:00Z). Base archive `base/` (`git archive f16b1f3`): manifest `7282f596a87e3e5076ca59ab66d10a761bc9f6b24becb662444b86ddb81709b4` (= the R6-C1 and reviewer base).

**Pins** (method reproduces the R6-C1 pins byte-for-byte on the R6-C1 seal):

| Pin | sha256 | Files |
|---|---|---|
| `CANDIDATE_TREE_MANIFEST_SHA256` (outside docs/Launch) | `42230f4981d368415dd67bdbe690ba6335279c4146b0e54c785b23aafd680c07` | 680 |
| `CANDIDATE_CHANGED_PATHS_SHA256` | `d559f3fba8fa82a00cbc325faeab68542d09aa2dbdb1af982541c5aab7906995` | 16 paths |
| `CANDIDATE_APP_MANIFEST_SHA256` (`backend/app`) | `e30fd8be62039967cb484f2bdad3c7f397d53c5e1f03bbf5a4937f1eecf6d4ca` | 137 |

`FINAL_TAG` is unchanged (`ridecheck-crm-backend:minlaunch-final-r6`), and no image carries it (checked at seal).

**Changed paths vs f16b1f3 (16; ★ = changed or added in R6-C2):**

| St | Path | sha256 |
|---|---|---|
| M ★ | backend/app/services/conversation_engine.py | `cc07f6ae46117734353859e1bbbf09a448e660884ca59aff82189d3c0ab519fe` |
| A | backend/app/services/reply_claim_shape.py | `b2f3c1080794c440b352e02876859616a4d2f57312caf7e9e34a7e5a67c58eec` |
| M ★ | backend/app/services/response_validator.py | `8631fc623f0b14062cb78f21eb6d6d77b24f4f2e329c2214ce3eb68a7571508c` (git blob `7f3c2a2821f956ec0f446aa547d7f58d0fd00505`) |
| A | tests/fixtures/r6_zone_names.txt | `5994ade38077d769f67fc509c3c46a48a7cfc075d7bdd054c8c6b867d9dd52fd` |
| M ★ | tests/test_l4_7c_3b_acceptance_authority_cutover.py | `53bee6e58bea9a1e14ba3bda4e566f32d233e24513385a1710ff6826ee161087` |
| M ★ | tests/test_l4_7d_response_validator.py | `7de064345445481bcf266fa951cebbb1f3bc574de285ba4b8383b6b3d8e72f45` |
| M | tests/test_l4_7w5_g3_6_proposition_migration.py | `9b0a3beda0aa1f2475fc86150e0c569c59e6a9384a4957789ec36a672596dbdb` |
| A | tests/test_r5_f1_motorcycle_handoff_production_path.py | `ed191b470e41a19e910b3028a9ebf16c2c7512b9bdeae53e0595c9932fc87da9` |
| A | tests/test_r5_f1_template_validation_contract.py | `d4da3808a53923f19cc27ff45f387263eda32cd2e0a60631f8432b2c40fd059a` |
| A ★ | tests/test_r6_c1_claim_frames.py | `c489b1d56653528ca99830a80451b3b479e0688bd0443b37d7066f9059dbb669` |
| A ★ | tests/test_r6_c1_claim_frames_production_path.py | `ac5e45ae21ddaebcbd0319427fc79ef56aa2d67ce23b807e5f4802e6fc6347ee` |
| A ★ | tests/test_r6_c2_day_date.py (new, 36 tests) | `a32d35a26dc3abe9ce1c29c5df93ae87862da522208263f82a277a911db5bc54` |
| A ★ | tests/test_r6_c2_day_date_production_path.py (new, 31 tests / 268 subtests) | `060daa6530f9228141821d5d9e77696a5d20e673ad645eeaa4a9765c7c6809f3` |
| A | tests/test_r6_full_engine_prose_corpus.py | `c59fe1512ed1c4a3fa7fad50c27d092c7987bab9041c0a6fa2609ddc2e8bc466` |
| A | tests/test_r6_structured_response_contract.py | `17d18b175a9b5cc8c42871547706c4fa251785bfda3bacbfd6419fd330f0ae63` |
| M ★ | tests/test_sb1_booking_representation.py | `de6b5d2f559f312c5b4e6b4d9c1b5550046fbb0eb474af6b1c278d69d04d7017` |

No migration; schema head `20261104_vehicle_identity`. No test was deleted, skipped or xfailed. §5 lists every changed existing test.

**Kit** (125 entries; anchor above; `PIN_STATE=PROVISIONAL`; the three FINALIZE keys `UNFINALIZED`): changes vs R6-C1 in `docs/R6C2_DIFF_vs_R6C1_kit.patch`. **Private runtime**: the four R5/R6 inputs and the rehearsal snapshot are copied byte-for-byte from the verified R6-C1 runtime. `PRIVATE_MANIFEST` `28a919813b73fabbd1d9c7ceb267ad026f6647050666d4433f8bbd7ce136c8b2` is `cmp`-identical to R6-C1's, and `private_runtime.sh verify` passes before and after every job.

---

## 3. R6C1RV-S01 — the stated day/date is bound to the canonical evaluated date

### 3.1 Canonical facts (`response_validator.CanonicalFacts`)

New fields, all typed:
- `availability_date: Optional[date]`: the calendar date ScheduleService evaluated `offered_slots` for.
- `business_today: Optional[date]`: "today" on the business calendar when the snapshot was taken.
- `booked_date: Optional[date]` and `booked_times: tuple[str, ...]`: the committed booking's own date and its `HH:MM` start/end.

The validator accepts only an exact `datetime.date` in these fields. A `str`, even a valid ISO one, or a `datetime` names **no** date. `canonical_date()` is the one strict parser: an exact `date`, or an exact `str` matching `\d{4}-\d{2}-\d{2}` that is a real calendar date. Free text, a datetime, `16/11/2026`, `2026-11-16T10:00`, `2026-13-40` and `2026-02-30` all give None.

### 3.2 Source and consistency rules (`ConversationEngine._canonical_availability_date`)

The date comes from the **same evaluation state** that produced the slots, and assistant prose is never read. Every CE evaluation site writes `active_requested_date` together with `last_offered_slots`, the full evaluated day. A static test walks every statement block of `conversation_engine.py` and fails if one writes `last_offered_slots` without `active_requested_date` (≥ 6 sites). `last_visible_slots` is always a subset of that evaluation.

The canonical date is `active_requested_date` only when **all** of these hold. Otherwise it is None, and any reply that names a day for those slots is refused:

| Rule | Case covered (unit `OneEvaluation` + full path `DD_StateConsistency`) |
|---|---|
| strict ISO calendar date | invalid ISO, a weekday word, a datetime string, empty → None |
| an evaluation record exists | date present but `last_offered_slots` missing or empty → None |
| every canonical slot is in that evaluation | `last_visible_slots` from another evaluation (⊄ `last_offered_slots`), or a visible list with no evaluation record → None |
| not before business today | an evaluation of a day already past (stale) → None (also re-checked in the validator) |
| a new request replaces both | a new evaluation (Thu 19, written with its slots) makes "el lunes" refused and "el jueves" delivered, on the same thread |

A slot list with no date also gives None: the slots still support a reply with **no** day anchor (rule 8), and no reply that names one.

**Confirmed booking** (`_committed_booking_slot`). The booking's own `ThreadRevision` (`current_revision_id`) must be `booked` or `completed` with a `scheduled_date` and `scheduled_time`. `booked_date` is that date. `booked_times` = (start, start + `schedule.SERVICE_MINUTES`). `active_requested_date` and the last requested time are never used. With no such revision (for example stage BOOKED without a booked revision), a confirmed booking restates no day and no time.

**Business timezone.** `response_validator.BUSINESS_TIMEZONE = "America/Argentina/Buenos_Aires"`, asserted equal to `schedule.BUSINESS_TIMEZONE` by test. `business_today()` = `business_date_at(_utcnow())`. CE passes `business_today` into every snapshot. The database session timezone is never read.

### 3.3 Day/date resolution (`_day_anchors`, deterministic)

Matched on `_norm` text in this order, each form consumed once:

| Form | Resolves to |
|---|---|
| ISO `2026-11-16` | that date |
| `16/11/2026`, `16/11/26` | that date |
| `16/11` | (month 11, day 16) |
| `16 de noviembre de 2026` | that date |
| `20 de noviembre` | (month, day) |
| `el día N` | the first date ≥ today with day N within 62 days, else malformed |
| `pasado mañana` | today + 2 |
| `hoy` | today |
| `mañana` (not "la/las/de/por mañana") | today + 1 |
| a definite weekday (`el / este / próximo …`), or the existing bare-weekday transactional form when the sentence has no general-hours frame | that weekday |

An impossible date (`31/11`, `30 de febrero`, `2026-02-30`, `el día 40`, a 3-digit year) is **malformed**. Weekdays are read exactly as R6-C1's `_has_day_anchor` reads them, and `_DATE_ANCHOR_RE` gained ISO dates so that classification and resolution agree.

### 3.4 The rule (`_check_availability`, for statements and questions alike)

If the sentence carries a day anchor and is an availability proposition (a concrete time or window, the availability lexicon, or a question with a concrete time or window):
1. A canonical date must exist and not be stale, or the claim is unsupported.
2. Every anchor must resolve, and nothing may be malformed.
3. Every anchor must equal the canonical date:
   - a full or relative date must be equal;
   - a (month, day) must match month and day (no other year is guessed);
   - a weekday must match the canonical date's weekday.
4. Every stated single time and both window endpoints must be evaluated slots of that date, and an empty evaluation supports nothing.

Any failure → `ACTION_REMOVED` → the existing plan-appropriate fallback. With no anchor, the R6-C1 endpoint rule is unchanged (rule 8).

A **confirmed booking** may restate only its committed date (same resolution rules) and only its committed start/end times.

**Declared (structured) AVAILABILITY claims** may now carry an ISO date element. It must equal `availability_date` and come with an evaluated slot. Time-only declarations are unchanged.

**No state from wrong-day prose** (`_ai_time_day_is_evaluated`). In SCHEDULING stage, CE's producer already held an AI-proposed time only if it was an evaluated slot (R6). It now also requires the reply's stated day to be the evaluated date, so an evaluated time placed on another day is never stored as `preferred_time`. A static test pins that the producer consults this gate before assigning.

### 3.5 Unchanged invariants

All re-proven by the R6 / R6-C1 suites, the red control (§8), mutants C01–C27 (§9) and the rehearsal:
- general business-hours exemptions;
- ordinary-prose protections (48/48 delivered);
- S02 vehicle frames;
- P01 question propositions;
- exact-type provenance (C23/C24);
- no state from assistant prose (C25/C26, D13/D13b);
- the motorcycle handoff (R5-F1 PG 26/26; RR10).

### 3.6 Tests (new)

- `tests/test_r6_c2_day_date.py` (36 tests). It covers:
  - strict canonical date;
  - every resolution form, morning ≠ tomorrow, general hours name no day, malformed dates, month/day never re-dated;
  - the business timezone (§4);
  - the reviewer mismatches and matches, the statement/question parity, no-date refusal, the unchanged no-anchor rule;
  - a no-anchor window with an empty evaluation, an impossible date beside a valid weekday;
  - confirmed booking, declared ISO dates, the public `stated_day_is`, the one-evaluation builder rules, the producer gate;
  - every evaluation write site.
- `tests/test_r6_c2_day_date_production_path.py` (31 tests, 268 subtests). It runs the full production path (`handle_message` → CE → reply model → admission → `_send_text_to_wa` → envelope → `plan.compose` → OutboundSafetyGate → Meta recorder) with the interlock armed, in all four postures (direct webhook false/true × Smart Booking false/true), on SQLite and on PostgreSQL (`R6_PG_URL`). The business clock is frozen at Wed 2026-11-11 12:00 Buenos Aires.

### 3.7 Exact result of the mismatched-date production-path tests

`DD_MismatchedDayRefused`: 13 tests, **92 posture cases, every one refused**, identically on SQLite and PostgreSQL. Each refused case asserts all of the following:
- exactly one message;
- not the reply, and the wrong day and times absent from it;
- AVAILABILITY refused, provenance `VALIDATION_FALLBACK`;
- no pending vehicle, no candidate change, 0 revisions;
- `preferred_time` and `preferred_day` None;
- only a DETERMINISTIC_TEMPLATE outstanding question.

| Id | Evaluated | Reply (4 postures each) | Result |
|---|---|---|---|
| M01 | Mon 16 | Te esperamos el jueves de 10:00 a 11:00. | refused 4/4 |
| M02 | Sat 14 | Te esperamos el 20 de noviembre de 15:00 a 15:30. | refused 4/4 |
| M03 | today Wed 11 | Podemos ir mañana entre 10:00 y 11:00. | refused 4/4 |
| M04 | Mon 16 | Te esperamos el 19/11 de 10:00 a 11:00. | refused 4/4 |
| M05 | Mon 16 | Te ofrezco el jueves a las 10:00. | refused 4/4 |
| M06 | Mon 16 | ¿Te reservo el jueves de 10:00 a 11:00? | refused 4/4 |
| M07 | Mon 16 | Pasado mañana te esperamos de 15:00 a 15:30. (= Fri 13) | refused 4/4 |
| M08 | Mon 16 | same weekday, wrong date: "el lunes 23/11", "el lunes 23 de noviembre", "el 2026-11-23", "el 16/11/2027" | refused 16/16 |
| M09 | Mon 16 | correct date, unsupported time 12:00 | refused 4/4 |
| M10 | Mon 16 | correct date, one unsupported endpoint (10:00 a 12:00) | refused 4/4 |
| M11 | none | anchor with no canonical date | refused 4/4 |
| M12 | invalid ×6 / stale | `2026-13-40`, `lunes`, `16/11/2026`, `2026-11-16T10:00`, empty, `2026-02-30`; stale `2026-11-09` | refused 28/28 |
| M13 | Mon 16 | unresolvable: `31/11`, `el día 40` | refused 8/8 |

Further mismatches refused on the full path in the other classes:
- booking: a requested-date weekday 4/4, another time 4/4;
- state consistency 24/24;
- timezone boundary 24/24;
- no anchor and an unoffered time 4/4.

**Valid cases delivered verbatim**, AI_COMPOSED, no outstanding question:
- V01: matching weekday (Mon, Sat);
- V02: explicit dates (`14 de noviembre`, `lunes 16 de noviembre de 2026`, ISO);
- V03: numeric (`16/11`, `16/11/2026`);
- V04: hoy / mañana / pasado mañana / `el día 16`;
- V05: a single time, and a single time in a question;
- V06: general hours, with and without an evaluation;
- V07: a confirmed booking restating `lunes 16/11 a las 10:00` and `de 10:00 a 10:45`;
- V08: no anchor with evaluated slots.

---

## 4. Timezone resolution evidence

| Instant (UTC) | Buenos Aires | Pinned |
|---|---|---|
| 2026-11-12T01:30Z (UTC crossed, BA not) | Wed 11, 22:30 | `business_date_at` = Wed 11. hoy = 11, mañana = 12, pasado mañana = 13 kept; hoy→Thu and mañana→Fri refused. `el jueves` (Thu 12) kept, `el miércoles` refused. **Control:** the UTC date is already Thursday, and the validator refuses "hoy" for a Thursday evaluation |
| 2026-11-12T03:30Z (BA crossed) | Thu 12, 00:30 | hoy = 12, mañana = 13 kept; mañana→Thu and pasado mañana→Fri refused. The Wed 11 evaluation is now **stale** and refused |

These run at unit level (`BusinessTimezone`, including `business_today=None`, so the validator's own clock is used) and on the full production path in all four postures on SQLite and PostgreSQL (`DD_BusinessTimezoneRollover`). The PostgreSQL server runs `TimeZone=Etc/UTC`, and the results are byte-identical to SQLite. Mutant D04 (resolve the business date in UTC) is killed.

---

## 5. Changes to existing tests (each strengthens; each disclosed)

| Test | Change | Why |
|---|---|---|
| `test_r6_c1_claim_frames.py` `SLOTS` | gains `availability_date=Thu 2026-11-12`, `business_today=Wed 2026-11-11` | **Required by the prompt.** It had no date and pinned day-blind acceptance |
| `test_r6_c1_claim_frames.py` `test_s01_07` | the booking gains `booked_date` / `booked_times` | rule 9: only the committed booking's date and times |
| `test_r6_c1_claim_frames_production_path.py` `slots` world | seeds `active_requested_date = 2026-11-12` and freezes the business clock on Wed 2026-11-11 | **Required by the prompt.** `test_s01_04` and `p01_06` kept "el jueves" with no date |
| `test_l4_7d_response_validator.py` `test_val_avail_01` (base) | facts gain the evaluated date Thu 2026-09-03; adds "viernes 04/09 … 13:00" → refused | it accepted "Para jueves 03/09 tengo 13:00." with no canonical date, which is the defect |
| `test_l4_7c_3b_…` `test_cut_20` (base) | the accepted facts gain an evaluated Thursday; adds "el viernes …" → refused | it accepted "Tenemos disponibilidad el jueves a las 15." with no canonical date |
| `test_sb1_booking_representation.py` | `response_validator.py` blob re-recorded (`5e5690ee…` → `7f3c2a28…`) with an authorization comment naming PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C2 | the diff is one value plus comments; assertion logic unchanged (§10) |

`test_l4_7c_4a…::test_livesem_12` is a static scan. It treats any CE function whose source names a booked status as a booking writer. The new **reader** `_committed_booking_slot` therefore reads the statuses from a module-level constant (`_COMMITTED_REVISION_STATUSES`). The scan itself is unchanged and passes.

---

## 6. SQLite regression (sealed trees, `--network none`, read-only, junit-identity `cmp.py`)

| Run | Base f16b1f3 | Candidate | Comparison |
|---|---|---|---|
| Focused (72 files; 64 exist on base) | 2554 cases: 18 failing / 2175 P / 361 S | 2781 cases: 17 / 2403 / 361 | **NEW 0, GONE 1, COMMON 17, changed 0**, +227 / −0, skip changes 0 |
| Full `../tests` | 8346 cases: 71 failing / 7489 P / 786 S | 8574 cases: 70 / 7718 / 786 | **NEW 0, GONE 1, COMMON 70, changed 0**, +228 / −0, skip changes 0 |

The only GONE identity is `test_sb1_20_frozen_files_unchanged`: red on base, green on the candidate. It is the authorized sentinel repair. The R5-F1, R6 contract and prose corpus, R6-C1 S01/S02/P01 and R6-C2 suites are all inside these runs and pass. No unexplained failure message or skip differs.

---

## 7. PostgreSQL (properly staged; `pgstage.sh`)

The setup is the R6 and R6-C1 reviewers' method:
- a disposable `postgres:16` (16.13) on tmpfs, on an `--internal` network, `TimeZone=Etc/UTC`;
- one template per tree (`create_all` from the pre-schema record, `alembic stamp 20261006_clarification_attempts`, `upgrade head`; both heads `20261104_vehicle_identity`; column and constraint md5 equal);
- each suite on its **own fresh clone**, with the 19 `*_PG_URL` set.

| Item | Base | Candidate | Comparison |
|---|---|---|---|
| 29 PG suites (the reviewer's list) | 2 F / 598 P / 113 S (713) | 2 F / 598 P / 113 S (713) | **NEW 0, GONE 0**, changed 0, skips identical (= the R6-C1 review's 713) |
| SB2B with its clean DB | 46 P / 1 F / 1 S | identical | NEW 0 / GONE 0 |
| Smart Booking PG (`test_sb2a_*` ×6, `test_sb2b_option_b_pg`) | as base | identical per file | NEW 0 / GONE 0 |
| R6-C2 day/date production path | — | **31 passed, 268 subtests** | real PG rows: 268 threads, 20 booked revisions (the booking fixtures), 0 outstanding questions, 13 distinct evaluated dates |
| R6-C1 frames production path + R6 corpus + contract | — | **71 passed, 1467 subtests** | 1314 threads |
| R5-F1 production path | — | **26/26**, outstanding questions 0 | — |
| Red-control probe | — | R6-C1 and R6-C2 outputs | byte-identical to SQLite (§8) |

The 2 common failures (stale head assertion `test_p2_pg_00`, the old tester-reset helper `test_rv03_pg_13`), the SB2B `alembic_version` staging artifact and the 113 `…_URL not set` migration-lineage skips are exactly the reviewer's, identical on both trees. Teardown left 0 containers and 0 networks.

---

## 8. Red controls (one harness copy from the sealed R6-C2 candidate, mounted into each tree)

`probe/red_c2.py` drives:
- the R6-C1 classes through `run_case` of the R6-C1 production path (worlds new/corolla/located/both, 2 postures);
- the R6-C2 classes through `run_case` of the R6-C2 production path (4 postures): **DDW**, a wrong day/date with times that **are** evaluated slots of the evaluated date, and **DDK**, the correct day.

| Tree | Ordinary prose altered | S01 window delivered | S02 delivered | P01 delivered | **DDW wrong day delivered** | **DDK correct day altered** |
|---|---|---|---|---|---|---|
| f16b1f3 | **48/48** (original prose defect; 4 pending vehicles armed from prose) | 0/16 | 0/16 | 30/32 | 32/32 | 0/24 |
| R6 | 0/48 | **16/16** (its known window issue) | 16/16 | 30/32 | 32/32 | 0/24 |
| R6-C1 | 0/48 | 0/16 | 0/16 | 0/32 | **32/32** (accepts a wrong day when times match) | 0/24 |
| **R6-C2** | 0/48 | 0/16 | 0/16 | 0/32 | **0/32** | **0/24** |

SQLite outputs (sha16): base `56eed1a0842a7f83`, R6 `c230e2c0e55e1111`, R6-C1 `7f8108dfc6624dfb`, R6-C2 `9969757dc509b597`. **PostgreSQL R6-C1 = `7f8108dfc6624dfb` and R6-C2 = `9969757dc509b597`, byte-identical to SQLite.** No tree wrote a preferred time or a revision on any case, and the outputs were identical on both candidate seals (§14.1).

---

## 9. Source mutations (final seal; one exact replacement per mutant, bind-mounted read-only)

The control passes 227 tests / 1496 subtests. **50/50 KILLED**, 0 survived, 0 invalid. These are R6-C1's C01–C27 (C05 now names its `_has_day_anchor` context, because the same line also exists in the new resolver) plus:

| Id | Weakens | Result |
|---|---|---|
| D01 | canonical day ignored (every stated day matches) | KILLED |
| D02 | canonical date never read (the builder uses today) | KILLED |
| D03 / D03b | explicit date compared by weekday only / day-month ignored | KILLED / KILLED |
| D04 | hoy resolved in UTC instead of the business timezone | KILLED |
| D05 | mañana treated as hoy | KILLED |
| D06 | pasado mañana treated as mañana | KILLED |
| D07 | missing canonical date accepted | KILLED |
| D08 / D08b / D08c | stale date + current slots (visible list of another evaluation) / a past evaluation current (builder) / (validator) | KILLED ×3 |
| D09 | single-time day check bypassed | KILLED |
| D10 | question-form day check bypassed | KILLED |
| D11 / D11b / D11c | confirmed booking uses the requested date (validator) / (builder) / restates any time | KILLED ×3 |
| D12 | endpoint-only comparison restored (no day rule at all) | KILLED |
| D13 / D13b | AI-proposed time stored although its day differs (gate body) / the producer no longer consults the gate | KILLED / KILLED |
| D14 | declared ISO date not compared | KILLED |
| D15 | canonical date accepts a `datetime` (isinstance) | KILLED |
| D16 | a malformed day beside a valid one accepted | KILLED |
| D18 | `el día N` resolved without the horizon | KILLED |

The first seal's run found two survivors and fixed them, which forced the second seal (§14.1):
- **C07**: an empty evaluation supporting an unanchored window. The new day rule refused R6-C1's weekday-anchored test sentence first, so a new no-anchor test was added.
- **D13**: the producer gate had no direct test. It was extracted into `_ai_time_day_is_evaluated` with unit and static tests.

Two candidate mutants were removed as equivalent by construction rather than reported:
- "dated time with an empty evaluation": with no slots every stated time is already unknown;
- "general-hours flag in the resolver": spans and habitual forms are removed before the flag matters.

---

## 10. SB1 frozen sentinel

The diff vs R6-C1 is one dictionary value (`response_validator.py` `5e5690ee…` → `7f3c2a2821f956ec0f446aa547d7f58d0fd00505`) plus an authorization comment naming **PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C2**. All other frozen entries are preserved, and the assertion code is unchanged. `sentinel_probe.sh` on the sealed tree:
- control: 1 passed;
- a one-line drift in `response_validator.py`, `claim_projection.py`, `flow_data_exchange.py`, `whatsapp.py`, `schedule.py` or `pricing.py`: **1 failed each**;
- restored: 1 passed.

The sentinel is **green and effective**.

---

## 11. Kit hardening K03–K06, kit suites and mutations (final kit seal `8e2254a6…`)

### 11.1 K03 — evidence precision

`VERIFY_PIN_FINALIZATION.sh` now performs these named checks, each reporting its own FAIL line:
- the evidence holds only the header and the 11 fields: any other, unknown, blank or second comment line fails, and duplicates fail through the exactly-once check;
- `source_repo` is exactly `PIN_SOURCE_REPO`;
- `finalized_at` has the `YYYY-MM-DDTHH:MM:SSZ` form;
- `finalized_at` is a real instant (it round-trips through `date -u`);
- `finalized_at` is not in the future (300 s skew).

FINALIZE writes the header `# R6-C2 pin finalization evidence …`. Runbook §5 and the README describe exactly these checks.

K82 covers:
- a foreign repo, a suffix, an empty value and a duplicate (same value);
- ten malformed timestamps (space-separated, `+00:00`, no `Z`, month 13, 30 February, hour 25, `now`, empty, 2099, doubled), an unknown field, a stray blank line and a second comment — all NOT VERIFIED;
- each layer failing on its own;
- an earlier valid UTC instant still VERIFIES.

**RED:** the sealed R6-C1 VERIFY passes `source_repo=/opt/elsewhere` with `finalized_at=now`.

### 11.2 K04 — fail-closed dispatch check

`kit_r5.sh` defines `dispatch_off_verified` and `dispatch_off_text`. These live outside `common.sh`, which stays byte-identical to its pinned upstream hash `5c7d8ab3…`. The check:
1. captures the whole `interlock` output;
2. requires producer exit 0;
3. requires exactly ONE `booking_dispatch_allowed=` line;
4. requires exactly `booking_dispatch_allowed=False`.

It is used by:
- the ENABLE premutation re-check (`kit_r5.sh` `r5_premutation_recheck`);
- `RESET_BETWEEN.sh`;
- `reset_testers.sh`;
- ENABLE's saved `interlock_before.txt` check, which uses the same exact rule.

No production script keeps the piped pattern.

K83 covers:
- a producer that prints the dispatch line first and keeps writing 200 KB after a pause passes **8/8**;
- **RED:** the old `interlock | grep -q` refuses that same unchanged OFF state (rc 141);
- True, a producer failing after `False` (rc 1), a producer killed (rc 141), a missing line, empty output, a duplicated False, False+True and five malformed spellings all refuse.

### 11.3 K05 — private-runtime umask

ENABLE, RESET_BETWEEN and reset_testers set `umask 077` before `common.sh` is sourced.

K84 runs the real three scripts under a caller umask of 022, on the existing and a brand-new window path: every new file is 0600 and every new directory 0700. Everything refused at the pin gate (0 docker events). **RED:** the R6-C1 scripts leave 0644/0755.

On the **real** configurations (`neg_realconfig.sh`, umask 022):
- ENABLE W2 and H, RESET_BETWEEN AB and BC execute, and reset_testers W1A execute all exit 2 at the pin gate with 0 docker calls;
- 14 files are 0600 and 17 directories 0700;
- `private_runtime.sh verify` → **PRIVATE RUNTIME VERIFIED** (in R6-C1 it failed closed).

### 11.4 K06 — interrupted publication recovery

`docs/FINAL_CONTROLLED_RELEASE_RUNBOOK.md` §4a covers:
- **the exact posture:** FINALIZE killed untrappably after publishing the evidence (and possibly CHECKSUMS) but before `pins.conf`; fail-closed, gate CLOSED, FINALIZE refusing;
- **the identifying checks:** PIN_STATE=PROVISIONAL exactly once; `pins.conf` = the sealed copy; the sealed manifest = the external `$ANCHOR`; every sealed file verifies; only FINALIZE's publication files are extra;
- **a self-checking block** that restores ONLY from the sealed provisional copies and re-verifies checksums, the anchor and the file set;
- **prohibitions:** manual FINAL, hand edits, copying evidence back, any other restore source;
- **a rerun of FINALIZE from §4's beginning.**

There is no automatic recovery mechanism; K85 asserts FINALIZE has no such path.

K85:
- SIGKILLs FINALIZE right after the CHECKSUMS rename, and right after the evidence rename (exit 137, no restore ran);
- proves the posture is PROVISIONAL with evidence present, the gate CLOSED for ENABLE / RESET_BETWEEN / tester reset, and FINALIZE refusing with exit 2 and writing nothing;
- runs **the runbook's own block**, which restores the kit byte-identical to the sealed provisional kit;
- reruns FINALIZE → exit 0 and VERIFIED.

The block STOPs and changes nothing for: a FINAL kit; a hand-edited PIN_STATE; a `pins.conf` edited outside PIN_STATE; a foreign file; a wrong anchor; a changed sealed file. Each STOP is asserted to come from its own check.

### 11.5 Kit suites and mutations (final seal; `kitchain.sh`, serial, gated)

| Job | Result |
|---|---|
| seal before / after | `sha256sum -c` OK, 125 entries, CHECKSUMS = PROVISIONAL_CHECKSUMS = anchor, PROVISIONAL; identical after |
| `tests/test_kit_r6c2.sh` (K82–K86) | **81 passed, 0 failed** |
| `tests/mutate_r6c2.py` | **24/24 KILLED** (N01–N06 K03, N07–N13 K04, N14–N17 K05, N18–N24 K06 runbook) |
| `tests/test_kit_r6c1.sh` (K76–K81, run on the R6-C2 kit with the R6-C2 candidate) | **84/0** |
| `tests/mutate_r6c1.py` | **28/29**. Q21 ("each evidence field exactly once") survives only in K78, whose field-removal probes the new K03 value checks now also catch. Run against K82 (`q21_vs_k82.txt`) it is **KILLED**: both duplicate-field cases fail |
| `tests/test_kit.sh` (R3) | **271/0** |
| `tests/test_kit_r5.sh` | **242/0** (K52, the flake K04 removes, passes) |
| `tests/test_kit_r6.sh` | **112/0** |
| `tests/mutate_r6.py` | 29/34. R25/26/27/29/31 are masked in K74, exactly as in R6-C1, and Q25–Q29 kill each on its own |

---

## 12. Rehearsal (`tests/rehearse_r6.sh`, final kit, sealed candidate mounted read-only)

Output directory `PRIVATE-R6-C2-20261112T041634Z/run-r6c2-20261112T084445Z`. Result: **REHEARSAL R6: 94 passed, 0 failed**. It ran on its own `--internal` network with PG under `TimeZone=Etc/UTC`, a real uvicorn and the daemons. Before it started, MemAvailable was 1771 MiB, disk 23453 MiB, and the live fingerprint `fbae2abd…`.

| Proof | Rehearsal evidence |
|---|---|
| order H → H2 SKIPPED → W1A → RESET_AB → W1B → RESET_BC → W2 → final KILL | RR11 "the order: H, H2 SKIPPED, W1A, RESET_AB, W1B, RESET_BC, W2"; ledger chain verifies |
| one OPENED/CLOSED pair per executed window | RR11 "exactly one CLOSED per OPENED for every kind"; re-running post_window_verify appends no CLOSED |
| `criteria_not_met=0` before ACCEPT | RR3 … RR10 "CLOSED once with criteria_not_met=0", then ACCEPT |
| reset isolation | RR6 / RR8 "only synthetic rows: non-tester rows identical in every table; nothing older than the scope"; never twice |
| local-wall-clock booking protection | RR13: the local now+1 h booking is missed by the R5 predicate under UTC, and counted by the R6 helper, the ENABLE watermark, the breach counter and preflight |
| truthful motorcycle handoff | RR10: the reply after the contact form IS the truthful handoff constant; W2-6 MET |
| no W2 outstanding question | RR10 "no outstanding question was opened on the handed-off thread" |
| no pending work | RR10 "no offer/booking/notice/job, nothing pending" |
| all eight switches false, dispatch False | RR10 / RR11 "ALL EIGHT literally false", "dispatch False (env + fresh-process parse)" |
| no live change | RR12 live containers and live fingerprint unchanged |
| no disposable container or network remains | checked after: 0 non-live containers, 0 rehearsal networks |

---

## 13. Live isolation (read-only `live/probe.sh`, sha16 `acdf1c965677be9f`)

Before was taken at 2026-11-12T03:57:22Z, before any work, and after at 09:00:17Z. **`diff before.txt after.txt` is empty.** The probe covers:
- containers, images and restart counts;
- GIT_SHA `f16b1f3`;
- the eight switches, all literally false, and `booking_dispatch_allowed=False`;
- alembic `20261104_vehicle_identity`;
- DB fingerprint `fbae2abdc3371f9146f0ac51a2ffa427`, max ids, pending outbound 0;
- schema md5s and the allowlist sha16;
- compose, `.env` and Flow file hashes;
- R3/R4/R5/R6/R6-C1 kits: verify OK, with the R6 tree `0aa62e3f…` and the R6-C1 CHECKSUMS `641dac2e…` / tree `02add21e…` unchanged;
- R3–R6-C1 private runtimes verify OK, with the R6-C1 runtime at the same 1313 files;
- `/opt/ridecheck-crm` refs, the image set and volumes.

No customer value was printed.

---

## 14. Disclosures

1. **Seal history.**
   - The candidate was sealed twice. Seal 1 (pins `9e10db89…` / `9e03cd88…` / `6c318184…`) was superseded when its mutation run exposed C07 and D13. Its evidence is kept in `seal1/`; its focused, full and red results were the same as seal 2's.
   - The kit was sealed six times: `e47a703d`, `e7b6becb` (dev), `b4bf958c`, `b81d8e38`, then `8e2254a6` (final). Only the final anchor is valid.
   - Every source gate in §6–§10 ran on candidate seal 2, and every kit gate in §11–§12 on the final kit seal. The kit changes after `b4bf958c` touched only kit files, never the candidate.
2. **Superseded kit-chain results**, kept in `kitseal2_superseded/` and `kitseal3_superseded/`:
   - On `b4bf958c`, `mutate_r6c2` was 20/24: N02/N03/N18/N24 were masked by sibling layers. I fixed this by splitting the `finalized_at` check and asserting each layer's own FAIL line or STOP reason.
   - On the next seal, R3 was 269/2. The first failure was `common.sh`'s pinned upstream hash, which I fixed by moving the K04 helpers into `kit_r5.sh` and restoring `common.sh` byte-identical. The second was the KILL-timing case "waiting verifier + hung verify_off". It touches only KILL/watcher code, which R6-C2 does not change. It ran under heavy memory pressure and passed on the final seal; I did not reproduce it.
   - One `test_kit_r6c1` run (71/13) is void because I edited the kit while it ran.
3. **Deliberate tightenings beyond the minimum.** Each one is strict-direction and pinned:
   - (a) the availability lexicon with a day ("Tenemos disponibilidad el jueves.") follows the date rule;
   - (b) a confirmed booking without a booked revision carrying a date and time (for example stage BOOKED only) restates no day or time. The reviewer's DD13 probe seeds exactly that and would now see a fallback, as rule 9 requires;
   - (c) a booked window's end must be start + `schedule.SERVICE_MINUTES` (45). An end the business never booked is refused;
   - (d) declared ISO dates are validated;
   - (e) the SCHEDULING producer gate.
4. **Not changed (residual):**
   - A NEGATIVE availability statement ("No tengo disponibilidad el jueves a las 18:00") is still allowed after any evaluation without a date comparison. This is the existing L4.7D rule, and it asserts nothing is available.
   - CE's own deterministic slot listings spell "hoy/mañana" from the container clock (`date.today()`). The shipped compose sets `TZ`, so this is consistent, and those listings carry the date of the same evaluation.
5. **Reachability.** The SCHEDULING producer gate (D13) is reached in production only when ScheduleService raises during that turn. It is proven by direct unit and static tests, not by the full-path harness.
6. **Mutation equivalents removed:** two, as described in §9. C05 was re-anchored rather than dropped.
7. **Permission.** The first attempts to create the kit and the runtime in one command were denied twice. I created them in separate steps instead: the kit by a plain copy of the sealed R6-C1 kit, and the runtime by the R6-C1 mechanism (copy inputs and snapshot, `manifest`, `cmp` with R6-C1, `verify`).
8. **Resources.** There were 47 heavy jobs, one at a time, and each started at ≥ 1603 MiB through the 1536 MiB gate. Long waits came from other sessions' memory use. The lowest reading during a job was 525 MiB (seal-1 full candidate run), and there were 0 ABORTs. Disk stayed above 23 GiB.
9. **Inherited.** The pin-gate refusal wording still says "this R6 kit".

---

## 15. W-3, WILD and next milestone

**W-3 remains OPEN. The final WILD remains NOT RUN.** No handset test, window, tester reset or live reset was performed. The real R6-C2 kit is **PROVISIONAL**, and every live ENABLE, RESET_BETWEEN and tester reset refuses at the pin gate. The R5 kit remains the live emergency instrument for the f16b1f3 deployment. Commit, push, image build, pin finalization, dark deployment, windows and the WILD remain **NOT AUTHORIZED** until the next review is READY.

**Next milestone: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C2-FINAL-FOCUSED-INDEPENDENT-REVIEW.**

STOP. This closeout is not staged or committed.
