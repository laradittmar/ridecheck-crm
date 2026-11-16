PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-INTEGRATED-INDEPENDENT-REVIEW
# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — FINAL CANDIDATE CONVERGENCE R6 — INTEGRATED INDEPENDENT REVIEW — AUDIT 001

Date: 2026-11-11 (review runs 18:09Z–19:47Z)
Reviewer: fresh independent reviewer (did not implement this candidate).
Source candidate: `/opt/ridecheck-crm-r5-f1-response-validation`, branch `fix/prelaunch-r5-f1-response-validation-boundary`, base = HEAD `f16b1f328f3fbf1050afdcec583d252d528c72bf`.
R6 kit: `/opt/ridecheck-builds/crm-minimum-launch-enablement-kit-R6-20261111T144848Z` (provisional anchor `99c3e161fe394a81cc0b6477b1be413acaa52045604647a63ee970ec02c7d848`).
Review evidence (disposable, reviewer-owned): `/opt/ridecheck-builds/crm-r6-integrated-review-20261111T181250Z/`, plus one rehearsal run directory `PRIVATE-R6-20261111T144848Z/run-r6-independent-review-20261111T193323Z/` (the sealed rehearsal requires its OUTDIR inside the R6 private runtime).

# VERDICT: CORRECTION REQUIRED

The structured boundary does what it set out to do. It removes the R5F1RV-01 prose-reinterpretation class on the real production path, in all four postures, on SQLite and PostgreSQL. State and outstanding questions no longer derive from assistant prose. R5-F1 remains fixed, and R5RV-01…04 are corrected. Regression is NEW 0 / GONE 0, and my independent rehearsal passed 94/0.

Two defects block a controlled-release-candidate commit:

1. **R6IRV-S01 (HIGH, source, introduced by R6).** The new hours-range exemption lets an invented concrete appointment window reach the customer. Examples: "Te esperamos el jueves de 15:00 a 15:30." and "Podemos ir mañana entre 10:00 y 11:00." Both are delivered verbatim on the full production path, in every posture and world; base f16b1f3 refused both.
2. **R6IRV-K01 (MEDIUM, kit/release).** `FINALIZE_PINS.sh` does not refuse an unsealed file added to the provisional kit. It writes `PIN_STATE=FINAL`, evidence `verdict=PIN_FINALIZATION_VERIFIED` and a CHECKSUMS that blesses the foreign file. Only then does its own post-write VERIFY fail (exit 3). After that failure **the live pin gate opens.**

Nothing was staged, committed, pushed, built, finalized, deployed or migrated. No flag or allowlist changed, Meta was not contacted, nothing was sent, and live data was not touched. **W-3 remains OPEN. The final WILD remains NOT RUN.**

---

## 0. Summary

| Area | Result |
|---|---|
| Identity / scope | HEAD = base = f16b1f3; 0 commits; 0 staged. 12 changed paths, as closed out. The sealed tree, the kit pin manifests and the worktree agree. **The closeout §1 hash table is stale for 2 files** (R6IRV-D01). No ignored files |
| Architectural boundary | Holds for provenance (exact type), declarations, structured claims, total fail-closed failure paths and exact `str` output. **Disproved for "a contradiction refuses the whole response"** on the AI-prose text layer, which still drops sentences and rewrites prices (pre-existing, R6IRV-P02) |
| Prose class (R5F1RV-01) | **Fixed.** In my independent 17-case adversarial set (×4 worlds ×4 postures), every ordinary answer is delivered verbatim with no state written. Base altered 13 of the 17 |
| Real contradictions | Single-sentence wrong vehicle, location, price, slot, booking and acceptance are all blocked. **R6-introduced false negatives:** range-form slots (S01, blocking) and lowercase or sentence-initial model names (S02, residual). **Pre-existing:** question-form contradictions are delivered (P01) |
| State / questions | 0 pending fuzzy vehicles, 0 `preferred_time`, 0 "¿Es un …?", 0 revisions, 0 undeclared outstanding questions across 664 candidate cases (SQLite = PG, byte-identical) |
| Regression | Focused SQLite 2527 → 2642: **NEW 0 / GONE 0**. Full SQLite 8346 → 8462: **NEW 0 / GONE 0**. PG, all 29 suites: 713 = 713, **NEW 0 / GONE 0**. SB2B clean: identical |
| Mutation (source) | Reviewer mutants **19/20 killed**. M03 (template provenance by `isinstance`) survived but is masked by a second exact-type gate (R6IRV-T01, LOW) |
| Mutation (kit) | `mutate_r6.py`: **34/34 killed** (independent run) |
| Kit suites on R6 | R3 **269/0**. R5 **242/0**. R6 **112/0** |
| Provisional gate | 12/12 live ENABLE / RESET_BETWEEN / reset_testers refuse, **0 docker calls**, with no lock, ledger or watermark. KILL and WATCH are ungated |
| FINALIZE / VERIFY | 49/51 scratch checks pass. **F18 fails** (K01). **VERIFY's deep pass does not re-check changed-path blobs or the whole tree** (K02) |
| Rehearsal | **Independent full run 94/0**, order H → H2 SKIPPED → W1A → RESET_AB → W1B → RESET_BC → W2 → final KILL |
| Live isolation | Before = after, exactly, except the one rehearsal run directory in the R6 private runtime |

---

## 1. Identity and scope

**Git.** `rev-parse HEAD` and the merge-base are both f16b1f3. `rev-list --count f16b1f3..HEAD` = 0, and `diff --cached` is empty.

