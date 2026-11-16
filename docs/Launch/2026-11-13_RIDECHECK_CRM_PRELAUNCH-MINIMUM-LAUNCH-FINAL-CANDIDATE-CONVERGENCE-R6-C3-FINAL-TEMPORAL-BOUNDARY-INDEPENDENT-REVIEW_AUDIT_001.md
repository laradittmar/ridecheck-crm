PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C3-FINAL-TEMPORAL-BOUNDARY-INDEPENDENT-REVIEW

# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — FINAL CANDIDATE CONVERGENCE R6-C3 — FINAL TEMPORAL-BOUNDARY INDEPENDENT REVIEW — AUDIT 001

Date: 2026-11-13 (review runs 01:00Z–03:30Z).
Reviewer: a fresh, independent reviewer. I did not implement R6, R6-C1, R6-C2 or R6-C3.
Source candidate: `/opt/ridecheck-crm-r5-f1-response-validation`, branch `fix/prelaunch-r5-f1-response-validation-boundary`, base = HEAD `f16b1f328f3fbf1050afdcec583d252d528c72bf`.
R6-C3 kit: `/opt/ridecheck-builds/crm-minimum-launch-enablement-kit-R6-C3-20261112T170308Z`, provisional anchor `f2945987b926a261458109ca796a4c328f68039e1c6d127dc17acbe9602bb594`.
R6-C3 evidence (read only): `/opt/ridecheck-builds/crm-final-convergence-r6-c3-20261112T165303Z`.
Reviewer evidence: session scratch directory only (`…/scratchpad/rv/`, mode 0700). Nothing was written under `/opt/ridecheck-builds`.

I read these completely:
- the R6-C3 closeout (`…R6-C3_CLOSEOUT_001.md`);
- the R6-C2 final focused review audit (`…R6-C2-FINAL-FOCUSED-INDEPENDENT-REVIEW_AUDIT_001.md`).

# VERDICT: CORRECTION REQUIRED

The R6-C3 architecture is a real improvement. It runs one analysis over the whole message, returns closed states, binds across sentences and checks weekday/date agreement. It also fixes every form the R6-C2 review listed: I reproduced 0/84 of the closeout's red forms delivered.

But it does **not** close R6C2RV-S01 as a class. The residual scan is still a finite list of positions, not a default-deny scan, and the negative-availability exemption skips the day and time check entirely. So natural wording still slips through. Wrong-date and wrong-time replies are **delivered verbatim, as `AI_COMPOSED` with no finding**, through the real production path:

handle_message → CE → reply model → admission → `_send_text_to_wa` → envelope → `plan.compose` → OutboundSafetyGate → Meta recorder.

This happens in all four postures, on SQLite and on PostgreSQL 16.13, byte-identically, and in the confirmed-booking posture too.

| ID | Severity | Summary |
|---|---|---|
| **R6C3RV-S01** | **HIGH, blocking** | Wrong date delivered. Shapes: a negative sentence sheltering a positive offer; unconsumed day numbers; year-first dates (**a regression vs R6-C2**); relative offsets; same-weekday-next-week when today is that weekday; booking restatements |
| **R6C3RV-S02** | **HIGH, blocking** | Wrong time delivered. Spelled hours not introduced by "a las", 24-hour spelled hours, and minute words are not read and not flagged, including a booking restatement |
| **R6C3RV-T01** | **HIGH, blocking (tests)** | 9 of my 15 source mutants survive, and each one permits a wrong date or time with the whole relevant suite green (410 tests / 3463 subtests) |
| R6C3RV-P01 | MEDIUM (state, via S01) | The CE producer gate `stated_day_is` returns True for every S01 form, so an AI-proposed time can still be stored for a reply that names another day. Proven by unit; not reached on the full path (§6) |
| R6C3RV-K08 | LOW, fail-closed | K07 block: a file name containing a newline gets past the pre-checks. The block then modifies the kit (restoration steps only) before it STOPs. Mode bits are not compared |
| R6C3RV-I01…I04 | INFO | §14 |

Everything else I checked holds:
- identity, pins and seal reproduce byte-for-byte;
- the test changes are disclosed, justified and fail-closed;
- SQLite is NEW 0 (only the sentinel GONE), with no skip or inventory loss;
- PostgreSQL is NEW 0 / GONE 0;
- deterministic slot offers are bound to validated structured claims;
- AI prose gets no structured trust;
- R5-F1 / R6 / R6-C1 / R6-C2 boundaries are preserved;
- ordinary prose, business hours and plain negatives are not broadly damaged;
- K07 is corrected for every link, special-file and directory posture;
- the kit is PROVISIONAL and refuses every live action;
- live is unchanged.

