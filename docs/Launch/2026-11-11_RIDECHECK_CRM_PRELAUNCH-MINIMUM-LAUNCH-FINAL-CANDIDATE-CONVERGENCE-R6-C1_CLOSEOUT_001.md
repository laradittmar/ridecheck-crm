PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C1
# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — FINAL CANDIDATE CONVERGENCE R6-C1 — CLOSEOUT 001

Date: 2026-11-11 (work 2026-11-11 20:30Z – 2026-11-12 00:30Z)
Source worktree: `/opt/ridecheck-crm-r5-f1-response-validation`, branch `fix/prelaunch-r5-f1-response-validation-boundary`, base = HEAD `f16b1f328f3fbf1050afdcec583d252d528c72bf` (0 commits, 0 staged).
Authoritative review: `docs/Launch/2026-11-11_…-R6-INTEGRATED-INDEPENDENT-REVIEW_AUDIT_001.md` (CORRECTION REQUIRED), read completely; not rewritten.
R6-C1 kit: `/opt/ridecheck-builds/crm-minimum-launch-enablement-kit-R6-C1-20261111T204950Z`
R6-C1 private runtime: `/opt/ridecheck-builds/crm-minimum-launch-enablement-PRIVATE-R6-C1-20261111T204950Z`
R6-C1 evidence: `/opt/ridecheck-builds/crm-final-convergence-r6-c1-20261111T203547Z` (sealed candidate `cand/`, base archive `base/`)

**New provisional anchor** (sha256 of `pins/PROVISIONAL_CHECKSUMS.sha256` = of `CHECKSUMS.sha256`, 121 entries):

> **`641dac2ec9473db19b9f3bea2e80149d93296c545f312441b476444d0a596184`**

# VERDICT: READY FOR FOCUSED INTEGRATED INDEPENDENT REVIEW

Every condition for this verdict holds:
- **S01, S02 and P01 are fixed on the full production path.** All four postures, six canonical-state worlds, SQLite and PostgreSQL.
- **Ordinary prose remains safe.** 0 of 48 ordinary replies altered; base f16b1f3 altered 48 of 48.
- **R5-F1 remains fixed.** 26/26 on PG; W2-6 MET in the rehearsal.
- **K01 and K02 are fixed.** A failed finalization always leaves the exact provisional, unusable posture, and the pin gate is proven closed for ENABLE, RESET_BETWEEN and tester reset.
- **The SB1 sentinel is green and effective.**
- **Regression is NEW 0.** The only GONE identity is that sentinel, red on base and green now by owner ruling 7; GONE is 0 for every other identity.
- **The complete R6-C1 rehearsal passes 94/0.**
- **Live is byte-identical before and after.**

The disclosures in §10 do not change any machine check. They are: an inherited umask behaviour in refusal evidence; an inherited R5-suite flake; PG staging coverage; and a late before-fingerprint.

Nothing was staged, committed, pushed, built, finalized, deployed or migrated. No flag, allowlist or live data changed; Meta was not contacted; nothing was sent. No final pin was applied to any kit. The R3, R4, R5 and R6 kits and their private runtimes were not modified. **W-3 remains OPEN. The final WILD remains NOT RUN.**

---

## 0. Summary

| Area | Result |
|---|---|
| Identity | HEAD = base = f16b1f3, 0 commits, 0 staged. 13 changed code/test paths. Relative to sealed R6, three files changed and two test files were added; no unrelated file changed. The worktree equals the sealed candidate (722 files) |
| S01 concrete windows | **Fixed.** A day anchor, or a transactional frame outside general hours, makes a range a claim; both endpoints must be evaluated slots. Red control: R6 delivers 16/16 → C1 0/16 |
| S02 lowercase / initial models | **Fixed.** Possessive-object and dislocated-topic frames, no word list. R6 16/16 → C1 0/16; the ordinary-prose set is unchanged |
| P01 question propositions | **Fixed.** Price, time/window, vehicle and place proposals in questions are checked; clarification questions survive. Base and R6 30/32 → C1 0/32 |
| T01 | **Fixed.** Direct subclass assertion; the audit's M03 mutant is killed |
| Sentinel H01 | **Repaired.** 4 blobs re-recorded with authorizations. Green, and red on drift in any of them |
| D01 | **Fixed** (two R6 closeout rows) |
| Regression | Focused SQLite **NEW 0** (GONE 1 = sentinel). Full SQLite **NEW 0** (GONE 1 = sentinel). PG ×2 runs **NEW 0 / GONE 0** |
| Source mutations | **27/27 killed** (S01 ×7, S02 ×6, P01 ×9, provenance ×2, state ×3) |
| Kit K01/K02 | **Fixed.** File-set preflight; staged → verified → published (pins.conf last) → re-verified; exact restore on any failure. Deep VERIFY checks blobs, tree, app, labels and all evidence fields |
| Kit suites | R6-C1 **84/0**; R3 **270/0**; R5 **242/0**; R6 **112/0** |
| Kit mutations | R6-C1 **29/29**; R6's `mutate_r6.py` 29/34 (5 masked by the new layers, each killed as Q25–Q29) |
| Kit red control | R6 FINALIZE leaves FINAL + VERIFIED and the gate **opens** after failure; R6 VERIFY passes a wrong test blob. R6-C1 does neither |
| Rehearsal | **94/0**, H → H2 SKIPPED → W1A → RESET_AB → W1B → RESET_BC → W2 → final KILL |
| Live | before = after (byte-identical probe); fingerprint `fbae2abd…a427` |

---

## 1. Owner rulings (recorded exactly as given)