**Changed paths (12).** M `backend/app/services/{conversation_engine,response_validator}.py` and M `tests/test_l4_7d_response_validator.py`, `tests/test_l4_7w5_g3_6_proposition_migration.py`. A `backend/app/services/reply_claim_shape.py`, `tests/fixtures/r6_zone_names.txt`, `tests/test_r5_f1_motorcycle_handoff_production_path.py`, `tests/test_r5_f1_template_validation_contract.py`, `tests/test_r6_full_engine_prose_corpus.py`, `tests/test_r6_structured_response_contract.py`. A three docs/Launch `.md`: the two R5-F1 documents and the R6 closeout (written after the seal).

**Reviewed manifest (sha256).**

| Path | sha256 |
|---|---|
| backend/app/services/response_validator.py | `9a7b9e463df5328ea57ee75670ad5a019083478e9facc0453ec5a94e51a77cdf` |
| backend/app/services/conversation_engine.py | `7bcabe6af3fbbebffa6bb2d4c815cc1622e32005bf9015b9710225887ad75c4c` |
| backend/app/services/reply_claim_shape.py | `4f9bfb3f6fa3ed30d2157a2eabf59f31a4a045da30823e68df738ace780d7725` |
| tests/test_l4_7d_response_validator.py | `ff379913cca5effcfc77a9513ebc3e262c69eb7ed58a47cd9dbd59175ed886a5` |
| tests/test_l4_7w5_g3_6_proposition_migration.py | `9b0a3beda0aa1f2475fc86150e0c569c59e6a9384a4957789ec36a672596dbdb` |
| tests/test_r5_f1_motorcycle_handoff_production_path.py | `ed191b470e41a19e910b3028a9ebf16c2c7512b9bdeae53e0595c9932fc87da9` |
| tests/test_r5_f1_template_validation_contract.py | `d4da3808a53923f19cc27ff45f387263eda32cd2e0a60631f8432b2c40fd059a` |
| tests/test_r6_structured_response_contract.py | `17d18b175a9b5cc8c42871547706c4fa251785bfda3bacbfd6419fd330f0ae63` |
| tests/test_r6_full_engine_prose_corpus.py | `c59fe1512ed1c4a3fa7fad50c27d092c7987bab9041c0a6fa2609ddc2e8bc466` |
| tests/fixtures/r6_zone_names.txt | `5994ade38077d769f67fc509c3c46a48a7cfc075d7bdd054c8c6b867d9dd52fd` |
| docs/Launch/…R6_CLOSEOUT_001.md | `0a413638fe8bed04747c42524c59628ad70c118468c7deaf45203d83c5466125` |
| docs/Launch/…R5-F1…CLOSEOUT_001.md / …R5-F1…INDEPENDENT-REVIEW_AUDIT_001.md | `66f33765…847a` / `c24d3fee…570b` |

**R6IRV-D01 (LOW, documentation).** Closeout §1 lists CE `43d98767…` and the contract test `6b7d5faf…`. Those are the pre-`_process_text`-fix values; the closeout's own §6 describes the re-seal. The sealed `cand/`, `pins/changed_paths.manifest`, `pins/candidate_tree.manifest` and the worktree all carry `7bcabe6a…` / `17d18b17…`. Because every machine-checked identity agrees, I treat this as a stale table, not identity drift.

**Seal.** The worktree (`ls-files -co --exclude-standard`, excluding this audit) has 719 files. The sealed `cand/` has 718 and is byte-equal except for the R6 closeout itself, which was written after the seal; that is a permitted docs/Launch `.md` addition. The sealed manifest `38e6d8c3…bffc` reproduces. `cand/` contains 0 `__pycache__`/`.pyc` entries. My own base archive reproduces `7282f596…09b4`.

**Pins, recomputed by me from the worktree.**

| Pin | Value |
|---|---|
| Tree outside docs/Launch (676 files) | `a86be992…5442` |
| Changed paths | `5f54a827…66ba` |
| `backend/app` (137 files) | `5358c28c…6a01` |

All three equal `config/pins.conf`.

**R6 kit.** `sha256sum -c` passes. `sha256(CHECKSUMS.sha256)` = `sha256(pins/PROVISIONAL_CHECKSUMS.sha256)` = the anchor `99c3e161…d848` (117 entries, exact). The file set equals the listing plus the two manifests.

**R3/R4/R5 kits and private runtimes.** All verify. CHECKSUMS prefixes are `dd9686c6` / `d884015e` / `3c228f71`. No file is unlisted or newer than CHECKSUMS. Whole-tree hashes are unchanged before and after (§11). The R5 kit review hash is `fb9604d1…a56ab`, as cited.

**Tests changed.** No test was deleted, skipped or xfailed. The three disclosed assertion changes (closeout §7) were reviewed against the R5-F1-reviewed versions:
- `test_validation_failure_never_breaks_a_turn` now forbids raw text.
- `test_g36_41` puts the vehicle in the customer burst, and `41b` is added.
- `d14`/`d16`/`d17`/`e04` follow the moved tag read and use the plan-appropriate fallback.

Each change is stricter or a faithful relocation; none weakens a test.

**Secrets and identifiers (count-only).** I checked the added and new content against every `.env` value (≥ 8 chars) and every number and email in the R6 private inputs: 0 hits. There are 0 EAA / sk- / PEM / Bearer shapes. There are 0 `549…` numbers in the candidate. The 15 `549…` numbers in the kit are synthetic patterns, none of them a private input. The only email domain is `example.invalid`. **Ignored files:** none (`ls-files -o -i --exclude-standard` is empty).

---

## 2. Architectural boundary (implementation read)