Nothing was staged, committed, pushed, built, finalized, deployed or migrated. No flag, allowlist or live data changed. Meta was not contacted and nothing was sent. **W-3 remains OPEN. The final WILD remains NOT RUN.**

---

## 1. Identity, manifests and hashes

- `rev-parse HEAD` = `merge-base HEAD f16b1f3` = f16b1f3; 0 commits ahead; `diff --cached` empty.
- I exported the worktree independently (`git ls-files -co --exclude-standard`, 731 files). It equals the sealed `cand/` (730) plus the post-seal closeout, with 0 content differences.
- My `git archive f16b1f3` gives 710 files, manifest `7282f596…09b4` = the sealed `base/`.
- Pins recomputed from my export, each `cmp`-identical to the sealed manifests:
  - tree outside docs/Launch, 682 files: `58266ffcaf98f6ef0c0c941e6d30473bb3cb6afb58ba09fdd0b7e9e42d62e3bd`;
  - `backend/app`, 137 files: `d0dc89f50bf21e7279dedefd8893ae64d9ff727997c6e055360d9299805c87ad`;
  - changed paths, 18: `ee76ebe7c3134e56534a975c59685c1b8f6fdfee261f0c634f026f4141df69ab`. Every per-path sha256 equals closeout §1.
- `response_validator.py` git blob = `e43ff98209e3e9c8a9b280949b36ab8d629c2258` = the SB1 sentinel value.
- Kit:
  - `sha256sum -c` passes, 129 entries;
  - `CHECKSUMS` = `PROVISIONAL_CHECKSUMS` = `f2945987…b594`;
  - 0 links or special files;
  - `config/pins.conf` is `cmp`-equal to `pins/pins.provisional.conf`;
  - `PIN_STATE=PROVISIONAL`, and the three keys are `UNFINALIZED`.

**Identity: no drift.**

## 2. Test changes

- No skip, xfail or marker was added (0 added lines).
- The only assertion removed against base is R6's reviewed `test_validation_failure_never_breaks_a_turn` tightening, carried unchanged.
- Against the sealed R6-C2 test files, every removed line matches a closeout §14.5 disclosure:
  - "a las 10 en punto" moved out of the ordinary lists (R5-F1 template, R6 corpus);
  - the R6-C1 day-naming kept cases moved to an evaluated tomorrow (`gol` world);
  - "las mañanas" now unresolved;
  - a time-only declared claim now refused;
  - the R6-C2 new-evaluation control moved from Thu 19 to Thu 12 (horizon).
- Base test changes:
  - `test_cut_20` slot fixture `"jueves 15:00"` → `"15:00"` (plus a refusal assertion);
  - the SB1 dictionary value plus comments.
- Each change is a fail-closed tightening or a fixture correction. **No safety contract was weakened or silently redefined.**

## 3. Temporal architecture (code read)

| Element | Ruling |
|---|---|
| Complete-message analysis | **Holds.** `analyze_temporal` scans every sentence once; `temporal_verdict` rules once; every involved sentence gets the same finding |
| Four closed states | **Holds** as an enum. But see S01: a sentence that the scanner does not fully understand can still end in `FULLY_PARSED_AND_CANONICAL` |
| Residual / unconsumed-fragment detection | **Does not hold as a class.** The residual scan flags a 1–2-digit number only in listed positions: after el/del/al/dia/las/tipo, before a time word or month, after a month, after a weekday *word*, after a connector next to a placeholder, or on a label-only line. Spelled numbers are flagged only after el/dia/del/al. A number after a **placeholder** (a consumed weekday), inside brackets, after "nº", or a spelled number after a weekday is not flagged. Unconsumed hour words (`las doce`) and minute words (`y veinte`) are never flagged |
| Date/time normalization | **Partly.** Digits, am/pm, hs, `a las H`, quarters and parts of the day are correct. Spelled 13–24 hours, minute words, and spelled hours after `para/desde/de/hasta las` are not read. Year-first `YYYY/MM/DD` and `YYYY.MM.DD` are neither read nor flagged |
| Cross-sentence binding | **Holds** (newline, bullet, label lines; my K-controls kept, wrong forms refused) |
| Multiple / conflicting anchors | **Holds** for recognised anchors ("el 16 o el 23", "lunes 16 o martes 17", "16 … o el jueves") |
| Weekday/date agreement | **Holds** for digits ("martes 16", "lunes 16/12", "lunes 16 de diciembre" refused) |
| Bare-weekday horizon | **Holds** for `[today, today+6]`. **Defect:** a definite or "próximo" weekday equal to today's weekday resolves to *today*. "el próximo lunes" / "el lunes que viene" said on Monday is read as today (S01-e) |
| General-hours exemption | **Holds** for my controls. A general sentence's `vague` list is cleared and not tested; that is information only, not a slot (INFO) |
| Negative-availability exemption | **Does not hold.** A sentence matching `_NEGATION_RE` without one of 15 listed positive markers is removed from the proposition (`rel`), and `_check_availability` then allows it on any evaluation. Its days and times are **never compared**. Markers not in the list: "sí", ";", ",", "excepto", "salvo", "solo" (S01-a) |
| Confirmed-booking values | **Holds** for the source: `booked_date` / `booked_times` come from the committed revision only. **Fails** for every S01/S02 form, which reach the booking branch unread |
| Structured deterministic AVAILABILITY | **Holds.** It requires exactly one strict ISO date = the evaluated, non-stale date, and every `HH:MM` must be an evaluated slot; it is validated before the words, and the words are still analysed. Mutant R15 is killed. R14 (stale declared date) survives but is masked: the builder never puts a stale date into the facts (§7) |
| AI output gets no structured trust | **Holds.** Provenance comes from the exact type; AI prose is `AI_COMPOSED` in every probe row |
| Flow button body | No structured claim. Validated by the full analyzer; its `Para {date_human}` comes from the same `day_iso` (CE 13740–13760). Fail-closed if `date.today()` diverges (§8) |
| `_regenerated_offer_es` | Dayless restatement of slots the thread really holds (`protected`), rendered after validation. No AI words. Residual (§8) |

