PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7A-TURN-EVIDENCE-SCHEMA

# L4.7A — TurnEvidence schema and provenance contract

Date: 2026-09-01
Schema and validation only · no ConversationEngine reordering · no prompt/model change ·
no live interpretation change · OUTBOUND OFF · crm_test only · production DB untouched.

---

## 1. What was built

`backend/app/schemas/turn_evidence.py` — schema version **`turn-evidence/1.0`**, the typed
contract that will sit between raw customer language and deterministic reconciliation:

```
RAW EVIDENCE  →  TURN EVIDENCE  →  CANONICAL STATE
(immutable)      (interpretation)   (deterministic reconciliation)
```

Pure pydantic. Its entire import set is `__future__`, `json`, `enum`, `typing`, `pydantic`
— asserted by an AST test — so it cannot reach a database, a service or the engine.
**Nothing in the runtime imports it yet**, verified by grep: this milestone adds a contract,
not a behaviour.

| Container | Carries |
|---|---|
| `TurnEvidence` | the whole turn; frozen; `iter_items()` yields stable refs like `location_mentions[0]` |
| `EvidenceItem` (base) | `field, value, normalized_value, role, status, confidence, alternatives, catalog_candidate, reason, provenance` |
| `ServiceIntentEvidence` | inspection interest, quote request, readiness, logistics offer |
| `VehicleEvidence` | `make, model, year, year_status, category_suggestion, is_superseded, mention_index` |
| `LocationEvidence` | `locality, zone_hint` + **mandatory `role`** |
| `FaqIntentEvidence` | one per topic |
| `AcceptanceEvidence` | `signal` ∈ ACCEPT / REJECT / HESITATE / QUESTION_ONLY / UNKNOWN |
| `SchedulingRequestEvidence` | `priority, day_expression, resolved_date, time, flexible_time, rank` |
| `CorrectionEvidence` | `relation` (5 values) + `from_value` / `to_value` / `target_ref` |
| `IdentityEvidence`, `HandoffEvidence` | identity mentions, handoff signal |
| `AmbiguityNote`, `ConflictNote` | alternatives / both sides preserved — no winner chosen |
| `Provenance`, `SourceSpan`, `TurnRef` | source kind, interpreter, model + schema version, source message ids, spans, burst reconstruction method |
| `ReconciliationRecord`, `ReconciliationLog` | **separate**, append-only dispositions |

## 2. Contract properties

**Unknown stays unknown.** Every value is optional, `confidence` defaults to `None` and is
never faked, an empty turn is legitimate (`is_empty()`), and `extra="forbid"` makes unknown
keys fail loudly instead of being silently dropped.

**No winner is chosen.** AMBIGUOUS and CONFLICT items carry no value; their alternatives and
both sides survive. `resolved` is False for them, so a reconciler cannot mistake them for
facts.

**Everything coexists.** FAQ intents, vehicles, locations, acceptance and scheduling live in
parallel collections — no single "intent" field can erase the rest. This is the L4.6
FAQ-bypass defect class made structurally impossible.

**Order is meaning.** Scheduling branches keep `priority` and `rank`; the Wild A input
represents as PRIMARY tomorrow 15:00 + FALLBACK Thursday open, and a time can never migrate
between branches.

**Roles are explicit and order-independent.** `INSPECTION_LOCATION`, `CUSTOMER_ORIGIN`,
`SELLER_LOCATION`, `UNKNOWN_LOCATION_ROLE`; both `"Está en Berazategui, pero yo soy de
Tigre."` and `"Yo soy de Tigre pero el auto está en Berazategui"` yield the same assignment.

**Provenance is auditable, including how the burst was assembled.**
`TurnRef.reconstruction` ∈ LIVE_DEBOUNCE / REPLAY_CHRONOLOGICAL / REPLAY_CAUSAL_MARKER /
CORPUS_FIXTURE / UNKNOWN — a direct answer to L4.7E's finding that historical burst grouping
is only PARTIAL. **No change to raw-message retention was made**; the audio-transcript
retention gap remains open and is tracked as its own future item, not folded in here.

**Interpretation is immutable; reconciliation is separate.** All models are frozen;
`TurnEvidence` has no reconciliation field and no apply/commit/save API. Dispositions
(ACCEPTED / REJECTED / DEFERRED / NEEDS_CLARIFICATION / CONFLICT_UNRESOLVED / SUPERSEDED)
are appended to a `ReconciliationLog` that returns a new log rather than mutating.

**Serialization is deterministic and versioned.** `to_canonical_json()` sorts keys and keeps
unicode; `from_json()` rejects a foreign prefix or a different major version, so an old
record can never be reinterpreted by a newer build. Minor bumps are additive-only.

## 3. Corpus compatibility — 162/162

`tests/semantic_corpus/corpus_mapping.py` maps every corpus case into the schema and back
into the L4.7E harness shape. All 162 cases:

- represent without dropping meaning (every expected field reappears);
- round-trip through the harness with **0 false positives, 0 false negatives, 0 unsupported
  inferences**;
- an unmapped corpus field raises rather than being silently dropped (asserted).

The 13 corpus fields map as: `service_intent` / `readiness` / `quote_request` /
`customer_logistics_offer` → `ServiceIntentEvidence.kind`; `vehicle` + `vehicle_year` +
`vehicle_superseded` → `VehicleEvidence`; `inspection_location` / `customer_origin` →
`LocationEvidence.role`; `faq_topics` → one `FaqIntentEvidence` per topic; `acceptance` →
`AcceptanceEvidence.signal` (hesitation is not acceptance); `scheduling_preference` →
ordered `SchedulingRequestEvidence`; `correction` → `CorrectionEvidence.relation`.

## 4. Tests — 47/47 PASS

`tests/test_l4_7a_turn_evidence_schema.py`: SCHEMA-01 (corpus × 162, plus meaning-preserved
and harness round-trip), SCHEMA-02 (no invented defaults, no faked confidence, empty turn
legitimate), SCHEMA-03 (coexistence incl. the WILD-B-01 FAQ+vehicle burst), SCHEMA-04
(make/model/year, model-only, multiple mentions, supersession, category as suggestion),
SCHEMA-05 (role separation, order-independence, all four roles, mandatory role), SCHEMA-06
(primary/fallback never collapsed, time never migrates, `resolved_date` optional),
acceptance/hesitation distinction, correction relations, SCHEMA-07/08 (ambiguity and
conflict preserve alternatives and both sides), SCHEMA-09 (item and turn provenance, stable
refs), SCHEMA-10 (version serialized, deterministic, lossless round-trip, major-version and
unknown-key guards), SCHEMA-11 (disposition outside interpretation, immutability,
append-only log), SCHEMA-12 (import-graph inspection, no mutating verbs, no ORM pull-in, no
apply/commit API).

Full regression: **3 241 passed / 55 failed / 9 errors** — the same pre-existing failure
set, **zero new failures**. `conversation_engine.py` untouched since L4.7D (`b889b67`).

## 5. Documentation

`docs/semantic/SEMANTIC_TRUTH_MODEL.md` gains §3.1 (implemented schema), §3.2 (versioning
rules), §3.3 (reconciliation boundary) and §3.4 (worked examples for the three Wild inputs
and the bare-"2008" ambiguity). The earlier provenance section now points at the
implementation instead of describing a future one — no contradictory definitions remain.

## 6. Status

L4.7A PASS. Next: **L4.7B-SHADOW-UNDERSTAND** — run a single UNDERSTAND pass in shadow
mode, emitting `TurnEvidence` alongside the current pipeline without changing any decision,
then compare against the corpus at L4.7B.1.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3** · no new Wild.
