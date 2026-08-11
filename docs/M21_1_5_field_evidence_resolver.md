# M21.1.5 — Central Field-Evidence Resolver

**Date:** 2026-08-11
**Status:** Approved
**Module:** `backend/app/services/field_evidence.py`

---

## Objective

Provide one centralized, source-aware, read-only resolver so the Conversation Engine
stops making repeated or contradictory decisions about whether key commercial fields
are already known.

This milestone consolidates evidence precedence. It does not add new extraction
capabilities, new AI prompts, or new schema.

---

## Rules

### ER-1 — One central read model

```python
resolve_field_evidence(ctx, state, current_turn_text="") -> FieldEvidenceSnapshot
```

The snapshot covers:

- `service_intent`
- `vehicle`
- `vehicle_year`
- `vehicle_category`
- `inspection_location`
- `customer_origin`
- `inspectability`
- `scheduling` (read-model only)

### ER-2 — Provenance is explicit

Each resolved field is a `FieldEvidence(value, source, confidence, confirmed, current_turn)`.

Source labels:

| Label | Meaning |
|-------|---------|
| `CURRENT_TURN_EXACT` | Exact catalog hit or explicit pattern match in current text |
| `CURRENT_TURN_FUZZY_HIGH` | Fuzzy AUTO_ACCEPT (≥0.87 + gap ≥0.15) on current text |
| `CURRENT_TURN_CONFIRMED_FUZZY` | Previously proposed and accepted fuzzy match |
| `CANDIDATE` | Focused candidate (most authoritative persisted evidence) |
| `THREAD_STATE` | Persisted thread state (home_zone, last_intent, flags) |
| `FLOW` | WhatsApp Flow submission |
| `WEBSITE_FORM` | Website form submission |
| `AI_EXTRACTED` | AI extraction result |
| `DERIVED` | Derived from other fields |
| `NONE` | No evidence |

Provenance labels are not persisted (ER-13).

### ER-3 — Precedence

General (highest to lowest):

1. Current-turn explicit deterministic evidence (`current_turn=True`, `CURRENT_TURN_*`)
2. Confirmed candidate evidence (`CANDIDATE`)
3. Confirmed thread state / Flow / form evidence (`THREAD_STATE`, `FLOW`, `WEBSITE_FORM`)
4. AI-extracted evidence (`AI_EXTRACTED`)
5. Stale generic thread fallback
6. None

Field-specific exceptions must match prior approved milestones (M21.1.1–M21.1.4).

### ER-4 — Candidate is commercial source of truth

For the active candidate (focused by `current_focus_candidate_id` or `status=="current_focus"`):

- `make/model/category` come from candidate fields (`marca`, `modelo`, `tipo_vehiculo`)
- `inspection_location` comes from candidate zone fields (`zone_group`, `zone_detail`)
- Thread home-zone fields are fallback only when candidate has no zone

Do not mirror candidate location back to thread state to satisfy old readers.

### ER-5 — Pending is not confirmed

These remain unconfirmed and must not satisfy `pricing_ready()`:

- `pending_fuzzy_catalog_key` set → `confirmed=False`
- `inspectability_clarification_sent=True` → `confirmed=False`
- Ambiguous/contradictory location → `confirmed=False`
- Unapplied AI guess → `confirmed=False`

### ER-6 — Service intent respects BR-1

`state.last_intent == "PREPURCHASE_INSPECTION"` → confirmed intent, source `THREAD_STATE`.

FAQ/conversational turns alone do not create commercial intent in the resolver.
The resolver does NOT re-run Layer F classification on `current_turn_text`.

### ER-7 — Vehicle respects M21.1.4

Resolution order:

1. Exact current-turn catalog hit (`lookup_vehicle(turn)`) → `CURRENT_TURN_EXACT`
2. Fuzzy AUTO_ACCEPT on current turn (`fuzzy_lookup_vehicle(turn).outcome=="AUTO_ACCEPT"`) → `CURRENT_TURN_FUZZY_HIGH`
3. Focused candidate with `marca` set → `CANDIDATE`, confirmed
4. `pending_fuzzy_catalog_key` → `THREAD_STATE`, `confirmed=False`
5. None → `NONE`

Pending `CONFIRM` (step 4) must never appear as confirmed vehicle evidence.

### ER-8 — Location respects M21.1.3

Resolution order:

1. Explicit current vehicle-location clause (`_VEHICLE_LOCATION_CLAUSE_PATTERNS[0-5]`) → `CURRENT_TURN_EXACT`
2. Focused candidate `zone_group` → `CANDIDATE`, confirmed
3. `state.home_zone_group` / `state.home_zone_detail` → `THREAD_STATE`, confirmed only when `home_zone_group` is set

Contradiction rule: if two distinct zones detected with uncertainty marker in same turn → `confirmed=False`, `value=None`.

Customer origin (detected by `_CUSTOMER_ORIGIN_CAPTURE_RE`) is reported in `customer_origin` and
**never** satisfies `inspection_location`.

### ER-9 — Inspectability respects M21.1.2

Current-turn detection order (historical patterns suppress all below):

1. Assembled patterns (`_INSP_ASSEMBLED`) → `ASSEMBLED_ACCESSIBLE`, confirmed
2. Disassembled patterns (`_INSP_DISASSEMBLED`) → `DISASSEMBLED_BLOCKED`, confirmed
3. Non-running patterns (`_INSP_NONRUNNING`) → `UNRESOLVED_NON_RUNNING`, `confirmed=False`