## 4. Independent adversarial corpus (pure validator)

Scratch probes `p1.py` / `p2.py` with four corpora (`c1`–`c4`, about 180 cases). Posture: evaluated Mon 2026-11-16, business today Wed 11, slots 10:00 / 11:00 / 15:00 / 15:30. Plus: today = Mon 16; booked Mon 16 10:00–10:45; no evaluation.

**Correct and canonical: kept.** None of these was refused:
- digits with `hs`, `h`, `10hs` and `10am`;
- `a. m.`, "3 de la tarde", "tres de la tarde", "15.30 hs", "3 y media", "11 en punto";
- `16/11/2026`, `16-11-26`, "16 de noviembre de 2026", `2026-11-16`, "16 de nov. a las 10 hs";
- a cross-line day and time;
- bullets;
- question forms;
- "hoy lunes" on Monday.

**Refused correctly:**
- every R6C2RV-S01 reviewer form;
- digit weekday/date conflicts;
- "lunes 23-11" and "lunes 23 nov";
- the bracketed form "(el 23)" and "(23/11)";
- two anchors with one valid;
- "este fin de semana", "la próxima semana", "en una semana", "dentro de dos semanas";
- "primer lunes de diciembre", "último lunes del mes", "el veintitrés";
- "a eso de las 10", "cerca de las 10", "tipo 10", "y pico", "a partir de las 10";
- 25:00, 10:75, "lunes 32", 31/11;
- part-of-day conflicts;
- a non-slot time on the right day ("9", "12:30", "10 de la noche", "9 y media", "4 de la tarde", "mediodía", "entre las 10 y las 12").

**Ordinary prose, business hours and negatives: not damaged.** All of these were kept:
- vehicle models and years (Corolla 2018, Fiat 500, Peugeot 3008/2008, Gol Trend 1.6 del 2015);
- "150 puntos", "80.000 km", "3 dueños", "2 técnicos", a phone number;
- "Calle 25 de Mayo 300", "Av. 9 de Julio 1500" when nothing is scheduled;
- "en 24 hs", "unas 2 horas", "entre 1 y 2 horas";
- "Atendemos de lunes a sábado de 9 a 18 hs", "Los sábados atendemos de 9:30 a 14 hs", "Respondemos todos los días de 8 a 22";
- "Los domingos no trabajamos", "El domingo no trabajamos";
- "No tenemos turnos disponibles el jueves", "Para el jueves no hay horarios disponibles".

**Delivered although wrong** (fail-open; the lead case of every row, ids N1–N4, W1–W6, H1, T1, T2 and B1–B3, was reproduced on the full path in §5; the "—" and T3 variants are unit-level only):

