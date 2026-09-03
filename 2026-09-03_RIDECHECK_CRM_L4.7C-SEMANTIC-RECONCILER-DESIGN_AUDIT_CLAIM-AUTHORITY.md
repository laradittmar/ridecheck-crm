PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7C-SEMANTIC-RECONCILER-DESIGN

# L4.7C — Claim authority: how semantic evidence becomes canonical state

Date: 2026-09-03
**Design only. No code changed, no migration, no runtime behaviour change, no authority
moved.** OUTBOUND OFF · production DB untouched · Wild clean count 0/3.

---

## 0. The finding that shapes everything else

RideCheck already has a reconciler-shaped layer in production, and it is read-only:
**`backend/app/services/field_evidence.py`** (M21.1.5, "Central Field-Evidence Resolver").
It resolves eight fields — service intent, vehicle, year, category, inspection location,
customer origin, inspectability, scheduling — from the turn, the candidate, the thread state,
the revision, the Flow and the website form; it labels each with a **source**
(`CURRENT_TURN_EXACT`, `FLOW`, `CANDIDATE`, `THREAD_STATE`, `REVISION`, `AI_EXTRACTED`,
`DERIVED`, `NONE`), a coarse **confidence**, a `confirmed` flag and a `current_turn` flag,
and it mutates nothing. `ConversationEngine` consumes it at three points.

So the design question is not "what reconciler should we build?" but **"what is the smallest
set of additions that lets `FieldEvidenceSnapshot` accept semantic claims, justify its
answers, and separate reconciliation from authorization?"** That framing keeps the migration
small and is the recommendation of this audit.

---

## 1. Target architecture

```
RAW EVIDENCE  (immutable inbound messages, Flow submissions, form posts)
      │
      ├─► SEMANTIC INTERPRETER ──► TurnEvidence            (proposes; zero authority)
      └─► DETERMINISTIC EXTRACTORS ──► FieldEvidence       (extracts; narrow, precise)
                    │
                    ▼
            CLAIM PROJECTION            ← one atomic claim per (type, value, source)
                    ▼
            VALIDATION                  ← schema, vocabulary, span, cycle scope
                    ▼
            RECONCILIATION POLICY       ← "which claims are sufficiently supported?"
                    ▼                     produces CANONICAL STATE + JUSTIFICATION
            CANONICAL STATE  (candidate · revision · thread state — unchanged tables)
                    ▼
            ACTION AUTHORIZATION        ← "what business action may now occur?"
                    ▼                     separate rules, risk-tiered, preconditions
      Pricing · Scheduling · Booking · Handoff   (deterministic services, unchanged)
                    ▼
            COMPOSE  ──►  CANONICAL RESPONSE VALIDATOR (L4.7D) ──► OutboundSafetyGate ──► send
```

**Reconcile ≠ authorize.** Reconciliation answers *what is true enough to record*;
authorization answers *what may now be done about it*. A fact can be canonical and still
authorize nothing: a confirmed vehicle does not authorize a quote, a confirmed quote does not
authorize a booking. Keeping them in one step is exactly how "the customer mentioned a day"
becomes "the customer booked".

---

## 2. Part 1 — the atomic claim model

**Yes: project both evidence streams into atomic claims before reconciling.** TurnEvidence is
organised by *how the interpreter thinks* (intents, vehicles, locations); reconciliation needs
evidence organised by *what canonical field it touches*. The projection is mechanical and
lossless, and it is where the two producers become comparable.

```python
@dataclass(frozen=True)
class Claim:
    claim_type: str          # "vehicle.model", "inspection_location", "quote_accepted", …
    value: Any               # normalised where a normaliser exists, raw otherwise
    polarity: Polarity       # ASSERTED | NEGATED            (Part 2)
    status: EvidenceStatus   # CONFIRMED | PROPOSED | AMBIGUOUS | CONFLICT
    evidence_class: EvidenceClass   # EXPLICIT_CUSTOMER | SEMANTIC_INFERRED | …  (Part 4)
    producer: str            # "semantic:understand/1.18" | "ce:vehicle_catalog" | "flow"
    producer_version: str
    source_message_ids: tuple[str, ...]
    source_span: Optional[SourceSpan]
    explicitness: Explicitness      # STATED | IMPLIED | DERIVED
    temporality: Temporality        # PRESENT | PAST | FUTURE | HYPOTHETICAL
    modality: Modality              # FACTUAL | CONDITIONAL | INTERROGATIVE
    confidence: Optional[float]     # advisory only, never load-bearing  (Part 14)
    cycle_id: str                   # current inspection cycle
    revision_id: Optional[int]
    created_at: str
    supersedes: tuple[str, ...] = ()   # claim ids this one replaces
    claim_id: str = ""                 # stable hash of the fields above
```

