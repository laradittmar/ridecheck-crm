PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C2-FINAL-FOCUSED-INDEPENDENT-REVIEW
# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — FINAL CANDIDATE CONVERGENCE R6-C2 — FINAL FOCUSED INDEPENDENT REVIEW — AUDIT 001

Date: 2026-11-12 (review runs 13:34Z–16:07Z)
Reviewer: fresh independent reviewer. I did not implement R6, R6-C1 or R6-C2.
Source candidate: `/opt/ridecheck-crm-r5-f1-response-validation`, branch `fix/prelaunch-r5-f1-response-validation-boundary`, base = HEAD `f16b1f328f3fbf1050afdcec583d252d528c72bf`.
R6-C2 kit: `/opt/ridecheck-builds/crm-minimum-launch-enablement-kit-R6-C2-20261112T041634Z`, provisional anchor `8e2254a65c2106e90eb1d71b44a8af4c07a44598d34e4b8d70315ed770af7c27`.
R6-C2 private runtime: `/opt/ridecheck-builds/crm-minimum-launch-enablement-PRIVATE-R6-C2-20261112T041634Z`.
R6-C2 evidence (read only): `/opt/ridecheck-builds/crm-final-convergence-r6-c2-20261112T035715Z`.
Reviewer evidence (mode 0700): `/opt/ridecheck-builds/crm-r6-c2-final-review-20261112T133452Z/`.
Rehearsal directory (the only write into a private runtime): `PRIVATE-R6-C2-20261112T041634Z/run-r6c2-final-review-20261112T155329Z/`.

Authoritative inputs, read completely:
- the R6-C2 closeout (`…R6-C2_CLOSEOUT_001.md`);
- the R6-C1 focused integrated review audit (`…R6-C1-FOCUSED-INTEGRATED-INDEPENDENT-REVIEW_AUDIT_001.md`), findings and correction section.

# VERDICT: CORRECTION REQUIRED

R6-C2 fixes every day/date form it enumerates, and the rest of the candidate and kit is sound. The required red control is clean: base, R6 and R6-C1 deliver the wrong day 48/48 times, R6-C2 0/48, and R6-C2 alters 0 of 44 correct-day controls. It is clean in all four postures and on both SQLite and PostgreSQL.

The day/date binding still **fails open** for natural forms outside that list. Those replies go out verbatim, as AI_COMPOSED with no finding, through the real production path.

**R6C2RV-S01 (HIGH, blocking, source).** A matching time on a wrong date is still delivered when the date is written in a form the resolver does not fully read. This is the R6C1RV-S01 harm itself:
- **(a) weekday plus day-of-month.** "Te esperamos el lunes 23 de 10:00 a 11:00." is delivered while Mon 16 was evaluated. Likewise "el jueves 19 a las 10:00" while Thu 12 was evaluated, and the booking restatement "Tu turno es el lunes 23 a las 10:00." while the committed date is Mon 16. Only the weekday is compared and the number is discarded. CE's own customer parser (unchanged since base) resolves the customer's "el lunes 23" to **Mon 16** and "el jueves 19" to **Thu 12**. Reply rule 4 tells the model to confirm the day the customer named. So the chain "the customer asks for the 23rd, ScheduleService evaluates the 16th, the model echoes 'el lunes 23' with the 16th's times" is realistic.
- **(b) unrecognised date forms are "no anchor".** The day-blind R6-C1 endpoint rule then applies: "el 19", "19-11", "19.11", "19 nov", "noviembre 19", "19 de nov.", a datetime string such as "2026-11-19T10:00", "el finde", "la semana que viene".
- **(c) bare weekday beyond the next occurrence.** "Te esperamos este lunes a las 10:00." is delivered when the evaluated Monday is the 23rd (today Wed 11), so the customer reads the 16th.
- **(d) times not in `HH:MM`.** "Te esperamos el jueves a las 10." (also "10 hs", "10am") is delivered while Monday was evaluated. `_TIME_TOKEN_RE` reads only `H:MM`, so the sentence is not an availability proposition and its stated day is never checked. This gap predates R6-C2 (L4.7D), but it defeats the same rule.
- **(e) day stated in another sentence.** "Para el jueves 19/11. Te esperamos a las 10:00." is delivered. The anchor sentence has no time, and the time sentence has no anchor.

Reproduced on the full production path (handle_message → CE → reply model → admission → `_send_text_to_wa` → envelope → `plan.compose` → OutboundSafetyGate → Meta recorder):
- 8 reviewer forms × 4 postures = **32/32 delivered** on R6-C2;
- byte-identical on SQLite, on PostgreSQL 16.13 and on PostgreSQL with session `TimeZone=Pacific/Kiritimati`.

This fails the verdict rule's "every stated day/date is checked" and "matching time on the wrong date is refused".

Everything else checked holds:
- identity and pins reproduce;
- the canonical-date authority is coherent: one evaluation, typed, business timezone, no alternate facts path;
- the confirmed booking reads only the committed revision;
- timezone rollover is correct and DB-session independent;
- S02, P01, T01 and R5-F1 stay closed;
- regression is NEW 0 with only the sentinel GONE;
- staged PostgreSQL is NEW 0 / GONE 0;
- source mutants 23/23 killed;
- the sentinel is green and effective;
- K01–K05 are closed and K06 is closed, apart from one LOW precision item;
- the kit suites are all green;
- the rehearsal passes 94/0;
- live is unchanged.

Nothing was staged, committed, pushed, built, finalized, deployed or migrated. No flag, allowlist or live data changed. Meta was not contacted and nothing was sent. The real R6-C2 kit remains PROVISIONAL and byte-identical. **W-3 remains OPEN. The final WILD remains NOT RUN.**