| Id | Reply (evaluated Mon 16 unless stated) | What is wrong | Why it passes |
|---|---|---|---|
| N1 | "No hay turnos el jueves, sí el lunes 23 a las 10:00." | Mon 23 offered | negative exemption ("sí" is not a marker) |
| N2 | "No tenemos disponibilidad el jueves; el lunes 23 tenemos a las 10:00 y 11:00." | Mon 23 | negative exemption (";" is not a sentence break) |
| N3 | "No hay horarios libres el jueves excepto a las 10:00." | Thu 10:00 offered | negative exemption |
| N4 | "No hay turnos el jueves, sí el lunes 16 a las 12:00." | 12:00 is not a slot | negative exemption skips the time check too |
| — | "…, solo el lunes 23…", "…, el lunes 23 hay…", "No quedan turnos el lunes 16 salvo el jueves 19 a las 10:00." | wrong day | same |
| W1 | "Te esperamos el lunes veintitrés a las 10:00." (also question and lexicon forms) | the 23rd | spelled day after a weekday: not consumed, not flagged |
| W2 | "Te esperamos el lunes (23) a las 10:00." / "[23]" / "lunes nº 23" | the 23rd | number after a consumed weekday placeholder: not flagged |
| W3 | "Te esperamos el 2026/11/23 a las 10:00." / "2026.11.23" | the 23rd | year-first not read. **R6-C2 refused this; R6-C3 delivers it** |
| W4 | "Te esperamos en 12 días a las 10:00." / "en 3 días" / "dentro de 5 días" | another day | relative offset: not read, not flagged → day-blind endpoint rule |
| W5 | "Te esperamos el lun 23 a las 10:00." | the 23rd | weekday abbreviation not recognised |
| W6 | "Te esperamos el otro lunes a las 10:00." / "el lunes siguiente" / "el lunes que viene no, el siguiente" | Mon 23 | qualifier ignored; resolves to Mon 16 |
| H1 | today Mon 16, evaluated Mon 16: "Te esperamos el próximo lunes a las 10:00." / "el lunes que viene" | the customer reads Mon 23 | horizon includes today; the lead word is ignored |
| T1 | "Te esperamos el lunes 16 a las dieciséis." / "a las trece y treinta" | 16:00 and 13:30 are not slots | 13–24 spelled hours not read, not flagged |
| T2 | "Te esperamos el lunes 16 a las diez y veinte." / "diez menos diez" | 10:20 / 09:50 | read as 10:00; the minute word is ignored |
| T3 | "Te reservo el lunes 16 para las doce." / "desde las nueve" / "de nueve a doce" / ", a eso de las nueve" | 12:00 / 09:00 | a spelled hour is read only after "a las" |
| B1–B3 | booked Mon 16 10:00: "Tu turno es el lunes veintitrés a las 10:00." / "el 2026/11/23 a las 10:00" / "el lunes 16 a las dieciséis" / "para las doce" / "el lun 23" / "el otro lunes" / "en 5 días" | wrong committed date or time | same gaps, booking branch |

**Disclosed residual limits, ruled:**
- **Time with no day** stays on the R6-C1 endpoint rule. It is acceptable by itself, but W4 turns it into a wrong-day route.
- **An approximator after the time** ("a las 10, más o menos", "10 aprox", "sobre las 10", "después de las 10", "antes de las 11", "a primera hora") is delivered. On an evaluated slot this is approximate but not wrong. **LOW**, folded into the S02 correction.
- **"9 de Julio"** in a scheduling reply is refused ("Te esperamos el lunes a las 10:00 en Av. 9 de Julio 1500." → fallback). This is **fail-closed**. It damages a real address sentence, but the customer is not misled. LOW.
- **`date.today()` vs the business date**: fail-closed (§8).
- **Flow button and `_regenerated_offer_es`**: no AI words and no deliverable wrong proposition found (§8).

## 5. Production path, all postures

`fp.py` (sha16 `c9925162bf0e973f`) uses `run_case` of the candidate's own `test_r6_c2_day_date_production_path` harness, with the interlock armed. It covers the four postures: direct webhook false/true × Smart Booking false/true. Every row shows `armed=True`, `ai_calls=1` and exactly 1 message sent.

| Tree | Wrong replies delivered verbatim (16 × 4) | Correct controls kept (3 × 4) |
|---|---|---|
| f16b1f3 | 64/64 | 12/12 |
| R6-C2 seal | 56/64 (refused only W3 / B2, the year-first date) | 12/12 |
| **R6-C3** | **64/64** | 12/12 |

On R6-C3, every wrong row is `AI_COMPOSED` with `refused=[]`. `preferred_time` and `preferred_day` are None. There are 0 new revisions and 0 outstanding questions. So the harm is customer-visible false scheduling text: a wrong day or time offered, or a confirmed booking restated with a wrong date or time.

The output sha16s are:
- base `11bd4ec1eb813e68`;
- R6-C2 `776030dd6fe3061b`;
- R6-C3 `4b53438dd4042fa2`.

**PostgreSQL 16.13 (`TimeZone=Etc/UTC`) is byte-identical to SQLite** for R6-C2 and R6-C3.