Four fields carry most of the safety value and none of them exist today:

* **`polarity`** — "el auto no está en Tigre" is evidence *against* Tigre, not evidence for it.
* **`temporality` / `modality`** — *"si me cierra te hablo"* is FUTURE + CONDITIONAL and can
  never satisfy an acceptance precondition, no matter how confidently it is read. This is the
  single most important field in the model: it is what makes Part 7 enforceable rather than
  hopeful.
* **`explicitness`** — separates what the customer said from what a producer added.
* **`cycle_id`** — a claim from a finished cycle is not evidence about this one (the L4.6
  stale-candidate defect class, enforced structurally instead of by convention).

Claims are **derived, not stored** in phase C1: they are computed per turn from TurnEvidence +
FieldEvidence and passed to reconciliation. Only the reconciliation record is persisted
(§16). If replay later needs the claims themselves, they are reconstructible from the shadow
record plus the raw messages.

---

## 3. Part 2 — information states

Four states, and the distinction between them is the point:

| State | Meaning | Produced when |
|---|---|---|
| **NEITHER** | no evidence either way | no claim of that type in scope |
| **TRUE_ONLY** | positive evidence only | ≥1 ASSERTED claim, no NEGATED claim |
| **FALSE_ONLY** | negative evidence only | ≥1 NEGATED claim, no ASSERTED claim |
| **BOTH** | contradictory evidence | ASSERTED and NEGATED, or two incompatible ASSERTED values |

Mapping to the existing `turn-evidence/1.1` statuses — additive, no schema break:

```
CONFIRMED  + ASSERTED → TRUE_ONLY (strong)     CONFIRMED + NEGATED → FALSE_ONLY (strong)
PROPOSED   + ASSERTED → TRUE_ONLY (weak)       PROPOSED  + NEGATED → FALSE_ONLY (weak)
AMBIGUOUS             → NEITHER, with alternatives preserved
CONFLICT              → BOTH
absence of any claim  → NEITHER      ← never FALSE_ONLY
```

**Absence is never false.** This is the owner's L4.7B quality exception made structural: the
measured residual is that the interpreter sometimes omits `SEARCHING_NOT_READY` beside
`FUTURE_INTENT`. Under this model that omission yields `readiness = NEITHER`, which cannot
satisfy any precondition and cannot authorise progression. **The known weakness of the
interpreter is contained by the reconciler's information model rather than by hoping the
interpreter improves.**

Corollary, stated as a rule for implementation: *no authorization precondition may be
expressed as "not X"*. Preconditions are written positively — `readiness == TRUE_ONLY`,
`acceptance == TRUE_ONLY` — so a missing claim blocks rather than permits.

---

## 4. Part 3 — the authority matrix

Producers: **SEM** = semantic interpreter · **CE** = deterministic extractor ·
**CAT** = vehicle catalog · **ZONE** = `ViaticosZone` resolver · **PRICE** = `PricingService` ·
**SCHED** = `ScheduleService` · **FLOW** = WhatsApp Flow submission · **HUM** = operator.

