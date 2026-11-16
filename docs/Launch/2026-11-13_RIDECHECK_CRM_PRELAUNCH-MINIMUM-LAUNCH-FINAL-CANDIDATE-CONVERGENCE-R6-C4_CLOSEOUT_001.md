PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C4

# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — FINAL CANDIDATE CONVERGENCE R6-C4 — CLOSEOUT 001

Date: 2026-11-13 (work 13:52Z–19:46Z).
Source worktree: `/opt/ridecheck-crm-r5-f1-response-validation`, branch `fix/prelaunch-r5-f1-response-validation-boundary`, base = HEAD `f16b1f328f3fbf1050afdcec583d252d528c72bf`. Same branch and base as R6-C3; no new worktree.
Authoritative input: `2026-11-13_…R6-C3-FINAL-TEMPORAL-BOUNDARY-INDEPENDENT-REVIEW_AUDIT_001.md`, verdict CORRECTION REQUIRED on R6C3RV-S01 (HIGH), S02 (HIGH), T01 (HIGH, tests), P01 (MEDIUM, state) and K08 (LOW, kit).
Evidence directory: `/opt/ridecheck-builds/crm-final-convergence-r6-c4-20261113T135207Z` (abbreviated `$EV` below).

Nothing was staged, committed, pushed, built, finalized, deployed, migrated, enabled or sent. W-3 remains OPEN. The final WILD remains NOT RUN.

# VERDICT: READY FOR FINAL TEMPORAL-BOUNDARY INDEPENDENT REVIEW

R6C3RV-S01 and S02 are corrected by replacing the positional residual scan with a **default-deny** temporal scanner. Every number and every temporal word in a reply must be consumed by a recognizer. Otherwise it must be consumed by a named inert class (address, phone, thousands, engine, count, ordinal label, list marker, designator). Anything else makes the reply **unresolved**, and an unresolved scheduling reply is refused.

The negative-availability exemption no longer removes a sentence from validation. A negation now has a scope, and only an exclusively negative sentence is exempt from the positive-offer check. Even then its days are compared under a booking, and it is never exempt when it carries a positive item or an unresolved form.

P01 is closed in two ways:
- the CE producer gate requires the AI-proposed time to be one that the analysis itself reads;
- the gate refuses a negated non-canonical day beside a dayless time.

K08 is fixed in runbook §4a. A final seal-2 correction makes the external-anchor check provably precede the `PIN_STATE` read.

| Verdict condition | Holds | Evidence |
|---|---|---|
| Every S01/S02 production-path counterexample refused | yes | red control on the sealed R6-C4 candidate, 4 postures: **0/140** wrong replies delivered (§6). Production-path suite P01–P05 + generated sample (§5) |
| Every canonical control survives | yes | red control **48/48** delivered verbatim. Generated corpus: 1,400/1,400 canonical kept (§5, §6) |
| Meaningful mutants killed | yes | source **110/111 KILLED**; the one survivor (G36) is proven equivalent over 33,600 validations (§8). Kit R6-C4 **10/10** (§10) |
| Regressions clean | yes | SQLite focused and full NEW 0. The only GONE is the SB1 frozen-file sentinel, re-recorded and disclosed. PG 713 = 713, NEW 0 / GONE 0 (§7) |
| K08 corrected | yes | K89 30/0; P01–P10 killed; RED control on the R6-C3 block (§10) |
| Live unchanged | yes | `before.txt` and `after.txt` are byte-identical, sha256 `fdff6e8e…a747cc` (§13) |

READY is a review verdict only. It does **not** authorize a commit, push, build, FINALIZE, deployment, migration, flag enablement, Meta change, W-3 or the final WILD.

---

## 1. Manifests and hashes

**Candidate (single seal, final)**
- Sealed copy `$EV/cand`: 734 files, full manifest `8ebac6bb71213d9617e9ba91dd69e51df5040abd81b7d15ce4a5e7f90458f87e`. The worktree equals the sealed candidate, rechecked at 19:46Z after every run.
- Base copy `$EV/base`: 710 files, `7282f596a87e3e5076ca59ab66d10a761bc9f6b24becb662444b86ddb81709b4`. This is the same base as R6-C3.