1. **R6IRV-C01 — APPROVED.** Approved fallback copy: "Gracias por tu mensaje. Cualquier otra consulta, escribime por acá." (The text is unchanged; `C01_ApprovedFallbackCopy` pins it.)
2. **R6IRV-S02 — NOT ACCEPTED.** A wrong vehicle must not be delivered merely because the model name is lowercase or sentence-initial. Fixed with transactional/vehicle frames, not a catalogue-word matcher (§3.2).
3. **R6IRV-P01 — NOT ACCEPTED FOR THE WILD.** Question punctuation must not bypass validation of transactional propositions (price, appointment time or range, vehicle and location in a booking/inspection proposition). Fixed (§3.3).
4. **R6IRV-P02 — ACCEPTED LAUNCH DEBT.** AI prose may still drop a contradicting sentence or replace an invented asserted price with the canonical quote; the false content is never delivered, no state is derived from it, declared deterministic responses still refuse the whole response. Pinned by tests (§3.5); documented for a later response-policy cleanup.
5. **R6IRV-P03 — ACCEPTED LOW LAUNCH DEBT.** The narrow subjectless location form remains deferred; pinned and documented (§3.5).
6. **R6IRV-Q01 — ACCEPTED FOR LAUNCH.** The residual AI location-question confirmation remains (canonical missing-location state is authoritative; prose alone creates no state).
7. **R6IRV-H01 — REPAIR NOW.** The SB1 frozen-file sentinel is repaired: the four authorized blobs are re-recorded with authorization comments naming their milestones; the sentinel is green and effective (§4).

## 2. Scope, identity and manifest

**Git.** HEAD = merge-base = `f16b1f3`; `rev-list --count f16b1f3..HEAD` = 0; nothing staged.

**Complete changed-path manifest vs f16b1f3** (outside `docs/Launch/`: 13 paths; reconciled from the sealed `cand/` against `git archive f16b1f3`, identical to `pins/changed_paths.manifest`, sha256 `2b927a5d…40ca`):

| St | Path | sha256 | Since R6 |
|---|---|---|---|
| M | `backend/app/services/conversation_engine.py` | `7bcabe6af3fbbebffa6bb2d4c815cc1622e32005bf9015b9710225887ad75c4c` | unchanged |
| A | `backend/app/services/reply_claim_shape.py` | `b2f3c1080794c440b352e02876859616a4d2f57312caf7e9e34a7e5a67c58eec` | **changed (S02)** |
| M | `backend/app/services/response_validator.py` | `634fc0f7ad8bba9c8c659e37e0af33be95bcfb72e9bc250dc754a2a79429207c` (git blob `5e5690ee…230b0a`) | **changed (S01, P01)** |
| A | `tests/fixtures/r6_zone_names.txt` | `5994ade38077d769f67fc509c3c46a48a7cfc075d7bdd054c8c6b867d9dd52fd` | unchanged |
| M | `tests/test_l4_7d_response_validator.py` | `ff379913cca5effcfc77a9513ebc3e262c69eb7ed58a47cd9dbd59175ed886a5` | unchanged |
| M | `tests/test_l4_7w5_g3_6_proposition_migration.py` | `9b0a3beda0aa1f2475fc86150e0c569c59e6a9384a4957789ec36a672596dbdb` | unchanged |
| A | `tests/test_r5_f1_motorcycle_handoff_production_path.py` | `ed191b470e41a19e910b3028a9ebf16c2c7512b9bdeae53e0595c9932fc87da9` | unchanged |
| A | `tests/test_r5_f1_template_validation_contract.py` | `d4da3808a53923f19cc27ff45f387263eda32cd2e0a60631f8432b2c40fd059a` | unchanged |
| A | `tests/test_r6_c1_claim_frames.py` | `8bddb52b12684fb8ebf79155b83c8c7d0692ad39d659b77ad89cf948698b13d8` | **new** |
| A | `tests/test_r6_c1_claim_frames_production_path.py` | `dfee9d7302e95299325719f6b35e8e3c09d8ac3b7c908d6fd466a6291e94b44e` | **new** |
| A | `tests/test_r6_full_engine_prose_corpus.py` | `c59fe1512ed1c4a3fa7fad50c27d092c7987bab9041c0a6fa2609ddc2e8bc466` | unchanged |
| A | `tests/test_r6_structured_response_contract.py` | `17d18b175a9b5cc8c42871547706c4fa251785bfda3bacbfd6419fd330f0ae63` | unchanged |
| M | `tests/test_sb1_booking_representation.py` | `e3fe4041745ca1c24d54184375585741eab10eda1afc3768ce208361271358c0` | **changed (H01)** |

`docs/Launch/` (outside the pinned content; only new `.md` files): the two R5-F1 documents (unchanged), the R6 integrated review audit (unchanged, not rewritten), the R6 closeout (now `603e37abf10783c7eea1b146a0d61302caa472304170fa06825f581f091d7141`, §6), and this closeout (written after the seal).

Diff size vs f16b1f3: validator +912/−36; `reply_claim_shape.py` 180 lines; the two new test files 441 + 269 lines. Relative to the sealed R6 candidate exactly three code/test files changed (validator, `reply_claim_shape.py`, the SB1 sentinel test) and two were added; **no other production or test file changed** (`diff -rq` of the two sealed trees).

**Sealed candidate.** `cand/` = `git ls-files -co --exclude-standard`, 722 files, manifest `9fbad7b010bc8ba9131f047d8a920e7fb90699dd365063b816e89eb2dd0767e6`; 0 ignored files, 0 `__pycache__`/`.pyc`. Base archive manifest `7282f596…09b4` (= both earlier reviews).

**Pins (recomputed from `cand/`).** Tree outside docs/Launch, 678 files: `3560ea14a91fef9d4cda5123e4df229d082bdec330d03db634805f6cb9e8ab38`. Changed paths: `2b927a5d21af308fe9ba8396922169d79ac21fcccb45c6b046aaf845b90d40ca`. `backend/app`, 137 files: `8aaa8d2805757649877ceda0f61df439fb066318f441abe772111895b753f07d`.

**Secret / identity scan** (count only, 4,045 added lines): 0 hits for any `.env` value ≥ 8 characters or any private tester/operator input; 0 credential shapes; 0 `549…` numbers; the only email domain is `example.invalid`.

