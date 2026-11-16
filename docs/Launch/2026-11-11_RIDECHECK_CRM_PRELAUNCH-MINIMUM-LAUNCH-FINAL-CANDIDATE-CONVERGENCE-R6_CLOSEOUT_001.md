PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-CANDIDATE-CONVERGENCE-R6
# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — FINAL CANDIDATE CONVERGENCE R6 — CLOSEOUT 001

Date: 2026-11-11 (runs 14:25Z–17:40Z)
Source candidate: worktree `/opt/ridecheck-crm-r5-f1-response-validation`, branch
`fix/prelaunch-r5-f1-response-validation-boundary`, base = HEAD
`f16b1f328f3fbf1050afdcec583d252d528c72bf`. 0 commits, 0 staged.
R6 kit: `/opt/ridecheck-builds/crm-minimum-launch-enablement-kit-R6-20261111T144848Z`
R6 private runtime: `/opt/ridecheck-builds/crm-minimum-launch-enablement-PRIVATE-R6-20261111T144848Z`
Harness and evidence: `/opt/ridecheck-builds/crm-final-convergence-r6-20261111T142504Z/`
Authoritative inputs:
- R5-F1 independent review `…R5-F1-RESPONSE-VALIDATION-BOUNDARY-CORRECTION-INDEPENDENT-REVIEW_AUDIT_001.md` (R5F1RV-01…08);
- R5 kit review `…KIT-R2-CORRECTION-INDEPENDENT-REVIEW_AUDIT_001.md` (sha256 `fb9604d1…a56ab`; R5RV-01…04).

**VERDICT: READY FOR INTEGRATED INDEPENDENT REVIEW**

- Nothing was staged, committed, pushed, built (no final image), deployed or migrated.
- No live flag changed. Meta was not contacted, no message was sent, nothing was written to live
  data, and no handset test, WILD window or live reset was run.
- R3, R4 and R5 kits and private runtimes are unchanged. The deployment-preparation worktree is unchanged.
- **W-3 remains OPEN. The final WILD remains NOT RUN.**
- **The R6 kit is PROVISIONAL.** Every live ENABLE, RESET_BETWEEN and in-window reset refuses until
  the owner inserts final pins.

---

## 0. Summary

| Requirement | Result |
|---|---|
| R5F1RV-01, the broader prose reinterpretation | **Corrected at a structured boundary** (§2). Every outbound value is validated as a typed response envelope: provenance by exact type, declared claims checked against canonical state, and claim-shaped text checks only as defence in depth. State and question recording read the declaration, never the words |
| Full-engine evidence, red control | 342 ordinary turns (57 answers × 3 worlds × 2 postures). **f16b1f3: 170 altered, 144 replaced by the vehicle/zone fallback with an outstanding question, 8 armed a pending fuzzy vehicle. Candidate: 6 altered, all the one deliberate locative claim; 0 pending vehicles.** PostgreSQL results are byte-identical to SQLite (§3.3) |
| Full matrix (worlds × direct webhook false/true × Smart Booking false/true) | `test_r6_full_engine_prose_corpus.py` passes on SQLite and **PostgreSQL 16.13**. Every R5F1RV-01 example is kept; all six real contradictions are still blocked (§3) |
| R5-F1 | Still fixed. Production path 26/26 on PG, 0 outstanding questions. In the R6 rehearsal, **W2-6-truthful-handoff-text is MET and W2 was ACCEPTED** |
| R5F1RV-02 / 03 / 04 | Corrected and tested (§2.6) |
| R5RV-01 | One canonical local-wall-clock construction is used at all five sites. Tested on PG `TimeZone=Etc/UTC` across clocks and date rollover, and on the real schema in the rehearsal (§4.1) |
| R5RV-02 / 03 / 04 | Corrected (§4.2–4.4) |
| Provisional pinning | Pins identify the candidate by base commit, whole-tree manifest, changed-path manifest and `/app/app` manifest. Live use fails closed. Finalization is narrow and verified (§5) |
| Regression | Focused SQLite: **NEW 0 / GONE 0**. Full SQLite: **NEW 0 / GONE 0** (71 = 71). Affected PG suites: **NEW 0 / GONE 0**, 0 failures. SB2B PG: identical (§6) |
| Kit suites on R6 | R3 **269/0**. R5 **242/0**. R6 **112/0**. Rehearsal **94/0**. Real-config negatives: 4 refused, **0 docker calls** |
| Mutations | Source **26/26** killed. Kit **34/34** killed |
| Live isolation | Before = after (§9) |

---

## 1. Scope and identity

**Changed paths vs f16b1f3.** There are exactly 12 paths: 10 code and test files, plus 2 earlier launch documents untouched here.

