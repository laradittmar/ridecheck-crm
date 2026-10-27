PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-W1F-W01-R3-LOCALITY-RESOLUTION-SAFETY

# R3 — an unsupported or ambiguous locality can no longer become a price or a booking

**Date:** 2026-10-26 · **Base commit:** `6a14cdb` (the deployed backend) · **Commit: NONE —
this is a checkpoint, not a landing** · **Flag:** `LOCALITY_RESOLUTION_SAFETY_ENABLED`,
default **OFF**

**Updated 2026-10-26 for `…-R3-LOCALITY-RESOLUTION-SAFETY-OWNER-REVIEW-R1`** — see §0,
§3.5, §5.1, §5.2, §5.4, §6.1, §8 and §10. Two defects the review's evidence gap was hiding
(R1RV-01 HIGH, R1RV-02 MEDIUM) are closed; one case is recorded as not closed and not R3's.

**Scope delivered:** R3 of the W1F-W-01 architectural review, all five of its scope items.
**Not delivered, by instruction:** R1 evidence admission and R2 outstanding-question
persistence. Nothing in this milestone touches them.

**STOP FOR OWNER REVIEW.** Nothing is committed, built, deployed, migrated or enabled.

---
## 0. OWNER-REVIEW-R1 addendum — the complete ambiguous turn (2026-10-26)

**The owner review was right, and the gap it named was covering two real defects.** This
section is the R1 checkpoint. It supersedes the ambiguity language in §5.1 and adds §5.4,
and nothing in it is committed, built, deployed, migrated or enabled.

The review asked how §5.1's "ambiguity writes nothing and sends nothing" could coexist with
the claim that a customer meaning the CABA barrio is asked to identify CABA. The answer is
that both statements were about different things, and saying so is not enough — one of them
was load-bearing for a promise the deployed code did not keep.

**Why the lower-level method "sends nothing" while the complete turn does answer.**
`_apply_zone_from_text` is a *write helper*. Its contract is `(early_return,
vehicle_location_written)`, and `early_return is None` means "I have not answered the
customer; carry on with the turn". For an UNSUPPORTED locality it returns an answer, because
coverage ends the conversation's commercial path. For an AMBIGUOUS one it returns
`(None, False)` — it writes nothing and it answers nothing, deliberately, because asking is
not a write helper's job. Clarification has exactly one authority in this engine,
`_turn_clarification_decision`, and exactly one executor, `_execute_clarification`, both of
which run later in the same turn. §5.1's R3-D row described the helper and read as though it
described the turn. The row is corrected below; the behaviour it described was accurate for
what it measured.

**What the review's gap was hiding.** Running the complete turn — requirement 1, all five
situations, offline, no provider, no network — produced two findings.

**R1RV-01 (HIGH, closed here) — the form refused the answer to its own question.** The
Location Flow asks two things: `zona_general` from a dropdown, and `localidad` as text. The
R3 guard passed only `localidad` to the verdict. A customer who selected **CABA** and typed
**San Nicolás** — the exact disambiguation we had just asked for — was therefore ruled
AMBIGUOUS, handed to a person (`needs_human=True`, `necesita_humano=True`), given no zone
and no quote. Measured, flag ON, before the correction:

```
form {'zona_general': 'CABA', 'localidad': 'San Nicolás'}
  -> 'Gracias por completar el formulario. Un agente … te va a contactar …'
  -> thread zone (None, None) · needs_human True · PricingService not called
```

The correction is one general rule, in `locality_text.disambiguated_by_text`: an **explicit
area selection** settles a cross-coverage ambiguity when it **agrees** with the matched
locality's own `zone_group`, and settles nothing when it disagrees. It compares two area
strings and reads no place name. A disagreeing selection ("Sur" + a CABA locality) is two
answers that cannot both be true, so it still reaches the warm handoff. Coverage is still
decided before ambiguity, so **no** dropdown can admit a declared out-of-coverage city.

**R1RV-02 (MEDIUM, closed here) — the ambiguity did not drive the clarification.** R3 relied
on the *absence* of a zone to make the router ask. That is a coincidence, not a mechanism,
and it fails exactly where it matters most — a **location correction**. Measured, flag ON,
before the correction, on a thread already quoted at CABA/Flores:

```
"perdón, el auto está en San Nicolás"
  -> route NO_CLARIFICATION_REQUIRED · reason DOMAIN_NOT_RELEVANT
  -> 'Genial! La cotización … en Flores es de $140.000 …'
```

The customer corrected the location and was re-quoted the **old** locality as if they had
said nothing. The flag-OFF posture is not better: it writes the wrong zone instead. The
correction records the verdict on the turn (`_turn_locality_ambiguity`) and the one
clarification decision reads it as **location evidence** — the family is relevant, and the
location is not resolved for this turn even if something is stored. This is the same
reasoning G3-7L applied to a CONTESTED vehicle burst: *admission governs writing, relevance
governs asking*, and this touches only the second. Nothing stored is deleted: the old zone
stands until the customer's answer replaces it. The pure router in `clarification_router.py`
is **unchanged** — only the two facts CE computes and hands it.

Neither correction is a phrase-specific patch (owner constraint 7). R1RV-01 compares two
area strings; R1RV-02 reads a verdict. `test_j23` asserts this structurally over the AST of
both consumers: no place name declared in `locality_text` may appear in executable code
anywhere else, in a string literal or otherwise.

### 0.1 The complete turn, per case (requirement 2)

Flag ON. `_process_text`/`handle` executed end to end against the real 211-row catalogue;
transport captured, reply model stubbed at `urllib`, `OutboundSafetyGate` mocked so no
`whatsapp_messages` row exists anywhere. Full transcripts: §5.4.

