# M21.1.6 — Long Voice and Narrative Understanding

## Objective

Implement narrative-level understanding for long, messy, multi-fact customer messages after they reach the Conversation Engine.

Build on M21.1.5 Central Field-Evidence Resolver. Do not create a parallel evidence system.

Primary goals:
- understand the meaning of the whole current inbound burst, not isolated keywords;
- extract multiple relevant facts from one narrative;
- distinguish current facts from historical, hypothetical, corrected, or deferred facts;
- respect negation, contrast, and “not ready yet” language;
- avoid redundant questions when evidence is already present;
- preserve all deterministic business boundaries and safety gates;
- make long voice transcripts and messy typed messages behave like natural WhatsApp conversations.

This milestone does not change transport or transcription. n8n and Whisper remain upstream.

## Baseline

Expected branch: `fix/m21.1.1-primary-flow-regression`

Expected HEAD: `9769bf7`

Expected image: `ridecheck-crm-backend:m21.1.5-9769bf7`

Before editing:

```bash
cd /opt/ridecheck-crm-release-candidate
git status --short
git branch --show-current
git rev-parse HEAD
git log -10 --oneline
```

If HEAD is later than `9769bf7`, audit intervening commits and work forward. Do not reset, rebase, squash, force-checkout, or rewrite history.

Stop if unrelated working-tree changes would be included.

## Safety

- Do not deploy.
- Do not push.
- Do not start n8n.
- Do not enable outbound.
- Use `crm_test` only.
- Do not connect to production `crm`.
- Do not call Meta, Whisper, Resend, Gmail, Google Maps, or other external services.
- AI tests must use mocks/fakes only.
- Do not modify n8n or Whisper prompts.
- Do not change pricing, viáticos, scheduling algorithms, location rules, inspectability rules, or fuzzy vehicle thresholds.
- Do not add schema changes unless explicitly stopped for approval.
- Do not replace deterministic safety gates with AI classification.
- Do not modify prior milestone expectations merely to force green results.

## Architecture rule

Conceptual order:

```text
Current inbound burst
→ deterministic all-stage boundaries
→ BR-1 service-intent safety
→ inspectability
→ deterministic exact/fuzzy vehicle and location evidence
→ M21.1.5 field-evidence snapshot
→ M21.1.6 narrative interpretation for unresolved/compound meaning
→ validated application of extracted facts
→ resolver refresh
→ pricing / clarification / scheduling
```

Narrative AI may interpret meaning, but it must not invent prices, override higher-priority gates, fabricate vehicle/year/location, convert uncertainty into confirmation without validation, schedule directly, or mutate CRM directly.

## Phase 1 — Source audit

Audit and report:
1. current AI extraction/classification path;
2. prompts, schemas, parser, and structured-output contract;
3. `_apply_extracted` or equivalent mutation path;
4. which fields AI can currently return;
5. how `current_turn_text` / burst text is assembled;
6. how multiple inbound messages in one debounce burst are joined;
7. how audio transcript text enters CE;
8. how images/descriptions are represented;
9. deterministic gates that run before AI;
10. M21.1.5 resolver integration;
11. places where AI-extracted data is trusted without deterministic validation;
12. current handling for conversational/soft close, deferred interest, FAQ, vehicle, location, price, scheduling, and human handoff.

Also inspect existing customer-reality tests for long or compound messages.

Write a concise source map before changing code.

## Phase 2 — Source of truth

Create `docs/M21_1_6_narrative_understanding.md` with these rules.

### NU-1 — Interpret the current burst as one narrative

Example:

`Hola, estoy buscando un auto. Todavía no decidí cuál. Agendé el número para no perderlo y cuando tenga uno en vista les aviso.`

Overall meaning: `DEFERRED_INTEREST`, not `ACTIVE_QUOTE_REQUEST` merely because “estoy buscando un auto” appears.

### NU-2 — Whole-message meaning beats isolated keywords

`Estoy buscando un auto, pero todavía no tengo ninguno en vista.` → deferred/conversational, no qualification question.