| Status | Path | sha256 |
|---|---|---|
| M | `backend/app/services/response_validator.py` | `9a7b9e463df5328ea57ee75670ad5a019083478e9facc0453ec5a94e51a77cdf` |
| M | `backend/app/services/conversation_engine.py` | `7bcabe6af3fbbebffa6bb2d4c815cc1622e32005bf9015b9710225887ad75c4c` (corrected in R6-C1, R6IRV-D01; this table first listed the pre-re-seal `43d98767…`) |
| A | `backend/app/services/reply_claim_shape.py` | `4f9bfb3f6fa3ed30d2157a2eabf59f31a4a045da30823e68df738ace780d7725` |
| M | `tests/test_l4_7d_response_validator.py` | `ff379913cca5effcfc77a9513ebc3e262c69eb7ed58a47cd9dbd59175ed886a5` |
| M | `tests/test_l4_7w5_g3_6_proposition_migration.py` | `9b0a3beda0aa1f2475fc86150e0c569c59e6a9384a4957789ec36a672596dbdb` |
| A | `tests/test_r5_f1_motorcycle_handoff_production_path.py` | `ed191b47…7da9` (unchanged from the reviewed R5-F1 candidate) |
| A | `tests/test_r5_f1_template_validation_contract.py` | `d4da3808a53923f19cc27ff45f387263eda32cd2e0a60631f8432b2c40fd059a` (was `234aed89…`; §7) |
| A | `tests/test_r6_structured_response_contract.py` | `17d18b175a9b5cc8c42871547706c4fa251785bfda3bacbfd6419fd330f0ae63` (corrected in R6-C1, R6IRV-D01; this table first listed the pre-re-seal `6b7d5faf…`) |
| A | `tests/test_r6_full_engine_prose_corpus.py` | `c59fe1512ed1c4a3fa7fad50c27d092c7987bab9041c0a6fa2609ddc2e8bc466` |
| A | `tests/fixtures/r6_zone_names.txt` | `5994ade3…52fd` (211 public place names, `group\|detail`, no amounts) |
| A | `docs/Launch/2026-11-10_…R5-F1…CLOSEOUT_001.md`, `docs/Launch/2026-11-11_…R5-F1…INDEPENDENT-REVIEW_AUDIT_001.md` | unchanged here |

Diff size: CE +174/−45, validator +759/−20 (vs f16b1f3, R5-F1 included).

**Candidate tree.** This is a copy of `git ls-files -co --exclude-standard`: 718 files, manifest
`38e6d8c3ec5d038f2804570dc4c79008072c4d5cd9ba18de4efcea03abd7bffc`, sealed at
`…-r6-20261111T142504Z/cand/`. It is byte-equal to the worktree at the end. 0 `__pycache__`/`.pyc`.

**Secret and identity scan.** It covered the 3,105 added lines and counted only; no value was printed.
- 0 hits for any value from `/opt/ridecheck-crm/.env` or the private tester and operator inputs.
- 0 credential shapes (EAA…, sk-…, PEM, Bearer, `WHATSAPP_TOKEN=`/`SMTP_PASSWORD=` literals).
- 0 `549…` numbers. The only email is `@example.invalid`.
- The five whole-file needle hits are all in lines unchanged from base (an existing email default
  in CE and an existing tester number in an untouched part of `test_l4_7d`).

---

## 2. The source correction (R5F1RV-01 class fix, R5F1RV-02/03/04)

The owner's authority order is now the order of the code:

> canonical state → structured plan/claims → deterministic validation → rendered text → conservative
> text checks (defence in depth only).

### 2.1 The structured response contract (`response_validator.py`)

**Closed enums.** Python refuses to extend them.

| Enum | Members |
|---|---|
| `ResponseProvenance` | `APPROVED_TEMPLATE`, `DETERMINISTIC_COMPOSED`, `AI_COMPOSED`, `VALIDATION_FALLBACK` |
| `DomainClaim` | `VEHICLE`, `LOCATION`, `AVAILABILITY`, `PRICE`, `BOOKING`, `ACCEPTANCE` |
| `QuestionIntent` | `VEHICLE_IDENTITY`, `INSPECTION_LOCATION` |
| `StateUpdate` | `RECORD_OUTSTANDING_QUESTION` |

**`StructuredClaim(kind, value)`** carries a canonical identity (vehicle `(marca, modelo)`, zone,
amount, `HH:MM`), never wording. **`ResponseDeclaration`** holds claims, question intents and allowed
state updates.

**`ComposedText(str)`** is deterministic text that carries its declaration.
- It has the same immutability as `ApprovedTemplateText`.
- Every derived value is a plain `str`, so any edit returns the text to AI trust.
- `composed_text(…)` declares it.

**`validate_envelope(value, facts, …)`** is the single entry point. It is total, and its result
text is always an exact `str`.

1. **Provenance is decided by exact type only.**
   - An `ApprovedTemplateText` is an approved template (the R5-F1 contract, unchanged).
   - A well-formed `ComposedText` is deterministic composed text.
   - Anything else is `AI_COMPOSED`, the least-trusted class.
   - A forged subclass, a duck-typed `_declaration` or a malformed declaration fails closed to AI trust and records a finding.
2. **Declared claims are validated against `CanonicalFacts`.**
   - Vehicle = focus marca/modelo; location = canonical zone; price = PricingService quote;
     availability = evaluated slots; booking = booked revision; acceptance = lead flag/stage.
   - An unknown kind or malformed claim is refused.
   - A conflict refuses the **whole** value. It is never edited.
3. **The rendered text then passes the claim-shaped defence in depth (§2.2).**
4. **Question intents.**
   - A template or composer may declare intents, and they are kept only if the delivered text
     really asks a question.
   - A fallback's intents come from its contract.
   - AI prose declares nothing of its own. Its one recordable intent is `INSPECTION_LOCATION`, and
     only when canonical state genuinely lacks the place (the reply model's rule 20 obliges it to
     ask) and its own surviving question asks it.
5. **Any internal failure** returns the safe line that asks nothing. It never returns the value.

**Plan-appropriate fallbacks (item F).** Selection is total; malformed facts select `NO_QUESTION`.

| Contract | When | Text | Declared intents |
|---|---|---|---|
| `ASK_VEHICLE_AND_LOCATION` | vehicle and location both missing | the existing `_FALLBACK_REPLY` (unchanged) | vehicle, location |
| `ASK_LOCATION` | vehicle known, location missing | existing lead + the existing `_CANONICAL_LOCATION_ASK` | location |
| `ASK_VEHICLE` | location known, vehicle missing | existing lead + the existing vehicle half of the fallback | vehicle |
| `NO_QUESTION` | both known | "Gracias por tu mensaje. Cualquier otra consulta, escribime por acá." (**new copy, owner review item**) | none |