| # | input | response path | customer-visible response | sent | intent → | stage → | candidate zone | thread zone | needs_human | clarification attempt | PricingService | quote / offer / booking |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **a** | new conversation, no vehicle: "en San Nicolás" | Layer F qualifying intent gate — returns **before** the zone block | the qualification question ("¿Estás pensando en comprar un vehículo…?") | 1 text | None → `AWAITING_QUALIFICATION` | QUALIFYING → QUALIFYING | (no candidate) | (None,None) → (None,None) | False → False | none | **not called** | none possible |
| **b** | vehicle known, awaiting location: "en San Nicolás" | AMBIGUOUS pre-gate (no write) → routing gate → reply model → **LOCATION_FLOW_REQUIRED / LOCATION_UNRESOLVED** → reserve → Flow | the **Location Flow**, body = the certified `_LOCATION_CLARIFICATION_INTRO`, screen `LOCATION_DETAILS` | 1 Flow, 0 texts | None → None | QUALIFYING → QUALIFYING | (None,None) → (None,None) | (None,None) → (None,None) | False → False | **1 row, LOCATION, DISPATCHED** | **not called** | none possible |
| **c** | vehicle known: "el auto está en San Nicolás" | identical to **b** | identical to **b** | 1 Flow | None → `PREPURCHASE_INSPECTION` | QUALIFYING → QUALIFYING | (None,None) → (None,None) | (None,None) → (None,None) | False → False | **1 row, LOCATION, DISPATCHED** | **not called** | none possible |
| **d** | form: `zona_general=CABA`, `localidad=San Nicolás` | the form guard: the selection **agrees** → RESOLVED | the quote: "…en San Nicolás es de $140.000…" | 1 text | — | — | unchanged | (None,None) → **('CABA','San Nicolás')** | False → False | — | called: `AUTO / CABA / San Nicolás` | quote produced (0 viáticos) |
| **d′** | form: `zona_general=SUR`, `localidad=San Nicolás` | the selection **disagrees** → still AMBIGUOUS | the warm handoff ("Un agente … te va a contactar…") | 1 text | — | — | unchanged | (None,None) → (None,None) | False → **True** | — | **not called** | none possible |
| **e1** | "el auto está en San Nicolás, CABA" | the text itself settles it → RESOLVED | the quote, $140.000 | 1 text | None → `PREPURCHASE_INSPECTION` | QUALIFYING → QUOTED | (None,None) → **('CABA','San Nicolás')** | → ('CABA','San Nicolás') | False → False | none | called: `AUTO / CABA / San Nicolás` | quote produced |
| **e2** | "el auto está en San Nicolás, Capital" | identical to **e1** | the quote, $140.000 | 1 text | None → `PREPURCHASE_INSPECTION` | QUALIFYING → QUOTED | → ('CABA','San Nicolás') | → ('CABA','San Nicolás') | False → False | none | called: `AUTO / CABA / San Nicolás` | quote produced |
| **j** | already quoted at CABA/Flores: "perdón, el auto está en San Nicolás" | AMBIGUOUS recorded → **LOCATION_FLOW_REQUIRED / LOCATION_UNRESOLVED** | the **Location Flow**; **no price sentence** | 1 Flow, 0 texts | unchanged | QUOTED → QUOTED | ('CABA','Flores') **unchanged** | ('CABA','Flores') **unchanged** | False → False | **1 row, LOCATION, DISPATCHED** | called once with the **old** pair by `_compute_price_quote`; **nothing sent** | no new quote, no offer, no booking |

Two readings in that table deserve to be stated rather than buried. In row **j**
`PricingService` *is* called — `_compute_price_quote` runs earlier in the turn than the
clarification decision, on the zone already on file — and its result is never sent, never
stored and never offered. That ordering is inherited and R3 does not change it; what matters
for this invariant is that **no price for the ambiguous locality is ever computed or sent**,
and none is. And in row **d** the quote is 0 viáticos because every CABA row carries 0, so
the corrected form answer and the pre-R3 wrong answer happen to cost the same — the defect
was never the money in that case, it was that the system chose a city for the customer.

### 0.2 The invariant, line by line (requirement 3)

| the invariant says an ambiguous locality must | verdict | evidence |
|---|---|---|
| persist no inspection zone | **holds** | rows a, b, c, d′, j: candidate and thread zone unchanged in every one; `test_j02/j03/j04/j10/j25` |
| produce no price, offer or booking | **holds** | `PricingService` not called in a/b/c/d′; in j only on the pre-existing pair and nothing sent; `thread_revisions` count 0 throughout; `test_j25` |
| not silently choose CABA | **holds** | `test_j05` asserts no reading becomes CABA/San Nicolás across three phrasings, and that "San Nicolás" never appears in the outbound text |
| not restart the conversation | **holds** | stage preserved in every row (QUALIFYING→QUALIFYING, QUOTED→QUOTED); `test_j04` asserts `STAGE_QUOTED` survives |
| not send the generic uncertain-service greeting | **holds for b, c, j** · **does NOT hold for a** | `test_j02` asserts the greeting marker is absent; row **a** sends exactly that greeting, see §0.3 |
| not leave the customer without a response | **holds** | every row sends exactly one response; `test_j01` asserts `responded` even in the row that is otherwise limited |
| ask one bounded, useful clarification that distinguishes the supported CABA locality from the unsupported province locality | **holds for b, c, j** · **not asked in a** | the Location Flow's `zona_general` dropdown **is** the distinguishing axis, and after R1RV-01 answering it resolves the locality (row **d**) — so the question asked and the answer accepted now form a closed loop. It is bounded by the durable attempt store: exactly one row per thread/cycle/family, and a second ambiguous turn escalates rather than re-asking |

### 0.3 The one case that is NOT closed, and why it is not R3's to close

**Row (a) — a brand-new conversation that names only a locality is answered with the
qualification question, and the locality is dropped.** The Layer F QUALIFYING intent gate
runs *before* the zone block in `_process_text` and returns, so no locality verdict is ever
reached. Measured facts: the outcome is **byte-identical with the flag off** (`test_j01b`),
nothing is persisted in either posture, no price is computed, and the customer does get a
response. R3 therefore neither causes nor worsens it.

This is **W1FRV-01, positional authority** — the HIGH finding of the architectural review,
and the Wild's own defect. Closing it means admitting the locality as evidence at the point
the gate exits and carrying the unanswered question forward, which is precisely **R1
evidence admission** and **R2 outstanding-question persistence**. Doing it here would be
implementing R1 inside R3, which the milestone forbids (constraint 6). It is recorded, not
closed, and **W1F-W-01 remains open**.

### 0.4 Requirement 6 — the unsupported and unresolved turns, re-confirmed

| input | response | zone written | needs_human | PricingService | quote/booking |
|---|---|---|---|---|---|
| "en Mar del Plata" | the coverage response, verbatim | **none** anywhere | → True (lead too) | **not called** | none |
| "el auto está en Mar del Plata" | the coverage response, verbatim | **none** anywhere | → True (lead too) | **not called** | none |
| "en San Miguel del Monte" | the Location Flow (verdict UNRESOLVED) | **none** anywhere | → False | **not called** | none |

"La Plata" and "San Miguel" appear nowhere in any of those responses (`test_j14`,
`test_j15`). With the flag off all three reproduce the deployed wrong answers exactly
(`test_j18`, `test_j19`), which is what makes the rollback real.

### 0.5 One instrument defect found and fixed in this round

My first version of the new test module set `OUTBOUND_ENABLED=true` at module scope.
`_outbound_armed()` reads the process environment, so that is a global mutation: it leaked
into every module imported afterwards and was itself overwritten by the next module that set
it. The measured consequence was 70 failures in the full suite instead of 62 — including
eight of my own tests, which passed in isolation. The kill switch is now armed per turn with
`patch.dict`, exactly as `tests/_sb2b_harness.py` does it, and the `app.db` stub is installed
only if nothing has imported `app.db` yet. The suite is back to a clean identity delta (§5.2).
Recording it because the 70-vs-62 reading was a real instrument fault, not a code defect, and
a reader comparing run logs would otherwise have no way to tell.

