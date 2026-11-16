PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-FINAL-INDEPENDENT-GROUNDED-GENERATION-REVIEW

Date: 2026-11-14 (UTC). The review started at 20:02Z.
Reviewer role: final independent reviewer of the R6-C4B verified-generative-output candidate. Review only: no production code was changed, and no implementation milestone was created.
Candidate: `/opt/ridecheck-crm-r5-f1-response-validation` (branch `fix/prelaunch-r5-f1-response-validation-boundary`, HEAD `f16b1f3`)
Evidence (author): `Y=/opt/ridecheck-builds/crm-r6-c4b-verified-gen-20261114T032904Z`
C4A baseline: `X=/opt/ridecheck-builds/crm-r6-c4a-provenance-20261113T200937Z`
Reviewer scratch (not part of the candidate): `R=/tmp/claude-0/-root/78d129da-5e75-4e46-8d78-d42097d243d0/scratchpad`

**VERDICT: STOP FOR OWNER DECISION**

Three findings block. Two are architectural and one is missing evidence:
- **B-1.** An implicit booking confirmation on a thread that is not booked is authorised and delivered, and the tests now assert that it is.
- **B-2.** The real-provider evaluation was not run, so the contract's acceptance criteria are unmeasured.
- **B-3.** Defence in depth is not a general backstop. Every non-temporal claim, and several booking phrasings, depend on the verifier's recall alone.

---

## 0. Reviewer process disclosures (read first)

1. **Reviewer contamination of the sealed evidence copy.** One read-only import of `verified_generation` / `response_validator` from `Y/cand` left 3 bytecode files at 17:08:09 ART (20:08:09Z):
   - `Y/cand/backend/app/services/__pycache__/{__init__,response_validator,verified_generation}.cpython-312.pyc`
   - I tried to remove them, but the session permission policy **denied** it, so they are still there.
   - Impact:
     - The sealed source content is unchanged. The manifest over every non-`.pyc` file still equals `f62240b8…cfb7389b`. The tree pin and the app pin exclude `.pyc` by definition and still equal `a1735dbe…` and `79e784a9…`.
     - A raw `find -type f` manifest of `Y/cand` now gives `af8d5fe2…` because of these 3 files only.
   - **Owner action:** delete that `__pycache__` directory to restore the seal byte for byte.
   - The worktree itself was never touched. All later runs used a scratch copy with bytecode writes off.
2. **Provider key.** I tried to read the provider settings from the live runtime env file, with the key redacted. The session permission policy **denied** it. I did not attempt to obtain the key any other way. No other key was in my environment.
3. **Container runs.** The `docker run --network none` invocation of my independent engine-level suite (`R/rev/test_rev_c4b.py`) was **denied**. The host has no FastAPI, so I could not import the engine locally. As a result:
   - **Engine-level** claims below rest on (a) source reading and (b) the author's own tests, which pass in the sealed junit (`Y/junit/full_cand.xml`).
   - **Pure-module** checks (comparator, pipeline, provenance type, defence in depth `validate_envelope`) were run independently against a scratch copy of the sealed source.
   - `test_rev_c4b.py` is retained, **not run**.
4. Nothing was staged, committed, pushed, built, deployed, migrated, enabled or sent. No provider, WhatsApp, Meta, n8n or live database was contacted.

## A. Identity and evidence

