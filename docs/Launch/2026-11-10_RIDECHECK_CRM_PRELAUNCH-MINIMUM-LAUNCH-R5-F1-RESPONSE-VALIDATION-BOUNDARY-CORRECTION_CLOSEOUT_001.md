PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-R5-F1-RESPONSE-VALIDATION-BOUNDARY-CORRECTION
# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — R5-F1 RESPONSE-VALIDATION BOUNDARY CORRECTION — CLOSEOUT 001

Date: 2026-11-10 (ART) / runs 2026-11-11T00:02–00:58Z
Branch: `fix/prelaunch-r5-f1-response-validation-boundary` (worktree `/opt/ridecheck-crm-r5-f1-response-validation`)
Base: `f16b1f328f3fbf1050afdcec583d252d528c72bf` (`origin/integration/prelaunch-minimum-launch-merge` = f16b1f3, verified with `git ls-remote` before starting; the LOCAL branch of that name is stale at 163b37e, so the worktree was created from the commit, not the local branch)
Authoritative input: `…FINAL-WILD-AND-HANDSET-VALIDATION-KIT-R2-CORRECTION-INDEPENDENT-REVIEW_AUDIT_001.md` §6 (R5-F1)

**VERDICT: READY FOR INDEPENDENT REVIEW**

Not committed, not staged, not pushed. No build, deploy, migration, flag change, Meta contact, message, or live write. No R3/R4/R5 kit or private runtime was modified. R5RV-01 is not touched (next, separate correction). **W-3 remains OPEN. The final WILD remains NOT RUN.**

---

## 1. Summary

| | |
|---|---|
| R5-F1 on the launch path | **FIXED.** On the real `handle_message` route → `ConversationEngine` → `_send_text_to_wa` → `_validate_outbound_text` → `OutboundSafetyGate`, a Honda CB500 contact-form submission now sends exactly *"A la brevedad uno de nuestros asesores se estará contactando con vos."*. needs_human is true. There is no vehicle/zone fallback, no outstanding question, and no booking, Flow, notice or recovery job. This holds on SQLite and on PostgreSQL 16.13 |
| Red control | The same production-path test on pristine f16b1f3: **12 failed / 13 passed**. The fallback was sent and an outstanding question opened, exactly as the audit measured |
| Boundary | Closed and typed. An `ApprovedTemplateId` enum identifies the template. A read-only contract registry holds the exact text and a `TemplateValidationPolicy`. Unknown ids, policies or texts **fail closed** to full validation. Only the VEHICLE comparison is skipped, and only for `NO_VEHICLE_CLAIM` |
| AI-composed prose | Unchanged. It carries no tag and is validated exactly as on f16b1f3. **The broader prose-matcher debt remains** (§8) |
| Full SQLite | base 71 failing / cand 71 failing. **NEW 0, GONE 0**, COMMON changed 1 (an already-failing frozen-file sentinel now also lists `response_validator.py`, §6.3). +57 cases, 0 removed |
| PostgreSQL | 12 affected files plus a corrected sb2b rerun and the production path on PG: **NEW 0, GONE 0, COMMON changed 0** |
| Mutations | **19 / 19 killed**, control green |
| Live isolation | Before and after are **identical** (§10) |

---

## 2. Pre-implementation map

### 2.1 Validator call sites

| Call | Where | Input |
|---|---|---|
| `validate_response(...)` | `conversation_engine.py:12148`, inside `_validate_outbound_text` (:12135). **The only production caller** | every CE text and Flow-button body |
| `_validate_outbound_text(text, …)` | `_send_text_to_wa` :16735 | 48 call sites (below) |
| `_validate_outbound_text(body_text, …)` | `_send_flow_button` :16871 | 5 call sites (Flow bodies, including `_MOTORCYCLE_CONTACT_INTRO` and the Booking Flow body) |

No other module calls the validator. These deterministic texts use their own `gate.attempt` and are **never validated**: `booking_acknowledgement.BOOKING_FLOW_RECEIPT_TEXT`, `booking_slot_recovery.RECOVERY_TEXT` / `RECOVERY_FLOW_BODY`, `buscando_followup`, `quote_followup`, `outstanding_question`, and the operator/manual paths.