| Pin | sha256 | Files |
|---|---|---|
| `candidate_tree.manifest` (all files outside `docs/Launch/`) | `b6131c8c71d0a63eb3c4d8b25853ff0d9c5ee33d59a817747fdfcd089e268bc1` | 684 |
| `changed_paths.manifest` (vs f16b1f3) | `d305a72aeefe0f59b3a47e9b836f4e4889b1426386f3d0c05468c728a3b66494` | 20 paths |
| `candidate_app.manifest` (`backend/app`, no bytecode) | `0e5cd6c9a44ab593fa8a6db5047c433d326478b3a36d7ef94d96a33ffef34ae9` | 137 |

- `response_validator.py` git blob: `683c033e28861a0ba041ced211a4d63348052e0a` (R6-C3: `e43ff982…`).
- The tree pin recomputes to `b6131c8c…` after all runs.
- Staged files: 0. HEAD is unchanged at `f16b1f3`.

This closeout lives in `docs/Launch/`, so it is outside the tree pin.

**Kit seals.** Seal 1 is superseded; §10 gives the reason.

| Seal | Kit directory | Anchor = sha256(`pins/PROVISIONAL_CHECKSUMS.sha256`) | Status |
|---|---|---|---|
| 1 | `crm-minimum-launch-enablement-kit-R6-C4-20261113T143526Z` | `6101bcdfdd3c7f2342c93d24c3f2b3efe5a543a744752fbf9b54398c42aeed19` | **SUPERSEDED**. Kept untouched, still verifies |
| 2 | `crm-minimum-launch-enablement-kit-R6-C4-S2-20261113T183908Z` | **`359062f49e8c2f3b7795d6d127ef28f360050d873276df4d7c6b5caef98557ac`** | **FINAL**, 134 checksum entries, 148 inventory lines |

- Private runtime: `crm-minimum-launch-enablement-PRIVATE-R6-C4-20261113T143526Z`, shared by both seals.
  - The four inputs were copied from R6-C3; `PRIVATE_MANIFEST` is `28a91981…c8b2`, VERIFIED.
  - It holds two rehearsal run directories: `run-r6c4-20261113T182250Z` (seal 1, superseded) and `run-r6c4s2-20261113T193046Z` (seal 2, final).
- The R6-C3 kit is unchanged: `f2945987b926a261…`, verifies.

## 2. Production changes (vs the sealed R6-C3 candidate)

There are only two production files. The R6-C3 → R6-C4 delta is +664/−158 lines in `response_validator.py` and +15/−4 in `conversation_engine.py`.

### `backend/app/services/response_validator.py`

The R6-C3 lexicon-to-`stated_day_is` block is replaced by a default-deny scanner.

**Numbers and clocks**
- Compositional Spanish cardinals: `_card_value`, `_CARD`, `_CARD_NO_ONE`. They cover "veintitrés", "treinta y uno" and similar.
- Spelled clock after any anchor ("a/para/desde/hasta/sobre las …"). `_LAS_RE` has named groups and replaces only the clock span.
  - `_spoken_clock` / `_minute_offset` compute the value: "y media", "y cuarto", "menos cuarto", "menos diez". So "8 menos cuarto" is 07:45.
  - A meridiem after a spelled hour is applied.

**New recognizers**
- `_SP_RANGE_RE`: spelled windows.
- `_YEAR_FIRST_RE`: `2026/11/23`, `2026.11.23`, `2026-11-23`. This closes the R6-C2 regression.
- `_RELATIVE_OFFSET_RE`: "en 12 días", "dentro de 5 días". These are unresolved.
- `_PAST_DAY_RE`: "ayer", "anteayer" and similar.
- `_WD_FULL_RE` covers weekdays with:
  - an article or qualifier;
  - abbreviations `lun/mar/mie/mier/jue/vie/sab/dom`;
  - attached `(23)`, `[23]`, `nº 23`;
  - tails "siguiente / que viene / pasado / anterior / otro".

**Weekday resolution**
- A qualified weekday ("el próximo lunes", "el otro lunes", "el lunes que viene") is unresolved.
- Today's own weekday is unresolved, unless it stands in apposition to "hoy" or "mañana". This fixes R6C3 H1.
- Otherwise the bare-weekday horizon is [today+1, today+6].

