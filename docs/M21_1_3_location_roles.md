# M21.1.3 — Location Semantic Roles and Candidate Persistence

**Approved:** 2026-08-05  
**Owner:** Lara Dittmar  
**Status:** Source of truth (locked)

---

## LR-1 — Inspection location owns commercial zone

The location used for quote, viáticos, availability/logistics, inspection revision, and location Flow completion is the physical location of the vehicle to be inspected.

That location must be persisted on the active vehicle candidate.

## LR-2 — Customer origin is not inspection location

Statements such as:

- "Soy de La Plata."
- "Vivo en Tigre."
- "Estoy en San Isidro."

describe customer origin/current location unless the message explicitly connects that place to the vehicle.

They must not overwrite candidate inspection location and must not drive pricing.

## LR-3 — Explicit vehicle-location language wins

Strong evidence includes:

- "El auto está en X."
- "El vehículo está en X."
- "Está para revisar en X."
- "Lo tienen en X."
- "Hay que verlo en X."
- "La revisión sería en X."
- "Está ubicado en X."

When one clear vehicle location is present, write it to the active candidate even if stale thread-state location exists.

## LR-4 — Bare locality in established clarification context

A bare locality reply such as "Palermo", "Tigre", or "Villa Urquiza" is vehicle inspection location when:

- the engine previously asked where the vehicle is;
- a location clarification or location Flow is active;
- the current candidate lacks resolved inspection location;
- or the established inspection conversation makes the reply unambiguously responsive.

Persist it on the candidate.

## LR-5 — Same-turn role distinction

Example:

> "Yo soy de La Plata, pero el auto está en Villa Urquiza."

Expected:

- customer origin: La Plata, informational only;
- candidate inspection zone: CABA / Villa Urquiza;
- pricing uses Villa Urquiza;
- thread `home_zone_*` is not overwritten as a proxy for vehicle location.

Do not add a customer-origin schema field in this milestone.

## LR-6 — Stale prior thread state never blocks current evidence

Precondition:

```text
state.home_zone_group = Norte
state.home_zone_detail = Tigre
```

Current message:

> "El auto está en Villa Urquiza."

Expected:

- candidate receives CABA / Villa Urquiza;
- current explicit evidence wins;
- stale thread state remains untouched unless existing cleanup semantics explicitly require otherwise;
- pricing uses the candidate location.

This is SC13.

## LR-7 — Candidate creation must not inherit stale thread zone

When a vehicle candidate is created:

- do not copy `state.home_zone_group`;
- do not copy `state.home_zone_detail`;
- leave candidate location empty unless current-turn or source-verified form/Flow evidence provides vehicle inspection location.

## LR-8 — Genuine same-turn contradiction

Example:

> "El auto está en Tigre, o puede ser Villa Urquiza, no sé."

Expected:

- ask exactly:
  `¿Dónde está físicamente el auto para hacer la revisión?`
- do not choose a location;
- do not mutate candidate zone;
- do not quote;
- do not schedule;
- do not create a revision;
- do not dispatch a location/booking Flow from that turn.

This is SC17.

## LR-9 — Multiple locations with clear roles are not contradictory

> "Vivo en Tigre, pero el auto está en Villa Urquiza."

Expected:

- Villa Urquiza is inspection location;
- Tigre is customer origin;
- no clarification.

## LR-10 — Current candidate is the persistence boundary

Location belongs to the active/focused candidate.

Do not implement broad multi-candidate switching in M21.1.3.

When no candidate exists:

- extract role evidence read-only;
- create the candidate through the existing normal path;
- then persist current-turn vehicle location to that candidate;
- do not store it as generic thread home zone merely to pass it forward.

## LR-11 — No pricing redesign

M21.1.3 may adapt pricing input selection so candidate location is authoritative.

It must not change:

- vehicle category lookup;
- zone-group mapping rules;
- zone-detail table contents;
- viáticos amounts;
- locality aliases;
- outside-coverage policy;
- PricingNotFoundError fallback policy.

## LR-12 — Mutation and precedence

Location-role processing occurs only after higher-priority gates have allowed commercial processing:

- motorcycle/human handoff;
- unsupported-service gates;
- BR-1;
- inspectability.

UNCERTAIN, informational, unsupported, motorcycle, or inspectability-boundary turns must not mutate candidate location.

## LR-13 — Kill switch

If a location-role clarification must be sent while outbound is disabled, return an existing or new handled action with `handled=True`, so n8n legacy fallback cannot continue.

Preferred clarification:

```text
¿Dónde está físicamente el auto para hacer la revisión?
```

---

## Scenario reference

| ID | Input | Expected |
|---|---|---|
| SC11 | "Yo soy de La Plata, pero el auto está en Villa Urquiza." | Candidate CABA/Villa Urquiza; La Plata ignored |
| SC12 | "Soy de La Plata." (bare origin, no vehicle clause) | No zone mutation |
| SC13 | state=Norte/Tigre + "El auto está en Villa Urquiza." | Candidate CABA/Villa Urquiza; stale guard bypassed; thread state untouched |
| SC14 | Candidate no-zone + clarification active + "Palermo" | Candidate CABA/Palermo |
| SC17 | "El auto está en Tigre, o puede ser Villa Urquiza, no sé." | Clarification sent; zero mutation |
