# RideCheck CRM — Project Context

## Canonical architecture documents

Before changing any of the following, **READ FIRST**:

- `conversation_engine.py`
- WhatsApp thread lifecycle
- Lead lifecycle
- Revision / inspection cycle lifecycle
- Candidate lifecycle
- Scheduling state
- Context assembly (messages, candidates, `_load_context`)
- Cycle boundary logic

**Required reading:**

- [`docs/architecture/DOMAIN_MODEL.md`](docs/architecture/DOMAIN_MODEL.md) — Owner-authoritative business model: persistent Lead/Thread, multi-revision lifecycle, field ownership matrix, returning customer contract, invariants, anti-patterns.
- [`docs/architecture/CONVERSATION_RUNTIME_CONTRACT.md`](docs/architecture/CONVERSATION_RUNTIME_CONTRACT.md) — CE runtime requirements: active-cycle context, cycle boundary, burst contract, latency thresholds, answer source taxonomy, human alert contract, historical context rules.

These documents contain owner-authoritative product architecture confirmed by WILD-04R audit (2026-08-24).

**If code conflicts with these documents:**

STOP.

Do not silently change the documents to match the code.

Report the mismatch as an architecture defect / owner decision required.

---

## Quick reference

| Question | Document | Section |
|---|---|---|
| What is a Lead? Can I create a new one per inspection? | DOMAIN_MODEL | §2 Core Entities, §8 Anti-Patterns |
| What fields survive between inspection cycles? | DOMAIN_MODEL | §4 Field Ownership Matrix |
| When does CE reset state for a new cycle? | CONVERSATION_RUNTIME_CONTRACT | §2 Cycle Boundary |
| How should CE filter messages and candidates? | CONVERSATION_RUNTIME_CONTRACT | §1 Active Revision Context |
| What are the latency SLA thresholds? | CONVERSATION_RUNTIME_CONTRACT | §5 Answer Performance Contract |
| What answer_source values are valid? | CONVERSATION_RUNTIME_CONTRACT | §6 Answer Source Contract |
| When does the human alert fire? | CONVERSATION_RUNTIME_CONTRACT | §7 Human Alert Contract |
| What is the CE stage lifecycle? | DOMAIN_MODEL | §5 Revision Lifecycle |

---

## Additional developer reference

- [`CLAUDE.md`](CLAUDE.md) — CE invocation path, n8n architecture, test environment, critical flags
- [`docs/lead_states_and_flags.md`](docs/lead_states_and_flags.md) — Canonical Lead.estado, flag, necesita_humano values and CRM endpoints
- [`backend/endpoint_inventory.md`](backend/endpoint_inventory.md) — API endpoint reference