---

## 0. Summary

| Area | Result |
|---|---|
| Identity | HEAD = merge-base = f16b1f3; 0 commits; 0 staged. 16 changed paths, every sha256 = closeout §2. Worktree = sealed `cand/` + the post-seal R6-C2 closeout. Tree / changed / app pins reproduce byte-for-byte (680 / 16 / 137). Kit verifies; CHECKSUMS = PROVISIONAL_CHECKSUMS = `8e2254a6…7c27` (125); PROVISIONAL; 3 keys UNFINALIZED |
| R6C1RV-S01 | **PARTIALLY FIXED.** Every enumerated form is bound (W01–W12 refused 48/48). **Not closed:** R6C2RV-S01 (a)–(e), 32/32 delivered |
| Canonical authority | Coherent: `availability_date` from the same evaluation; every slot write writes the date; one `CanonicalFacts` site; fail-closed defaults |
| Timezone | Business date = Buenos Aires; DB session never consulted (Kiritimati run byte-identical). Container `TZ` pinned by compose and bound by the config hash: non-blocking |
| Confirmed booking | Committed revision only (Y12a/Y12b/Y13 killed), except (a): "lunes 23" restated for a Mon-16 booking is delivered |
| Preserved boundaries | S02, P01, T01, R5-F1, C01, P02/P03/Q01 unchanged (suites green on SQLite and PG; mutants; rehearsal RR10) |
| SQLite regression | Focused NEW 0 / GONE 1 (sentinel), full NEW 0 / GONE 1 (sentinel); changed 0; skip changes 0 |
| PostgreSQL (staged) | 29 suites 713 = 713, NEW 0 / GONE 0; R6-C2 PG 67 passed / 351 subtests; R6-C1 + corpus + contract 71 / 1467; R5-F1 26/26, 0 outstanding questions |
| Source mutants (mine) | **23/23 KILLED** (control 257 passed / 2166 subtests) |
| SB1 sentinel | Green; red on a one-line drift in each of 6 frozen files; assertion code unchanged |
| K03 / K04 / K05 | Closed |
| K06 | Closed for its stated posture. **R6C2RV-K07 (LOW):** the block says RESTORED with a foreign symlink/directory present; FINALIZE still refuses (fail-closed) |
| K01 / K02 | Retained (R3/R6/R6-C1/R6-C2 suites, kit mutants) |
| Kit suites | R6-C2 81/0 · R6-C1 84/0 · R3 271/0 · R5 242/0 · R6 112/0 |
| Kit mutants | `mutate_r6c2` 24/24 · `mutate_r6c1` 28/29 (Q21 masked; killed by K82 in my own run) · `mutate_r6` 29/34 (R25/26/27/29/31 masked; killed as Q25–Q29) |
| Rehearsal | **94 passed, 0 failed** (independent run) |
| Live | before (13:35:19Z) = after (16:06:19Z) except the R6-C2 private runtime gaining exactly the rehearsal directory |

---

## 1. Identity and scope

**Git.** `rev-parse HEAD` = `merge-base HEAD f16b1f3` = f16b1f3. `rev-list --count f16b1f3..HEAD` = 0, and `diff --cached` is empty. Tracked docs/Launch modified vs base: 0.

**Changed paths (16, outside docs/Launch).** I reproduced every sha256 independently, and every one equals the closeout §2 table:
- `conversation_engine.py` `cc07f6ae…`;
- `reply_claim_shape.py` `b2f3c108…`;
- `response_validator.py` `8631fc62…`, blob `7f3c2a28…`;
- `r6_zone_names.txt` `5994ade3…`;
- the four modified tests `53bee6e5…` / `7de06434…` / `9b0a3bed…` / `de6b5d2f…`;
- the eight new tests `ed191b47…` / `d4da3808…` / `c489b1d5…` / `ac5e45ae…` / `a32d35a2…` / `060daa65…` / `c59fe151…` / `17d18b17…`.

The path/status set equals `pins/changed_paths.manifest` (16 rows).

**Seal.** `git ls-files -co --exclude-standard` gives 727 files. That is the sealed `cand/` (726) plus the R6-C2 closeout, with 0 content differences. My `git archive f16b1f3` manifest = `7282f596…` = the sealed `base/`.

**Pins recomputed from my own export of the worktree**, each byte-equal to the kit's manifest and to `config/pins.conf`:
- whole tree outside docs/Launch, 680 files: `42230f49…0c07`;
- `backend/app`, 137 files: `e30fd8be…d4ca`;
- changed paths: `d559f3fb…6995`.

**Kit.**
- `sha256sum -c` passes with 125 entries.
- `sha256(CHECKSUMS)` = `sha256(PROVISIONAL_CHECKSUMS)` = `8e2254a65c2106e90eb1d71b44a8af4c07a44598d34e4b8d70315ed770af7c27`, and the two files are `cmp`-identical.
- The file set equals the listing plus the two manifests, with 0 links and 0 hidden files.
- `PIN_STATE=PROVISIONAL`; `EXPECT_GIT_SHA`, `SOURCE_COMMIT` and `EXPECT_IMAGE` are `UNFINALIZED`.
- `FINAL_TAG` is unchanged.

**Kit diff vs R6-C1.** Only expected files differ:
- path re-pointing in 11 configs;
- pins;
- VERIFY (K03), `kit_r5.sh` (K04), the umask lines (K05) and the runbook §4a (K06);
- FINALIZE's evidence header;
- the docs;
- the new `test_kit_r6c2.sh` and `mutate_r6c2.py`.

`common.sh` is unchanged.