| Check | Result |
|---|---|
| Branch / HEAD | `fix/prelaunch-r5-f1-response-validation-boundary` / `f16b1f328f3fbf1050afdcec583d252d528c72bf` ✔ |
| Staged index | 0 entries ✔ |
| Worktree vs sealed `Y/cand` | Identical, except one extra file: the author's closeout `…R6-C4B-VERIFIED-GENERATIVE-OUTPUT_CLOSEOUT_001.md` (sha256 `01f22a3d…`), written after the seal ✔ |
| Cand manifest (741) | `f62240b857179c1b9d5f9b222840fa4c6c500156fd5e3fd1d31200f6cfb7389b`, reproduced from both `Y/cand.manifest.full` and `Y/cand` (non-`.pyc`) ✔ |
| Tree pin (689, excl. docs/Launch) | `a1735dbe64470028faaa6ba8edeb501f085da762327b353da31cbadd251a1531`, reproduced from `Y/cand` **and** from the worktree ✔ |
| App pin (`backend/app`, 139) | `79e784a974266481c158e0271d2d72eee25eb9db0792a62c3886f272ed00db89`, reproduced from `Y/cand` **and** from the worktree ✔ |
| `response_validator.py` blob | `d4421ab2682423564d7caa044b272edf160573b3` = SB1 sentinel ✔ |
| C4A seal | `X/seal.txt` manifest `dd7eada8…`, recomputed `dd7eada8…`; tree `f1616dce…` ✔; app `fb2c8717…` ✔ (unchanged) |
| Changed vs C4A | 17 = 3 production + 13 test + 1 doc ✔ (see manifest below) |

**C4A → C4B manifest (independently recomputed by diffing `X/cand` against `Y/cand`):**
- **Production:**
  - `backend/app/services/verified_generation.py` (new)
  - `backend/app/services/response_validator.py`
  - `backend/app/services/conversation_engine.py`
- **Test:**
  - `tests/_c4b_model_fakes.py` (new)
  - `tests/test_r6_c4b_verified_generation.py` (new)
  - `tests/conftest.py`
  - `tests/test_l4_7w5_g3_5e2_attribution_and_binding.py`
  - `tests/test_l4_7w5_g3_7_reply_renderer_demotion.py`
  - `tests/test_l4_7w5_g3_7h_prerequisites.py`
  - `tests/test_r5_f1_template_validation_contract.py`
  - `tests/test_r6_c1_claim_frames_production_path.py`
  - `tests/test_r6_c2_day_date_production_path.py`
  - `tests/test_r6_c4a_transactional_provenance.py`
  - `tests/test_r6_full_engine_prose_corpus.py`
  - `tests/test_sb1_booking_representation.py`
  - `tests/test_wild04r_phase2_observability.py`
- **Doc:** the C4A closeout, added to the tree.

**Author results reconciled from retained evidence (not rerun):**

| Suite | Retained file | Matches closeout |
|---|---|---|
| Focused SQLite | `focused.cmp.txt`: base 2998/2620/17, cand 3045/2666/18, NEW 1 (`test_s01_02…`, calendar-dependent), GONE 0 | ✔ |
| Full SQLite | `full.cmp.txt`: NEW 4 (1 calendar-dependent + 3 stale expectations); `junit/r2_fix.xml` and `r2_fix_base.xml` have 127 entries each, 0 failures (= 92 tests + 35 subtests) | ✔ |
| PG | `pg.cmp.txt` 713/598/2 vs 713/598/2, NEW 0; `pg_sb2b.cmp.txt` 48/46/1 NEW 0; `pg_pp.cmp.txt` c1pp 72, c2pp 67, c3pp 68, c4pp 64, c4app 31→33, all NEW 0 | ✔ |
| Mutants | `mut/results.txt`: CONTROL 129 passed / 664 subtests; V01–V18 all KILLED | ✔ |

**A.5 Assertions weakened or inverted to make C4B pass: YES, one class.** It is not disclosed as such in the closeout.
- Most edits are legitimate re-expressions:
  - provenance AI_COMPOSED → VERIFIED_GENERATED;
  - "reply None, body held";
  - `UPSTREAM_GUARD` emptied, because the scrub call sites were removed.
- OBS-04 was **strengthened**: an `if … is not None` skip became an unconditional assert.
- **But in `tests/test_r6_c4a_transactional_provenance.py`:**
  - C4A asserted that the whole `HOSTILE = REVIEWER + NOVEL + CANONICAL` set is *discarded* on QUOTED.
  - `CANONICAL = ("Te esperamos el lunes 16 a las 10:00.",)` is annotated "before R6-C4A this was delivered verbatim on QUOTED". It is the hazard C4A was created to close.
  - C4B narrows the discard assertion to `REVIEWER + NOVEL`.
  - It adds `test_a_body_whose_claims_are_the_current_evaluation_is_delivered`, which asserts that this exact sentence **is delivered verbatim** as VERIFIED_GENERATED on a not-booked QUOTED thread.