**Inert classes**
- `_ADDRESS_RE`, `_PHONE_RE`, `_THOUSANDS_RE`, `_ENGINE_RE`.
- `_COUNT_RE`: not after "y/menos" and not after a day word.
- `_ORDINAL_LABEL_RE`, `_LIST_MARK_RE`, `_PRONOUN_COUNT_RE`, `_ENUMERATION_RE`.
- `_designator_numbers`, with `_DESIGNATOR_EXCLUDED` and `_DESIGNATOR_BLOCKERS`.

**Default deny**
- Every leftover 1–2-digit number is unresolved.
- A leftover spelled cardinal is **strong** when it is ≥13, in a day position, on a label line or standalone. Otherwise it is **weak**. See `_STRONG_FRAGMENT_WORDS` / `_WEAK_FRAGMENT_WORDS`.
- Also unresolved:
  - a leftover meridiem;
  - an interval bound before a day;
  - "a las <word>" (weak);
  - approximators before or after a time (`_APPROX_TIME_RE`, `_APPROX_WORD_RE`: "más o menos", "sobre las", "aprox.").

**Structure**
- A general-hours parenthetical is scanned separately and then removed.
- Non-ASCII punctuation is a clause boundary.
- A layout label (`_LABEL_COLON_RE`, e.g. "Día: 23") makes the sentence a scheduling sentence.
- `_SENTENCE_SPLIT_RE` does not split after weekday abbreviations or after `av./avda./nro./pje./bv.`

**Negative scope (S01-a)**
- `_NEGATION_RE` also covers "sin disponibilidad…".
- `_NEG_SCOPE_GLUE` bounds the negation.
- A sentence is exclusively negative only when it has no positive placeholder, no lexicon or fragment outside the scope, and no unresolved form.
- A mixed sentence joins the proposition with its positive items.
- The R6-C3 `_POSITIVE_OFFER_RE` marker list is **removed**: there is no marker list left to be incomplete.

**Analysis types and verdict**
- `SentenceTemporal` gains `weak`, `negated_days` / `negated_times` / `negated_dayparts` and the property `temporal_weak`.
- `TemporalAnalysis.unresolved` includes `temporal_weak`.
- `temporal_verdict`:
  - Under a confirmed booking there is no negative exemption; negated items are validated too (R18).
  - A dayless positive time, part of day or lexicon beside a negated non-canonical day is refused.
- `stated_day_is` counts negated days. A new public function `stated_times_of` is added.

### `backend/app/services/conversation_engine.py` (P01)

- `_ai_time_day_is_evaluated(cls, state, reply_text, evaluated, proposed_time=None)` additionally requires `proposed_time ∈ stated_times_of(reply_text)`. An AI-proposed time that the reply does not itself state canonically is not stored.
- The call site passes `ai_proposed_time`. The module imports `stated_times_of as _rv_stated_times_of`.

No other production file changed. These are unchanged:
- `reply_claim_shape.py`;
- the claim projection;
- the routes;
- the schedule, pricing and outbound gate;
- the booking authority.

## 3. Test changes and expectation changes

**New suites**
- `tests/test_r6_c4_default_deny.py` (807 lines):

  | Class | Content |
  |---|---|
  | C4_01 | Audit counterexamples S01 / S02 / BOOKING, and H1 |
  | C4_02 | Negative scope |
  | C4_03 | Default deny |
  | C4_04 | Spanish numbers, with an independent oracle |
  | C4_05 | Reviewer mutants R03/R05/R07/R08/R09/R14/R16/R18/R20/R22 |
  | C4_06 | Producer gate (P01) |
  | C4_07 | Controls |
  | C4_08 | Generated corpus (§5) |
  | C4_09 | Structure |
  | C4_10 | Further shapes: label line E16; G01/G04/G05 killers; trailing approximators; parentheticals |

- `tests/test_r6_c4_default_deny_production_path.py` (208 lines): P01–P05 on the full engine in 4 postures, plus a generated sample.