**Predecessors.** The R3/R4/R5/R6/R6-C1 kits and the R3–R6-C1 private runtimes verify, and their whole-tree hashes are identical before and after this review (§14). The R6-C1 kit tree `be600fafc5d977f1` equals the R6-C1 review's value.

**Tests.** No test was deleted, skipped, xfailed or weakened:
- 0 skip/xfail markers were added.
- The only removed assertion vs base is R6's reviewed tightening of `test_validation_failure_never_breaks_a_turn`, carried unchanged.
- R6-C2's changes to existing tests seed a canonical date and add refusal assertions (`test_val_avail_01`, `test_cut_20`, the R6-C1 `SLOTS` world, `test_s01_07`). The SB1 diff is one dictionary value plus a comment.

**Secrets.** The closeout's secret counts were not contradicted. My probes print no customer value.

**INFO R6C2RV-I01.** The worktree holds 4 git-ignored `__pycache__/*.pyc` files under `backend/app`, written at 04:01Z during R6-C2 work and not disclosed. They cannot reach the commit (ignored) or the image: the runbook builds from `git archive $COMMIT`, and FINALIZE/VERIFY exclude `*.pyc` and `__pycache__` from the app manifest. I left them untouched (no write authority).

**Identity: no drift.**

---

## 2. Canonical availability contract (implementation read)

| Requirement | Ruling | Evidence |
|---|---|---|
| Typed `availability_date` | **Holds** | `CanonicalFacts.availability_date: Optional[date]`. The validator honours only `type(x) is date`, and `canonical_date()` accepts only an exact `date` or a strict `YYYY-MM-DD` str (Y17a/Y17b killed) |
| Same evaluation as the slots | **Holds** | `_canonical_availability_date` returns the date only when `active_requested_date` is strict ISO, `last_offered_slots` is a list, every canonical slot is in that list, and the date is not before business today (Y02b/Y09b/Y09c killed) |
| Visible slots never paired with another evaluation's date | **Holds** | The subset check guarantees every accepted (date, time) pair is in that date's own ScheduleService answer. This covers the R3/D revoke path, which restores an older `last_visible_slots` while keeping the new date: the restored list is paired only if it is a subset of the new date's evaluation, which is then true |
| Every `last_offered_slots` write also writes the date | **Holds** | Read sites CE 13183/13185, 13314/13316, 13633/13635, 13644/13646, 13937/13938, 13993/13995, 14949/14951. `_dispatch_booking_flow_for_day` (13733) writes the date + visible list only, but all four callers write `last_offered_slots` for the same day first, and `_handle_period_request` filters the existing evaluation. Family resets in `offer_lifecycle` / `pending_correction` clear the fields together, and a partial clear can only fail closed |
| Absent/invalid/stale/contradictory date fails closed | **Holds** for the enumerated anchors | §3 M-cases; Y08/Y09a killed |
| No canonical date from prose | **Holds** | The builder reads state only; `_ai_time_day_is_evaluated` reads prose only to refuse storing a time (Y15 killed) |
| DB session timezone not the authority | **Holds** | §5 |
| One business timezone | **Holds** | `response_validator.BUSINESS_TIMEZONE` = `schedule.BUSINESS_TIMEZONE` = `America/Argentina/Buenos_Aires` |
| `business_today` captured once per snapshot | **Holds** | The builder computes `today` once and passes it into the facts; the validator prefers it |
| Confirmed booking uses the committed revision | **Holds**, except R6C2RV-S01(a) | §6 |
| BOOKED without a revision authorises nothing | **Holds** | `(None, ())`, so any day or time is refused |
| A new request replaces date and slots together | **Holds** | write-site table above |
| No alternate path restores day-blind behaviour | **Holds** | exactly one `CanonicalFacts(` construction (CE 12166) and one `validate_envelope` call site (CE 12290); a builder exception yields `safe_failure_text` |

The defect is not in the authority. It is in **reading the day from the prose** (§3).

---

## 3. Day/date resolution — R6C1RV-S01 disposition

**Unit probe** (`probe/unit_probe.py` `b6d8c824…`, pure validator, evaluated Mon 16, today Wed 11, slots 10:00/11:00/15:00/15:30). 61 of 83 cases match my expectation, and every miss is a delivery that should have been refused.

**Correct** (refused when wrong, kept when right):
- ISO, DD/MM/YYYY, DD/MM/YY, DD/MM;
- month name with and without a year;
- "el día N";
- hoy / mañana / pasado mañana, and "mañana por la mañana";
- definite and bare-transactional weekdays;
- impossible dates (31/11, 16/13, a 3-digit year, 30 de febrero, día 40);
- two different dates; one valid and one malformed anchor;
- anchor with no canonical date; stale date;
- list formatting with the date on the first line (refused);
- booking: right date and window kept, wrong date refused, wrong start refused, invented end refused, no revision refused.

**Delivered although wrong** (R6C2RV-S01):

| Id | Reply | Evaluated | Why |
|---|---|---|---|
| X01/X02 | "el lunes 23 a las 10:00" / "… de 10:00 a 11:00" | Mon 16 | weekday matches; "23" discarded |
| X34 | "el jueves 19 a las 10:00" | Thu 12 | same |
| B06 | "Tu turno es el lunes 23 a las 10:00." | booked Mon 16 10:00 | same, booking branch |
| X03 | "el 19 a las 10:00" | Mon 16 | not an anchor; endpoint rule |
| X04/X05/X06/X17/X18 | "19-11", "19.11", "19 nov", "noviembre 19", "19 de nov." | Mon 16 | not anchors |
| M04 | "el 2026-11-16T10:00 …" (and any wrong datetime string) | Mon 16 | `\b` fails before `T`; not an anchor |
| X13/X14 | "el lunes" / "este lunes a las 10:00" | Mon 23 | weekday-only comparison, no horizon |
| X19/X20 | "la semana que viene", "el finde" | — | not anchors |
| X07/X08/X09/B07 | "el jueves a las 10" / "10 hs" / "10am" | Mon 16 | no `H:MM` token, so no availability proposition |
| X10/X26 | "Para el jueves 19/11. Te esperamos a las 10:00." / "El jueves te esperamos. Horario: 10:00." | Mon 16 | day and time in different sentences |