- **In `tests/test_r6_c2_day_date_production_path.py::test_the_canonical_date_follows_a_new_evaluation`:**
  - `assert_ai_body_discarded(…"Te esperamos el lunes a las 10:00.")` became an assertion that it **is sent** (and likewise for "Te esperamos el jueves a las 10:00.").
- Both tests pass in the sealed junit. See B-1.

## B. Source-level architecture and send-path analysis

I read every changed production line in all three files: the `verified_generation.py` file in full (656 lines), and the diffs of `response_validator.py` (+31 lines) and `conversation_engine.py` (498 diff lines).

**Pipeline as built** (it matches closeout §1):
1. The interpreter (`_call_openai` → `_reply_decision`) returns a decision.
2. `_apply_transactional_boundary` sets `decision["reply"] = None` **unconditionally**, keeping the body only in `_discarded_ai_reply`.
3. The route label is chosen by `_vg_route_label`.
4. `_build_fact_ledger` builds the ledger.
5. `run_verified_generation` runs. It mints only after `compare(...).approved`.
6. `_send_generated_reply` runs defence in depth (`validate_envelope`, refused or edited → fallback).
7. The value goes to `_send_text_to_wa`, which applies the tripwire, `_validate_outbound_text`, `plan.compose`, then `OutboundSafetyGate`.