### 2.1 Existing test assertions modified, and why

| File | Change | Why |
|---|---|---|
| `tests/test_sb1_booking_representation.py` | `_FROZEN_AT_PARENT`: four blobs re-recorded (`claim_projection.py` → `a420ef32…`, `response_validator.py` → `5e5690ee…`, `flow_data_exchange.py` → `4a2310c5…`, `routes/whatsapp.py` → `4e979d37…`), each with an authorization comment naming its milestone(s). The assertion code is unchanged | Owner ruling 7 (R6IRV-H01): repair the sentinel. §4 |
| `tests/test_l4_7d_response_validator.py`, `tests/test_l4_7w5_g3_6_proposition_migration.py`, `tests/test_r5_f1_template_validation_contract.py` | none in R6-C1 (the R6 changes, reviewed in the R6 audit §1, are carried unchanged) | — |

No test was deleted, skipped or xfailed. Every R6 assertion runs unchanged against the corrected validator (the R6 contract and corpus suites, the R5-F1 suites, `test_l4_7d`).

## 3. Source correction

All in `response_validator.py` and `reply_claim_shape.py`. CE, the envelope, the provenance rules, the fallback contracts, the state isolation and the R5-F1 template path are unchanged.

### 3.1 R6IRV-S01 — concrete appointment windows

The blanket hours-range exemption is replaced by `_range_is_concrete(sentence)`, a structural rule over role lexicons (no sentence list):
1. a **day anchor** — a date (`14/11`, `20 de noviembre`, `el día 14`), `hoy`, `mañana` (not `la/las/por la/de la mañana`), `pasado mañana`, a **definite** weekday (`el/este/próximo jueves`), or a bare single weekday in a sentence with no general-hours frame → **concrete**;
2. otherwise a **general-hours frame** — a weekday span (`de lunes a viernes`), a habitual weekday (`los sábados`), `todos los días`, `horario de atención`, `nuestro horario`, `atendemos`, `trabajamos`, `abrimos`, `hábiles` → **information**;
3. otherwise a **transactional action** — offer, booking, coordination, visit, inspection or arrival (`te ofrezco`, `reservo`, `agendamos`, `coordinamos`, `turno`, `visita`, `revisión`, `inspección`, `te esperamos`, `vamos`, `pasamos`, `llegamos`, `ir`…) → **concrete**;
4. otherwise (a bare range: "De 9:00 a 18:00 atendemos consultas") → information.

A concrete window is an AVAILABILITY claim: supported only when the evaluation exists AND **both endpoints are evaluated ScheduleService slots** (an evaluation with no slots supports no window); a confirmed booking may restate its window. Unsupported → the existing plan-appropriate fallback.

### 3.2 R6IRV-S02 — vehicle frames (no catalogue-word matcher)

`classify_vehicle_mention` gains two construction rules; they name no model:
- **(d) possessive object of an action:** the model follows a possessive (`tu/su/mi/nuestro…`) and an inspection/booking action precedes it **in the same clause** — "Perfecto, revisamos tu gol mañana", "coordinamos la revisión de tu gol", "Te reservo el turno para tu gol";
- **(e) dislocated topic:** the model opens the sentence, is set off by a comma, and a later clause resumes it with an object clitic immediately followed by an action — "Gol, perfecto, lo revisamos."

The action lexicon is shared with the validator (`is_transactional_token`). Non-claims, asserted in every world: "Uno nunca sabe.", "uno por uno", "uno de los dos autos", "punto por punto", "buena idea", "Me gusta tu idea, coordinamos la revisión…", "Revisamos el auto, tu idea nos parece buena.", "Uno, dos y tres: revisamos todo.", "Perfecto, uno por uno, lo revisamos todo.", the R5-F1 handoff. The same frames with the canonical VW Gol are delivered (positive control: a claim, checked, not a blacklist).

### 3.3 R6IRV-P01 — question-form propositions

The blanket `if _is_question(sentence): keep` is removed. Every sentence runs the claim checks with a `question` flag:
- **PRICE:** a concrete amount proposed in a question is checked like a statement; a mismatch is **refused** (never rewritten into a proposal of the canonical amount).
- **AVAILABILITY:** in a question only concrete times and concrete windows are propositions ("¿Te reservo el jueves a las 11:30?", "¿Podemos ir mañana entre 10:00 y 11:00?"); the lexicon alone ("¿Hay disponibilidad el jueves?") asks.
- **VEHICLE:** a claim-shaped vehicle inside a question that proposes an inspection/booking **through an action verb** ("¿Coordinamos la revisión de tu Fiat Uno 2015?") is checked; an identity question ("¿Es un Peugeot 2008?", "¿La revisión es para un Peugeot 2008?" — nouns do not propose) is information-seeking and recorded as a diagnostic.
- **LOCATION:** a place inside a question that proposes going there or an inspection action ("¿Vamos hasta Pilar mañana?", "¿Coordinamos la revisión en Pilar?") is checked; "¿El auto está en Pilar?" asks.
- **BOOKING / ACCEPTANCE** are completed-state claims; a question cannot assert them ("¿Ya quedó confirmado el turno?").

### 3.4 R6IRV-T01 — provenance of subclasses

`T01_SubclassProvenance` asserts directly that a subclass of `ApprovedTemplateText` or of `ComposedText` (with a fully formed declaration) gets `AI_COMPOSED` provenance, `template_id None`, `declared False`, no declared question intent, and no template exemption (a subclass carrying the motorcycle tag over "revisamos tu Fiat Uno 2015" is refused). The audit's surviving mutant M03 (template provenance by `isinstance`) is now KILLED (C23).

### 3.5 Preserved contract; accepted debt pinned