A known vehicle is never re-asked. The fallback's location question becomes an outstanding question
only through its declared contract (asked_by `DETERMINISTIC_TEMPLATE`).

### 2.2 Claim-shaped defence in depth

These rules contain no list of model words or place words; a test parses the code and finds no such
literal.

**Vehicle (`reply_claim_shape.py`).** A catalogue match is a claim only when at least one holds:
- (a) the make is written with the model: "Fiat Uno";
- (b) the model carries a model year: "un Uno 2015", "un 2008 del 2014";
- (c) it is a proper noun after a determiner: "Es un Corolla".

Otherwise it is a **mention**, recorded as a diagnostic. "uno por uno", "uno de los dos autos",
"a las 10 en punto", "buena idea", "punto por punto" and "Uno nunca sabe…" are all mentions.
A classification error reads strictly, as a claim.

**Location.** A whole-word zone is a claim only when all three hold:
- it sits in a locative frame (en/hasta/a/para/por, optionally through zona/barrio/…);
- it is outside a conditional clause (si/cuando/aunque/donde);
- the sentence speaks of the inspection subject or of going to / being at a place.

So "el auto está en Pilar" and "vamos hasta Pilar" are claims. "pilares", "norteamericano",
"Gracias Pilar", "unas flores", "Si el auto está en zona Norte…" and "Trabajamos en CABA y GBA…"
are not.

**Availability.**
- An hours range ("de 9:00 a 18:00") is business-hours information, recorded as a diagnostic.
- "según (la) disponibilidad" and "sujeto a disponibilidad" are conditions, not slots.
- Every other time token and the availability lexicon are unchanged.

**Price, booking and acceptance** checks are unchanged.

### 2.3 Wiring in CE (`conversation_engine.py`)

- **`_validate_outbound_text`** calls `validate_envelope` with `_reply_vehicle_claim_resolver` (the
  same catalogue chain plus the shape verdict). It keeps `_last_outbound_envelope` and logs
  `CE_RESPONSE_CONTRACT`.
- **On failure** it returns the safe no-question line, or the exact approved template. The
  exception-driven raw-text path is closed.
- **`_send_text_to_wa`** passes the primary's envelope to `_record_delivered_questions`.
- **Composer declarations:**
  - `_composed_quote_reply` (PRICE + VEHICLE + LOCATION) at the four focus-based quote sites;
  - the four blocked-acceptance prompts declare their question intents;
  - `ComposedText` from the AI decision path skips the model-prose scrubs (it is not model prose).

### 2.4 No state from outbound prose (item D)