| # | Question | Finding |
|---|---|---|
| B1 | VerifiedGeneratedText constructible only by the factory? | **Partially.** `VerifiedGeneratedText(...)` raises, and pickle, copy and deepcopy raise. But `str.__new__(VerifiedGeneratedText, s)` plus `object.__setattr__(v, "_record", VerificationRecord(...))` produces a value for which `is_verified()` is **True** and `validate_envelope` labels it **VERIFIED_GENERATED** (reproduced: E5). Only structural test S01's text needles prevent this; an aliased spelling such as `_vg.VerifiedGeneratedText` would evade them. No production code does this today. It is an enforced convention, not "unforgeable". Non-blocking (N-2). |
| B2 | Can raw interpreter text, a rejected draft or a plain `str` reach VERIFIED_GENERATED? | No production path found. A plain `str` is always AI_COMPOSED (the type gate is `type(...) is`). The interpreter body is `None` past the boundary on every route. The commercial tail and the FAQ route take only a `ComposedText` from the decision, or route to `_send_generated_reply` or a deterministic renderer. The tripwire refuses any text that contains a discarded or rejected body, but only exact folded containment: a one-character edit evades it (E6). No code passes drafts, so this is informational. |
| B3 | Ledger only from canonical sources? | Vehicle, zone, price (PricingService via `_build_canonical_facts`), date and slots (ScheduleService state), hours (`business_hours_for_weekday`), and payment and scope (engine constants mirroring `_FAQ_*`). **Caveat:** BOOKING_STATUS=`NOT_BOOKED` and HUMAN_HANDOFF_STATUS=`NOT_HANDED_OFF` are **hard-coded constants** labelled `BOOKING_SERVICE` / `HANDOFF_STATE`. They are not read from state. They are sound only because BOOKED, FLOW_PENDING and `needs_human` are routed away before generation, and the FAQ path does not check `needs_human` (N-3). |
| B4 | Reply model never a ledger source? | ✔ `FactSource` has no reply-model member. `_build_fact_ledger` reads no decision fields. |
| B5 | Verifier isolation | ✔ `verifier_messages(message, business_date)` sends only the system prompt, the schema, the date, the weekday and the message. U07 asserts the exact key set, which would also catch customer text leaking. **But** the verifier is the **same model** as the generator: `openai_verifier_model` is **not a `Settings` field**, so `getattr` always returns None and falls back to `openai_chat_model` (default `gpt-4o-mini`). Independence is of context only, not of model. N-1. |
| B6 | Three-way agreement | ✔ `compare` requires all of the following: every verifier claim is permitted by the ledger and declared; every declaration is found by the verifier and bound by `fact_ref` to a ledger fact of the same kind and value; every required fact is found; nothing is uncertain; `contains_business_claim` is consistent. |
| B7 | Unknown, uncertain, undeclared, unsupported, forbidden and missing-required claims reject | ✔ For **claims the verifier emits**. An unknown kind gives a MalformedOutput fallback; a non-normalising value gives UNCERTAIN. **A claim the verifier does not emit is approved.** See §C and B-3. |
| B8 | No prose parsed to authorise | ✔ The comparator reads normalised values only. The C4 parser runs only after minting, and can only refuse. |
| B9 | No prose writes state | ✔ by reading: no state writer appears in `_send_generated_reply`, `_build_fact_ledger` or the provider seams. The author's P05/V09 cover it; not independently rerun. |
| B10 | Failure paths end in the fallback without leaking a draft | ✔ by reading. Every non-approved outcome calls `fallback()`. Rejected drafts go to `_vg_rejected_texts`. The fallbacks are `_send_transactional_reply`, `VerificationFallbackText` (validator contract words) or the composed quote. A fallback never calls `_send_generated_reply`, so there is no recursion. |
| B11 | All customer-facing send paths | Engine: 48 `_send_text_to_wa` call sites, all passing through tripwire → validator → plan → gate. Generated text enters only at `_send_generated_reply`, which has 3 callers (first quote, commercial tail, FAQ). Non-engine senders: `api/whatsapp.py:617,902` and `ui/whatsapp_ui.py:2537` are manual-CRM / system-notification sends (operator or n8n text, not generated here); `quote_followup` / `buscando_followup` are constants. **Outside the engine there is no provenance gate.** That is consistent with scope, but it is a residual if any upstream (e.g. n8n) produces LLM text (R-6). |
| B12 | BOOKED / FLOW_PENDING deterministic | ✔ `_vg_route_label` returns None for them, and so does `state.needs_human` on the commercial tail. V16 killed. |

## C. Claim-schema completeness (independent probe)

**Method** (`R/rev/schema_probe.py`, output `R/out/schema_probe.json`, sha256 `94547d02…`):
- I built 37 synthetic Argentine-Spanish messages.
- The world is a QUOTED thread that is not booked. Its current evaluation is Mon 16 Nov at 10:00, 11:00, 15:00 or 15:30; the vehicle is a Toyota Corolla; the zone is Palermo/CABA; the quote is 140000; the business date is Wed 11 Nov 12:00 ART.
- The ledger mirrors `_build_fact_ledger` for QUOTATION.
- Each message is run through the **real** `run_verified_generation` → `compare`, then the real, unchanged `validate_envelope` (defence in depth), under two verifier postures:
  - **F (schema-faithful):** the best a perfect verifier can emit within the closed schema, with "uncertain" wherever no kind or value represents the claim.
  - **O (omission):** the verifier reports nothing.
- An honest generator declares what it wrote.

**Caveat:** the `CanonicalFacts` were built by hand, not by `_build_canonical_facts`, and no `vehicle_resolver` was supplied, so the V-row defence-in-depth results are indicative only. The B1 result matches the author's own engine-level test.