**Ruling on R6C1RV-S01:** fixed for every enumerated form, **not closed** (R6C2RV-S01).

---

## 4. Full-path red control (reviewer harness, four trees)

`probe/red_rv.py` (`4267f4c7…`) runs one harness copy, taken from my export of the R6-C2 worktree, against each mounted tree. It uses `run_case` of `test_r6_c2_day_date_production_path` with the interlock armed, in 4 postures (direct webhook false/true × Smart Booking false/true). Times in W/X cases are evaluated slots of the evaluated date.

| Tree | W wrong date (12 × 4) delivered | K correct day (11 × 4) kept verbatim | X reviewer forms (8 × 4) delivered |
|---|---|---|---|
| f16b1f3 | **48/48** | 40/44 (K11 general hours altered: the original prose defect) | 32/32 |
| R6 | **48/48** | 44/44 | 32/32 |
| R6-C1 | **48/48** | 44/44 | 32/32 |
| **R6-C2** | **0/48** | **44/44** | **32/32** |

The W cases are:
- Mon → "el jueves";
- 14 Nov → "20 de noviembre";
- today → "mañana";
- "19/11";
- a single time on the wrong weekday;
- a question form;
- pasado mañana;
- "el lunes 23/11" (same weekday, wrong date);
- "16/11 o 19/11" (two anchors, one matching);
- no canonical date;
- a stale date with current slots;
- a malformed second anchor.

The K cases are:
- the correct weekday, explicit date and numeric date;
- hoy, mañana and pasado mañana;
- a single time and a question form;
- a booking restating "lunes 16/11 de 10:00 a 10:45";
- a no-anchor window;
- general hours.

The X cases are X01, X02, X03, X04, X05 (this-Monday when the evaluation is Mon 23), X06 (booking "lunes 23"), X07 ("19-11") and X08 (day in another sentence).

**R6-C2 refused W cases (48/48):**
- exactly one message, the `VALIDATION_FALLBACK` with AVAILABILITY refused;
- `preferred_time` and `preferred_day` None, 0 revisions;
- no outstanding question, no pending vehicle.

The X cases were delivered as `AI_COMPOSED` with no finding, and stored no state.

Output sha16:

| Run | sha16 |
|---|---|
| base SQLite | `3ff0ca6e58c77383` |
| R6 SQLite | `023e349ca34b1635` |
| R6-C1 SQLite | `023e349ca34b1635` |
| R6-C2 SQLite | `77ace662caf77094` |
| R6-C1 PostgreSQL | `023e349ca34b1635` (= SQLite) |
| R6-C2 PostgreSQL | `77ace662caf77094` (= SQLite) |
| R6-C2 PostgreSQL, session `TimeZone=Pacific/Kiritimati` | `77ace662caf77094` (= SQLite) |

---

## 5. Timezone ruling — NON-BLOCKING

- The validator's business date is `ZoneInfo("America/Argentina/Buenos_Aires")` applied to an aware UTC instant (`_utcnow`).
- The R6-C2 unit and full-path rollover suites pass on SQLite and PG: UTC midnight crossed while BA is still Wed; BA midnight; hoy / mañana / pasado mañana and the weekday on each side; stale after BA midnight. Mutant Y05 (UTC business date) is killed.
- Month-end and year-end use `date + timedelta`, which rolls over correctly; "el día N" searches a 62-day horizon from business today.
- The DB session timezone has no effect: identical output under `Pacific/Kiritimati` (§4).

**Disclosed `date.today()`.** CE resolves the customer's relative days for the ScheduleService evaluation (CE 8923/9127/9133/13583/13912) and its deterministic listings (`_format_date_human`) from the container's local clock. TZ is pinned:
- `docker-compose.yml` and `docker-compose.beta.yml` set `TZ: "America/Argentina/Buenos_Aires"`;
- the kit's ON/OFF overlays layer on top of them and do not override it;
- ENABLE/preflight bind the running config hash to the OFF render;
- the live backend reports `TZ=America/Argentina/Buenos_Aires`, offset −03:00, and the slim base image carries tzdata.

If TZ ever diverged, the validator would still refuse a relative day that disagrees with the evaluated date, so the result is fail-closed. **Non-blocking.** The kit's `SCHEDULE_TZ` check compares the constant, not the process's local zone. Recommended hardening, not required: add a read-only preflight line asserting the container's `TZ`.

---

## 6. Confirmed booking ruling

| Case | Result |
|---|---|
| booked revision, correct date / start / end ("lunes 16/11 de 10:00 a 10:45") | kept (K09 4/4; unit B01/B08) |
| wrong date ("jueves 19/11") | refused |
| wrong start (11:00) | refused |
| invented end (10:00 a 11:00; the end must be start + `SERVICE_MINUTES` = 45) | refused |
| stage BOOKED, no revision | refused (B05) |
| last requested date ≠ committed date | the committed date governs (Y12a/Y12b killed) |
| `current_revision_id` → non-booked revision | `(None, ())`, restates nothing (code read; `_COMMITTED_REVISION_STATUSES`) |
| `completed` revision | treated as committed (intended) |
| **"Tu turno es el lunes 23 a las 10:00."**, booked Mon 16 | **delivered** (B06, X06 4/4), which is R6C2RV-S01(a) |
| "Tu turno es el jueves a las 10." | **delivered** (B07), which is R6C2RV-S01(d) |