---

## 1. What this changes, in one paragraph

Three measured wrong-location defects are closed, and the order that caused one of them is
inverted. `"en mar del plata"` no longer resolves to La Plata (180 000 viáticos, 400 km
away); `"en san miguel del monte"` no longer resolves to San Miguel (50 000 viáticos) —
refused by a structural rule that names no city; `"en san nicolás"` no longer resolves
silently to the CABA barrio (0 viáticos) when the customer may mean the province city
240 km away. Coverage and ambiguity are now evaluated **before** any candidate or thread
zone is written instead of afterwards in the routing gate, so an unsupported locality
produces the existing coverage/handoff outcome **and persists nothing**. Plural
vehicle-location clauses ("los autos están en Flores" — the Wild's own turn 1) are
understood. The clause patterns, the locative-context rule, the coverage list and the new
validators now live in one leaf module that both consumers import, so the two copies the
review found cannot diverge again. With the flag off, every one of those behaviours is the
deployed behaviour, byte for byte — including the three defects, which is what makes the
rollback real rather than asserted.

---

## 2. Exact files changed

| file | status | lines | what |
|---|---|---|---|
| `backend/app/services/locality_text.py` | **NEW** (untracked) | 386 | the shared description: clause patterns (singular + plural), customer-origin language, uncertainty markers, article rule, `locality_in_context`, the coverage list, and the three validators `is_outside_coverage` / `composite_name_conflict` / `is_cross_coverage_ambiguous`, plus `LocalityVerdict`. A leaf: imports only `re` and `unicodedata` |
| `backend/app/services/conversation_engine.py` | modified | +269/−128 (24 hunks) | imports the leaf and **binds** the names it used to define; adds `_locality_safety_on`, `_locality_verdict`, `_matched_forms_in_text`; splits the inherited matcher out as `_match_zone_in_text` and makes `_extract_zone_from_text` the guarded wrapper; plural clauses in `_extract_vehicle_location_zones`; the coverage/ambiguity pre-gate in `_apply_zone_from_text`; the form-answer guard in `_process_location_fallback_response`; passes the flag at all five `resolve_field_evidence` call sites |
| `backend/app/services/field_evidence.py` | modified | +96/−48 | the replicated patterns are gone — bound from the leaf; `_extract_vehicle_location_from_turn(..., locality_safety=)` reads plural clauses and drops a locality the text itself disqualifies; the flag is threaded through `_resolve_inspection_location` and `resolve_field_evidence` |
| `backend/app/settings.py` | modified | +12 | `locality_resolution_safety_enabled: bool = False` ← `LOCALITY_RESOLUTION_SAFETY_ENABLED` |
| `tests/test_w1f_w01_r3_locality_safety.py` | **NEW** (untracked) | 676 | 55 tests in 9 groups, both flag postures. **R1 corrected one assertion here** — see §0 and §5.1 |
| `tests/test_w1f_w01_r3_r1_fullturn_ambiguity.py` | **NEW** (untracked, OWNER-REVIEW-R1) | 611 | 27 tests in 6 groups: the COMPLETE turn for every ambiguous, disambiguated, unsupported and form case the owner review enumerated |
| `tests/fixtures/viaticos_zones_20261026.tsv` | **NEW** (untracked) | 211 | the real viáticos catalogue exported from `crm_test` on 2026-10-26. Configuration data: zone group, locality, viáticos amount. No customer data |

**Untouched, and verified untouched** (re-verified after OWNER-REVIEW-R1)**:** `PricingService`, `ScheduleService`,
`BookingAuthority`, `OfferLifecycle`, `OutboundSafetyGate`, every offer/ledger/attempt
table, the Meta template and its parameters, the n8n workflow, every migration, and
`docs/operations/RECOVERY_MANIFEST.md` — the pre-existing tracked modification, still
20 421 bytes dated Oct 23 19:02, byte-for-byte as it was before this milestone.

**Deliberately NOT done** (scope item 7): no phrase-specific routing patch. The only new
textual material is (a) the plural clause patterns, which capture a locality that is then
validated by the catalogue like any other, and (b) the function-word stop list inside the
composite-name validator, which contains no place name at all. No gate decides whether
evidence is retained on the strength of a new phrase.

---

## 3. Behavioural truth table

Measured on the deployed code and on this working tree, in a sealed container
(`--network none`, `DATABASE_URL=sqlite://`), against the real 211-row catalogue. `zone` is
`_extract_zone_from_text`; `clause` is `_extract_vehicle_location_zones`; `FE` is
`field_evidence._extract_vehicle_location_from_turn`.

### 3.1 The rows that change (flag ON) — 12 of 61 probed inputs

| input | zone BEFORE | zone AFTER | verdict | clause BEFORE | clause AFTER |
|---|---|---|---|---|---|
| `mar del plata` | `Sur / La Plata` | **None** | UNSUPPORTED | — | — |
| `en mar del plata` | `Sur / La Plata` | **None** | UNSUPPORTED | — | — |
| `el auto esta en mar del plata` | `Sur / La Plata` | **None** | UNSUPPORTED | `Sur / La Plata` | **[]** |
| `en san miguel del monte` | `Oeste / San Miguel` | **None** | UNRESOLVED | — | — |
| `el auto esta en san miguel del monte` | `Oeste / San Miguel` | **None** | UNRESOLVED | `Oeste / San Miguel` | **[]** |
| `en san nicolas` | `CABA / San Nicolás` | **None** | AMBIGUOUS | — | — |
| `en San Nicolás` | `CABA / San Nicolás` | **None** | AMBIGUOUS | — | — |
| `el auto esta en san nicolas` | `CABA / San Nicolás` | **None** | AMBIGUOUS | `CABA / San Nicolás` | **[]** |
| `los autos estan en flores` | `CABA / Flores` | `CABA / Flores` | RESOLVED | **[]** | **`CABA / Flores`** |
| `los autos están en Flores` | `CABA / Flores` | `CABA / Flores` | RESOLVED | **[]** | **`CABA / Flores`** |
| `los vehiculos estan en Flores` | `CABA / Flores` | `CABA / Flores` | RESOLVED | **[]** | **`CABA / Flores`** |
| `las camionetas estan en Flores` | `CABA / Flores` | `CABA / Flores` | RESOLVED | **[]** | **`CABA / Flores`** |

The last four are scope item 4: the locality was already found by the *bare-locality*
fallback, which writes it as a thread-level buffer with no role. Reading the clause gives
the same locality its **role** — vehicle location — and therefore its correct target.

### 3.2 The rows that do not change (49 of 61), a sample

| input | before = after | why it must not change |
|---|---|---|
| `en flores` / `flores` / `Flores` | `CABA / Flores` | the Wild's own answer |
| `en floresta` | `CABA / Floresta` | the near-miss neighbour |
| `en la plata` / `la plata` / `en plata` | `Sur / La Plata` | La Plata itself is a real zone |
| `en paternal` / `en la paternal` | `CABA / La Paternal` | L4.7W5-F5 article rule |
| `en boca` / `en la boca` | `CABA / La Boca` | article rule, positive case |
| `me mordi la boca` | `None` | article rule, negative control |
| `soy de Tigre` / `vivo en Tigre` | `Norte / Tigre` | "de" before a locality is ordinary Spanish |
| `en lomas de zamora`, `en villa del parque`, `en paso del rey`, `en 3 de febrero`, `en belen de escobar`, `en remedios de escalada`, `en exaltacion de la cruz`, `en lomas del mirador` | unchanged | catalogue names that *contain* connectors — the composite rule must let them through |
| `en cordoba` / `en rosario` / `en neuquen` / `en bahia blanca` | `None` | already correct |
| `en villurquiza` / `en VillaUrquiza` | `CABA / Villa Urquiza` | M21.2.15 ASR/compact match |
| `en dock sud` / `en doc sud` | `Sur / Dock Sud` | the alias fast-path |
| `en capital federal` / `en caba` | `CABA / CABA` | the CABA synonym fast-path |
| `en oeste` / `zona oeste` | `Oeste / —` | the bare-group sentinel |

### 3.3 The whole catalogue

**211 localities × 3 phrasings** (`<name>`, `en <name>`, `el auto esta en <name>`), flag ON
vs flag OFF: **210 unchanged, 1 changed** — `CABA / San Nicolás`, which is the declared
cross-coverage-ambiguous name and now resolves to nothing unless the customer also names
the area (`"en san nicolas, caba"` → `CABA / San Nicolás`, measured). That single
regression is the intended cost of the finding, and it is the only one.

### 3.4 Flag OFF, against the deployed code

| instrument | differences |
|---|---|
| 211 catalogue localities × 3 phrasings (633 readings) | **0** |
| 61 adversarial inputs, `_extract_zone_from_text` | **0** |
| 61 adversarial inputs, `_extract_vehicle_location_zones` | **0** |
| 61 adversarial inputs, `field_evidence` clause extraction | **0** |

### 3.5 OWNER-REVIEW-R1 — the two rows the corrections change (flag ON)

The unit-level readings in §3.1–§3.4 are unchanged by R1: neither correction touches the
resolver, the clause patterns or the coverage list. What changes is two whole-turn outcomes,
and both move from a *refusal* to the *right answer* — the corrections only ever admit more,
never less.

| situation | flag ON **before** R1 | flag ON **after** R1 | flag OFF (both) |
|---|---|---|---|
| Location Flow form: area `CABA` + locality `San Nicolás` | warm handoff, `needs_human=True`, **no zone**, no quote | **RESOLVED** → `('CABA','San Nicolás')`, quoted $140.000, stays automated | `('CABA','San Nicolás')`, quoted — the inherited answer |
| already quoted at CABA/Flores: "perdón, el auto está en San Nicolás" | `DOMAIN_NOT_RELEVANT` → **re-quotes the OLD locality**, $140.000 "en Flores" | **LOCATION_FLOW_REQUIRED** → the Location Flow, no price sentence, stored zone untouched | writes `('CABA','San Nicolás')` over Flores and re-quotes |
| Location Flow form: area `SUR` + locality `San Nicolás` | warm handoff, no zone | **unchanged** — warm handoff, no zone (a disagreeing selection settles nothing) | `('CABA','San Nicolás')`, quoted |
| Location Flow form: any area + `Mar del Plata` | coverage response, no zone | **unchanged** — coverage outranks every selection | `('Sur','La Plata')`, quoted $320.000 |

---

## 4. Migrations

**None. No schema change of any kind.** No table, column, index, constraint, trigger or
Alembic revision was added, altered or removed. `crm_test` is still at
`20261022_operator_notifications`. The new fixture is a test file, not a database object.

This is deliberate: R3 is a read-path correction. The data-model work the review proposed
(`whatsapp_thread_evidence`, `whatsapp_outstanding_questions`) belongs to R1 and R2.

**OWNER-REVIEW-R1 adds no migration either.** Its per-turn state (`_turn_locality_ambiguity`)
is process memory on the engine instance, reset in `handle()` alongside the fourteen other
`_turn_clarification_*` fields; it is never persisted. Re-verified: `crm_test` is still at
`20261022_operator_notifications` and every row count is unchanged (§8).

---

## 5. Test results

Runner (the project's sealed runner, no network, no provider, no live database):

```
docker run --rm --network none -e DATABASE_URL=sqlite:// -e PYTHONDONTWRITEBYTECODE=1 \
  -v <tree>:/work:ro -w /work/backend ridecheck-crm-test:l4.7b2 \
  python -m pytest /work/tests -q -p no:cacheprovider -rf
```

### 5.1 The new suite

`tests/test_w1f_w01_r3_locality_safety.py` + `tests/test_w1f_w01_r3_r1_fullturn_ambiguity.py`
— **82 passed, 507 subtests passed** (8.2 s), of which the R1 module is **27 passed,
17 subtests** on its own (4.0 s).

| group | tests | what it proves |
|---|---|---|
| R3-A | 6 | the two consumers hold the **same objects** (`is`, not `==`), the alt-location pattern stays last for `field_evidence`'s `[:-1]`/`[-1]` contract, and neither file re-defines a clause pattern (source assertion) |
| R3-B | 3 | flag OFF reproduces the inherited answers **including the three defects** |
| R3-C | 16 | Mar del Plata (5 phrasings), the generic composite rule on a locality no list names, the 8 catalogue connector names, origin phrasing, La Plata itself, San Nicolás ambiguity + disambiguation + the province homonym, unsupported cities, unknown places, La Boca negative control, Flores vs Floresta, La Paternal, no model call |
| R3-D | 7 | **zero** thread-zone and **zero** candidate-zone writes for a refused locality; the coverage outcome is the existing one (`needs_human`, "CABA y GBA"); **the write HELPER writes nothing and answers nothing for an ambiguous locality — the turn's answer is decided later, by the one clarification authority, and is proven in R3-J** (corrected by OWNER-REVIEW-R1: the original wording described the helper and read as though it described the turn); the replay path never sends; supported localities still write; flag OFF writes the inherited wrong zone |
| R3-E | 6 | the Flow form answer: unsupported → coverage response and no zone; ambiguous **with a disagreeing area selection** → warm handoff and no zone; ambiguous **with an agreeing area selection** → resolves (R1RV-01, the corrected assertion); no area selection rescues an out-of-coverage city; supported → unchanged; flag OFF → inherited |
| R3-F | 4 | `PricingService.quote` is **never called** after a refusal (all three defect inputs), no quote is possible, the booking precondition cannot be met, and a supported locality does reach pricing with the right pair |
| R3-G | 6 | 8 plural phrasings resolve; 6 singular phrasings unchanged; the plural clause writes the **candidate** zone; a plural clause for an unsupported place still writes nothing; `field_evidence` reads plural clauses and refuses what the text disqualifies |
| R3-H | 2 | the whole catalogue, both flag postures, three phrasings each |
| R3-I | 3 | the whole turn through `_process_text`: unsupported → no zone anywhere + `needs_human`; supported → candidate zone written; flag OFF → the inherited wrong zone |
| **R3-J** (R1) | **27** | **the COMPLETE turn, `handle()` end to end**, in `test_w1f_w01_r3_r1_fullturn_ambiguity.py`: the five enumerated ambiguous situations and both disambiguated inputs; the form with an agreeing, a disagreeing and an unsupported answer; the ambiguous location CORRECTION; Mar del Plata and San Miguel del Monte; the flag-OFF defect reproduction for all three paths; per-turn memory; no place name in executable code (AST); nothing bookable after any refusal |

### 5.2 Full suite — exact failure-identity delta

| run | failed | passed | skipped | errors | subtests | failure identities |
|---|---|---|---|---|---|---|
| baseline, pristine `git archive 6a14cdb` | 62 | 6 658 | 646 | 9 | 6 203 | 71 |
| this tree, flag **OFF** (default) | 62 | 6 740 | 646 | 9 | 6 715 | 71 |
| this tree, `LOCALITY_RESOLUTION_SAFETY_ENABLED=true` | 62 | 6 740 | 646 | 9 | 6 715 | 71 |

**Identity delta in both postures: NEW = [] · GONE = []**, and the two candidate identity
sets are byte-identical to each other (`diff` empty). The 71 identities are the same in all
three runs: 61 `FAILED`, 1 `SUBFAILED`
(`test_or_73_no_booking_revision_is_modified`, subtest `_withdraw_dependent_proposals` —
this is the 62nd of pytest's "62 failed") and 9 `ERROR` (all nine in
`test_l4_2_clean_slate.py`). The +82 passed and +512 subtests are this milestone's own tests.

Re-measured for OWNER-REVIEW-R1 with the full short summary captured and `ERROR`/`SUBFAILED`
lines counted alongside `FAILED`. That is why the identity count reads 71 here and 62 in the
R3 checkpoint — a fuller instrument over the same set, not a changed set. The delta is
computed baseline-to-candidate inside this one instrument, so it is sound either way.

Honest reading of the flag-ON row: most existing suites build their engine with a
`MagicMock` settings object, so `getattr(..., "locality_resolution_safety_enabled", False)
is True` is False there and they run in the OFF posture regardless of the environment. The
flag-ON row therefore proves **no collateral damage**, not coverage; the ON posture is
exercised deliberately, by the new suite, in every group.

**One real finding during the work, and it is the suite's own guard working.** The first
implementation added a fourth `if not self._outbound_armed(): return self._blocked_outbound(...)`
exit to `conversation_engine.py`, and three existing structural tests failed immediately
(`test_g35f_01_every_blocked_exit_derives_its_transport_from_its_path`,
`test_bc_35_the_seven_blocked_transports_are_still_truthful`,
`test_g35e2_23_every_early_exit_records_the_dispatch`) because they count those exits. The
ambiguous-form branch was rewritten to use the exit the function already had — the warm
handoff — and the three went green. **R3 adds no new outbound transport site.**

### 5.3 Performance

Minimum of 5 × 200 iterations × 10 representative inputs, µs per call, same sealed image
and the same in-memory catalogue for all three columns:

| path | baseline `6a14cdb` | this tree, flag OFF | this tree, flag ON |
|---|---|---|---|
| `_extract_zone_from_text` | 3 554 | 3 565 (+0.3 %) | 4 161 (+17 %) |
| `_extract_vehicle_location_zones` | 499 | 498 (0 %) | 1 025 (+105 %) |
| model calls | 0 | 0 | **0** |

Reading them honestly: flag OFF is free. Flag ON costs **+0.6 ms** on a locality resolution
and **+0.5 ms** on a clause extraction — the clause path doubles because the pattern set
goes from 7 to 11 and more matches then reach the resolver. The dominant cost in all three
columns is the inherited per-call `SELECT` of 211 catalogue rows; **R3 adds no query** (the
validators read the snapshot the matcher already loaded) and **no model call**. Against the
Wild's measured CE time of 3 361–8 850 ms per turn, the added cost is under 0.03 % of a
turn. The per-call catalogue query remains an obvious optimisation and is **not** taken
here: caching it would change when a catalogue edit becomes visible, which is not R3's
decision to make.

**OWNER-REVIEW-R1 re-measurement.** Re-run on a loaded host with a different text mix (211
catalogue localities × 2 phrasings + 60 adversarial), 9 repetitions, best-of, interleaved
baseline/candidate. The honest reading is that the host's own noise band exceeds the
flag-OFF effect: the *pristine baseline* measured 2 107 µs and 3 207 µs for byte-identical
code in two consecutive runs. Within a single process — the only fair comparison — flag ON
costs **+0.6 to +1.9 ms** per locality resolution (2 506→3 072, 2 171→4 155, 2 441→3 632 µs)
and **+0.3 to +0.5 ms** per clause extraction, and flag OFF is not separable from baseline.
R1's own two corrections add **one normalised string comparison** on a refused form answer
and **one attribute read** per turn: still **no new database query and no model call**. The
§5.3 table above stands as the quieter-host measurement; this is the conservative one.


### 5.4 OWNER-REVIEW-R1 — the measured transcripts

Flag ON unless noted. Captured from `handle()` executing end to end: `result` is the
`ConversationHandleOut.action`, `route/reason` the one clarification decision, `texts`/`flows`
what transport was handed, `pricing` every `PricingService.quote` call with its arguments.

```
--- (a) new conversation, no vehicle: "en San Nicolás"
    result 'replied'   route None
    texts  ['¡Hola! Somos Ridecheck y nos especializamos en revisiones pre-compra de autos.
             ¿Estás pensando en comprar un vehículo y querés que lo revisemos antes de decidir?']
    flows  []          pricing []
    zone   thread (None,None) -> (None,None)   no candidate
    needs_human False -> False   stage QUALIFYING -> QUALIFYING
    intent None -> AWAITING_QUALIFICATION      attempts [] -> []
    revisions 0 -> 0   semantic model calls 0

--- (b) vehicle known, awaiting location: "en San Nicolás"
    result 'replied'   route LOCATION_FLOW_REQUIRED   reason LOCATION_UNRESOLVED
    texts  []
    flows  [{'flow_id': 'FLOW_LOCATION', 'screen': 'LOCATION_DETAILS',
             'body': 'Para calcular correctamente los viáticos de la revisión, te dejo un
                      formulario breve para indicarnos dónde se encuentra el vehículo.
                      Realizamos revisiones en CABA y GBA.'}]
    pricing []
    zone   thread (None,None) -> (None,None)   cand (None,None) -> (None,None)
    needs_human False -> False   stage QUALIFYING -> QUALIFYING
    attempts [] -> [('LOCATION', 'DISPATCHED')]        revisions 0 -> 0

--- (c) "el auto está en San Nicolás"      — identical to (b); intent -> PREPURCHASE_INSPECTION

--- (d) form {'zona_general': 'CABA', 'localidad': 'San Nicolás'}
    AFTER  R1RV-01: texts ['Genial! La cotización … en San Nicolás es de $140.000 …']
                    zone thread -> ('CABA','San Nicolás')   needs_human False
                    pricing [{'AUTO','CABA','San Nicolás'}]
    BEFORE R1RV-01: texts ['Gracias por completar el formulario. Un agente de Ridecheck
                            te va a contactar a la brevedad …']
                    zone thread (None,None)   needs_human True   pricing []

--- (d') form {'zona_general': 'SUR', 'localidad': 'San Nicolás'}   (unchanged by R1)
    texts ['Gracias por completar el formulario. Un agente … te va a contactar …']
    zone thread (None,None)   needs_human True   pricing []

--- (e1) "el auto está en San Nicolás, CABA"
    result 'replied'   route NO_CLARIFICATION_REQUIRED
    texts ['Genial! La cotización para la revisión del Toyota Corolla 2024 en San Nicolás
            es de $140.000. Si te parece bien, podemos avanzar.']
    cand (None,None) -> ('CABA','San Nicolás')   stage QUALIFYING -> QUOTED
    pricing [{'AUTO','CABA','San Nicolás'} ×2]   attempts [] -> []

--- (e2) "el auto está en San Nicolás, Capital"   — identical to (e1)

--- (j) already quoted at CABA/Flores: "perdón, el auto está en San Nicolás"
    AFTER  R1RV-02: route LOCATION_FLOW_REQUIRED / LOCATION_UNRESOLVED
                    texts []   flows [the Location Flow]
                    cand ('CABA','Flores') UNCHANGED   stage QUOTED -> QUOTED
                    pricing [{'AUTO','CABA','Flores'}]  ← the stored pair; nothing sent
                    attempts [] -> [('LOCATION','DISPATCHED')]
    BEFORE R1RV-02: route NO_CLARIFICATION_REQUIRED / DOMAIN_NOT_RELEVANT
                    texts ['Genial! La cotización … en Flores es de $140.000 …']

--- (f1) "en Mar del Plata"   /   (f2) "el auto está en Mar del Plata"
    result 'replied'   texts ['Por el momento trabajamos en CABA y GBA. Para una revisión
            fuera de esa zona tenemos que evaluar la logística. Te confirmamos si podemos
            hacerlo.']
    flows [] pricing []   zone (None,None) everywhere   needs_human False -> True (lead too)

--- (f3) "en San Miguel del Monte"
    result 'replied'   route LOCATION_FLOW_REQUIRED / LOCATION_UNRESOLVED
    flows [the Location Flow]   pricing []   zone (None,None)   needs_human False

--- flag OFF, (b): the deployed defect, reproduced
    texts ['Genial! La cotización … en San Nicolás es de $140.000 …']
    cand -> ('CABA','San Nicolás')   pricing [{'AUTO','CABA','San Nicolás'} ×2]
--- flag OFF, (f2): the deployed defect, reproduced
    cand -> ('Sur','La Plata')
```

The newline inside the coverage response is folded by `_validate_outbound_text`, which is
why the tests compare on collapsed whitespace rather than against the constant byte for
byte — asserting the constant would assert the validator's absence.

---

## 6. Mutation results

Each mutant is a one-line change to this tree, run against the new suite only, in a
sealed container. **6 planted, 6 killed.** The three the milestone required are the first
three.

| # | mutant | result | killed by |
|---|---|---|---|
| m1 | **substring acceptance returns** — `composite_name_conflict` never refuses | **KILLED** (2 failed) | `test_composite_name_is_refused_without_any_city_list`, `test_no_quote_is_possible_after_a_refusal` |
| m2 | **coverage validation moved after persistence** — the pre-gate in `_apply_zone_from_text` is disabled, leaving the inherited order | **KILLED** (3 failed) | `test_unsupported_locality_writes_no_thread_zone`, `test_coverage_outcome_is_the_existing_one`, `test_unsupported_turn_leaves_no_zone_on_the_thread` |
| m3 | **plural-clause handling removed** | **KILLED** (11 failed) | `test_plural_clauses_resolve_with_safety_on`, `test_plural_clause_writes_the_candidate_zone`, `test_field_evidence_reads_plural_clauses_too`, `test_plural_patterns_are_absent_with_safety_off` |
| m4 | the cross-coverage ambiguity set is emptied | **KILLED** (9 failed) | `test_ambiguous_locality_writes_nothing_and_lets_the_turn_continue`, `test_ambiguous_form_answer_persists_no_zone`, `test_every_catalogue_locality_resolves_to_itself`, + 3 |
| m5 | the de-duplication is undone — `field_evidence` re-defines its own uncertainty regex | **KILLED** (1 failed) | `test_uncertainty_and_origin_are_the_same_object` |
| m6 | the resolver wrapper stops consulting the verdict | **KILLED** (13 failed) | `test_mar_del_plata_never_resolves_to_la_plata`, `test_san_nicolas_is_ambiguous_not_caba`, `test_explicit_clause_for_mar_del_plata_yields_no_zone`, + 10 |

Every mutant tree was copied, mutated, run and **deleted**; the pre-existing `m01_*…m14_*`
campaign in the same scratchpad directory belongs to the earlier G3-7R-R3 work and was left
untouched.

### 6.1 OWNER-REVIEW-R1 — re-run and extended (13 planted, 12 killed, 1 equivalent)

The campaign was re-run from scratch against **both** R3 suites, and six mutants were added
for the two corrections. Anchors differ slightly from §6 because the code moved.

| # | mutant | result | killed by |
|---|---|---|---|
| M1 | **substring acceptance returns** — the resolver hands back the raw matcher result, skipping the verdict entirely | **KILLED** | `test_mar_del_plata_never_resolves_to_la_plata`, `test_san_nicolas_is_ambiguous_not_caba`, + the R3-J turn tests |
| M1b | the refused verdict is returned instead of `None` | **SURVIVED — provably equivalent** | `_locality_verdict` returns `(verdict, None, reason)` for *every* refused verdict, so skipping the branch returns the same `None`. Kept in the campaign as the equivalence control: a surviving mutant that changes no behaviour is evidence the tests are measuring behaviour, not shape |
| M2 | coverage/ambiguity validated **after** persistence (the inherited order) | **KILLED** (5 failed) | the R3-D write-path tests and `test_j14` |
| M3 | plural-clause handling removed | **KILLED** (11 failed) | the R3-G group |
| M4 | the composite-name rule never refuses | **KILLED** (485 failed) | the whole-catalogue group collapses |
| M5 | the cross-coverage declaration is emptied | **KILLED** (21 failed) | the ambiguity groups, R3-C and R3-J alike |
| M6 | `field_evidence` stops dropping a text-disqualified locality | **KILLED** (2 failed) | `test_field_evidence_refuses_what_the_text_disqualifies` |
| **M7** | **the area hint is trusted without agreeing** with the locality's own area (R1RV-01 over-corrected) | **KILLED** (3 failed) | `test_j10_a_conflicting_area_selection_settles_nothing`, `test_ambiguous_form_answer_persists_no_zone`, `test_j22` |
| **M8** | **R1RV-01 reverted** — the form guard ignores the area dropdown | **KILLED** (2 failed) | `test_j09_area_selection_settles_the_ambiguity`, `test_an_agreeing_area_selection_settles_the_ambiguity` |
| **M9** | **R1RV-02 reverted** — the ambiguity verdict is never recorded | **KILLED** (2 failed) | `test_j04_an_ambiguous_correction_is_asked_about_not_ignored`, `test_j24` |
| **M10** | the ambiguity no longer un-resolves the location for the decision | **KILLED** (1 failed) | `test_j04` |
| **M11** | the ambiguity no longer makes the location family relevant | **KILLED** (1 failed) | `test_j04` |
| **M12** | the per-turn ambiguity memory is never cleared | **KILLED** (1 failed) | `test_j24_the_ambiguity_is_per_turn_memory` |

M7 is the one worth pausing on: it is the *over-correction* of R1RV-01 — trusting the
dropdown unconditionally — and it is the mutant most likely to have shipped if the fix had
been written to make the failing case pass rather than to state a rule. It dies on the
conflicting-selection test, which exists precisely because "Sur" + a CABA locality is two
answers that cannot both be true.

---

## 7. Compatibility and rollback

**Compatibility**

- **Flag OFF is the deployed behaviour, measured, not asserted**: 633 catalogue readings +
  183 adversarial readings across three instruments, **0 differences**, and a whole-suite
  identity set identical to the pristine baseline.
- **Signatures are additive.** `resolve_field_evidence`, `_resolve_inspection_location` and
  `_extract_vehicle_location_from_turn` gained keyword-only parameters that default to the
  inherited behaviour, so any caller not updated behaves exactly as before. The five CE call
  sites were updated; a sixth caller anywhere would still be correct, only unguarded.
- **Module-level names are preserved.** `_VEHICLE_LOCATION_CLAUSE_PATTERNS`,
  `_LOCATION_UNCERTAINTY_RE`, `_CUSTOMER_ORIGIN_RE`, `_ORIGIN_CLAUSE_SPLIT_RE`,
  `_OUTSIDE_COVERAGE_CITIES`, `_is_outside_coverage`, `_strip_accents`, `_ARTICLE_PREFIX_RE`,
  `_LOCATIVE_MARKERS`, `_locality_in_context`, `_has_customer_origin_clause` and
  `_strip_customer_origin_clauses` all still exist in `conversation_engine`, now as bindings
  to the leaf module. 20+ existing suites reference them and all still pass.
- **`_extract_zone_from_text` keeps its signature and its contract** (zone or `None`). The
  inherited matcher is intact as `_match_zone_in_text`; the only edit inside it is one line
  that stores the catalogue list it had already loaded.
- **The alt-location contract is preserved**: `clause_patterns()` always returns the
  contradiction pattern last, with or without the plural patterns, which is what
  `field_evidence`'s `[:-1]` / `[-1]` indexing depends on. Asserted in R3-A.

Added by OWNER-REVIEW-R1:

- **Both corrections live behind the same single flag.** `area_hint` is only ever passed by
  the form guard, which is itself inside `if self._locality_safety_on()`, and
  `_turn_locality_ambiguity` is only ever set inside the same guard — so with the flag off
  it is permanently `None` and the two reads in the clarification decision are `bool(None)`.
  There is no second switch to remember and no new configuration surface.
- **`disambiguated_by_text` stayed backward-compatible**: `area_hint` is keyword-only with
  default `None`, and the two positional arguments are unchanged, so the existing callers in
  `field_evidence` and in `_locality_verdict`'s text path are untouched.
- **`clarification_router.clarification_decision` is byte-identical.** The pure router was
  not modified; only the two facts CE computes and passes it. That keeps the owner's policy
  ladder — human ownership, correction confirmation, vehicle before location, spent attempts,
  outstanding Flows — exactly where it was.
- **One existing assertion was deliberately corrected**, not deleted:
  `test_ambiguous_form_answer_persists_no_zone` now asserts the refusal for a *disagreeing*
  area selection and a sibling test asserts resolution for an agreeing one. It is the only
  pre-existing assertion this round changed, and §0 records why.

**Rollback**

| level | action | effect |
|---|---|---|
| runtime | `LOCALITY_RESOLUTION_SAFETY_ENABLED=false` (or unset) | the inherited behaviour returns on the same image, with no restart of anything else and no data change |
| code | revert the four modified files and delete the four new ones | no migration to unwind, nothing to clean in any database |
| data | — | **nothing to roll back**: no schema object and no row was created, and a refused locality writes nothing, so there is no partial state to repair |

**What a rollback re-opens, stated plainly:** W1FRV-02 and W1FRV-03. With the flag off,
`"en san nicolas"` is a priced, bookable CABA barrio and `"en mar del plata"` is La Plata.
That is the current production behaviour and it does not change until the flag is enabled,
which is a separate owner checkpoint.

---

## 8. Proof that the live runtime and `crm_test` were untouched

| check | evidence |
|---|---|
| the running backend never saw this code | container `b340087e4cee`, image `ridecheck-crm-backend:prewild-6a14cdb`, **restart count 0**, started 2026-10-26T15:00:30Z (before this milestone). Its only bind mounts are `/opt/ridecheck-secrets → /run/secrets` (ro) and `/opt/ridecheck-crm-forensics → /run/forensics`; **the worktree is not mounted into it**, so editing files on disk cannot reach the running process |
| no deploy, no build | no `docker build`, no image created or tagged, no compose action. The only containers that ran were throwaway `--rm` test/probe containers from the pre-existing sealed image `ridecheck-crm-test:l4.7b2` |
| flags unchanged | `OUTBOUND_ENABLED=false`, `SMART_BOOKING_CE_ENABLED=false`, `OPERATOR_WHATSAPP_NOTIFY_ENABLED=false`, `CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=false`, `HYBRID_TRACE_ENABLED=true`. `LOCALITY_RESOLUTION_SAFETY_ENABLED` is **not set anywhere outside the test runs** — not in the container, not in a compose file, not in `.env` |
| `crm_test` unchanged | before and after this milestone, identical: messages **52** (max id **6239**), hybrid traces **12**, ai_events **7**, candidates **1**, revisions **61**, booking_commands **8**, operator_notifications **1**, security_events **734**, viaticos_zones **211**; `alembic_version = 20261022_operator_notifications` |
| nothing was written to any live database | every test and probe ran against **in-memory SQLite** inside a `--network none` container, which cannot reach the postgres container at all. The one read of `crm_test` was the catalogue export for the fixture, plus the before/after counts above — all `SELECT` |
| no external call | no Meta, OpenAI, SMTP, Resend, Maps or n8n call. `--network none` on every container; `model calls = 0` recorded by the probes; no message was sent |
| no git action | no commit, no branch, no tag, no stash, no push. `git status` shows the four modified files, the four new untracked files (three after R3, the R1 test module added here), and the pre-existing `docs/operations/RECOVERY_MANIFEST.md` modification, unchanged |
| the Meta template | untouched. No parameter, name, language or body was read, edited or submitted. `{{10}}` still renders `/whatsapp/thread/{thread_id}` from `operator_notification.py:230` |
| **re-verified after OWNER-REVIEW-R1** | the same container `b340087e4cee`, image `prewild-6a14cdb`, **restart count 0**, same start time `2026-10-26T15:00:30Z`, still no worktree mount, still `OUTBOUND_ENABLED=false`, still no `LOCALITY_RESOLUTION_SAFETY_ENABLED` in its environment. `crm_test` read-only again: messages **52** (max id **6239**), traces **12**, ai_events **7**, candidates **1**, zones **211**, security_events **734**, `alembic_version = 20261022_operator_notifications` — **every counter identical**. `git log --oneline -1` is still `6a14cdb`; the working tree now shows the same four modified files and **four** untracked files (the new R1 test module). `docs/operations/RECOVERY_MANIFEST.md` is still 20 421 bytes dated Oct 23 19:02 |
| the one env var R1 touched, and how | the R1 full-turn tests need the kill switch armed to observe a real dispatch. It is armed **per turn inside `patch.dict`**, never at module scope and never in any live process — see §0.5 for the defect that taught me the difference |

---

## 9. What R3 does not fix

- **W1F-W-01 itself remains OPEN.** R3 is the independent, wrong-price half of the review's
  remediation. The conversational defect — an answer to the system's own question being
  discarded because the turn exits before admission — is R1 + R2.
- **`field_evidence` can still report a locality the catalogue would refuse.** It is pure
  and DB-free by contract (ER-10/ER-13), so it applies the text-level validators only:
  `"el auto esta en san miguel del monte"` still yields the raw locality
  `"san miguel del monte"` in its snapshot, while the CE refuses to turn it into a zone.
  No write, no quote and no booking can result — measured in R3-F — but the snapshot's
  `location_known()` is text-level truth, not catalogue truth. The central fix is R1's
  admission step, where one validator serves both producers. Carried.
- **`"en flores capital"` resolves to `CABA / Capital`, not `CABA / Flores`** (longest-first
  containment picks the longer catalogue name). Inherited, unchanged by R3, and harmless
  today: both rows are CABA with **0** viáticos, so the quote and the agenda are identical.
  Recorded as an observation, not fixed, because changing the matcher's precedence is a
  wider change than R3's scope and has no measured consequence.
- **The ambiguity set has exactly one entry.** `san nicolas`, with its reason. Candidates I
  considered and did not add, for want of owner or geographic confirmation: Belgrano, San
  Martín, Mercedes, Olivos. Every entry costs a real customer one extra question, so the set
  should grow only on evidence. A test asserts every entry carries a justification, and
  another asserts no catalogue name silently overlaps the declared out-of-coverage city
  list.
- **A burst naming two places, one of them unsupported** ("está en Flores, no en Mar del
  Plata") takes the coverage outcome, because `is_outside_coverage` reads the whole text.
  That is the inherited predicate on the inherited input, now evaluated earlier; R3 does not
  make it better or worse. The review's `W1F-C-09` case belongs to R1's per-claim admission.
- **The per-call catalogue query** (211 rows on every locality resolution) is untouched.

Added by OWNER-REVIEW-R1:

- **A brand-new conversation naming only a locality still drops it** (§0.3). Layer F owns
  that turn and returns before the zone block. Nothing is persisted, nothing is priced, the
  customer is answered, and the behaviour is identical with the flag off — but the locality
  is not carried forward. This is W1FRV-01 and it closes in R1 + R2.
- **Which reading wins when a turn names two catalogue localities is still the matcher's
  longest-first order**, and that order decides which locality the verdict then judges.
  Measured on both orderings of "yo soy de San Nicolás pero el auto está en Caballito": the
  matcher picks Caballito and the turn quotes it correctly (`test_j17`). So there is no
  measured defect — but the guarantee rests on the matcher's precedence rather than on
  per-claim admission, which is R1's job. Recorded as an observation.
- **An ambiguous locality anywhere in a turn un-resolves the LOCATION family for that turn**
  (R1RV-02, by design). A customer with a known zone who mentions an ambiguous name in
  passing will therefore be asked to confirm the location. That is the conservative
  direction — it asks instead of assuming, it writes nothing and it costs one bounded
  attempt — and it is the price of never re-quoting a contradicted zone. If owner policy
  prefers the opposite trade for a *mention* as against a *statement*, distinguishing the
  two needs the per-claim role that R1 admits; it cannot be done honestly from raw text here.

---

## 10. Owner decision required

1. **Review and authorise the commit.** Nothing is committed. Suggested message subject:
   `fix(prelaunch): W1F-W-01 R3 — an unsupported or ambiguous locality is never a zone`.
   The R1 corrections are part of the same change set; no separate commit is proposed.
2. **Decide the flag.** The defects this closes are live with the flag off. Enabling
   `LOCALITY_RESOLUTION_SAFETY_ENABLED=true` needs a build + controlled deployment
   checkpoint of its own; until then W1FRV-02 and W1FRV-03 remain open in the runtime.
   Launch is 2026-11-01.
3. **Confirm the one intended regression:** a customer who means the CABA barrio
   San Nicolás must now also name CABA/capital, **or answer the Location Flow's area
   dropdown with CABA**, or they will be asked. This is the only behaviour change affecting
   a supported locality, and after OWNER-REVIEW-R1 the loop closes — the question we ask is
   the one whose answer resolves it (§0.1 row **d**).
4. **Note what OWNER-REVIEW-R1 did NOT close:** a brand-new conversation that names only an
   ambiguous locality and no vehicle is still answered with the qualification question and
   the locality is dropped (§0.3). It persists nothing, prices nothing and always replies,
   it is identical with the flag off, and closing it is R1 + R2 — not R3.
5. **Next milestones**, unchanged by this one: R1 evidence admission, then R2
   outstanding-question persistence. W1F-W-01 stays open until both land.

NEXT: PRELAUNCH-W1F-W01-R3-CONTROLLED-BUILD-AND-DEPLOYMENT (or R1 first) — OWNER AUTHORIZATION REQUIRED

STOP.