| Claim | Producers | SEM role | CE role | Canonical acceptance rule | Conflict | Clarify | Authorizes |
|---|---|---|---|---|---|---|---|
| `service_intent` | SEM, CE, FLOW | primary | secondary | TRUE_ONLY from either; PROPOSED is enough (low risk) | union | no | nothing on its own |
| `vehicle.make` | SEM(suggest), CAT, FLOW, HUM | **suggest only** | extract | **CAT confirms**; SEM alone → `catalog_candidate`, never canonical | CAT wins over SEM; two CAT hits → AMBIGUOUS | if AMBIGUOUS | candidate creation |
| `vehicle.model` | SEM, CE, CAT, FLOW, HUM | propose | extract | EXPLICIT_CUSTOMER + CAT-resolvable → canonical | CAT arbitrates | if unresolvable | candidate creation |
| `vehicle.year` | SEM, CE, FLOW, HUM | propose | extract (`_extract_year_from_text`) | agreement, or single producer with EXPLICIT_CUSTOMER | AMBIGUOUS → keep both | yes | pricing needs it only where category depends on it |
| `vehicle.category` | CAT, FLOW, HUM | **none** | none | **CAT only** (`tipo_vehiculo`) | n/a | n/a | pricing |
| `inspection_location` | SEM, CE, ZONE, FLOW, HUM | role assignment | locality extraction | SEM/CE proposes locality → **ZONE validates** → canonical zone_group/zone_detail | unresolved role → CLARIFY | yes | pricing (viáticos), scheduling (travel) |
| `customer_origin` | SEM, CE | primary | `_has_customer_origin_clause` | recorded, **never** substituted for inspection location | keep both | no | nothing |
| `seller_location` | SEM | primary | none | recorded | keep both | no | nothing |
| `quote_request` | SEM, CE (`_wants_price_quote`) | primary | secondary | TRUE_ONLY from either | union | no | permits *computing* a quote, never a price |
| `quote_accepted` | SEM, FLOW, HUM | **interpret only** | acceptance heuristics | **HIGH RISK** — see §8 | never auto-resolve | yes | stage transition, scheduling |
| `future_intent` | SEM | primary | none | TRUE_ONLY | — | no | nothing; explicitly **blocks** acceptance |
| `searching_not_ready` | SEM | primary | none | TRUE_ONLY; **absence = NEITHER, not "ready"** | — | no | blocks quote/scheduling progression when TRUE |
| `correction/replacement` | SEM, CE | primary | none | relation recorded; supersession applied (§11) | — | if target unclear | invalidation of derived facts |
| `scheduling.preference` | SEM, CE, FLOW | interpret day/time/order | `_parse_scheduling_requests` | agreement, else SEM's ordered branches with CE as validator | disagreement → CLARIFY | yes | slot search only |
| `availability` | **SCHED only** | none | none | `ScheduleService.check/list_slots` | n/a | n/a | slot offer |
| `price` | **PRICE only** | **none** | none | `PricingService.quote(...)` | n/a | n/a | quote message |
| `booking_confirmed` | **FLOW only** | none | none | `_process_flow_response` → `ThreadRevision(status="booked")` | n/a | n/a | booking |
| `business_exception` | CE, SEM, HUM | detect | `_is_outside_coverage`, `_detect_disassembled_vehicle`, … | either producer raises it | union | — | **NEEDS_HUMAN** |
| `needs_human` | CE, SEM, HUM | may request | phone-call / handoff detectors | any TRUE_ONLY | union | — | handoff |

Three invariants fall out of the matrix and should be asserted in tests:

1. **No claim type lists SEM as its sole canonical authority.** Every claim SEM can produce is
   either validated by a deterministic authority (catalog, zone), or is consequence-free
   (origin, seller location, FAQ), or is high-risk and gated by §8.
2. **Price, availability and booking have no semantic producer at all** — the interpreter is
   structurally unable to express them (enforced today in the prompt and asserted by
   `test_interpreter_has_no_business_imports`).
3. **Authority is per claim type, never per producer.** SEM outranks CE on location *roles*;
   CE and CAT outrank SEM on vehicle *identity*. Neither "wins" globally.

---

## 5. Part 4 — explicit vs inferred

| Class | Meaning | May reach canonical alone |
|---|---|---|
| `EXPLICIT_CUSTOMER` | the customer said it in words | yes, for low/medium-risk claims |
| `SEMANTIC_INFERRED` | the interpreter added it | **no** — becomes `catalog_candidate` / PROPOSED |
| `DETERMINISTIC_EXTRACTED` | a CE parser matched it | yes, where the matrix says so |
| `CATALOG_CONFIRMED` | the catalog resolved it | yes — authoritative for identity/category |
| `SERVICE_COMPUTED` | PricingService / ScheduleService produced it | yes — exclusive for price/availability |
| `HUMAN_CONFIRMED` | an operator entered or approved it | yes — highest |