Preserved and re-asserted: exact trusted types; structured declarations (a declared conflict refuses the whole value); safe failure output; exact `str` outputs; no state from assistant prose; no outstanding question from words alone; the R5-F1 truthful handoff; every R6 ordinary-prose protection (the R6 contract and full-engine corpus suites pass unchanged).
- **P02 (accepted):** `test_p02_01..04` pin that AI prose drops only the contradicting sentence, that an invented ASSERTED amount is rewritten to the canonical quote and never delivered, that no state comes from the dropped content, and that a declared deterministic response still refuses the whole value. For a later response-policy cleanup.
- **P03 (accepted, low):** `test_p03_01` pins "Te lo revisamos en Pilar mañana." to a Palermo customer as delivered (no inspection noun, no presence/motion token).
- **Q01 (accepted):** unchanged.
- **C01 (approved):** `test_c01_the_owner_approved_no_question_copy` pins the copy.

### 3.6 Known residuals of the new frames (disclosed)

- A sentence that pairs a definite weekday with general hours ("El sábado atendemos de 9:00 a 13:00.") is read as concrete and refused unless both endpoints are evaluated slots (the prompt classes weekday + range as concrete). The canonical FAQ hours answer is unaffected: it is written "de 9 a 18 hs" (no `H:MM`), which carries no time token at all.
- Classification is sentence-level: one day anchor anywhere in the sentence makes every range in it concrete (strict direction).
- A concrete window is checked by its two endpoints against evaluated slot times; the day of the window is not compared with the evaluated day (as for single times since L4.7D).
- With a focus vehicle and no zone, an invented price in a question is removed EARLIER by `_scrub_invented_price` (BUG-2), which sends its own location request; the validator never sees it (as for statements in the R6 corpus, `UPSTREAM_GUARD`).

## 4. SB1 frozen sentinel (R6IRV-H01, repaired)

`test_sb1_20_frozen_files_unchanged` was red on f16b1f3 (3 drifts) and on R6 (4). The four authorized blobs are re-recorded; each entry carries an authorization comment:

| File | New blob | Authorized by |
|---|---|---|
| `services/claim_projection.py` | `a420ef32fc3d0f401e671ca8392401bfe000e507` (= f16b1f3) | W1F-W-01 R2 outstanding-question contract (`e0e83c3`, 2026-10-28) |
| `services/response_validator.py` | `5e5690eecc40954d98d08e360160b75c16230b0a` (base `7085211b…`) | FINAL-CANDIDATE-CONVERGENCE-R6 and R6-C1 |
| `routes/flow_data_exchange.py` | `4a2310c55aaed4fd10c572c99ac7b0d99e0d2188` (= f16b1f3) | G3-7R `9181966`, Phase 4-R1 `70fbbc4`, WILD1 `c26f980` / R2 `b31020a` / R3 `6f20587`, Smart Booking recovery `198ed4d`, launch merge `163b37e` |
| `routes/whatsapp.py` | `4e979d370c91e7bff945727c1e970b335f6234a4` (= f16b1f3) | booking Phase 4 `06250ed`, W1F-W-01 R2 `e0e83c3` |

**Effective** (`sentinel_probe.sh`, each drift a one-line change bind-mounted read-only): green on the sealed candidate (1 passed); RED for a drift in each of the four re-recorded files and in the still-frozen `schedule.py` (1 failed each). Not skipped, xfailed, deleted or weakened.

In the SQLite comparisons the sentinel is the one **GONE** identity (red on base, green on the candidate): that is the authorized repair, not a lost test (§7).

## 5. Kit correction (R6-C1 kit)

Created by `cp -a` of the sealed R6 kit (verified `99c3e161…` before copying); only the two self-paths were rewritten in configuration and procedures; R6's historical `docs/AUTHORIZATION_TEXT_R6.md` was restored byte-identical. Every change is in `docs/R6C1_DIFF_vs_R6_kit.patch`. 121 entries.

- **R6IRV-K01, file set.** Before any write, `FINALIZE_PINS.sh` refuses unless the kit's file set equals the sealed listing plus the two manifests, the directory set equals the directories that listing implies, and no link or special file exists. The provisional anchor remains the external trust root.
- **R6IRV-K01, transactional.** Step 4 stages the whole proposed final kit in a private temp copy, writes the verdict `PIN_FINALIZATION_STAGED` (never accepted by the live pin gate), and runs `VERIFY_PIN_FINALIZATION.sh --staged` deep. Only then is `PIN_FINALIZATION_VERIFIED` written, in the stage, and re-verified. Step 5 publishes the evidence, then CHECKSUMS, then `config/pins.conf` LAST. Step 6 verifies the published kit deep. On any failure during or after publication (a failed post-write verification, a failed rename, an INT/TERM/HUP interruption), the EXIT trap restores the exact provisional bytes, pins.conf first and with signals ignored, removes the evidence, and re-checks pins.conf = sealed, CHECKSUMS = sealed, no evidence, PROVISIONAL, manifest OK and file set = sealed. Exit 3 means restored, exit 4 means restore unconfirmed, and exit 2 means refused before any write.
- **R6IRV-K02, deep VERIFY.** It independently re-checks:
  - the commit: single parent; changed path set; **every changed-path blob**; **the whole tree outside docs/Launch** (git archive into a private temp dir); the docs/Launch addition rule; **the commit's `backend/app` manifest**;
  - the image: **id exists**; tag; **schema_head label**; git_commit label; GIT_SHA; `/app/app`;
  - the evidence: **every one of its 11 fields** exactly once, naming the sealed pins;
  - the external anchor, and the directory set and link check.
  An unknown option is refused (exit 64).
