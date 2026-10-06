PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W5-GATE3-G3-7F-CLARIFICATION-LIFECYCLE-ARCHITECTURAL-REVIEW

## 0. EXECUTIVE CONCLUSION

A safe bounded architecture exists. I select **Option B — a dedicated clarification-attempt
entity** with a database-enforced `UNIQUE (thread_id, cycle_key, family)` reservation. It
requires one additive migration and one new table, and it is the only option of the three that
can make the required invariant enforceable at the database boundary.

Three facts found during this review decided it, and none of them were visible from the
closeouts.

**There is no usable cycle identity in production today.** `current_cycle_start_message_db_id`
is `NULL` for **all 33** live thread states, and so is `current_cycle_started_at`. The column
has exactly one writer — `_execute_cycle_reset` — which fires only when `cycle_reset_pending`
is true, and that flag is set by an operator action in the CRM (`set_lead_estado()`). It is
false on all 33 threads. `_reconciler_cycle_id()` therefore returns `None` for every live
thread, which also makes the pending-correction `CYCLE_ENDED` check vacuous today.

This is not fatal — it is clarifying. Because cycles advance **only** on an explicit,
operator-initiated reset, "one attempt per canonical cycle" is a well-defined and conservative
rule that satisfies §3's "may change only when a new canonical cycle is explicitly and validly
established". The architecture needs one small thing the current code lacks: a *non-null,
deterministic* rendering of that identity, because a `NULL` cannot participate in a uniqueness
constraint.

**The attempt fact is written in four places across three competing authorities.**
`state.vehicle_fallback_flow_sent` / `location_fallback_flow_sent` are assigned `True` at
`conversation_engine.py:6250`, `:6290`, `:6363` and `:6412`. The first two are the helpers the
owner-resolution milestone fixed to set the flag after a confirmed dispatch. **The second two,
inside `_check_fallback_flow_triggers`, still set the flag before the `_send_flow_button` call**
— so a blocked or failed Flow there still consumes the thread's one attempt. My own
owner-resolution closeout claimed "the flag now waits for a real `wa_message_id`"; that claim
covered half the write sites. This is `CL-04` below.

That is the architectural case in one sentence: **an invariant about a fact written from four
places in three authorities cannot be enforced in application code.** It must move to the
database boundary, where a uniqueness constraint enforces it regardless of which caller tries.

**The project already knows the ledger approach is raceable.** `WhatsAppOutboundDedup`'s own
docstring says the rolling dedup SELECT "allow[s] concurrent sends to race on the application
side before the Meta API call." That rules out Option A on the repository's own stated terms,
not on my judgement. There is also an existing atomic-upsert precedent to follow —
`quote_followup.py` uses `ON CONFLICT (thread_id) DO UPDATE`.

**Author disclosure.** I wrote `66ec6c8` and its closeout. Two of this review's findings
(`CL-04`, and the unreachable router it inherits) are defects in my own work, and `CL-04` is a
claim in my closeout that was not true. I record them as findings rather than restate the
closeout.

---

## 1. VERIFIED BASELINE

| Item | Observed | Expected | Match |
|---|---|---|---|
| Branch | `fix/m21.1.1-primary-flow-regression` | same | yes |
| HEAD | `66ec6c88991a7dfd781615978fde16b8e4e677b0` | same | yes |
| HEAD~1 (roadmap) | `4b3c22de4484323a57ac405887de36b310b83819` | same | yes |
| HEAD~2 (parent) | `b5a600e40e3bbee204835d4041a78f53b2fac2ee` | same | yes |
| Remote | `161aa8d6573c8fb316922c51d9af3cf4616d0cc2` | same | yes |
| Ahead / behind | 25 / 0 | 25 / 0 | yes |
| Tracked worktree | clean | clean | yes |
| Runtime `GIT_SHA` | `840bce0` | `840bce0` | yes |
| `OUTBOUND_ENABLED` | `false` | false | yes |
| Correction flags | both unset | unset/false | yes |
| Deployed Alembic | `20260918_hybrid_traces` | migration not deployed | yes |
| Deployed `pending_correction` | absent | absent | yes |
| Gate 3 commit deployed | none | none | yes |
| Container ids / restarts | `2c06976a4034` / `43879bd4be63` / `7afa22b8a0a0`, 0/0/0 | — | — |
| Business data | 47/34/1/35/734/2 | — | — |

---

## 2. RECONSTRUCTED CURRENT ARCHITECTURE

### 2.1 Three clarification authorities

| # | Authority | Symbol | Decides by | Reachable with flags off |
|---|---|---|---|---|
| 1 | priority ladder | `_routing_gate` Priorities 2, 4 (Location), 5 (Vehicle) | substring lists `_PRICE_INTENT_WORDS`, `_VEHICLE_CONCERN_WORDS`, `_FAQ_OR_SOFT_CLOSE_PHRASES` | **yes** |
| 2 | two-step fallback | `_check_fallback_flow_triggers` | same substring predicates | **yes** |
| 3 | typed router | `_route_clarification` → `_dispatch_clarification_route` | reconciled canonical evidence | **no** — only call site is inside `if (self._provisional_correction_on() and self._correction_grounding_on() …)` |

