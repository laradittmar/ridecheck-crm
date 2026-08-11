# M21.1.6 — Long Voice and Narrative Understanding

**Date:** 2026-08-11  
**Status:** Approved  
**Module:** `backend/app/services/narrative_schema.py`  
**CE integration:** `backend/app/services/conversation_engine.py`

---

## Objective

Implement narrative-level understanding for long, messy, multi-fact customer messages
after they reach the Conversation Engine. Builds on M21.1.5 Central Field-Evidence
Resolver. Does not create a parallel evidence system.

---

## Rules

### NU-1 — Interpret the current burst as one narrative

The overall meaning of the message determines the response, not isolated keywords.

`Hola, estoy buscando un auto. Todavía no decidí cuál. Agendé el número para no
perderlo y cuando tenga uno en vista les aviso.` → `DEFERRED_INTEREST`, not
`ACTIVE_QUOTE_REQUEST`.

### NU-2 — Whole-message meaning beats isolated keywords

- `Estoy buscando un auto, pero todavía no tengo ninguno en vista.` → deferred
- `Pensaba comprar un Focus, pero al final compré un Corolla y quiero revisar ese.` → Corolla current
- `El auto estaba en Tigre pero ahora está en Palermo.` → Palermo current

### NU-3 — Explicit corrections win

Correction markers: `en realidad`, `me equivoqué`, `quise decir`, `no, es...`,
`al final`, `ahora`, `finalmente`.

Corrected/current fact supersedes prior fact in the same narrative.

### NU-4 — Historical facts are not current commercial facts

Historical markers: `antes`, `estaba`, `estuvo`, `había`, `pensaba`, `iba a`,
`tenía`, `me habían dicho`.

Historical facts must not overwrite current explicit evidence.

### NU-5 — Hypothetical facts remain informational

- `¿Qué pasa si el auto no arranca?`
- `Supongamos que está desarmado.`
- `Si estuviera en Tigre, ¿cambia el precio?`

Do not mutate candidate/location/inspectability/pricing inputs from hypothetical
clauses alone.

### NU-6 — Deferred-interest / not-ready-yet intent

Recognize messages whose overall meaning is: still looking, no specific vehicle yet,
saved the contact, will return later, not ready to quote/schedule.

When triggered and no stronger current commercial evidence exists:
- respond with approved copy;
- no commercial mutation;
- no vehicle/location question;
- no Flow;
- no `needs_human`;
- no quote/schedule.

Approved default copy:

```
Perfecto, cuando tengas algún auto en vista escribinos y te ayudamos con la revisión.
```

### NU-7 — Explicit active intent overrides deferred language

`Estoy buscando, pero ya tengo un Focus 2019 en Palermo que quiero revisar.` → active flow.

`is_effectively_deferred()` returns False when `has_active_vehicle()` is True.

### NU-8 — Multiple facts in one narrative

```
Es un Focus 2019, yo vivo en La Plata pero el auto está en Palermo,
no arranca aunque está completo. ¿Cuánto sale?
```

Expected: vehicle + year + customer_origin + vehicle_location + inspectability + price intent.
Do not ask again for resolved fields.

### NU-9 — Narrative output cannot bypass deterministic validation

Vehicle must pass catalog validation; location must pass M21.1.3 role/zone rules;
inspectability must respect M21.1.2; intent must respect BR-1 and all-stage boundaries.

### NU-10 — Confidence/status per fact

Supported statuses: `CONFIRMED`, `LIKELY`, `UNCERTAIN`, `ABSENT`, `SUPERSEDED`,
`HYPOTHETICAL`, `HISTORICAL`.

Only `CONFIRMED` or `LIKELY` facts (`is_active()` = True) may become confirmed commercial evidence.

### NU-11 — Contradictions remain unresolved

- `El auto está en Tigre o en Palermo, no sé.` → `UNCERTAIN` status; existing clarification.
- `Creo que es un Focus o un Fiesta, no sé.` → `UNCERTAIN` vehicle; do not guess.

### NU-12 — Resolver refresh

After validated narrative facts are applied via `_apply_narrative_interpretation`,
the M21.1.5 evidence snapshot is updated because state/candidate fields are updated
in-place. The next resolver call in the same or subsequent turn reflects the new state.

### NU-13 — No redundant questions

A compound message containing vehicle + year + inspection location must not trigger
vehicle/location questions or corresponding fallback Flows.
`narrative_needs_ai()` returns False when evidence is already complete and no complex
markers are present, bypassing the narrative AI call.

### NU-14 — Deterministic easy cases bypass narrative AI

`narrative_needs_ai(snap, text)` returns False when:
- `snap.vehicle_known() and snap.location_known()` (M21.1.5 resolver)
- AND no complex narrative markers present in text

Easy cases: exact known vehicle, clear location, motorcycle (pre-gate), Formulario 12,
exact fuzzy-confirmation flow, clean acceptance.

### NU-15 — One narrative AI call per burst

Narrative interpretation is integrated into the existing AI call via extended JSON
schema fields. No second AI call is made. `_use_narrative` flag controls whether
narrative fields are included in the prompt and parsed from the response.

### NU-16 — AI failure is safe

`parse_narrative_interpretation(None or bad_input)` returns `None`.
`parse_narrative_interpretation({})` returns safe `NarrativeInterpretation` with all defaults.
CE falls back to existing deterministic behavior when `_narr is None`.

