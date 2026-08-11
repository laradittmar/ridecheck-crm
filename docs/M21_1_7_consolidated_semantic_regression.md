# M21.1.7 — Consolidated Semantic Regression Pack

**Date:** 2026-08-11  
**Status:** Approved  
**Test file:** `tests/test_m21_1_7_consolidated_semantic_regression.py`

---

## Objective

Prove that M21.1.1 through M21.1.6 work together as one coherent Semantic Conversation
Engine. 35 scenarios (CR01–CR35) and 5 multi-turn sequences (SEQ01–SEQ05) exercise
cross-feature invariants that no single prior milestone test covers in isolation.

---

## Cross-Feature Invariants (CR-1 through CR-15)

| ID | Invariant | Covered by |
|----|-----------|------------|
| CR-1 | Deterministic boundaries fire before narrative AI; kill switch; exact catalog beats fuzzy | M21.1.1, M21.1.2, M21.1.4 |
| CR-2 | Narrative interprets whole message, not isolated keywords | M21.1.6 |
| CR-3 | Current explicit evidence beats stale state; corrections win | M21.1.5, M21.1.6 |
| CR-4 | Focused candidate is protected; candidate is source of truth | M21.1.5 |
| CR-5 | Fuzzy pending blocks pricing; exact or confirmed clears it | M21.1.4, M21.1.5 |
| CR-6 | Customer origin never becomes inspection location | M21.1.3, M21.1.6 |
| CR-7 | Deferred interest is non-commercial; active vehicle overrides deferred language | M21.1.6 |
| CR-8 | FAQ coexists with commercial facts; both retained | M21.1.1, M21.1.6 |
| CR-9 | Contradictions clarified; ambiguous facts are UNCERTAIN, not guessed | M21.1.3, M21.1.6 |
| CR-10 | Multiple facts in one narrative burst are resolved in one interpretation | M21.1.6 |
| CR-11 | Malformed or missing AI output → safe defaults; no unsafe mutation | M21.1.6 |
| CR-12 | No redundant vehicle/location/inspectability questions once evidence is complete | M21.1.5, M21.1.6 |
| CR-13 | pricing_ready() requires all four fields; semantic layer never calls pricing service | M21.1.5 |
| CR-14 | FAQ intent alone does not invent or mutate commercial state | M21.1.1 |
| CR-15 | AI exception is safe; CE always returns a valid handled action | M21.1.5, M21.1.6 |

---

## Scenario Table

### Pure Resolver + Narrative Tests (no CE instantiation)

| Scenario | Class | Key assertion |
|----------|-------|---------------|
| CR01 | TestCR01CleanMultiFactQuote | pricing_ready=True with confirmed candidate; no redundant asks |
| CR02 | TestCR02OriginVsVehicleLocation | customer_origin ≠ inspection_location; has_active_location=False for origin alone |
| CR03 | TestCR03HistoricalVehicleCorrection | Corolla CONFIRMED after "al final compré un Corolla" |
| CR04 | TestCR04HistoricalLocationCurrentLocation | current candidate zone beats stale thread state |
| CR05* | TestCR05RealDeferredMessage | is_effectively_deferred=True pure; deferred copy contains approved text |
| CR06 | TestCR06DeferredOverriddenByActiveVehicle | is_effectively_deferred=False when vehicle active |
| CR07 | TestCR07ActualNonRunningButAccessible | ASSEMBLED_ACCESSIBLE CONFIRMED; state cleared |
| CR08 | TestCR08HypotheticalNonRunning | HYPOTHETICAL inspectability is_active=False; state unchanged |
| CR12 | TestCR12HighConfidenceFuzzyCompound | narrative parses year+location when fuzzy auto-accepts vehicle |
| CR13 | TestCR13FuzzyConfirmationBlocksPricing | pending_fuzzy_catalog_key → vehicle_known=False, pricing_ready=False |
| CR14 | TestCR14FuzzyConfirmationAccepted | cleared pending → vehicle_known=True from candidate |
| CR15 | TestCR15FuzzyRejected | Kuga CONFIRMED, Ka absent |
| CR16 | TestCR16CurrentExplicitLocationBeatsStaleState | candidate zone_group beats home_zone_group |
| CR17 | TestCR17LocationContradiction | UNCERTAIN location not active |
| CR18 | TestCR18VehicleContradiction | UNCERTAIN vehicle not active; pricing_ready=False |
| CR19 | TestCR19FAQPlusVehicleLocation | asks_faq=True AND vehicle+location both active |
| CR21 | TestCR21PrepurchaseIntentFuzzyNoCandidate | intent known + fuzzy pending → pricing_ready=False |
| CR22 | TestCR22ExistingCandidateProtected | known vehicle → narrative bypass |
| CR23 | TestCR23ExactCorrectionWithExistingCandidate | correction marker triggers narrative even with complete evidence |
| CR24 | TestCR24MultiMessageBurst | one narr covers vehicle+year+location+price_intent |
| CR25 | TestCR25LongVoiceNarrative | 6 facts parsed from long voice-like message |
| CR26 | TestCR26VehicleCorrection | Kuga CONFIRMED after "me equivoqué, es un Ford Kuga" |
| CR27 | TestCR27AmbiguousYear | uncertain year ≠ uncertain vehicle; vehicle+location still known |
| CR32 | TestCR32NoRedundantVehicleQuestion | vehicle_known → needs_vehicle=False; narrative bypass |
| CR33 | TestCR33NoRedundantLocationQuestion | location_known → needs_location=False |
| CR34 | TestCR34NoRedundantInspectabilityQuestion | assembled clarification clears inspectability_clarification_sent |
| CR35 | TestCR35FullQualificationReadiness | pricing_ready → all four fields confirmed; no PricingService call |