Control flow, `conversation_engine.py`:

```
if state.last_stage in (STAGE_QUALIFYING, None) and not state.needs_human:
    _gate_result, _skip_fallback = self._routing_gate(ctx, state, pre_detected_vehicle,
                                                      ai_input_messages)      # :7305
    if _gate_result is not None: return _gate_result
    if not _skip_fallback:
        _fb_result = self._check_fallback_flow_triggers(...)
```

### 2.2 One boolean, two meanings, four writers

```
:6250  state.vehicle_fallback_flow_sent  = True   # after dispatch  (fixed in 66ec6c8)
:6290  state.location_fallback_flow_sent = True   # after dispatch  (fixed in 66ec6c8)
:6363  state.vehicle_fallback_flow_sent  = True   # BEFORE dispatch (CL-04)
:6412  state.location_fallback_flow_sent = True   # BEFORE dispatch (CL-04)
```

Clearers: both completion handlers (unconditionally, before any resolution check),
`_generic_human_handoff` (as a "proposal" withdrawal), and `_execute_cycle_reset`.
Readers: the ladder, the two-step fallback, and the typed router's `*_flow_attempted`.

### 2.3 Cycle identity

```
writer   : _execute_cycle_reset only, from burst_start_message.id   (:12240)
trigger  : if state.cycle_reset_pending:  ... (:3494) — operator-set via set_lead_estado()
live data: 33/33 states have current_cycle_start_message_db_id NULL
           33/33 states have current_cycle_started_at NULL
           0/33  have cycle_reset_pending true
accessor : _reconciler_cycle_id -> str(db_id) or str(timestamp) or None
```

### 2.4 Atomicity

| Table | Uniqueness |
|---|---|
| `whatsapp_messages` | PK(id), **UNIQUE(wa_message_id)** |
| `whatsapp_thread_states` | PK(id), **UNIQUE(thread_id)** |
| `thread_revisions` | PK(id), UNIQUE(appointment_approval_token) |
| `whatsapp_outbound_dedup` | PK(id) only — **no uniqueness**, by design |
| `hybrid_decision_traces` | PK(id) only |

No `with_for_update` anywhere. One `ON CONFLICT … DO UPDATE` (`quote_followup.py`).

---

## 3. FAILURE-STATE SEQUENCE

The live defect, in the smallest terms that reproduce it:

```
1. vehicle unresolved, flag False            -> ladder/router authorizes the Vehicle Flow
2. Flow dispatched, flag True                -> outstanding proposal recorded
3. customer submits {tipo:"AUTO", marca:"", modelo:""}
4. _process_vehicle_fallback_response clears the flag FIRST (offset 1208, before the
   first needs_human guard at 1479)
5. tipo is in _FALLBACK_VEHICLE_TYPES        -> no handoff
6. _apply_vehicle_identity guards writes on truthiness (`if marca:`) -> identity still ('','')
7. flag False, identity unresolved, needs_human False
8. next inbound turn -> step 1
```

Reproduced in the preceding audit: four Vehicle Flows in one cycle, unbounded. The Location
family escapes only because `canonical_group` is non-`None` for any mapped `zona_general`, so
`home_zone_group` is always written — safety by accident, not by design.

A second sequence, from `CL-04`: a Vehicle Flow dispatched through
`_check_fallback_flow_triggers` that the kill switch blocks sets the flag anyway, so the
customer is never asked and the attempt is nonetheless spent — the mirror image of the loop.

---

## 4. COMPARISON OF ARCHITECTURAL OPTIONS

### Option A — immutable outbound-ledger authority — REJECTED

Two independent disqualifications, both evidential rather than aesthetic.

1. **The ledger cannot express the key.** `whatsapp_messages` has no cycle column and no
   clarification-family column. "Was a Vehicle Flow dispatched in cycle *k*?" is only
   answerable by joining `direction='out' AND message_type='flow'` to a cycle watermark and
   then *inferring* the family from `initial_screen` — which is not stored — or from the body
   text, which is prose matching and prohibited by §16.
2. **No atomic reservation is possible.** A uniqueness constraint over
   `(thread_id, cycle, family)` cannot be declared on a table that holds none of those three
   as columns, so the pattern degenerates to read-then-send. The repository already documents
   that this races: `WhatsAppOutboundDedup`'s docstring states the rolling check "allow[s]
   concurrent sends to race on the application side before the Meta API call."

§4 warned against recommending A merely because historical rows exist. They do exist, and they
remain valuable for forensics — but they cannot carry the invariant.

### Option C — explicit cycle-scoped thread-state fields — REJECTED

`whatsapp_thread_states` has `UNIQUE(thread_id)`, one row per thread, so a compare-and-swap
via `UPDATE … WHERE … RETURNING` is genuinely available and would be atomic. C is therefore
*safe* in the narrow concurrency sense and needs only additive columns.

