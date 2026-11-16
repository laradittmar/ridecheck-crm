PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-LAUNCH-R5-F1-RESPONSE-VALIDATION-BOUNDARY-CORRECTION-INDEPENDENT-REVIEW
# RIDECHECK_CRM — PRELAUNCH-MINIMUM-LAUNCH — R5-F1 RESPONSE-VALIDATION BOUNDARY CORRECTION — INDEPENDENT REVIEW — AUDIT 001

Date: 2026-11-11 (runs 12:34Z–13:39Z)
Candidate: worktree `/opt/ridecheck-crm-r5-f1-response-validation`, branch `fix/prelaunch-r5-f1-response-validation-boundary`
Base = HEAD: `f16b1f328f3fbf1050afdcec583d252d528c72bf`
Closeout under review: `…R5-F1-RESPONSE-VALIDATION-BOUNDARY-CORRECTION_CLOSEOUT_001.md`, sha256 `66f337652a76e11a54d5f4b41a044a5b268ff1892b62ee853065ed29f817847a` (matches the pin; begins with the required three header lines)
Authoritative finding: `…FINAL-WILD-AND-HANDSET-VALIDATION-KIT-R2-CORRECTION-INDEPENDENT-REVIEW_AUDIT_001.md` (sha256 `fb9604d1dcefe0913b4e3cb649d6778836e7d45b3e8c0eafb15ffd1fad7a56ab`), §6 and §12

**VERDICT: CORRECTION REQUIRED**

The narrow R5-F1 correction is sound. It is independently fixed on the production path, the template boundary held every forging and leakage attempt that is reachable through CE, only VEHICLE is skipped, and regression is clean on SQLite and PostgreSQL. It should be kept.

The verdict is driven by the broader gate decision (§6), which the review was required to make. **Decision B: launch-blocking.** AI-composed CE prose is still reinterpreted as domain claims. The same full engine path sends the irrelevant vehicle/zone fallback in place of ordinary answers. It also writes an `INSPECTION_LOCATION` outstanding question or an invented `pending_fuzzy_catalog_key`. This happens in both webhook postures, with no protecting layer. Per this review's READY criteria, that requires a class-level correction before the conversation engine is enabled.

Nothing was staged, committed, pushed, built, deployed or migrated. No flag was changed, Meta was not contacted, no message was sent and nothing was written to live data. Candidate code and tests were not modified. The only file written in the worktree is this audit.

---

## 0. Summary

| Question | Result |
|---|---|
| 1 Scope and manifest | **PASS.** HEAD = merge-base = f16b1f3. Exactly 5 paths in both directions. Product and test bytes equal the implementer's sealed `cand/` tree (the doc-only correction changed nothing else). No secret, identity, bytecode or cache was found |
| 2 Architectural boundary | **HOLDS through CE.** 82 HELD, 11 NOTE, 1 BREACH (malformed-registry `__repr__`; LOW, R5F1RV-02). One direct-call NOTE (R5F1RV-03) is not reachable from CE |
| 3 Production path | **FIXED.** Reviewer journey (moto/quad/UTV × Smart Booking off/on × focus off/on) passes 12/12 on SQLite and 12/12 on PG. Pristine f16b1f3 fails 12/12 on both. The implementer's production-path suite gives 26/26 on PG and 12/26 failing on base |
| 4 Genuine claims | Fiat Uno, un Uno 2015, Fiat Punto, Fiat Idea and Toyota Corolla are still rejected exactly as on base. Honda CB500 is not a reply claim on either side. It is pre-existing, and I judge it **not** a launch blocker (R5F1RV-06) |
| 5 Template inventory | 208 strings behind 54 CE-referenced names, × 3 foci. Base→candidate changes **only** `_MOTORCYCLE_ADVISOR_REPLY` (FALLBACK→UNCHANGED). Only the two registered handoffs carry a tag. Composed strings are plain `str` |
| 6 Broader prose gate | **B — launch-blocking (R5F1RV-01, HIGH).** 7 of 13 plausible AI replies are replaced by the fallback and open an outstanding question. 2 more get "¿Es un Fiat …?" appended and arm a fuzzy vehicle. 13 of 40 replies drawn from the system prompt's own FAQ guidance are replaced. The result is identical on base, under direct webhook on and off, with Smart Booking on and off, and on SQLite and PG |
| 7 Fail-open | The new code adds no raise path for untagged (AI) text. For tagged text, only a corrupted immutable registry can raise, and the raw text CE then sends is the approved constant itself (R5F1RV-02) |
| 8 Frozen sentinel | **Existing failure correctly carried as launch debt** (§8) |
| 9 Regression / mutations | Focused NEW 0 / GONE 0. Full SQLite NEW 0 / GONE 0 (71 common). PG 439/439 = 439/439. 18 mutants: 17 killed by the implementer's suites, **18/18** killed with the reviewer probes. RV07 survives the suites (R5F1RV-04) |
| 10 Live isolation | Before = after (diff empty). Fingerprint `fbae2abdc3371f9146f0ac51a2ffa427` |

---

## 1. Scope and manifest

