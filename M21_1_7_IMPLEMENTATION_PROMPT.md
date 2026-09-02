# M21.1.7 — Consolidated Semantic Regression Pack

## Objective

Close M21.1 by proving that M21.1.1 through M21.1.6 work together as one coherent Semantic Conversation Engine.

This milestone is primarily a consolidated validation and regression milestone, not a feature-development milestone.

The purpose is to test realistic compound conversations that combine service intent and business boundaries, unsupported-service/motorcycle handling, inspectability, location semantic roles, exact/fuzzy vehicle normalization, pending fuzzy confirmation, the M21.1.5 field-evidence resolver, M21.1.6 narrative understanding, FAQ + commercial context, pricing readiness, clarification suppression, human handoff, and kill-switch behavior.

Do not redesign working subsystems merely because this milestone combines them. Only fix defects directly proven by consolidated semantic scenarios.

## Baseline

Expected branch:
`fix/m21.1.1-primary-flow-regression`

Expected HEAD:
`af694be`

Expected image:
`ridecheck-crm-backend:m21.1.6-af694be`

Before editing:

```bash
cd /opt/ridecheck-crm-release-candidate
git status --short
git branch --show-current
git rev-parse HEAD
git log -12 --oneline
```

If HEAD is later than `af694be`, audit intervening commits and work forward.

Do not reset, rebase, squash, force-checkout, or rewrite history. Stop if unrelated working-tree changes would be included.

## Safety

- Do not deploy.
- Do not push.
- Do not start n8n.
- Do not enable outbound.
- Use `crm_test` only.
- Do not connect to production `crm`.
- Do not call Meta, OpenAI, Whisper, Resend, Gmail, Google Maps, or any external service.
- AI tests must use mocks/fakes only.
- Do not modify n8n or Whisper prompts.
- Do not change pricing values, viáticos, scheduling algorithms, fuzzy thresholds, or location rules unless a failing consolidated case proves a real regression and the fix is narrowly scoped.
- Do not add schema migrations unless absolutely required; stop for owner approval first.
- Do not change prior milestone expected behavior just to make the pack green.

## Phase 1 — Audit completed M21.1 contract

Inspect and summarize the authoritative behavior implemented by:

- M21.1.1 service intent / boundaries;
- M21.1.2 inspectability;
- M21.1.3 location semantic roles;
- M21.1.4 ASR vehicle normalization;
- M21.1.5 field-evidence resolver;
- M21.1.6 narrative understanding.

Read source-of-truth docs and tests for all six milestones.

Build a matrix:

```text
Milestone
→ invariant
→ primary source file
→ test file
→ state/mutation owned
→ higher-priority rule that can override it
```

Do not change code in this phase.

## Phase 2 — Consolidated source of truth

Create:

`docs/M21_1_7_consolidated_semantic_regression.md`

Define and lock these cross-feature invariants:

### CR-1 Higher-priority deterministic boundaries always win
Motorcycle/quad/ATV/UTV manual handoff, Formulario 12 exact boundary, unsupported service, disassembled vehicle, existing needs_human/handoff, and outbound kill switch must never be overridden by narrative interpretation.

### CR-2 Whole-message meaning and deterministic evidence coexist
Narrative handles ambiguous whole-message meaning; exact deterministic evidence remains authoritative.

### CR-3 Current explicit evidence beats stale state
Across vehicle, year, location, and inspectability.

### CR-4 Candidate is commercial source of truth where confirmed
Vehicle identity/category and candidate inspection location beat stale thread fallbacks.

### CR-5 Pending evidence remains pending
Pending fuzzy vehicle, unresolved inspectability, contradictory location, ambiguous vehicle, malformed AI cannot satisfy pricing readiness.

### CR-6 Customer origin never becomes inspection location

### CR-7 Deferred interest is non-commercial unless stronger active evidence exists

### CR-8 FAQ can coexist with commercial facts
Answer FAQ and retain valid current vehicle/location evidence.

### CR-9 Contradictions are clarified, not guessed

### CR-10 One current burst, one narrative interpretation

### CR-11 Resolver refreshes after validated narrative application

### CR-12 No redundant qualification loops

### CR-13 Pricing remains downstream only
Semantic layers never invent a price.

### CR-14 Established PREPURCHASE intent alone is not stale-field authority

### CR-15 Safe failure
Malformed AI, unknown vehicle, ambiguity, or unsupported case degrades safely.

## Phase 3 — Consolidated regression pack

Create:

`tests/test_m21_1_7_consolidated_semantic_regression.py`

Use real CE paths with mocked outbound/AI where appropriate. No external calls.

Required scenarios:

CR01 Clean multi-fact quote:
`Hola, quiero revisar un Focus 2019 que está en Palermo. ¿Cuánto sale?`
Expect active PREPURCHASE, Focus, 2019, Palermo, no redundant vehicle/location ask, pricing path only with valid evidence.

CR02 Origin vs vehicle location:
`Yo vivo en Tigre pero el auto está en Palermo. Es un Corolla 2020 y quiero revisarlo.`
Expect origin Tigre, inspection Palermo, pricing uses Palermo.

CR03 Historical vehicle + correction:
`Pensaba comprar un Focus 2018, pero al final compré un Corolla 2020 y está en Villa Urquiza.`
Expect Corolla current, Focus superseded.

CR04 Historical location + current location:
`El auto estaba en Tigre pero ahora está en Palermo.`
Existing vehicle context. Expect Palermo.

CR05 Real deferred message:
`Hola por ahora estoy buscando un auto agende esto para no perderlo asijina vez q decida aviso`
Expect deferred/conversational, zero candidate mutation, location request, pricing, scheduling, Flow, or needs_human.

CR06 Deferred language overridden by active vehicle:
`Todavía estoy buscando, pero ya tengo un Focus 2019 en Palermo que quiero revisar.`
Expect active flow.

CR07 Actual non-running but accessible:
`Es un Focus 2019 en Palermo. No arranca, pero está armado, completo y se puede revisar.`
Expect progress, no repeated inspectability clarification.

CR08 Hypothetical non-running:
`Es un Focus 2019 en Palermo. ¿Qué pasa si el auto no arranca?`
Informational only; no inspectability pending state from hypothetical.

CR09 Disassembled:
`Es un Focus 2019 en Palermo, pero está desarmado. ¿Cuánto sale?`
Deterministic decline; no quote/scheduling.

CR10 Motorcycle:
`Estoy viendo una Honda CB 500 en Palermo y quiero revisarla.`
Manual RideCheck unit/handoff, no automotive quote.

CR11 Formulario 12:
`Tengo un Corolla 2020 en Palermo y necesito hacer el Formulario 12.`
Exact response:
`Nosotros realizamos revisiones precompra; no gestionamos el Formulario 12.`

CR12 High-confidence fuzzy in compound narrative:
Use M21.1.4 approved high-confidence corrupted Ford Fiesta input with year/location. Auto-accept normalized vehicle; preserve other fields; no duplicate question.

CR13 Fuzzy confirmation blocks pricing:
`Ford ksl 2019 en Palermo, cuánto sale?`
Expect `¿Es un Ford Ka?`, pending key, no candidate/price/schedule.

CR14 Fuzzy confirmation accepted:
Second turn `sí`.
Expect Ford Ka resolved once, pending cleared, continue through current approved path. Do not invent persistence semantics for fields that architecture does not persist pre-confirmation.

CR15 Fuzzy confirmation rejected:
Second turn `no, es un Ford Kuga`.
Clear old proposal, process Kuga, no Ka candidate.

CR16 Current explicit location beats stale state:
Existing Tigre. Current `El auto ahora está en Villa Urquiza.`
Expect Villa Urquiza.

CR17 Location contradiction:
`El auto está en Tigre o puede ser Villa Urquiza, no sé.`
Expect exact existing clarification:
`¿Dónde está físicamente el auto para hacer la revisión?`
No mutation/pricing/scheduling.

CR18 Vehicle contradiction:
`Creo que es un Focus o un Fiesta, no sé.`
Unresolved; no guessed candidate/price.

CR19 FAQ + vehicle/location:
`Es un Corolla 2021 en Palermo. ¿Qué revisan?`
Answer FAQ, retain/create context, no redundant vehicle/location ask.

CR20 FAQ-only fresh thread:
`¿Qué revisan?`
Informational only; no candidate/commercial field invention.

CR21 Established PREPURCHASE + fuzzy, no candidate:
last_intent PREPURCHASE_INSPECTION, no focused candidate, current `ford ksl`.
Expect Ford Ka confirmation, pending key, no candidate.

CR22 Existing candidate protected:
Focused candidate + unrelated fuzzy-looking message. No replacement/proposal.

CR23 Exact correction with existing candidate:
`No, en realidad es un Ford Kuga.`
Preserve current source-approved correction behavior; do not introduce general multi-candidate switching.

CR24 Multi-message burst:
Use actual CE burst format equivalent to:
`Es un Focus` / `2019` / `Está en Palermo` / `Cuánto sale?`
One narrative interpretation at most; resolve facts; no redundant asks.