**`_register_canonical_vehicle_proposition`.**
- The vehicle now comes from **customer evidence** (this turn's inbound text, the same catalogue chain).
- The reply is only a trigger: it must make a **claim-shaped** statement of that same vehicle.
- A vehicle only the reply names arms nothing.
- "¿Te parece buena idea…?" and "uno de los dos autos" can neither arm `pending_fuzzy_catalog_key`
  nor append "¿Es un …?".

**AI-proposed time** (`M20` block). A time parsed from the AI reply is held in `preferred_time`
**only if it is one of the slots ScheduleService evaluated and showed** (`_evaluated_slot_times`). An
invented time is never stored.

**Residual (disclosed for the reviewer).** These two paths still *read* the delivered text:
- the AI location-question intent and the AI-proposed-time *selection* confirm against the words;
- in both, the state comes from canonical state (missing location / evaluated slots) and the
  customer's next inbound evidence, never from the words alone.

Neither the base test that exercised the time path (`test_m20_6d2…test_rc36`) nor 20 sibling `m20_6d2`
tests pass on **base** f16b1f3 (fixture/date dependent; identical on candidate). So the time rule is
evidenced structurally plus by the helper's unit test, not by RC36.

### 2.5 Question recording (item E)

`_record_delivered_questions` records an outstanding question when **either**:
- the structural clarification proposition was included; **or**
- the delivered primary's validated contract declares the intent with `RECORD_OUTSTANDING_QUESTION`,
  and the plan included that primary (`_primary_declares`).

Words alone no longer record anything. An undeclared location question logs
`outstanding_question_not_declared`.

### 2.6 R5-F1 hardening

| Item | Correction | Test |
|---|---|---|
| R5F1RV-02 | `_template_exemptions` is total. Refusals name `type(...).__name__` (no `__repr__`). The skipped set must be an exact `frozenset` of known claim ids. Any registry defect is a refusal inside a `try`, never an exception | `h01`, `h02`, `h03` (a corrupted registry cannot reach raw-text delivery) |
| R5F1RV-03 | `_exact_text_equal`: exact accepted types and `str.__eq__` | `h04` (a lying `str` subclass on a direct call is refused) |
| R5F1RV-04 | Every validator and envelope output is an exact `str` | `h05` (all paths, including the tagged constants and empty values) |

Kept unchanged:
- the approved motorcycle templates;
- only VEHICLE is skipped for `NO_VEHICLE_CLAIM` (`h06`: price, location, availability, booking and acceptance all still fire);
- AI text cannot acquire template or composed trust (`a04`, `a06`).

---

## 3. Source verification

### 3.1 New suites

- **`test_r6_structured_response_contract.py`** has 52 tests in groups A–K: provenance and forging,
  structured claims, vehicle / location / availability shapes, fallbacks, question intents, the
  RV-02/03/04 hardening, no state from prose, question recording and structure.
- **`test_r6_full_engine_prose_corpus.py`** has 6 tests and about 1,040 subtests. Every turn takes the
  real route (`handle_message` → CE → reply admission → `_send_text_to_wa` → envelope →
  `plan.compose` → OutboundSafetyGate → Meta recorder). The R1/R2/R3 interlock is armed (asserted per case).
  - Worlds: `new` (no vehicle, no place); `corolla` (Toyota Corolla 2018 focus, no place);
    `located` (no vehicle, CABA/Palermo).
  - Postures: direct webhook false/true × Smart Booking false/true.
  - Database: SQLite, and PostgreSQL via `R6_PG_URL`.

| Corpus | Cases | Expectation |
|---|---|---|
| R5F1RV-01 examples (review §6.2, plus Flores non-locative, Norte conditional, "punto por punto", hours, disponibilidad) | 17 replies | verbatim; no fallback, outstanding question, pending vehicle, candidate or revision change |
| A genuine location question | 1 reply | verbatim; an `INSPECTION_LOCATION` outstanding question (REPLY_MODEL) only in the two worlds lacking a place |
| **The reviewer's 40 prompt-derived answers (verbatim)** | 39 + 1 | 39 verbatim. The one claim-shaped assertion "La revisión del auto la hacemos **en Flores** sin problema" is refused in these worlds (no canonical Flores) into the plan-appropriate fallback. This is the intended reading and is recorded, not hidden |
| RideCheck-style extra answers | 10 | verbatim |
| Real contradictions: wrong vehicle, wrong location, invented slot, wrong price, false booking, false acceptance | 6 × 12 | the contradicting content is never delivered; refused by the named claim class; the fallback asks only for what is missing (`corolla` is never asked for a vehicle); its question is recorded only where the place is missing |
| A supported claim | 2 | kept ("el auto está en Palermo" with Palermo canonical; "Toyota Corolla" with the Corolla focus) |

One contradiction cell is blocked **before** the validator: wrong price in the `corolla` world. The
existing `_scrub_invented_price` (BUG-2) replaces the amount with its own missing-zone request, so
the amount is still never delivered; the test asserts this explicitly.

### 3.2 R5-F1 still fixed

- `test_r5_f1_motorcycle_handoff_production_path.py` passes 26/26 on SQLite and **26/26 on PG**
  (outstanding questions 0).
- The R6 rehearsal W2 handoff passes (§8).

### 3.3 Red control: f16b1f3 versus the candidate, on the same full-engine `run_case`

`probe/red_corpus.py` was run on `base_red6` (f16b1f3 plus the corpus test file and fixture) and on
the candidate. It covers 57 ordinary answers × 3 worlds × (SB off/direct off, SB on/direct on) = 342
ordinary turns, plus 36 contradiction turns.

| Tree | Ordinary turns altered | of which generic vehicle/zone fallback + outstanding question | pending fuzzy vehicle armed |
|---|---|---|---|
| f16b1f3 (SQLite) | **170 / 342** | 144 | 8 |
| Candidate (SQLite) | **6 / 342** (the "en Flores" assertion, 3 worlds × 2) | 2 (`new` world, where the vehicle and the place are both genuinely missing) | **0** |
| Candidate (PostgreSQL 16.13) | identical. The output file is byte-identical to SQLite (`8829464247fd6e96`) | | |

---

## 4. R6 kit corrections

The R6 kit is a fresh copy of R5, re-targeted. R5 is unchanged: CHECKSUMS `3c228f71…13ac2bd`
verifies, and no file in it is newer than its creation.

- `docs/R6_DIFF_vs_R5_kit.patch`: 57 files, 4,073 lines.
- New: `scripts/r6_time.sh`, `scripts/kit_r6.sh`, `scripts/FINALIZE_PINS.sh`,
  `scripts/VERIFY_PIN_FINALIZATION.sh`, `config/pins.conf`, `pins/*`, `tests/test_kit_r6.sh`,
  `tests/rehearse_r6.sh`, `tests/mutate_r6.py`, `docs/FINAL_CONTROLLED_RELEASE_RUNBOOK.md`,
  `docs/COMBINED_HANDSET_WILD_RUNBOOK.md`, `docs/AUTHORIZATION_TEXT_R6.md`.
- Moved: `tests/rehearse_r5.sh` → `tests/superseded/rehearse_r5.R5.sh`.

### 4.1 R5RV-01: local wall clock

`scripts/r6_time.sh` is the one construction:
- `r6_local_now_sql [NOW]` → `(NOW AT TIME ZONE '<SCHEDULE_TZ>')`;
- `r6_future_occupying_pred [ALIAS.]`;
- `r6_future_occupying_count_sql`.

A zone name containing anything but `[A-Za-z0-9_/+-]` falls back to the business zone, so it cannot
inject SQL. `SCHEDULE_TZ=America/Argentina/Buenos_Aires` is in `window.conf`. ENABLE and preflight
refuse unless it equals the running application's `schedule.BUSINESS_TIMEZONE`, read by a fresh
parser process in the backend.

| Site | Now |
|---|---|
| 1 ENABLE precondition (`ext_watermarks future_occupying`, read by `wm`) | helper (its heredoc unquoted only for the helper; K69 proves nothing else expands) |
| 2 external breach counter (`ext_counters future_occupying`) | helper |
| 3 preflight | helper |
| 4 cleanup (`cancel_test_bookings.sh`) | `r6_future_occupying_pred b.` |
| 5 rehearsal reset verification (RR6/RR8) | helper |

No `slot_end > now()` remains in any script. In active tests it appears only as the rehearsal's one
labelled red control and as mutants (K69).

**K68** ran on a disposable PostgreSQL 16, `--network none`, `TimeZone=Etc/UTC`. Four pinned clocks:
15:00, 21:00 (the UTC date has already rolled), 23:59 (just before local midnight) and 00:30 (the
local date has rolled). For each:
- R6 counts exactly the bookings at +1 h, +2 h (whose UTC reading is in the past) and +3 h 01;
- it never counts −1 min, now, DETACHED or non-occupying rows;
- the R5 predicate counts only +3 h 01 (the defect, measured).

With the real clock, local now+1 h is counted and −1 min is not; the aliased cleanup form agrees.

**Rehearsal RR13** used the real schema under `TimeZone=Etc/UTC`. A synthetic booking ending at local
now+1 h is missed by the R5 predicate and counted by the helper, by the kit's own ENABLE watermark,
by the breach counter and by the preflight predicate.

### 4.2 R5RV-02

`r6_manifest_pyc_count` prints exactly one integer, or `MISSING` (a failure). It counts `.pyc`, `.pyo`
and `__pycache__` entries. The R5 suite's K61 now uses it. K70 proves:
- the sealed manifest gives 0;
- an injected `.pyc` entry and an injected `__pycache__` entry are each counted;
- an absent manifest is MISSING;
- K61 fails on an injected manifest.

### 4.3 R5RV-03

`r5_ledger_verify` (replaced in `kit_r6.sh`) reads **raw bytes** first. It refuses a CR byte (CR,
CRLF, CR-only), a missing final LF, an empty line, invalid UTF-8, and a CR in `LEDGER.head`.
K71 tests each case. Its red control shows the R5 verifier accepting a trailing CR.

### 4.4 R5RV-04

"fresh parser process reading the container environment" is now used for the second posture read
in STATUS (`FRESH-PARSER-PROCESS=`), kit_r4, preflight, WATCH, fake_docker, the R5 suite and
rehearse_r4. "compared in-process" (meaning *in memory*) is reworded too.

The R5 suite's K57 now scans scripts, docs, tests, README and config, any case, comments included:
**0** files. Archived `tests/superseded/` copies, historical `*.patch` files and `tests/results/` (R5
evidence) are deliberately unchanged.

---

## 5. Provisional pins (R6 pinning)

**`config/pins.conf`** is the only file finalization may change. While provisional:
- `PIN_STATE=PROVISIONAL`;
- `EXPECT_GIT_SHA=SOURCE_COMMIT=EXPECT_IMAGE=UNFINALIZED`.

Fixed at seal:

| Key | Value |
|---|---|
| `FINAL_TAG` | `ridecheck-crm-backend:minlaunch-final-r6` (every R6 overlay names it) |
| `CANDIDATE_BASE_COMMIT` | `f16b1f328f3fbf1050afdcec583d252d528c72bf` |
| `CANDIDATE_TREE_MANIFEST_SHA256` | `a86be9925381ff8abb496f5412c7087938cbbb699bd20e8204bc5095cd135442` (`pins/candidate_tree.manifest`, 676 files: the whole candidate tree outside `docs/Launch/`) |
| `CANDIDATE_CHANGED_PATHS_SHA256` | `5f54a8277f8fb5b08c31e25f18e32dcd706063740589cfdccc87da5f5c7566ba` (`pins/changed_paths.manifest`: the 10 paths, status and blob) |
| `CANDIDATE_APP_MANIFEST_SHA256` | `5358c28c50fd874d9fb73b5a4db3f75f36884c155ce8d1c6bd8bd6a82aed6a01` (`pins/candidate_app.manifest`, 137 files of `/app/app`) |

**Method check.** The same `/app/app` manifest computed inside the live f16b1f3 image equals the
base tree's `backend/app` (`d6e401c1…3747`). So an image built from the commit will reproduce
the candidate value.

**`EXPECT_OFF_CONFIG_HASH16=e489a4638c286909`.** This is the read-only `compose config --hash` of
the R6 OFF overlay over the live COMPOSE_FILES. The same computation on the R5 overlay reproduces the
live `7de743f7c57326d5`.

**Fail closed (`kit_r6.sh r6_pin_gate`).**
- It runs in ENABLE (after the R4 configuration check, before the lock, ledger or any docker call),
  in RESET_BETWEEN, and in `reset_testers.sh` (right after the private-evidence check, before any
  reset work, including read-only inventories).
- **Live mode requires all of:**
  - `PIN_STATE=FINAL`;
  - a `sha256:` image id;
  - a 40-hex commit that is not the base, with `SOURCE_COMMIT` equal to it;
  - `pins/FINAL_PIN_EVIDENCE.txt` with `verdict=PIN_FINALIZATION_VERIFIED`, naming exactly these
    pins and the candidate app manifest.
- **Rehearsal mode** requires `REHEARSAL_MOUNTED_CANDIDATE`, plus proof that the running `/app/app`
  is the pinned candidate.
- **KILL and WATCH are never gated.** But KILL still verifies the running image against
  `EXPECT_IMAGE`. Until finalization, **the R5 kit remains the live emergency instrument for the
  f16b1f3 backend**; the provisional R6 kit cannot verify it.

**Real-config negative** (`neg_realconfig.sh`). The sealed kit's own ENABLE `window-W2.conf`,
ENABLE `window-H.conf`, `RESET_BETWEEN reset-AB.conf execute` and
`reset_testers window-W1A.conf execute` were run with docker replaced by a recording shim that fails.
- All four exited 2 with `R6 pins: pins are 'PROVISIONAL'…`.
- **0 docker calls** were attempted. The kit is still sealed.
- They left refusal evidence in the R6 private runtime (`window-W2-1/`, `window-H-1/`, `reset-AB-1/`,
  `window-W1A-1/`: timeline and `enable/RESULT` files; no ledger, lock or watermark). The owner may
  archive these before the first real window.

**Finalization.** `scripts/FINALIZE_PINS.sh COMMIT IMAGE ANCHOR` verifies first and writes nothing on
any failure:
1. **The kit is the sealed provisional kit:** the anchor matches, every entry is OK, and pins.conf
   equals the sealed `pins/pins.provisional.conf`.
2. **The commit is the reviewed candidate:**
   - its only parent is the base;
   - its changed paths, with status and blob, equal the manifest;
   - its whole tree outside `docs/Launch/` equals the reviewed tree;
   - under `docs/Launch/` it only **adds** `.md` files (this closeout and the review audits are
     written after the seal).
3. **The image is that commit's:**
   - the tag resolves to it;
   - its labels name the commit and the head, and its `GIT_SHA` is the commit;
   - its `/app/app` is the reviewed app, computed in a `--network none` container of the image.

It then changes exactly the four keys, writes the evidence and regenerates CHECKSUMS, and finally
runs the read-only `VERIFY_PIN_FINALIZATION.sh ANCHOR`. That proves, against the sealed copies and
the out-of-band anchor, that nothing else changed. The deep pass re-checks the commit and the image.

K74 proves all of this with shimmed git and docker:
- the exact three-file difference and the exact four keys;
- 14 kinds of wrong commit or image are refused and write nothing;
- a `.md` addition under docs/Launch is accepted;
- after finalization, an edited script, budget, runbook, non-pin key or image pin, an added file,
  and a consistently re-sealed "provisional" manifest are all NOT VERIFIED. The last is caught only
  by the anchor.

**Provisional anchor** (sha256 of `pins/PROVISIONAL_CHECKSUMS.sha256` = of `CHECKSUMS.sha256`; quote
it in the authorisation texts):

> **`99c3e161fe394a81cc0b6477b1be413acaa52045604647a63ee970ec02c7d848`** (117 entries)

**R6 private runtime.** It holds the four R5 inputs, byte-identical. `PRIVATE_MANIFEST.sha256` has 4
entries and is `28a91981…c8b2`, identical to R5's because the inputs are identical.
`private_runtime.sh verify`: VERIFIED. 884 files, 8.0 MiB, including two rehearsal runs (§8).

---

## 6. Regression (serial, gated, `ridecheck-crm-test:l4.7b2`, `--network none`, read-only trees)

Base tree: `git archive f16b1f3`, manifest `7282f596…09b4` (equal to both earlier reviews).

| Run | Base | Candidate | Comparison by junit identity |
|---|---|---|---|
| Focused SQLite (67 files: the previous 65 plus the two R6 suites) | 18 F / 2148 P / 361 S (2527) | 18 F / 2263 P / 361 S (2642) | **NEW 0, GONE 0**, COMMON 18, changed 1 (the SB1 sentinel), +115, −0, skip changes 0 |
| Full SQLite `../tests` | 62 F + 9 E / 7489 P / 786 S (8346) | 62 F + 9 E / 7605 P / 786 S (8462) | **NEW 0, GONE 0**, COMMON 71, changed 1 (the SB1 sentinel), +116, −0, skip changes 0 |
| PostgreSQL 16.13, 16 affected suites (convergence P3 race/P3-R1/P4/P4-R1, PW1, G3-7P, G3-7R projection/R2/R3, minimum launch, W1F O1/unanswered, vehicle identity recovery/T1, P-1 recipient lock, M19 PG integration), each on a fresh clone of the head template, dropped after | 297 P / 129 S (426), 0 failing | 297 P / 129 S (426), 0 failing | **NEW 0, GONE 0, COMMON 0** |
| PG SB2B with its clean database | 46 P / 1 F / 1 S | 46 P / 1 F / 1 S | identical (the one pre-existing clean-DB case) |
| PG: R5-F1 production path; R6 corpus + contract | — | 26/26, OQ 0; 58 passed, 1,043 subtests | — |

The full-run comparison was first **NEW 1**. `test_g35f_13a` forbids `json.loads(` inside
`_process_text`, and the first version of the AI-proposed-time rule parsed the slot lists there.

The fix moved that parse into `_evaluated_slot_times`. After it:
- the candidate was re-sealed (only CE and the contract test changed);
- both candidate SQLite runs were repeated, and the numbers above are the repeat;
- the PostgreSQL stage was run after the fix.

PG skips: the vehicle-identity PG suites (75) and parts of P-1 skip identically on both sides
(MIG/CLEAN gates).

---

## 7. Existing assertions changed (each owner-mandated by this milestone)

| File | Change | Why |
|---|---|---|
| `test_l4_7d_response_validator.py::test_validation_failure_never_breaks_a_turn` | It asserted that the **raw text** is returned when validation raises. It now asserts that the turn completes with the safe no-question line, an exact `str`, and never the unvalidated words | Item "exception-driven raw-text delivery" must be impossible |
| `test_l4_7w5_g3_6_proposition_migration.py::test_g36_41` | The customer burst was a placeholder ("burst") while the **reply alone** armed Peugeot 2008. The burst now names the car ("Hola, para revisar un 2008 del 2014"); new `test_g36_41b` asserts that a reply-only vehicle arms nothing | Item D: state only from customer evidence or a declared claim |
| `test_r5_f1_template_validation_contract.py` (the reviewed R5-F1 suite) | `d14`: the tag is still read in exactly one place, now `response_validator._approved_template_of` (CE reads none). `d16`, `d17`, `e04`: refused text now expects the **plan-appropriate** fallback, and `d16` adds that the *untagged* handoff words now survive too (the class fix) | Items A/F; the boundary moved into the envelope |

No test was skipped, deleted or xfailed. No other existing test changed.

---

## 8. R6 kit verification

| Job | Result |
|---|---|
| R3 regression suite `tests/test_kit.sh` on R6 | **269 passed, 0 failed**. R6 lines: the copy list gains the two R6 modules, the synthetic kits copy `pins.conf`, the pin assertions read `pins.conf`, and the source order ends with `kit_r6.sh` |
| R5 suite `tests/test_kit_r5.sh` on R6 | **242 passed, 0 failed** (K61 via the helper; K57 widened; unit kits are rehearsal-mode with `PIN_STATE=FINAL`) |
| R6 suite `tests/test_kit_r6.sh` (K67–K75), after sealing | **112 passed, 0 failed** |
| Real-config negatives | 4/4 refused, 0 docker calls (§5) |
| Kit mutations `tests/mutate_r6.py` (pin gate × 11, R5RV-01 × 6, R5RV-02 × 2, R5RV-03 × 4, R5RV-04 × 1, FINALIZE × 6, VERIFY × 4) | **34/34 KILLED** |
| Source mutations (26, on the sealed candidate: dropped claims; forged provenance × 3; malformed declaration trusted; ambiguous prose → claim / state; restored post-validation reparsing; bypassed contradictions × 2; undeclared question recorded; AI location intent with the location known; raw-text delivery × 2; template policy leak × 2; tagged output; RV-03/RV-02; fallback always asks both; location substring / no frame; hours / conditional availability counted; AI time unchecked; a suppressed primary declaring; a composed declaration without a question) | **26/26 KILLED**, control 396 passed |
| **Isolated rehearsal** `tests/rehearse_r6.sh`, run `PRIVATE-R6/run-r6-20261111T172328Z` | **94 passed, 0 failed** |

**Rehearsal details.** It used its own `--internal` network, PostgreSQL restored from the private
snapshot (`TimeZone=Etc/UTC`) and a real uvicorn backend with its daemons. The backend ran the
f16b1f3 image with the **sealed candidate `backend/app` mounted read-only**; ENABLE's pin gate proved
the content manifest at every window. The overlays differ from the kit's only in their `image:` line
(RR0 proves this).

- **The order:** H → H2 SKIPPED → W1A → RESET_AB → W1B → RESET_BC → W2 → final KILL.
- **Every window** was CLOSED once with `criteria_not_met=0` and ACCEPTED.
- **RR10 W2:** contact Flow only; needs_human false → true; nothing booked or pending.
  - **The reply after the contact form IS the truthful handoff constant.**
  - **No outstanding question** on the handed-off thread.
  - **`W2-6-truthful-handoff-text = 1` MET**, and ACCEPT W2 succeeded.
- **RR11:** the ledger chain verifies; the order and one CLOSED per OPENED hold; the final posture
  is all eight false and dispatch False; no bytecode was written.
- **RR12:** live unchanged. **RR13:** R5RV-01 on the real schema.

A first rehearsal run (`run-r6-20261111T164932Z`) gave 93/1. The single FAIL was the rehearsal's own
W2-6 check using basic `grep` with `+`, while the criterion itself was MET and W2 was accepted. The
pattern was fixed (`grep -E`) and the final seal re-run: 94/0. Both runs are kept.

**Kit seal history.** The kit was re-sealed six times during verification:
1. the candidate re-pin after the `_process_text` fix;
2. R3 fixture lines;
3. `kit_r6.sh` made safe under `set -u`, a real bug found by K70;
4. R3 private-runtime and source-order lines, plus the private manifest;
5. `FINALIZE_PINS.sh` wrote its temporary manifest inside the kit, so the final manifest listed a
   vanished file — a real bug found by K74 — plus two K74/K75 test bugs;
6. isolating cases for six masked kit mutants, and the rehearsal pattern.

Every suite, mutation run and rehearsal reported above ran on the final seal `99c3e161…d848`.

---

## 9. Live isolation (read-only; before 14:25:28Z, after 17:40Z): `diff` before/after empty

| Item | Before = after |
|---|---|
| backend | `04e14e5770f9`, image `sha256:1b8ed611…8fc14`, started 2026-11-09T00:00:44Z, restarts 0, config hash `7de743f7c57326d5`, GIT_SHA f16b1f3 |
| postgres / n8n | `5246fd86d829` / `6ea80570b67b`, restarts 0; running containers 3; networks 5 |
| Eight switches | all literally `false`; `booking_dispatch_allowed=False`; allowlist sha16 `ad98b5c3…` |
| Schema | `20261104_vehicle_identity`; columns md5 `15e93aab…`, constraints md5 `4cbd6165…` |
| Fingerprint | **`fbae2abdc3371f9146f0ac51a2ffa427`** |
| Max ids | msg 6268, offers 5, notices 2, jobs 0, security 735, dedup 2529, threads 2077, leads 171, thread_revisions 9, booking_commands 10, outstanding questions 2, ai_events 244, evidence 22, candidates 147, states 2078, clarification 0, attempts 7, attention 0, revisions 82; pending outbound 0 |
| Files | compose `10d47105…`, `.env` `b7204ee2…`, beta `79afd94b…`, probe-off `28442e29…`, pin-candidate `fe13e1c3…`; 9 booking Flow files unchanged (v7.8 `2102a8ec…`, published v7.3 `274038ba…`) |
| Kits / runtimes | R3 `dd9686c6…`, R4 `d884015e…`, R5 `3c228f71…` verify, with no file newer; R3/R4/R5 private manifests verify; deployment-prep HEAD f16b1f3, tracked diff 0 |

**Live touches (all read-only):**
- `docker inspect`;
- fresh-process `docker exec` env/dispatch reads;
- `psql` with `default_transaction_read_only=on`;
- one `docker compose config --hash` render of the live compose files (read only, to derive the R6
  OFF hash);
- one `--network none` run of the live image to compute its `/app/app` manifest.

No outbound, booking, notice, recovery, outstanding-question or evidence activity was caused. All
disposable objects (`r6c-*`, `rcr6-*`, `r6k-pg-*`, `r6c-composeid-probe`) are gone: 0 containers,
0 networks.

---

## 10. Frozen sentinel (not repaired, as instructed)

`test_sb1_booking_representation::UnchangedContracts::test_sb1_20_frozen_files_unchanged` was
**red before this candidate**: on f16b1f3, three files had drifted (`claim_projection.py`,
`routes/flow_data_exchange.py`, `routes/whatsapp.py`).

- **The candidate adds `response_validator.py` to its message** (3 → 4 entries).
- That is its only change: red → red, and **not a new failing identity** (NEW 0 in both comparisons).
- **It is currently ineffective:** it detects nothing while red.
- Repair remains a separate hygiene item. The recommendation is to remove the four entries with
  authorisation notes, per the R5-F1 review §8.

---

## 11. Disclosures and owner/reviewer items

1. **New customer copy for owner approval:** `NO_QUESTION` fallback "Gracias por tu mensaje.
   Cualquier otra consulta, escribime por acá."
   - It is sent only when vehicle and location are both canonical and a reply is refused.
   - The other three fallbacks reuse existing approved words.
2. **The one locative corpus answer** ("…la hacemos en Flores sin problema") remains a claim needing
   canonical proof. This is a deliberate reading; the reviewer may rule otherwise.
3. **Residual text reads** (§2.4): AI location-question intent and AI-proposed-time selection confirm
   against the delivered words. State comes from canonical state and customer evidence. RC36 (the
   base time test) is red on f16b1f3 itself.
4. **Honda CB500 (R5F1RV-06)** is unchanged: the claim resolver is the car catalogue. Following the
   review's advice, motorcycle recognition was not added.
5. **The provisional R6 kit cannot be the live emergency instrument** until finalization (§5). The R5
   kit remains that instrument for f16b1f3.
6. **Refusal evidence in the R6 private runtime** from the real-config negatives (§5).
7. **Process deviations.**
   - Several light jobs (single R6 kit sections with fake docker or shims, and one never-started
     compose probe container) ran while a heavy job was running. No two heavy jobs overlapped.
   - Every heavy job started at ≥ 1600 MiB MemAvailable (gate 1536 MiB) and disk ≥ 24.9 GiB.
   - The lowest reading was 598 MiB, during the first full candidate run. The 500 MiB watchdog never
     fired (0 ABORT).
   - The first chain was stopped once by me on the NEW 1 finding (§6), and its disposable PG was
     removed by exact name.
   - One `rm` of the R5 `CHECKSUMS.sha256` copy inside the new kit was refused by the environment's
     deny rule; it was overwritten by the R6 seal instead.
8. **Not done here:** commit, push, final build, pin finalization, dark deployment, any window.
   W-3 OPEN; final WILD NOT RUN.

---

## 12. Next steps (each needs the owner)

1. The integrated independent review of the candidate and the R6 kit.
2. Then `docs/FINAL_CONTROLLED_RELEASE_RUNBOOK.md`: commit → push → build once
   (`minlaunch-final-r6`) → `FINALIZE_PINS.sh` → `VERIFY_PIN_FINALIZATION.sh 99c3e161…d848` →
   one dark deployment → post-deployment verification.
3. Then `docs/COMBINED_HANDSET_WILD_RUNBOOK.md`, window by window, each separately authorised
   (`docs/AUTHORIZATION_TEXT_R6.md`).

## 13. Evidence (sha256 prefix)

| Path (under `/opt/ridecheck-builds/`) | sha256 |
|---|---|
| `crm-final-convergence-r6-20261111T142504Z/focused.cmp2.txt` | `8dfb6bf84616e00b` |
| `…/full.cmp2.txt` | `4e7af83c1769ff2b` |
| `…/pg.cmp.txt` | `4b1aca204ae13eb5` |
| `…/pg_sb2b.cmp.txt` | `9353063ff91d4215` |
| `…/pgstage.txt` | the PG stage log |
| `…/red_corpus_base.jsonl` | `e91cd5ac821cf069` |
| `…/red_corpus_cand.jsonl` = `…/red_corpus_cand_pg.jsonl` | `8829464247fd6e96` |
| `…/kit_r3c.txt`, `kit_r5c.txt`, `kit_r6c.txt` | `0763ef925caf39d7`, `ab07ba505535c028`, `2109a9058d8b501c` |
| `…/kitmut3/MUTATION_RESULTS.txt` | `0579d08cc47d0014` |
| `…/mut/results_final.txt` (+ `mut/mutants.py`, `mut/final2/`) | `03deffede0df4b75` |
| `…/neg_realconfig_c.txt` | `d6b551e970e1f3e5` |
| `…/live/before.txt`, `live/after.txt`, `live/probe.sh` | `61e8e8b8dd97bc84`, `dad69dbca6af4686` |
| `…/memory.log` (67 lines) | `58d6b6fc5689d139` |
| `…/cand/` + `cand.manifest.full` (`38e6d8c3…bffc`); `base/` (`7282f596…09b4`); `base_red6/` | trees |
| `crm-minimum-launch-enablement-PRIVATE-R6-20261111T144848Z/run-r6-20261111T172328Z/rehearsal.log` | `cb82c5dd8ae260fc` |
| `crm-minimum-launch-enablement-kit-R6-20261111T144848Z/docs/R6_DIFF_vs_R5_kit.patch` | `dd79816d870ee455` |

**VERDICT: READY FOR INTEGRATED INDEPENDENT REVIEW.** STOP. Nothing was staged, committed, pushed,
built or deployed.
