# M21.1.2 — Vehicle Inspectability Constraint

**Status:** APPROVED SOURCE OF TRUTH  
**Date:** 2026-08-04  
**Owner:** Lara Dittmar  
**Project:** RideCheck CRM

## 1. Scope

This milestone covers only:

- disassembled vehicles;
- assembled but non-running vehicles;
- access/logistics ambiguity;
- deterministic decline, clarification, or human escalation;
- prevention of quote, scheduling, revision, or Flow activity until inspectability is resolved.

It does not change BR-1, motorcycle routing, location roles, ASR, scheduling UX, n8n, or database schema.

## 2. Gate priority

Existing priority remains:

1. Motorcycle/quad/UTV handoff
2. Phone-call/human request
3. Formulario 12 / transfer / repair
4. FAQ/informational handling
5. BR-1 intent qualification
6. **Vehicle inspectability**
7. Candidate, zone, pricing, scheduling, revision, and Flow processing

Motorcycle always wins.  
Example: "Tengo una moto desarmada" → motorcycle handoff.

## 3. Approved business rules

### BR-I1 — Disassembled vehicle

RideCheck cannot inspect a materially disassembled vehicle.

Examples:

- "El auto está desarmado."
- "Tiene el motor afuera."
- "Está sin motor."
- "Está sin ruedas."
- "Está desmontado."

Required behavior:

- send the approved inspectability explanation;
- do not quote;
- do not schedule;
- do not create a revision;
- do not dispatch Flows;
- do not create/update a commercial candidate from that turn;
- do not set commercial flags;
- do not set `needs_human` by default.

Approved reply:

> **Para poder hacer la revisión, el vehículo tiene que estar armado y accesible. Si está desarmado, no podemos inspeccionarlo correctamente.**

### BR-I2 — Non-running but assembled

"No arranca" does not automatically make the vehicle uninspectable.

Examples:

- no arranca;
- no enciende;
- batería muerta;
- está parado;
- no está andando;
- hay que empujarlo o remolcarlo.

Required behavior:

- never use the disassembled decline from these signals alone;
- ask one concise clarification when accessibility is unclear;
- do not quote or schedule until clarified;
- escalate only for exceptional, contradictory, or unresolved logistics.

Approved clarification:

> **¿El vehículo está armado y se puede acceder normalmente al motor, interior, ruedas y parte inferior, aunque no arranque?**

### BR-I3 — Assembled and accessible confirmation

If the customer confirms that the vehicle is assembled and accessible, continue normally even if it does not start.

Examples:

- "Está armado; solo no arranca."
- "Está completo y se puede revisar."
- "No enciende, pero está armado y accesible."

Do not repeat the same clarification once resolved.

### BR-I4 — Inaccessible vehicle

If the vehicle is assembled but cannot be accessed safely or properly, do not quote automatically.

Examples:

- locked vehicle with no keys;
- no permission to inspect at the workshop;
- no access to the underside;
- restricted or unsafe location.

Ask one clarification when resolvable. Otherwise use human review and warm handoff.

### BR-I5 — Historical or hypothetical mentions

Past or hypothetical disassembly must not trigger a decline.

Examples:

- "Estuvo desarmado, pero ya está armado." → continue
- "Lo habían dejado sin motor, ahora está completo." → continue
- "¿Qué pasa si estuviera desarmado?" → informational
- unclear current condition → clarify

### BR-I6 — Mixed signals

- explicit current assembled/accessibility evidence wins over historical wording;
- true contradiction requires clarification;
- "No arranca y tiene el motor afuera" → disassembled boundary;
- "No arranca, pero está completo" → continue;
- do not guess.

## 4. Detection categories

### High-confidence disassembled

- desarmado/a
- desmontado/a
- sin motor
- motor afuera
- motor desmontado
- sin ruedas
- partes sueltas
- piezas desmontadas

### Non-running

- no arranca
- no enciende
- no prende
- batería muerta
- está parado
- no funciona
- no está andando
- hay que empujarlo
- hay que remolcarlo

### Assembled/accessibility confirmation

- está armado
- está completo
- se puede revisar
- se puede acceder
- se puede abrir
- se puede levantar
- tiene todas las ruedas
- el motor está puesto
- está accesible

### Historical/hypothetical qualifiers

- estaba
- estuvo
- antes
- ya
- ahora
- si estuviera
- hipotéticamente

Detection must be accent-insensitive, boundary-aware, and context-aware. Do not use global substring short-circuits.

## 5. Mutation rules

### Disassembled boundary

Allowed:

- processed-message/dedup bookkeeping.

Forbidden:

- candidate create/update;
- zone mutation;
- pricing;
- commercial lead flags;
- revision;
- scheduling;
- Flow dispatch;
- automatic `needs_human=True`.

### Non-running clarification

Until resolved, forbid:

- quote;
- scheduling;
- revision creation;
- commercial acceptance.

### Human escalation

Use only for unresolved ambiguity, exceptional logistics, contradictory answers, or unsafe/inaccessible conditions.

Kill-switch behavior must return `handled=True`.

## 6. Idempotency

- Repeated disassembled messages must not create duplicate candidates, revisions, Flows, or notifications.
- Repeated non-running messages must not cause an infinite clarification loop.
- Once assembled/accessibility is confirmed, do not ask again in the same established context.
- Existing `needs_human=True` still short-circuits automation.

## 7. Decision table

| ID | Current message | Expected |
|---|---|---|
| I01 | "El auto está desarmado." | Disassembled boundary |
| I02 | "Tiene el motor afuera." | Disassembled boundary |
| I03 | "Está sin ruedas." | Disassembled boundary |
| I04 | "No arranca." | Clarify assembled/accessibility |
| I05 | "No enciende, pero está armado y completo." | Continue |
| I06 | "Estuvo desarmado, pero ya está armado." | Continue |
| I07 | "Está medio desarmado, pero se puede revisar." | Clarify |
| I08 | "No arranca y tiene el motor afuera." | Disassembled boundary |
| I09 | "No arranca, pero se puede abrir y revisar todo." | Continue |
| I10 | "Está armado, pero no tenemos las llaves." | Clarify access |
| I11 | "Está en un taller y no sé si nos dejan revisarlo." | Clarify or human review |
| I12 | "¿Qué pasa si estuviera desarmado?" | Informational only |
| I13 | "Tengo una moto desarmada." | Motorcycle handoff |
| I14 | "Quiero revisar un auto desarmado en Palermo." | Disassembled boundary; no commercial mutation |
| I15 | Prior inspection intent + "Ahora me dicen que tiene el motor afuera." | Disassembled boundary |
| I16 | Prior clarification + "Sí, está armado y accesible." | Continue; do not repeat |
| I17 | Kill switch + disassembled | handled blocked action |
| I18 | Kill switch + clarification | handled blocked action |

## 8. Persistence

No schema migration is approved by default.

Audit existing state fields first. Add no new DB field without stopping for approval.

## 9. Launch and acceptance

M21.1.2 is complete when:

- I01–I18 are executable and green;
- BR-1, SI, and M20 compatibility gates remain green;
- no new deterministic-suite failures appear;
- no tests are newly hidden/deselected;
- image builds and imports;
- image is not deployed;
- runtime and production remain untouched.