CR25 Long voice-like narrative:
`Hola, mirá, estoy viendo un Peugeot, creo que un 3008, año 2021. Yo vivo en San Isidro pero el auto está en Villa Urquiza. El vendedor me dijo que no arranca porque está sin batería, pero está completo y se puede revisar. Quería saber cuánto cuesta.`
Expect vehicle/year/origin/location/accessibility/price intent, no redundant asks.

CR26 Vehicle correction:
`Es un Ford Ka 2020... no, perdón, me equivoqué, es un Ford Kuga 2020 y está en Palermo.`
Expect Kuga only.

CR27 Ambiguous year:
`Es un Focus, creo que 2018 o 2019, está en Palermo.`
Vehicle/location known, year unresolved, ask only what is genuinely missing under current pricing contract.

CR28 Malformed narrative AI output:
No unsafe mutation; deterministic fallback.

CR29 AI timeout/exception:
Same safe fallback.

CR30 Kill switch:
Scenario that would otherwise dispatch. Semantic processing may run; real dispatch blocked per current contract.

CR31 Existing needs_human:
No automated commercial continuation violating handoff.

CR32 No redundant vehicle question:
Confirmed candidate + follow-up. No vehicle clarification.

CR33 No redundant location question:
Confirmed candidate location + follow-up. No location fallback/Flow.

CR34 No redundant inspectability question:
Resolved accessible state + follow-up. No repeated clarification.

CR35 Full qualification readiness:
Confirmed PREPURCHASE + vehicle + year + inspection location + no inspectability blocker. Resolver says required evidence known/pricing_ready true. Semantic layer itself does not price.

## Phase 4 — Multi-turn sequence tests

SEQ01 Fuzzy → confirm → quote continuation
1. `ford ksl 2019 en Palermo`
2. `sí`
3. `cuánto sale?`
Prove pending lifecycle, one candidate, no repeated fuzzy/vehicle/location question.

SEQ02 Non-running clarification → unresolved → human
Use M21.1.2 approved behavior. First clarification, then warm handoff on repeated unresolved answer; no loop.

SEQ03 Deferred → later active
1. deferred-interest message
2. `Ahora sí, estoy viendo un Corolla 2020 en Palermo.`
First turn has no unwanted commercial mutation; second starts active flow.

SEQ04 Candidate location correction
1. candidate in Tigre
2. `Ahora el auto está en Villa Urquiza.`
3. ask price
Pricing-facing evidence uses Villa Urquiza.

SEQ05 FAQ → active vehicle
1. `¿Qué revisan?`
2. `Buenísimo, es un Focus 2019 en Palermo.`
Fresh FAQ does not block later active intent.

## Phase 5 — Regression gates

Run:

```bash
python -m pytest tests/test_m21_1_7_consolidated_semantic_regression.py -q --tb=short
```

Then all prior semantic suites:

```bash
python -m pytest   tests/test_m21_1_6_narrative_understanding.py   tests/test_m21_1_5_field_evidence_resolver.py   tests/test_m21_1_4_asr_vehicle_normalization.py   tests/test_m21_1_3_location_roles.py   tests/test_m21_1_2_vehicle_inspectability.py   tests/test_br1_intent_gate.py   tests/test_m21_1_1_service_intent_gate.py   tests/test_m21_0_1_live_path_contract.py   -q --tb=short
```

Run correct M20 seven-file gate:

```bash
python -m pytest   tests/test_m20_2_kill_switch_proof.py   tests/test_m20_4_1_zone_accent_normalization.py   tests/test_m20_4_3_blocked_dispatch.py   tests/test_m20_6b1_acceptance_no_quote.py   tests/test_m20_6c1_copy_polish.py   tests/test_m20_6d2_customer_reality.py   tests/test_m20_6d4v_vehicle_catalog.py   -q --tb=short
```

Then:

```bash
python -m pytest tests/ -m "not requires_infra" -q --tb=no
```

Compare to baseline `af694be`.

Require targeted green, prior M21 green, M20 green, zero new deterministic failing node IDs, zero newly deselected tests.

## Phase 6 — Failure triage

For each failure:
1. identify owning milestone/invariant;
2. classify test-design error, pre-existing behavior, or real cross-feature regression;
3. do not broaden fix;
4. modify smallest responsible source file;
5. retain failing case as regression test;
6. rerun failed scenario, owning suite, M21.1.7, prior M21, and M20 gate.

No broad refactors.

## Phase 7 — Semantic invariant report

Closeout should include:

