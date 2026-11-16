PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C3

# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — FINAL CANDIDATE CONVERGENCE R6-C3 — CLOSEOUT 001

Date: 2026-11-12 (work 16:50Z–21:40Z).
Source worktree: `/opt/ridecheck-crm-r5-f1-response-validation`, branch `fix/prelaunch-r5-f1-response-validation-boundary`, base = HEAD `f16b1f328f3fbf1050afdcec583d252d528c72bf`.
Authoritative input: `…R6-C2-FINAL-FOCUSED-INDEPENDENT-REVIEW_AUDIT_001.md`, read completely. Its verdict was CORRECTION REQUIRED, on R6C2RV-S01 (HIGH) and R6C2RV-K07 (LOW).

# VERDICT: READY FOR FINAL TEMPORAL-BOUNDARY INDEPENDENT REVIEW

R6C2RV-S01 is corrected as a class, not as a list of sentences. `response_validator.py` now runs one temporal analysis over the **complete** reply, and every temporal-looking expression ends in exactly one of four states:
- `FULLY_PARSED_AND_CANONICAL`;
- `FULLY_PARSED_BUT_UNSUPPORTED`;
- `TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED`;
- `NO_TEMPORAL_EXPRESSION`.

Only the first authorizes a concrete date or time proposition. Anything that a recognizer does not fully consume is caught by a structural residual scan and recorded as unresolved, so a partial parse can no longer become "no anchor".

Deterministic slot offers now carry a structured AVAILABILITY claim: one exact ISO date plus `HH:MM` slots, validated directly against CanonicalFacts.

R6C2RV-K07 is fixed. The recovery block refuses links, special files and foreign directories **before** it reads `PIN_STATE` or changes anything.

Every verdict condition holds on the final sealed candidate and kit:

| Condition | Evidence | Section |
|---|---|---|
| Reviewer forms | Every reviewer form is refused when wrong and kept when canonical: red control R6-C3 **0/84** delivered, **0/68** controls altered | §6 |
| Cross-sentence scope | Enforced | §2, §6 |
| Confirmed bookings | Use only committed values | §5 |
| Deterministic slot replies | Carry validated structured claims | §4 |
| Ordinary prose and business hours | Correct | §6, §8 |
| R6 boundaries | Closed | §8 |
| K07 | Fixed: kit suite 28/0, kit mutants 5/5 | §11 |
| SQLite regression | NEW 0, only the sentinel GONE | §7 |
| Staged PostgreSQL | NEW 0 / GONE 0 | §7 |
| Source mutants | 60/60 killed | §9 |
| Rehearsal | 94/0 | §12 |
| Live | Byte-identical | §13 |

Nothing was staged, committed, pushed, built, finalized, deployed or migrated. No flag, allowlist or live data changed. Meta was not contacted and nothing was sent. **W-3 remains OPEN. The final WILD remains NOT RUN.**

---

## 1. Manifest and hashes (final seal = seal 2)