| Check | Evidence |
|---|---|
| Base | `git rev-parse HEAD` = branch = `merge-base HEAD f16b1f3` = `f16b1f328f3fbf1050afdcec583d252d528c72bf`. Staged: 0 |
| Manifest (worktree → expected) | ` M backend/app/services/conversation_engine.py`, ` M backend/app/services/response_validator.py`, `?? docs/Launch/2026-11-10_…CLOSEOUT_001.md`, `?? tests/test_r5_f1_motorcycle_handoff_production_path.py`, `?? tests/test_r5_f1_template_validation_contract.py`. `--ignored`: none |
| Manifest (expected → worktree) | all 5 present. `diff -rq` of a `git archive f16b1f3` tree against a worktree copy lists exactly these 5 |
| Diff size | CE +21/−4, validator +188/−9. New tests 272 + 495 lines |
| Existing tests | No tracked test file is modified (only 2 tracked paths changed, both under `backend/app/services`). The skip set is unchanged in every comparison (§9) |
| Doc-only correction changed no product/test byte | The 4 product/test files are byte-identical (`cmp`) to the implementer's sealed tree `/opt/ridecheck-builds/crm-r5-f1-response-validation-20261111T000201Z/cand/`, sealed before their test runs. `diff -rq --exclude=docs` against it is empty. The worktree sha256 values match the prefixes the closeout records |
| Secrets / identities | Over all added content (diff + 3 new files): Meta token, `sk-`, Bearer, `PASSWORD/SECRET/TOKEN=` literals, PEM, `549…` 13-digit numbers, the second tester's number, the masked-tester pattern, real WAMIDs and 15–16-digit ids: **0** each. Emails: only `@example.invalid`. The test wa_ids are generated at run time (`54911{…}`), not literals. Values were never printed |
| Generated evidence / cache / bytecode | `find` for `__pycache__`, `*.pyc`, `.pytest_cache`, junit, `.coverage`: 0. Untracked: exactly the 3 files |

Hashes (sha256):

| Path | base (f16b1f3 blob) | candidate |
|---|---|---|
| `backend/app/services/response_validator.py` | `73d26f52e42b062cdb4eb5d5c38e51cd0b77a05292c42830fa5207852c0d5032` | `f865cc123016a839dd587ae12b63ddac0ee747e2d07eb45c59d35cd1fac6d5db` |
| `backend/app/services/conversation_engine.py` | `6fb827ecee2d6e8e8ba40f090011e7d090dac82310e5c7dc1a1731cde7ca0e86` | `4986010f6e4e4c5c4afc5460c63949ad8a45cc4a0bd0b1bab104d08abbd808b9` |
| `tests/test_r5_f1_motorcycle_handoff_production_path.py` | — | `ed191b470e41a19e910b3028a9ebf16c2c7512b9bdeae53e0595c9932fc87da9` |
| `tests/test_r5_f1_template_validation_contract.py` | — | `234aed89b06c9d236b9a2ddc145941089bdd62433e41c6750970f7282e97bcb6` |
| closeout | — | `66f337652a76e11a54d5f4b41a044a5b268ff1892b62ee853065ed29f817847a` |

Review trees (null-safe tree manifests):
- base `7282f596a87e3e5076ca59ab66d10a761bc9f6b24becb662444b86ddb81709b4` (710 files). This equals the implementer's base manifest, which is independent agreement.
- cand `d4ac76a10f0de5ec7437fe3c7bd1b410708c4a38689b97a8c1540b50a78d242a` (713 files).
- base_red `399870d15a17ca42c2607b88f56766698a04c2c81e6f572ce541c63b1254e249` (base + the production-path test only).

---

## 2. Architectural boundary (implementation read; independent attempts)

**Reading of the implementation.**

- **`ApprovedTemplateId` and `TemplateValidationPolicy`** are `enum.Enum` types with members.
  - Python refuses to extend them (D1, D2: `TypeError`).
  - A look-alike enum with the same name and value is refused by `isinstance` (D3).
- **Policies.** `_POLICY_SKIPPED_CLAIMS` is read with `.get()` and only for members, so an unmapped policy skips nothing.
- **Registry.** `_APPROVED_TEMPLATE_CONTRACTS` is a `MappingProxyType` of frozen dataclasses (E1, E3).
- **`template_tag_of`.** Requires `type(text) is ApprovedTemplateText`, so a subclass gets nothing (A3).
- **`_template_exemptions`.** Grants an exemption only when all four hold:
  - (a) the tag is a member;
  - (b) the contract is registered with that same id;
  - (c) the policy is a mapped member;
  - (d) `text == contract.text`.
- **Claim isolation.** Only the claim classes listed for the policy are skipped, per call. The skip set is a local, never a global.
- **Output.** The validator's output is a fresh `str` (`" ".join(...)`), so the tag cannot travel into `plan.compose`, the gate, the ledger or later turns (C-validated-output-type).