```text
Invariant                          Owner       Status
Service intent / BR-1             M21.1.1     PASS
Motorcycle boundary               M21.1.1     PASS
Unsupported-service boundary      M21.1.1     PASS
Inspectability                    M21.1.2     PASS
Location roles                    M21.1.3     PASS
Fuzzy vehicle normalization       M21.1.4     PASS
Pending fuzzy lifecycle           M21.1.4     PASS
Field evidence precedence         M21.1.5     PASS
Redundant-question suppression    M21.1.5     PASS
Narrative whole-message meaning   M21.1.6     PASS
Deferred interest                 M21.1.6     PASS
Historical/correction semantics   M21.1.6     PASS
Safe AI failure                   M21.1.6     PASS
Cross-feature composition         M21.1.7     PASS
```

## Phase 8 — Review

Before commit:

```bash
git diff --check
git diff --stat
git diff
```

Report scenario count, sequence count, defects found/fixed, files changed, proof no scope expansion, test totals, deterministic failure delta, deselection delta.

Expected files:
- `docs/M21_1_7_consolidated_semantic_regression.md`
- `tests/test_m21_1_7_consolidated_semantic_regression.py`

Source files change only for proven cross-feature defects. No migration expected.

## Phase 9 — Commit

If test-only:

```bash
git add   docs/M21_1_7_consolidated_semantic_regression.md   tests/test_m21_1_7_consolidated_semantic_regression.py

git commit -m "test(M21.1.7): add consolidated semantic regression pack"
```

If meaningful source fixes are required:

```bash
git commit -m "fix(M21.1.7): close semantic cross-feature regressions"
```

Do not push. Do not use `git add .`.

## Phase 10 — Build

```bash
SHA=$(git rev-parse --short HEAD)

docker build   -t ridecheck-crm-backend:m21.1.7-${SHA}   ./backend
```

Verify clean import, all M21.1 modules present, migrations unchanged unless approved, image not deployed.

## Phase 11 — Closeout report

Write:

`/opt/ridecheck-crm/forensics/M21_1_7_consolidated_semantic_regression_closeout_20260811.md`

Return:

```text
PASS / PARTIAL PASS / FAIL

Branch:
<branch>

Baseline:
af694be

M21.1.7 commit:
<SHA>

Source of truth committed:
YES/NO

Consolidated scenarios:
<count>

Multi-turn sequences:
<count>

Cross-feature defects found:
<count>

Cross-feature defects fixed:
<count>

Source files changed:
<list or NONE>

Service intent invariants:
PASS/FAIL

Motorcycle boundary:
PASS/FAIL

Unsupported-service boundary:
PASS/FAIL

Inspectability:
PASS/FAIL

Location semantics:
PASS/FAIL

Fuzzy vehicle normalization:
PASS/FAIL

Pending fuzzy lifecycle:
PASS/FAIL

Field-evidence precedence:
PASS/FAIL

Redundant-question suppression:
PASS/FAIL

Narrative whole-message meaning:
PASS/FAIL

Deferred interest:
PASS/FAIL

Historical/correction handling:
PASS/FAIL

Safe AI failure:
PASS/FAIL

Cross-feature composition:
PASS/FAIL

CR05 real client deferred message:
PASS/FAIL

CR13 fuzzy confirmation blocks pricing:
PASS/FAIL

CR25 long voice-like multi-fact narrative:
PASS/FAIL

SEQ01 fuzzy lifecycle:
PASS/FAIL

SEQ02 inspectability escalation:
PASS/FAIL

SEQ03 deferred → later active:
PASS/FAIL

SEQ04 location correction:
PASS/FAIL

SEQ05 FAQ → active:
PASS/FAIL

Targeted M21.1.7:
<PASSED>/<FAILED>/<SKIPPED>

Prior M21 semantic suites:
<PASSED>/<FAILED>

M20 seven-file gate:
<PASSED>/<FAILED>

Deterministic baseline:
<totals>

Deterministic candidate:
<totals>

New deterministic failures:
<number and node IDs>

Newly deselected:
<number>

Schema changed:
YES/NO

Image:
<tag>

Image deployed:
NO

Runtime changed:
NO

Production touched:
NO

M21.1 semantic engine complete:
YES/NO

Safe for M21.2 Wild Conversation 2:
YES/NO

Report:
<path>
```

## Acceptance criteria for closing M21.1

M21.1 may be declared complete only if:

1. M21.1.7 consolidated pack is green.
2. All M21.1.1–M21.1.6 regression suites remain green.
3. Correct M20 seven-file gate remains green.
4. Zero new deterministic failure node IDs.
5. Zero newly deselected tests.
6. No unresolved cross-feature semantic defect remains.
7. Image is built and not deployed.
8. Production was untouched.
9. Closeout explicitly says:
   `M21.1 semantic engine complete: YES`
   `Safe for M21.2 Wild Conversation 2: YES`