Worked example, *"Quiero comprar un Fox"*:

```
raw            "fox"                              → EXPLICIT_CUSTOMER   model=Fox
interpreter    make=Volkswagen (status PROPOSED)  → SEMANTIC_INFERRED   catalog_candidate
catalog        lookup_vehicle("fox") → unique     → CATALOG_CONFIRMED   marca=Volkswagen
canonical      modelo=Fox (explicit) · marca=Volkswagen (catalog) · tipo from catalog
```

The interpreter's *Volkswagen* did not create canonical make **because a model-name→brand
mapping is a catalog fact, and the catalog is the register of that fact.** If the catalog
resolves it, the canonical value comes from the catalog and is reproducible against a catalog
version; if the catalog does not, the customer is asked. Letting the model supply it would
make canonical state depend on model weights, unversioned and unreproducible — and the L4.7B.3
measurements showed the same model naming the brand in one draw and omitting it in the next.
Same suggestion, different draws: exactly the thing that must not be authoritative.

---

## 6. Part 5 — vehicle reconciliation

The existing candidate lifecycle is **preserved**: `WhatsAppThreadCandidate`
(marca/modelo/anio/tipo_vehiculo/zone_group/zone_detail/status) with the focus candidate on
`WhatsAppThreadState.current_focus_candidate_id`. Reconciliation writes through this lifecycle;
it does not replace it.

| Case | Rule |
|---|---|
| unique vehicle | claims agree → canonical; catalog fills marca/tipo |
| model-only | model canonical (EXPLICIT_CUSTOMER); make from CAT if unique, else `NEITHER` + clarify |
| numeric model | never resolved by SEM alone; `_contextual_numeric_model_lookup` arbitrates; unresolved → AMBIGUOUS + clarify, never a guess |
| year ambiguity | two year-shaped numbers → AMBIGUOUS with both alternatives; **do not pick**; the interpreter already preserves this and the reconciler must not collapse it |
| correction | supersession (§11): old value marked superseded, new value canonical, derived facts invalidated |
| replacement | a **new candidate**, not an edited one — history is preserved and the previous candidate keeps its own quote/scheduling context |
| multiple candidates | all recorded; exactly one is focus; focus changes are an explicit claim, never a side effect |
| switch-back | `SWITCH_TO_PRIOR_CANDIDATE` only when the prior candidate exists **in this cycle**; otherwise clarify |

---

## 7. Part 6 — location reconciliation

Two independent decisions, and conflating them is what broke Wild B: **which locality**
(extraction, then `ViaticosZone` validation) and **what role** (semantic).

```
"Está en Berazategui, pero yo soy de Tigre."
  SEM   Berazategui → INSPECTION_LOCATION ; Tigre → CUSTOMER_ORIGIN     (roles)
  ZONE  Berazategui → zone_group/zone_detail ; Tigre → known locality   (validity)
  canonical  inspection_location = Berazategui (+zone) · customer_origin = Tigre
```

Rules: a locality stated *about the car* is the inspection location; only a sentence about the
customer yields origin; **origin never substitutes for inspection location**; an unresolvable
role stays `UNKNOWN_LOCATION_ROLE` and triggers clarification; a locality the zone resolver
does not know is a candidate for the out-of-coverage path, not a canonical zone. If two
localities are both claimed as the inspection location, the state is `BOTH` → **CLARIFY**, and
under no circumstance is one silently chosen. Group C now measures 1.000/1.000 with 1.000 role
accuracy, so the semantic side of this is the strongest part of the interpreter — but zone
validity remains deterministic because pricing depends on it.

---

## 8. Part 7 — acceptance, stance and readiness (highest risk)

| Signal | Canonical meaning | Authorizes |
|---|---|---|
| `ACCEPT` | agrees to the concrete proposal outstanding now | acceptance, **only with §8 preconditions** |
| `REJECT` | declines it | stage stays, follow-up path |
| `HESITATE` | doubt about the proposal | nothing; remain available |
| `FUTURE_INTENT` | will come back later | nothing; **explicitly blocks** acceptance |
| `QUESTION_ONLY` | a question, no stance | nothing |
| `UNKNOWN` / absent | `NEITHER` | nothing |
| `SEARCHING_NOT_READY` | still choosing which car to buy | **blocks** quote/scheduling progression while TRUE |