`Pensaba comprar un Focus, pero al final compré un Corolla y quiero revisar ese.` → Corolla current; Focus historical/rejected.

`El auto estaba en Tigre pero ahora está en Palermo.` → Palermo current.

### NU-3 — Explicit corrections win

Correction markers include: `en realidad`, `me equivoqué`, `quise decir`, `no, es...`, `al final`, `ahora`, `finalmente`.

Corrected/current fact supersedes prior fact in the same narrative.

### NU-4 — Historical facts are not current commercial facts

Historical markers include: `antes`, `estaba`, `estuvo`, `había`, `pensaba`, `iba a`, `tenía`, `me habían dicho`.

Historical facts must not overwrite current explicit evidence.

### NU-5 — Hypothetical facts remain informational

Examples:
- `¿Qué pasa si el auto no arranca?`
- `Supongamos que está desarmado.`
- `Si estuviera en Tigre, ¿cambia el precio?`

Do not mutate candidate/location/inspectability/pricing inputs from hypothetical clauses alone.

### NU-6 — Deferred-interest / not-ready-yet intent

Recognize messages whose overall meaning is: still looking, no specific vehicle yet, saved the contact, will return later, not ready to quote/schedule.

Examples:
- `Por ahora estoy buscando un auto.`
- `Todavía no tengo ninguno en vista.`
- `Agendé esto para no perderlo.`
- `Cuando decida les aviso.`
- `Cuando encuentre uno les escribo.`

Required:
- conversational response;
- no candidate;
- no location request;
- no pricing;
- no scheduling;
- no Flow;
- no `needs_human`;
- no commercial progression caused by that turn.

Approved default copy:

`Perfecto, cuando tengas algún auto en vista escribinos y te ayudamos con la revisión.`

### NU-7 — Explicit active intent overrides deferred language

`Estoy buscando, pero ya tengo un Focus 2019 en Palermo que quiero revisar.` → active inspection flow.

`Todavía estoy comparando, pero quiero cotizar este Corolla 2020 que está en Tigre.` → active quote flow.

### NU-8 — Multiple facts in one narrative

Example:

`Es un Focus 2019, yo vivo en La Plata pero el auto está en Palermo, no arranca aunque está completo. ¿Cuánto sale?`

Expected current evidence:
- vehicle = Ford Focus;
- year = 2019;
- customer origin = La Plata;
- inspection location = Palermo;
- inspectability = assembled-accessible/non-running;
- intent = price/inspection;
- continue to quote when other pricing inputs are complete;
- do not ask again for resolved fields.

### NU-9 — Narrative output cannot bypass deterministic validation

Vehicle must pass catalog validation; location must pass M21.1.3 role/zone rules; inspectability must respect M21.1.2; intent must respect BR-1 and all-stage boundaries.

### NU-10 — Confidence/status per fact

At minimum support statuses equivalent to:
`CONFIRMED`, `LIKELY`, `UNCERTAIN`, `ABSENT`, `SUPERSEDED`, `HYPOTHETICAL`, `HISTORICAL`.

Only sufficiently supported current facts may become confirmed commercial evidence.

### NU-11 — Contradictions remain unresolved

`El auto está en Tigre o en Palermo, no sé.` → existing location clarification.

`Creo que es un Focus o un Fiesta, no sé.` → unresolved vehicle; do not guess.

### NU-12 — Resolver refresh

After validated narrative facts are applied, regenerate the M21.1.5 evidence snapshot and ask only for remaining required information.

### NU-13 — No redundant questions

A compound message containing vehicle + year + inspection location must not trigger vehicle/location questions or corresponding fallback Flows.

### NU-14 — Deterministic easy cases bypass narrative AI

Exact known vehicle, clear location, motorcycle, Formulario 12, clear disassembled vehicle, exact fuzzy-confirmation flow should continue through deterministic paths without unnecessary narrative AI calls.

### NU-15 — One narrative AI call per burst

Do not create multiple narrative AI calls for the same inbound burst.