State flag: `inspectability_clarification_sent=True` → `UNRESOLVED_NON_RUNNING`, `confirmed=False`

Default: `UNKNOWN` (allows progress — vehicle has not been flagged as blocked).

`inspectability_allows_progress()` returns `True` for `ASSEMBLED_ACCESSIBLE` or `UNKNOWN`.

### ER-10 — Resolver is read-only

The resolver must not:

- create/update candidates
- mutate thread state
- price
- schedule
- create revisions
- dispatch Flows
- send WhatsApp messages
- set human flags
- write to the database

### ER-11 — Redundant-question suppression

Before asking again for vehicle, location, or inspectability, CE integration points
consult the resolver. If a field is confirmed at sufficient authority:

- do not ask again
- do not resend its fallback Flow

Integration targets: `_routing_gate`, `_check_fallback_flow_triggers`.

### ER-12 — Contradiction is explicit

- Current explicit evidence beats stale persisted evidence
- Contradictory current-turn values remain unresolved; use existing clarification behavior
- Do not silently merge

### ER-13 — No new persistence

The resolver is assembled from existing state. No new DB table, JSON blob, snapshot
column, or provenance enum is approved. Source labels exist only in memory.

---

## Readiness helpers

| Helper | Condition |
|--------|-----------|
| `vehicle_known()` | `vehicle_category.confirmed and bool(vehicle_category.value)` |
| `location_known()` | `inspection_location.confirmed and value is not None` |
| `inspectability_allows_progress()` | value in `{ASSEMBLED_ACCESSIBLE, UNKNOWN}` |
| `service_intent_known()` | `service_intent.confirmed and value is not None` |
| `pricing_ready()` | All four above true; does NOT call PricingService |
| `has_confirmed_vehicle()` | `vehicle.confirmed and value is not None` |
| `has_confirmed_location()` | `location_known()` |
| `needs_vehicle()` | `not vehicle_known()` |
| `needs_location()` | `not has_confirmed_location()` |

---

## CE integration

Minimal integration (Phase 5): `_routing_gate` and `_check_fallback_flow_triggers`
call `resolve_field_evidence(ctx, state)` (without `current_turn_text`, since candidates
are populated before these functions run) and use `snap.vehicle_known()` and
`snap.location_known()` in place of the inline `vehicle_known`/`zone_known` computations.

Intentionally retained direct checks:
- Inspectability gate (`_handle_vehicle_inspectability_gate`) — already correct per M21.1.2
- `_has_established_inspection_context` — different semantic purpose (context detection, not field evidence)
- All scheduling logic — read-model only, no CE integration needed

---

## Detection patterns

Patterns replicated from CE (no import) to avoid circular dependency:

| Pattern set | Source | CE constant |
|-------------|--------|-------------|
| Vehicle-location clauses | `_VEHICLE_LOCATION_CLAUSE_PATTERNS` | Same name in CE |
| Location uncertainty | `_LOCATION_UNCERTAINTY_RE` | Same name in CE |
| Customer origin | `_CUSTOMER_ORIGIN_CAPTURE_RE` | Extends `_CUSTOMER_ORIGIN_RE` with capture group |
| Inspectability assembled | `_INSP_ASSEMBLED` | `_INSPECTABILITY_ASSEMBLED_PATTERNS` in CE |
| Inspectability disassembled | `_INSP_DISASSEMBLED` | `_INSPECTABILITY_DISASSEMBLED_PATTERNS` in CE |
| Inspectability non-running | `_INSP_NONRUNNING` | `_INSPECTABILITY_NONRUNNING_PATTERNS` in CE |
| Inspectability historical | `_INSP_HISTORICAL` | `_INSPECTABILITY_HISTORICAL_PATTERNS` in CE |

---

## Test cases

| ID | Scenario | Key assertion |
|----|----------|---------------|
| FE01 | Confirmed candidate vehicle | source=CANDIDATE, confirmed=True |
| FE02 | Pending fuzzy proposal | confirmed=False, pricing_ready=False |
| FE03 | Exact current-turn vehicle | source=CURRENT_TURN_EXACT, confirmed=True |
| FE04 | Candidate location beats thread zone | source=CANDIDATE, Palermo not Tigre |
| FE05 | Current explicit location beats candidate | source=CURRENT_TURN_EXACT, no mutation |
| FE06 | Customer origin separated | origin=Tigre, location=Palermo |
| FE07 | Bare locality in clarification context | location confirmed from state |
| FE08 | Inspectability pending | value=UNRESOLVED_NON_RUNNING, confirmed=False |
| FE09 | Inspectability assembled current turn | value=ASSEMBLED_ACCESSIBLE, confirmed=True |
| FE10 | BR-1 intent established | source=THREAD_STATE, confirmed=True |
| FE11 | FAQ fresh thread | all sources=NONE |
| FE12 | Website/Flow evidence | source=CANDIDATE, no stale downgrade |
| FE13 | Contradictory locations | confirmed=False, value=None |
| FE14 | No redundant vehicle question | needs_vehicle=False |
| FE15 | No redundant location question | needs_location=False |
| FE16 | No redundant inspectability question | allows_progress=True |
| FE17 | Pending fuzzy not pricing-ready | pricing_ready=False |
| FE18 | Full qualification completeness | pricing_ready=True |