| ID | Category | Wrong? | F: comparator / DiD → delivered | O: comparator / DiD → delivered |
|---|---|---|---|---|
| P1 | exact price | no | APP / PASS → **yes** | APP / PASS → yes |
| P2 | "unos 140 mil, más o menos" | mild | APP / PASS → **yes** | APP / PASS → yes |
| P3 | "desde $140.000" | mild | APP / PASS → **yes** | APP / PASS → yes |
| P4 | 10% cash discount | yes | REJ → no | APP / PASS → **yes** |
| P5 | "140 lucas, más el traslado" | yes | REJ → no | APP / PASS → **yes** |
| P6 | credit card in instalments | yes | REJ → no | APP / PASS → **yes** |
| P7 | "con débito no trabajamos" | no | APP / PASS → yes | APP / PASS → yes |
| D1 | Mon 16 at 10/11 offered | no | APP / PASS → yes | APP / PASS → yes |
| D2 | "mañana a las 10" (Thu 12) | yes | REJ → no | APP / **REF** → no |
| D3 | range "entre las 10 y las 12" | yes | REJ → no | APP / REF → no |
| D4 | "el lunes 16 a la tarde" | yes | REJ → no | APP / PASS → **yes** |
| D5 | **"El lunes 16 a las 10:00 ya no tenemos lugar."** (negation) | **yes** | **APP / PASS → yes** | APP / PASS → **yes** |
| D6 | "lugar toda la semana" | yes | REJ → no | APP / PASS → **yes** |
| B1 | **"Te esperamos el lunes 16 a las 10:00."** | **yes** | **APP / PASS → yes** | APP / PASS → **yes** |
| B2 | **"Nos vemos el lunes 16 a las 10:00 entonces."** | **yes** | **APP / PASS → yes** | APP / PASS → **yes** |
| B3 | "Te guardo el lugar… hasta mañana" | yes | REJ → no | APP / REF → no |
| B4 | **"Listo, quedó reservado el lunes 16 a las 10:00."** | yes | REJ → no | APP / **PASS → yes** |
| B5 | cancellation policy | yes | REJ → no | APP / PASS → **yes** |
| B6 | conditional offer "si te sirve… avisame" | no | APP / PASS → yes | APP / PASS → yes |
| V1 | "Corolla **2019**" (canonical 2018) | yes | **APP / PASS → yes** | APP / PASS → yes |
| V2 | "tu Corolla" | no | APP / PASS → yes | APP / PASS → yes |
| L1 | "Palermo Soho" | no | REJ → no (naturalness cost) | APP / PASS → yes |
| L2 | "todo GBA sin recargo" | yes | REJ → no | APP / PASS → **yes** |
| L3 | zone-sur surcharge | yes | REJ → no | APP / PASS → **yes** |
| S1 | 6-month warranty | yes | REJ → no | APP / PASS → **yes** |
| S2 | "más de 250 puntos" | yes | REJ → no | APP / PASS → **yes** |
| S3 | report same day, photos, scan | yes | REJ → no | APP / PASS → **yes** |
| S4 | duration "una hora" | yes | REJ → no | APP / PASS → **yes** |
| S5 | "más baratos que cualquier mecánico" | yes | REJ → no | APP / PASS → **yes** |
| H1 | Mondays 13–18 | no | APP / PASS → yes | APP / PASS → yes |
| H2 | "todos los días de 9 a 18" | yes | REJ → no | APP / PASS → **yes** |
| H3 | "los domingos también" | yes | REJ → no | APP / PASS → **yes** |
| M1 | valid Mon 16 10:00 + invalid Tue 17 9:00 | yes | REJ → no | APP / REF → no |
| M2 | valid price + "podés pagar con débito" | yes | REJ → no | APP / PASS → **yes** |
| A1 | "140 lucas… del Corolla" | no | APP / PASS → yes | APP / PASS → yes |
| A2 | misspelt "el lune 16… alas 11" | no | APP / **REF** → no (false positive) | APP / REF → no |
| N1 | "Ahora te llama un asesor" | yes | REJ → no | APP / PASS → **yes** |

Additional omission checks (`R/rev/did_extra.py`):

| Message | Delivered? |
|---|---|
| "$99.000" | refused by defence in depth ✔ |
| "Tu turno está confirmado…" | refused ✔ |
| "Tenés turno confirmado…" | refused ✔ |
| "quedó reservado el martes 17…" | refused ✔ (via the date) |
| "Listo, quedó reservado el lunes 16 a las 10:00." | **delivered** |
| "Ya te agendé, te esperamos." | **delivered** |
| "…tu Ford Fiesta sale $140.000." | **delivered** (no resolver in the probe; indicative only) |