### CE Integration Tests (use _make_engine / _run pattern)

| Scenario | Class | Key assertion |
|----------|-------|---------------|
| CR05* | TestCR05RealDeferredMessage | deferred intercept fires; DEFERRED_RESPONSE_ES sent; no _apply_candidate |
| CR06* | TestCR06DeferredOverriddenByActiveVehicle | active vehicle → deferred intercept skipped; _apply_candidate called |
| CR09 | TestCR09Disassembled | disassembled gate fires before AI; _call_openai.assert_not_called() |
| CR10 | TestCR10Motorcycle | motorcycle gate fires before AI ("motocicleta"); warm handoff sent |
| CR11 | TestCR11Formulario12 | F12 gate fires; "Formulario 12" in reply; no AI call |
| CR13* | TestCR13FuzzyConfirmationBlocksPricing | fuzzy CONFIRM → confirmation text sent with vehicle name |
| CR17* | TestCR17LocationContradiction | CE processes location-ambiguous text without crash |
| CR20 | TestCR20FAQOnlyFreshThread | FAQ only → no new current_focus_candidate_id |
| CR28 | TestCR28MalformedNarrativeAI | malformed AI JSON → result.action in HANDLED_ACTIONS |
| CR29 | TestCR29AITimeout | AI RuntimeError → result.action in HANDLED_ACTIONS |
| CR30 | TestCR30KillSwitch | F12 text + OutboundBlockedError → service_gate_blocked in HANDLED_ACTIONS |
| CR31 | TestCR31ExistingNeedsHuman | needs_human=True → service_intent_known() but CE won't price/schedule |

*Tests with both pure and CE components.

---

## Multi-Turn Sequence Tests

| Sequence | Class | Turns | Key assertion |
|----------|-------|-------|---------------|
| SEQ01 | TestSEQ01FuzzyLifecycle | 3 | fuzzy→pending→confirm→vehicle_known |
| SEQ02 | TestSEQ02InspectabilityEscalation | 2 | non-running→clarification→UNRESOLVED blocks progress |
| SEQ03 | TestSEQ03DeferredThenLaterActive | 3 | deferred→no mutation; active vehicle→normal flow |
| SEQ04 | TestSEQ04LocationCorrectionPricing | 3 | Tigre→correction marker→Villa Urquiza→pricing uses corrected |
| SEQ05 | TestSEQ05FAQThenActiveVehicle | 3 | FAQ→no state mutation; active vehicle not blocked |

---

## Technical Notes

### Integration with `_make_engine` pattern

CE integration tests require:
1. `DATABASE_URL=sqlite:///./test.db` env var for module-level CE import
2. `eng.db = MagicMock()` (prevents real DB calls)
3. `_routing_gate = MagicMock(return_value=(None, True))` (routing gate passes)
4. `state.last_intent = "PREPURCHASE_INSPECTION"` for deferred intercept tests
   (ensures Layer F service intent gate passes before AI is reached)

### FuzzyLookupResult constructor

Real fields: `outcome, hit, score, second_hit, second_score, gap, make_constrained`.
Not `match=` or `key=`.

### Deferred intercept flow order

Layer F (service intent gate) runs BEFORE the AI call. For deferred intercept to fire,
the text must pass Layer F. Setting `state.last_intent = "PREPURCHASE_INSPECTION"`
ensures step 7 passes (prior confirmed intent).

### Kill switch (CR30)

The main reply path in `_process_text` does NOT catch `OutboundBlockedError`.
Pre-gate wrappers (`_send_service_boundary`, `_send_inspectability_reply`) DO catch it.
CR30 uses F12 text so the pre-gate path catches the error and returns a blocked action.

---

## Test counts

| Suite | Tests | Result |
|-------|-------|--------|
| M21.1.7 targeted | 86 | 86 PASSED |
| Prior M21 (1.1–1.6) | 540 | 540 PASSED |
| M20 seven-file gate | 220 | 220 PASSED |
| Full suite delta | +86 new, 0 new failures | — |

---

## Safety checklist

- [x] Not deployed
- [x] Not pushed
- [x] n8n not started
- [x] Outbound not enabled
- [x] crm_test only (SQLite in-memory)
- [x] Production crm not connected
- [x] No external service calls
- [x] n8n not modified
- [x] Whisper prompts not modified
- [x] Pricing unchanged
- [x] Viáticos tables unchanged
- [x] Scheduling algorithms unchanged
- [x] No schema migration
- [x] No deterministic safety gate replaced with AI classification
- [x] Prior milestone expectations not modified