- **New pins.** Candidate tree `3560ea14…ab38`, changed paths `2b927a5d…40ca`, app `8aaa8d28…f07d`. `PIN_STATE=PROVISIONAL`, three keys `UNFINALIZED`. `FINAL_TAG` is unchanged (`ridecheck-crm-backend:minlaunch-final-r6`): no image carries it (checked), and FINALIZE binds it to the C1 commit's image by labels, GIT_SHA and content.
- **Docs.** `docs/FINAL_CONTROLLED_RELEASE_RUNBOOK.md` and `docs/COMBINED_HANDSET_WILD_RUNBOOK.md` updated for R6-C1: the focused review gate, the FINALIZE exit-code table, the deep VERIFY scope, R6-C1 build label, and the WILD STOP notes for S01/S02/P01/C01/P02/P03/Q01. `docs/AUTHORIZATION_TEXT_R6C1.md` is new. README has an R6-C1 section.
- **Tests.** `tests/test_kit_r6c1.sh`, sections K76–K81, with shimmed git/docker/mv only:

  | Section | Covers |
  |---|---|
  | K76 | eight preflight refusals: one unsealed added file, under pins/, a fake evidence temp, one missing sealed file, a rename, an unlisted dir, a symlink, a hidden file. Each exits 2 with the kit byte-identical; the refusal comes from step 1 itself |
  | K77 | failed stage verify (exit 2); failed post-write verify, failed pins.conf rename, failed CHECKSUMS rename, interruption during publish (exit 3). Each leaves `PROVISIONAL/no-evidence/0 VERIFIED lines/pins sealed/CHECKSUMS sealed/whole kit = pre-run`. ENABLE, RESET_BETWEEN and tester reset are all refused by the pin gate with 0 docker changes, and the kit can still be finalized correctly afterwards |
  | K78 | same-path-set wrong TEST blob; wrong whole-tree blob outside the changed paths; wrong backend/app; image app; schema label; missing image; parent; GIT_SHA; six evidence fields tampered |
  | K79 | the valid single-parent commit and matching image (exit 0, exactly 3 files differ, VERIFIED, gate OPEN); six kinds of tampering after success |
  | K80 | RED control on the sealed R6 kit |
  | K81 | each layer effective on its own |

  `tests/mutate_r6c1.py` has 29 mutants.

**Red control (K80, against copies of the sealed R6 kit and R6 candidate).** The uncorrected R6 FINALIZE fails in three ways:
- with one unsealed added file it exits 3 *after* writing `PIN_STATE=FINAL` and `verdict=PIN_FINALIZATION_VERIFIED`, and **the live pin gate OPENS** on that failed kit;
- after a post-write verification failure it leaves FINAL plus a VERIFIED verdict;
- R6 VERIFY (deep) **passes** a same-path-set commit with a different test blob.

R6-C1, in the same three scenarios: exit 2 with nothing written and the gate closed; exit 3 with the exact provisional posture and the gate closed; NOT VERIFIED.

**Private runtime.** It holds the four R5/R6 inputs, byte-identical, copied by `cp -p` from the verified R6 private runtime. `private_runtime.sh manifest` gives a `PRIVATE_MANIFEST` of `28a919813b73fabbd1d9c7ceb267ad026f6647050666d4433f8bbd7ce136c8b2` (4 entries), identical to R6's. `verify`: VERIFIED.

## 6. Documentation correction (R6IRV-D01)

The R6 closeout §1 table is corrected in place for exactly the two stale rows. Each now names the sealed value and says it was corrected in R6-C1:
- `conversation_engine.py` → `7bcabe6a…75c4c` (it listed the pre-re-seal `43d98767…`);
- `test_r6_structured_response_contract.py` → `17d18b17…ae63` (it listed `6b7d5faf…`).

Nothing else in it changed. Its sha256 is now `603e37abf10783c7eea1b146a0d61302caa472304170fa06825f581f091d7141` (the R6 audit cites the earlier `0a413638…`). The R6 integrated review audit is not rewritten.

## 7. Verification (serial, gated; `ridecheck-crm-test:l4.7b2`, `--network none`, read-only sealed trees)

**Regression (failing-identity comparison, normalized messages).**

| Run | Base f16b1f3 | R6-C1 candidate | Comparison |
|---|---|---|---|
| Focused SQLite (69 files; 63 exist on base) | 18 F / 2149 P / 361 S (2527) | 17 F / 2310 P / 361 S (2687) | **NEW 0**, GONE 1, COMMON 17, changed 0, +160 cases, −0, skip changes 0 |
| Full SQLite `../tests` | 62 F + 9 E / 7490 P / 786 S (8346) | 61 F + 9 E / 7652 P / 786 S (8507) | **NEW 0**, GONE 1, COMMON 70, changed 0, +161, −0, skip changes 0 |
| PG 16.13 (`Etc/UTC`), affected suites, R6 runner (32 files, fresh clone per file) | 1 F / 664 P / 342 S (1007) | 1 F / 684 P / 342 S (1027) | **NEW 0, GONE 0**, COMMON 1, changed 0, +20 |
| PG 16.13, all 33 PG-capable suites with EVERY `*_PG_URL` wired (migration/stamp/base/ext/clean URLs → an empty per-file DB) | 47 F / 966 P / 42 S (1055) | 47 F / 986 P / 42 S (1075) | **NEW 0, GONE 0**, COMMON 47, changed 0, +20 |
| PG SB2B with its clean database | as R6 | identical | NEW 0 / GONE 0 |

- **The one GONE** in both SQLite runs is `test_sb1_20_frozen_files_unchanged`. It is red on base, green on the candidate, and is the repair owner ruling 7 required (§4). Every other identity has GONE 0 and NEW 0.
- **The 47 common PG failures** in the fully wired run are harness-environment failures, identical on both trees:
  - suites whose migration/base/stamp URL expects a pre-staged schema got an empty database (`alembic_version`, `revisions`, `leads` do not exist);
  - the two pre-existing failures the R6 review reported (the stale-head assertion in `test_convergence_p2_authority_pg`, and `test_rv03_pg_13`).
- The R6 reviewer's per-suite schema staging (29 suites, 113 skips) was not replicated here (§10).

**R6-C1 source suites.**