### 2.2 `_send_text_to_wa` call sites, by text provenance

- **Owner-approved module constants (deterministic):** `BOOKING_HANDOFF_TEXT` (×2), `_CORRECTION_TARGET_CLARIFICATION`, `_FALLBACK_WARM_HANDOFF` (×6), `_COVERAGE_RESPONSE`, `_LOCALITY_ASK_AGAIN_REPLY`, `_FUZZY_ASK_VEHICLE_REPLY`, `_MOTORCYCLE_HANDOFF_REPLY` (:10299), `_MOTORCYCLE_ADVISOR_REPLY` (:10472), `_PHONE_CALL_HANDOFF_REPLY`, `_LOCATION_CONTRADICTION_CLARIFICATION`, `_AVAILABILITY_VS_HOURS_CLARIFICATION`.
- **Deterministic composed strings** (f-strings and templates at scheduling, quote and clarification sites): `msg`, `reply`, `question`, `fallback`, `_pq`, `_requery_reply`, `_numeric_model_question`, `f"{primary_msg} {fallback_msg}"`, `customer_msg`.
- **AI-composed:** the reply-model `reply` sites (for example :10118, :12747, :12805, :12971) and the boundary replies chosen from AI routing.
- **Empty primary** (producer-only turns): :5438, :5888.

### 2.3 Fallbacks when validation fails

- `response_validator._FALLBACK_REPLY` is sent when every sentence is dropped. It asks "…¿Me contás qué vehículo querés revisar y en qué zona está?". With R1/R2/R3 armed, `_record_delivered_questions` → `_reply_already_asks_location` then records an INSPECTION_LOCATION outstanding question. **This is the R5-F1 outstanding question.**
- Partial drops: the surviving sentences are re-joined with single spaces.
- A price mismatch is *rewritten*, not dropped.
- A validator exception makes `_validate_outbound_text` return the raw text (pre-existing fail-open, unchanged; §8 row 10).
- `_DEGRADED_RESPONSE_FALLBACK` covers a required-producer failure (plan level, not the validator).

### 2.4 Deterministic customer-facing constant inventory (offline, `probe/inventory.py`)

There are 29 customer-facing CE constants, plus `BOOKING_HANDOFF_TEXT`, plus 3 informational non-CE texts. Each was run under no focus, a car focus (Toyota Corolla) and a motorcycle focus (Honda CB500).

| Result on f16b1f3 | Constants |
|---|---|
| **Dropped → fallback (VEHICLE)** | `_MOTORCYCLE_ADVISOR_REPLY`, all three foci. This is R5-F1 |
| Whitespace only (`\n\n` → space) | `_COVERAGE_RESPONSE`, `_CANONICAL_LOCATION_ASK` (pre-existing, cosmetic) |
| Would be altered, but not validated by CE | `RECOVERY_FLOW_BODY` (AVAILABILITY + VEHICLE, "Elegí uno"), `RECOVERY_TEXT` (AVAILABILITY) |
| Unchanged | all others |

On the candidate, `_MOTORCYCLE_ADVISOR_REPLY` is unchanged under all three foci. Every other row is identical to base.

---

## 3. The correction

### 3.1 Design: provenance travels on the value

`response_validator.py`:

| Element | Line | Role |
|---|---|---|
| `TemplateValidationPolicy` (enum) | :130 | Closed set: `FULL_VALIDATION`, `NO_VEHICLE_CLAIM` |
| `_POLICY_SKIPPED_CLAIMS` (`MappingProxyType`) | :138 | Policy → claim classes it skips. Read with `.get()`, so an unmapped policy skips nothing |
| `ApprovedTemplateId` (enum) | :144 | `MOTORCYCLE_ADVISOR_REPLY`, `MOTORCYCLE_HANDOFF_REPLY` |
| `_APPROVED_TEMPLATE_CONTRACTS` (`MappingProxyType`) | :161 | id → frozen `ApprovedTemplateContract(template_id, text, policy, reason)`, holding the **exact owner text** |
| `ApprovedTemplateText(str)` | :178 | Immutable `str` that carries its `ApprovedTemplateId`. Every derivation (`+`, f-string, `%`, `format`, `format_map`, `strip`, `join`, slicing, `partition`, copy, pickle…) yields a plain `str` |
| `template_tag_of(text)` | :224 | Exact type only (a subclass yields `None`). Returns the tag unchecked |
| `_template_exemptions(template, text)` | :242 | Total and never raises. Grants a skip only if (a) the tag is an `ApprovedTemplateId`, (b) a contract with that `template_id` is registered, (c) the policy is a member that is present in the skip table, and (d) `text == contract.text`. Otherwise it records a `TEMPLATE_CONTRACT` refusal finding and skips nothing |
| `validate_response(…, *, approved_template=None)` | :461 | Keyword-only. Skips only the claim classes granted. **PRICE, LOCATION, AVAILABILITY, BOOKING and ACCEPTANCE still run on approved templates** |

`conversation_engine.py`:

- `_MOTORCYCLE_HANDOFF_REPLY` (:263) and `_MOTORCYCLE_ADVISOR_REPLY` (:280) are now `ApprovedTemplateText(ApprovedTemplateId.X, "<same text>")`. **The text is byte-identical.**
- `_validate_outbound_text` (:12151) passes `approved_template=template_tag_of(text)`. This is the only place the tag is read.
- Call sites, signatures and every test stub are unchanged.

### 3.2 Why this shape

- **Not a reword.** That was required, and the copy is an owner decision.
- **Not a word blacklist.** "Fiat Uno", "un Uno 2015", "Fiat Punto" and "Fiat Idea" are still claims (§5 C). Mutant M16, a global blacklist, is killed by 52 tests.
- **Not inferred from text.** A plain `str` with the exact words gets nothing (d01, d16). The policy is attached where the constant is defined and verified against the registry.
- **Why a value tag rather than a keyword argument.** About 100 existing stubs are `lambda ctx, txt: …`. A new keyword at the moto call sites would have forced edits to existing tests. The tag needs no call-site change, and it cannot survive composition, so composed and AI text can never inherit it.
- **`_MOTORCYCLE_HANDOFF_REPLY` is also registered.** It is the same handoff family (the no-Flow variant), asserts no vehicle, and passed on f16b1f3 too. Registering it costs nothing and keeps the family consistent. It is proven through the production route (`R5F1NoFlowMotorcycleHandoff`).
- **The post-validation vehicle producer** (`_register_canonical_vehicle_proposition`, :16666) also reparses the reply. It returns early when `needs_human` is true. Both motorcycle handoffs set `needs_human` *before* sending, so the producer never sees their words; the production test proves no "¿Es un Fiat Uno?" is appended. **Any future NO_VEHICLE_CLAIM registration must check the same thing** (§8 row 2).

### 3.3 Diff

```
backend/app/services/conversation_engine.py   +21  -4   sha256 4986010f6e4e4c5c…
backend/app/services/response_validator.py    +188 -9   sha256 f865cc123016a839…
tests/test_r5_f1_motorcycle_handoff_production_path.py  new, 272 lines  sha256 ed191b470e41a19e…
tests/test_r5_f1_template_validation_contract.py        new, 495 lines  sha256 234aed89b06c9d23…
```

No existing test file was modified. No assertion was weakened, skipped or xfailed.

---

## 4. Tests added

### 4.1 `tests/test_r5_f1_motorcycle_handoff_production_path.py` (A, F)

This file imports nothing introduced by the fix, so it is runnable on base as the red control.

**How it runs**
- Real route `app.api.conversation.handle_message` via `_sb2b_harness.run_turn`.
- Real `ConversationEngine`, `_send_text_to_wa`, validator and `OutboundSafetyGate`.
- Fresh SQLite database. Meta is a recorder, and any other egress raises.
- R1/R2/R3 interlock **armed**, asserted in test a06, so a question *would* be recorded.
- Client-data Flow configured. Operator WhatsApp off.