**Reachability.** The chain the R6-C2 review accepted applies unchanged:
1. reply rule 4 makes the model confirm the customer's day;
2. CE resolves the customer's "el lunes 23" to the next Monday;
3. the model restates the customer's words with that evaluation's times.

N1–N4 need no model error at all beyond ordinary phrasing. "No hay turnos el jueves, sí el lunes…" is the natural Rioplatense way to explain a rejection and offer an alternative, which is exactly what the text conversation is told to do.

## 6. State path (R6C3RV-P01)

`stated_day_is` (the CE producer gate behind `_ai_time_day_is_evaluated`) returns **True** for W1–W6, H1, T1–T3 and B1–B3 (`p2.py` prints it per case). In `STAGE_SCHEDULING` with `active_requested_date` set, an AI reply such as "¿Te reservo el lunes veintitrés a las 10:00?" therefore passes both producer gates. The R6 slot gate and the R6-C2 day gate hold `preferred_time=10:00`, and the next "sí" would open the booking path for Mon 16.

I could not reach that block through the shared harness: the seeded customer line diverts a SCHEDULING turn to handoff (`fp2.py`, 0 stored times on both R6-C2 and R6-C3). So this is a unit-level finding.

The mitigation is that the Booking Flow shows the real date before the customer confirms. **MEDIUM.** It is corrected by the same S01 fix.

## 7. Reviewer source mutations (15, materially different from the supplied 60)

Each mutant is one exact, unique replacement in `response_validator.py`, bind-mounted read-only over my export. It runs with `-x` over the R6-C3, R6-C2 and R6-C1 unit suites, l4_7d, the R6 contract, the R5-F1 template, l4_7c_3b and g3_6, then the R6-C3, R6-C2 and R6-C1 production paths, the R6 corpus and the R5-F1 production path.

The control passes **410 tests / 3463 subtests**.

| Id | Weakening | Result | Consequence when it survives (pure probe, candidate refuses) |
|---|---|---|---|
| R01 | `a las H` (1–7) not read as afternoon | KILLED | — |
| **R03** | yearless date compared by day only | **SURVIVED** | "Te esperamos el 16/12 a las 10:00." and "el 16 de diciembre" delivered for Nov 16 |
| **R05** | "pero" removed from the positive-offer markers | **SURVIVED** | "No hay turnos el jueves, pero el lunes 23 hay a las 10:00." delivered |
| **R07** | a number before a month word not residual | **SURVIVED** | "Te esperamos 23-nov a las 10:00." delivered |
| **R08** | leftover hs/am/pm not residual | **SURVIVED** | "Te esperamos el lunes a las once pm." delivered |
| **R09** | "menos cuarto" keeps the hour | **SURVIVED** | "10 menos cuarto" read as 10:45: a wrong time on a 10:45 slot set |
| R10 | "y media" reads :00 | KILLED | — |
| R11 | bare weekday never resolves to today | KILLED | — |
| R12 | booking may restate any offered slot | KILLED | — |
| R14 | declared AVAILABILITY claim accepts a stale date | survived, **masked**: `_canonical_availability_date` never passes a stale date (D08b) | none on the production path; defence-in-depth only |
| R15 | declared claim needs only one valid slot | KILLED | — |
| **R16** | producer gate: one canonical day suffices (`any`) | **SURVIVED** | `stated_day_is("¿Te reservo el lunes 16 o el jueves 19 a las 10:00?")` = True, so a time could be stored for a two-day reply |
| **R18** | a negative sentence exempt even under a confirmed booking | **SURVIVED** | "No tenemos turnos el jueves; tu turno es el lunes 23 a las 10:00." delivered for a Mon-16 booking |
| **R20** | an ISO datetime's time not read | **SURVIVED** | "Te esperamos el 2026-11-16T12:00." delivered (12:00 is not a slot) |
| **R22** | "esta tarde/mañana" no longer states today | **SURVIVED** | "Te esperamos esta tarde a las 15:00." delivered for a Mon-16 evaluation |

That is **5 killed, 1 masked, 9 surviving with a wrong date or time permitted.** Under the prompt's rule, each is a blocking test gap. The supplied 60/60 is consistent with this: those mutants target the rules the suite pins, and mine target the normalisation and exemption details the suite does not pin.

## 8. Residual-limit rulings