**Conclusions:**
1. **The comparator is sound for every claim the schema can represent.** With a schema-faithful verifier, 22 of 28 wrong messages are refused.
2. **The schema has blind spots that no verifier can express, so no comparator can refuse them:**
   - **Polarity of availability.** AVAILABILITY_DATE and AVAILABLE_SLOT have no "unavailable" value. D5 states the opposite of the canonical evaluation and is approved.
   - **Implied appointment.** "Te esperamos / nos vemos el lunes 16 a las 10" fits the literal AVAILABILITY_DATE format ("a calendar date offered or said to be available"). The author's own "HONEST" verifier model (`HONEST[CANONICAL[0]] = [AVAILABILITY_DATE 2026-11-16, AVAILABLE_SLOT 10:00]`) classifies it that way. → B-1.
   - **Vehicle attributes beyond `brand|model`** (year, version, km).
   - **Conditions and approximations around a correct value** ("desde", "unos … más o menos"). These are mild.
   - **Only SERVICE_SCOPE and PAYMENT_METHOD have an `OTHER` catch-all.** Warranty, number of checks, duration, report timing and contents, cancellation and refund policy, coverage area and zone surcharges, holds, discounts and comparatives have **no kind at all**. They are refused only if the verifier chooses to report them as `SERVICE_SCOPE OTHER` or as uncertain.
3. **Defence in depth is temporal and price only.**
   - Under verifier omission, **24 of 28** wrong messages are delivered.
   - These include an explicit fabricated reservation ("quedó reservado el lunes 16 a las 10:00"), "Ya te agendé", unsupported payment methods, hours, warranty, coverage and handoff promises.
   - The `_scrub_scheduling_confirmation` and `_scrub_invented_price` call sites were removed from the tail (closeout §4).
   - So **for booking status, payment, hours, scope and handoff, the verifier's recall is the only control.** That recall is unmeasured (B-2). The closeout's §11.1 ("defence-in-depth stays mandatory" as the backstop) materially overstates the backstop.

## D. Bounded real-provider evaluation

**REAL-PROVIDER EVALUATION NOT RUN.**

- **Reason:** the only configured provider key is in the live runtime env file, and the session permission policy denied reading it (§0.2). No other sanctioned key was available. I did not substitute scripted evidence for provider behaviour.
- **What was prepared:** `R/rev/real_provider_eval.py` (sha256 `731c065b…`), not run.
  - It covers 24 synthetic turns (6 per route, 16 ordinary and 8 adversarial).
  - It uses the candidate's own prompts, pipeline and unchanged defence in depth.
  - It has no database, WhatsApp or Meta access, and no customer identifiers.
  - It records route, ledger, claims, comparator result, fallback reason, per-call latency and calls per turn.
  - Run with an owner-sanctioned key in the environment: `OPENAI_API_KEY=… python3 -B real_provider_eval.py <Y/cand-copy>/backend [model]`.

| Metric | Value |
|---|---|
| First-attempt approval | NOT MEASURED |
| Approval after regeneration | NOT MEASURED |
| Fallback rate | NOT MEASURED |
| Unsupported claims delivered | NOT MEASURED |
| Median / max latency | NOT MEASURED |
| Generator / verifier calls per turn | design bound: ≤ 2 / ≤ 2 (author P08, stubbed: 1/1 success, 2/2 regeneration or fallback) |

**This prevents acceptance.** Criteria D.1–D.5 (zero unsupported claims, zero fabricated confirmations, ≥ 80 % natural approval, every turn under 20 s) are about real-provider behaviour. §C shows that safety for most claim kinds rests *solely* on the verifier's recall, so this is essential evidence, not a formality.