### NU-16 — AI failure is safe

Timeout, malformed JSON, or low confidence → no unsafe mutation; fall back to existing deterministic behavior.

### NU-17 — No schema migration by default

Prefer current-turn structured results plus existing persisted candidate/thread fields. Do not persist raw narrative blobs by default.

## Phase 3 — Narrative schema

Create or extend a typed schema equivalent to:

```python
class NarrativeFact(BaseModel):
    value: str | int | bool | None
    status: Literal[
        "CONFIRMED", "LIKELY", "UNCERTAIN", "ABSENT",
        "SUPERSEDED", "HYPOTHETICAL", "HISTORICAL",
    ]
    confidence: float | None = None
    evidence: str | None = None

class NarrativeInterpretation(BaseModel):
    overall_intent: str | None
    deferred_interest: bool = False
    vehicle_make_model: NarrativeFact | None
    vehicle_year: NarrativeFact | None
    vehicle_location: NarrativeFact | None
    customer_origin: NarrativeFact | None
    inspectability: NarrativeFact | None
    asks_price: bool = False
    asks_faq: bool = False
    asks_schedule: bool = False
```

Names may follow repository conventions. Do not include pricing amounts.

## Phase 4 — Executable specification first

Create `tests/test_m21_1_6_narrative_understanding.py` using mocked AI outputs only.

Required scenarios:

### NU01 — Real deferred-interest message

Input:

`Hola por ahora estoy buscando un auto agende esto para no perderlo asijina vez q decida aviso`

Interpret the corrupted ending as the intended meaning “así una vez que decida aviso”.

Require deferred/conversational handling and zero commercial mutation.

### NU02 — Clean deferred version

`Hola, por ahora estoy buscando un auto. Agendé esto para no perderlo. Una vez que decida les aviso.`

Same behavior as NU01.

### NU03 — Deferred language but active vehicle

`Estoy buscando, pero ya tengo un Focus 2019 en Palermo que quiero revisar.`

Require active inspection flow.

### NU04 — Historical vehicle correction

`Pensaba comprar un Focus 2018, pero al final es un Corolla 2020.`

Require Corolla 2020; Focus 2018 superseded.

### NU05 — Location correction

`El auto estaba en Tigre pero ahora está en Palermo.`

Require Palermo current.

### NU06 — Hypothetical inspectability

`¿Qué pasa si el auto no arranca?`

Require informational handling; do not set inspectability clarification state.

### NU07 — Actual non-running + accessible

`No arranca, pero está armado, completo y se puede revisar.`

Require inspectability resolved; no redundant inspectability question.

### NU08 — Multi-fact quote

`Es un Focus 2019, yo vivo en La Plata pero el auto está en Palermo, no arranca aunque está completo. ¿Cuánto sale?`

Require all supported facts and no redundant vehicle/location/inspectability question.

### NU09 — Vehicle ambiguity

`Creo que es un Focus o un Fiesta, no sé.`

Require unresolved vehicle, no guessed candidate/pricing.

### NU10 — Location ambiguity

`El auto está en Tigre o en Palermo, no sé.`

Require existing location contradiction behavior.

### NU11 — Explicit correction

`Es un Ford Ka... no, perdón, es un Ford Kuga.`

Require Kuga; Ka superseded.

### NU12 — Customer origin + vehicle location

`Yo estoy en San Isidro pero el auto está en Villa Urquiza.`

Require correct role separation.

### NU13 — Deterministic easy-case bypass

`Ford Focus 2019 en Palermo, ¿cuánto sale?`

Require deterministic path when sufficient; narrative AI not invoked unnecessarily.

### NU14 — Unsupported boundary bypass

`Quiero revisar el auto pero también necesito que hagan la transferencia.`

Require existing unsupported-service gate.

### NU15 — Motorcycle bypass

`Estoy viendo una Honda CB 500 y quiero revisarla.`

Require motorcycle handoff.

### NU16 — Malformed AI result

Require zero unsafe mutation and deterministic fallback.

