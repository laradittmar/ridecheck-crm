# M21.1.5 — Central Field-Evidence Resolver

## Objective

Implement one centralized, source-aware field-evidence resolver so the Conversation Engine stops making repeated or contradictory decisions about whether key commercial fields are already known.

This milestone is about evidence consolidation and precedence, not adding new extraction capabilities.

Centralize how the engine determines the best-known value and provenance for:

- service intent;
- vehicle make/model/category;
- vehicle year;
- vehicle inspection location;
- customer origin when explicitly present;
- inspectability status;
- scheduling-relevant date/time evidence only as a read model where already available.

Preserve all behavior completed in M21.1.1 through M21.1.4.

Do not extend into long-voice/narrative extraction, Whisper prompt changes, n8n changes, new AI extraction prompts, pricing-rule redesign, locality alias expansion, scheduling UX redesign, multi-candidate switching, or CRM source attribution.

## Baseline

Expected branch:

`fix/m21.1.1-primary-flow-regression`

Expected HEAD:

`f5bc7fd`

Before editing:

```bash
cd /opt/ridecheck-crm-release-candidate
git status --short
git branch --show-current
git rev-parse HEAD
git log -10 --oneline
```

If HEAD is later than `f5bc7fd`, audit intervening commits and work forward.

Do not reset, rebase, squash, force-checkout, or rewrite history.

Stop if unrelated working-tree changes would be included.

## Safety

- Do not deploy.
- Do not push.
- Do not start n8n.
- Do not enable outbound.
- Use `crm_test` only.
- Do not connect to production `crm`.
- Do not call Meta, OpenAI, Whisper, Resend, Gmail, Google Maps, or any external service.
- Do not modify n8n.
- Do not change pricing amounts, viáticos tables, or scheduling algorithms.
- Do not add a schema migration unless persistence is proven unavoidable; stop for owner approval first.
- Do not modify prior milestone expectations merely to force green results.

## Phase 1 — Source audit before code

Build a source map for:

- service intent;
- vehicle;
- vehicle year;
- vehicle category;
- inspection zone group/detail;
- customer origin;
- inspectability;
- scheduling date/time evidence.

For each field identify:

1. every source of evidence:
   - current text;
   - exact catalog;
   - fuzzy catalog;
   - pending fuzzy confirmation;
   - candidate;
   - thread state;
   - website form;
   - WhatsApp Flow;
   - AI extracted payload;
   - revision/booking state;
   - prior accepted conversation state;

2. every reader;

3. every writer;

4. all precedence rules currently embedded implicitly;

5. places causing redundant questions because one subsystem does not see evidence already held elsewhere;

6. stale thread fields that can disagree with candidate fields;

7. duplicate representations of the same field.

Report this source map before editing.

## Phase 2 — Source of truth

Create:

`docs/M21_1_5_field_evidence_resolver.md`

Use these rules as authoritative.

### ER-1 — One central read model

Create a read-only resolver equivalent to:

```python
resolve_field_evidence(ctx, state, current_turn_text)
```

Preferred conceptual result:

```python
FieldEvidenceSnapshot(
    service_intent=FieldEvidence(...),
    vehicle=FieldEvidence(...),
    vehicle_year=FieldEvidence(...),
    vehicle_category=FieldEvidence(...),
    inspection_location=FieldEvidence(...),
    customer_origin=FieldEvidence(...),
    inspectability=FieldEvidence(...),
    scheduling=FieldEvidence(...),
)
```

### ER-2 — Provenance is explicit

Each resolved field exposes at least:

- value;
- source;
- confidence/authority;
- confirmed;
- current-turn evidence flag.

Suggested source labels:

- `CURRENT_TURN_EXACT`
- `CURRENT_TURN_FUZZY_HIGH`
- `CURRENT_TURN_CONFIRMED_FUZZY`
- `FLOW`
- `WEBSITE_FORM`
- `CANDIDATE`
- `THREAD_STATE`
- `REVISION`
- `AI_EXTRACTED`
- `DERIVED`
- `NONE`

Do not persist these labels unless later approved.

### ER-3 — Precedence

General precedence:

1. current-turn explicit deterministic evidence;
2. explicit Flow/form evidence;
3. confirmed candidate evidence;
4. confirmed prior thread intent/state;
5. AI-extracted evidence;
6. stale/generic thread fallback;
7. none.

Field-specific exceptions must match already-approved prior milestones.

### ER-4 — Candidate is commercial source of truth

For the active candidate:

- make/model/category come from the confirmed candidate;
- inspection location comes from candidate zone fields;
- thread home-zone fields are fallback only where legacy code still requires them.

Do not mirror candidate location back to thread state just to satisfy old readers.

### ER-5 — Pending is not confirmed

These remain unconfirmed:

- pending fuzzy vehicle proposal;
- unresolved inspectability clarification;
- ambiguous location contradiction;
- unapplied AI guess.

They must not satisfy pricing/scheduling readiness.

### ER-6 — Service intent respects BR-1

Do not reinterpret M21.1.1.