### NU-17 — No schema migration by default

Narrative interpretation is assembled in-memory from existing state/candidate fields.
No new DB table, column, or blob. `INSP_*` constants and `inspectability_clarification_sent`
from prior milestones are reused.

---

## Narrative Schema

```python
@dataclass(frozen=True)
class NarrativeFact:
    value: Any
    status: str  # one of the STATUS_* constants
    confidence: Optional[float] = None
    evidence: Optional[str] = None

    def is_active(self) -> bool:
        """True for CONFIRMED or LIKELY."""

@dataclass(frozen=True)
class NarrativeInterpretation:
    overall_intent: Optional[str] = None
    deferred_interest: bool = False
    vehicle_make_model: Optional[NarrativeFact] = None
    vehicle_year: Optional[NarrativeFact] = None
    vehicle_location: Optional[NarrativeFact] = None
    customer_origin: Optional[NarrativeFact] = None
    inspectability: Optional[NarrativeFact] = None
    asks_price: bool = False
    asks_faq: bool = False
    asks_schedule: bool = False

    def has_active_vehicle(self) -> bool
    def has_active_location(self) -> bool
    def has_active_inspectability(self) -> bool
    def is_effectively_deferred(self) -> bool
        # deferred_interest AND NOT has_active_vehicle()
```

---

## Public API

```python
parse_narrative_interpretation(raw: Any) -> Optional[NarrativeInterpretation]
narrative_needs_ai(snap: FieldEvidenceSnapshot, current_turn_text: str) -> bool
DEFERRED_RESPONSE_ES: str
```

---

## CE Integration

Conceptual order:

```
Current inbound burst
→ deterministic all-stage boundaries (motorcycle, phone, service gate, etc.)
→ BR-1 service-intent safety
→ inspectability gate
→ deterministic exact/fuzzy vehicle and location evidence
→ M21.1.5 field-evidence snapshot (_snap_pre)
→ narrative_needs_ai(_snap_pre, current_turn_text) → _use_narrative flag
→ _build_ai_messages(..., include_narrative=_use_narrative)
→ _call_openai → decision JSON
→ parse_narrative_interpretation(decision) → _narr
→ if _narr.is_effectively_deferred(): _handle_deferred_interest → return
→ _apply_extracted, _apply_candidate (existing paths)
→ _apply_narrative_interpretation (inspectability flag update)
→ pricing / clarification / scheduling
```

### Call sites

| Location | Purpose |
|----------|---------|
| `_process_text` (~line 2383) | `_use_narrative` computation + AI call + deferred intercept |
| `_build_ai_messages` | `include_narrative` param adds narrative section to prompt |
| `_handle_deferred_interest` | Returns approved deferred copy, no mutation |
| `_apply_narrative_interpretation` | Updates inspectability state from confirmed narrative facts |

### Intentionally retained direct checks

- All deterministic pre-gates (motorcycle, coverage, service gate, etc.) — run before narrative
- `_routing_gate` and `_check_fallback_flow_triggers` — unchanged (use M21.1.5 resolver)
- Pricing logic — unchanged; narrative does not compute or override prices

---

## Deterministic bypass markers

Correction markers: `en realidad`, `me equivoqué`, `quise decir`, `no, es`,
`al final compré`, `al final es`, `corrijo`, `perdón, es`

Historical markers: `pensaba comprar`, `pensaba que`, `antes era`, `pero ahora`,
`ya no es`, `estaba en [zone]`

Hypothetical markers: `supongamos`, `qué pasa si`, `si estuviera`, `si fuera`,
`y si el auto`, `qué pasaría`

When any of these appear in `current_turn_text` AND evidence is already complete,
`narrative_needs_ai` still returns True (narrative is needed to handle the complexity).

---

## Test cases

| ID | Scenario | Key assertion |
|----|----------|---------------|
| NU01 | Real corrupted deferred message | is_effectively_deferred=True |
| NU02 | Clean deferred version | is_effectively_deferred=True |
| NU03 | Deferred language + active vehicle | is_effectively_deferred=False |
| NU04 | Historical vehicle correction | Corolla CONFIRMED, Focus implicit |
| NU05 | Location correction | Palermo CONFIRMED |
| NU06 | Hypothetical inspectability | is_active=False, no state change |
| NU07 | Actual non-running + accessible | ASSEMBLED_ACCESSIBLE CONFIRMED |
| NU08 | Multi-fact quote | all 5 fields parsed |
| NU09 | Vehicle ambiguity | UNCERTAIN, has_active_vehicle=False |
| NU10 | Location ambiguity | UNCERTAIN, has_active_location=False |
| NU11 | Explicit vehicle correction | Kuga CONFIRMED |
| NU12 | Origin/location role separation | customer_origin ≠ vehicle_location |
| NU13 | Deterministic bypass | narrative_needs_ai=False when complete |
| NU14 | Unsupported boundary | narrative parses; gate fires first |
| NU15 | Motorcycle | narrative captures vehicle; gate fires first |
| NU16 | Malformed AI result | safe defaults, no exception |
| NU17 | AI timeout/error | parse returns None |
| NU18 | Resolver refresh | snapshot reflects post-narrative candidate |
| NU19 | FAQ + facts | asks_faq=True AND vehicle/location captured |
| NU20 | Soft close after context | is_effectively_deferred=True |
| NU21 | Multi-message burst | one interpretation, no redundant asks |