**Independent attempts** (`probe/bypass.py`, none in the implementer's D group verbatim): 94 attempts. **82 HELD, 11 NOTE, 1 BREACH.**

| Group | Attempts | Result |
|---|---|---|
| A forging | tag built on evil text; `__dict__` write re-tagging an instance (bypasses `__setattr__`); subclass with exact text; a `str` subclass whose `__eq__`/`__ne__` lie, via CE | all HELD |
| A4b | the same lying subclass passed **directly** to `validate_response(..., approved_template=ID)` | NOTE: exemption granted on "Es un Fiat Uno.". `text != contract.text` defers to the subclass's `__ne__`. Not reachable: CE derives the id only from an exact-type `ApprovedTemplateText`, and no other caller exists (`grep` over `backend/app`). R5F1RV-03 |
| B drift | trailing space, NFD accents, zero-width space, NBSP, appended claim, prefixed claim, upper case | all refused and fully validated |
| C derivations (44) | capitalize/casefold/center/ljust/rjust/zfill/expandtabs/translate/replace(count=0)/removeprefix/removesuffix/strip(chars)/lstrip/rstrip/split(maxsplit=0)/encode-decode/`format()`/`format(t,'')`/`'{}'.format`/`'{!s}'`/`f'{t!s}'`/`%`-tuple/`string.Template`/`textwrap.dedent`/`fill`/`html.escape`/`re.sub` no-match/JSON/pickle p0, p2, p5/deepcopy-in-list/`str.__str__`/full `__getitem__`/iter-join… | tag dropped. **NOTE ×5**: `f"{t:<0}"`, `unicodedata.normalize('NFC', t)`, `max([t])`, list and dict round-trips return the **same object**. That is the same registered text, so the exemption is legitimate |
| D bad ids | `str`, `bool`, `int`, the policy enum, the enum class, `None`, `bytes`, `list` | refused, full validation, no raise |
| E registry | item assignment and frozen attribute assignment: HELD | NOTE: `gc.get_referents` reaches the underlying dict, and the module global can be rebound. Both need in-process code execution (CPython limit, R5F1RV-08) |
| F claim isolation | under a NO_VEHICLE_CLAIM contract, PRICE, AVAILABILITY, BOOKING, ACCEPTANCE and LOCATION (with the 211 production zone names) each still fire. There is no leak to the next call, and no cross-thread leak in 2 × 200 interleaved calls | all HELD |
| G totality | malformed ids (`object()`, an enum with a raising `__repr__`, NaN, an object with a raising `.value`); malformed registry entries (policy as `str`, text `None`, non-contract, crossed id) | HELD |
| G2-policy-badrepr | registry contract whose **policy** object has a raising `__repr__` | **BREACH**: `_refused(f"unknown policy {policy!r}…")` raises and CE's fail-open returns raw text. R5F1RV-02 |
| G4/G5 | policy table value `5` (non-iterable) | raises `TypeError` → CE returns the **raw approved text** (NOTE) |

**Answer to Q2.**

- The identity is closed. The policy set is closed and fails closed for every plausible malformation.
- The registry is immutable at the language level available to non-malicious code.
- Exact text is required. Arbitrary strings, subclasses, copies and derivations cannot obtain a policy through CE, and only the VEHICLE claim is skipped.
- AI and composed prose carry no tag (composed sites are f-strings, `.format()` or helper returns; §5), and the policy cannot leak into later text.
- The only construction sites are the two CE module constants (`grep ApprovedTemplateText(` over `backend/app`).

---

## 3. Production path

Route: `app.api.conversation.handle_message` → `ConversationEngine.handle` → `_send_text_to_wa` → `_validate_outbound_text` → `validate_response` → `plan.compose` → `OutboundSafetyGate.attempt` → Meta recorder.

The configuration has R1/R2/R3 armed (asserted per case), the client-data Flow configured and operator WhatsApp off. Any other egress raises.

**Reviewer journey** (`probe/journey.py`, independent of the implementer's test). Each case runs 2 real turns:
1. customer text naming a moto/quad/UTV → contact Flow;
2. the Flow's `nfm_reply` → handoff.

The case grid is 3 families × Smart Booking off/on × MOTO candidate as focus or not: 12 cases.

The Flow transport is replaced by a unique-WAMID recorder; the production `wamid.TEST_<epoch>` collided within one second, which is a probe artefact and was fixed before the reported runs.

| Assertion per case | Candidate SQLite | Candidate PG 16.13 | f16b1f3 SQLite | f16b1f3 PG |
|---|---|---|---|---|
| turn 1 `flow_button_sent`, exactly one outbound Flow on `CE_FLOW`, NAVIGATE, the configured client-data Flow id | 12/12 | 12/12 | 12/12 | 12/12 |
| turn 2 Meta text **exactly** "A la brevedad uno de nuestros asesores se estará contactando con vos." | 12/12 | 12/12 | **0/12** (fallback) | **0/12** |
| needs_human true on state and lead; MOTO candidate preserved (Honda CB500 / Can-Am Outlander / Polaris RZR) | 12/12 | 12/12 | 12/12 | 12/12 |
| no vehicle/zone fallback; no "¿Es un …" proposition; `pending_fuzzy_catalog_key` None | 12/12 | 12/12 | fallback in 12/12 | fallback in 12/12 |
| outstanding questions 0 | 12/12 | 12/12 | **1 in 12/12** | **1 in 12/12** |
| ThreadRevision / booking commands / offers / notices / recovery jobs 0, no Booking Flow, egress 0 | 12/12 | 12/12 | 12/12 | 12/12 |
| verdict | **ALL_PASS** | **ALL_PASS** | SOME_FAIL (12) | SOME_FAIL (12) |

**Implementer's suite, independently re-run:**
- SQLite candidate: 57 passed / 218 subtests (both new files).
- Red control on `base_red`: **12 failed / 14 passed** (a02/a03/a04/a06 × 3 Flow variants).
- PG (reviewer engine-swap runner `probe/prodpath_pg.py`): candidate 26/26, PG ledger = 25 advisor texts + 1 no-Flow handoff, OQ 0. Base: 12 failures, ledger = 25 fallbacks, **OQ 25**.

The closeout's "12 failed / 13 passed" sums to 25 of 26; the correct figure is 14 passed (R5F1RV-05).

---

## 4. Genuine vehicle claims (no contract)

The real `_validate_outbound_text` with the real `_reply_vehicle_resolver`, on both trees (`probe/inventory.py`):

| Text | Resolver | no focus | Corolla focus | CB500 focus | base = cand |
|---|---|---|---|---|---|
| Es un Fiat Uno. | Fiat Uno | FALLBACK | FALLBACK | FALLBACK | yes |
| Tenés un Uno 2015. | Fiat Uno | FALLBACK | FALLBACK | FALLBACK | yes |
| Es un Fiat Punto. | Fiat Punto | FALLBACK | FALLBACK | FALLBACK | yes |
| Es un Fiat Idea. | Fiat Idea | FALLBACK | FALLBACK | FALLBACK | yes |
| Es un Toyota Corolla. | Toyota Corolla | FALLBACK | **KEPT** | FALLBACK | yes |
| Es una Honda CB500. / La Honda CB500 la revisamos el martes. | **None** | KEPT | KEPT | KEPT | yes |

The correction does not globally ignore catalogue words: mutant RV12, a uno/punto/idea blacklist, is killed by 43 tests.

**Honda CB500 (R5F1RV-06, LOW, separate; not a launch blocker).** The reply resolver is the car catalogue, and it has never recognised motorcycles (identical on base). The consequence is a false negative: an AI sentence naming a motorcycle is not checked against the canonical focus. On the launch path every motorcycle/quad/UTV enquiry is taken over deterministically by `_motorcycle_human_handoff` before the reply model (the regex covers moto/scooter/cuatriciclo/quad/ATV/UTV), and the thread is then human-owned. A motorcycle claim therefore reaches AI prose only when the customer names a motorcycle model without any motorcycle word. Even then, the sentence restates what the customer said. Adding motorcycle recognition would make more prose rejectable (the R5F1RV-01 class) and must not be done in isolation.

---

## 5. Deterministic template inventory

Method (`probe/inventory.py`):
1. An AST walk of `conversation_engine.py` collects every loaded name and attribute.
2. Each name that is an UPPER_CASE binding on the CE module (its own or imported) is resolved to its value.
3. Every string in a `str`/`tuple`/`list`/`set`/`dict` value that has a space and more than 12 characters is collected, with format templates rendered (Fiat Uno / Palermo).
4. Each string is validated through the real `_validate_outbound_text` under no focus, Toyota Corolla and Honda CB500, on both trees.

This is a superset of the implementer's E inventory: it adds imported constants and container members. It also starts from the other end: I enumerated all 48 `_send_text_to_wa` and 5 `_send_flow_button` call sites (§2.2 of the closeout matches).

| Result | Strings |
|---|---|
| Changed base → candidate | **only** `_MOTORCYCLE_ADVISOR_REPLY`, FALLBACK → UNCHANGED, all 3 foci |
| Whitespace-only (`\n\n` → space), both sides | `_CANONICAL_LOCATION_ASK`, `_COVERAGE_RESPONSE` |
| Rejected (intentionally), both sides | `_SCHEDULING_FORBIDDEN_PHRASES` members "turno confirmado" and "reserva confirmada". These are a lexicon, not sent text; correctly BOOKING-rejected |
| Unchanged | all others (208 strings / 54 names, 624 name×text×focus rows; symmetric difference 0) |
| Tagged on candidate | exactly `_MOTORCYCLE_ADVISOR_REPLY`, `_MOTORCYCLE_HANDOFF_REPLY` |

- **Exemptions are justified.** Both registered texts assert no vehicle, price, zone, slot, booking or acceptance. `_MOTORCYCLE_HANDOFF_REPLY` gains nothing (it passed on base) but belongs to the same family.
- **Composed strings stay plain.** Every composed argument (`msg`, `reply`, `question`, `fallback`, `_pq`, `_requery_reply`, `_numeric_model_question`, `f"{primary_msg} {fallback_msg}"`, `customer_msg`) is an f-string, a `.format()` of a module template or a helper return. Each is a plain `str` with no tag, so it is fully validated exactly as on base. The prose-probe equality base == cand over 112 full-engine cases (§6) confirms the untagged path is byte-for-byte unchanged.
- **Limitation of both inventories.** The implementer's e03, and my first pass, ran with `known_zone_names=()`, which leaves LOCATION inert. The §6 corpus and engine probes use the 211 production zone names, and that is where LOCATION false positives appear.

---

## 6. Broader architectural launch gate — DECISION B (launch-blocking)

### 6.1 Reachability

- **Direct webhook does not change the validator.** `routes/whatsapp.py:474` with `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=true` calls `_dispatch_to_engine` → `ConversationEngine(db, settings).handle(...)`. That is the same entry `POST /api/conversation/handle` uses from n8n. Either way, CE composes the reply, validates it in `_send_text_to_wa`, and sends it to Meta itself.
- **No downstream layer repairs replies.** n8n is upstream only and never sees CE's reply. `OutboundSafetyGate` checks the kill switch, path, allowlist, dedup and recipient lock, never meaning. The closed-beta allowlist limits *who* can converse, not *what* they are sent.
- **The AI path is live in the launch posture.** The R5 kit's six ON windows (H, H2, W1A, W1B, W2) keep direct webhook **false**, R1/R2/R3 true, and an OpenAI key and the semantic flags set on live (read-only check). AI-composed replies are therefore live in the certified posture, and reachable identically with direct webhook true.

### 6.2 Full ConversationEngine evidence (`probe/prose.py`)

Real route. `_call_openai_inner` is replaced by a canned provider response, so `_call_openai`'s accounting and the reply **admission** are real (`ADMITTED` in every case). The viaticos table holds the 211 production zone names (exported read-only as reference data, `probe/zones.psv` sha256 `439eb3b9…94b1`).

Two worlds:
- `new`: QUALIFYING, no vehicle.
- `car_nozone`: QUALIFYING, Toyota Corolla 2018 as the current focus, zone not yet given.

The quoted world could not reach the reply model; the deterministic quote override answers first (disclosed). Each reply ran × Smart Booking off/on × direct-webhook flag off/on: 112 cases per tree.

| AI reply (abridged) | `new` | `car_nozone` (vehicle known) |
|---|---|---|
| control "Perfecto, cualquier duda me escribís por acá." | verbatim | verbatim |
| "…vemos todo **uno por uno**: motor, frenos…" | **fallback + OQ** | **fallback + OQ** |
| "Podemos revisar primero **uno de los dos autos**…" | **fallback + OQ** | **fallback + OQ** |
| "¿La revisión sería para **uno de los dos autos**…?" | question kept, then **"Todavía no tengo confirmado el vehículo, así que te confirmo: ¿Es un Fiat Uno?" appended; `pending_fuzzy_catalog_key = Fiat‖Uno`** | verbatim |
| "Perfecto, te esperamos **a las 10 en punto**." | **fallback + OQ** | **fallback + OQ** |
| "¡**Buena idea**! Revisarlo antes de comprar…" | first sentence dropped | first sentence dropped |
| "¿Te parece **buena idea** revisarlo…?" | **"¿Es un Fiat Idea?" appended; `pending_fuzzy = Fiat‖Idea`** | verbatim |
| "En la revisión del auto chequeamos los **pilares**…" | **fallback + OQ** | **fallback + OQ** |
| "Gracias **Pilar**, la revisión del auto la hacemos donde esté." | **fallback + OQ** | **fallback + OQ** |
| "…el chequeo del chasis **norteamericano**." | **fallback + OQ** | **fallback + OQ** |
| "Trabajamos de lunes a viernes **de 9:00 a 18:00**." | **fallback + OQ** | **fallback + OQ** |
| hours + "Cualquier duda me escribís." | hours dropped | hours dropped |
| "…250 puntos, **uno por uno**. ¿En qué zona está el auto?" | answer dropped, zone question + OQ | answer dropped, zone question + OQ |

- The fallback is *"Para poder avanzar necesito confirmar algunos datos. ¿Me contás qué vehículo querés revisar y en qué zona está?"*. In `car_nozone`, it re-asks for a vehicle the customer already gave.
- "OQ" means an `INSPECTION_LOCATION` outstanding question, `PENDING_DELIVERY`. It becomes `OUTSTANDING` on delivery and will then claim the customer's next message as a location answer.
- **The result is invariant** across Smart Booking × direct webhook. It is identical on pristine f16b1f3 (base == cand on all 112 cases), so the defect is pre-existing and not introduced. It is identical on PostgreSQL 16.13 (112/112 equal to SQLite: 56 fallback + OQ, 8 fuzzy + pending).

### 6.3 Frequency (`probe/corpus.py`)

40 plausible single-sentence replies were written from the CE system prompt's own guidance (rules 13–20: scope, duration, payment, location, hours) and run through the real validator with the production zone lexicon. **13 of 40 (32.5%) are replaced wholesale by the fallback**, for no focus, a Corolla focus, and a Corolla in Palermo with a quote alike.

| Trigger | Replaced replies |
|---|---|
| VEHICLE | "…punto por punto", "Chequeamos uno por uno…", "Cada uno de los puntos de control…", "Llegamos a la hora acordada, en punto.", "¡Buena idea revisarlo antes de comprar!", "Me parece una excelente idea.", "Uno nunca sabe lo que puede tener un usado…", "…podemos revisar uno y después el otro.", "Si querés, te paso el detalle punto a punto." |
| AVAILABILITY | rule 16 almost verbatim: "Cuanto antes nos avises, mejor: coordinamos según la disponibilidad de agenda."; "Trabajamos de lunes a sábado de 9:00 a 18:00." |
| LOCATION | "Si el auto está en zona Norte, también vamos.", "La revisión del auto la hacemos en Flores sin problema." |

"más de 250 puntos de control" (plural) survives. Real model output is usually multi-sentence, so it more often loses a sentence silently than falls back entirely. Both are wrong customer responses.

**Counter-evidence, recorded honestly.** Read-only aggregate counts on live `crm_test`: **0** fallback sends among **38** delivered `CE_TEXT` replies (24 Sep, 8 Oct, 6 Nov, tester sessions). Partial drops are invisible in sent text, the sample is small and tester-driven, and how many of the 38 were AI-composed rather than deterministic was not determined. This lowers the observed rate in past Wilds. It does not show a boundary.

### 6.4 Decision

**B. It is launch-blocking and requires a class-level correction before the conversation engine is enabled.**

- No independently proven boundary prevents it in the enabled posture, and none was found by inspection or measurement.
- It sends an irrelevant question in place of the answer, including re-asking for known vehicle data.
- It writes incorrect conversational state: an `INSPECTION_LOCATION` outstanding question (with R2 armed), or an invented `pending_fuzzy_catalog_key` plus a "¿Es un Fiat …?" question (via `_register_canonical_vehicle_proposition`, which reparses validated questions).
- The launch's purpose, the final WILD and then real customer conversation on CE, is precisely the natural free-form exchange in which "uno", "idea", "punto", "disponibilidad", hours and zone words occur.

**Governance placement (CLAUDE.md rules 1–2).** This is new contradictory runtime evidence against **L4.7D-RESPONSE-VALIDATOR** (PASS), whose stated guarantee is that "FAQ answers and the required next question are preserved". It also contradicts the G3-6 canonical-vehicle producer, which re-reads composed prose after validation. Only that gate (the response-validation/claim-reparsing boundary) should be reopened. No other gate is affected, and the R5-F1 template boundary should be retained inside the corrected candidate.

Class-level direction (for the correction milestone, not implemented here):
- A claim needs a claim-shaped construction (a make, or a vehicle noun with a model in a predicate position, a zone with a locative frame), not a token occurrence.
- A lone common-word model token, zone substring or bare `H:MM` range must not be a claim on its own.
- The post-validation vehicle producer must not arm canonical state from composed prose.
- The outstanding-question recorder must not record a question that only the validator's fallback asked.

---

## 7. Fail-open

- **Pre-existing behaviour.** `_validate_outbound_text` catches every exception and returns the raw text. That is unchanged.
- **New code and untagged (AI) text.** `template_tag_of` is a `type()` check plus `getattr` with a default, and `_template_exemptions(None, …)` returns immediately. So the candidate adds **no raise path for any untagged text** (CE01/CE02/RV10 and the 112 identical prose cases).
- **New code and tagged text.** It is total for every plausible metadata defect (G1, G2 except one case, G3): unknown or non-member policy, missing contract, crossed id, non-contract value, `None` text and `None` policy-table entries all refuse into full validation.
  - It is **not strictly total** (R5F1RV-02). It interpolates `{policy!r}` (arbitrary `__repr__`) and calls `frozenset(skipped)` on an unvalidated table value. Either can raise, and then CE sends the raw value.
  - Both need a source edit placing a pathological object in an immutable module registry. The raw value is necessarily a tagged constant, and only the two approved handoffs are tagged, so what goes out is the approved handoff itself.
- **Malformed template metadata cannot turn a refusal into raw delivery of unapproved text through CE.** The closeout's "Total: never raises" is overstated. Suggested hardening: refuse by `type(policy).__name__`, and require `isinstance(skipped, frozenset)`.

---

## 8. Frozen-file sentinel — existing failure correctly carried as launch debt

`test_sb1_booking_representation::UnchangedContracts::test_sb1_20_frozen_files_unchanged` pins blob ids at SB1's parent `ab95706`.

- **On pristine f16b1f3 it already fails.** `claim_projection.py` (base blob `a420ef32…` vs pin `e61cd7ef…`, changed by e0e83c3), `routes/flow_data_exchange.py` (`4a2310c5…` vs `55987188…`, 163b37e) and `routes/whatsapp.py` (`4e979d37…` vs `b2913d55…`, e0e83c3) have drifted. Those reviewed commits carried it in the known-failing baseline (62 F / 9 E, equal to P-1).
- **The candidate adds a fourth entry.** `response_validator.py` (pin `7085211b…` = base blob; candidate `43f6f70f…`) becomes the fourth drifted file. This is the single COMMON_changed message in the focused and full comparisons. Its status is red → red, NEW 0.
- **Why updating it is not required before commit.** Removing only the validator entry would leave the test red. Repairing it means owner-authorised un-freezing of three files from other milestones. The precedent in the file (the SB2B and SB2A-R2 comments) is to remove the entry with an authorisation note.
- **Recommendation.** When the sentinel is repaired as its own hygiene item, remove `response_validator.py` with an "R5-F1 authorised" note together with the other three. Until then the sentinel detects nothing, and that should be stated in the launch-debt register.

---

## 9. Regression and mutations

Image `ridecheck-crm-test:l4.7b2` (`c50f470951d4`), read-only trees, `--network none`, serial, gated.

| Run | Base | Candidate | Comparison (by junit identity) |
|---|---|---|---|
| Focused SQLite, 45 files (validator/CE, attribution and composition, moto MF/MH, fuzzy vehicle, outstanding question R2, outbound gate, kill switch M19/M20, L2 transport, Smart Booking SB1/SB2A R1–R7/SB2B, convergence P2–P4, minimum launch r0–r2 + e2e, vehicle identity, WILD1 recovery, the 2 new files) | 1 F / 1342 P / 12 S (1355) | 1 F / 1399 P / 12 S (1412) | **NEW 0, GONE 0**, COMMON 1 (sentinel, message changed), +57, −0, skip changes 0 |
| Full SQLite `../tests` | 62 F / 9 E / 7489 P / 786 S (8346) | 62 F / 9 E / 7546 P / 786 S (8403) | **NEW 0, GONE 0**, COMMON 71, changed 1 (sentinel), +57, −0, skip changes 0 |
| PostgreSQL 16.13, 17 files, each on a fresh clone of a head template (`create_all` on the 305a112 pre-schema → stamp `20261006_clarification_attempts` → `upgrade head` = `20261104_vehicle_identity`): convergence P3 race/P3-R1/P4/P4-R1, PW1, G3-7P, G3-7R projection/R2/R3, minimum launch, SB2B, W1F O1/unanswered, vehicle identity recovery/T1, P-1 recipient lock, M19 PG gate integration | 439 P / 35 S | 439 P / 35 S | **NEW 0, GONE 0, COMMON 0** |
| PG production path / journey / prose | 12 F of 26 / 12 SOME_FAIL / — | 26 P / 12 ALL_PASS / 112 = SQLite | §3, §6 |

**PG harness incident (disclosed).** In the first PG pass the disposable server exited during the candidate's last three files, so `connection failed` was reported for 59 cases. The 34 undropped clones had filled the 600 MiB tmpfs. The server was restarted, the template rebuilt, and those three files rerun on **both** sides with each clone dropped after use (tmpfs 65/600 MiB). Base and candidate then both passed 10 + 37 + 15. The table reports the rerun.

**MIG/STAMP/CLEAN-gated cases** were skipped identically on both sides (35). `test_w1f_operator_notification_pg` was not run (it needs a notification floor, and notifications are untouched).

**Mutations** (`mut/mutants.py`, each in its own tree copy).
- T = `test_r5_f1_*` ×2, `test_m21_2_mf_motorcycle_flow`, `test_m21_2_mh_motorcycle_handoff`, `test_l4_7d_response_validator`. Control: 139 passed.
- P = reviewer `bypass.py` (new BREACH beyond control's 1) and `journey.py`.

| Id | Attempt | T | P |
|---|---|---|---|
| RV01 | forged id: `isinstance` (subclasses accepted) | KILLED (d04) | KILLED (A3) |
| RV02 | forged id: string coerced to enum | KILLED (3) | KILLED (D4-str) |
| RV03 | forged id: duck-typed `_template_id` | KILLED (d04) | KILLED (A3) |
| RV04 | mutable registry (dict) | KILLED (6) | KILLED (E1) |
| RV05 | mutable policy table (dict) | KILLED (d10) | — |
| RV06 | leak: `__reduce__` restores the class | KILLED (3) | — |
| RV07 | leak: validator returns the same tagged object when unchanged | **SURVIVED** | KILLED (C-validated-output-type) → R5F1RV-04 |
| RV08 | approved template skips **all** claim classes | KILLED (5) | KILLED (F1 ×4) |
| RV09 | NO_VEHICLE_CLAIM also skips LOCATION | KILLED (1) | KILLED (F2) |
| RV10 | refusal raises (exception-driven fail-open) | KILLED (28) | KILLED (traceback) |
| RV11 | exact text weakened to a prefix | KILLED (d05) | KILLED (B1/B2/B5) |
| RV12 | global uno/punto/idea blacklist | KILLED (43) | KILLED (A1, A4a…) |
| CE01 | AI reply wrapped as an approved template | KILLED (d13) | — (fails closed by text) |
| CE02 | CE grants the advisor id to every text | KILLED (d14, d16) | — |
| CE03 | template text drift without its contract | KILLED (21) | KILLED (journey) |
| CE04 | vehicle-claiming text consistently registered as NO_VEHICLE_CLAIM | KILLED (d13, e01) | — |
| CE04b | existing unregistered constant tagged + registered | KILLED (d13, +1) | — |
| CE05 | CE call site reverted (R5-F1 returns) | KILLED (17) | KILLED (journey) |

The implementer's suites kill **17/18**; with the reviewer probes, **18/18**.

**Resource gates** (`memory.log`): 25 heavy jobs, each started at MemAvailable ≥ 1571 MiB and disk ≥ 27.8 GiB, one at a time.
- At review start MemAvailable was 1033 MiB (other sessions' processes, not touched); no job started until it was above the gate.
- Lowest readings: full-cand 702 MiB, pgsuites 733 MiB, pgsuites2 942 MiB. The 500 MiB watchdog never fired. WAIT 0, ABORT 0.

---

## 10. Live isolation (read-only; before 12:34:42Z, after 13:39:09Z)

Instrument: `live/probe.sh` (`docker inspect`; a fresh-process `docker exec` of env/settings/`booking_dispatch_allowed`; `psql` with `default_transaction_read_only=on` running the kit's `db_fingerprint.sql`, sha256 `4e4445b6…d7dd`, copied). **`diff` of before and after, excluding the timestamp line: empty.**

| Item | Before = after |
|---|---|
| backend | `04e14e5770f9`, image `sha256:1b8ed6119066…8fc14`, started 2026-11-09T00:00:44.317Z, restarts 0, GIT_SHA f16b1f3 |
| postgres | `5246fd86d829`, image `71e27bf60b70…`, started 2026-10-21T23:31:05Z, restarts 0 |
| n8n | `6ea80570b67b`, image `2d70fafea7c0…`, started 2026-10-21T23:31:12Z, restarts 0. Running containers: 3 |
| Eight flags | OUTBOUND, SMART_BOOKING_CE, OPERATOR_WHATSAPP_NOTIFY, CONVERSATION_ENGINE_DIRECT_WEBHOOK, EVIDENCE_ADMISSION, OUTSTANDING_QUESTION, LOCALITY_RESOLUTION_SAFETY, BOOKING_FLOW_DETAILS_PREFILL: all literally `false` in env, and `False` when parsed |
| Dispatch | `booking_dispatch_allowed() = False` |
| Schema | `crm_test` at `20261104_vehicle_identity`; constraints md5 `9e8fd6dbb788c60cb36b954256512b66`; columns md5 `1427d58f72ba94b19109a0cd0e777a5e` |
| Fingerprint | **`fbae2abdc3371f9146f0ac51a2ffa427`** (equal to the authoritative audit's) |
| Max ids | msg 6268 (out 6267, pending 0), offers 5, notices 2, jobs 0, security 735, dedup 2529, thread_revisions 9, threads 2077, leads 171, outstanding questions 2, ai_events 244, candidates 147, states 2078 |
| Files | compose `10d47105…03a3`, `.env` `b7204ee2…8ba1`, beta `79afd94b…71ad`, probe-off `28442e29…02a6`, pin-candidate `fe13e1c3…a03e`, enable-off `ae30e76e…251c`; 10 booking Flow files unchanged, including v7.8 `2102a8ec…8354` and published v7.3 `274038ba…fb15` |
| Worktrees | deployment-prep HEAD f16b1f3, 0 tracked changes; candidate HEAD f16b1f3, 0 staged, candidate bytes unchanged (§1 hashes re-verified after) |

**Live touches (all read-only):** inspect, exec settings read, read-only psql (fingerprint, max ids, schema digests, `viaticos_zones` reference export and counts, aggregate counts of `CE_TEXT` and fallback sends; no row content printed).

No outbound, booking, notice, recovery, outstanding-question or evidence activity was caused by the review. All disposable `r5rv-*` containers are gone. No Docker network was created; the PG instance ran with `--network none`, and clients joined its namespace.

---

## 11. Findings (ranked)

| ID | Severity | Gate | Summary |
|---|---|---|---|
| **R5F1RV-01** | **HIGH — launch-blocking for CE enablement** | L4.7D response validator + G3-6 vehicle producer | Ordinary AI prose ("uno", "idea", "punto", "disponibilidad", `H:MM` hours, zone substrings such as Pilar/pilares, Norte/norteamericano, Flores) is read as VEHICLE/AVAILABILITY/LOCATION claims. Whole replies are replaced by the vehicle/zone question, including to customers whose vehicle is known, and an INSPECTION_LOCATION outstanding question is opened. Questions survive the validator but then arm an invented `pending_fuzzy_catalog_key` and append "¿Es un Fiat …?". Pre-existing (base = cand), reachable in both webhook postures, SQLite = PG. 13/40 prompt-derived replies replaced. Class correction required (§6.4) |
| R5F1RV-02 | LOW | R5-F1 boundary | `_template_exemptions` is not strictly total: `{policy!r}` and `frozenset(skipped)` can raise on a pathological registry value, and CE's fail-open then sends the raw tagged value (necessarily an approved handoff). Needs a source edit to reach. The closeout's "never raises" is overstated |
| R5F1RV-03 | LOW | R5-F1 boundary | The exact-text check `text != contract.text` defers to a `str` subclass's `__ne__` when a caller passes `approved_template=` directly. Not reachable (CE derives the id from exact-type values only; there is no other caller). Suggest `type(text) in (str, ApprovedTemplateText) and str.__eq__(text, contract.text)` |
| R5F1RV-04 | LOW | test coverage | Mutant RV07 (validator returns the same tagged object) survives the implementer's suites. Nothing downstream reads the tag today, so there is no runtime effect. Add an assertion that `validate_response(...).text` is an exact `str` |
| R5F1RV-05 | INFO | closeout accuracy | The red control is 12 failed / **14** passed (26), not 13. The E inventory and my first pass ran with no zone names (LOCATION inert); §6.3 corrects this |
| R5F1RV-06 | LOW (separate; not a launch blocker) | conversation | Honda CB500 is not a reply-catalogue claim, identically on base (§4). Accepted for this boundary: moto enquiries are handed off deterministically first. Do not add motorcycle recognition without the R5F1RV-01 class fix |
| R5F1RV-07 | INFO | test hygiene | The SB1 frozen sentinel has been red since e0e83c3/163b37e (3 files); the candidate makes it 4. Carried as launch debt (§8) |
| R5F1RV-08 | INFO | threat model | Registry/enum immutability is language-level: `gc.get_referents`, module rebinding and `_member_map_` pokes need in-process code execution, which is outside the threat model |
| Carried | — | — | `RECOVERY_TEXT`/`RECOVERY_FLOW_BODY` would be altered if ever routed through CE validation (unchanged; the recovery daemon does not validate). R5RV-01 (kit 3 h shift) is untouched by this candidate and remains open |

**Required before a re-review can be READY:**
1. A class-level correction of R5F1RV-01 at the L4.7D / G3-6 boundary (§6.4), retaining this candidate's template boundary, with full-engine tests equivalent to §6.2 (both worlds, both postures, SQLite and PG) and a frequency corpus like §6.3.
2. Optionally, fold R5F1RV-02/03/04 into the same revision.
3. The frozen-sentinel decision as in §8.

W-3 remains OPEN. The final WILD remains NOT RUN. No live window is authorised by this audit.

---

## 12. Limitations

- **AI output is simulated.** The provider response is canned (the real admission and accounting run). The phrases and the corpus are reviewer-written to be plausible, not sampled from the model. The live sample shows 0 fallbacks in 38 delivered CE texts.
- **Semantic interpreter off in the probes.** `shadow_understand_enabled`/`semantic_same_turn_enabled` were set false (live has them true) to avoid model egress. The validator, the proposition producer and the outstanding-question recorder do not depend on them, but routing details under semantic evidence were not exercised.
- **Untested world.** The quoted / post-quote world could not be brought to the reply model in a fixture (the deterministic quote override answered). The Corolla-no-zone world stands in for "vehicle known".
- **PG schema.** The template was built by the project's documented recipe (create_all on the 305a112 pre-schema, stamp, upgrade), not by restoring live.
- **Not run.** No n8n workflow and no live or handset run. The direct-webhook route was established by reading code (`_dispatch_to_engine` → `ConversationEngine.handle`), and the flag was toggled in the CE probes, where CE does not read it.

---

## 13. Test and run identities; scratch evidence

Evidence root: **`/opt/ridecheck-builds/crm-r5-f1-independent-review-20261111T123442Z/`** (0700). Nothing was deleted.

| Path | Contents (sha256 prefix) |
|---|---|
| `base/`, `cand/`, `base_red/` (read-only) + `*.manifest.sha256` | review trees (§1) |
| `manifest.txt`, `secret_scan.txt`, `added.txt` (0600), `base_vs_cand.txt` | §1 |
| `probe/journey.py` (`773d7cb5…`), `journey_{cand,base}_{sqlite,pg}.out`, `summ.py` | §3 |
| `probe/prodpath_pg.py` (`507679f3…`), `prodpath_pg_{cand,base}.out`; `prodpath_cand.txt`, `red_base.txt`, `junit/{prodpath_cand,red_base}.xml` | §3 |
| `probe/bypass.py` (`29e47374…`), `bypass.out` | §2, §7 |
| `probe/inventory.py` (`23b0a7fe…`), `inventory_{base,cand}.out` | §4, §5 |
| `probe/prose.py` (`ca58b137…`), `prose_{cand,base}_sqlite.out`, `prose_cand_pg.out` | §6.2 |
| `probe/corpus.py` (`25cea2a9…`), `corpus_cand.out`; `probe/zones.psv` (`439eb3b9…`, place-name reference data) | §6.3 |
| `focused.list`, `focused_{base,cand}.txt`, `focused.cmp.txt` (`460ed6cf…`), `full_{base,cand}.txt`, `full.cmp.txt` (`8c8fc458…`), `junit/*.xml` | §9 |
| `pg.sh` (`be46f85a…`), `pgsuites.sh`, `pgsuites2.sh`, `pgsuites.txt`, `pgsuites2.txt`, `pg.cmp.txt` (`0abe6c90…`), `junit/pg_{base,cand}/`, `.pgpw` (0600, disposable) | §9 |
| `mut/mutants.py` (`86fb7f31…`), `apply.py`, `run.sh`, `results.txt` (`96683f0d…`), `trees/<id>/` | §9 |
| `gate.sh` (`f825429f…`), `sq.sh`, `py.sh`, `cmp.py` (`36b70340…`), `memory.log` | harness, resource log |
| `live/probe.sh` (`342de343…`), `db_fingerprint.sql`, `before.txt` (`c8360217…`), `after.txt` (`c0ed308b…`) (0600) | §10 |

Session scratchpad: `/tmp/claude-0/-opt-ridecheck-crm-r5-f1-response-validation/bc6b372d-aac0-42f7-8ed7-3864533a9687/scratchpad/` (pointer file `EV`, `focused.list`).

**Process disclosure.** A first mutation runner that reset its work tree with `rm -rf` was refused by the environment's deny rule. It was replaced by one fresh tree per mutant (`mut/trees/`), with no deletion.

**VERDICT: CORRECTION REQUIRED.** STOP. Nothing staged, committed, pushed, built or deployed.