Accepted PREPURCHASE intent is established evidence. FAQ/conversational turns alone do not become commercial intent.

### ER-7 — Vehicle respects M21.1.4

Resolution order:

1. exact current-turn catalog hit;
2. confirmed fuzzy result;
3. high-confidence AUTO_ACCEPT;
4. existing focused candidate;
5. pending fuzzy proposal as unconfirmed only;
6. none.

Pending `CONFIRM` must never appear as confirmed vehicle evidence.

### ER-8 — Location respects M21.1.3

Resolution order:

1. explicit current vehicle-location clause;
2. valid bare-locality reply in active clarification context;
3. focused candidate zone;
4. source-verified Flow/form location;
5. legacy thread zone fallback only when no candidate/current evidence exists.

Customer origin never satisfies inspection-location requirements.

### ER-9 — Inspectability respects M21.1.2

Represent at minimum:

- `DISASSEMBLED_BLOCKED`
- `ASSEMBLED_ACCESSIBLE`
- `UNRESOLVED_NON_RUNNING`
- `HUMAN_REVIEW`
- `UNKNOWN`

A pending clarification is not inspectable.

### ER-10 — Resolver is read-only

The resolver must not:

- create/update candidates;
- mutate thread state;
- price;
- schedule;
- create revisions;
- dispatch Flows;
- send WhatsApp messages;
- set human flags.

### ER-11 — Redundant-question suppression

Before asking again for vehicle, location, or inspectability, consult the resolver.

If a field is confirmed at sufficient authority:

- do not ask again;
- do not resend its fallback Flow.

### ER-12 — Contradiction is explicit

- current explicit evidence beats stale persisted evidence;
- contradictory current-turn values remain unresolved and use existing clarification behavior;
- do not silently merge.

### ER-13 — No new persistence by default

The resolver is assembled from existing state.

No new DB table, JSON blob, snapshot column, or provenance enum is approved by default.

Stop for approval if persistence is genuinely required.

## Phase 3 — Executable specification first

Create:

`tests/test_m21_1_5_field_evidence_resolver.py`

Reuse existing fixtures.

Required cases:

### FE01 — Confirmed candidate vehicle
Candidate Ford Focus 2019.
Require confirmed vehicle from CANDIDATE and no vehicle clarification needed.

### FE02 — Pending fuzzy proposal
`pending_fuzzy_catalog_key = "Ford||Ka"`.
Require pending/unconfirmed proposal; confirmed vehicle remains none unless candidate exists; pricing readiness false.

### FE03 — Exact current-turn vehicle
Current `Ford Kuga 2020`, no focused candidate.
Require exact current evidence wins over stale thread text/state.

### FE04 — Candidate location beats thread zone
Candidate Palermo, thread Tigre.
Require inspection location Palermo from CANDIDATE.

### FE05 — Current explicit location beats stale candidate location
Candidate Tigre; current `El auto ahora está en Villa Urquiza.`
Require current-turn Villa Urquiza as highest authority. Resolver itself does not mutate.

### FE06 — Customer origin separated
`Yo vivo en Tigre, pero el auto está en Palermo.`
Require customer_origin=Tigre and inspection_location=Palermo.

### FE07 — Bare locality in location clarification context
Pending location clarification + current `Palermo`.
Require inspection_location=Palermo.

### FE08 — Inspectability pending
`inspectability_clarification_sent=True`, no confirmation.
Require unresolved/pending inspectability and pricing readiness false.

### FE09 — Inspectability resolved
Current `Sí, está armado y accesible.`
Require assembled-accessible current evidence wins.

### FE10 — BR-1 intent established
`last_intent=PREPURCHASE_INSPECTION`.
Require confirmed service intent.

### FE11 — FAQ-only fresh thread
`¿Qué revisan?`
Require no commercial field invented.

### FE12 — Website/Flow evidence
Use existing source-verified fixtures. Require correct provenance and no downgrade from stale thread state.

### FE13 — Contradictory current locations
`El auto está en Tigre o puede ser Villa Urquiza, no sé.`
Require unresolved/contradictory location.

### FE14 — No redundant vehicle question
Confirmed candidate + generic follow-up.
Require vehicle question unnecessary.

### FE15 — No redundant location question
Candidate has resolved location.
Require no location fallback/clarification.

### FE16 — No redundant inspectability question
Resolved assembled-accessible evidence/state.
Require no repeated inspectability clarification.

### FE17 — Pending fuzzy is not pricing-ready
Require commercial readiness false.

### FE18 — Qualification completeness
Confirmed candidate + candidate location + PREPURCHASE intent + no inspectability blocker.
Require those fields considered complete. Do not assert price amount.

## Phase 4 — Resolver design

Preferred file:

`backend/app/services/field_evidence.py`

Suggested types:

```python
@dataclass(frozen=True)
class FieldEvidence:
    value: object | None
    source: str
    confidence: str
    confirmed: bool
    current_turn: bool

@dataclass(frozen=True)
class FieldEvidenceSnapshot:
    service_intent: FieldEvidence
    vehicle: FieldEvidence
    vehicle_year: FieldEvidence
    vehicle_category: FieldEvidence
    inspection_location: FieldEvidence
    customer_origin: FieldEvidence
    inspectability: FieldEvidence
```