| Suite | SQLite | PostgreSQL |
|---|---|---|
| `test_r6_c1_claim_frames.py` (S01/S02/P01 frames, T01, P02/P03/C01 pins) | 32 passed, 348 subtests | — (pure) |
| `test_r6_c1_claim_frames_production_path.py`: full CE path; direct webhook false/true × Smart Booking false/true; worlds new / corolla / located / both / gol / slots | 13 passed, 424 subtests | **13 passed, 424 subtests** |
| `test_r6_full_engine_prose_corpus.py` + `test_r6_structured_response_contract.py` (R6, unchanged) | pass (focused run) | corpus 7 passed, 888 subtests |
| R5-F1 production path (`test_r5_f1_motorcycle_handoff_production_path.py`) | pass (focused run) | **26/26, outstanding questions 0** |

**Red controls (full engine, the same `run_case`, 14 cases × 4 worlds × 2 postures = 112 per tree; `probe/red_c1.py`).**

| Tree | Ordinary prose altered | S01 window delivered | S02 wrong vehicle delivered | P01 false question delivered |
|---|---|---|---|---|
| f16b1f3 | **48 / 48** (the original defect) | 0 / 16 (refused) | 0 / 16 (refused) | **30 / 32** (bypass) |
| R6 (sealed) | 0 / 48 | **16 / 16** (introduced) | **16 / 16** (introduced) | **30 / 32** (bypass) |
| **R6-C1** | **0 / 48** | **0 / 16** | **0 / 16** | **0 / 32** |

- The two P01 cells that are not delivered on base and R6 are the upstream price scrub.
- The R6-C1 output on PostgreSQL is **byte-identical** to SQLite (`8908ab1dd8dc775c`).
- No tree armed a pending vehicle from prose in this corpus.

**Source mutations** (27, each one exact replacement bind-mounted read-only over the sealed tree; five suites, `-x`): **27/27 KILLED**; the unmutated control passes 160, with 1145 subtests. They cover:

| Area | Mutants | What each weakens |
|---|---|---|
| S01 | C01–C07 | range always information; day anchor ignored; transactional frame ignored; endpoints unchecked; general frame ignored; definite weekday dropped; empty evaluation supporting a window |
| S02 | C08–C13 | possessive frame off; topic frame off; any possessive a claim; action in another clause; topic without clitic; topic not initial |
| P01 | C14–C22 | blanket question skip restored; question price rewritten; question availability skipped; every vehicle question a clarification / a claim; nouns as proposal verbs; every location question a clarification; question lexicon counted; question asserting a booking |
| Provenance (T01) | C23–C24 | the audit's M03 `isinstance` mutant, and its ComposedText twin |
| State isolation | C25–C27 | producer reads the reply; AI time unchecked; AI location intent with the place known |

**SB1 sentinel effectiveness:** green on the candidate; red on drift in each of the 4 re-recorded files and in `schedule.py` (§4).

## 8. Kit verification (on the FINAL R6-C1 seal `641dac2e…6184`)