---

## 7. Preserved R6 / R6-C1 boundaries

All of the following hold:
- ordinary prose is non-claiming (K11 and the R6 corpus);
- S02 lowercase and sentence-initial wrong vehicles are refused, and genuine "Uno" is safe;
- P01 question-form price, slot, window, vehicle and location are refused, and clarifications are allowed;
- exact-type provenance holds;
- no state and no outstanding question come from prose alone (§4: 0 state on all 124 R6-C2 cases);
- the R5-F1 motorcycle handoff is truthful (PG 26/26, 0 outstanding questions; rehearsal RR10 W2-6 MET);
- the C01 fallback copy is exact (all refusals are the pinned fallback text);
- P02/P03/Q01 have not expanded.

The evidence is the suites inside my focused/full runs and the PG runs (R6 contract, R6 corpus, R6-C1 S01/S02/P01 unit + production path, R5-F1), plus my mutant control (257 passed / 2166 subtests). No owner ruling is reopened.

---

## 8. SQLite and PostgreSQL

The SQLite runs used `ridecheck-crm-test:l4.7b2`, `--network none`, read-only trees and my own junit comparison:

| Run | Base f16b1f3 | R6-C2 | Comparison |
|---|---|---|---|
| Focused (72 files; 64 on base) | 18 F / 2175 P / 361 S (2554) | 17 F / 2403 P / 361 S (2781) | **NEW 0, GONE 1, COMMON 17, changed 0, +227/−0, skip changes 0** |
| Full `../tests` | 62 F + 9 E / 7489 P / 786 S (8346) | 61 F + 9 E / 7718 P / 786 S (8574) | **NEW 0, GONE 1, COMMON 70, changed 0, +228/−0, skip changes 0** |

The only GONE is `UnchangedContracts::test_sb1_20_frozen_files_unchanged`, red on base and green on the candidate: the authorized sentinel.

**PostgreSQL, properly staged** (`pgstage.sh` `5879a289…`):
- a disposable `postgres:16` 16.13 on tmpfs, on an `--internal` network, `TimeZone=Etc/UTC`;
- one template per tree: `create_all` from the pre-schema record, `stamp 20261006_clarification_attempts`, `upgrade head`, giving `20261104_vehicle_identity` on both;
- each suite on its own fresh clone, with all 19 `*_PG_URL` set.

| Item | Result |
|---|---|
| 29 PG suites | base 2 F / 598 P / 113 S (713) = cand: **NEW 0, GONE 0, changed 0, skip changes 0** |
| SB2B with a clean DB | 46 P / 1 F / 1 S on both (`alembic_version` staging artifact) |
| R6-C2 day/date unit + production path on PG | **67 passed, 351 subtests**; real rows: 268 threads, 20 booked revisions, 0 outstanding questions, 13 distinct evaluated dates |
| R6-C1 frames path + R6 corpus + contract on PG | **71 passed, 1467 subtests** (1314 threads) |
| R5-F1 production path on PG | **26/26**, outstanding questions 0 |
| Red probe | PG = SQLite byte-for-byte (§4) |

The 2 common failures (`test_p2_pg_00` stale head assertion; `test_rv03_pg_13` old tester-reset helper) and the 113 `…_URL not set` lineage skips are identical on both trees, and are the prior reviewers' known harness items. Teardown left 0 containers and 0 networks.

---

## 9. Source mutations (reviewer-authored, 23)

Each mutant is one exact, unique replacement, bind-mounted read-only over my export. The suite runs with `-x` and starts unit-first: R6-C2 unit, R6-C1 unit, l4_7d, l4_7c_3b, R6 contract, then the R6-C2 production path, the R6-C1 production path, the R6 corpus and the R5-F1 production path. The control passes 257 tests / 2166 subtests.