| Property | Ruling |
|---|---|
| One typed envelope | **Holds.** `_validate_outbound_text` → `validate_envelope` → `ResponseEnvelope`. `_send_text_to_wa` passes the primary's envelope to `_record_delivered_questions` |
| Provenance by exact type | **Holds.** `type(value) is ApprovedTemplateText` / `is ComposedText`, and the template tag is read only by `template_tag_of` (exact type). A subclass, a duck-typed `_declaration` or a malformed declaration gets AI trust. Mutants M04/M05 are killed. M03 (`isinstance`) survives, but `template_id` stays `None`, so no exemption and no intent is granted (T01) |
| AI prose cannot gain template/composed trust | **Holds.** Model output is a JSON `str`. Derived values of both tagged classes are plain `str`. `ComposedText` is built only in CE code. The composed path skips the model-prose scrubs (`type(_raw_reply) is ComposedText`), which is correct |
| Structured claims by canonical identity | **Holds.** Vehicle (marca, modelo), zone, int amounts, `HH:MM`, each checked against `CanonicalFacts` |
| Contradiction refuses the whole response | **Holds for declared claims only** (M14 is killed). **Disproved for AI prose.** The text layer still drops a failing sentence and delivers the rest ("La revisión incluye más de 250 puntos. Revisamos tu Fiat Uno 2015." → first sentence only) and rewrites invented amounts to the canonical quote. This is identical on base (P02). The contradicting content is never delivered |
| Internal errors fail closed | **Holds.** The envelope's `except` returns `safe_failure_text()`. CE's `except` returns the exact approved template or the safe line. The raw-text path is closed (M01/M02 killed) |
| Exact `str` output | **Holds** (`_plain` via `str.__str__`; M19 killed) |
| Motorcycle handoff | **Holds.** Accepted under `NO_VEHICLE_CLAIM`, and the words survive untagged too. Rehearsal RR10 delivers the exact constant |
| Template exemptions total and fail closed | **Holds** (R5F1RV-02): `type(...).__name__` only, the skipped set is type-checked as an exact frozenset of known ids, and everything sits inside `try` |
| Only VEHICLE exempted | **Holds** (M11 and M12 killed: skipping all classes, or adding PRICE) |

**Authority order.** For deterministic and declared values the order is: canonical state → declaration → validation → rendered text → defence in depth. **For AI prose, which declares nothing, the "defence-in-depth" text layer is the only check.** Its precision therefore decides correctness on the dominant path (§3).

---

## 3. Prose-reinterpretation class: independent full-production-path probe

`probe/adv_probe.py` is reviewer-authored. Every turn goes through `handle_message` → CE → reply admission → `_send_text_to_wa` → envelope → `plan.compose` → OutboundSafetyGate → Meta recorder; only the model call, the Flow send and Meta are stubbed. The R1/R2/R3 interlock was armed in every case.

- Worlds: `new`, `corolla` (focus, no place), `located` (CABA/Palermo), `both`.
- Postures: direct webhook false/true × Smart Booking false/true.
- Volume: 41 replies × 16 cells, plus scheduling cases, for 664 turns per tree.
- Trees: f16b1f3 plus the harness (red control), and the candidate on SQLite and on PG 16.13. The PG output is **byte-identical** to SQLite (`d646b4e2…`).

**Ordinary prose.** I tested 17 replies, among them uno por uno, uno de los dos autos, Uno nunca sabe, buena idea, punto por punto, a las 10 en punto, de 9:00 a 18:00, según disponibilidad, sujeto a disponibilidad, pilares, norteamericano, Gracias Pilar and unas flores. I also included a conditional "si"/"cuando" + Pilar/Flores, and two service-area sentences.
- **Base:** 13 of the 17 were altered into the vehicle/zone fallback with an INSPECTION_LOCATION outstanding question (REPLY_MODEL).
- **Candidate:** every one is delivered verbatim in `new`, `located` and `both`. In `corolla` the candidate appends only the structural canonical location question, recorded as DETERMINISTIC_TEMPLATE; this is legitimate because the vehicle is known and the place is missing.
- **Candidate state:** across all 664 cases, no pending fuzzy key, no `preferred_time`, no "¿Es un …?", no candidate or revision change, and no REPLY_MODEL outstanding question except for a genuine location question while the place is missing.

**Real contradictions (single sentence).** Wrong vehicle "Perfecto, revisamos tu Fiat Uno 2015.", wrong location "el auto está en Pilar", invented slot "Te ofrezco el jueves a las 11:30.", false booking and false acceptance are all refused into the plan-appropriate fallback in every cell. An invented price ($12.345) becomes the fallback where no quote exists, and is **rewritten** to the canonical $140.000 where one does. The invented amount is never delivered.

**R6-introduced false negatives (base refused, candidate delivers):**

| Case | Candidate delivers | Finding |
|---|---|---|
| "Te esperamos el jueves de 15:00 a 15:30." | verbatim, diag AVAILABILITY | **S01** |
| "Podemos ir mañana entre 10:00 y 11:00." | verbatim | **S01** |
| "Perfecto, revisamos tu gol mañana." (`both`: Corolla focus) | verbatim, diag VEHICLE | S02 |
| "Gol, perfecto, lo revisamos." | verbatim | S02 |

**Pre-existing (identical on base):**