Useful read-only helpers may include:

```python
snapshot.has_confirmed_vehicle()
snapshot.has_confirmed_location()
snapshot.needs_vehicle()
snapshot.needs_location()
snapshot.pricing_ready()
```

Do not turn this into a second Conversation Engine.

## Phase 5 — Minimal CE integration

Use the resolver only in the minimum set of places that decide whether to ask again for:

- vehicle;
- location;
- inspectability.

Expected integration targets:

- vehicle clarification/fallback trigger;
- location clarification/fallback trigger;
- inspectability repeated-question decision;
- direct `if not state...` checks that duplicate the same “is this known?” logic.

Do not refactor every CE branch in this milestone.

Preserve outward behavior unless the old behavior is demonstrably redundant or contradictory.

## Phase 6 — Readiness helpers

Add read-only helpers for:

- `vehicle_known`
- `location_known`
- `inspectability_allows_progress`
- `service_intent_known`
- `pricing_ready`

`pricing_ready` means only required evidence exists. It must not call PricingService.

## Phase 7 — Regression gates

Run:

```bash
python -m pytest tests/test_m21_1_5_field_evidence_resolver.py -q --tb=short
```

Then:

```bash
python -m pytest   tests/test_m21_1_4_asr_vehicle_normalization.py   tests/test_m21_1_3_location_roles.py   tests/test_m21_1_2_vehicle_inspectability.py   tests/test_br1_intent_gate.py   tests/test_m21_1_1_service_intent_gate.py   -q --tb=short
```

Run correct M20 seven-file gate:

```bash
python -m pytest   tests/test_m20_2_kill_switch_proof.py   tests/test_m20_4_1_zone_accent_normalization.py   tests/test_m20_4_3_blocked_dispatch.py   tests/test_m20_6b1_acceptance_no_quote.py   tests/test_m20_6c1_copy_polish.py   tests/test_m20_6d2_customer_reality.py   tests/test_m20_6d4v_vehicle_catalog.py   -q --tb=short
```

Then:

```bash
python -m pytest tests/ -m "not requires_infra" -q --tb=no
```

Compare with baseline `f5bc7fd`.

Require:

- targeted resolver suite green;
- prior M21 suites green;
- correct M20 gate green;
- zero new deterministic failure node IDs;
- zero newly deselected tests.

## Phase 8 — Evidence review

Before commit:

```bash
git diff --check
git diff --stat
git diff
```

Report:

- fields centralized;
- evidence sources per field;
- precedence per field;
- resolver consumers;
- redundant direct checks removed/replaced;
- direct checks intentionally retained;
- proof resolver has no mutations/outbound;
- before/after test totals;
- deterministic failure delta;
- deselection delta.

Expected files:

- `docs/M21_1_5_field_evidence_resolver.md`
- `backend/app/services/field_evidence.py`
- `backend/app/services/conversation_engine.py`
- `tests/test_m21_1_5_field_evidence_resolver.py`

Other files only if source audit proves directly necessary.

No migration is expected.

## Phase 9 — Commit

After mandatory gates pass:

```bash
git add   docs/M21_1_5_field_evidence_resolver.md   backend/app/services/field_evidence.py   backend/app/services/conversation_engine.py   tests/test_m21_1_5_field_evidence_resolver.py
```

Add other directly required files explicitly. Do not use `git add .`.

Commit:

```bash
git commit -m "feat(M21.1.5): centralize conversation field evidence"
```

Do not push.

## Phase 10 — Build

```bash
SHA=$(git rev-parse --short HEAD)

docker build   -t ridecheck-crm-backend:m21.1.5-${SHA}   ./backend
```

Verify:

- clean import;
- resolver module present;
- no outbound imports in resolver;
- no DB writes in resolver;
- M21.1.2 and M21.1.4 migrations present;
- image not deployed.

## Phase 11 — Closeout report

Write:

`/opt/ridecheck-crm/forensics/M21_1_5_field_evidence_resolver_closeout_20260810.md`

Return:

```text
PASS / PARTIAL PASS / FAIL

Branch:
<branch>

Baseline:
f5bc7fd

M21.1.5 commit:
<SHA>

Source of truth committed:
YES/NO

Resolver module:
<path>

Fields centralized:
<list>

Evidence provenance implemented:
YES/NO

Current-turn precedence:
PASS/FAIL

Candidate vehicle authority:
PASS/FAIL

Candidate location authority:
PASS/FAIL

Pending fuzzy remains unconfirmed:
PASS/FAIL

Inspectability pending/resolved:
PASS/FAIL

Customer origin separated:
PASS/FAIL

No redundant vehicle question:
PASS/FAIL

No redundant location question:
PASS/FAIL

No redundant inspectability question:
PASS/FAIL

Resolver read-only:
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

M21.1.5 complete:
YES/NO

Safe for M21.1.6:
YES/NO

Report:
<path>
```