- **`date.today()` vs business date.** CE's `_format_date_human` labels hoy/mañana from the container clock. The validator uses Buenos Aires. If they diverged (TZ is pinned in compose; live backend `TZ=America/Argentina/Buenos_Aires −0300`), the label would mismatch the evaluated date. The full analyzer then refuses the deterministic offer and sends the fallback. **Fail-closed, non-blocking.**
- **Flow button body.** It contains only `Para {date_human}{window} tengo N horario(s)…`, composed from the same `day_iso` that `_dispatch_booking_flow_for_day` writes in the same call. It names no time, and the Flow screen itself carries the slots. Non-blocking.
- **`_regenerated_offer_es`.** It restates the minutes a rejected PRIMARY carried, only if they are in `last_visible_slots` / `last_offered_slots`, with no day. If the rejected prose had put those times on another day, the dayless restatement could be read in that day's context. I found no reachable instance: it needs a FAQ contradiction in the same reply. **LOW residual.** It would be closed by giving it the same structured AVAILABILITY claim and the evaluated date.

## 9. SQLite comparisons (memory-gated)

My `heavy.sh` waits for ≥ 1536 MiB, runs one heavy job at a time, and has a 500 MiB watchdog that kills `rvw-*` containers. Image `ridecheck-crm-test:l4.7b2`, `--network none`, read-only trees. The comparison uses my own `cmp.py`: failing identities and messages, skip identities and reasons, and case inventory.