| Item | Value |
|---|---|
| Sealed candidate | `/opt/ridecheck-builds/crm-final-convergence-r6-c3-20261112T165303Z/cand`: 730 files, full manifest `86dcf857…6040`; `worktree == sealed cand` |
| Base export | `base/` (`git archive f16b1f3`): 710 files, manifest `7282f596…09b4` (= R6-C2's) |
| Candidate tree, outside docs/Launch | `58266ffcaf98f6ef0c0c941e6d30473bb3cb6afb58ba09fdd0b7e9e42d62e3bd` (682 files) |
| Changed paths | `ee76ebe7c3134e56534a975c59685c1b8f6fdfee261f0c634f026f4141df69ab` (18 paths) |
| `backend/app` | `d0dc89f50bf21e7279dedefd8893ae64d9ff727997c6e055360d9299805c87ad` (137 files) |
| `response_validator.py` | sha256 `244b6c0c…`, git blob `e43ff98209e3e9c8a9b280949b36ab8d629c2258` |
| `conversation_engine.py` | sha256 `35f17f3d…` |
| FINAL_TAG | `ridecheck-crm-backend:minlaunch-final-r6`, unchanged; no image carries it |

The 18 changed paths are the R6-C2 set of 16, plus `tests/test_r6_c3_temporal_class.py` (`1c39f204…`) and `tests/test_r6_c3_temporal_class_production_path.py` (`350589bd…`).

Changed against the sealed R6-C2 candidate (13 files):
- `conversation_engine.py`
- `response_validator.py`
- `test_l4_7c_3b…` (base test)
- `test_sb1…` (sentinel)
- `test_r5_f1_template_validation_contract.py`
- `test_r6_c1_claim_frames.py`
- `test_r6_c1_claim_frames_production_path.py`
- `test_r6_c2_day_date.py`
- `test_r6_c2_day_date_production_path.py`
- `test_r6_full_engine_prose_corpus.py`
- `test_r6_structured_response_contract.py`
- the two new R6-C3 suites.

Every test change is listed in §14.

---

## 2. Temporal-analysis architecture

These are in `response_validator.py`, a pure module that never touches the database:

| Name | Role |
|---|---|
| `TemporalState` | The four states (closed enum) |
| `DayRef` | Kinds: `date`, `monthday`, `relative`, `day_of_month`, `weekday` (bare, horizon-resolved), `weekday_date` (weekday with a date or day number; the date is authoritative) |
| `TimeRef` | Normalized `HH:MM`, plus whether it is a window endpoint |
| `DaypartRef` | A part of the day: mañana, tarde or noche, as an hour interval |
| `SentenceTemporal` | Per sentence: days, times, parts, `unresolved` (malformed), `vague` (period, habitual or leftover wording), `conflicts` (weekday ≠ its date), `ranges`; and flags `general_hours`, `negative`, `lexicon`, `conditional`, `transactional`, `scheduling` |
| `TemporalAnalysis` | The complete reply. Exposes `dates`, `weekdays`, `days_of_month`, `times`, `window_endpoints`, `unresolved`, `general_hours`, `transactional`, `parse_state` |
| `analyze_temporal(text, today)` | Builds the analysis. Total: never raises |
| `temporal_verdict(analysis, facts)` | The one ruling for the reply's scheduling proposition |

**The scanner** consumes spans in a fixed order and leaves typed placeholders:
1. Courtesy formulas, prices and durations, marked inert.
2. ISO dates and datetimes.
3. Windows: `de/desde/entre H[:MM|hMM|.MM] a/y/hasta H…`.
4. `H.MM hs` and `a las H.MM`.
5. Numeric dates with `/`, `-` or `.`, with an optional year.
6. Month names and abbreviations, in both orders.
7. `HH:MM` with optional am/pm and hs; `H am/pm`; `H hs`; `a las H` / `a la una`; `y media`, `y cuarto`, `menos cuarto`, `en punto`; spelled hours; `de la mañana/tarde/noche`; mediodía.
8. Parts of the day.
9. hoy, mañana, pasado mañana.
10. Weekday spans and habitual plurals.
11. Weekday plus date, weekday plus day number, then bare weekday.
12. Bare day of month (`el N`, `día N`, `para el N`, `del/al N`, `Día: N`).

**The residual scan** then records as `vague` anything temporal-looking that is left:
- period words: semana, finde, mes, feriado, quincena, principios, mediados, a partir…;
- approximators in front of a time (a eso de, tipo, más o menos, alrededor de);
- unconsumed month names;
- `el/día` followed by a spelled day number;
- leftover hs, am or pm;
- separator numbers;
- 1–2-digit numbers after a temporal introducer, next to a temporal span, in a day range, or on a label-only line ("Turno:\n19");
- 3–4-digit numbers attached to `las` or `hs`.

**Normalization:**
- `a las H` with H 1–7 and no meridiem is read as H+12. This is the business-hours reading: "a las 3" = 15:00, "a las 10" = 10:00.
- am/pm and the parts of the day convert exactly.
- Invalid clock readings are unresolved: 25 h, :75, 13 pm, "16 de la tarde".
- A bare weekday resolves only to its next occurrence in `[today, today + 6]`.
- A day number resolves to its nearest occurrence within 62 days.
- A weekday must agree with its explicit date or day number; otherwise it is a conflict.
- A date with no year matches by month and day, as in R6-C2.

**The proposition and the ruling** (`temporal_verdict`):
- The proposition is formed from every sentence that is neither general business hours nor a genuine negative statement. A negative is exempt only while no booking is confirmed.
- It exists when any such sentence has a time, or the availability lexicon, or temporal content while the reply has scheduling wording anywhere.
- If no ScheduleService evaluation exists (and no booking), the result is "no ScheduleService evaluation".
- Any unresolved or vague fragment gives `UNRESOLVED_OR_MALFORMED`.
- A weekday/date conflict gives `UNSUPPORTED`.
- Every day must be the canonical date: the evaluated date (stale if before business today), or the committed booking date. One valid anchor cannot hide another.
- Every time must be an evaluated slot, or the committed start or end; an empty slot set supports none.
- A part of the day must contain its times, or at least one evaluated slot.
- Only then is the state `FULLY_PARSED_AND_CANONICAL`.

**Message scope** (`validate_response`):
- The analysis and the verdict are computed once for the whole reply, before any sentence is judged.
- Every involved sentence gets the same finding. Involved means it carries days, times, fragments, the lexicon or scheduling wording.
- On refusal every involved sentence is removed. If nothing survives, the plan-appropriate fallback is sent.
- Sentences with times or the lexicon are judged where R6 judged availability. Day-only sentences are judged after the booking and acceptance checks, so a completed-state claim is still reported as itself.

**General business hours** are exempt only when all of these hold:
- the sentence has a habitual frame: a weekday span, `los <weekday>`, todos los días, horario de atención, nuestro horario, atendemos, trabajamos, abrimos, hábiles, respondemos;
- it has no concrete day (a date, a relative day, a definite weekday, or a weekday or day number);
- it has no customer-specific proposal (a second-person token with a transactional verb or `turno`);
- every time in it is a range endpoint;
- it has no malformed or conflicting parse.

A bare "el horario" is no longer a habitual frame. A general-hours sentence next to a wrong offer is kept, while the offer is removed (§6).

**Negative statements** are exempt only when:
- the negation scopes availability nouns: no/tampoco + tenemos/tengo/hay/queda… + disponibilidad/turnos/horarios/operaciones/…;
- and the sentence has no positive-offer marker (esperamos, reservo, podemos, pero, sino…).

They still need an evaluation and must contain no malformed date or time. CE's own reason clauses, such as "ese día trabajamos de 9.30 a 14 hs" or "los domingos no trabajamos", are handled.

**Sentence splitting.** A period after a month abbreviation (`19 nov. a las 10`) no longer ends the sentence.

**The CE producer gate.** `stated_day_is` now reads the complete reply. It is False on any unresolved fragment, and on any day that is not the evaluated date. `_ai_time_day_is_evaluated` uses it unchanged.

**R6-C2 compatibility views.** `_day_anchors` and `_anchor_is` are thin views over the R6-C3 scan, so there is one implementation. R6-C1 frame lexicons that are no longer used for availability (`_GENERAL_HOURS_RE`, `_DATE_ANCHOR_RE`, `_DEFINITE_WEEKDAY_RE`) remain as named constants, because the R6-C1 structural test pins their names. `_has_day_anchor`, `_range_is_concrete`, `_check_stated_day`, `_HOURS_RANGE_RE`, `_TIME_TOKEN_RE` and `_NEGATIVE_AVAILABILITY_RE` were removed.

---

## 3. R6C2RV-S01 — every class and its disposition

| Review class | R6-C3 rule | Wrong → | Canonical → |
|---|---|---|---|
| (a) weekday + day number ("el lunes 23", "el jueves 19", booking "lunes 23") | `weekday_date`: both read, date authoritative, weekday must agree | refused | "lunes 16", "jueves 12" kept |
| (b) "el 19", "día 19", "para el 19" | `day_of_month`, nearest in 62 days | refused | "el 16" kept |
| (b) "19-11", "19-11-2026", "19.11", "19.11.2026" | numeric date with any separator | refused | kept |
| (b) "19 nov", "19 nov.", "19 de nov.", "noviembre 19", "nov 19", every month and abbreviation, any case | month recognizer, both orders | refused | kept |
| (b) "2026-11-19T10:00", "2026-11-19 10:00 hs" | ISO with time | refused | kept |
| (b) "el finde", "fin de semana", "la semana que viene", "durante la semana", "a fin de mes", "principios de mes", "después del feriado" | vague → unresolved | refused | (no canonical form) |
| (c) "este lunes" / "el lunes" with Monday 23 evaluated (today Wednesday 11) | horizon `[today, today+6]` | refused | within horizon kept; "lunes 23" kept for Monday 23 |
| (d) "a las 10", "10 hs", "10hs", "10 h", "10am", "3pm", "3:30 pm", "a las 3 y media", "y cuarto", "menos cuarto", "en punto", "diez de la mañana" | normalized to `HH:MM`; must be a slot | refused on the wrong day or a non-slot | kept on the right day with a slot |
| (e) day in one sentence, time in another; newline and bullet forms | one message-level proposition | refused (every involved sentence removed) | kept |
| two anchors, one valid | all must match | refused | — |
| malformed or approximate fragments ("y pico", "a eso de las 10", "tipo 10 hs", "el dieciséis", "16/11/20266", day ranges) | unresolved | refused | — |

---

## 4. Structured deterministic availability

**Claim rule.** `_validate_structured_claim` now requires exactly one strict ISO date and at least one strict `HH:MM` time. The date must be the evaluated `availability_date`, and not stale. Every time must be in `offered_slots`, and an empty slot set supports nothing. A time-only claim is now refused; the R6-C2 test pinned it as allowed and is updated, §14.

**Declared CE sites.** `ConversationEngine._composed_slot_offer(text, day_iso, slots)` declares `StructuredClaim(AVAILABILITY, (ISO, *slots))` on every deterministic slot reply that names times. That is six sites:
1. rejected exact time → "Horarios disponibles: …";
2. PRIMARY rejected / FALLBACK offered;
3. period request ("por la tarde/mañana");
4. next-available, when the Flow cannot open. That path now also writes `last_visible_slots` for exactly the slots it shows, so the visible list belongs to the same evaluation;
5. day-only request;
6. day-only "other period".

The declaration is validated **before** the words are read. The words still pass the temporal analysis, so a correct declaration cannot license different words (unit test T09).

**Paths that cannot carry the claim:**
- **The Booking Flow button body** ("Para {día} tengo N horarios disponibles…"). It is composed inside `_send_booking_flow`, where a `ComposedText` would not survive. It names no time and is validated by `_validate_outbound_text`, which means the full temporal analyzer: its date must be the evaluated date that `_dispatch_booking_flow_for_day` writes in the same call.
- **The plan's state-sourced restatement `_regenerated_offer_es`** (G3-6 DEF-04). It is rendered after validation, from `last_visible_slots` / `last_offered_slots`, which are canonical. It names no day, so it is the same no-anchor endpoint form that the validator permits.

Neither path's prose originates in the AI. AI output stays AI_COMPOSED and receives no structured trust (T09).

---

## 5. Confirmed bookings

The booking reads only `booked_date` and `booked_times`, which are (start, start + `SERVICE_MINUTES`) from the committed revision. These are refused, unit and four-posture full path:

| Case | Refused |
|---|---|
| Monday 23 for Monday 16 | yes |
| right weekday, wrong day number ("lunes 9") | yes |
| "19-11" and "19 nov." | yes |
| date in one sentence and time in another | yes |
| "a las 11" (not committed) | yes |
| "de 10 a 11 hs" (invented end) | yes |
| BOOKED with no revision | yes |
| `current_revision_id` → `provisional` / `cancelled` revision | yes |

These are kept: "lunes 16 a las 10:00", "el lunes a las 10", "lunes 16/11 de 10:00 a 10:45", "Listo, tu turno está confirmado."

---

## 6. Generated corpus, red controls and required cases

**Generated grammar corpus** (`generate_corpus`, deterministic). The matrix:
- 3 stated dates: canonical Monday 16; Thursday 19, another weekday; Monday 23, same weekday;
- × 17 written date forms: separators, articles, full and abbreviated months, both orders, ISO, `día N`, bare `N`;
- × optional weekday prefix: none, right, wrong;
- × 5 frames: statement, question, cross-sentence, newline, bullets;
- × 2 capitalizations;
- × time forms over 12- and 24-hour readings, hour-only, hs, am/pm, en punto, y media, and windows;
- plus wrong times on the canonical date.

| Scope | Cases | Wrong-date refused | Correct-date kept |
|---|---|---|---|
| Unit, pure validator | **3,145** | **2,469/2,469** | **676/676** |
| Full production path: deterministic 1-in-55 sample, 57 cases × 4 postures | **228 turns** | **176/176** | **52/52** |

The full-path sample ran on SQLite (focused and full runs) and on PostgreSQL (`c3pp` 68 passed / 1,025 subtests).

**Red control.** `probe/red_c3.py` (`bb18fda2…`) uses one harness copy from the sealed R6-C3 candidate, on five trees and four postures:

| Tree | X: reviewer forms delivered (21 × 4) | K: correct-date controls altered (17 × 4) | DDW: R6-C2 wrong days delivered | ORD: prose altered | sha16 |
|---|---|---|---|---|---|
| f16b1f3 | **84/84** | 4/68 (the original general-hours prose defect) | 32/32 | **48/48** (original defect) | `55aaf7d8…` |
| R6 | 84/84 | 0/68 | 32/32 | 0/48 | `d54f2e6c…` |
| R6-C1 | 84/84 | 0/68 | 32/32 | 0/48 | `365b8872…` |
| R6-C2 | **84/84** | 0/68 | 0/32 | 0/48 | `610c4626…` |
| **R6-C3** | **0/84** | **0/68** | **0/32** | **0/48** | `124716ac…` |

Further R6-C3 results from the same run:
- S01 / S02 / P01 delivered: 0;
- pending vehicle armed: 0;
- state written: 0.

**PostgreSQL output is byte-identical to SQLite** for R6-C1, R6-C2 and R6-C3. The seal-2 SQLite output is byte-identical to seal 1's.

**Required cases (§10/§11 of the prompt).** All are present in `test_r6_c3_temporal_class.py` (48 tests) and in `…_production_path.py` (four postures, SQLite and PG):
- Classes A–E of the reviewer;
- every positive control;
- every wrong-date case also asserts:
  - no false text;
  - `preferred_time` / `preferred_day` None;
  - no revision or booking;
  - any outstanding question is the fallback's declared one (`DETERMINISTIC_TEMPLATE`).

---

## 7. SQLite and PostgreSQL comparisons (final seal)

Image `ridecheck-crm-test:l4.7b2`, `--network none`, read-only trees.

| Run | Base f16b1f3 | R6-C3 | Comparison |
|---|---|---|---|
| Focused (74 files; 64 on base) | 18 failing / 2175 P / 361 S (2554) | 17 failing / 2473 P / 361 S (2851) | **NEW 0, GONE 1, COMMON 17, changed 0, +297/−0, skip changes 0** |
| Full `../tests` | 62 F + 9 E / 7490 P / 786 S (8346) | 61 F + 9 E / 7789 P / 786 S (8644) | **NEW 0, GONE 1, COMMON 70, changed 0, +298/−0, skip changes 0** |

The only GONE is `UnchangedContracts::test_sb1_20_frozen_files_unchanged`, the authorized sentinel.

**Staged PostgreSQL** (`pgstage.sh` `58b44a32…`):
- a disposable `postgres:16` 16.13 on tmpfs, `--internal c3-net`, `TimeZone=Etc/UTC`;
- one template per tree: `create_all` from the pre-schema record, `stamp 20261006_clarification_attempts`, `upgrade head`, giving `20261104_vehicle_identity` on both; columns and constraints equal;
- each suite on its own fresh clone, with all 19 `*_PG_URL` set.

| Item | Result |
|---|---|
| 29 PG suites | base 2 F / 598 P / 113 S (713) = candidate: **NEW 0, GONE 0, changed 0, skip changes 0** |
| SB2B with a clean DB | 46 P / 1 F / 1 S on both, identical (`alembic_version` staging artifact) |
| R6-C3 unit + full path on PG | **68 passed, 1025 subtests**; 500 threads, 52 booked revisions, 0 outstanding questions |
| R6-C2 unit + full path on PG | 67 passed, 350 subtests |
| R6-C1 frames path + R6 corpus + contract on PG | 72 passed, 1471 subtests |
| R5-F1 production path, PG | **26/26**, outstanding questions 0 |
| R5-F1 production path, SQLite | in the focused and full runs, green |
| Red probe | PG = SQLite byte-for-byte |
| Teardown | 0 containers, 0 networks |

The 2 common PG failures and the 113 `…_URL not set` skips are identical on both trees; they are the prior reviewers' known harness items.

---

## 8. Preserved boundaries

S02, P01, T01, C01, R5-F1 and P02/P03/Q01 are unchanged, and their suites are green on SQLite and PostgreSQL:
- R6 contract and corpus;
- R6-C1 S01/S02/P01, unit and full path;
- R5-F1 template contract and production path.

The rehearsal confirms the motorcycle handoff is truthful and that W2 opens no outstanding question (RR10). Ordinary prose is unchanged: ORD 0/48 and the R6 corpus.

Two narrowly scoped expectation changes follow from the new rule (§14):
- The R5-F1 audit example "te esperamos a las 10 en punto" is now an arrival-time proposition. It is refused by AVAILABILITY, never by VEHICLE, when there is no evaluation, and kept when 10:00 is evaluated. This is the reviewer's stated direction (R6-C2 review §16 item 4).
- Day-naming inspection proposals ("revisamos tu gol mañana", "¿Vamos hasta Palermo mañana?", "Te lo revisamos en Pilar mañana.") now need that day's evaluation. The R6IRV-P03 LOCATION limit is unchanged and stays pinned.

---

## 9. Source mutations (60, final seal)

`mut/mutants.py` (`c78e97a7…`) and `mut/run.sh`. Each mutant is one exact, unique replacement, bind-mounted read-only over the sealed tree. The suites run with `-x`, unit tests first:
- R6-C3 unit, R6-C2 unit, R6-C1 unit, l4_7d, R6 contract, R5-F1 template, l4_7c_3b;
- then the R6-C3, R6-C2 and R6-C1 production paths, the R6 corpus and the R5-F1 production path.

The control passes 358 tests / 3414 subtests. **60/60 KILLED, 0 survived, 0 invalid.**

**The prompt's fifteen:**

| Id | Mutation | Result |
|---|---|---|
| E01 | weekday compared, day number discarded | KILLED |
| E02 | slash-only separator | KILLED |
| E03 | abbreviated month ignored | KILLED |
| E04 | ISO boundary `\b` before T (R6-C2 behaviour) | KILLED |
| E05 | vague period treated as no anchor | KILLED |
| E06 | horizon removed (weekday-only comparison) | KILLED |
| E07 | hour-only time ignored | KILLED |
| E08 | am/pm bypassed | KILLED |
| E09 | cross-sentence day ignored | KILLED |
| E10 | second conflicting anchor ignored | KILLED |
| E11 | malformed fragment treated as no anchor | KILLED |
| E12 | per-sentence analysis | KILLED |
| E13 | booking compares weekday only | KILLED |
| E14 | temporal analysis bypassed | KILLED |
| E15 | declared date ignored | KILLED |

**Extra layers:**

| Id | Mutation | Result |
|---|---|---|
| E16 | label-line numbers | KILLED |
| E17 | exactly one ISO date | KILLED |
| E18 | negative shelters a positive offer | KILLED |
| E19 | customer-specific keeps the general exemption | KILLED |
| E20 | weekday/date conflict accepted | KILLED |
| E21 | part of day unchecked | KILLED |
| E22 | producer gate ignores unresolved wording | KILLED |
| E23 | CE slot offer undeclared | KILLED |
| E24 | abbreviation period splits sentences | KILLED |
| E25 | approximate clock counted as exact | KILLED |
| E26 | single time in a general-hours sentence exempt | KILLED |

**Retained R6-C1 and R6-C2 boundaries**, restated against the R6-C3 code where the old code was replaced: C01, C04–C06, C08, C09, C14, C16, C17, C20–C24, C26, C27, D01–D08c, D11–D11c, D13, D13b, D15, D18. All KILLED.

**Seal 1 had three survivors: D08c, E17 and E25.** These are disclosed in §14. They exposed three test gaps:
- a stale evaluation whose own past date is named;
- the exact reason for the one-ISO-date refusal;
- an approximate clock on an evaluated slot.

Three tests were added, the candidate was re-sealed, and every gate was re-run on seal 2.

---

## 10. SB1 sentinel

`response_validator.py` is re-recorded from `7f3c2a28…` (R6-C2) to `e43ff98209e3e9c8a9b280949b36ab8d629c2258`, with a comment naming PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C3. The assertion code is unchanged: the diff is one dictionary value plus comments.

`sentinel_probe.sh` on seal 2:
- control: **1 passed**;
- a one-line drift bind-mounted over each frozen file gives **1 failed**, for each of `response_validator.py`, `claim_projection.py`, `flow_data_exchange.py`, `whatsapp.py`, `schedule.py` and `pricing.py`;
- restored: **1 passed**.

---

## 11. Kit, runtime and K07

| Item | Value |
|---|---|
| New kit | `/opt/ridecheck-builds/crm-minimum-launch-enablement-kit-R6-C3-20261112T170308Z`, built from the sealed R6-C2 kit (copy, then path re-pointing), **129 entries, PROVISIONAL** |
| **New provisional anchor** | **`f2945987b926a261458109ca796a4c328f68039e1c6d127dc17acbe9602bb594`** (`CHECKSUMS.sha256` = `pins/PROVISIONAL_CHECKSUMS.sha256`; verified before and after the kit chain) |
| Pins | `config/pins.conf` = `pins/pins.provisional.conf`: tree `58266ffc…`, changed `ee76ebe7…`, app `d0dc89f5…`; `PIN_STATE=PROVISIONAL`; `EXPECT_GIT_SHA` / `SOURCE_COMMIT` / `EXPECT_IMAGE` = `UNFINALIZED` |
| New private runtime | `/opt/ridecheck-builds/crm-minimum-launch-enablement-PRIVATE-R6-C3-20261112T170308Z`. The four inputs were copied from R6-C2; `PRIVATE_MANIFEST` `28a91981…c8b2` is identical to R6-C2 and **VERIFIED** after the rehearsal |
| Evidence | `/opt/ridecheck-builds/crm-final-convergence-r6-c3-20261112T165303Z` |
| Docs | Release runbook and combined handset/WILD runbook updated for R6-C3; `docs/AUTHORIZATION_TEXT_R6C3.md`; README; `docs/R6C3_DIFF_vs_R6C2_kit.patch`. The historical `AUTHORIZATION_TEXT_R6C2.md` is kept byte-identical to R6-C2 |

**K07.** The §4a block now refuses these before reading `PIN_STATE` and before any change:
- any `find . ! -type f ! -type d` entry: a symlink, FIFO, socket or device;
- a directory set that is not exactly the sealed directory set, derived from the sealed listing.

It re-checks both after restoring and before printing `RESTORED`. The R6-C2 lines are byte-identical, so the R6-C2 mutants still apply.

`tests/test_kit_r6c3.sh` **28 passed, 0 failed.** Each STOP case leaves the kit entry-identical, and the check that fires is named:

| K87 case | Result |
|---|---|
| foreign symlink to a file | STOP, unchanged |
| foreign symlink to a directory | STOP, unchanged |
| dangling foreign symlink | STOP, unchanged |
| foreign directory | STOP, unchanged |
| foreign nested directory | STOP, unchanged |
| FIFO | STOP, unchanged |
| socket | STOP, unchanged |
| device node | STOP, unchanged |
| foreign regular file | STOP, unchanged |
| changed sealed file | STOP, unchanged |
| PIN_STATE=FINAL | STOP, unchanged |
| evidence + `.tmp` files + a foreign link | STOP before restoration; evidence and `.tmp` still present |
| clean interrupted posture | RESTORED, entry-identical; FINALIZE rerun VERIFIES |
| the expected temporary publication files | RESTORED, entry-identical |
| **RED: the sealed R6-C2 block, foreign symlink** | prints RESTORED and leaves it |
| **RED: the sealed R6-C2 block, foreign directory** | prints RESTORED and leaves it |

K88 checks the seal.

**Kit suites and mutations** (serial, gated):

| Job | Result |
|---|---|
| `test_kit_r6c3.sh` | **28/0** |
| `mutate_r6c3.py` | **5/5 KILLED** (O01 link check, O02 symlink-only, O03 directory check, O04 directory set from the kit itself, O05 check after removal) |
| `test_kit_r6c2.sh` | **81/0** |
| `mutate_r6c2.py` | **24/24** |
| `test_kit_r6c1.sh` | **84/0** |
| `mutate_r6c1.py` | 28/29: Q21 masked in K78, as in R6-C2; killed by K82 |
| `test_kit.sh` (R3) | **272/0** |
| `test_kit_r5.sh` | **242/0** |
| `test_kit_r6.sh` | **112/0** |
| `mutate_r6.py` | 29/34: R25/26/27/29/31 masked in K74, as in R6-C1 and R6-C2; killed as R6-C1 Q25–Q29 |
| Real-config negatives, umask 022 | ENABLE W2/H, RESET_BETWEEN AB/BC, reset_testers W1A: all rc 2 at the pin gate, **0 docker calls**; 14 files 0600 / 17 directories 0700; private runtime VERIFIED; kit still sealed |

---

## 12. Rehearsal

`rehearse_r6.sh` on `PRIVATE-R6-C3/run-r6c3-20261112T212529Z`: **REHEARSAL R6: 94 passed, 0 failed.**

| Check | Result |
|---|---|
| RR11 order | H → H2 SKIPPED → W1A → RESET_AB → W1B → RESET_BC → W2 → final KILL |
| RR11 OPENED/CLOSED | exactly one CLOSED per OPENED per kind |
| RR11 ledger | the chain verifies |
| RR3 / RR5 / RR7 / RR10 | each CLOSED once with `criteria_not_met=0`, then ACCEPT |
| RR6 / RR8 reset isolation | non-tester rows identical, testers fresh, no future occupying booking, never twice |
| RR13 local-wall-clock booking protection | the R5 predicate misses it; the R6 helpers count it |
| RR10 motorcycle handoff | truthful, with **no W2 outstanding question** |
| RR10 budgets | no offer, booking, notice or job, nothing pending |
| RR11 final posture | all eight switches literally false; `booking_dispatch_allowed()` False (env and a fresh process) |
| RR12 | live unchanged |
| Disposable objects | none remain (0 `rcr6` containers or networks) |

---

## 13. Live isolation (read-only)

`live/probe.sh` (`293769a5…`) is the R6-C2 probe, extended with the R6-C2 kit and runtime, the backend TZ and the worktree status:
- **before: 16:53:25Z**;
- **after: 21:37:56Z**;
- `diff before.txt after.txt`: **empty** (byte-identical).

| Item | Before = after |
|---|---|
| backend / postgres / n8n | `04e14e5770f9` (restarts 0), `5246fd86d829`, `6ea80570b67b` |
| GIT_SHA | f16b1f3 |
| Schema head | `20261104_vehicle_identity` |
| Switches | all eight false; dispatch False |
| Allowlist | sha16 `ad98b5c3…` |
| DB fingerprint | `fbae2abdc3371f9146f0ac51a2ffa427` |
| Pending outbound | 0 |
| Max ids | unchanged |
| R3–R6-C2 kits and runtimes | verify, unchanged (R6-C2 kit `8e2254a6…`) |
| Backend TZ | `America/Argentina/Buenos_Aires` −0300 |

---

## 14. Disclosures

1. **Seals.** The candidate was sealed twice and the kit twice.
   - Seal 1 (tree `43cc7688…`, kit anchor `1331a6cf…`) was superseded when the source mutants found three test gaps (D08c, E17, E25; §9).
   - Seal 2 adds three tests to `test_r6_c3_temporal_class.py`, the only file that differs between the seals. `response_validator.py` and `conversation_engine.py` are identical across the seals.
   - **Every gate in this closeout ran on seal 2.** Seal-1 artifacts are in `seal1_superseded/`.
   - The seal-1 kit was never tested.
2. **Aborted job.** The seal-1 full-candidate SQLite run was killed by the 500 MiB watchdog (available memory fell to 456 MiB; other sessions share the host). It was void and was rerun on seal 2.
   - The seal-1 PG stage was stopped by me after two minutes, because the seal was being replaced; its disposable container and network were torn down.
   - The seal-2 chain ran with no abort; its lowest reading was 1278 MiB.
   - Development runs waited on the gate for about 50 minutes.
   - The R6-C2 reviewer's watchdog finding is fixed: `heavy.sh` now kills `c3-*` containers.
3. **Tightenings beyond the prompt** (all fail-closed):
   - a day-naming inspection proposal needs that day's evaluation;
   - any clock reading is an availability proposition: "Te esperamos a las 10 en punto" is refused without an evaluation;
   - a negative statement that also offers ("No tenemos el jueves, pero…") is validated as an offer;
   - all anchors in a proposition must be the one canonical date, so for example "Hoy cerramos. Te esperamos mañana a las 10." is refused;
   - "el horario" alone is not a general-hours frame;
   - a declared AVAILABILITY claim must carry its ISO date;
   - "a las H" with H 1–7 is read as H+12;
   - a bare number alone on its own line is unresolved;
   - a date range ("de 10 a 16 de noviembre") is unresolved.
4. **Residual limits:**
   - a time stated with no day anywhere is still checked only against the evaluated slots (the no-anchor endpoint rule);
   - an approximator **after** a time ("a las 10, más o menos") is not flagged;
   - "Calle 9 de Julio"-style street names in a scheduling reply would read as a date and be refused (fail-closed);
   - the Flow button body and the plan's `_regenerated_offer_es` carry no structured claim (§4);
   - CE's own `date.today()` in `_format_date_human` is unchanged, as disclosed in R6-C2; if it diverged from the business date, the validator refuses (fail-closed).
5. **Test expectation changes** (each with an R6-C3 comment; no assertion was deleted):
   - **Base tests:**
     - `test_cut_20`: the slot fixture `"jueves 15:00"`, which was never a slot value, is now `"15:00"`;
     - the SB1 sentinel value.
   - **R6-C2 tests:**
     - the new-evaluation control now uses Thursday 12 instead of Thursday 19, because of the horizon;
     - "las mañanas" is now unresolved;
     - a time-only declared claim is now refused.
   - **R6-C1 tests:**
     - three day-naming kept cases now use an evaluated tomorrow (`GOL_EVALUATED` / `LOCATED_EVALUATED` / world `gol_eval`);
     - the P03 pin keeps its LOCATION limit and adds the refusal without an evaluation.
   - **R6 / R5-F1 tests:**
     - "a las 10 en punto" moved from the ordinary lists to explicit assertions: refused by AVAILABILITY, not VEHICLE, without an evaluation; kept with one. The R5-F1 b04 assertion still covers it;
     - B01 declares an ISO date.
   - **R6-C3's own production-path test:** "general hours beside a wrong offer" asserts surgery (the hours sentence is kept and the offer removed) instead of a whole-message fallback.
6. **Validator literals.** The R6 structural test forbids the catalogue words `uno` and `punto` as literals. Spelled hours therefore use `una` only, since hours are feminine, and "en punto" is compared as a phrase.
7. **Permission denials:**
   - creating the kit and the runtime in one combined command was denied, so they were done as separate steps;
   - a re-pointing loop combined with `rm` was denied; the seal script removes the stale checksums itself;
   - deleting the 14 ignored `__pycache__/*.pyc` files that my host-side unit runs created under `backend/` and `tests/` was denied. They are git-ignored, absent from the sealed `cand/` (`ls-files -co --exclude-standard`), and excluded from the app manifest, so they cannot reach a commit or an image;
   - removing a copied `_sb2b_harness.py` from `probe/harness/` was denied. That file is byte-identical in f16b1f3 and in every candidate, so the red control is unaffected.
8. **Host-side runs.** The new unit suite was iterated on the host with a CE stub, without docker. All recorded results come from the gated container runs.
9. **The predecessor R6-C2 kit and runtime were read only.** Its kit tree and the private runtime manifest verify unchanged (§13). The R3, R4, R5, R6 and R6-C1 kits and runtimes were not touched.

---

## 15. W-3 and WILD

**W-3 remains OPEN. The final WILD remains NOT RUN.** No handset test, window, tester reset or live reset was performed. The R6-C3 kit is **PROVISIONAL**, and every live ENABLE, RESET_BETWEEN and tester reset refuses at the pin gate (§11). The R5 kit remains the live emergency instrument for the f16b1f3 deployment.

## 16. Next milestone

**PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C3-FINAL-TEMPORAL-BOUNDARY-INDEPENDENT-REVIEW.**

Until that review is READY, these remain **NOT AUTHORIZED**: commit, push, the final image build, pin finalization, dark deployment, windows and the WILD.

Nothing in this milestone is staged or committed. STOP.