**Latency note (N-4).** The 20 s `VerificationBudget.total_s` covers only the generator and verifier calls. The interpreter call (`_call_openai_inner`, `urlopen(timeout=30)`) runs **before** it and outside it. The worst-case turn is therefore about 30 s + 20 s plus defence in depth and send, not 20 s. Criterion D.5 ("every turn within 20 s") cannot hold by construction unless "turn" means the verification pipeline only.

## E. Focused adversarial checks (≤ 8)

| # | Target | Method | Result |
|---|---|---|---|
| E1 | Alternate sender bypass | Read every send call site (§B11) | No generated-text bypass. The FAQ generated path **does not check `state.needs_human`**, while the commercial tail does, and the ledger then asserts the constant `NOT_HANDED_OFF`. AI is suppressed at turn start when `needs_human` is set (`conversation_engine.py:3749`), so only a mid-turn handoff is exposed. Low (N-3). |
| E2 | Verifier sees declarations or customer text | U07 test review | Killed by U07's exact key-set assertion; V03 also killed (author). ✔ |
| E3 | Operational claim kind missing from the schema | §C probe | **Confirmed gap.** Eleven operational categories have no kind, and availability has no polarity (B-1, B-3). |
| E4 | Verifier returns only the valid half | §C M1, M2 | Temporal half (M1): defence in depth refuses ✔. Non-temporal half (M2, "podés pagar con débito"): **delivered**. |
| E5 | Plain string acquires verified provenance | `str.__new__` + `object.__setattr__` | `is_verified` is True and the envelope is VERIFIED_GENERATED. Only a text-needle structural test guards it (N-2). |
| E6 | Rejected-draft leakage | Tripwire containment on the folded draft | Verbatim or prefixed draft refused ✔. A one-comma or one-accent edit is **not** refused. No code path passes drafts, so this is informational. |
| E7 | State from prose | Source reading | ✔ No writer in the generation path (author V09 killed). |
| E8 | Fallback recursion / repeated calls | Source reading | ✔ `attempts = max(1, min(max_attempts, 2))`. A defence-in-depth refusal does not regenerate. No fallback calls `_send_generated_reply`. At most 2 + 2 provider calls after the interpreter. |

`R/rev/test_rev_c4b.py` would have run E1, B1, D5, K3 and M2 end to end on the engine in four postures. It was not run (§0.3).

## F. Product quality and naturalness

Only the author's **scripted** examples exist (closeout §5, `Y/probe2/style.out`). Read as customer messages, the ordinary ones:
- are warm and natural, use voseo, and have Argentine tone ("Dale, sin apuro", "pensalo tranquilo", "¡Buenísimo!");
- answer the question;
- expose no machinery.

Fallbacks are noticeably template-like:
- "Para poder avanzar necesito confirmar algunos datos. ¿En qué localidad o barrio está el auto?"
- "¿Qué día y horario te viene mejor para la revisión?"
- "Disculpá la demora, ya te ayudamos."

They are acceptable as fallbacks.

Two schema and defence-in-depth properties will cost naturalness with a real model:
- exact LOCATION string equality ("Palermo Soho" vs "palermo" is rejected);
- defence-in-depth false positives on misspellings (A2 refused).

**Real-provider naturalness and the fallback rate are unmeasured.** No naturalness acceptance can be given.

## G. Discrepancies from the author closeout

1. §2 / §13 "`VerifiedGeneratedText` is unforgeable". **Overstated.** It can be forged with `str.__new__` plus `object.__setattr__`; only grep-style structural tests prevent this (E5).
2. §4 "the verifier model setting is optional". **No such setting exists** in `Settings`. The verifier always uses the generator's model.
3. §4 lists the expectation edits as provenance and reply changes. **It does not disclose that** the C4A HOSTILE assertion was narrowed, and that two tests now assert *delivery* of "Te esperamos el lunes (16) a las 10:00." on a not-booked QUOTED thread (A.5, B-1).
4. §11.1 relies on defence in depth as the backstop for verifier misses. **Defence in depth does not cover** booking status (several phrasings), payment, hours, scope, handoff or coverage (§C).
5. §6 / §10 "total 20 s". **This is the verification pipeline only.** The interpreter's 30 s timeout is outside it (N-4).
6. §8 round-2 "92/92". The junit has 127 entries = 92 tests + 35 subtests. Consistent.