**What is asserted:** the route replied; Meta received exactly the approved text, once; the ledger holds a `CE_TEXT` row with that text, `sent`; no fallback wording anywhere; needs_human true on state and lead; the moto token is consumed; no outstanding question; 0 ThreadRevision, ledger, offer, notice, recovery job or outbound Flow; the MOTO Honda CB500 evidence is preserved.

**Variants**
- Smart Booking off.
- Smart Booking on.
- Honda CB500 as the **current focus** (the audit's "'fiat uno' differs from canonical 'honda cb500'" posture).
- No-Flow handoff (`_MOTORCYCLE_HANDOFF_REPLY`) from a customer text turn.

**Results**
- SQLite: 26 passed.
- **Base (red): 12 failed.** `a02`/`a03`/`a04`/`a06` failed in each of the 3 Flow variants: the fallback text was sent and 1 outstanding question opened.
- **PostgreSQL 16.13:** 26/26 passed. The PG ledger's outbound texts are only the two approved handoffs.

### 4.2 `tests/test_r5_f1_template_validation_contract.py` (B, C, D, E)

| Group | Proves |
|---|---|
| **B** | b01: the real handoff survives under every focus. b02 (**precondition**): each phrase *is* read as a vehicle by the generic resolver. b03: an approved no-claim contract containing each phrase survives under every focus. b04: the same phrases **without** a contract are still validated in full |
| **C** | c01: Fiat Uno, un Uno 2015, Fiat Punto, Fiat Idea and Toyota Corolla are still resolved. c02–c04: they are rejected against a moto focus, a foreign car focus and no focus, and a car focus keeps its own vehicle. c05: a **tagged** template whose words assert a vehicle is still rejected. **c06: Honda CB500 is NOT a reply-catalogue claim (pre-existing, §6.1)** |
| **D** | d01: plain text with the exact words → nothing. d02: strings, booleans, `1`, the policy enum, a look-alike enum and `object()` → refused. d03: tag on other words → refused. d04: subclass or forged tag → nothing. d05: contract text drift → refused. d06: unknown or unmapped policy → refused. d07: unregistered id, crossed contract or non-contract → refused. d08: PRICE/AVAILABILITY/BOOKING/ACCEPTANCE/LOCATION still run on an approved template. d09: 24 string operations drop the tag. d10: tag and registries immutable. d11: resolution is total. d12: `FULL_VALIDATION` skips nothing. d13 (AST over `app/`): `ApprovedTemplateText` is constructed only as module-level CE constants for registered ids. d14: the tag is read in exactly one place, and only CE passes `approved_template=`. d15: each id has one constant and the texts are equal. d16: the CE wrapper fully validates untagged words. d17: a refused tag through the CE wrapper → fallback, never passed through |
| **E** | e01: inventory completeness, by runtime discovery of every CE string constant. e02: classification equals what the constant carries. e03: every constant plus `BOOKING_HANDOFF_TEXT` (format templates rendered with Fiat Uno / Palermo) survives the **real** `_validate_outbound_text` under all three foci; the two `\n\n` constants are compared whitespace-normalised and named. e04: a vehicle-naming template stays under the comparison (a question survives; the same words as an assertion are rejected) |

Result: 55 tests, 215 subtests passed (with A/F in the same run).

---

## 5. Requirement trace

| Requirement | Evidence |
|---|---|
| 1–3 explicit, typed policy; motorcycle handoff declares no vehicle claim; skip only for it | §3.1; d13–d15; e02 |
| 4 all other validation active | d08; M02, M15 killed |
| 5 AI text gains no bypass | d01, d16; M10 killed |
| 6 no bypass from an arbitrary string or boolean | d02, d04; M08, M09 killed |
| 7 closed, allowlisted, not inferred from text | §3.1; d01; M10 |
| 8 unknown values fail closed | d06, d07, d11, d17; M01, M05, M18, M19 killed |
| 9 no global blacklist; real Fiat claims still detected | C c01–c05; M16 killed |
| A Honda CB500 → handoff unchanged, needs_human, no fallback, no outstanding question, no booking/Flow/notice/job | §4.1 (SQLite and PG) |
| B ordinary Spanish | B b01–b04 |
| C real claims | C c01–c05; **CB500: §6.1** |
| D bypass isolation | D d01–d17 |
| E inventory, three foci | E e01–e04; §2.4 |
| F production path through ConversationEngine | §4.1 |

---

## 6. Deviations and disclosures

**6.1 Requirement C, "Honda CB500 must still be detected as a vehicle claim": cannot be met by preservation.**
- `_reply_vehicle_resolver` is the car catalogue (`lookup_vehicle` → `extract_model_del_year` → `_contextual_numeric_model_lookup`), and it returns `None` for "Es una Honda CB500." **identically on f16b1f3** (measured).
- `_extract_motorcycle_make_model` is a customer-input extractor keyed on a motorcycle word, not a claim resolver.
- Teaching the reply resolver motorcycles is new vehicle recognition. It would make more prose rejectable, and it is outside this milestone's narrow boundary. It was therefore **not done**.
- What does hold: against a Honda CB500 canonical focus, every conflicting car claim is rejected (c02).
- c06 pins the current fact with a docstring that says it is expected to be replaced when this is fixed. **Reviewer decision requested.**

**6.2 Precondition test b02 asserts the class debt.**
It asserts that "uno por uno" etc. are read as Fiat vehicles. It exists only so that b03 cannot pass vacuously. A future class fix will need to update it.

**6.3 SB1 frozen-file sentinel.**
- `test_sb1_booking_representation::test_sb1_20_frozen_files_unchanged` already failed on base (3 drifted files).
- `response_validator.py` is on its `_FROZEN_AT_PARENT` list, so the candidate's message now lists 4 files. This is the one COMMON_changed in both SQLite comparisons.
- The pin was **not** updated, because that would change an existing assertion. Per CLAUDE.md, R5-F1 is new runtime evidence (the rehearsal) against the conversation gate, which justifies touching the validator. Updating the sentinel is the owner's call.

**6.4 Memory.**
- The gate refused the first focused start: MemAvailable was 1258 MiB, with an unrelated `npm install` running.
- After that, every heavy job started at ≥ 1607 MiB and ≥ 28 GiB disk, one at a time. The 500 MiB watchdog never fired.
- Lowest readings: full-cand 632 MiB, pg 888 MiB, focused-base 984 MiB.

**6.5 PostgreSQL harness limits.**
- Run 1 started `test_sb2b_option_b_pg` from the wrong working directory, giving 40 setup errors on both sides. It was rerun correctly (§7.3).
- Cases that need dedicated MIG/STAMP/CLEAN databases (`*_PG_MIG_URL`, `MINLAUNCH_PG_STAMP_URL`, `SB2B_PG_CLEAN_URL` as a migrated db) skipped or failed identically on both sides.
- `test_w1f_operator_notification_pg` was not run: it needs a provisioned notification floor, and the change does not touch notifications.

**6.6 Scope probe limits.** §8 rows 2–4 were measured at the validator or resolver level, not as full engine turns.

---

## 7. Regression verification

Image `ridecheck-crm-test:l4.7b2`. Trees are read-only, `--network none` for SQLite, serial.
- Base tree: `git archive f16b1f3`, manifest `7282f596…09b4`.
- Candidate tree: exact worktree files, manifest `22cefde9…dc42`; byte-equal to the worktree at the end.

### 7.1 Focused (SQLite)

65 files: validator/CE, motorcycle/quad/UTV, outstanding question, outbound safety and kill switch, L2 transport, Smart Booking, minimum launch, plus the 2 new files.

| side | failed | passed | skipped | cases |
|---|---|---|---|---|
| base | 18 | 2148 | 361 | 2527 |
| cand | 18 | 2205 | 361 | 2584 |

**NEW 0, GONE 0, COMMON 18**, changed 1 (§6.3). +57 added, 0 removed.

### 7.2 Full SQLite (`../tests`)

| side | failed | errors | passed | skipped | cases (junit) |
|---|---|---|---|---|---|
| base | 62 | 9 | 7489 | 786 | 8346 |
| cand | 62 | 9 | 7546 | 786 | 8403 |

**NEW 0, GONE 0, COMMON 71**, changed 1 (§6.3). +57 added, 0 removed, skip set unchanged. The base totals match the P-1 closeout's base counts (62 failed / 9 errors).

### 7.3 PostgreSQL

Disposable `postgres:16` (16.13) on tmpfs, `--internal` network with no route to live. Template brought to `20261104_vehicle_identity` by create_all on the 305a112 pre-schema → stamp → `alembic upgrade head`. Each file ran on a fresh clone.

- **Run 1, 12 files:** `prewild_pw1_bounded_runtime_pg`, `g3_7r_r3_cycle_reset_bound_pg`, `g3_7p_offer_authority_pg`, `sb2b_option_b_pg`, `minimum_launch_pg`, `w1f_o1_ce_sla_successor_pg`, `w1f_unanswered_alert_pg`, `convergence_p4_pg`, `convergence_p3_race_pg`, `convergence_p4_r1_pg`, `g3_7r_booking_projection_pg`, `g3_7r_r2_refusal_bounds_pg`. Base and cand: 332 cases, 270 passed, 40 failing, 22 skipped. **NEW 0, GONE 0, COMMON 40, changed 0.** The 40 are the sb2b harness artefact (§6.5).
- **Run 2, sb2b corrected:** base and cand 46 passed, 1 failed, 1 skipped. **NEW 0, GONE 0, COMMON 1** (`m_02_clean_install`: the clean database was provisioned empty).
- **Run 2, production path on PG:** 26/26.

### 7.4 Targeted mutations

Suites: the 2 new files, `test_m21_2_mf_motorcycle_flow`, `test_m21_2_mh_motorcycle_handoff`, `test_l4_7d_response_validator`. Control: 139 passed.

| Area | Mutants (all KILLED; failing tests) |
|---|---|
| Policy enforcement | M01 unknown policy skips VEHICLE (5); M02 NO_VEHICLE_CLAIM skips all (5); M03 FULL_VALIDATION skips VEHICLE (1) |
| Template registration | M04 exact-text check removed (10); M05 id cross-check removed (1); M06 contract text drifts from constant (20); M07 advisor sent as plain str (12) |
| Caller isolation | M08 subclass tag accepted (1); M09 string tag coerced to enum (2); M10 CE grants the id to every text (2); M11 copy/pickle keep tag (1); M12 `format()` keeps tag (1) |
| Vehicle-claim bypass boundary | M13 any tag skips vehicle (24); M14 vehicle skipped globally (49); M15 exemption breaks later checks (4); M16 global uno/punto/idea blacklist (52) |
| Fallback | M17 nothing survives → original sent (62); M18 refusal raises → CE passes raw text (28); M19 refusal grants exemption (28) |

**19/19 killed.**

---

## 8. Scope review: rendered prose reparsed into domain claims (not redesigned here)

| # | Component | Reachable at launch | Example false positives (measured) | Severity | Protected by the new boundary |
|---|---|---|---|---|---|
| 1 | `response_validator._check_vehicle` via `_reply_vehicle_resolver` (catalogue tokens that are Spanish words: uno, punto, idea, clase, serie, up, ka, gol, fox, polo, golf, fiesta, master, partner, symbol — audit list) | **Yes**: every AI-composed CE text and Flow body | "La revisión incluye el tren delantero, uno por uno." / "Buena idea, la revisión te da tranquilidad." / "Te esperamos a las 10 en punto para la revisión." / "Es para uno de los dos autos." → whole reply replaced by `_FALLBACK_REPLY`, which with R2 armed also opens an outstanding question | **MEDIUM** (customer sees the wrong question; an outstanding question opens) | **No**. Only the two registered motorcycle templates |
| 2 | `_register_canonical_vehicle_proposition` (:16666): post-validation `lookup_vehicle` chain over the reply; appends "Todavía no tengo confirmado el vehículo, así que te confirmo: ¿Es un X?" and **arms `pending_fuzzy_catalog_key`** | Yes: QUALIFYING, no candidate, no focus, not needs_human. Questions survive the validator | "¿Te parece buena idea revisarlo antes de comprar?" survives validation unchanged, then resolves to Fiat Idea → would append "¿Es un Fiat Idea?" and arm Fiat‖Idea (resolver and validator measured; full turn not run) | **MEDIUM** (writes canonical pending state from prose) | **No**. Registered motorcycle templates are safe only because needs_human is set before sending |
| 3 | `_check_location`: zone names matched as **substrings** of the normalised sentence, when an inspection-subject word is present | Yes (AI prose) | "Revisamos los pilares y la carrocería del auto." → Pilar; "…el chasis norteamericano." → Norte; sentence dropped | LOW–MEDIUM | No |
| 4 | `_check_availability`: any `H:MM` token is an availability claim without a ScheduleService evaluation | Yes (AI prose) | "Trabajamos de lunes a viernes de 9:00 a 18:00." → dropped → fallback (hours belong to the FAQ proposition) | LOW | No |
| 5 | `_record_delivered_questions` → `_reply_already_asks_location(text)`: reads our own delivered text to record a question | Yes, with R1/R2/R3 armed | Correct reading of what was sent; it produced the R5-F1 question only because the fallback really asked it | LOW | Indirectly (the handoff no longer becomes the fallback) |
| 6 | `_check_price` / `_check_booking` / `_check_acceptance` lexicons | Yes (AI prose) | Not measured beyond the existing L4.7D corpus | LOW / INFO | No |
| 7 | `booking_slot_recovery.RECOVERY_TEXT` / `RECOVERY_FLOW_BODY` | **Not via the validator** (the recovery daemon uses its own gate path) | Would be altered (AVAILABILITY; VEHICLE "Elegí uno") if ever routed through CE validation | LATENT | No. A contract would be the route if ever needed |
| 8 | `\n\n` re-join of `_COVERAGE_RESPONSE`, `_CANONICAL_LOCATION_ASK` | Yes | Paragraph break becomes a space | INFO (cosmetic, pre-existing) | n/a |
| 9 | Deterministic *composed* strings (`msg`/`reply` f-strings at scheduling and quote sites) | Yes | Not in the constant inventory; plain `str`, so fully validated exactly as on f16b1f3 | INFO (not measured) | No (unchanged by design) |
| 10 | `_validate_outbound_text` returns raw text on any validator exception | Yes | Pre-existing fail-open | INFO | The new code is total; d11/d17 and M18 prove a refusal can never take that path |

**Closeout distinction**

1. **R5-F1 is fixed for the launch path.** Both motorcycle handoff templates are protected by contract, proven through the production route on SQLite and PostgreSQL, and the inventory shows no other deterministic CE constant affected.
2. **Broader AI-composed prose matcher debt remains** (rows 1–4; rows 1–2 MEDIUM). This is the audit's "R5-F1-class" item. It is untouched here and needs its own vehicle-recognition and validator review (for example, the resolver should not accept a lone common-word model token as a claim when there is no make or vehicle context).
3. **Launch-gate status of that broader debt is undecided.** The broader rendered-prose re-interpretation problem remains reachable for AI-composed CE responses. This implementation does **not** classify it as accepted launch debt. Its launch-gate status must be decided by the independent reviewer before `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` can be enabled.

---

## 9. Launch-governance placement

- R5-F1 invalidates the conversation behaviour checked by the final-WILD **W2** window (the audit's `W2-6-truthful-handoff-text` NOT MET). Only that boundary was remediated.
- No other gate was reopened. `docs/launch/LAUNCH_TRUTH_ROADMAP.md` was not edited; this milestone did not ask for it.
- W-3 remains **OPEN**. The final WILD remains **NOT RUN**.
- Per the audit, the kit repin needs a new image built from a reviewed and committed version of this fix, together with the separate R5RV-01 kit correction.

---

## 10. Live isolation (read-only, before 2026-11-11T00:0xZ / after 00:58Z)

Instrument: scratchpad `live/probe.sh`. It uses `docker inspect`, a fresh-process `docker exec` env/dispatch read, and `psql` with `default_transaction_read_only=on`, running the kit's `db_fingerprint.sql` (copied, not executed from the kit). **`diff before after` is empty.**

| Item | Value (before = after) |
|---|---|
| backend | `04e14e5770f9`, image `1b8ed611…8fc14`, started 2026-11-09T00:00:44Z, restarts 0, config hash `7de743f7c57326d5`, GIT_SHA f16b1f3 |
| postgres | `5246fd86d829`, image `71e27bf6…`, started 2026-10-21T23:31:05Z, restarts 0 |
| n8n | `6ea80570b67b`, image `2d70fafe…`, started 2026-10-21T23:31:12Z, restarts 0 |
| Eight flags | all `false`: OUTBOUND, SMART_BOOKING_CE, OPERATOR_WHATSAPP_NOTIFY, CONVERSATION_ENGINE_DIRECT_WEBHOOK, EVIDENCE_ADMISSION, OUTSTANDING_QUESTION, LOCALITY_RESOLUTION_SAFETY, BOOKING_FLOW_DETAILS_PREFILL |
| Dispatch | `booking_dispatch_allowed=False` |
| Database | `crm_test` at `20261104_vehicle_identity`; fingerprint **`fbae2abdc3371f9146f0ac51a2ffa427`** (equal to the audit's) |
| Max ids | msg 6268, offers 5, notices 2, jobs 0, security 735, dedup 2529, threads 2077, leads 171, thread_revisions 9, booking_commands 10; pending outbound 0 |
| Compose / .env / beta / probe-off / pin-candidate | `10d47105…`, `b7204ee2…`, `79afd94b…`, `28442e29…`, `fe13e1c3…` |
| Flow files (`meta/flows/booking/*.json`, 9) | unchanged, including v7.8 `2102a8ec…` and published v7.3 `274038ba…` |
| Deployment-prep worktree | HEAD f16b1f3, 0 tracked changes (not edited) |
| R5 kit | `sha256sum -c` OK (not modified) |

No outbound, booking, notice, recovery or evidence activity was caused by this milestone. All max ids are unchanged. No disposable `r5f1-*` container or network remains.

---

## 11. Evidence locations

Harness: `/opt/ridecheck-builds/crm-r5-f1-response-validation-20261111T000201Z/` (0700)

| Path | Contents |
|---|---|
| `base/`, `cand/` | read-only trees, plus manifests |
| `base_red/` | base plus the production-path test, used for the red control |
| `red/red_on_base.txt` | red-control output |
| `probe/inventory.py`, `probe/scope.py`, `probe/scope.out` | §2.4 and §8 measurements |
| `focused.*`, `full.*`, `full.cmp.txt`, `junit/` | SQLite runs and comparisons |
| `pg.txt`, `pgfollow.txt`, `junit/pg_*`, `junit/pg2/` | PostgreSQL runs |
| `mut/mutants.py`, `mut/results.txt` | mutation definitions and results |
| `memory.log` | heavy-job gate log |
| `heavy.sh`, `waitrun.sh`, `sq.sh`, `pg.sh`, `pgrun.sh`, `pgall.sh`, `pgfollow.sh`, `chain.sh`, `cmp.py` | harness scripts |

Scratchpad `live/before.txt`, `live/after.txt`: live isolation.

---

## 12. Proposed commit (NOT staged — for the owner after independent review)

```
fix(conversation): approved templates declare their validation contract (R5-F1)

git add backend/app/services/response_validator.py \
        backend/app/services/conversation_engine.py \
        tests/test_r5_f1_motorcycle_handoff_production_path.py \
        tests/test_r5_f1_template_validation_contract.py \
        docs/Launch/2026-11-10_RIDECHECK_CRM_PRELAUNCH-MINIMUM-LAUNCH-R5-F1-RESPONSE-VALIDATION-BOUNDARY-CORRECTION_CLOSEOUT_001.md
```

---

## 13. Verdict

**READY FOR INDEPENDENT REVIEW**

The reviewer is asked to rule on §6.1 (Honda CB500 is not a reply-catalogue claim, pre-existing) and §6.3 (SB1 frozen-file sentinel).

W-3 remains OPEN. The final WILD remains NOT RUN. STOP.
