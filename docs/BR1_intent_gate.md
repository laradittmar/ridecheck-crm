# BR-1 — Service Intent Gate (Layer F)

**Status:** APPROVED  
**Approved by:** Lara Dittmar  
**Date:** 2026-08-03  
**Scope:** RideCheck CRM — M21.1.1 service-intent qualification

## 1. Purpose

Layer F decides whether a `QUALIFYING` or uninitialized conversation continues into RideCheck’s inspection flow or receives a clarification reply.

RideCheck should avoid unnecessary friction. Messages that naturally look like an inspection enquiry are treated as inspection enquiries, even when the customer does not explicitly say “pre-purchase”.

## 2. Precedence

1. Motorcycle / scooter / quad / ATV / UTV → manual RideCheck handoff
2. Phone-call or human-agent request → human escalation
3. Formulario 12 → deterministic boundary
4. Transfer / paperwork → deterministic boundary
5. Repair / mechanical-service request → deterministic boundary
6. Pure informational FAQ → informational response path
7. Layer F

A higher-priority boundary wins even when inspection language is present.

## 3. Outcomes

| Outcome | Meaning |
|---|---|
| `CONTINUE_SET` | Continue and persist `last_intent = PREPURCHASE_INSPECTION`. |
| `CONTINUE` | Continue without changing `last_intent`. |
| `CONVERSATIONAL` | Answer naturally with no commercial mutation and no intent mutation. |
| `FAQ_WITH_CONTEXT` | Answer the FAQ and retain/create the identified vehicle context. Persist inspection intent. |
| `UNCERTAIN` | Ask one concise service-clarification question. No commercial mutation. |
| `BOUNDARY` | Already handled by a higher-priority gate. |

## 4. Approved decision table

| # | Message type | Example | Fresh | Established |
|---|---|---|---|---|
| 1 | Explicit inspection request | “Quiero revisar un Ford Focus.” | `CONTINUE_SET` | `CONTINUE_SET` |
| 2 | Explicit pre-purchase signal | “Estoy por comprar un auto.” | `CONTINUE_SET` | `CONTINUE_SET` |
| 3 | Inspection-specific price | “¿Cuánto sale la revisión?” | `CONTINUE_SET` | `CONTINUE_SET` |
| 4 | Generic price | “¿Cuánto sale?” / “precio” | `CONTINUE_SET` | `CONTINUE` |
| 5 | Bare vehicle | “Ford Ranger 2020” | `CONTINUE_SET` | `CONTINUE` |
| 6 | Bare location | “Palermo” | `CONTINUE_SET` | `CONTINUE` |
| 7 | Vehicle-location phrase | “El auto está en Palermo.” | `CONTINUE_SET` | `CONTINUE` |
| 8 | Vehicle + location | “Ford Ranger 2020 en Palermo.” | `CONTINUE_SET` | `CONTINUE` |
| 9 | Vehicle + year + location | “Ford Ranger 2020, está en Palermo.” | `CONTINUE_SET` | `CONTINUE` |
| 10 | Vehicle + generic price | “Ford Ranger 2020, ¿cuánto sale?” | `CONTINUE_SET` | `CONTINUE` |
| 11 | Bare concern | “Me preocupa el estado del auto.” | `UNCERTAIN` | `CONTINUE` |
| 12 | Generic help | “Necesito ayuda con un auto.” | `UNCERTAIN` | `CONTINUE` |
| 13 | Courtesy / soft close | “Gracias.” / “Los tengo en cuenta.” | `CONVERSATIONAL` | `CONVERSATIONAL` |
| 14 | General info request | “¿Me pasás info?” | `CONVERSATIONAL` | `CONVERSATIONAL` |
| 15 | Pure FAQ | “¿Qué incluye la revisión?” | `CONVERSATIONAL` | `CONVERSATIONAL` |
| 16 | FAQ + vehicle | “Es un Focus 2019, ¿qué revisan?” | `FAQ_WITH_CONTEXT` | `FAQ_WITH_CONTEXT` |
| 17 | FAQ + explicit inspection | “Quiero revisar un Focus 2019, ¿qué revisan?” | `CONTINUE_SET` | `CONTINUE_SET` |
| 18 | Contextual confirmation | “Perfecto.” | `UNCERTAIN` | `CONTINUE` |
| 19 | Scheduling follow-up | “A ver qué fechas tienen.” | `UNCERTAIN` | `CONTINUE` |

## 5. “¿Me pasás info?” copy

Answer concisely about RideCheck and finish with:

> **¿Tenés algún vehículo en vista?**

This message alone must not create a candidate, set a zone, calculate a price, schedule, create a revision, mutate a lead flag, or set `last_intent`.

## 6. Established context and provenance

A thread is established when the engine has reliable provenance that the conversation was already accepted as a RideCheck inspection enquiry.

Valid provenance:

1. `state.last_intent == PREPURCHASE_INSPECTION`
2. A prior processed customer message accepted under rows 1–10, 16, or 17
3. A vehicle/location clarification Flow sent because of such an accepted message
4. A validated RideCheck website inspection form
5. An existing inspection revision
6. A focused non-motorcycle candidate created during an accepted inspection flow

Supporting state may then be used:

- `last_stage == QUALIFYING`
- `vehicle_clarification_sent`
- `location_clarification_sent`
- focused candidate
- known vehicle/location

These fields must not establish intent by themselves when they have no reliable provenance.

## 7. Launch reset

Before production launch, existing prelaunch leads, threads, and conversation data will be cleared. Seeded-state tests remain useful defensively, but seeded state is not equivalent to a real prior processed turn unless the fixture explicitly models provenance.

## 8. Mutation rules

Before Layer F permits the commercial path, detection must be read-only.

`UNCERTAIN` and `CONVERSATIONAL` must not create/update candidates, mutate zones, call pricing, create revisions, schedule, dispatch Flows, mutate lead flags, or set `last_intent`.

## 9. Change control

1. This document is the source of truth for Layer F.
2. Executable BR-1 tests must remain synchronized.
3. Settled rows change only through an explicit business decision approved by Lara.
4. Blocking reviewer findings must map to a failing test against this specification.