`quote_accepted = TRUE` requires **all** of:

1. a claim with `signal == ACCEPT`, `polarity == ASSERTED`, `temporality == PRESENT`,
   `modality == FACTUAL` — a conditional or future acceptance is structurally disqualified;
2. a quote **already delivered in this cycle** (`Revision.precio_total` set and an outbound
   quote message recorded for this cycle);
3. the accepted quote is the **current** one — no candidate, location or category change since
   it was sent (§10 invalidation);
4. `searching_not_ready != TRUE_ONLY`;
5. no unresolved CONFLICT on candidate or inspection location.

*"si me cierra te hablo"* fails at (1) on both temporality and modality — before any
confidence or precision consideration. Absence of `SEARCHING_NOT_READY` never contributes to
(4) being satisfied *positively*; it is written as "not TRUE_ONLY" precisely because the
blocking direction is the safe one: a missing readiness claim leaves the block off, so
condition (4) is the one place absence is permissive, and it is compensated by (2) and (3),
which require deterministic state that no interpreter can fabricate.

---

## 9. Part 8 — scheduling interface

```
SEM  → ordered branches: {priority, day_expression ∈ vocabulary, time HH:MM|null,
                          flexible_time}          — no ISO dates, ever
      ↓
RECONCILER → resolve_day(day_expression, current_local_date, timezone) → concrete date
      ↓
SCHED → ScheduleService.check() / list_slots()  → business hours, travel, occupancy, slots
      ↓
canonical: requested_date/time (what was asked) + offered_slots (what is possible)
```

The split is already implemented in the interpreter (relative days stay relative;
`resolved_date` is dropped by the mapper) and in `ScheduleService`. What the reconciler adds is
the deterministic `resolve_day` step and the rule that **a requested slot is never a booking**:
`ThreadRevision(status="booked")` is created only by `_process_flow_response`, which stays
true under this design.

---

## 10. Part 9 — price and quote authority, and the dependency graph

`PricingService.quote(db, tipo_vehiculo, zone_group, zone_detail)` is the **only** producer of
an amount. The semantic layer may say *a quote was requested / accepted / rejected / doubted*
and nothing else; the L4.7D validator already blocks any amount the AI introduces.

```
candidate(marca, modelo, anio) ──► catalog ──► tipo_vehiculo ─┐
inspection_location ──► zone resolver ──► zone_group/detail ──┴──► PricingService ──► quote
                                                                         │
                                                                         ▼
                                                              acceptance (needs quote)
                                                                         ▼
                                                              scheduling ──► booking
```

Invalidation is the mirror of that graph:

| Change | Invalidates |
|---|---|
| candidate replaced / category changed | quote → acceptance → offered slots |
| inspection location / zone changed | quote (viáticos) → acceptance → travel-validated slots |
| quote recomputed with a different total | prior acceptance (must be re-obtained) |
| year corrected where category depends on it | category → quote → acceptance |
| booking created | freezes candidate, location and quote for that revision |

---

## 11. Part 10 — truth maintenance, minimally

**No event-sourcing platform.** The minimum RideCheck needs is a *justification* per canonical
field plus a *static* dependency table:

```python
@dataclass(frozen=True)
class Justification:
    claim_type: str
    canonical_value: Any
    evidence_ids: tuple[str, ...]     # the claims that supported it
    rule_id: str                      # "vehicle.identity.catalog_arbitration"
    rule_version: str                 # "1.0"
    decided_at: str
    cycle_id: str
    revision_id: Optional[int]
    depends_on: tuple[str, ...]       # claim types this value was derived from
```

Every canonical value answers *"VALUE, because [evidence], under [rule@version]"*. Invalidation
is a table lookup over `depends_on`, not a general reasoner: when a claim type changes, every
justification depending on it is marked `STALE` and its canonical value is recomputed or
cleared. Roughly 20 rows of dependency table cover the whole product.