### NU17 — AI timeout/error

Require zero unsafe mutation and deterministic fallback.

### NU18 — Resolver refresh

Narrative resolves previously missing vehicle/location. Require refreshed M21.1.5 snapshot to suppress fallback questions.

### NU19 — FAQ + facts

`Es un Corolla 2021 en Palermo, ¿qué revisan?`

Require FAQ answer plus retained vehicle/location context.

### NU20 — Soft close after prior inspection context

`Gracias, todavía no decidí. Cuando tenga uno en vista les escribo.`

Require conversational/deferred response; no new candidate or Flow.

### NU21 — Multi-message burst

Use the exact `current_turn_text` format produced when a burst semantically contains:

`Es un Focus.` / `2019.` / `Está en Palermo.` / `¿Cuánto sale?`

Require one narrative interpretation and no redundant asks.

## Phase 5 — AI prompt contract

Update the existing AI service path, not a parallel AI stack.

Prompt requirements:
- extract current facts only;
- mark historical/hypothetical/superseded facts;
- resolve explicit corrections;
- identify deferred interest;
- never invent missing fields;
- never invent prices;
- structured JSON only;
- distinguish customer origin from vehicle location;
- distinguish “not running” from “disassembled”;
- preserve ambiguity rather than guessing.

Use concise examples including NU01, NU04, NU08, NU11.

Do not send full conversation history when current burst + resolver snapshot is sufficient.

## Phase 6 — Validation and application layer

Narrative output must pass deterministic validators before mutation.

Vehicle: exact catalog first, fuzzy only under M21.1.4 contract, ambiguity unresolved.

Location: M21.1.3 role/zone validation; customer origin never inspection location; contradictions use existing clarification.

Inspectability: M21.1.2 rules; hypothetical non-running does not set clarification state; assembled/accessibility confirmation resolves pending state.

Intent: BR-1 and deterministic boundaries remain authoritative.

Year: validate plausible format/range using existing business logic if available; never invent.

## Phase 7 — Deferred-interest handling

Implement a narrow internal outcome equivalent to `DEFERRED_INTEREST`.

When triggered and no stronger current commercial evidence exists:
- respond naturally;
- no commercial mutation;
- no vehicle/location question;
- no Flow;
- no `needs_human`;
- no quote/schedule.

Approved default copy:

`Perfecto, cuando tengas algún auto en vista escribinos y te ayudamos con la revisión.`

## Phase 8 — M21.1.5 resolver integration

Before narrative interpretation, generate current evidence snapshot.

After validated narrative facts are applied, regenerate snapshot.

Use refreshed evidence for vehicle-known, location-known, inspectability-known, and pricing-readiness decisions.

Do not duplicate resolver logic in M21.1.6.

## Phase 9 — Regression gates

Run:

```bash
python -m pytest tests/test_m21_1_6_narrative_understanding.py -q --tb=short
```

Then:

```bash
python -m pytest \
  tests/test_m21_1_5_field_evidence_resolver.py \
  tests/test_m21_1_4_asr_vehicle_normalization.py \
  tests/test_m21_1_3_location_roles.py \
  tests/test_m21_1_2_vehicle_inspectability.py \
  tests/test_br1_intent_gate.py \
  tests/test_m21_1_1_service_intent_gate.py \
  -q --tb=short
```

Correct M20 gate:

```bash
python -m pytest \
  tests/test_m20_2_kill_switch_proof.py \
  tests/test_m20_4_1_zone_accent_normalization.py \
  tests/test_m20_4_3_blocked_dispatch.py \
  tests/test_m20_6b1_acceptance_no_quote.py \
  tests/test_m20_6c1_copy_polish.py \
  tests/test_m20_6d2_customer_reality.py \
  tests/test_m20_6d4v_vehicle_catalog.py \
  -q --tb=short
```

Then:

```bash
python -m pytest tests/ -m "not requires_infra" -q --tb=no
```

Compare with baseline `9769bf7`.

Require targeted narrative suite green, prior M21 suites green, correct M20 gate green, zero new deterministic failure node IDs, and zero newly deselected tests.