| Id | Weakens | Result |
|---|---|---|
| Y01 | canonical date ignored | KILLED |
| Y02a / Y02b | canonical date never read (validator / builder) | KILLED / KILLED |
| Y03 | explicit date compared by weekday only | KILLED |
| Y04 | DD/MM month/day ignored | KILLED |
| Y05 | business today from UTC | KILLED |
| Y06 | mañana treated as hoy | KILLED |
| Y07 | pasado mañana treated as mañana | KILLED |
| Y08 | missing date accepted | KILLED |
| Y09a / Y09b / Y09c | stale date accepted (validator / builder); visible slots of another evaluation paired with the date | KILLED ×3 |
| Y10 | single-time day check bypassed | KILLED |
| Y11 | question-form day check bypassed | KILLED |
| Y12a / Y12b | booking uses the requested date (validator / builder) | KILLED / KILLED |
| Y13 | booking may restate any time | KILLED |
| Y14 | endpoint-only comparison restored | KILLED |
| Y15 | AI preferred time ignores its day | KILLED (static producer-gate test; behaviour covered by the gate's unit tests — closeout disclosure 5) |
| Y16 | declared dated AVAILABILITY claim not checked | KILLED |
| Y17a / Y17b | datetime accepted as a date (`canonical_date` / facts) | KILLED / KILLED |
| Y18 | malformed second anchor ignored | KILLED |

**No surviving high-risk mutant.** The supplied 50/50 is consistent with this. As with R6C1RV-S01, the defect in R6C2RV-S01 is shipped behaviour, so no mutant can expose it.

---

## 10. SB1 sentinel

- **Green** on the candidate.
- `response_validator.py` `5e5690ee…` → `7f3c2a2821f956ec0f446aa547d7f58d0fd00505` (= the worktree blob), with a comment naming PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C2.
- The test diff vs base is dictionary values and comments only; `test_sb1_20_frozen_files_unchanged` is unchanged.

**Effective.** A one-line drift bind-mounted over each file gave `1 failed` for:
- `response_validator.py`;
- `claim_projection.py`;
- `flow_data_exchange.py`;
- `whatsapp.py`;
- `schedule.py`;
- `pricing.py`.

Restored, it gives `1 passed`. The R6-C2 correction will need one more re-record, with an authorization comment.

---

## 11. Kit hardening K03–K06; retained K01/K02

**Relocated kit.** The kit suites ran from a relocated, re-sealed scratch copy of the R6-C2 kit (`kit/relo`, scratch anchor `1d199bd1…`). Its 17 path-bearing files were rewritten to point at a scratch copy of the private runtime (`kit/priv`), and its entry set is identical to the real kit's. The real kit was only read.

**K03 — CLOSED.** VERIFY now checks:
- the strict line set (the header plus the 11 fields; anything else fails);
- `source_repo` = `PIN_SOURCE_REPO`;
- the `finalized_at` form, its date round-trip, and not-future (300 s);
- each field exactly once.

The runbook §5 and README say the same. K82 is green inside 81/0. I ran the R6-C1 Q21 mutant ("each field exactly once" removed) against K82 myself: **killed** (both duplicate-field cases are VERIFIED, i.e. caught).

**K04 — CLOSED.** `dispatch_off_verified` captures the whole output, requires producer rc 0, exactly one `booking_dispatch_allowed=` line and exactly `=False`. It is used by the ENABLE premutation re-check, RESET_BETWEEN and reset_testers. No production script keeps `interlock | grep`. K83 (SIGPIPE red control, True / missing / duplicate / malformed / producer failure) is green.

INFO: ENABLE's earlier saved pre-check (`interlock > interlock_before.txt`, no `set -e`) applies the same text rule but does not record the producer's rc. The authoritative under-lock re-check before the first mutation does, so no admission path was loosened.

**K05 — CLOSED.**
- `umask 077` is the first executable statement in ENABLE, RESET_BETWEEN and reset_testers.
- On the relocated real configurations under caller umask 022, ENABLE W2/H, RESET_BETWEEN AB/BC execute and reset_testers W1A execute all exit 2 at the pin gate with **0 docker calls**.
- 14 new files are all 0600 and 4 new directories all 0700, and `private_runtime.sh verify` gives **PRIVATE RUNTIME VERIFIED**.

**K06 — CLOSED for the posture it names; one LOW item.** I ran the runbook's own block on fresh scratch copies:
- **RESTORED, byte-identical to the sealed kit:**
  - clean;
  - evidence published;
  - evidence + CHECKSUMS published;
  - the `.tmp` publication files.
- **STOP, nothing changed:**
  - PIN_STATE FINAL;
  - a foreign regular file;
  - a sealed file edited;
  - pins.conf edited;
  - PIN_STATE duplicated.

The block forbids manual FINAL, restores only from sealed copies, verifies the anchor, checksums and the regular-file set, and requires FINALIZE from §4's start.

**R6C2RV-K07 (LOW, fail-closed).** With a foreign **symlink** or a foreign **directory** present, the block's `find -type f` checks do not see it. It prints `RESTORED: the sealed provisional kit` (rc 0) and leaves the link or directory in place. FINALIZE run afterwards refuses with exit 2 (`the kit contains a link or special file` / `an unlisted directory`), writes nothing and makes 0 docker calls, so the gate stays closed. The prompt's "stops without changing anything on an unexpected posture" is not met for these two postures, and the RESTORED claim is inaccurate.

**K01/K02 — retained.** These suites are green on the R6-C2 kit:
- R3 271/0, R5 242/0, R6 112/0 and R6-C1 84/0, which carry the transactional finalization, exact restoration, gate-closed-after-failure and deep commit/tree/blob/image verification cases (K74–K81);
- `mutate_r6c1` Q25–Q29, which kill each masked R6 layer on its own.

The real R6-C2 kit stayed PROVISIONAL and byte-identical (treehash `f692f4cc/96485bfeb7596d29` before and after).

---

## 12. Kit suites and mutations (relocated kit, serial, gated)

| Job | Result |
|---|---|
| `test_kit_r6c2.sh` (K82–K86) | **81 passed, 0 failed** |
| `mutate_r6c2.py` | **24/24 KILLED** |
| `test_kit_r6c1.sh` | **84/0** |
| `mutate_r6c1.py` | **28/29**: Q21 survives in K78 only, and is **killed by K82** (my run, above) |
| `test_kit.sh` (R3) | **271/0** |
| `test_kit_r5.sh` | **242/0** |
| `test_kit_r6.sh` | **112/0** |
| `mutate_r6.py` | **29/34**: R25/R26/R27/R29/R31 are masked in K74 by the stronger independent layers. Each layer is killed on its own as Q25–Q28 (FINALIZE parent, whole tree, image `/app/app`, launch docs) and Q29 (VERIFY pins.conf outside the four keys). The outcome stays fail-closed, so this is acceptable |

My first chain run of the three mutation suites aborted before testing anything (`FileExistsError`: I had pre-created their output directories). The table is the rerun with fresh directories.

---

## 13. Independent rehearsal — PASS

`R6_CANDIDATE_TREE=<my export> bash <real sealed kit>/tests/rehearse_r6.sh PRIVATE-R6-C2/run-r6c2-final-review-20261112T155329Z`. Result: **REHEARSAL R6: 94 passed, 0 failed** (stdout sha16 `57ffac52…`).

It ran on its own `--internal` network, with PG restored under `TimeZone=Etc/UTC`, a real uvicorn and the daemons, and the candidate `backend/app` mounted read-only. It showed:
- RR11: the order H → H2 SKIPPED → W1A → RESET_AB → W1B → RESET_BC → W2 → final KILL; exactly one OPENED/CLOSED pair per executed kind; the ledger chain verifies.
- RR3/RR5/RR7/RR10: each CLOSED once with `criteria_not_met=0`, then ACCEPT.
- RR6/RR8: reset isolation; non-tester rows identical.
- RR13: local-wall-clock booking protection (missed by the R5 predicate, counted by the R6 helpers).
- RR10:
  - the truthful motorcycle handoff constant;
  - **no outstanding question** on the W2 thread;
  - no offer, booking, notice or job, and nothing pending.
- RR11: all eight switches literally false and `booking_dispatch_allowed()` False (env and a fresh process).
- RR12: live unchanged.
- No disposable container or network remained.

---

## 14. Live isolation (read-only)

Probe: `live/probe.sh`, sha16 `dee61ae1…`. It is the R6-C1 reviewer's probe plus R6-C2 kit and runtime verification, whole-tree hashes for every kit and runtime, the candidate worktree status, pending notices and jobs, and the backend TZ. Before was taken at 13:35:19Z and after at 16:06:19Z. No customer value was printed.

| Item | Before = after |
|---|---|
| backend / postgres / n8n | `04e14e5770f9` (image `sha256:1b8ed611…`, restarts 0), `5246fd86d829`, `6ea80570b67b`; start times unchanged |
| GIT_SHA | f16b1f3 |
| schema head | `20261104_vehicle_identity`; column and constraint md5 unchanged |
| switches | all eight literally false; `booking_dispatch_allowed=False` |
| allowlist | sha16 `ad98b5c31a4e8ed8` |
| DB fingerprint | `fbae2abdc3371f9146f0ac51a2ffa427` |
| max ids, pending outbound (0), pending notices (0), recovery jobs | unchanged |
| compose, `.env`, beta, probe-off, pin-candidate, Flow files | hashes unchanged |
| R3/R4/R5/R6/R6-C1/R6-C2 kits | verify OK; tree hashes unchanged; R6-C2 PROVISIONAL |
| R3/R4/R5/R6/R6-C1 private runtimes | verify OK; unchanged |
| worktrees, refs, images, volumes | unchanged |

**Single difference:** the R6-C2 private runtime grew from 450 to 882 files. All 432 new files are inside the rehearsal directory, 0 new files are outside it, and `PRIVATE_MANIFEST` still verifies. Besides that, the only other writes were this audit and the reviewer evidence directory.

**Reviewer disclosures.**
1. **Resources.** Eight heavy jobs ran one at a time, each started at ≥ 1545 MiB through a gate requiring ≥ 1536 MiB. The lowest reading during a job was 645 MiB (full SQLite), and there were 0 ABORTs.
2. **Ungated light jobs.** These ran without the gate: the pure-validator unit probe, the CE-parser probe and the Q21-vs-K82 shell run (single small processes), and the K06 shell probe.
3. **Implementer watchdog.** The implementer's `heavy.sh` watchdog kills only `c1-*` containers, while its runner names its container `c2-sq`. Its 500 MiB abort could therefore not have fired. The closeout reports 0 ABORTs and a lowest reading of 525 MiB, so nothing was affected; this is evidence hygiene only.
4. **Scratch copies.** The scratch kit and private-runtime copies remain under the 0700 reviewer directory (`kit/relo`, `kit/priv`, `kit/k06/*`), together with the extracted candidate and base trees. The owner may remove them by exact path.

---

## 15. Disposition of findings

| ID | Severity | Disposition |
|---|---|---|
| **R6C2RV-S01** | **HIGH, blocking (source)** | Day/date binding fails open for (a) weekday + day-of-month, (b) unrecognised date forms, (c) a bare weekday beyond its next occurrence, (d) non-`H:MM` times, (e) a day stated in another sentence. A wrong date with evaluated times is delivered, including a booking restatement; 32/32 full-path cases; SQLite = PG |
| R6C2RV-K07 | LOW, non-blocking (fail-closed) | K06 block prints RESTORED with a foreign symlink/directory present; FINALIZE refuses afterwards |
| R6C2RV-I01 | INFO | Undisclosed ignored `__pycache__` in the worktree; cannot reach the commit or image |
| R6C2RV-I02 | INFO | ENABLE's saved interlock pre-check records no producer rc; the authoritative re-check does |
| R6C2RV-I03 | INFO | CE's customer parser maps "lunes 23" → next Monday (pre-existing); its own listing states the evaluated date truthfully, but this makes S01(a) reachable |
| R6C1RV-S01 | — | **Partially fixed; not closed** (R6C2RV-S01) |
| R6C1RV-K03 / K04 / K05 | — | **CLOSED** |
| R6C1RV-K06 | — | **CLOSED** for the stated posture (K07 follow-up) |
| R6IRV-S02 / P01 / T01 / K01 / K02 / H01 / D01 | — | remain **CLOSED** |
| R6IRV-C01 / P02 / P03 / Q01 | — | owner rulings unchanged, not expanded |
| R5-F1 | — | remains **FIXED** |

---

## 16. Smallest exact correction (R6-C3)

**Source (required), in `response_validator.py`.** The principle: a day-like or time-like expression the resolver does not fully read must make the claim **unsupported**, never "no anchor".

1. **Weekday + day number.** In `_day_anchors`, read `{weekday}\s+(\d{1,2})\b` (not followed by `:`, `/`, `-`, `.` or `de {month}`) as a weekday anchor **and** a day-of-month anchor. Every anchor must equal the canonical date, so "el lunes 23" for Mon 16 is refused. Apply the same rule in the confirmed-booking branch.
2. **Bare and alternative date forms.** Recognise these as anchors in both `_DATE_ANCHOR_RE` and `_day_anchors`:
   - `el\s+(\d{1,2})\b` (not a time);
   - `DD-MM[-YY(YY)]` and `DD.MM`;
   - abbreviated months (`ene… dic`, optional `.`), and `{month} N`;
   - ISO date immediately followed by `T` or a time.
   An unresolvable day-like token makes the sentence `malformed`. Vague period words (`finde`, `fin de semana`, `semana que viene`) next to a concrete time count as unresolvable anchors.
3. **Bare weekday horizon.** A weekday without a date resolves to its next occurrence in `[today, today+6]` and must equal the canonical date. A weekday paired with an explicit date that matches is resolved by the date.
4. **Non-`H:MM` times.** For availability, treat `a las H`, `H hs`/`H h`/`Hhs` and `H am/pm` as the time `HH:00` (with `y media` / `y cuarto` → `:30` / `:15`). They are then propositions subject to rules 1–3 and slot membership. The R6-C1 ordinary-prose control "Te llegamos a las 10 en punto." becomes an arrival-time proposition, refused without an evaluation and kept only when 10:00 is evaluated. That is the correct direction.
5. **Message scope.** When any sentence of a reply is an availability proposition with a time and no anchor, check the day anchors of the reply's other sentences against the canonical date too, so the day cannot be stated in a neighbouring sentence.
6. **Tests.** Full-path, 4 postures, SQLite and PG, for every reviewer form in §3/§4 (X01–X08, B06/B07, M04) plus their correct-date controls ("el lunes 16", "el 16", "16-11", "16 nov", "a las 10" with 10:00 evaluated). Mutants for each new rule.
7. **SB1.** Re-record `response_validator.py`'s blob with an authorization comment naming the correction milestone.

**Kit.**
- Required only as a consequence: re-pin and re-seal (new tree, changed-path and app manifests; new anchor), then re-run the kit suites, the mutations and one rehearsal.
- Recommended in the same milestone (R6C2RV-K07): the K06 block should STOP on `find . ! -type f ! -type d` (any link or special file) and on any directory not in the sealed directory set, before changing anything.

---

## 17. W-3 and WILD status

**W-3 remains OPEN. The final WILD remains NOT RUN.** No handset test, window, tester reset or live reset was performed. The real R6-C2 kit is **PROVISIONAL**, and every live ENABLE, RESET_BETWEEN and tester reset refuses at the pin gate. The R5 kit remains the live emergency instrument for the f16b1f3 deployment.

## 18. Next milestone

**PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C3**: the narrow day/date reading correction in §16, with re-pin and re-seal. Then **…-R6-C3-FINAL-FOCUSED-INDEPENDENT-REVIEW**.

Until that review is READY, commit, push, the final image build, pin finalization, dark deployment, windows and the WILD remain **NOT AUTHORIZED**.

## 19. Reviewer evidence (sha16, under `/opt/ridecheck-builds/crm-r6-c2-final-review-20261112T133452Z/`)

| Item | sha16 |
|---|---|
| `probe/unit_probe.py`, `logs/unit_probe.txt` | `b6d8c8246f9f4fa8`, `1964087bf60b6507` |
| `probe/red_rv.py` | `4267f4c7ef567efd` |
| red base / R6 / R6-C1 / R6-C2 (SQLite) | `3ff0ca6e58c77383` / `023e349ca34b1635` / `023e349ca34b1635` / `77ace662caf77094` |
| red R6-C1 PG / R6-C2 PG / R6-C2 PG Kiritimati | `023e349ca34b1635` / `77ace662caf77094` / `77ace662caf77094` |
| `pgstage.sh`, `logs/pgstage.txt` | `5879a2898165e7ec`, `c5a2f60b3aea0fa5` |
| `mut/mutants.py`, `mut/results.txt` | `1bc4738e031722c0`, `5e92b0191cbe1b8f` |
| `regress.sh`, `logs/regress_stdout.txt` | `ef227a0629dbc5b7`, `c57330bc4f1a2731` |
| `sentinel/results.txt` | `384afc12104d718e` |
| `kit/chain.sh`, `logs/kitchain.log` | `14767ba8171b10b5`, `a13a2fb80afe2dfc` |
| `logs/kit_mutc2.txt`, `kit_mutc1.txt`, `kit_mutr6.txt`, `q21_vs_k82.txt` | `1288acf6ca71777e`, `3cc77fa067d496b8`, `fe2afbc789620ba5`, `5eeff8519b4b4192` |
| `kit/k06_probe.sh`, `logs/k06_probe.txt`, `logs/kit_neg.txt` | `56d6cb7b3b0e9386`, `81d09cf1be590b78`, `405f36ed2e4958eb` |
| `logs/rehearsal_stdout.txt` | `57ffac52259620c2` |
| `live/probe.sh`, `live/before.txt`, `live/after.txt`; `logs/memory.log` | `dee61ae18d8d4c36`, `3d7f1a2ae33d6e77`, `85168f5cb14a46e9`; `4b190b4db3d10075` |

**VERDICT: CORRECTION REQUIRED.** STOP. This audit is not staged or committed.