Where it lives: `Justification` rows for the *current* cycle can be held in the reconciliation
log (§16) with the canonical value continuing to live in `candidate` / `revision` /
`thread_state`. **No new canonical tables. One new append-only log table at most.**

---

## 12. Part 11 — correction and supersession

Append-only. *"Es 2015, no 2014"*:

```
claim A  vehicle.year = 2014   EXPLICIT_CUSTOMER  (earlier turn, kept)
claim B  vehicle.year = 2015   EXPLICIT_CUSTOMER  supersedes=[A]
correction  CORRECT_EXISTING  from=2014 to=2015
canonical projection: vehicle.year = 2015  ·  justification cites B and the correction
```

Nothing is erased: A remains in the log, and the audit answer to "why 2015?" is complete.
Recalculation triggered: category (if year-dependent) → quote → acceptance → offered slots.
The companion-evidence work in L4.7B.4 is what makes this reliable — a corrected value now
arrives with its relation, and where the interpreter omits the relation the mapper derives it
from a named superseded vehicle.

---

## 13. Part 12 — conflict policy

| Outcome | When | Example |
|---|---|---|
| **ACCEPT** | one state, no contradiction, authority satisfied | single locality, catalog-resolved vehicle |
| **CLARIFY** | `BOTH`, or `AMBIGUOUS` with ≥2 plausible alternatives, on a LOW/MEDIUM claim, **and** no clarification for this claim already pending in this cycle | two candidate models; unresolved location role |
| **HOLD** | `NEITHER` on a claim needed for the next step, low risk, and the conversation can proceed | year unknown while quoting a category that does not need it |
| **NEEDS_HUMAN** | material conflict on a HIGH claim · business exception · the **second** unresolved clarification on the same claim in a cycle · out-of-coverage · inspectability blocked | acceptance conflicting with a superseded quote |

Escalation fatigue is controlled by three explicit limits: at most **one open clarification per
claim type per cycle**, at most **two clarification rounds** before escalation, and no
clarification for a claim the next action does not need. Conflict is always *recorded* even
when the outcome is HOLD — disagreement is never averaged away.

---

## 14. Part 13 — risk tiers

| Tier | Claims | Evidence required | Extra preconditions |
|---|---|---|---|
| **LOW** | customer_origin, seller_location, FAQ topics, service_intent | one PROPOSED claim | none |
| **MEDIUM** | vehicle identity/year/category, inspection location, quote_request, scheduling preference | EXPLICIT_CUSTOMER or DETERMINISTIC_EXTRACTED, catalog/zone-validated where applicable | no unresolved CONFLICT |
| **HIGH** | quote_accepted, slot selection, booking creation, business exception, needs_human | EXPLICIT_CUSTOMER, PRESENT + FACTUAL, plus the deterministic state the action depends on | §8 preconditions; a HIGH action is never taken on SEMANTIC_INFERRED evidence alone |

---

## 15. Part 14 — confidence policy

Model confidence is **advisory**. It may order a review queue, tip a borderline case toward
CLARIFY, or trigger abstention. It may **not** grant canonical authority, override
deterministic evidence, authorise a quote, a booking or a state transition, or break a tie
between two producers. This is already true in code — `_confidence()` clamps it and nothing
reads it back — and the reconciler must keep it that way: no rule may take `confidence` as an
input to an ACCEPT decision.

---

## 16. Part 15 — the existing CE parsers, classified

Roughly forty deterministic helpers exist in `conversation_engine.py`. Discarding them would
throw away years of precision; promoting them to primary NLP would repeat the L1 mistake.