## Phase 10 — Evidence review

Before commit:

```bash
git diff --check
git diff --stat
git diff
```

Report:
- narrative schema;
- AI prompt changes;
- exact AI call site;
- proof of one call per burst;
- deterministic bypass cases;
- validation rules per field;
- deferred-interest behavior;
- resolver before/after refresh;
- every new mutation path;
- before/after test totals;
- deterministic failure delta;
- deselection delta.

Expected files may include:
- `docs/M21_1_6_narrative_understanding.md`
- `tests/test_m21_1_6_narrative_understanding.py`
- `backend/app/services/conversation_engine.py`
- existing AI service/schema files
- `backend/app/services/field_evidence.py` only for a minimal resolver API extension

No migration is expected.

## Phase 11 — Commit

After mandatory gates pass:

```bash
git add \
  docs/M21_1_6_narrative_understanding.md \
  tests/test_m21_1_6_narrative_understanding.py \
  backend/app/services/conversation_engine.py
```

Add only directly required AI/schema/resolver files explicitly. Do not use `git add .`.

Commit:

```bash
git commit -m "feat(M21.1.6): add narrative conversation understanding"
```

Do not push.

## Phase 12 — Build

```bash
SHA=$(git rev-parse --short HEAD)
docker build -t ridecheck-crm-backend:m21.1.6-${SHA} ./backend
```

Verify clean import, narrative schema, one-call-per-burst wiring, deterministic gate ordering, deferred-interest path, resolver refresh, prior migrations, and image not deployed.

## Phase 13 — Closeout report

Write:

`/opt/ridecheck-crm/forensics/M21_1_6_narrative_understanding_closeout_20260811.md`

Return:

```text
PASS / PARTIAL PASS / FAIL

Branch:
<branch>

Baseline:
9769bf7

M21.1.6 commit:
<SHA>

Source of truth committed:
YES/NO

Narrative schema:
<path>

Narrative AI prompt:
<path/function>

One AI call per burst:
PASS/FAIL

Deterministic boundaries preserved:
PASS/FAIL

Resolver integrated before/after:
PASS/FAIL

Deferred-interest handling:
PASS/FAIL

NU01 real client message:
PASS/FAIL

NU02 clean deferred:
PASS/FAIL

NU03 active vehicle overrides deferred language:
PASS/FAIL

NU04 historical correction:
PASS/FAIL

NU05 location correction:
PASS/FAIL

NU06 hypothetical inspectability:
PASS/FAIL

NU07 actual non-running accessible:
PASS/FAIL

NU08 multi-fact quote:
PASS/FAIL

NU09 vehicle ambiguity:
PASS/FAIL

NU10 location ambiguity:
PASS/FAIL

NU11 explicit vehicle correction:
PASS/FAIL

NU12 origin/location roles:
PASS/FAIL

NU13 deterministic easy-case bypass:
PASS/FAIL

NU14 unsupported boundary:
PASS/FAIL

NU15 motorcycle:
PASS/FAIL

NU16 malformed AI:
PASS/FAIL

NU17 AI error:
PASS/FAIL

NU18 resolver refresh:
PASS/FAIL

NU19 FAQ + facts:
PASS/FAIL

NU20 soft close:
PASS/FAIL

NU21 multi-message burst:
PASS/FAIL

No redundant vehicle question:
PASS/FAIL

No redundant location question:
PASS/FAIL

No redundant inspectability question:
PASS/FAIL

Schema changed:
YES/NO

Targeted suite:
<PASSED>/<FAILED>/<SKIPPED>

Prior M21 suites:
<PASSED>/<FAILED>

M20 seven-file gate:
<PASSED>/<FAILED>

New deterministic failures:
<number>

Newly deselected:
<number>

Image:
<tag>

Image deployed:
NO

Runtime changed:
NO

Production touched:
NO

M21.1.6 complete:
YES/NO

Safe for M21.1.7:
YES/NO

Report:
<path>
```