**Expectation changes** (all from one rule: H1, today's own weekday is ambiguous)

| File | Test | R6-C3 expectation | R6-C4 expectation |
|---|---|---|---|
| `test_r6_c3_temporal_class.py` | T03 horizon, k=0 | bare "lunes" said on Monday = today, kept | refused (ambiguous: today or +7) |
| `test_r6_c2_day_date.py` | BusinessTimezone `test_weekday_at_the_boundary`, `test_a_past_evaluation_is_stale` | bare weekday | wording changed to "hoy miércoles" / "hoy jueves" (apposition resolves it). The assertion is unchanged |
| `test_r6_c2_day_date_production_path.py` | `test_weekday_at_the_boundary` | bare weekday | "hoy …" wording. The assertion is unchanged |
| `test_sb1_booking_representation.py` | SB1 frozen-file sentinel | blob `e43ff982…` (R6-C3) | blob `683c033e…`, with an R6-C4 comment |

No test was deleted, and no assertion was weakened. The SB1 re-record is the only change to a sentinel value.

## 4. R6C3RV findings: dispositions

| Finding | Disposition |
|---|---|
| **S01** HIGH, wrong date delivered | **Fixed as a class (default deny).** The audit shapes are covered: (a) negative sheltering, (b) unconsumed day numbers, (c) year-first, (d) relative offsets, (e) same-weekday-next-week, (f) booking restatements. The red control is 0/140 (§6) |
| **S02** HIGH, wrong time delivered | **Fixed.** Spelled clocks and minute arithmetic; meridiem; approximators; "para/desde las doce" |
| **T01** HIGH, tests | **Fixed.** The full-path tests kill R03, R05, R07, R08, R09, R16, R18, R20 and R22 (and the rest of the 15) on SQLite and PG (§7, §8) |
| **P01** MEDIUM, state | **Fixed.** `stated_times_of` is required for the stored AI time; negated days count in `stated_day_is`. C4_06 and mutants G20/G21/G22/G23/D13/D13b are all killed |
| **K08** LOW, kit | **Fixed** (§10) |

## 5. Generated corpora

- **Pure validator** (`C4_08`): 12,320 generated cases.
  - Wrong, must be refused: 10,920/10,920 refused.
  - Canonical, must be kept: 1,400/1,400 kept.
- **Production path** (`test_r6_c4_default_deny_production_path`): a sample of 68 cases × 4 postures = 272 runs.
  - 55 wrong cases × 4 = 220 refused.
  - 13 canonical cases × 4 = 52 kept verbatim.
- **G36 equivalence probe** (evidence only): 33,600 validations, 0 differing outputs (§8).

## 6. Red control (full engine, 4 postures, frozen clock Wed 2026-11-11 12:00 ART; H1 cases Mon 2026-11-16)

`$EV/probe/red_c4.py` runs one harness copy against three trees:
- **W**: 35 wrong forms (the audit's N1–N4, W1–W6, H1, T1, T2, B1–B3, plus 19 further S01/S02 and reviewer-mutant shapes);
- **K**: 12 canonical controls.

| Tree | W wrong delivered | K controls kept | State written | Output sha16 (188 lines) |
|---|---|---|---|---|
| base f16b1f3 | 140/140 | 48/48 | 0 | `e842677728179d0b` |
| sealed R6-C3 | **112/140** | 44/48 | 0 | `88eb0f22735f5cee` |
| **sealed R6-C4** | **0/140** | **48/48** | 0 | `5debee190e8eced7` |

- **R6-C3 delivered**: A1, A2, B1–B4, H1, H1b, N1–N5, T1, T2, T2b, T3, T3b, W1–W6, W2b, W3b, W4b, W6b.
- **R6-C3 controls altered**: K8, the "Av. 9 de Julio 1500" address control, in all 4 postures. R6-C3 refused it with AVAILABILITY and RESPONSE_CONTRACT findings, a false refusal that R6-C4 corrects through `_ADDRESS_RE`.
- **R6-C4**: delivered none.

Disclosure: the first base run failed because the pre-R6-C2 tree has no `_utcnow`. The probe was fixed with a shim (`RV._utcnow = lambda: None`, applied only when the attribute is absent), and all three trees were rerun. The c3 and c4 outputs are byte-identical across both runs.

The PG rerun of c3 and c4 is byte-identical to SQLite (§7).

## 7. SQLite and PostgreSQL comparisons (single candidate seal)

**Sentinel.** The control passes 1/1. Drift in each of the six frozen files fails: `response_validator.py`, `claim_projection.py`, `flow_data_exchange.py`, `whatsapp.py`, `schedule.py`, `pricing.py`. The restored control passes.

**SQLite**

| Run | Base | Candidate | NEW failing | GONE | Cases | Skips |
|---|---|---|---|---|---|---|
| Focused (77 files) | 2,605: 2,226 passed / 18 failing / 361 skipped | 2,967: 2,589 / 17 / 361 | **0** | 1 | +362 / −0 | 0 changes |
| Full | 8,346: 7,489 / 71 / 786 | 8,708: 7,852 / 70 / 786 | **0** | 1 | +362 / −0 | 0 / 0 |

- The single GONE failure is `test_sb1_20_frozen_files_unchanged`. It fails on base (where the candidate's validator is absent) and passes on the candidate. This is the expected effect of the SB1 re-record.
- The common failing tests are the 17 (focused) and 70 (full) failures already present on base. None changed.

**PostgreSQL** (disposable postgres 16.13, `Etc/UTC`; candidate and base schemas, columns and constraints, are equal)
- 29 PG suites: 713 = 713 cases, NEW 0 / GONE 0, 2 failing on both (pre-existing: `test_convergence_p2_authority_pg`, `test_l4_7w5_g37r_rv03_tester_reset_pg`).
- SB2B: 46 passed / 1 failed / 1 skipped, identical on both.
- Production-path suites on PG:

  | Suite | Passed | Subtests | Dialect check |
  |---|---|---|---|
  | c4pp | 64 | 1,798 | 864 threads, 164 revisions, oq 0 |
  | c3pp | 68 | 1,025 | — |
  | c2pp | 67 | 354 | — |
  | c1pp | 72 | 1,471 | — |
  | R5-F1 | 26 passed / 0 failed | — | 0 OQ |

- Red control c3/c4 on PG: byte-identical to SQLite.
- Teardown: 0 containers, 0 networks.

## 8. Source mutations

111 mutants (`$EV/mut/mutants.py`):
- 60 inherited from R6-C3, restated where the R6-C4 code moved them;
- the reviewer's 15 (R01–R22);
- 36 new G-mutants on the R6-C4 boundaries.

**Pre-seal development pass** (`$EV/mut_dev/results.txt`, control 470 tests / 5,254 subtests): 105/111 KILLED.

| Initial survivor | Weakened boundary | Disposition |
|---|---|---|
| E16 | a label-only line's numbers are not temporal | killing test added (C4_10) |
| R14 | a declared AVAILABILITY claim may carry a stale evaluated date | killing test added (C4_05) |
| G01 | negative scope extends left over any word | killing test added (C4_10) |
| G04 | availability wording outside the scope does not end exclusivity | killing test added (C4_10) |
| G05 | a fragment outside the scope does not end exclusivity | killing test added (C4_10) |
| G36 | an exclusive negative with an unresolved fragment stays exempt | **equivalent**, see below |

A monitor line during the dev pass read "C26 SURVIVED". It did not match the results file, where C26 is KILLED. It was a spurious event.

**On the seal** (`$EV/mut/results.txt`, control 474 tests / 5,265 subtests): **110/111 KILLED**, 0 invalid. All 15 reviewer mutants are KILLED. The only survivor is G36.

**G36 is equivalent.** An exclusively negative sentence already requires "no unresolved form" (§2), so the G36 branch is unreachable. `$EV/mut_dev/G36_equivalence.txt` shows 0 differing outputs over 33,600 validations (the generated corpus, 4,480 negative forms, and offer and booking facts).

## 9. SB1 sentinel

The sentinel is re-recorded to blob `683c033e…`, with a comment naming R6-C4 and the R6-C3 blob `e43ff982…`. The drift probe (§7) proves that it still detects each frozen file.

## 10. Kit, K08, and the seal-1 → seal-2 correction

**K08 (runbook §4a, recovery block).**
- Check 0b runs first: it refuses a name holding a newline or a carriage return.
- Checks 0 / 0a are the R6-C3 K07 checks.
- Then, before `PIN_STATE` is read:
  - 0c: the external `$ANCHOR` check, followed by the anchored inventory hash;
  - 0d: a per-entry type/mode comparison against `pins/PROVISIONAL_INVENTORY.txt` (only `CHECKSUMS.sha256` is exempt, because FINALIZE may republish it).
- Before `RESTORED`, the block re-checks the exact type/mode/path inventory.
- New in the kit:
  - `pins/PROVISIONAL_INVENTORY.txt`;
  - `tests/test_kit_r6c4.sh` (K89, K90);
  - `tests/mutate_r6c4.py`;
  - `docs/AUTHORIZATION_TEXT_R6C4.md`;
  - `docs/R6C4_DIFF_vs_R6C3_kit.patch`.
- README and runbooks are updated.

**Why seal 1 is superseded.** On seal 1 the kit chain was clean, except that inherited mutant **N19** in `mutate_r6c2.py` now SURVIVED: 23/24, where R6-C3 had 24/24. N19 removes the R6-C2 anchor check.

Analysis (`$EV/n19/`, evidence only, kit untouched):

| Mutant | K85 | K89 (seal 1) | K89 (seal 2) |
|---|---|---|---|
| A: remove only the new 0c anchor check | survived | **survived** | KILLED (3 failing) |
| B: remove both anchor checks | KILLED | survived | KILLED |

So the external anchor was still enforced: the two checks mask each other. However, the runbook's promise that the anchor is checked **before `PIN_STATE` is read** had no test.

**Fix (seal 2):**
- K89 gains two checks:
  - a wrong anchor must STOP at 0c;
  - a wrong anchor plus `PIN_STATE=FINAL` must STOP on the anchor, not on `PIN_STATE`.
- The ordering check now also requires the anchor before `PIN_STATE` and before every change.
- Mutant **P10** (remove 0c) is added.
- README: "10 kit mutants".

Seal 2 was built fresh into a new directory (`seal_kit.sh` refuses an already sealed kit, and nothing was deleted). Ignoring the kit-name re-point, seal 2 differs from seal 1 in exactly three files: `README.md`, `tests/test_kit_r6c4.sh` and `tests/mutate_r6c4.py`. The entry listing is identical. Seal-1 outputs are preserved in `$EV/seal1_evidence/`; seal-2 outputs are in `$EV/s2/`.

**Kit chain on seal 2** (serial, memory-gated):

| Job | Seal 2 | Seal 1 (superseded) | R6-C3 |
|---|---|---|---|
| seal verify before / after | OK, 134 entries, anchor `359062f4…` | OK `6101bcdf…` | — |
| `test_kit_r6c4.sh` | **30/0** | 28/0 | — |
| `mutate_r6c4.py` | **10/10 KILLED** (P01–P10) | 9/9 | — |
| `test_kit_r6c3.sh` | 28/0 | 28/0 | 28/0 |
| `mutate_r6c3.py` | 5/5 | 5/5 | 5/5 |
| `test_kit_r6c2.sh` | 81/0 | 81/0 | 81/0 |
| `mutate_r6c2.py` | 23/24: N19 masked in K85 by 0c; killed jointly (B) and its 0c twin killed by K89 (P10) | 23/24 | 24/24 |
| `test_kit_r6c1.sh` | 84/0 | 84/0 | 84/0 |
| `mutate_r6c1.py` | 28/29: Q21 masked in K78, as before; killed by K82 | same | same |
| `test_kit.sh` (R3) | 273/0 | 273/0 | 272/0 |
| `test_kit_r5.sh` | 242/0 | 242/0 | 242/0 |
| `test_kit_r6.sh` | 112/0 | 112/0 | 112/0 |
| `mutate_r6.py` | 29/34: R25/26/27/29/31 masked in K74, as before; killed as R6-C1 Q25–Q29 | same | same |
| Real-config negatives (umask 022) | all rc 2 at the pin gate, **0 docker calls**; files 0600 / dirs 0700; private runtime VERIFIED; kit still sealed | same | same |

The R3 count of 273 includes one R6-C4 pin check.

K89 RED control: the sealed R6-C3 block fails both postures. It changes the kit and then STOPs on a newline publication name, and it prints RESTORED on a setuid world-writable script.

## 11. Rehearsal

`rehearse_r6.sh` on `PRIVATE-R6-C4/run-r6c4s2-20261113T193046Z` (seal 2): **REHEARSAL R6: 94 passed, 0 failed**.
- Final posture: all eight flags false, dispatch False.
- Live containers and fingerprint unchanged.
- Final MemAvailable 2,353 MiB.

The seal-1 rehearsal (`run-r6c4-20261113T182250Z`) was also 94/0. It is superseded.

## 12. Memory discipline

- 47 heavy jobs were gated by `waitrun.sh`, one at a time. Each started at ≥1,536 MiB MemAvailable.
- **Memory waits: 0. Aborts: 0.**
- Lowest MemAvailable during any job was 842 MiB (SQLite full, candidate, 16:20Z), above the 500 MiB abort line.
- Cleanup was limited to the exact disposable objects: PG containers and networks named `c4-*` and the kit tests' own temp dirs. 0 `c4-` containers and 0 networks remain.
- No host-scope prune.

## 13. Live isolation (read-only)

`$EV/live/probe.sh` was run before (13:52:07Z) and after (19:46:15Z).

**`before.txt` and `after.txt` are byte-identical**, sha256 `fdff6e8e1b40ed1b5998dd865b5ed8a548e9841ceda2a2d141758b1af9a747cc`:
- backend `04e14e5770f9`, restarts 0, GIT_SHA `f16b1f3`;
- alembic `20261104_vehicle_identity`;
- all eight flags false; `booking_dispatch_allowed=False`;
- DB fingerprint `fbae2abdc3371f9146f0ac51a2ffa427`; max ids and pending outbound 0 unchanged;
- schema md5s, allowlist sha16 `ad98b5c31a4e8ed8`, compose/.env/Flow file hashes and n8n workflow count unchanged;
- running_containers=3, networks=5;
- every prior kit and private runtime OK; R6-C3 kit `f2945987b926a261`.

Nothing was sent, and no Meta, n8n, DB or container state was changed. No tester was contacted.

## 14. Residual limitations (disclosed, all fail-closed unless stated)

1. "mar N" reads as March, not "martes", so "el mar 17" is compared as 17 March. A wrong-date reading is refused. A canonical "mar" abbreviation for martes is refused too (fail-closed).
2. "9 de Julio" without a street prefix is refused as a date; with "Av."/"calle" it is an address.
3. "y uno" as a minute ("las diez y uno") is unresolved and refused.
4. **Fail-open residual:** "el 3008", a DDMM-like 3–4-digit number after a bare "el", is inert. It is neither read as a date nor flagged. No generated or reviewer form uses it. It is disclosed for the independent review.
5. "en N horas" in transactional prose ("te confirmamos en 2 horas") is refused (fail-closed; the cost is availability).
6. `_regenerated_offer_es` still carries no structured AVAILABILITY claim. Its prose is validated by the same default-deny scanner (residual from R6-C3, unchanged).
7. G36 is an equivalent mutant (§8).
8. Kit mutants N19 (K85), Q21 (K78) and R25/R26/R27/R29/R31 (K74) are masked in their own section and killed elsewhere (§10).
9. Ignored bytecode (`__pycache__/*.pyc`) exists in the worktree. It is not in any manifest, and it is not tracked or staged.
10. The red-probe base row needed the `_utcnow` shim (§6).

## 15. W-3 and WILD

W-3 remains **OPEN**. The final WILD remains **NOT RUN**. The kit is PROVISIONAL; its pin gate is CLOSED and its FINALIZE keys are UNFINALIZED.

## 16. Next milestone

The next milestone is the FINAL TEMPORAL-BOUNDARY INDEPENDENT REVIEW of the sealed R6-C4 candidate:
- tree `b6131c8c…`;
- app `0e5cd6c9…`;
- changed paths `d305a72a…`;
- validator blob `683c033e…`;
- kit seal 2, anchor `359062f49e8c2f3b7795d6d127ef28f360050d873276df4d7c6b5caef98557ac`.

Quote the anchor from this closeout, never from the kit.

STOP