| Job | Result |
|---|---|
| R6-C1 `tests/test_kit_r6c1.sh` (K76–K81) | **84 passed, 0 failed** |
| R6-C1 kit mutations `tests/mutate_r6c1.py` (K01 file set ×4, transactional ×11, K02 ×9, each layer on its own ×5) | **29/29 KILLED** |
| R3 `tests/test_kit.sh` | 269/1 on the first run (private-runtime modes, §10.3); **270/0** on the rerun after restoring them |
| R5 `tests/test_kit_r5.sh` | **242/0** (twice) |
| R6 `tests/test_kit_r6.sh` (K67–K75; FINALIZE/VERIFY under R6's K74 cases) | **112/0** |
| R6 kit mutations `tests/mutate_r6.py` | **29/34 KILLED**. R25, R26, R27, R29 and R31 are MASKED in R6's K74 by the new independent layers (the staged deep VERIFY; the evidence-field checks): FINALIZE still refuses and VERIFY still fails. The same five mutations are Q25–Q29 in `mutate_r6c1.py`, each KILLED by K81, which requires the refusal to come from the layer itself |
| Real-config negatives (the sealed kit's own `window-W2.conf`, `window-H.conf`, `reset-AB.conf execute`, `window-W1A.conf execute`; docker replaced by a recording shim that fails) | all four exit 2 `R6 pins: pins are 'PROVISIONAL'…`; **0 docker calls**; kit still sealed |
| **Rehearsal** `tests/rehearse_r6.sh` → `PRIVATE-R6-C1/run-r6c1-20261111T233308Z` (log `d283bc05bc826d59`) | **94 passed, 0 failed** |
| Seal after every job | `sha256sum -c` OK; anchor `641dac2e…6184` |

**What the R6-C1 rehearsal proved.** It used the sealed R6-C1 candidate's `backend/app` mounted read-only on the f16b1f3 image. RR0/RR2 prove its manifest is the pinned `8aaa8d28…`. It ran on its own `--internal` network, with PG restored from the private snapshot under `TimeZone=Etc/UTC`, a real uvicorn and its daemons.
- **Order and ledger:** H → H2 SKIPPED → W1A → RESET_AB → W1B → RESET_BC → W2 → final KILL. Exactly one OPENED/CLOSED per executed window, each CLOSED with `criteria_not_met=0` before ACCEPT, and the final ledger chain verifies (RR11).
- **Reset isolation (RR6/RR8):**
  - both resets refused before RESET and without an accepted expectation, and refused out-of-scope rows;
  - dry-run rolled back; executed once;
  - non-tester rows identical; evidence, ledger and head byte-identical; audit rows retained; both testers fresh.
- **Local-time bookings:** no future occupying booking remains after either reset, on the local wall clock. RR13 measures the R5 predicate missing a local now+1 h booking that the helper, the ENABLE watermark, the breach counter and preflight all count.
- **W2 (RR10):** contact Flow only; needs_human false → true; no offer, booking, notice or job; nothing pending. The reply after the contact form IS the truthful handoff constant, and **no outstanding question** was opened. W2-6 MET; ACCEPT W2.
- **Final posture:** all eight switches literally false and `booking_dispatch_allowed()` False (env and a fresh parser process). No bytecode was written. Live containers and fingerprint unchanged (RR12). 0 rehearsal containers or networks remain.

An earlier rehearsal on an intermediate seal (`run-r6c1-20261111T215017Z`; identical scripts, earlier test/README bytes) also gave 94/0; it is kept.

## 9. Live isolation (read-only; before 21:03:08Z, after 00:27:09Z)

`live/probe.sh` is R6's probe: `docker inspect`, fresh-process `docker exec` reads, and `psql` with `default_transaction_read_only=on`; it prints no customer value. **`diff before after`: empty (byte-identical).**

| Item | Before = after |
|---|---|
| backend | `04e14e5770f9`, image `sha256:1b8ed611…8fc14`, restarts 0, GIT_SHA f16b1f3 |
| postgres / n8n | `5246fd86d829` / `6ea80570b67b`, restarts 0 |
| switches | all eight literally false; `booking_dispatch_allowed=False`; allowlist sha16 `ad98b5c31a4e8ed8` |
| database fingerprint | `fbae2abdc3371f9146f0ac51a2ffa427` (also equal to the R6 closeout and the R6 audit) |
| deploy-prep worktree | head f16b1f3, tracked diff 0 |

- **Kits.** R3/R4/R5/R6 verify OK (CHECKSUMS `dd9686c6` / `d884015e` / `3c228f71` / `99c3e161`), with no file newer than CHECKSUMS. The one exception is R6's `pins/PROVISIONAL_CHECKSUMS.sha256`, written 3 ms after CHECKSUMS at R6's own seal and equal to the anchor.
- **Private runtimes.** R3/R4/R5/R6 verify OK; their newest file predates this milestone (R6: 1315 files, the R6 audit's after-state).
- **Git.** The source worktree HEAD is f16b1f3, with 0 commits and 0 staged; it equals the sealed candidate byte-for-byte. `/opt/ridecheck-crm` HEAD is unchanged; no ref was written.

## 10. Disclosures

1. **The live "before" fingerprint was taken at 21:03Z, after source work had begun** (20:30Z), not at the very start. Until then only `--network none` test containers had run, against read-only mounts of the worktree and scratch copies; nothing reached live. The before reading is byte-identical to the R6 after-state.
2. **The GONE 1 identity** is the repaired sentinel (§4, §7). It is the intended effect of ruling 7, and the only GONE.
3. **Private-runtime file modes (LOW, pre-existing kit behaviour, not fixed).**
   - The first real-config negative run left 7 refusal-evidence files (`timeline.log`, `.r4_overlay_why`) in the R6-C1 private runtime at mode 0644. An early refusal writes them with the caller's umask; the parent directories are 0700 root.
   - `private_runtime.sh verify` then failed closed, and the R3 suite reported 269/1.
   - I restored 0600 on those 7 test artefacts, the runtime verifies, and the R3 rerun is 270/0. The second negative run created no 0644 file.
   - The R6 runtime's equivalent files are 0600, so R6's negatives evidently ran under a stricter umask.
   - Owner/reviewer item: whether ENABLE/RESET should set `umask 077` before their first evidence write.
   - The refusal evidence (`window-W2-1/`, `window-H-1/`, `reset-AB-1/`, `window-W1A-1/`) remains in the R6-C1 private runtime; the owner may archive it before the first real window.
4. **R5 suite flake (pre-existing).** K52's "the re-check passes on an unchanged OFF state" failed once in the first R5 run (241/1).
   - Rerun ×6 it flaked 1/6 on the sealed R6 kit and 0/6 on R6-C1; the files are identical to R6.
   - It fails closed (the re-check refuses, never admits). Both final R5 runs are 242/0.
   - Root cause not established (a pipefail/SIGPIPE theory was tested and NOT reproduced).
5. **`mutate_r6.py` is 29/34**, not 34/34, for the masking reason in §8; the five mutations are killed as Q25–Q29. One further candidate mutant (FINALIZE's `backend/app` check) is equivalent, because the whole-tree check precedes and contains it, and was dropped.
6. **PG coverage.**
   - The R6-style run leaves 342 skips: its runner wires only a subset of the `*_PG_URL` variables.
   - The fully wired run has 42 skips and 47 common harness failures (empty staged databases).
   - Both are NEW 0 / GONE 0. The R6 reviewer's per-suite schema staging was not reproduced.
7. **`FINAL_TAG` is unchanged** (`minlaunch-final-r6`): no image carries it, and the C1 pins bind content. The pin-gate refusal text still says "this R6 kit is PROVISIONAL", because the `kit_r6.sh` wording is unchanged.
8. **Seal history.** The kit was sealed seven times during verification:
   - a seal script that wrote its temporary manifest inside the kit was caught by the new file-set preflight itself;
   - K76/K77 probe and expectation fixes;
   - the K76 step-1 tightening and the Q10/Q15 tests (two mutants found weak tests);
   - the K81 layer tests;
   - the README.

   **Every reported kit result ran on the final seal `641dac2e…6184`**, except the intermediate rehearsal named in §8.
9. **Light work overlapping heavy jobs.** Shell-only kit runs with fake docker ran while a heavy job was running: the K52 flake reruns, K81 development, and the private-runtime verify. Every heavy job started at ≥ 1599 MiB MemAvailable; the lowest reading during a job was 730 MiB; 0 ABORT (35 gated jobs).
10. **An empty `window-OFF/evidence/` directory** in the R6-C1 private runtime was created by `private_runtime.sh` (`window.conf` sets `EV_ROOT=…/window-OFF`). It has 0 files.
11. **Residuals of the new frames** are in §3.6. The accepted debts P02, P03 and Q01 are pinned (§3.5).

## 11. Disposition of every R6IRV finding

| ID | Severity | Disposition |
|---|---|---|
| R6IRV-S01 | HIGH | **FIXED** (§3.1). Full path, 4 postures, SQLite and PG; red control base refuses / R6 delivers 16/16 / C1 0/16 |
| R6IRV-K01 | MEDIUM | **FIXED** (§5). File-set and directory-set preflight; transactional finalization with an exact provisional restore; K76/K77/K80; Q01–Q15 killed |
| R6IRV-K02 | LOW | **FIXED** (§5). Deep VERIFY re-checks blobs, tree, app, labels and all evidence fields; K78/K80; Q16–Q24 killed |
| R6IRV-S02 | MEDIUM | **FIXED** (owner: not accepted) (§3.2). Red control R6 16/16 → C1 0/16; ordinary prose 0/48 altered |
| R6IRV-P01 | HIGH | **FIXED** (owner: not accepted for the WILD) (§3.3). Base/R6 30/32 → C1 0/32; clarification questions preserved |
| R6IRV-P02 | MEDIUM | **ACCEPTED LAUNCH DEBT** (owner). Pinned (`test_p02_01..04`); documented for a response-policy cleanup |
| R6IRV-P03 | LOW | **ACCEPTED LOW LAUNCH DEBT** (owner). Pinned (`test_p03_01`) |
| R6IRV-T01 | LOW | **FIXED** (§3.4). Direct assertion; the audit's M03 is now killed (C23) |
| R6IRV-Q01 | LOW | **ACCEPTED FOR LAUNCH** (owner); unchanged |
| R6IRV-D01 | LOW | **FIXED** (§6) |
| R6IRV-C01 | INFO | **APPROVED** (owner); copy pinned |
| R6IRV-H01 | INFO | **REPAIRED** (owner: repair now) (§4). Green and effective |

Predecessor items: R5-F1 remains **FIXED** (SQLite; PG 26/26; rehearsal RR10 W2-6 MET). R5F1RV-01 remains corrected (0/48 ordinary prose altered). R5RV-01…04 are unchanged and corrected (RR13; K67–K72 112/0).

## 12. W-3 and WILD status

**W-3 remains OPEN. The final WILD remains NOT RUN.** No handset test, window, tester reset or live reset was performed. The R6-C1 kit is **PROVISIONAL**: every live ENABLE, RESET_BETWEEN and tester reset refuses. **The R5 kit remains the live emergency instrument** for the f16b1f3 deployment, because R6-C1's `EXPECT_IMAGE=UNFINALIZED` cannot verify it. The R6 kit is superseded and was never finalized.

## 13. Next milestone

**PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6-C1-FOCUSED-INTEGRATED-INDEPENDENT-REVIEW.**

A fresh reviewer should independently re-measure:
- S01, S02 and P01 on the full path (4 postures, SQLite and PG) and the ordinary-prose corpus;
- K01/K02 against the R6-C1 FINALIZE/VERIFY: an added unsealed file, a failed post-write verification, an interruption, and a same-path-set wrong-blob commit;
- the sentinel's effectiveness;
- the regression comparisons;
- one rehearsal against the R6-C1 kit (anchor `641dac2e…6184`).

They should also rule on the §10 items (the umask of refusal evidence; the K52 flake; PG staging coverage). Until that review is READY, commit, push, image build, pin finalization, dark deployment, windows and the WILD remain **NOT AUTHORIZED**.

## 14. Evidence (sha256 prefix; under `/opt/ridecheck-builds/crm-final-convergence-r6-c1-20261111T203547Z/` unless stated)

| Item | sha16 |
|---|---|
| `cand.manifest.full` (sealed candidate, 722 files) | manifest `9fbad7b010bc8ba9…` |
| `junit/focused_base.xml`, `focused_cand.xml`; `focused.cmp.txt` | `a574e3569830ea65`, `7871957d8ca9ad7d`; `9b0f924dc5425aba` |
| `junit/full_base.xml`, `full_cand.xml`; `full.cmp.txt` | `c8b556ec74c35387`, `90155f5b2d480c53`; `abb1d85049e95319` |
| `pgstage.txt`, `pg.cmp.txt`; `pgstage2.txt`, `pg2.cmp.txt` | `cd723495bba762f2`, `1d46d95e9fe931ed`; `fd3d23db5de41b03`, `43d124f4ba4302d6` |
| `probe/red_c1.py`; `red_base.jsonl`, `red_r6.jsonl`, `red_c1.jsonl` = `red_c1_pg.jsonl` | `5f01e8075dd21216`; `ec79ca2c66e66794`, `feb0b30eceb9b42d`, `8908ab1dd8dc775c` |
| `mut/mutants.py`, `mut/results.txt` | `9b0a8d2c4fd46fb7`, `509b250679efa18d` |
| `sentinel.txt` | `56b53121fee05d48` |
| `final_kit_r6c1.txt`; `final_kitmut_c1/MUTATION_RESULTS.txt` | `7ee5abf5dbb9fe0e`; `60e70151819e43f2` |
| `final_kit_r3.txt`, `final_kit_r3_rerun.txt`; `final_kit_r5.txt`, `final_kit_r5_rerun.txt`; `final_kit_r6.txt` | `26b79b945a560d99`, `0b92682021a1c956`; `ac24d3fca9f52012`, `153389aa5ceed6bf`; `2109a9058d8b501c` |
| `final_kitmut_r6/MUTATION_RESULTS.txt`; `final_neg_realconfig.txt` | `f0abd2d1896808b3`; `d6b551e970e1f3e5` |
| `final_rehearsal_stdout.txt`; `PRIVATE-R6-C1/run-r6c1-20261111T233308Z/rehearsal.log` | `5968a02bd64d7487`; `d283bc05bc826d59` |
| `live/probe.sh`, `live/before.txt` = `live/after.txt` | `fd64bbc750c3799b`, `1179c5ed6c7db8e3` |
| `chain.log`, `memory.log` | `4f42d307070ef1e7`, `512344aa1c6e7166` |
| R6-C1 kit `docs/R6C1_DIFF_vs_R6_kit.patch` | in the sealed kit |

**VERDICT: READY FOR FOCUSED INTEGRATED INDEPENDENT REVIEW.** STOP. This closeout is not staged or committed.