| Class | Meaning | Examples |
|---|---|---|
| **A. Authoritative validator** — keep permanently, they arbitrate | `vehicle_catalog.lookup_vehicle`, `fuzzy_lookup_vehicle`, `_contextual_numeric_model_lookup`, `extract_model_del_year`; `ViaticosZone` resolution (`_extract_zone_from_text`); `_normalize_tipo_vehiculo`; `ScheduleService`; `PricingService` |
| **B. Secondary evidence producer** — keep as a second opinion, feed into claims | `_extract_year_from_text`, `_parse_scheduling_requests`, `_scan_day_tokens`, `_scan_time_tokens`, `_has_customer_origin_clause`, `_select_slot_from_offered`, `_detect_time_period`, `_parse_website_form` |
| **C. Business-rule logic** — not NLP at all, unaffected by this design | `_is_outside_coverage`, `_should_escalate_scheduling_to_human`, `_tipo_compatible`, `_nearest_slots`, `_scrub_scheduling_confirmation`, inspectability detectors (`_detect_disassembled_vehicle`, `_detect_non_running_vehicle`, `_detect_access_barrier`), `_faq_hours_answer` |
| **D. Redundant natural-language gate — retire after C6** | `_is_acceptance` / `_has_acceptance_word`, `_wants_price_quote`, `_is_general_faq_or_soft_close`, `_is_generic_vehicle_text`, `_has_vehicle_concern`, `_is_phone_call_request`, `_detect_historical_or_hypothetical_context`, `_strip_customer_origin_clauses` |

Class D is precisely the set the corpus shows the interpreter now handles better (stance exact
0.875, false ACCEPT 0.000, group C 1.000/1.000) — **and it is retired last, in C6, only after
replay certification shows the reconciler reproduces or improves their decisions.**

---

## 17. Part 16 — the reconciliation record: EXTEND

The L4.7A `ReconciliationRecord` (`evidence_ref`, `status`, `reason`, `decided_by`,
`decided_at`, `canonical_value`) is the right shape and roughly half the fields. Additions:

| Field | Why |
|---|---|
| `claim_type` | records are per claim, not per evidence item |
| `evidence_ids: tuple[str,...]` | a decision usually rests on several claims |
| `candidate_values: tuple[Any,...]` | what was considered, not only what won |
| `rule_id`, `rule_version` | reproducibility; `decided_by` is too coarse |
| `information_state` | NEITHER / TRUE_ONLY / FALSE_ONLY / BOTH |
| `outcome` | ACCEPT / CLARIFY / HOLD / NEEDS_HUMAN (distinct from `status`) |
| `cycle_id`, `revision_id` | scope |
| `depends_on`, `supersedes` | invalidation and history |
| `risk_tier` | audit of HIGH-risk decisions |

`ReconciliationLog` stays append-only and its `append()` contract is unchanged. Verdict:
**EXTEND** — additive, `turn-evidence/1.2`, backward compatible under the existing
major-version guard.

---

## 18. Part 17 — reproducibility, honestly

Reproducible **by construction**: the reconciliation decision, given the claims. Rule ids and
versions, the dependency graph and the append-only log make "why did canonical state say X?"
answerable offline, forever.

Reproducible **in practice, with drift**: the claims themselves. Deterministic claims replay
exactly at a pinned code version. Semantic claims do **not** — the same prompt and model
produced measurably different draws (group I recall 0.737 vs 0.684 on consecutive runs). The
honest guarantee is therefore: *the shadow record stores what the interpreter said, so a
historical decision can always be re-explained; re-running the model may produce different
claims and therefore a different decision.* Do not promise bit-exact historical
reconstruction of model output — record it instead, which the shadow recorder already does.

Practical requirement: retain, per turn, the TurnEvidence JSON, prompt/model version, the
deterministic FieldEvidence snapshot, rule versions, and the catalog/zone/pricing data version
in force.

---

## 19. Part 18 — response validator interface

`response_validator.validate_response(text, CanonicalFacts)` already blocks vehicle, location,
price, availability, booking and acceptance claims the canonical state does not support. Under
this design **`CanonicalFacts` is built from the reconciliation output, not re-derived**: each
field is populated from the canonical projection plus its justification, and
`acceptance_confirmed` / `booking_confirmed` come from the authorization layer rather than
from any reading of the conversation. One semantic authority, consumed twice — never two
interpreters disagreeing about what was proven.

---

## 20. Part 19 — runtime metrics for Wild certification

Corpus precision/recall says nothing about consequences. Before and during Wild, measure:

| Metric | Definition | Target |
|---|---|---|
| **false progression rate** | turns advancing stage/quote/booking without the required evidence | **0** |
| unnecessary clarification rate | clarifications asked where evidence was already sufficient | low, trended |
| human escalation rate | turns ending in NEEDS_HUMAN | trended; spikes are signal |
| conflict detection recall | real contradictions surfaced ÷ contradictions present | high |
| automatic-resolution coverage | turns reconciled with no clarification and no escalation | trended up |
| quote error rate | quotes whose inputs were later invalidated | **0** |
| booking error rate | bookings on stale candidate/location/quote | **0** |
| semantic abstention rate | turns where the interpreter proposed nothing usable | trended |

The three zero-targets are launch-blocking; the rest are trends that tell whether automation is
earning its keep.

---

## 21. Part 20 — implementation plan (not implemented)

| Phase | Scope | Risk | Rollback | Tests | Authority change |
|---|---|---|---|---|---|
| **C1 primitives + log** | `Claim`, `Polarity/Temporality/Modality/Explicitness`, information states, extended `ReconciliationRecord`, append-only log, claim projection from TurnEvidence **and** FieldEvidence. Shadow: reconcile and log, change nothing. | very low | flag off; log only | projection round-trip; absence ≠ false; state lattice | **none** |
| **C2 vehicle + location** | Reconcile these two domains for real, writing through the existing candidate lifecycle; catalog and zone remain arbiters. | medium | per-claim flag reverts to CE path | catalog arbitration, role split, AMBIGUOUS preserved, replacement creates a candidate | SEM becomes a *proposer of record* for two domains |
| **C3 intent / stance / acceptance** | The §8 preconditions, temporality/modality gate, readiness blocking. | **high** | flag; CE acceptance heuristics remain until C6 | conditional never accepts; absence never authorises; false-progression suite | acceptance authority formalised |
| **C4 scheduling interface** | `resolve_day` + the SEM↔SCHED contract; requested ≠ booked. | medium | flag | relative-day resolution, branch order, no booking from a preference | none new |
| **C5 derived-state invalidation** | Dependency table + justification staleness. | medium | recompute-on-read fallback | candidate change invalidates quote; location change invalidates viáticos; quote change invalidates acceptance | none new |
| **C6 authority cutover** | Retire class-D parsers; `CanonicalFacts` sourced from reconciliation. | high | keep D behind a flag for one release | parity: reconciler ≥ CE on the corpus and on replayed real threads | old NLP gate removed |
| **C7 replay certification** | Replay stored threads through the reconciler; publish the §20 metrics; then and only then consider a Wild. | low | n/a — read-only | replay determinism, zero false progression | none |

Each phase is independently shippable, flag-guarded, and reversible. **Migration size: MEDIUM.
No big-bang rewrite**; no canonical table is replaced; at most one append-only log table is
added.

---

## 22. Part 21 — the no-phrase-patch rule stands

Unchanged and carried into this design: *no production fix may consist solely of adding a
phrase, regex or alias so that one Wild sentence passes, unless it implements a documented
general semantic invariant and is accepted by the corpus.* Reconciliation rules are invariants
over claim types — never over sentences — and each carries a `rule_id` and version so that a
rule, unlike a phrase, can be reviewed and replayed.

---

## 23. Quality exception, recorded

L4.7B.4 passed **9 of 10** gate lines. The tenth — every group A–L recall ≥ 0.70 — fails with
group L at 0.636 and group I straddling at 0.737/0.684. The owner accepts this **for
reconciler design only**. It does not authorise semantic evidence to mutate canonical state,
and the recorded metrics stand unaltered: L4.7B is **not** marked complete or perfect.

The exception is defensible precisely because the residual is an **omission** class, and §3
makes omission structurally harmless: a missing `SEARCHING_NOT_READY` yields `NEITHER`, which
authorises nothing. The design converts the interpreter's known weakness into a blocked path
rather than a wrong action.

---

## 24. Status

Design complete; nothing implemented. No ConversationEngine change, no migration, no runtime
change, no authority moved, no Wild. L1/L2/L3 FROZEN · L4 ACTIVE · OUTBOUND OFF ·
Wild clean count **0/3**.

Recommended next: **L4.7C.1-RECONCILER-PRIMITIVES (phase C1)** — claim model, information
states, extended reconciliation record and an append-only log, running in shadow and changing
nothing.