## H. Findings

**Blocking:**
- **B-1 — An implicit booking confirmation is authorised and delivered on a thread that is not booked, and the tests assert it.**
  - "Te esperamos el lunes 16 a las 10:00." is the exact body C4A classified as hostile.
  - It is approved by the comparator under the author's own honest-verifier model, passes defence in depth, and is delivered as VERIFIED_GENERATED. Evidence: the author's passing tests `T01_Quotation::test_a_body_whose_claims_are_the_current_evaluation_is_delivered` and `DD_StateConsistency::test_the_canonical_date_follows_a_new_evaluation`, and independent probe B1/B2.
  - A customer reads it as a confirmed appointment. This violates "zero fabricated booking confirmations" and reopens the C4A hazard.
  - The schema has no way to represent an implied appointment.
- **B-2 — Essential real-provider evidence is missing.**
  - None of the D acceptance criteria were measured: safety, ≥ 80 % approval, fallback rate, latency.
- **B-3 — For most claim kinds the verifier's recall is the only control.**
  - Defence in depth is temporal and price only, and the scrub call sites were removed.
  - Under omission, 24/28 wrong messages are delivered, including "quedó reservado el lunes 16 a las 10:00" and "Ya te agendé, te esperamos".
  - Availability polarity (D5) is unrepresentable even for a perfect verifier.
  - Together with B-2, this is an architectural disagreement with closeout §11.1.

**Non-blocking residuals:**
- **N-1** The verifier is the same model as the generator (context-independent, not model-independent). The setting is missing.
- **N-2** The provenance type can be forged in-process; only a text-needle test guards it.
- **N-3** The FAQ generated path does not check `needs_human`. BOOKING_STATUS and HANDOFF are constants labelled as service sources.
- **N-4** The latency budget excludes the interpreter call. The worst case is about 50 s or more per turn.
- **N-5** The tripwire uses exact containment and trivial edits evade it (no current path).
- **N-6** Vehicle year and version are unrepresentable; LOCATION strict equality costs naturalness.
- **N-7** Defence-in-depth false positives on colloquial misspellings push turns to the fallback.
- **N-8** The generator sees customer text, so prompt injection is possible. It is contained only to the extent the verifier extracts the injected claim.
- **R-6** Non-engine manual and system senders are outside the provenance gate (in scope as human or n8n text).
- **Carried over:** W-3 OPEN; the final WILD NOT RUN; the kit is not re-pinned to C4B (closeout §11.9).

## I. Verdict

The architecture separates the generator, verifier and comparator correctly. Provenance is type-gated at the validator, the interpreter body is discarded on every route, retries are bounded, BOOKED and FLOW_PENDING stay deterministic, and no state is derived from prose.

The contract is **not established**:
- the claim schema cannot represent implied bookings or unavailability;
- the author's tests now endorse delivering an implied confirmation;
- defence in depth does not backstop the non-temporal kinds;
- the real-provider behaviour, on which all of this depends, is unmeasured.

**STOP FOR OWNER DECISION.**

As instructed, I did not implement or propose a fix, and no C4C or C5 milestone was created.

Reviewer artefacts (scratch, not candidate):

| Artefact | sha256 |
|---|---|
| `R/rev/schema_probe.py` | `e6a1f626…` |
| `R/rev/did_extra.py` | `726f1879…` |
| `R/rev/real_provider_eval.py` (not run) | `731c065b…` |
| `R/rev/test_rev_c4b.py` (not run) | `db50da16…` |
| `R/out/schema_probe.json` | `94547d02…` |

Nothing was staged, committed, pushed, built, deployed, migrated, enabled or sent.

STOP