| Case | Behaviour | Finding |
|---|---|---|
| "¿Coordinamos la revisión de tu Fiat Uno 2015?" / "¿Te reservo el jueves a las 11:30?" / "¿Te parece bien $12.345 por la revisión?" | delivered verbatim; questions skip every claim check. Base also armed `pending_fuzzy=Fiat\|\|Uno`; the candidate does not | P01 |
| multi-sentence replies | the contradicting sentence is dropped and the rest is delivered | P02 |
| "Te lo revisamos en Pilar mañana." to a Palermo customer | delivered (no subject or motion token) | P03 (LOW) |

**Mention plus claim in one sentence.** "Revisamos todo uno por uno en tu Volkswagen Gol." and "Uno de nuestros técnicos revisa tu Fiat Uno." are both refused, so the resolver does surface the claim.

**Locative-claim ruling.** Treating "La revisión del auto la hacemos **en Flores** sin problema." as a claim that needs canonical proof is **correct**. It asserts where this inspection happens: in the `located` world it is a contradiction, and elsewhere it is an unsupported presumption. When the customer supplies the place (inbound "El auto está en Pilar"), canonical state takes it and the equivalent "Sí, vamos hasta Pilar sin problema." is delivered verbatim. Refusing into a fallback that asks for the place costs little.

---

## 4. State and question authority