It is rejected for three reasons:

1. **It cannot represent per-family attempt history without re-creating the ambiguity.** Two
   families × (outstanding, attempted) is four facts; expressing them as four more columns on
   a wide mutable row is exactly the shape §3 forbids ("another mutable boolean with the same
   lifecycle ambiguity"), and the row already carries 37 columns and four existing writers of
   the two flags.
2. **Attempt history must be immutable, and this row is the most-written row in the system.**
   Every turn updates `last_processed_inbound_wa_message_id` and stage fields. Placing an
   immutable fact in the most mutable row invites exactly the accidental clearing that
   produced the defect — `_generic_human_handoff` erased the flags because they were reachable
   from a generic loop over thread-state fields.
3. **It records no dispatch identity.** §13 requires a durable link from reservation →
   dispatch → completion → outcome; a column cannot hold the relation to the outbound row.

### Option B — dedicated clarification-attempt entity — SELECTED

One additive table whose primary key *is* the invariant:

```
UNIQUE (thread_id, cycle_key, family)
```

The reservation is an `INSERT`. A conflict is not an error to handle but the answer to the
question: *this family already had its attempt in this cycle.* The constraint holds against
every caller — the ladder, the two-step fallback, the typed router, a future one — which is
precisely what four write sites in three authorities makes impossible in application code.

It carries the dispatch relation and the completion outcome, so §13's chain is a row rather
than an inference. It costs one migration and one model.

---

## 5. SELECTED ARCHITECTURE

### 5.1 The entity

```
whatsapp_clarification_attempts
  id                      integer  PK
  thread_id               integer  NOT NULL  FK -> whatsapp_threads.id
  cycle_key               integer  NOT NULL        -- see 5.2
  family                  varchar  NOT NULL        -- 'VEHICLE' | 'LOCATION'  (CHECK)
  idempotency_key         varchar  NOT NULL  UNIQUE -- see 5.4
  status                  varchar  NOT NULL        -- see 6  (CHECK)
  reserved_at             timestamptz NOT NULL
  outbound_message_id     integer  NULL  FK -> whatsapp_messages.id
  wa_message_id           varchar  NULL            -- the Flow's WAMID
  dispatch_result         varchar  NULL
  completion_result       varchar  NULL
  resolved_family         boolean  NULL
  terminal_reason         varchar  NULL
  updated_at              timestamptz NOT NULL

  UNIQUE (thread_id, cycle_key, family)            -- THE invariant
  INDEX  (thread_id, cycle_key)
```

`status` is mutable; the row's *existence* is the immutable attempt history. Nothing deletes
rows; a new cycle produces a new `cycle_key` and therefore a new row.

### 5.2 Cycle identity — exact definition

```
cycle_key := COALESCE(state.current_cycle_start_message_db_id, 0)
```

Justified by executable evidence, not by the column's name:

| Property | Evidence |
|---|---|
| single writer | `_execute_cycle_reset` only (`:12240`); grep over `backend/app/` confirms no other assignment |
| advances only on an explicit, valid event | written only under `if state.cycle_reset_pending:` (`:3494`), and `cycle_reset_pending` is set by the CRM's `set_lead_estado()` — an operator action |
| durable | persisted integer column, added by `20260824_wild04r_cycle_boundary` |
| stable across a burst, duplicate delivery, restart | nothing else writes it; it is a DB message primary key, not a clock |
| non-null and deterministic | `COALESCE(…, 0)`; `0` denotes the thread's initial, never-reset cycle |

The `COALESCE` is load-bearing, not cosmetic: **all 33 live threads are NULL today**, so without
it every reservation would collide on NULL semantics or be unusable in a unique index. `0` is a
real cycle — the first one — and when an operator later resets, the key becomes the burst's
message id and one fresh attempt per family becomes available, which is the authorized
behaviour in §3 and owner policy 2.5.

`current_cycle_started_at` is **not** part of the key. §6 forbids timestamps as the cycle
identity, and the existing `_reconciler_cycle_id()` fallback to that timestamp is itself a
defect (`CL-03`): it mixes two domains into one string, so the same cycle can be keyed two
ways. The implementation must read the integer column directly and must not reuse that
accessor.

### 5.3 The other fact keeps its one meaning

`vehicle_fallback_flow_sent` / `location_fallback_flow_sent` retain exactly one meaning —
**a Flow is currently outstanding** — and may be cleared freely by completion, withdrawal,
handoff, expiry or reset. They become a convenience, not an authority. Attempt history lives
only in the attempt row. This is the separation §3 requires, and it is what makes the existing
clearers correct instead of dangerous.

### 5.4 Idempotency key

```
idempotency_key := sha256(f"clarification:{thread_id}:{cycle_key}:{family}")[:48]
```

Deterministic from the three identity components and nothing else — no timestamp, no counter,
no prose. It is redundant with the composite UNIQUE by construction, which is deliberate: it
gives the outbound gate a stable `correlation_id` to carry, so the ledger row and the attempt
row are joinable without the ledger needing new columns.

---

## 6. TYPED STATE MACHINE

```
                          (no row)
                              │  reserve: INSERT … ON CONFLICT DO NOTHING
                              ▼
                          RESERVED ──────────────────────────────┐
                              │                                  │
            dispatch accepted │          gate refused            │ Meta/transport error
                              ▼                 ▼                ▼
                         DISPATCHED      DISPATCH_BLOCKED   DISPATCH_FAILED
                              │                 │                │
        ┌─────────────┬───────┴───────┬─────────┘                │
        ▼             ▼               ▼                          ▼
COMPLETED_RESOLVED  COMPLETED_   INVALID_COMPLETION          HANDED_OFF
        │           UNRESOLVED          │                         ▲
        │               │               │                         │
        │               └───────────────┴─────────────────────────┘
        ▼
  normal resumption
```

Terminal for automated clarification: `COMPLETED_UNRESOLVED`, `INVALID_COMPLETION`,
`DISPATCH_BLOCKED`, `DISPATCH_FAILED`, `HANDED_OFF`, and `RESERVED` (see Q3).
Non-terminal: `DISPATCHED`. Resolving: `COMPLETED_RESOLVED`.

**The twelve required answers.**

1. **When is the attempt consumed?** At `RESERVED` — the committed `INSERT`, *before* the Meta
   call. Not at dispatch, not at completion. This mirrors the outbound gate's existing
   forensic invariant, which CLAUDE.md states as policy: the `pending` row is written to
   PostgreSQL **before** the Meta API call so that what was attempted is knowable without
   container logs.
2. **What prevents two simultaneous turns reserving the same family?** The composite UNIQUE.
   The loser's `INSERT` affects zero rows; it reads the winner's row and takes the
   already-attempted branch. No lock, no retry, no application-side window.
3. **Dispatch fails after reservation?** `RESERVED` → `DISPATCH_FAILED` → generic human
   handoff. No automated retry: owner policy 2.5 makes a failed attempt a handoff, and the row
   already exists so a second reservation is impossible. A crash leaving the row at `RESERVED`
   is treated identically — `RESERVED` is terminal-for-automation, which deliberately trades a
   rare lost clarification for the guarantee that a customer is never asked twice.
4. **Kill switch blocks transport?** `DISPATCH_BLOCKED`, generic handoff, no retry. Note this
   is a behavioural change from today and the safer one: the present code either burns the
   attempt silently (`CL-04`) or leaves it available forever.
5. **Partial completion?** `COMPLETED_UNRESOLVED` — terminal. The outstanding flag clears; the
   row remains. This is the exact defect being closed.
6. **Duplicate completion delivery?** Idempotent: the handler loads the attempt row by
   `(thread_id, cycle_key, family)`; a row already in a terminal state is a no-op.
7. **Customer messages before completion?** `DISPATCHED` is non-terminal and the row exists, so
   no new reservation is possible. The turn proceeds without clarification.
8. **What creates a new cycle?** `_execute_cycle_reset` under `cycle_reset_pending`, set by the
   operator. Nothing else.
9. **What proves the boundary is stable?** One writer, operator-gated, a DB-assigned message id,
   persisted — all four verified above by grep and by live data.
10. **Terminal states?** Listed above.
11. **Source of truth for Inspector measurement?** The attempt row. Trace rows describe turns;
    the attempt row describes the attempt across turns, which is what makes completion rate
    measurable at all (§13 / `CL-05`).
12. **Rollback and truthfulness?** The reservation commits in its own transaction before the
    Meta call, so a rolled-back business transaction never un-reserves an attempt whose Flow may
    already have reached the customer. Status transitions after dispatch are separate
    single-statement commits. The row is never deleted.

---

## 7. TRANSACTION AND CONCURRENCY DESIGN

```
# T1 — reservation, own transaction, committed before any Meta call
INSERT INTO whatsapp_clarification_attempts
       (thread_id, cycle_key, family, idempotency_key, status, reserved_at, updated_at)
VALUES (:thread_id, :cycle_key, :family, :idem, 'RESERVED', now(), now())
ON CONFLICT (thread_id, cycle_key, family) DO NOTHING
RETURNING id;
COMMIT;

rowcount == 1  -> this turn owns the single attempt; proceed to dispatch
rowcount == 0  -> already attempted this cycle; read the row and route to generic handoff
```

| Requirement | Mechanism |
|---|---|
| transaction boundary | T1 alone; committed before the Meta call; business transaction separate |
| atomic guard | `UNIQUE (thread_id, cycle_key, family)` + `ON CONFLICT DO NOTHING` |
| deterministic idempotency key | §5.4; also the gate's `correlation_id` |
| uniqueness conflict | not an error — the already-attempted branch |
| process crash after T1 | row is `RESERVED`, terminal-for-automation; a recovery sweep may move stale `RESERVED` rows to `DISPATCH_FAILED` for reporting only |
| Meta accepts, local tx fails | T1 already committed, so no second attempt; the ledger holds the WAMID and the sweep reconciles `wa_message_id` by `correlation_id` |
| local commits, Meta fails | `DISPATCH_FAILED`, generic handoff |
| reconciliation / recovery | `correlation_id == idempotency_key` joins attempt ↔ ledger; no new ledger columns |
| relation to the outbound ledger | the ledger stays the forensic record of what was sent; the attempt row is the authority on whether an attempt was owed and spent. Neither derives the other |

Pattern precedent in-repo: `quote_followup.py` already uses `ON CONFLICT (thread_id) DO UPDATE`.

---

## 8. ROUTER PLACEMENT

**Exact location.** `backend/app/services/conversation_engine.py`, immediately before the
`_routing_gate` call at `:7305`, inside the guard that already exists there:

```python
if state.last_stage in (STAGE_QUALIFYING, None) and not state.needs_human:
    # NEW: the single authoritative typed clarification decision
    _clar = self._clarification_decision(ctx, state, evidence)      # pure, no mutation
    if _clar.route is not NO_CLARIFICATION_REQUIRED:
        return self._execute_clarification(ctx, state, _clar)       # reserve → dispatch
    _gate_result, _skip_fallback = self._routing_gate(...)          # unchanged, subordinate
```

This position satisfies all twelve §8 conditions: it is on the ordinary path; it is independent
of both correction flags; it runs after semantic interpretation and reconciliation have written
canonical state; it precedes both substring authorities; the enclosing guard already respects
human ownership and stage; grounded correction confirmation is reached earlier in the handler
and returns before this point; MOTO safety and the category floor also precede it; it returns
at most one route; and `_clarification_decision` is pure — only `_execute_clarification` writes,
and it writes the attempt row and the outstanding flag, never a candidate.

**Legacy paths displaced at cutover.** There must be one authority, so these become
unreachable for clarification and should be removed in the implementation milestone:

| Path | Disposition |
|---|---|
| `_routing_gate` Priority 2 (concern-aware Location Flow) | remove the Flow dispatch; keep any non-clarification effect |
| `_routing_gate` Priority 4 (standard Location Flow) | remove |
| `_routing_gate` Priority 5 (Vehicle Flow) | remove |
| `_check_fallback_flow_triggers` vehicle branch (`:6363`) | remove — also the `CL-04` site |
| `_check_fallback_flow_triggers` location branch (`:6412`) | remove — also the `CL-04` site |
| `_routing_gate` Priorities 1, 3 (out-of-coverage, FAQ/soft-close → AI) | **retain** — not clarification |
| `_dispatch_vehicle_flow_direct` / `_dispatch_location_flow_direct` | retain as the single dispatch mechanism, called only by `_execute_clarification` |

After cutover `_PRICE_INTENT_WORDS`, `_VEHICLE_CONCERN_WORDS` and `_FAQ_OR_SOFT_CLOSE_PHRASES`
no longer participate in any clarification decision. They remain for the retained FAQ and
coverage branches; the implementation must not extend them.

---

## 9. DOMAIN-RELEVANCE DESIGN

`focused candidate exists → vehicle domain proven` is replaced. The decision takes ten typed
inputs, each from a named existing authority:

| Input | Source | Not |
|---|---|---|
| vehicle-domain relevance *this turn* | admitted `TurnEvidence.vehicle_mentions` via `_admitted_vehicle_evidence`, or `service_intents` requiring a vehicle | a phrase list |
| location-domain relevance *this turn* | admitted `TurnEvidence.location_mentions` via `_admitted_location_evidence`, or a scheduling/pricing action requiring a location | a phrase list |
| vehicle identity resolved | `FieldEvidenceSnapshot.vehicle` / `vehicle_category` confirmed | the candidate being non-null |
| location resolved | `FieldEvidenceSnapshot.location_known()` | `home_zone_*` being non-blank |
| required-next-information authorized | the existing `_register_clarification_proposition` gate conditions, lifted into a typed predicate | prose |
| grounded correction pending | `pending_correction.load(state)` | — |
| human ownership | `state.needs_human` | — |
| attempt already spent | the attempt row for `(thread_id, cycle_key, family)` | a boolean flag |
| Flow currently outstanding | `*_fallback_flow_sent` | — |
| category resolvable | `_catalog_tipo_for(marca, modelo)` | defaulting to AUTO |

**Domain relevance requires typed current-turn evidence OR an authorized
required-next-information proposition.** Stored incomplete state alone never proves it — that
is owner policy 2.8, and it is what the current coarse rule violates.

Worked cases:

| Situation | Result |
|---|---|
| `qquieor reviisar un autoo 208 del 2022 en ppalermo`, semantic resolves 208 / 2022 / Palermo | reconciliation admits the typed propositions; the snapshot reports both resolved; **no Flow** |
| vehicle enquiry, make/model badly misspelled and unresolvable | vehicle-domain relevance proven by `vehicle_mentions`; identity unresolved → Vehicle Flow |
| locality mentioned fuzzily and unresolvable | `location_mentions` proven; zone unresolved → Location Flow |
| payment FAQ while an old candidate is incomplete | no vehicle/location evidence this turn, no authorized required-next-information → **no Flow** (fixes OR-04) |
| business-hours question | same — no Flow |
| correction to a known vehicle | grounded correction pending with known values → typed confirmation, not a Flow |
| catalogue miss on a known identity | category unresolvable → generic human handoff, never the Vehicle Flow, never MOTO |
| no relevant evidence | `NO_CLARIFICATION_REQUIRED`, reason `DOMAIN_NOT_PROVEN` |

### Semantic freedom (§10)

The snapshot consumes *resolution*, not provenance: `FieldEvidenceSnapshot` reads
`ctx.candidates` and canonical state, which is where reconciliation writes admitted semantic
evidence. CE is never required to re-derive the interpretation, and its silence cannot veto it.
No spelling variant, alias or sentence exception is introduced anywhere in this design.

---

## 10. COMPLETION AND HANDOFF DESIGN

### Completion

Both handlers gain the same five-step shape, in this order:

1. **Identify the attempt** — load by `(thread_id, cycle_key, family)`. No row ⇒ an unsolicited
   submission; handle defensively and do not create one.
2. **Idempotency** — a terminal status is a no-op.
3. **Validate** submitted fields.
4. **Reconcile** through canonical authority (`_apply_vehicle_identity`, the canonical zone
   path) — never a direct write around the reconciler, never an invented value.
5. **Classify and record**:
   - family resolved → `COMPLETED_RESOLVED`, clear the outstanding flag, resume normally;
   - family still unresolved, or invalid → `COMPLETED_UNRESOLVED` / `INVALID_COMPLETION`,
     clear the outstanding flag, **generic human handoff**.

The outstanding flag may be cleared at step 5 exactly as it is today. What changes is that
clearing it no longer makes an attempt available, because availability is the attempt row.

**Both-unresolved progression.** Vehicle completion writes a `VEHICLE`-family terminal status.
Re-reconciliation then runs; if location is still unresolved, the `LOCATION` family has **no
row for this cycle**, so its reservation succeeds and the Location Flow is dispatched. A second
Vehicle attempt is impossible because its row exists. The ordering requirement and the
one-attempt requirement are satisfied by the same mechanism, with no sequencing flag.

### Human handoff

`_generic_human_handoff` keeps every effect verified in the preceding audit — ownership on both
conversation and lead state, one truthful enumerated alert, autonomous progression stopped, no
candidate, no MOTO candidate, neutral wording, grounded facts preserved, no second message
after a degraded response.

One change: its withdrawal loop must stop iterating `pc.LEGACY_PROPOSALS` blindly. The
architectural distinction §12 asks for is now representable:

- **withdrawing something still offered** → clear `*_fallback_flow_sent`; legitimate;
- **forgetting the attempt occurred** → would require deleting the attempt row; the
  implementation must never do so, and no row is ever deleted.

That closes OR-03 structurally rather than by masking it with `needs_human`.

---

## 11. TRACE AND INSPECTOR DESIGN

Contract `hybrid-decision-trace/1.9`, additively extending 1.8's `clarification` object with
the attempt identity so a turn's trace joins the attempt's life:

```
clarification.attempt_id          int    the row
clarification.cycle_key           int    which cycle this belonged to
clarification.family              str    VEHICLE | LOCATION
clarification.attempt_status       str    the state-machine value at turn end
clarification.attempt_reserved    bool   did THIS turn win the reservation
clarification.already_attempted   bool   was it spent before this turn
clarification.outbound_message_id int    the Flow's ledger row
clarification.completion_result   str    on a completion turn
clarification.resolved_family     bool   on a completion turn
clarification.prevented_second    bool   a reservation conflict was observed
```

Retained from 1.8 unchanged: `route`, `reason`, `resolved_without_flow`, `flow_dispatched`,
`explanatory_planned`, `generic_handoff`, `handoff_reason`, `confirmation_bound`,
`corrected_category`, `moto_proven`, `g3_8_enforced=False`.

This answers all eleven §13 questions. Critically it closes `CL-05`: completion rate becomes
measurable because the completion turn's trace and the dispatch turn's trace carry the same
`attempt_id`, and the attempt row itself is independently countable by status. The Inspector
gains an attempt-lifecycle row; the existing "a Flow is the fallback, not the target" note is
retained.

Privacy unchanged: ids, enums and booleans only — no raw customer text, no phone number, no
full WAMID in the trace payload (the WAMID stays in the ledger, where it already lives).

---

## 12. MIGRATION, ROLLBACK, FILE SET, TESTING, DEPLOYMENT

### Migration — required

One additive migration creating `whatsapp_clarification_attempts`, `down_revision =
"20261005_pending_correction"`. No column is altered and no existing row is touched, so
`downgrade()` is a single `drop_table`. Rolling the image back after applying it is safe
because the older ORM does not know the table.

Avoiding a migration was considered and rejected: Option C avoids one but cannot hold immutable
per-family history or the dispatch relation, and Option A cannot express the key at all. §7
permits the migration when avoiding it makes concurrency or recovery unsafe, which is the case.

**Deployment order is unchanged and still mandatory**: backup → apply migration (this one *and*
the pending-correction migration, still undeployed) → verify head and columns → deploy the
image → outbound stays OFF → verify runtime SHA, restarts, trace contract and Inspector →
offline certification → owner checkpoint. Deploying the image first breaks ordinary thread-state
reads, as the G3-7F review proved.

### Implementation file set

```
backend/migrations/versions/2026xxxx_clarification_attempts.py   new
backend/app/models.py                                           + WhatsAppClarificationAttempt
backend/app/services/clarification_attempt.py                   new — reservation, transitions, cycle_key
backend/app/services/clarification_router.py                    new — pure typed decision (moved out of pending_correction.py)
backend/app/services/conversation_engine.py                     router call site; _execute_clarification;
                                                                remove 5 legacy clarification branches;
                                                                completion handlers; handoff withdrawal scope
backend/app/schemas/hybrid_trace.py                             contract 1.9 fields
backend/app/services/hybrid_trace.py                            build_trace parameter
backend/app/ui/hybrid_trace_view.py                             attempt-lifecycle rendering
tests/…                                                          new suite + updates to displaced-path tests
```

`pending_correction.py` loses the router (it is not about pending corrections) and keeps the
correction tiers, patch and admission logic.

### Testing strategy

Beyond the §18 contract: at least one test driving the real `handle()` through a clarification
turn; one against a real PostgreSQL instance asserting the unique constraint rejects a second
reservation; one concurrency test issuing two reservations from two sessions and asserting
exactly one wins; and one crash/recovery test committing T1 then abandoning the process and
asserting no second attempt is offered. Sealed containers and a disposable database throughout.

### Risk and scope

Risk is **moderate**, concentrated in removing five legacy clarification branches — that is
where live behaviour changes and where regression identities could move. The new table and
reservation are low risk because they are additive and constraint-enforced. Scope: one
migration, two new modules, six edited files, roughly 600–900 production lines and a test suite
comparable to the owner-resolution milestone's. It is one bounded commit plus the migration.

---

## 13. IMPLEMENTATION ACCEPTANCE CONTRACT

Executable proof required for all twenty:

1. one Vehicle Flow maximum per cycle; 2. one Location Flow maximum per cycle; 3. partial
completion cannot reopen it; 4. invalid completion cannot reopen it; 5. duplicate completion
cannot reopen it; 6. dispatch failure → human ownership, no retry; 7. two concurrent turns
cannot dispatch duplicates; 8. Vehicle-then-Location ordering works; 9. semantic-resolved messy
language sends no Flow; 10. an unrelated FAQ sends no Flow despite incomplete stored data;
11. known correction values use confirmation, not a Flow; 12. an unknown car category never
becomes MOTO; 13. the router is reachable with both correction flags OFF; 14. no legacy
substring path competes for clarification authority; 15. completion and handoff cannot erase
attempt history; 16. the trace links reservation → dispatch → completion → outcome;
17. no second candidate writer; 18. no regression identity added; 19. the published Flow JSON
is unchanged (content hash `274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15`);
20. outbound remains OFF throughout certification.

Mandatory test kinds: one real-handler test, one persisted-PostgreSQL test, one concurrency
test, one crash/recovery test. Additionally, and because this review found it: a test asserting
**zero** remaining pre-dispatch writes of either `*_fallback_flow_sent`.

---

## 14. FINDING REGISTER

| ID | Severity | Finding | Introduced |
|---|---|---|---|
| CL-01 | HIGH | One boolean carries both "outstanding" and "attempted", written at four sites across three authorities; no application-level invariant is enforceable | inherited, relied upon by `66ec6c8` |
| CL-02 | HIGH | The typed router's only call site is behind both correction flags, so it cannot execute in the mandated posture | `66ec6c8` (confirmed in the prior audit as OR-02) |
| CL-03 | MEDIUM | No usable cycle identity: `current_cycle_start_message_db_id` is NULL on 33/33 live threads and `_reconciler_cycle_id()` falls back to a timestamp, then `None` — mixing domains and making the pending-correction cycle check vacuous | inherited |
| CL-04 | MEDIUM | `_check_fallback_flow_triggers` still sets both flags **before** dispatch (`:6363`, `:6412`), so a blocked or failed Flow burns the attempt. My owner-resolution closeout's Part E claim covered only `:6250`/`:6290` | inherited; the closeout's claim is mine and was wrong |
| CL-05 | LOW | Completion rate is not measurable: nothing links a completion turn's trace to the dispatch turn's | `66ec6c8` (prior audit OR-08) |
| CL-06 | LOW | `whatsapp_outbound_dedup` has no uniqueness constraint and its own docstring concedes the race — relevant only because it disqualifies Option A | inherited, by design |

---

## 15. SEPARATED VERDICTS

| # | Subject | Verdict | Reason |
|---|---|---|---|
| 1 | Current outstanding-Flow representation | **CONDITIONAL_PASS** | the flag is adequate for this one meaning once attempt history moves elsewhere |
| 2 | Current attempt-history representation | **FAIL** | CL-01 — destroyed by completion, handoff and reset; four writers |
| 3 | Current cycle identity | **FAIL** | CL-03 — NULL on 33/33 threads; timestamp fallback; unusable as a key |
| 4 | Current concurrency safety | **FAIL** | no atomic guard anywhere on the attempt fact |
| 5 | Current router reachability | **FAIL** | CL-02 — unreachable with the mandated flags off |
| 6 | Current router authority | **FAIL** | three competing authorities; the two reachable ones decide by substring |
| 7 | Current domain-relevance rule | **FAIL** | `focus is not None` proves nothing about the current turn (owner policy 2.8) |
| 8 | Semantic-freedom preservation | **PASS** | the snapshot consumes resolution, not provenance; CE silence is not a veto |
| 9 | Vehicle/Location ordering | **PASS** | correct in the router and in the legacy ladder; preserved by the selected design |
| 10 | Completion lifecycle | **FAIL** | clears the attempt before any resolution check; partial completion reopens the Flow |
| 11 | Human-handoff lifecycle | **CONDITIONAL_PASS** | all required effects correct; it erases attempt history (OR-03), fixed structurally by §10 |
| 12 | Trace/Inspector lifecycle truth | **CONDITIONAL_PASS** | nothing false is reported; completion is unmeasurable (CL-05) |
| 13 | Option A viability | **FAIL** | the ledger holds no cycle or family column and cannot express the unique key; the repo documents the race |
| 14 | Option B viability | **PASS** | the invariant becomes a database constraint; carries the dispatch and completion relation |
| 15 | Option C viability | **FAIL** | atomic but cannot hold immutable per-family history or the dispatch relation; recreates the ambiguity on the most-written row |
| 16 | Selected architecture | **Option B** | dedicated attempt entity, `UNIQUE (thread_id, cycle_key, family)`, INSERT-as-reservation |
| 17 | Migration necessity | **REQUIRED** | one additive table; avoiding it makes concurrency and recovery unsafe |
| 18 | Implementation boundedness | **PASS** | one migration, two new modules, six edited files, one commit; risk concentrated in removing five legacy branches |
| 19 | Compatibility with retained G3-7F work | **PASS** | `4b3c22d` and `66ec6c8` retained; degraded-response authority, flag-off parity, generic handoff, MOTO isolation, accounting repair, composition and contract 1.8 all stand; 1.9 is additive |
| 20 | Readiness for architectural implementation | **PASS** | architecture selected, invariants and transaction boundaries specified, acceptance contract defined |
| 21 | Readiness for G3-8 | **FAIL** | clarification lifecycle must be implemented and independently reviewed first; R2R-07 remains G3-8's |
| 22 | Gate 3 | **FAIL** | remains open |
| 23 | Deployment readiness | **FAIL** | CL-01 and CL-04 are live; two migrations now pend, and the migration-before-image order stands |
| 24 | Wild readiness | **FAIL** | a Wild would reproduce the unbounded loop against a real customer |

### R2R-07 compatibility (§15 of the prompt)

The selected design does not make R2R-07 harder. The attempt entity is orthogonal to
`ThreadRevision`: it records whether a clarification was owed and spent, never vehicle data,
and it adds no writer to revisions. G3-8's confirm-then-recompute sequence gains a clean place
to check "is a clarification attempt still outstanding for this thread and cycle?" before
allowing commercial progression — if anything, slightly easier.

---

## 16. SAFETY / NO-DELTA PROOF

| Item | Before | After |
|---|---|---|
| HEAD | `66ec6c8` | `66ec6c8` |
| Tracked worktree | clean | clean |
| Ahead / behind | 25 / 0, unpushed | 25 / 0, unpushed |
| Runtime `GIT_SHA` | `840bce0` | `840bce0` |
| Container ids | `2c06976a4034` / `43879bd4be63` / `7afa22b8a0a0` | unchanged |
| Restart counts | 0 / 0 / 0 | 0 / 0 / 0 |
| `OUTBOUND_ENABLED` | `false` | `false` |
| Correction flags | both unset | both unset |
| Deployed Alembic | `20260918_hybrid_traces` | unchanged |
| Business data | 47/34/1/35/734/2 | 47/34/1/35/734/2 |

No code, test, migration, documentation, configuration or data change. No commit, amend, push,
build, deployment, container restart, flag change, Meta call, model call, WhatsApp message,
tester reset or Wild. Investigation was read-only: `git` inspection, source reading, and
read-only `SELECT`s against the deployed database. This artifact is the only file created, and
it is not committed.

NEXT: L4.7W5-GATE3-G3-7F-CLARIFICATION-LIFECYCLE-ARCHITECTURAL-IMPLEMENTATION — OWNER AUTHORIZATION REQUIRED
STOP.