| Run | Base f16b1f3 | R6-C3 | Comparison |
|---|---|---|---|
| Focused (75 files: the implementer's 74 + g3_6) | 2605 cases: 18 F / 2226 P / 361 S | 2903 cases: 17 F / 2525 P / 361 S | **NEW 0, GONE 1, COMMON 17, message changes 0; +298 / −0 cases; skips NEW 0 / GONE 0 / reason changes 0** |
| Full `../tests` | 8346: 71 F / 7489 P / 786 S | 8644: 70 F / 7788 P / 786 S | **NEW 0, GONE 1, COMMON 70, message changes 0; +298 / −0; skips 0 / 0 / 0** |

The only GONE is `UnchangedContracts::test_sb1_20_frozen_files_unchanged`, the authorized sentinel. The lowest memory reading was 1165 MiB (full candidate), with 0 aborts.

## 10. PostgreSQL (disposable)

I used my adaptation of the implementer's `pgstage.sh`, with `rvw-` names, my own exports and my own probes:
- a disposable `postgres:16` 16.13 on tmpfs, `--internal rvw-net`, `TimeZone=Etc/UTC`;
- one template per tree (create_all from the pre-schema record, stamp, `upgrade head` → `20261104_vehicle_identity` on both; columns and constraints equal);
- a fresh clone per suite, with all 19 `*_PG_URL` set.

| Item | Result |
|---|---|
| 29 PG suites | base 713 = candidate 713: 2 F / 598 P / 113 S. **NEW 0, GONE 0, message changes 0, skip changes 0** |
| SB2B with a clean DB | 1 F / 46 P / 1 S on both, identical |
| R6-C3 unit + production path on PG | 68 passed, 1025 subtests (500 threads, 52 revisions, 0 outstanding questions) |
| R6-C2 on PG | 67 passed, 350 subtests |
| R6-C1 frames path + corpus + contract on PG | 72 passed, 1471 subtests |
| R5-F1 production path on PG (engine override; 26 PG threads) | **26/0**, 0 outstanding questions |
| Reviewer full-path probe on PG | R6-C3 and R6-C2 byte-identical to SQLite (§5) |
| Teardown | 0 `rvw-` containers, 0 networks (checked after both PG runs) |

My first R5-F1 "PG" line (26 passed, 0 threads) ran on SQLite inside the container. I reran it with the engine override above. Only the rerun counts.

## 11. K07 (recovery block, runbook §4a)

I extracted the block (sha16 `6be5029c0b86a06d`) and ran it on 26 fresh `cp -a` scratch copies of the sealed kit. Each run snapshotted the full entry listing (type, mode, path, link target) plus content hashes before and after.

| Posture | Result |
|---|---|
| clean | RESTORED, unchanged |
| interrupted publication (evidence + `CHECKSUMS.sha256.tmp` + damaged CHECKSUMS) | RESTORED, entry-identical to the seal |
| with every `.tmp` | RESTORED, entry-identical to the seal |
| foreign symlink to a file / to a directory / dangling / named like a `.tmp` / as the evidence / CHECKSUMS as a symlink | **STOP before any change** |
| foreign directory / nested / hidden empty directory / `.tmp` name as a directory | **STOP before any change** |
| FIFO / UNIX socket / character device | **STOP before any change** |
| hidden foreign file / foreign hard link / missing sealed file / PIN_STATE=FINAL | **STOP before any change** |
| file name with a trailing newline (`pins/FINAL_PIN_EVIDENCE.txt⏎`) | the listing-based pre-checks pass. The block **removes the evidence and rewrites CHECKSUMS, then STOPs** at its final file-set check. No RESTORED (R6C3RV-K08) |
| a sealed script `chmod 4777` | **RESTORED**: modes are not compared (R6C3RV-K08) |

**R6C2RV-K07 is CORRECTED** for every link, special-file and directory posture. R6C3RV-K08 is LOW and fail-closed: it ends in STOP or a still-PROVISIONAL kit, and it needs a hostile writer. The recommended fix:
- refuse any name that contains a newline (`find . -name "$(printf '*\n*')"`) as check 0b;
- optionally compare modes against a sealed mode list.

## 12. Candidate, kit, runtime and rehearsal evidence

- The kit verifies (§1) and is PROVISIONAL.
- The implementer's real-config negatives (`neg_realconfig.txt`) show:
  - ENABLE W2 / H, RESET_BETWEEN AB / BC and `reset_testers` W1A all exit rc 2 at `kit_r6.sh:100` ("pins are 'PROVISIONAL', not FINAL");
  - 0 docker calls;
  - 14 files at 0600 and 17 directories at 0700.
- The code read confirms that the gate tests `PIN_STATE = FINAL` literally.
- The rehearsal evidence reads `REHEARSAL R6: 94 passed, 0 failed` (RR12 live unchanged).
- `PRIVATE-R6-C3` verifies (manifest `28a91981…`) and contains exactly the implementer's `run-r6c3-20261112T212529Z`.
- I did not rerun the rehearsal or the kit suites. They do not bear on the source findings, and they will have to be re-run on the corrected seal anyway. **The candidate cannot enable, reset or live-run before finalization.**

## 13. Live isolation (read-only)

My probe (sha16 `b00bb7c96330b4bd`) is the implementer's read-only `live/probe.sh` with three changes:
- the R6-C3 kit and runtime are added;
- container and network counts exclude my own `rvw-` objects;
- the worktree status excludes this audit.

Before was taken at 01:19:27Z, after my two first read-only probe containers (`--network none`) had already run. After is §15.

| Item | Before |
|---|---|
| backend / postgres / n8n | backend `04e14e5770f9` (image `sha256:1b8ed611…`), GIT_SHA f16b1f3 |
| Schema head | `20261104_vehicle_identity` |
| Switches | all eight literally `false`; `booking_dispatch_allowed=False` |
| Allowlist | sha16 `ad98b5c31a4e8ed8` |
| DB fingerprint | `fbae2abdc3371f9146f0ac51a2ffa427` (= closeout §13) |
| Pending outbound | 0 |
| Backend TZ | `America/Argentina/Buenos_Aires −0300` |
| R6-C3 kit | OK, checksums `f2945987b926a261` |
| R6-C3 private runtime | OK, manifest `28a919813b73fabb` |

## 14. Findings and required correction

### R6C3RV-S01 — HIGH, blocking (source): wrong date delivered

**Smallest counterexamples.** Full path, 4/4 postures, SQLite = PG, `AI_COMPOSED`, no finding:
1. **"No hay turnos el jueves, sí el lunes 23 a las 10:00."** (evaluated Mon 16). Expected: AVAILABILITY refused. Actual: delivered verbatim. Path: `_scan_sentence` sets `negative=True`, so `temporal_verdict` drops the sentence from `rel`, and `_check_availability`'s negative branch returns allowed.
2. **"Te esperamos el lunes veintitrés a las 10:00."** — the weekday resolves to Mon 16, and "veintitres" is neither consumed nor residual.
3. **"Te esperamos el 2026/11/23 a las 10:00."** — not read and not residual. **A regression: R6-C2 refused it.**
4. **"Te esperamos en 12 días a las 10:00."** — no day is read, so the time-only endpoint rule applies.
5. **"Te esperamos el lunes (23) a las 10:00."** / "el lun 23" / "el otro lunes".
6. On Monday, evaluated today: **"Te esperamos el próximo lunes a las 10:00."**
7. Booked Mon 16 10:00: **"Tu turno es el lunes veintitrés a las 10:00."**

**Required correction** in `response_validator.py`. This is the class rule the closeout claims, applied as default-deny:
- **(a) Negative exemption.** A negative sentence is exempt only when it states no time, no availability lexicon outside the negation, and no day outside the negation's scope. Otherwise every day and time in it joins the proposition. Do not rely on a marker list ("sí", ";", ",", "excepto", "salvo", "solo", "en cambio" all carry an offer). Keep the exemption under a confirmed booking off (R18).
- **(b) Default-deny residual.** In any sentence of a scheduling proposition, every number token (digits, or a spelled cardinal 1–31 / 0–24) and every weekday/month fragment that no recogniser consumed is `unresolved`, whatever its neighbour, placeholders included. Add the weekday abbreviations (lun, mar, mié, jue, vie, sáb, dom) as weekdays.
- **(c)** Read year-first `YYYY[/.-]MM[/.-]DD`, or flag it as unresolved (any separated token with a 4-digit part).
- **(d)** Relative offsets ("en/dentro de N días", "en N días hábiles") and weekday qualifiers ("otro", "siguiente", "que viene", "próximo" after the weekday) are unresolved. "próximo/que viene + today's weekday" resolves to today+7, or is unresolved.
- **(e)** The same analysis drives the booking branch and `stated_day_is` (R6C3RV-P01).

### R6C3RV-S02 — HIGH, blocking (source): wrong time delivered

**Smallest counterexamples** (full path T1/T2/B3; unit T3):
- **"Te esperamos el lunes 16 a las dieciséis."**
- **"Te esperamos el lunes 16 a las diez y veinte."**
- **"Te reservo el lunes 16 para las doce."**
- booked **"Tu turno es el lunes 16 para las doce."**

Expected: refused (not an evaluated slot or the committed time). Actual: delivered. Hour words are read only in `a las …`, 13–24 are not in `_SPELLED_HOURS`, and minute words after `y/menos` are ignored.

**Required correction:**
- read `las + spelled hour` after any preposition;
- read spelled 13–24 and spelled minutes, or flag them as unresolved;
- flag approximators after the time (`más o menos`, `aprox`, `sobre las`, `después/antes de las`, `primera hora`) as unresolved.

### R6C3RV-T01 — HIGH, blocking (tests)

Add full-path tests (4 postures, SQLite and PG) that kill R03, R05, R07, R08, R09, R16, R18, R20 and R22. Add every S01/S02 counterexample above with its canonical control. Re-run the supplied 60 plus these 15.

### R6C3RV-P01 — MEDIUM (state)

This is closed by S01(e). Add a unit test of `stated_day_is` for each S01 form and an R16-style two-day case.

### R6C3RV-K08 — LOW, fail-closed (kit)

As §11: refuse newline-bearing names first. Optionally compare modes.

### INFO

- **R6C3RV-I01.** The worktree holds 17 git-ignored `__pycache__/*.pyc` files (the closeout said 14). One, `backend/app/services/__pycache__/response_validator.cpython-312.pyc`, is dated 17:45Z, after the seal. All are absent from the seal, the app manifest and any `git archive`. Untouched (no write authority).
- **R6C3RV-I02.** The closeout's SQLite "passed" figures are one higher than mine on each side (7490 / 7789 vs my 7489 / 7788) with identical case totals: a counting method difference. Not material.
- **R6C3RV-I03.** "Av. 9 de Julio 1500" inside a scheduling reply is refused (fail-closed false positive, disclosed).
- **R6C3RV-I04.** `_regenerated_offer_es` is a dayless restatement (§8). It is recommended, not required, that it carry a structured claim.

### Dispositions

| ID | Disposition |
|---|---|
| R6C2RV-S01 | **Fixed for every enumerated form (0/84). Not closed as a class:** R6C3RV-S01 / S02 |
| R6C2RV-K07 | **CORRECTED** (§11). Follow-up R6C3RV-K08 (LOW) |
| R6IRV-S02 / P01 / T01 / C01, R5-F1, P02 / P03 / Q01 | remain closed or unchanged. Suites green on SQLite and PG; no ownership ruling reopened |

**Kit consequence.** Re-pin and re-seal (tree, changed-path and app manifests; a new anchor), then re-run the kit suites, the kit mutations and one rehearsal on the corrected seal.

## 15. Live after-probe and STOP

**After-probe at 03:17:59Z: `diff before.txt after.txt` is empty** (both sha16 `6c4e6a7728ca7189`). Live containers, GIT_SHA, schema, switches, dispatch, allowlist, DB fingerprint, max ids, pending outbound, all kits and private runtimes (R3–R6-C3), refs, images, volumes and backend TZ are unchanged.

At the end of the review:
- 0 `rvw-` containers and 0 networks remain;
- the worktree tree pin recomputes to `58266ffc…e3bd`;
- 0 files are staged;
- the only new worktree file is this audit.

Even a READY verdict would not authorize build, deployment, W-3 or WILD. This verdict is **CORRECTION REQUIRED**, so commit, push, the final image build, pin finalization, dark deployment, windows and the WILD remain **NOT AUTHORIZED**.

**Next milestone:** PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C4, the corrections in §14, with re-pin and re-seal. After it: …-R6-C4-FINAL-TEMPORAL-BOUNDARY-INDEPENDENT-REVIEW.

**W-3 remains OPEN. The final WILD remains NOT RUN.**

STOP.