| Requirement | Ruling |
|---|---|
| Vehicle propositions need customer evidence | **Holds.** The hit comes from `_turn_text` (this turn's inbound evidence), and the reply is only a claim-shaped trigger for the same vehicle (M06 killed; probe: 0 fuzzy) |
| Assistant prose cannot arm vehicle state | **Holds** |
| AI time only selects an evaluated, shown slot | **Holds structurally.** `_evaluated_slot_times` reads `last_visible_slots`/`last_offered_slots`, and an invented time is logged, not stored (M15 killed by `i04`). On the full path the M20 block is **unreachable on both trees** in the RC36 fixture: my probe shows even an evaluated `09:00` is not stored on base f16b1f3, because an offer path runs first. This confirms the closeout disclosure |
| Outstanding questions only from structure or a validated declared intent | **Holds** (M07 and M17 killed; probe: "Cuando sepas en qué zona está el auto, me avisás." records nothing) |
| Suppressed primary records nothing | **Holds** (`plan.included(PRIMARY_REPLY)`; M17) |
| Known vehicle / location not re-asked | **Holds** (corolla never "qué vehículo"; located never asked the place) |
| Fallback selection total (4 combinations, malformed → NO_QUESTION) | **Holds** (M13 killed) |
| Delivered text contains the question of every retained intent | **Holds.** Intents are computed on the validated primary's surviving questions, composed intents are withdrawn if no question is delivered, and recording requires the primary to be included |

**Residual text reads.**
- **(a) AI location-question confirmation.** This is confirmation only. It is gated by `not facts.location_known` (M16 killed) and applies only to a surviving *question* of the primary. It records an expectation about a question that was actually delivered, and the location itself still needs customer evidence.
  - Remaining surface (LOW, non-blocking): `_LOCATION_ASK_PROBES` is a substring set ("donde queda", "en que zona"…). A question such as "¿Te paso dónde queda nuestro taller?" while the place is missing would record an INSPECTION_LOCATION expectation.
- **(b) AI-proposed-time selection.** This is selection among canonical evaluated slots only. It cannot create an invented time. The customer's "sí" then requires a fresh ScheduleService check.

Neither read creates state from model prose alone.

---

## 5. Fallback copy ruling

The NO_QUESTION fallback is "Gracias por tu mensaje. Cualquier otra consulta, escribime por acá." It is sent only when the vehicle and location are both canonical and the reply is refused; it is also the validator-failure safe line.

**Ruling:** acceptable launch copy, **pending explicit owner approval**; that approval is not proven here. One non-stylistic observation (non-blocking): "Cualquier *otra* consulta" presupposes that the customer's message was answered, yet the line is sent exactly when the answer was refused. A neutral wording that does not imply an answer was given, together with an operator signal on NO_QUESTION refusals, is worth an owner decision before the WILD.

---

## 6. Source verification (independent; `ridecheck-crm-test:l4.7b2`, `--network none`, read-only trees)

| Run | Base f16b1f3 | Candidate | Comparison (junit identity) |
|---|---|---|---|
| Focused SQLite (67 files; 63 exist on base) | 18 F / 2148 P / 361 S (2527) | 18 F / 2263 P / 361 S (2642) | **NEW 0, GONE 0**, COMMON 18, changed 1 (SB1 sentinel), +115, −0, skip changes 0. Identity-identical to the implementer's runs |
| Full SQLite `../tests` | 62 F + 9 E / 7489 P / 786 S (8346) | 62 F + 9 E / 7605 P / 786 S (8462) | **NEW 0, GONE 0**, COMMON 71, changed 1 (SB1 sentinel), +116, −0, skip changes 0 |
| PG 16.13 (`TimeZone=Etc/UTC`), **all 29** PG suites (the implementer used 16), each on a fresh clone of the head template built per tree; schemas identical | 2 F / 598 P / 113 S (713) | 2 F / 598 P / 113 S (713) | **NEW 0, GONE 0**, COMMON 2, changed 0, skip changes 0 |
| PG SB2B with its clean database | 46 P / 1 F / 1 S | identical | NEW 0 / GONE 0 |
| PG candidate: R6 corpus + contract | — | 58 passed, 1043 subtests | — |
| PG candidate: R5-F1 production-path suite | — | 26/26, outstanding questions 0 | — |

The 2 common PG failures are pre-existing and outside the implementer's selection:
- `test_p2_pg_00_the_schema_is_the_migrated_head` asserts a stale head.
- `test_rv03_pg_13_every_blocking_referent_is_handled`: an older tester-reset helper does not handle `whatsapp_outstanding_questions` / `whatsapp_thread_evidence`. This is pre-existing debt; the R6 kit's own resets were proven isolating in the rehearsal (§9).

**Red control.** The supplied red control is confirmed independently by a different corpus (§3): on base f16b1f3 the prose class alters 13 of 17 ordinary replies and arms a fuzzy vehicle; on the candidate, none. Real contradictions stay blocked, except the S01/S02 gaps.

**Source mutants (reviewer-authored, 20; each one exact replacement bind-mounted over the read-only candidate; six relevant suites, `-x`).** The unmutated control passes 200, with 1310 subtests. **19 of 20 are KILLED:**
- raw text after a validator failure (M01);
- the envelope exception returning the value (M02);
- duck-typed composed provenance (M04);
- a malformed declaration trusted (M05);
- state from assistant prose (M06);
- an undeclared question recorded (M07);
- an invented or unevaluated slot accepted in text (M08/M09) or in a declaration (M10);
- a template skipping all claims or also skipping price (M11/M12);
- the fallback always asking both questions (M13);
- a declared conflict edited instead of refused (M14);
- the AI time unchecked (M15);
- an AI location intent while the place is known (M16);
- a suppressed primary declaring (M17);
- the claim shape disabled (M18);
- non-exact `str` output (M19);
- the conditional clause ignored (M20).

**M03 (template provenance by `isinstance`) SURVIVED** (T01). Only the provenance label changes; the tag is still read by exact type, so the mutant grants no exemption and records no intent (verified by probe).

The implementer's supplied results (26/26 source, 34/34 kit) are consistent with these.

---

## 7. R6 kit corrections

| Item | Disposition | Evidence |
|---|---|---|
| **R5RV-01** | **CORRECTED.** `r6_time.sh` builds `slot_end > (now() AT TIME ZONE '<SCHEDULE_TZ>')`, which is correct regardless of the session TimeZone. It is used at all five sites: `launch_ext.sh` watermark (ENABLE), `ext_counters`, `preflight.sh:92`, `cancel_test_bookings.sh:54` (aliased), and rehearsal RR6/RR8. No active script contains `slot_end > now()`; it appears only in mutants and the labelled red control. `SCHEDULE_TZ` must equal the app's `schedule.BUSINESS_TIMEZONE`, read by a fresh process (ENABLE and preflight refuse otherwise). A malformed zone is normalised to the default and that effective zone is what is compared (INFO) | K68 (PG `Etc/UTC`; four clocks, UTC- and local-date rollover; −1 min / now / +1 h / +2 h / +3 h01; R5 predicate measured missing). Rehearsal RR13 on the real schema: the local now+1 h booking is missed by R5 and counted by the helper, the ENABLE watermark, the breach counter and preflight. None over-counted |
| **R5RV-02** | **CORRECTED.** `r6_manifest_pyc_count` prints exactly one integer, counts `.pyc`, `.pyo` and `__pycache__`, and prints MISSING with rc 1 | My checks: sealed 0; injected 1/1/2; missing → MISSING; `pycache_notes.md` / `x.pyc.txt` → 0. K61 uses it |
| **R5RV-03** | **CORRECTED.** It reads raw bytes before decoding and is defined last (`kit_r6.sh` is sourced after `kit_r5.sh`) | My 10 crafted ledgers: valid → 0. CRLF, CR-only, a single trailing CR, missing final LF, empty line, invalid UTF-8, CR in `LEDGER.head`, truncated and reordered → all refused with the right reason |
| **R5RV-04** | **CORRECTED.** The second read is `docker exec -i <backend> python -` → `get_settings()`, a genuinely new interpreter. 0 "in-process" / "in process" mentions remain in active scripts, docs, config or tests (archives and patches excluded, as disclosed) | `kit_r4.sh r4_effective_state`; STATUS `FRESH-PARSER-PROCESS=` |

---

## 8. Provisional pinning and finalization

**Provisional gate.** I used scratch copies of the kit and private runtime, with only the two path lines rewritten in the copy, and a recording docker shim that always fails.

| Operation | Result |
|---|---|
| ENABLE H, H2, W1A, W1B, W2 | each exit 2, `R6 pins: pins are 'PROVISIONAL'…`, **0 docker calls** |
| RESET_BETWEEN AB, BC (execute and dry-run) | same |
| reset_testers W1A, W1B, W2 (execute) and W1A (inventory) | same |

Twelve of twelve refused, with no lock, ledger or watermark created. KILL is never pin-gated: it proceeded to docker (30 calls into the failing shim). WATCH pre-arms.

- **Process note:** WATCH's dead-man correctly fired a KILL on the scratch config when the watcher was terminated; it inherited the shim. I stopped both by exact PID; KILL traps SIGTERM by design, so it needed SIGKILL. No real docker call was made.

`KIT_MODE` comes from the checksummed config. Rehearsal mode refuses live project and container names, so `rehearsal`+`FINAL` cannot reach live. The private inputs, sourced after `pins.conf`, define no pin keys (checked by key name only). The README and STATUS headline say "PINS PROVISIONAL". **R5 remains the correct live emergency instrument for the f16b1f3 deployment**: R6's `EXPECT_IMAGE=UNFINALIZED` cannot verify it.

**FINALIZE / VERIFY (`fin/fin_test.sh`).** This used a scratch `--shared` clone (new objects and refs stay in the scratch directory), scratch single-parent commits, and a fake docker. The `/opt/ridecheck-crm` refs are unchanged (§11).

- **F0, the good commit and image:** exit 0. Exactly 3 files differ (`config/pins.conf`, the new `pins/FINAL_PIN_EVIDENCE.txt`, `CHECKSUMS.sha256`), and exactly the 4 keys change. VERIFY (deep) passes, and a second FINALIZE is refused.
- **Refused with nothing written (rc 2, tree hash unchanged).**
  - Commits: an extra code path, a test blob differs, an app blob differs, an existing docs/Launch file modified, a non-.md file added under docs/Launch, the wrong parent, a merge, the base commit itself, a nonexistent commit.
  - Images: the tag resolves elsewhere, the label names another commit, GIT_SHA is the base, the image's `/app/app` differs, the image is absent, the schema label is wrong.
  - Kits: the wrong anchor, an edited runbook, a re-sealed fake provisional kit presented with the real anchor, an edited non-pin key in pins.conf.
- **VERIFY after a genuine finalization: NOT VERIFIED** for:
  - an edited script after re-checksumming;
  - an added file after re-checksumming;
  - an edited non-pin key;
  - the image pin swapped consistently (deep pass);
  - an altered runbook;
  - a re-sealed fake provisional kit (caught only by the external anchor; the same fake verifies against its *own* anchor, which proves the anchor is what catches it).
- **F18, an unsealed file added to the provisional kit: NOT refused.** See K01 in §12.
- **V7:** a hand-finalized kit pinned to a commit whose **test** blob differs (same path set, identical app) is **VERIFIED** by the deep pass. See K02 in §12.

Image provenance is verified by tag, labels, GIT_SHA and `/app/app`. Content outside `/app/app` (for example alembic or requirements) relies on the labels; the candidate does not change those (INFO).

---

## 9. Integrated kit tests and rehearsal (independent)

| Job | Result |
|---|---|
| R3 `tests/test_kit.sh` on R6 | **269 passed, 0 failed** |
| R5 `tests/test_kit_r5.sh` on R6 | **242 passed, 0 failed** |
| R6 `tests/test_kit_r6.sh` (K67–K75) | **112 passed, 0 failed** |
| Kit mutations `tests/mutate_r6.py` (pin gate, clock, ledger, pyc, wording, FINALIZE, VERIFY) | **34/34 KILLED** |
| **Rehearsal** `tests/rehearse_r6.sh` → `PRIVATE-R6/run-r6-independent-review-20261111T193323Z` (log `b91023625cb42f25`) | **94 passed, 0 failed** |

**Rehearsal setup.** It used the sealed candidate's `backend/app` mounted read-only on the f16b1f3 image. RR0/RR2 prove the content manifest equals the pin. The overlays differ only in their `image:` line. It ran on its own `--internal` network, with PG restored from the private snapshot under `TimeZone=Etc/UTC`, a real uvicorn and its daemons.

**What the rehearsal verified:**
- **Order and ledger:** H → H2 SKIPPED → W1A → RESET_AB → W1B → RESET_BC → W2 → final KILL. Exactly one OPENED and one CLOSED per executed window, each closed with `criteria_not_met=0` before ACCEPT, and the ledger chain verifies (RR11).
- **Reset isolation:**
  - RESET_AB and RESET_BC are refused without an accepted expectation, refuse out-of-scope rows, and commit once.
  - Window evidence, ledger and head are byte-identical afterwards.
  - Non-tester rows are identical, both testers are fresh, and the audit rows are retained.
  - W1B before RESET_AB and W2 before RESET_BC are refused by the ledger.
- **W2:**
  - Contact Flow only; needs_human false → true; no offer, booking, notice or job; nothing pending.
  - The reply **is** the truthful handoff constant, and **no outstanding question** was opened.
  - W2-6 MET; ACCEPT W2.
- **Final posture and live:** all eight switches literally false, and `booking_dispatch_allowed()` False (env and fresh-process parse). No bytecode was written. Live containers and the live fingerprint are unchanged (RR12). RR13 is as in §7.
- **Cleanup:** 0 `rcr6-*` containers or networks remain.

---

## 10. Frozen sentinel

`test_sb1_booking_representation::UnchangedContracts::test_sb1_20_frozen_files_unchanged` is red on f16b1f3 with 3 drifted files. On the candidate it lists 4: R6's intentional edit adds `response_validator.py`, which is on the frozen list at blob `7085211…` (= base). It is the same failing identity: NEW 0 in both SQLite comparisons, and no other frozen file was changed by R6. R6 did not conceal a regression behind it: the drift *message* exposes the new entry, and the NEW 0 / GONE 0 comparisons plus the PG run show no other change.

While red, the sentinel is **ineffective**.

**Ruling:** separate hygiene debt, **not a blocker** for the RC commit. Repair it, by re-recording the four blobs with authorisation notes, in its own milestone before the WILD, so that it gates again.

---

## 11. Live isolation (read-only; before 18:09:12Z, after 19:47:10Z)

The probe (`live/probe.sh`, sha16 `c1bf695a00afa6e5`) uses `docker inspect`, fresh-process `docker exec` reads, and `psql` with `default_transaction_read_only=on`. It printed no customer value.

| Item | Before = after |
|---|---|
| backend | `04e14e5770f9`, image `sha256:1b8ed611…8fc14`, restarts 0, config hash `7de743f7c57326d5`, GIT_SHA f16b1f3 |
| postgres / n8n | `5246fd86d829` / `6ea80570b67b`, restarts 0. Containers 3/3, networks 5, volumes 48, image set unchanged |
| switches | all eight literally `false`; `booking_dispatch_allowed=False`; allowlist sha16 `ad98b5c31a4e8ed8` |
| schema | head `20261104_vehicle_identity`; columns md5 `15e93aab…`, constraints md5 `4cbd6165…` |
| database | fingerprint `fbae2abdc3371f9146f0ac51a2ffa427` |
| max ids | msg 6268, offers 5, notices 2, jobs 0, security 735, dedup 2529, threads 2077, leads 171, thread_revisions 9, booking_commands 10, oq 2, ai_events 244, evidence 22, candidates 147, states 2078, clar 0, attempts 7, attention 0, revisions 82 |
| pending work | pending outbound 0; notices FAILED:1, SENT:1; recovery jobs none |
| compose, `.env`, beta, probe-off, pin-candidate; 9 + 9 Flow files | hashes unchanged |
| R3/R4/R5/R6 kits | verify OK; whole-tree hashes unchanged |
| R3/R4/R5 private runtimes | verify OK; whole-tree hashes unchanged |
| all `/opt/ridecheck-crm*` worktrees | HEAD, branch, diff hash, staged and untracked counts unchanged |
| refs and worktree list | `be093bd6…` / `7d0e6128…` |

**Single difference:** the R6 private runtime tree, 884 → 1315 files. All new files are under `run-r6-independent-review-20261111T193323Z/` (the rehearsal, which must write there); the `PRIVATE_MANIFEST` still verifies. All reviewer containers and networks (`r6rv-*`, `rcr6-*`) are gone.

**Resource discipline.** Heavy jobs ran one at a time through a gated queue, each started at ≥ 1536 MiB MemAvailable (lowest start 1571 MiB). The lowest reading during a job was 609 MiB; the 500 MiB watchdog never fired (0 ABORT).
- **Deviation:** the light scratch negatives and FINALIZE tests (shell plus fake docker, no containers) ran while heavy SQLite jobs were running.

---

## 12. Findings (by severity)

| ID | Severity | Class | Finding |
|---|---|---|---|
| **R6IRV-S01** | **HIGH — blocking (product/code, R6-introduced)** | availability claim shape | `_HOURS_RANGE_RE` strips *any* `H:MM (a\|y\|hasta\|-) H:MM` range before the slot check, regardless of a day anchor or an offer/visit verb. An invented concrete window ("Te esperamos el jueves de 15:00 a 15:30.", "Podemos ir mañana entre 10:00 y 11:00.") is delivered verbatim on the full path in every posture and world, SQLite and PG; base refused it. Correction: treat a range as business-hours information only without a specific-day anchor (weekday name, hoy/mañana, a date) and without an offer/visit construction, or only with a weekday range or hours lexicon. Add full-path tests for both forms |
| **R6IRV-K01** | **MEDIUM — blocking (kit/release)** | FINALIZE | Step 1 checks listed CHECKSUMS entries but not the file set, so an unsealed added file passes. FINALIZE then writes pins, evidence `verdict=PIN_FINALIZATION_VERIFIED` (before any verification) and a CHECKSUMS that covers the foreign file. Its post-write VERIFY fails (exit 3, "do NOT use this kit"), yet the kit is left `PIN_STATE=FINAL`, self-consistent, and **the live pin gate opens** (proven). Correction: (a) a file-set check (found = listed ∪ manifests) in step 1; (b) write evidence and CHECKSUMS only after VERIFY passes, or restore the provisional pins.conf and remove the evidence on any post-write failure; (c) tests for both |
| R6IRV-K02 | LOW (kit; fold into K01's correction) | VERIFY | The deep pass re-checks parent, path set, docs rule, tag, labels and `/app/app`, but not changed-path blobs or the whole tree outside docs/Launch, despite "exactly as FINALIZE_PINS.sh did". A hand-finalized commit with a different test blob verifies. Shipped code is still covered by the image manifest. Add the blob and tree checks |
| R6IRV-S02 | MEDIUM — owner decision / disclosed residual (R6-introduced) | vehicle claim shape | Claim rule (c) requires a capitalised, non-sentence-initial model name. "revisamos tu gol" (lowercase) and "Gol, perfecto, …" (sentence-initial) are mentions, so a wrong vehicle is delivered where base refused it. This is the price of not using a word list ("tu idea", "el punto"). Owner to accept explicitly; pin the behaviour with a test |
| R6IRV-P01 | HIGH — pre-existing debt, **owner decision before WILD** | validator design | `_is_question` sentences skip every claim check, so an invented price ("¿Te parece bien $12.345…?", while the canonical quote is $140.000), an invented slot or a wrong vehicle in question form is delivered. Identical on f16b1f3; R6 removed the fuzzy-arming side effect. Recommend extending at least PRICE and AVAILABILITY to questions |
| R6IRV-P02 | MEDIUM — pre-existing, owner decision | validator design | For AI prose a contradiction drops its sentence and the rest is delivered; prices are rewritten. "Refuses the whole response" holds only for declared claims |
| R6IRV-P03 | LOW — pre-existing | location shape | A locative claim without a subject or motion token ("Te lo revisamos en Pilar mañana.") is not checked |
| R6IRV-T01 | LOW — test coverage | provenance | Mutant M03 (`isinstance`) survives. It is masked by the exact-type tag read, so there is no runtime effect. Add an assertion that a subclass's envelope provenance is `AI_COMPOSED` |
| R6IRV-Q01 | LOW — residual | outstanding question | The AI location-question probe is a substring set over the primary's questions (§4a). Acceptable for launch |
| R6IRV-D01 | LOW — documentation | closeout | The §1 hash table is stale for CE and the contract test (§1) |
| R6IRV-C01 | INFO — owner decision | copy | NO_QUESTION fallback wording (§5) |
| R6IRV-H01 | INFO — hygiene debt | sentinel | SB1 frozen sentinel red and ineffective (§10). Two pre-existing PG failures (§6) |

---

## 13. Disposition of predecessor findings

| ID | Disposition |
|---|---|
| R5-F1 (motorcycle handoff replaced) | **Remains FIXED.** Production-path suite 26/26 on SQLite and PG, 0 outstanding questions; rehearsal RR10 delivers the exact constant, W2-6 MET |
| R5F1RV-01 (prose reinterpretation, HIGH) | **CORRECTED for the reported class** (§3). The new false negatives S01/S02 come from the correction itself; S01 blocks |
| R5F1RV-02 (exemptions not total) | **CORRECTED** (`_template_exemptions` total, `__name__` only, type-checked skip set, inside `try`) |
| R5F1RV-03 (lying `str` `__ne__`) | **CORRECTED** (`_exact_text_equal`: exact types and `str.__eq__`) |
| R5F1RV-04 (tagged output) | **CORRECTED** (`_plain`; M19 killed) |
| R5F1RV-05 (closeout arithmetic) | Closed (historical) |
| R5F1RV-06 (Honda CB500) | Unchanged and accepted. Not a blocker; motorcycles are handed off first |
| R5F1RV-07 (SB1 sentinel) | Carried: 3 → 4 entries, same identity, hygiene debt (§10) |
| R5F1RV-08 (language-level immutability) | Accepted; outside the threat model |
| R5RV-01 / 02 / 03 / 04 | **All CORRECTED** (§7) |

---

## 14. W-3 and WILD status

**W-3 remains OPEN. The final WILD remains NOT RUN.** No handset test, window or live reset was performed. The R6 kit is **PROVISIONAL**. **R5 remains the live emergency instrument** for f16b1f3.

## 15. Next authorized milestone

**PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C1 (narrow correction), then a fresh integrated independent review.**

1. **Source (S01):** bound the hours-range exemption so that a day-anchored or offer/visit range is a slot claim, as on base, while "Trabajamos de lunes a viernes de 9:00 a 18:00" stays information. Add full-path corpus cases for both forms in all four postures, SQLite and PG. Optionally add T01's provenance assertion.
2. **Kit (K01, K02):** add the FINALIZE file-set preflight; write the evidence verdict only after VERIFY passes, or roll back on failure; add blob and tree re-checks to VERIFY's deep pass; add K74 cases for an added unsealed file and a same-path-set wrong-blob commit.
3. **Re-pin and re-seal** the kit on the corrected candidate. This gives new tree, app and changed-path manifests and a **new provisional anchor**. Re-run the focused, full and PG comparisons, the kit suites and mutations, and one rehearsal.
4. **Docs:** a corrected closeout manifest (D01).
5. **Owner decisions** to record alongside, none of which blocks the C1 engineering correction:
   - the NO_QUESTION copy (C01);
   - acceptance of the S02 residual;
   - P01 (question-form claims), recommended before the WILD;
   - P02;
   - the timing of the sentinel repair.

Until then, commit, push, image build, pin finalization, dark deployment, windows and the WILD remain **NOT AUTHORIZED**.

## 16. Reviewer evidence (sha256 prefix; under `/opt/ridecheck-builds/crm-r6-integrated-review-20261111T181250Z/`)

| Item | sha16 |
|---|---|
| `junit/focused_base.xml`, `focused_cand.xml` | `57ae6d51c81e613b`, `53fb3c8a1c9f33c2` |
| `junit/full_base.xml`, `full_cand.xml` | `3d4c62bfa95b13ac`, `d49a650f323835ae` |
| `junit/pg_base/`, `pg_cand/`, `pg2/`; `logs/050_pgstage.log` | `ded3eb5ae4b2fa79` (log) |
| `probe/adv_probe.py`; `adv_base_red.jsonl`; `adv_cand.jsonl` = `adv_cand_pg.jsonl` | `77db91efa7552c3a`; `f5d34f412f0d6bd1`; `d646b4e290a51501` |
| `probe/ai_time_probe.py`; `logs/035_*`, `036_*` | AI-time path unreachable on both trees |
| `mut/make_mutants.py`; `logs/045_mutants.log`; `mut/m03_probe.py` | `21a12e950e397f34`; `d7b5f92763d93ba1` |
| `fin/fin_test.sh`; `fin/fin_results.txt`; `fin/f18.out`; `fin/gate_probe.sh` | `9f66100ae7038f34`; `042d60b4cb8f08b3`; `4c928b14b1b602dc` |
| `neg/` (provisional-gate negatives, recording shim) | `neg/docker_calls.log` (all shim) |
| `ledger/` (R5RV-02/03 crafted inputs) | — |
| `logs/kit_r3.txt`, `kit_r5.txt`, `kit_r6.txt`, `kit_mut.txt` | `509c193d65bd47d5`, `9df0f29a7fdeb6cc`, `2109a9058d8b501c`, `39cd1a4668fb2b7f` |
| `PRIVATE-R6…/run-r6-independent-review-20261111T193323Z/rehearsal.log` | `b91023625cb42f25` |
| `live/probe.sh`, `before.txt`, `after.txt`; `memory.log` | `c1bf695a00afa6e5`, `55611bee526a6f48`, `55bfb27eac88ab24`; `fd8b943e0b9b4d2c` |

**VERDICT: CORRECTION REQUIRED.** STOP. This audit is not staged or committed.
