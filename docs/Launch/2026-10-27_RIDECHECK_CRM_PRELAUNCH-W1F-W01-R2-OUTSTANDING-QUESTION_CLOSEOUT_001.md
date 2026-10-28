PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-W1F-W01-R2-R3-INCUMBENT-STATE-CONTRACT-CORRECTION

# W1F-W-01 R2 — the outstanding-question contract (incumbent state contract corrected)

**Date:** 2026-10-27, revised 2026-10-28 · **Starting HEAD:** `a6aabece08afbb8c508f5417e5bd3698b29d1cf3`
(`fix(prelaunch): W1F-W-01 R1 evidence admission`) · **Branch:**
`integration/prelaunch-booking-convergence` · **Remote:** `origin` is at the same commit,
verified before editing · **Flag:** `OUTSTANDING_QUESTION_ENABLED`, default **OFF** ·
**NOT committed. Not built, not deployed, no database migrated, no live configuration or
flag changed, no provider contacted.** This is an owner checkpoint, not a landing.

**Revision 4 — owner correction OC-R2-R3 (2026-10-28).** The delivery-order race, including
the PostgreSQL case the partial unique index cannot mask, was accepted — but the document
still carried **two incompatible contracts**, and the owner was right to refuse to pick
between them by prose. Case B in §4.1 said an undelivered re-ask retires nothing; §15 item 8,
§18 items 6–7 and the checkpoint summary said only the newest accepted ask may own the field,
so an undelivered re-ask leaves nothing active.

**Resolved by measurement, not by choosing.** All six of the owner's sequences were driven
through the real state-machine service against a real database (§4.2). **The implementation
does case B**, and it already implements the owner's stated rule exactly:

> Only the newest **eligible** row may newly **activate**. An existing `OUTSTANDING`
> incumbent remains active until a newer question **actually activates**. A newer terminal
> sibling prevents an older **inactive** row from activating later, but does not
> retroactively deactivate an incumbent that was already `OUTSTANDING`.

The three other passages had generalised sequence E — where no incumbent was ever activated —
into a universal rule. They are corrected, not annotated, and **no code changed**: the
correction is the contract statement, §4.2's measured table, `TestR2P_Incumbent` (8 tests),
and mutants **M38/M39**, which make acceptance and failure retire an incumbent and both die.
**M39 would have survived the previous revision** — no test asserted that a failed re-ask
leaves the incumbent alone, which is exactly the property the two contradictory passages
disagreed about. §18 item 7 now carries the corrected residual-risk analysis: the two risks
**do not always compound**.

**Revision 3 — owner correction OC-R2-R1 (2026-10-28).** The true-delivery activation, the
authenticated existing webhook integration, the eight-posture interlock, the PostgreSQL race
testing and the corrected rollback boundary were accepted in principle, with **one unsafe
delivery-ordering case** to correct, a terminology correction and a documentation sweep. All
three are made:

1. **Late delivery of an older question can no longer replace a newer one.** The owner's
   sequence — Q1 accepted, Q2 accepted for the same thread/cycle/kind, Q2 delivered, then
   Q1 delivered LATE — promoted Q1 and superseded Q2, because supersession asked *who is
   live?* and never *who is newer?*. There is now **one deterministic ordering, the row
   id**, enforced in one function; webhook arrival time orders nothing. §4.1 is the rule and
   its eight cases, `TestR2N_Ordering` (15 tests) and `test_k06a/b/c` (two PostgreSQL
   connections) are the proof, and mutants **M35–M37** attack it — M35 dies in the sealed
   harness **and** independently on PostgreSQL in the one case the unique index cannot see.
2. **`delivered` no longer means "the customer saw it" anywhere in this document or in the
   code.** `delivered` is delivery to the recipient's device; only `read` would support
   "seen"; the invariant is now *"a question becomes active because Meta confirmed delivered
   or read"*. The contract never needs the stronger fact.
3. **The documentation sweep the owner itemised** is done: §2's state vocabulary, §3.5's
   rollback sentence, §9.2's unreachable "R1 only" posture, §18's path count, and a sweep of
   the whole document for all six stale phrases. §9.3's timing table was **re-measured**
   rather than relabelled, because the interlock had made its comparison column describe
   code that no longer exists.

**Revision 2 — owner correction OC-R2 (2026-10-27).** The schema design, the
question/evidence state-machine direction, the cross-turn binding, the location-form
convergence, the mutation work, the PostgreSQL rehearsal, the assertion changes and the
proposed commit scope were accepted in principle, with **three corrections required**. All
three were made:

1. **The delivery contract was wrong and is corrected.** A WAMID from the Meta call proves
   ACCEPTANCE, not delivery. Acceptance now produces a non-active `PENDING_DELIVERY` row
   that owns nothing; only an observed `delivered` or `read` status activates it; `failed`
   ends it. §5 and §4 are rewritten, the state vocabulary went from four states to six, the
   migration and its indexes changed with it, and `TestR2M_Delivery` covers the lifecycle.
   **I measured the integration before designing it** and the statuses do arrive — §5.0.
2. **The flag matrix fails closed across all eight combinations.** The owner was right that
   guarding "R2 without R1" was not enough: this closeout itself measured R1 ON + R2 OFF
   writing `('CABA','La Boca')`. R1 and R2 behaviour is now behind ONE interlock requiring
   all three flags; R3 keeps its certified independence. §9.4 is the eight-row table.
3. **The rollback contract was self-contradictory and is corrected.** Flags OFF is a
   BEHAVIOURAL rollback, not an image rollback, and **direct rollback to the `a6aabece`
   image is unavailable once either new table holds a row.** §14 is the truth table and
   §14.1 records the compatibility rollback image as a blocking requirement of the next
   milestone.

Two of OC-R2's three corrections changed behaviour that earlier sections of this document
described, and every such claim has been rewritten rather than annotated. OC-R2-R1's
ordering correction changed one more — §4's supersession rule — and §4.1 is new.

---

## 0. What this milestone did, and what it did not do

**Did.** The system now knows what it asked **and whether Meta confirmed it delivered**. A
question Meta accepted becomes a non-active row; an observed `delivered` or `read` status
makes it active, and only the newest eligible row may newly activate — an incumbent the
customer already has keeps the field until a newer ask genuinely activates (§4.1, §4.2); an admitted claim for that question's own field, cycle and candidate closes it,
idempotently and by conditional update; the closure is a typed routing fact that suppresses
exactly one thing — the contradictory Layer F step 9 greeting on the turn that answered.
Admitted and refused evidence is persisted with provenance, so a locality given before the
vehicle existed binds to the candidate created afterwards through the certified rules. The
Location Flow's answer now takes the same route as a locality read from text (W1FRV-04). One
additive migration carries R1's and R2's schema together.

**The Wild's exact three-turn sequence now replays with one location request.** Measured end
to end, in both postures, by `TestR2B_Wild`.

**Did not.**
* **No image, no deployment, no migration against any live database.** The migration was
  rehearsed only on a disposable PostgreSQL 16.13 created for it and disposed by exact name.
* **No flag enabled.** All three are OFF, and **all eight combinations fail closed** except
  the fully integrated one (§9.4). This is stronger than the first revision, which guarded
  only one partial posture and left the one that admitted `la boca` open.
* **R1-PL-01 is addressed but is not closed as a general property.** §9 is precise about what
  was removed and what remains.
* **W1F-W-01 stays OPEN.** Committing R2 enables nothing; the gate closes at a runtime
  checkpoint on the integrated R1+R2+R3 candidate, which is the owner's to authorise.

---

## 1. The defect, and the one fact that was missing

> The bot asked where the vehicle was. The customer answered `en flores`. The interpreter
> read `INSPECTION_LOCATION = Flores, CONFIRMED`. The turn exited at Layer F step 9 and sent
> the generic qualification greeting — and the location was asked three times in one
> conversation.

R1 established that the words were never the problem: the locality survives now. What was
still missing is that **nothing in the system knew a question had been asked**, so the answer
arrived as an unprompted fragment and was treated as one. Two rules follow, and the whole
milestone is their enforcement:

```
    A question becomes ACTIVE because Meta confirmed DELIVERED or READ.
    A question closes because ADMITTED EVIDENCE answered it.
```

Neither may be weakened into the other. A question nobody received must not be able to
suppress a greeting; and **the model saying the customer answered closes nothing**, because
that is the model deciding a state transition, which is the authority the review refuses it.

**On the first rule's wording (OC-R2-R1).** `delivered` means Meta handed the message to the
recipient's device; it is **not** proof a human looked at it. Only `read` would support
"seen", and this contract never needs the stronger fact — it needs *the customer has the
question*, which is what `delivered` establishes. Nothing in this document, in
`outstanding_question.py` or in `models.py` says the customer *saw* a delivered-only
message.

---

## 2. Files changed

| file | status | what |
|---|---|---|
| `backend/app/models.py` | modified | the two tables of review §8.1/§8.2, the **six-state** delivery vocabulary and the three provenance vocabularies, the partial unique index, the row-id ordering rule in the class contract, `zone_pair()`, and the two additive candidate columns of §8.3 |
| `backend/app/services/outstanding_question.py` | **NEW** | the state machine: deterministic identity, acceptance-gated creation, delivery-gated activation, the row-id ordering (`newest_question_id`, `_terminalize_older`, `_refuse_stale_activation`), explicit supersession, pure matching, conditional closure, counted expiry |
| `backend/app/services/thread_evidence.py` | **NEW** | persistence of `ClaimEvidence` and the five binding conditions; writes no candidate field and sends nothing |
| `backend/app/services/conversation_engine.py` | modified | `_r2_on`, `_r2_cycle_key`, `_locality_possible_in_burst`, context-aware locality validation, `_persist_turn_evidence`, `_reconcile_outstanding_questions`, `_answered_question_kinds`, `_record_delivered_questions`, `_bind_evidence_to_candidate`, `_apply_form_location`, the Layer F step 9 precondition, and the binding at **both** candidate-creation sites |
| `backend/app/services/claim_projection.py` | modified | `locality_mention_possible` — the ONE shared predicate R1-PL-01 requires |
| `backend/app/services/schema_preflight.py` | modified | `EXPECTED_HEAD` → the new revision. This is what makes the migration deployment-ordered |
| `backend/app/services/tester_reset.py` | modified | both new tables deleted before `whatsapp_messages`, guarded by a table-presence check — the G37R-RV-03 lesson applied in advance |
| `backend/app/settings.py` | modified | `outstanding_question_enabled: bool = False` ← `OUTSTANDING_QUESTION_ENABLED`, with the interaction rule in the comment |
| `backend/migrations/versions/20261027_w1f_conversation_memory.py` | **NEW** | the one additive migration: both tables, both columns, four indexes, adopt-or-refuse upgrade, data-safe downgrade |
| `tests/test_w1f_w01_r2_outstanding_question.py` | **NEW** | the R2 suite, including the gated PostgreSQL migration rehearsal |
| `tests/test_w1f_w01_r1_evidence_admission.py` | modified | **one assertion updated**, in the direction R1's own closeout predicted — §12.1 |
| `tests/test_convergence_p4_r1.py` | modified | **one assertion updated**: the single-Alembic-head pin, in the file's own documented idiom — §12.2 |

`docs/operations/RECOVERY_MANIFEST.md` is **not** in that list and was not touched.

---

## 3. Schema and migration contract

### 3.1 `whatsapp_outstanding_questions` (review §8.1)

Three database-enforced invariants:

```
UNIQUE (identity)                                 one asked question -> one row
UNIQUE (thread_id, cycle_key, question_kind)      at most ONE live question may own a field
       WHERE status = 'OUTSTANDING'
CHECKs on status / question_kind / asked_by        the vocabulary cannot drift
```

`identity` is a deterministic UUIDv5 over `(namespace, thread, cycle, kind, the WAMID of OUR
outbound message)`. It carries no timestamp, no counter, no retry nonce, no customer text and
no phone number, so a retried record or a duplicate delivery callback recomputes the same
value and the second insert is refused **by a constraint rather than by a read another worker
can also pass**. Asking the same field twice in one cycle produces two different identities —
two real asks, the second superseding the first.

`cycle_key` is `COALESCE(state.current_cycle_start_message_db_id, 0)`, read from
`clarification_attempt.cycle_key` — **imported, not re-derived**, so the questions, the
evidence rows and the Flow attempts cannot end up keyed three ways (W1FRV-07).

`outbound_message_id` is a **HISTORICAL scalar and deliberately not a foreign key**: see §3.4.

### 3.2 `whatsapp_thread_evidence` (review §8.2)

The persistence of an existing in-memory type. Every column already exists on
`schemas/claims.ClaimEvidence`, including `cycle_id`, `supersedes` and the content-hash
`claim_id`. `UNIQUE (thread_id, claim_id)` makes a replayed turn idempotent at the database.
**Refusals are rows too**, carrying R1's `outcome` and `AdmissionReason` unchanged — finding
G-11, closed for the locality family.

### 3.3 Candidate provenance (review §8.3)

`zone_evidence_id` and `vehicle_evidence_id`, both nullable integers with
`ON DELETE SET NULL`. Measured on the rehearsal server: `integer`, `YES`, no default.

### 3.4 The reference design, and the conflict it resolves

The review's §8.1 lists `outbound_message_id`; a foreign key there would have been the
obvious reading. **It would have broken certified tooling, and the project has already
measured exactly that.** `services/tester_reset.py` deletes in this order:

```
ai_events → thread_revisions → booking_offers → clarification_attempts
          → whatsapp_messages → candidates → thread_states → … → threads → leads
```

G37R-RV-03 found a reset failing `23503` and deleting **nothing** because
`whatsapp_clarification_attempts` referenced both the thread and the outbound row, and
`20261022_operator_notifications` drew the conclusion in its own docstring: "HISTORICAL
scalar ids — never a foreign key, so a tester reset is never blocked."

So: the thread references are the only `NO ACTION` keys (the reset deletes threads last, and
R2 adds both tables to it before the messages); **every** reference to a candidate or an
evidence row is `SET NULL`; the gate's ledger row is a scalar. `test_k05` asserts this
against `pg_constraint` on a live server rather than against the code — "ask
`pg_constraint`, not the code" is the lesson that finding left.

### 3.5 Upgrade and downgrade

One additive migration, `20261027_w1f_conversation_memory`, parent
`20261022_operator_notifications`. Nothing existing is altered or dropped; there is no
`DELETE`, `UPDATE` or `TRUNCATE` of a business row anywhere in it.

**Upgrade**, per object, under a lock, inside Alembic's single transaction: absent →
`CREATED`; present and empty → `ADOPTED_EMPTY`; present with rows → `VERIFIED`. A column
that exists and is not the nullable integer this revision adds **refuses** with nothing
changed.

**Downgrade — data-safe, and it refuses rather than deletes.** With any row in either table
it raises and changes nothing: the rows are the record of what the customer was told and what
the system decided about what they said, and `superseded_by_id` means that history is not
reconstructible from canonical state. An empty pair is dropped; the two candidate columns are
dropped only when every value in them is NULL.

**What a rollback actually requires — the §14 truth, not the weaker claim this paragraph
used to make** (OC-R2-R1 struck "a code rollback needs no schema rollback at all"):

* **flags OFF is a BEHAVIOURAL rollback on the new image** — both tables are unread with the
  flags off, which is review §15's property, and it is the only rollback that needs nothing
  from the schema;
* **rolling back to the OLD image after the migration has run requires the downgrade**, and
  the downgrade only succeeds **while both tables are empty**;
* **once either table holds a row the downgrade refuses, so direct rollback to the existing
  `a6aabece` image is unavailable** — that image's `schema_preflight.EXPECTED_HEAD` is the
  parent revision and it will not start against a database at this one;
* therefore the **compatibility rollback image is BLOCKING** before any live migration
  (§14, §14.1).

---

## 4. The outstanding-question state machine

```
                 (no row at all)  ──  "we never even got it to Meta"
                        │
        Meta ACCEPTED the message and returned a WAMID
                        ▼
              PENDING_DELIVERY  ──────────────────────────────┐ Meta: failed
                 owns NOTHING                                 ▼
                        │                                  UNDELIVERED
        Meta: delivered │ or read          (terminal — a late `delivered`
        (sent is NOT    │                   cannot resurrect it)
         sufficient)    ▼
                   OUTSTANDING ───────────────────────────────┐
                    │     │                    │              │
   admitted evidence│     │ the same field     │ turns run out│ handoff /
   for THIS field,  │     │ is re-asked AND    │ (counted,    │ cycle reset
   cycle, candidate │     │ that ask is        │  not timed)  │
                    │     │ delivered          │              │
                    ▼     ▼                    ▼              ▼
                ANSWERED  SUPERSEDED        ABANDONED      ABANDONED
```

**Six states, every one of them WRITTEN**, and the absence of a row is a seventh fact.
Nothing is inferred from absence — that is how `_apply_candidate`'s RISK-03 problem was
created and R2 does not repeat it.

**`OQ_ACTIVE_STATUSES = (OUTSTANDING,)` is the one definition of "active".** Only a row in
that set may own the per-field constraint, match evidence, close, suppress a greeting or
influence routing. `PENDING_DELIVERY` owns none of those things, which is the entire content
of OC-R2 CORRECTION 1.

| rule | where | proved by |
|---|---|---|
| a row exists only if Meta ACCEPTED the message | `_record_delivered_questions`, after `gate.mark_sent` | `j01` (source order), `c01` blocked gate, `c02` Meta raised, `c03` nothing asked |
| a row is ACTIVE only on `delivered`/`read` | `activate_on_status`, from the signed webhook | `m01`–`m04`, `m11` |
| `sent` is not sufficient | `OQ_DELIVERING_STATUSES` | `m02`, `m13`, mutant **M22** |
| `failed` is terminal | `activate_on_status` | `m05`, `m07`, mutant **M26** |
| duplicate / out-of-order callbacks are idempotent | conditional UPDATE off `PENDING_DELIVERY` | `m06`, `m07`, mutant **M25** |
| an early callback is not lost | `reconcile_delivery` over the outbound ledger | `m08`, `m08b`, `m08c`, mutant **M27** |
| a pending question suppresses nothing | `live()` filters on OUTSTANDING | `m10`, mutant **M24** |
| supersession of a LIVE question happens at ACTIVATION, not acceptance | `_terminalize_older(include_outstanding=True)` | `a04`, `n02`, `n03` |
| an older INACTIVE question can never activate once a newer ask exists | `_refuse_stale_activation` (§4.1) | `n01`, `n01d`, `n07`, **`k06c` on PostgreSQL** |
| an OUTSTANDING incumbent is deactivated ONLY by a newer question actually activating | `_terminalize_older(include_outstanding=…)`, acceptance passes `False` | `p01`–`p07` (§4.2), mutants **M38**, **M39** |
| one live question per field | partial unique index | `a04`, **`k03` on PostgreSQL** |
| provenance of the activation is kept | `delivery_status`, `activated_at` | `m03`, `m04`, `m06` |

One ordering detail is worth recording because the database caught it: activation
**supersedes the incumbent before** it promotes the new row. The first implementation
promoted first and superseded second, and the partial unique index refused it with
`IntegrityError` — the suite found that, not review.

### 4.1 Which of two asks may ACTIVATE — OC-R2-R1, scoped by OC-R2-R3

**The defect the owner found, and it was real.** Delivery callbacks do not arrive in the
order the messages were accepted. This sequence

```
  Q1 accepted  ─────────────────────────────────▶ PENDING_DELIVERY
  Q2 accepted (same thread, cycle, kind)  ─────▶ PENDING_DELIVERY
  Q2 delivered ─────────────────────────────────▶ OUTSTANDING          correct
  Q1 delivered, LATE ──────────────────────────▶ OUTSTANDING, and Q2 SUPERSEDED   ✗
```

ended with a question the customer had been re-asked out of owning the field, and the one
they actually have retired — on a callback Meta happened to deliver out of order.
The function that did it, `_supersede_live_for_field`, asked only *who is live?*. Nothing
asked *who is newer?*. It is gone, replaced by `_terminalize_older` plus
`_refuse_stale_activation`.

**The ordering is the row id, and it is the only candidate that survives inspection.**

| ordering candidate | verdict |
|---|---|
| **`id`** | **chosen.** Monotonic, immutable, assigned locally when the ask was accepted, visible to every connection, and already the column the table orders by |
| `asked_at` | a clock. Two asks in one burst can share it, and it can be rewritten |
| `wa_message_id` | Meta's opaque handle; it has no order |
| webhook arrival time | **explicitly excluded by the owner, and rightly**: it is a property of Meta's retry schedule, not of this conversation, and using it is exactly what makes the outcome depend on which callback landed first |

The owner tuple is `(thread_id, cycle_key, question_kind)` — the same three columns the
partial unique index guards, so "who may own this field" and "who is newest for this field"
are asked of the same grouping and cannot disagree.

**THE RULE — OC-R2-R3, in the owner's words, and now the only statement of it in this
document or in the code:**

> Only the newest **eligible** row may newly **activate**. An existing `OUTSTANDING`
> incumbent remains active until a newer question **actually activates**. A newer terminal
> sibling prevents an older **inactive** row from activating later, but does not
> retroactively deactivate an incumbent that was already `OUTSTANDING`.

**That is the actual intended contract and it is what the code already does** — §4.2 measures
all six of the owner's sequences through the service to establish it rather than argue it. The
previous revision's prose did not say this consistently: case B said an undelivered re-ask
retires nothing, while three other passages generalised sequence E ("nothing is active") into
a universal rule. **Case B was the correct one.** The ordering rule governs **activation**,
never **deactivation**, and the three enforcement points say so:

1. **a row with a newer sibling can never activate**, whatever its own delivery turns out to
   be; it becomes `SUPERSEDED` with `closed_reason = SUPERSEDED_BEFORE_DELIVERY`, keeping the
   `delivery_status` that arrived so the provenance is not lost
   (`_refuse_stale_activation`);
2. **when the newest row activates**, every older sibling leaves its non-terminal state in
   the same transaction — `OUTSTANDING` as `SUPERSEDED_BY_REASK`, `PENDING_DELIVERY` as
   `SUPERSEDED_BEFORE_DELIVERY` — and nothing writes `activated_at` or `delivery_status` on
   a row other than the one being activated (`_terminalize_older`, reached from the
   activation path only);
3. **accepting** a newer ask retires older `PENDING_DELIVERY` siblings and **nothing else**
   (`include_outstanding=False`), and the `failed` branch writes **only** the row carrying
   that WAMID. So an incumbent the customer is holding survives a re-ask that Meta merely
   accepted, and survives that re-ask failing. **Nothing can take the field from a question
   the customer demonstrably received except another question the customer demonstrably
   received.**

`record_asked` applies the same helper at acceptance (older `PENDING_DELIVERY` siblings only,
never a live incumbent), so a re-ask leaves no obsolete pending row behind for a late
callback to find. **That is eager hygiene through the same function, not a second copy of the
rule** — `_terminalize_older` is the one implementation and `n12` asserts from the AST that
the `id <` comparison appears in it and in no other function, after M34 taught this milestone
what two copies of one rule cost.

| the owner's case | sequence | required outcome | measured by |
|---|---|---|---|
| **A** | Q1 pending → Q2 delivered → Q1 delivered LATE | Q2 stays OUTSTANDING, Q1 terminal, Q2's provenance untouched | `n01`, `n01b`, `n01d` |
| **B** | Q1 outstanding → Q2 pending | Q1 **remains** active: an undelivered re-ask retires nothing. **This is the contract** — OC-R2-R3 confirmed it by measurement and withdrew the passages that said otherwise | `n02`, `a04`, `p01` |
| **C** | Q1 outstanding → Q2 delivered | Q2 supersedes Q1 (`SUPERSEDED_BY_REASK`) | `n03`, `a04` |
| **D** | Q1 pending → Q2 pending → Q2 delivered | Q1 is **terminalized**, as a written state | `n04` |
| **E** | a late `read` after Q1 was superseded | nothing changes; `read` cannot revive it | `n05` |
| **F** | concurrent Q1/Q2 activation, **two PostgreSQL connections** | the same winner in both interleavings | **`k06a`, `k06b`, `k06c`** |
| **G** | duplicate and reversed `delivered`/`read` after supersession | idempotent; the ask that activated still owns the field | `n01c`, `n06` |
| **H** | new inbound activity / a re-ask | no obsolete pending row capable of later activation | `n10` (through the live engine), `n04` |

Three things worth stating plainly rather than leaving for a reader to discover:

* **a newer sibling blocks an older INACTIVE row from activating, even when the newer one is
  itself terminal** (`UNDELIVERED`, `ANSWERED`). That is deliberate: if a failed newer ask
  let a never-activated older row activate, the outcome would again depend on whether Q2's
  `failed` arrived before or after Q1's `delivered`. `n07` pins it. **It does not reach an
  incumbent** — a row that is already `OUTSTANDING` is not activating, so this clause has
  nothing to say about it (`p02`, `p07`).
* **the cost, with the condition it actually depends on:** when there was **no active
  incumbent** — the first ask was still only `PENDING_DELIVERY` when the re-ask was accepted,
  so it was retired as obsolete — and the re-ask then fails or never confirms, nothing for
  that field is active and the older row may not stand in for it. The field is then unasked
  and the next turn asks it, which is the fail-closed direction. **When there was an active
  incumbent there is no such window**: it stays active until it is answered, until a newer
  ask is genuinely delivered, or until it expires on its own counted clock (§4.2 rows A–C,
  `p01`–`p03b`).
* **in the ordinary single-threaded sequence the eager sweep gets there first**, so the late
  callback finds a row that is already terminal and changes nothing. The activation guard is
  what holds when it does **not** — two connections, one of which had not yet seen the
  re-ask. That state cannot be produced in the sealed SQLite harness, so `n01d` constructs it
  and **labels itself as constructed**, and `k06a`/`k06b`/`k06c` produce it for real on
  PostgreSQL. Claiming the guard covered by the outcome the sweep happened to produce would
  have been the M25 mistake again.

### 4.2 The six sequences, measured

Driven through `record_asked` and `activate_on_status` against a real database, with the row
states read back after every step. No prose was consulted; the table is the output.

| seq | sequence | Q1 after | Q2 after | `live()` |
|---|---|---|---|---|
| **A** | Q1 OUTSTANDING → Q2 accepted | `OUTSTANDING` | `PENDING_DELIVERY` | **[Q1]** |
| **B** | … → Q2 `failed` | `OUTSTANDING` | `UNDELIVERED` / `DELIVERY_FAILED` | **[Q1]** |
| **C** | … → Q2 stays pending, 2 customer turns | `OUTSTANDING` | `PENDING_DELIVERY` | **[Q1]** |
| **C′** | … → the 3rd turn | `ABANDONED` / `EXPIRED_TURNS` | `PENDING_DELIVERY` | [] |
| **D** | Q1 OUTSTANDING → Q2 `delivered` | `SUPERSEDED` / `SUPERSEDED_BY_REASK` | `OUTSTANDING` | **[Q2]** |
| **E** | Q1 pending → Q2 accepted → Q2 `failed` → late Q1 `delivered` | `SUPERSEDED` / `SUPERSEDED_BEFORE_DELIVERY`, callback changed **0** rows | `UNDELIVERED` / `DELIVERY_FAILED` | [] |
| **F** | Q1 superseded → Q2 `ANSWERED` → late Q1 `read` | `SUPERSEDED`, callback changed **0** rows, `activated_at` unchanged | `ANSWERED` | [] |

Read A, B and C against E. **The difference is not the re-ask; it is whether an incumbent was
ever activated.** A, B and C keep a live question because Q1 had been delivered before Q2 was
accepted. E has none because Q1 was still only accepted when Q2 arrived, so Q1 was retired as
obsolete — correctly, since at that moment neither question was one the customer held. That
single distinction is what the previous revision's three passages collapsed.

**C′ is included so "remains active" is not read as "forever".** The incumbent leaves on the
third observed turn by `EXPIRED_TURNS` — the ordinary counted-expiry rule, measured at
`expires_after_turns = 3` — and not because of the re-ask. The re-ask is still pending
afterwards, which is the §18.6 residual risk and not this contract's.

**`TestR2P_Incumbent` (8 tests) pins all seven rows**, and `p07` states the rule as a property
rather than a sequence: across every non-activating event that can reach the service —
`sent`, `pending`, an unknown status, a status for another message, the acceptance of a
re-ask, that re-ask failing, and duplicates of each — an `OUTSTANDING` incumbent is still
`OUTSTANDING` afterwards.

---

## 5. Creation, matching and closure rules in full

### 5.0 The status-webhook integration path, measured before it was designed

The owner's instruction was to inspect this and not assume it exists. What is there:

| element | finding |
|---|---|
| route | `backend/app/routes/whatsapp.py::inbound_webhook`, `POST /integrations/whatsapp/webhook` — the **same** route that receives inbound messages. Meta delivers `messages` and `statuses` in the same envelope |
| status handling | already present: it iterates `value["statuses"]`, matches `status_item["id"]` to `whatsapp_messages.wa_message_id`, and applies `STATUS_PRECEDENCE` (`pending < sent < delivered < read < failed`), ignoring downgrades and no-ops |
| unknown WAMIDs | already raise a `META_STATUS_FOR_UNKNOWN_WAMID` HIGH security event, and a `delivered`/`sent` for an unknown WAMID while outbound is off raises the `SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF` BLOCKER |
| authenticity | `X-Hub-Signature-256` HMAC against `WHATSAPP_APP_SECRET`, verified on the raw body **before** the JSON is parsed; `_verify_signature` **fails closed** when the secret is unset. `WHATSAPP_APP_SECRET` **is set** in the deployed container |
| does it actually receive them? | **yes, measured.** `crm_test` outbound: **43 `read`, 1 `delivered`, 1 `failed`** — and **0** rows stranded at `sent` or `pending`. Production `crm`: 4 `read`, 5 `delivered`, 8 `failed`, also none stranded |

So the deployed integration does receive delivery and read statuses reliably, and the owner's
STOP condition ("if the deployed integration does not receive them, present the measured
incompatibility") **does not apply**. The correction needs no new transport, no new route and
no new authenticity boundary: one call is added inside that existing authenticated handler.
`m12` asserts from the source that the activation site is unique, that it sits **after**
`_verify_signature`, and that no second webhook route was introduced.

### 5.1 Why acceptance is not delivery, and what each provenance means

`_send_text_to_wa` is the one place a turn can occupy a message. The recorder is called
**inside** the `try` that performs the Meta send, **after** `gate.mark_sent(...)`. That is
the point at which Meta has accepted the message and handed back a correlation handle — and
it is **not** the point at which the customer has it. Every way a turn can fail even to be
accepted leaves that line unreached: the gate blocked it (`OutboundBlockedError` is raised
earlier), Meta raised (`MetaSendError` is caught below), composition suppressed every
proposition, or the text was empty.

The row created there is `PENDING_DELIVERY`. **A message sitting in Meta's queue, or rejected
outside the 24-hour session window, was never asked of anybody**, and the whole purpose of
the contract is to suppress a reply on the strength of having asked — so treating acceptance
as delivery would have let an undelivered message buy silence. `m10` measures exactly that
case and requires the greeting to go out.

Two provenances for *who worded it*, unchanged by the correction:

* **`DETERMINISTIC_TEMPLATE`** — the clarification producer's typed proposition reached the
  composed message. `plan.included()` answers that **structurally**, from the same
  `_ordered_included()` set `compose()` rendered; the G3-7F owner resolution forbids proving
  inclusion by searching customer prose, and this does not.
* **`REPLY_MODEL`** — the reply model's own sentence asked it. **On the Wild's turn 1 there
  was no proposition at all**: the clarification producer requires a focus candidate with a
  `tipo_vehiculo`, and turn 1 had no candidate, so the location question came purely from the
  model's prose. `_reply_already_asks_location` is the **existing** certified probe that same
  producer already uses to avoid asking twice — reused, not copied.

Reading our own delivered sentence is not treating model prose as authority over the
customer: it is observing what we sent. No claim about the customer is derived from it, and
closing the question still requires R1's admitted evidence — delivery alone closes nothing
(`m10`, `m11`).

### 5.2 What does not close a question

| offered | refused as | measured by |
|---|---|---|
| `en san nicolas` (ambiguous) | R1 never admits it | `d01` |
| `en mar del plata` (unsupported) | R1 never admits it | `d02` |
| `en flore` + the interpreter's `Flores` | `INFERRED_NOT_EXPLICIT` | `d03` |
| `soy de flores` (the customer's own origin) | `ROLE_DECIDED_BY_TEXT` — LR-2 | `d04` |
| a reading naming an earlier turn's message | `STALE_TURN` | `d05` |
| a model reply asserting the customer answered | nothing in `match` can see it | `d06` |
| a locality, against a VEHICLE question | `WRONG_FIELD` | `d07`, `a08` |
| evidence from another cycle | `STALE_CYCLE` | `d08`, `a08` |
| evidence about another candidate | `WRONG_CANDIDATE` | `a09` |
| a second close of the same question | the conditional update | `a11`, `d10` |

One line is worth stating plainly: **every ambiguity, coverage, negation and conflict case is
refused by R1, not by R2.** R2 adds no second opinion about any of them — a refusal is simply
never a match. That is why `match` is a pure function over typed decisions and why it is
short.

---

## 6. Cross-turn evidence binding

`bindable_locality` offers a row only when five conditions hold, each a column rather than an
assumption: same thread; same `cycle_key`; `role = INSPECTION_LOCATION`; `outcome = ACCEPT`;
not superseded and not already bound.

**This is what retires `RISK-03` for typed rows, and only for those.** `_apply_candidate`
refuses to inherit `state.home_zone_*` because an untyped buffer value cannot say which
cycle, turn or role it came from. The guard stays exactly where it is. A row can answer all
three questions, so the binding is *checked*.

**Measured, and it changed the implementation:** on the Wild's turn 3
(`es un toyota corolla 2024`) the candidate is created by `_create_candidate_from_catalog`,
**not** by `_apply_candidate`. A binding placed only in the latter — which is where the
review's R2 plan points — would never have run on the very sequence this milestone exists to
close. Both sites now call one method (`test_j08` pins both).

The value still goes through `_apply_inspection_zone`, so the C2 chokepoint and the certified
reconciler remain the only authority; `zone_evidence_id` then records which row set it.

---

## 7. Location-form convergence (W1FRV-04)

The finding, in the review's words: the Location Flow "writes `state.home_zone_*` only;
candidate 144 `zone=NULL`; reconciler not consulted". The strongest evidence class in the
system — the customer typed it into a form we sent — was the one write that bypassed the
authority every weaker producer goes through.

It now takes the identical route: the R3 validator has already run (the UNSUPPORTED and
AMBIGUOUS branches are the fail-closed exits and still come first), the write goes through
`_apply_inspection_zone`, and the claim is `HUMAN_CONFIRMED` — the top of the reconciler's
ladder, so it outranks every other producer **by the certified rule rather than by assignment
order**. The target is chosen by the same `_is_current_focus` predicate admission uses.

`f01` measures the candidate carrying `('CABA','Flores')`; `f03`/`f04` measure unsupported
and ambiguous submissions still writing nothing; `f02` measures the flag-OFF path unchanged;
`j06` asserts the helper assigns no zone field itself.

---

## 8. The routing effect of ANSWERED — exactly

```python
if self._answered_question_kinds():      # Layer F, between step 8.5 and step 9
    return None                          # "continue", and NOTHING else changes
```

* It is a **set of kinds**, not a boolean, so "the customer answered the location question"
  can never be read as "the customer answered whatever we are about to ask".
* It is read by **exactly one function**, and `test_j09` proves that from the AST rather than
  from a string count: the only reader is `_handle_qualifying_intent`.
* It is reached only after steps 1–8 have all declined, so it takes no decision away from
  them. Layers A/B/C/D and every coverage, consent, pricing, scheduling, handoff and booking
  authority sit outside that function entirely.
* It **sets no intent**. An answer about *where the car is* is not a statement of commercial
  intent, and inventing one would be the silent invention the milestone forbids (`i03`).
* `i01`/`i02` measure that no offer, slot, booking or revision appears and the stage is not
  advanced; `i04` measures the motorcycle, phone-call and unsupported-service gates
  byte-identical with the flag on and off.

---

## 9. R1-PL-01 — what was removed, and what remains

Owner decision OC-R1-R1 chose option (c) and set three conditions. All three are met, and
the honest limits are stated.

### 9.1 One shared predicate

`claim_projection.locality_mention_possible(texts, *, resolve)` — in the module that already
owns the verbatim rule (`_explicitness`), with the catalogue resolver **handed in** so the
module stays DB-free and exactly one resolver is in play. `test_g05` pins one definition and
one engine call site; the predicate holds no name list, no window scan and no second
normalisation.

### 9.2 The design changed twice, and both times a measurement forced it

**First change — a probe disproved the monotonicity assumption.** The first version of the
predicate asked the resolver about the whole burst and claimed the resolver was *monotone*:
that a name found in a substring is found in the whole text. A probe over all 211 catalogue
localities disproved it. Twelve article-prefixed names (`La Plata`, `La Boca`, `El Talar`, …)
resolve standing alone and are correctly refused inside `me mordí la boca`, because the
L4.7W5-F5 article rule is *about context*. Window-scanning the burst instead would restore
safety and cost **up to 770 ms per turn** — measured — which is worse than the wait it exists
to avoid.

**So R2 added a second mechanism: the admission validator re-validated the claim value
against the burst. That was wrong, and mutation M21 proved it.** The mutant disabled the
context branch and **SURVIVED**, which was the correct answer: the branch can never fire.
With R2 on, the predicate gates the semantic read on `resolve(burst) is not None`, so by the
time any claim exists the burst has already resolved — and the only case the context check
would refuse is exactly the case where **no claim was ever produced**.

Measured before removing it: a spy over twelve bursts, including every La Boca case,
recorded **7 validator calls and 0 context-branch refusals**. The check was a second
mechanism for a case the first one already owned, and an unreachable guard misleads the next
reader more than it protects anyone. It is gone, and the comment where it stood records why
so the next person does not re-add it.

**What actually closes the hole: the predicate is self-protecting.** The one case where the
matcher is non-monotone — `resolve(burst)` None while `resolve("la boca")` is not — is
precisely the case where the predicate skips the semantic read, so no claim exists to admit.
One mechanism, not two.

**And the hole is real — but it is not reachable from any supported flag posture, and
OC-R2-R1 is right that the previous wording implied otherwise.** R1 validates a claim value
in isolation, where the article rule cannot apply:

| burst | interpreter says | the admission boundary ARMED IN ISOLATION (interlock bypassed — a test-only posture) | the integrated posture R1+R2+R3 |
|---|---|---|---|
| `me mordi la boca` | `la boca` | **`('CABA','La Boca')` written** | `(None, None)`, wait skipped, **0 records** |
| `me mordi la boca` | `La Boca` | **`('CABA','La Boca')` written** | `(None, None)`, wait skipped, **0 records** |
| `el auto esta en la boca` | `La Boca` | `('CABA','La Boca')` | `('CABA','La Boca')` — not a blanket refusal |

**Read the second column exactly as it is labelled.** It is not a flag posture. Since OC-R2
correction 2 there is **no supported runtime configuration in which R1 is active on its own**:
`_integrated_features_armed()` requires all three flags, so `EVIDENCE_ADMISSION_ENABLED=true`
with R2 off leaves the admission boundary **inert** — `test_e01` in the R1 suite is now
literally named "R1 alone is inert", and the eight-row matrix of §9.4 proves it for every
combination. The defect in that column is reproduced only by **invoking the isolated
admission boundary directly and bypassing the integrated interlock**, which is what the
safety test does deliberately so the limitation is measured rather than asserted.

So the honest statement is: the `W1F-C-18` protection can be defeated through the semantic
path **at the admission boundary considered alone**, and the integrated posture closes it;
there is no flag setting that exposes it. `test_g06` asserts the mechanism (skip, zero
records) and not merely the outcome, plus the isolated-boundary contrast, so a future change
to the gating cannot silently reopen it while the test still passes.

### 9.3 Timing, by the PASS-B method

Real `TurnSemanticEvidence` provider, real bounded shadow worker, a local fake interpreter
that sleeps. Dispatch separated from waiting throughout.

**Re-measured for OC-R2-R1, not relabelled.** The previous table's comparison column was
headed "R1 only", and after the interlock that posture is **inert** — the column described
code that no longer runs, which is precisely the §9.2 mistake in numeric form. The honest
comparison is the committed behaviour, the integrated behaviour, and what the integrated
posture *would* cost without the shared predicate (measured by pinning
`_locality_possible_in_burst` to `True`), which is the residual R1-PL-01 cost the predicate
removes. 600 ms fake interpreter; production bound is `SEMANTIC_SAME_TURN_TIMEOUT_SECONDS=6.0`.

| burst | committed (all flags OFF) | integrated R1+R2+R3 | integrated, predicate forced | wait skipped | interpreter dispatched |
|---|---|---|---|---|---|
| `buenas` | 101 ms | **104 ms** | 624 ms | yes | yes (1/1/1) |
| `hola` | 26 ms | **32 ms** | 627 ms | yes | yes (1/1/1) |
| `de que consta la revision?` | 24 ms | **81 ms** | 623 ms | yes | yes (1/1/1) |
| `cuanto sale?` | 640 ms | 700 ms | 720 ms | yes | yes (1/1/1) |
| `es un corolla del 2024` | 673 ms | 662 ms | 674 ms | yes | yes (1/1/1) |
| `me mordi la boca` | 627 ms | 625 ms | 638 ms | yes | yes (1/1/1) |
| `en flores` | 626 ms | 638 ms | 635 ms | **no** | yes (1/1/1) |
| `el auto esta en flores` | 635 ms | 655 ms | 650 ms | **no** | yes (1/1/1) |
| `en mar del plata` | 629 ms | 641 ms | 631 ms | **no** | yes (1/1/1) |
| `en san nicolas` | 623 ms | 643 ms | 642 ms | **no** | yes (1/1/1) |

**This is a stronger result than the previous table claimed, and a narrower one.** Stronger:
R1-PL-01 is now an *absolute* statement — the integrated posture costs what the committed one
costs on `buenas`, `hola` and the locality-free FAQ (104/32/81 ms against 101/26/24 ms), and
the third column shows the predicate is what makes that true, saving ~520–600 ms per such
turn. Narrower: on `cuanto sale?`, `es un corolla del 2024` and `me mordi la boca` the
**committed** posture already waits ~630 ms, so there was never anything for R2 to remove
there — the wait belongs to inherited certified readers, exactly as the owner's narrow
acceptance of R1-PL-01 records.

**Read the last column first.** The interpreter is dispatched on every turn in every posture,
and that is the starting HEAD's behaviour, not R2's: `_run_shadow_understand` sits above
Layer A and R2 does not move it. R2 removed the **wait**, which is the only part a customer
can feel. `_turn_semantic_calls == 0` would not prove any of this and is not used as if it
did (`g01` asserts the fake interpreter was constructed exactly once).

**And now the limit, stated precisely.** Admission skips its read on *every* locality-free
burst (`g03`), but the **turn** stops waiting only where admission was the sole reader —
`buenas`, `hola`, the FAQ. `cuanto sale?` and `es un corolla del 2024` still wait, because
they have **inherited** readers of the same memoised provider that need the interpretation
for their own reasons (scheduling admission, FAQ topic authority) — and the first column
shows those waits are already there on `a6aabece`. Those are not R2's to remove and removing
them would weaken certified behaviour. **The canonical proof cases the owner named —
`buenas`, `hola` and the locality-free FAQ — are closed; "no turn without a locality ever
waits" is not, and R2 does not claim it.**

---

## 9.4 The complete flag matrix — OC-R2 CORRECTION 2

The owner's correction, and the measurement behind it is this document's own: **R1 ON with
R2 OFF wrote `('CABA','La Boca')` out of `me mordí la boca`** (§9.2). Guarding only
"R2 without R1" was therefore not enough. R1's own safety rests on R2's predicate — the
predicate is what declines to consult the interpreter about a burst whose words resolve to
no locality — and R3 is what makes a resolved locality safe to price at all.

So **R1 and R2 behaviour is one interlocked switch requiring all three flags**, implemented
once in `_integrated_features_armed()`. `_admission_on()` and `_r2_on()` delegate to it and
add no condition of their own; a second opinion about the posture is how interlocks drift
apart. **R3 keeps its independence**, exactly as the owner specified.

| # | `EVIDENCE_ADMISSION` | `OUTSTANDING_QUESTION` | `LOCALITY_RESOLUTION_SAFETY` | R1/R2 behaviour | R3 behaviour |
|---|---|---|---|---|---|
| 1 | OFF | OFF | OFF | **inert** — the starting behaviour | off |
| 2 | **ON** | OFF | OFF | **inert** | off |
| 3 | OFF | **ON** | OFF | **inert** | off |
| 4 | OFF | OFF | **ON** | **inert** | **its certified default-off safety behaviour** |
| 5 | **ON** | **ON** | OFF | **inert** — no R3 underneath it | off |
| 6 | **ON** | OFF | **ON** | **inert** — *this is the posture that admitted `la boca`* | on |
| 7 | OFF | **ON** | **ON** | **inert** | on |
| 8 | **ON** | **ON** | **ON** | **integrated R1+R2+R3** | on |

Row 8 is the intended posture and the only one that does anything. `test_h04` asserts the
table flag-by-flag — including that `_locality_safety_on()` still tracks R3 alone —
`test_h04b` drives all eight rows through **complete turns** on the five inputs the owner
named (the La Boca idiom, San Nicolás, an unsupported locality, stale evidence, and an
admissible locality), and `test_h04c` checks candidate isolation in every row. In the seven
inert rows the assertions are absolute: no zone written, no evidence row, no question, and
`_turn_admission` is `None`.

Six mutants attack the interlock from six directions and all six die: **M29** allows R1
without R2, **M30** allows R1+R2 without R3, **M31** allows R2 without R1, and **M32/M33/M34**
bypass the interlock at the admission, question-recording and candidate-binding sites
respectively.

**What this costs, stated plainly:** it changes R1's committed flag semantics.
`EVIDENCE_ADMISSION_ENABLED` alone no longer does anything, and the R1 suite was updated to
arm the interlock as one switch — including two R1 assertions whose subject moved (§12.4).
That is a real change to a committed milestone's contract, and it is the owner's decision
rather than mine; it is recorded as such.

---

## 10. Tests

Sealed every time: `docker run --rm --network none -e DATABASE_URL=sqlite://
-e PYTHONDONTWRITEBYTECODE=1 -v <tree>:/work:ro -w /work/backend
ridecheck-crm-test:l4.7b2 python -m pytest … -q -p no:cacheprovider`. The migration group
additionally receives `R2_PG_URL` and runs on its own docker network.

### 10.1 Focused

| suite | result |
|---|---|
| `test_w1f_w01_r2_outstanding_question.py` | **148 passed, 186 subtests passed** with `R2_PG_URL` set; **138 passed / 10 skipped** sealed without it (the PostgreSQL group is the ten that skip). OC-R2 added `TestR2M_Delivery` (17 tests), the eight-row matrix, three concurrency/rollback tests and the two PostgreSQL interleavings; **OC-R2-R1 added `TestR2N_Ordering` (15 tests) and `k06a`/`k06b`/`k06c`**; **OC-R2-R3 added `TestR2P_Incumbent` (8 tests)**. 148 tests in 15 groups |
| `test_w1f_w01_r1_evidence_admission.py` | **72 passed, 161 subtests passed** — six assertions moved by the interlock (§12.4), none by OC-R2-R1 (§12.5) |
| the two committed R3 suites | **82 passed, 507 subtests passed** |
| the certified regression set (**86** modules: semantic, conversation, candidate, location, zone, quote, scheduling, claim, reconciler, clarification, intent, FAQ, form, outbound, idempotency, hybrid-trace, G3 series), flag **OFF** | **3 261 passed, 3 662 subtests, 188 skipped, 16 failed** |
| the same 86 modules, integrated posture (R1+R2+R3 ON) | **3 261 passed, 3 662 subtests, 188 skipped, 16 failed — the 16 identities byte-identical to the OFF run** |
| the three W1F suites together, integrated (R1 + R2 + both R3) | **292 passed, 10 skipped, 843 subtests passed** |

All 16 focused failures are **inherited**: every one of them is in the pristine `a6aabece`
baseline identity set of §13, checked by name rather than by count (12 are
`test_m19_r1_2_outbound_gate`, 2 are `test_m21_1_1_service_intent_gate`, one is
`test_br1_intent_gate` and one is the `test_or_73` `SUBFAILED`).

### 10.2 The migration rehearsal

On a **disposable** PostgreSQL **16.13** (`r2-rehearsal-pg`, its own docker network, created
for this and disposed by exact name — never the live server, never `crm_test`). The deployed
schema-adoption path, not a clean Alembic chain: the pre-R2 schema is built by
`Base.metadata.create_all` from a pristine `git archive a6aabece`, the parent revision is
stamped, and only then does this one run.

| branch | result |
|---|---|
| **CREATED** — the tables absent, as on `crm_test` today | `whatsapp_thread_evidence=CREATED whatsapp_outstanding_questions=CREATED candidates=+2`; **23** and **24** columns; both candidate columns added; the partial unique index present |
| the partial unique index, against the real server | a second **live** question for the same field is refused (`IntegrityError`); a second **ANSWERED** row for it is accepted |
| **downgrade with rows** | **REFUSED**, head unchanged, both rows still there |
| **downgrade when empty** | `DROPPED (both tables empty, no candidate reference set)`; head back to `20261022_operator_notifications`; the two candidate columns gone |
| **re-upgrade** | both tables **fingerprint-identical** to the first upgrade (`table_fingerprint` equality, not a column-name comparison) |
| **ADOPTED_EMPTY** — the tables already built by `create_all` | `…=ADOPTED_EMPTY …=ADOPTED_EMPTY candidates=+0` |

`test_k05` additionally asks `pg_constraint` — not the code — that every reference which
could block the certified tester reset is `SET NULL`, and that `outbound_message_id` is not
a foreign key at all.

**Re-run unchanged for OC-R2-R1.** The ordering correction touched no schema — no column, no
index, no CHECK — so the migration file is byte-identical to the revision the owner reviewed.
The rehearsal was repeated anyway, on a freshly created disposable server, and all five
branches passed again; the ordering tests then ran against that schema, which is where
`k06a`/`k06b`/`k06c` live.

### 10.3 The boundaries that are easy to assume rather than measure

| boundary | measured |
|---|---|
| a crash **inside** the turn (`_handle_qualifying_intent`, `_apply_zone_from_text`) | `handle()` returns `action='error'`; the question is still OUTSTANDING, no evidence row, no zone — the answer has not been consumed, so a retry still consumes it (`l01`, `l02`) |
| a crash **after** the turn committed (`_write_hybrid_trace`) | the committed work stands: ANSWERED, the evidence row, the zone. Observability must not be able to undo an answer the customer already has a reply to (`l02b`) |
| two workers closing one question | one winner, one `False`, and the loser does not overwrite the winner's provenance (`l03`, `a11`) |
| two concurrent asks for one field | the partial unique index refuses the second live row (`l04` on SQLite, `k03` on PostgreSQL) |
| two concurrent **delivery callbacks** for two asks of one field | the NEWEST accepted ask wins in both interleavings, on two real connections (`k06a`, `k06b`) |
| a late delivery where the newer ask is already ANSWERED, so the index cannot help | the stale question stays terminal; the guard alone holds (`k06c`) |

---

## 11. Mutation run

Each mutant is a real behaviour change, planted one at a time on a copied tree, run against
the R2 module, the R1 module and both R3 modules.

**39 planted, 39 killed, 0 survived — but one of them is killed on PostgreSQL and not in
the sealed SQLite harness, and that distinction is stated rather than averaged away.**

| where | planted | killed | survived | measured |
|---|---|---|---|---|
| sealed SQLite harness (`--network none`) | 39 | **38** | 1 (**M25**) | the survivor is M25 and only M25 |
| **M25** re-planted against the PostgreSQL rehearsal database | 1 | **1** | 0 | `k03b` + `k03c` fail: 2 failed / 138 passed / 174 subtests |
| **M35** (the ordering guard) re-planted on PostgreSQL — an **additional, independent** kill, not a substitute for its SQLite kills | 1 | **1** | 0 | `k06b` + `k06c` fail: 2 failed / 8 passed in the K group |

All figures re-measured on the final tree after OC-R2-R3 (which raised the count to 39 with
M38 and M39, §11.1d): **39 planted, 38 killed in the sealed harness, M25 the sole survivor
there and killed on PostgreSQL**, `stale_trees.py` reporting `STALE: none` for the re-run
tree. The per-mutant verdicts are in §11.1a–d.

**Every verdict is a verdict against the FINAL tree, and that is checked rather than
assumed.** A mutation run copies trees over ~45 minutes, so a later edit can leave the early
trees describing an earlier state — which happened twice here, both times to comments or
docstrings. Rather than argue that comments are inert, `stale_trees.py` hashes each tree's
copy of the two test modules against the final one (no mutant in the table touches them, so
they must match byte for byte) and any tree that differs is **re-run**. `verify_anchors.py`
confirms all 39 anchors are present exactly once before a pass starts; a drifted anchor is an
assertion failure, never a silent skip, which is the protection added after one run truncated
at M15. The 37 of OC-R2-R1 were additionally run twice end to end, and both passes agreed
(36 killed, the same single survivor).

**M25 cannot be killed in SQLite, by construction.** It removes the
`status = 'PENDING_DELIVERY'` condition from the activation UPDATE, and the SELECT
immediately above it carries the same predicate — so in one process the two statements
cannot be interleaved and a second caller's own SELECT already returns nothing. My first
attempt at killing it was a two-session SQLite test; **it passed against the mutant as well
as against the real code, so it proved nothing.** Rather than report a kill I could not
support, the assertion moved to `test_k03b`/`k03c`, which take two real PostgreSQL
connections and interleave them: A reads the pending row, B activates and commits, A's
UPDATE then runs against a row that has moved on. With M25 planted — in the service **and**
in the raw statements those tests issue, so the server is not being tested instead of the
guard — both fail and the mutant dies (measured on the final tree: 2 failed, 138 passed,
174 subtests).

**Six mutants survived a first run across the two revisions and every one of them was a real
gap in my tests, not an equivalent mutant.** §11.1b and §11.2 are the honest account of all
six.

| # | mutant | killed by |
|---|---|---|
| M01 | a question is recorded BEFORE delivery is confirmed | `c01`–`c03`, `j01` |
| M02 | stale-cycle evidence closes a question | `a08`, `d08` |
| M03 | the wrong question KIND is closed | `a08`, `d07` |
| M04 | a question closes without matching admitted evidence | `d01`–`d06` |
| M05 | evidence binds to a candidate from another cycle | **`e03b`** (written for it) |
| M06 | a REFUSED evidence row becomes bindable | **`e02`** (strengthened for it) |
| M07 | a `mentioned` candidate inherits the locality | **`e04b`** (written for it) |
| M08 | an already-bound evidence row is bound again | `e03`, `e04c` |
| M09 | a duplicate delivery opens a SECOND question | `a03`, `c06` |
| M10 | closure is not conditional (the same answer twice) | `a11`, `d10`, `l03` |
| M11 | a replayed claim inserts a second evidence row | `e07` |
| M12 | the location form bypasses the reconciler (W1FRV-04 reopened) | `f01`, `j06` |
| M13 | `buenas` waits on semantic completion again (R1-PL-01 reopened) | `g01`, `g03` |
| M14 | the predicate says a locality-free burst MIGHT have one | `g01`, `g03` |
| M15 | the unsafe partial flag combination is allowed (R2 without R1) | `h04`, `h05` |
| M16 | the R2 flag defaults ON | `h01` |
| M17 | a merely truthy setting arms R2 | `h03` |
| M18 | an answered locality question suppresses MORE than the greeting | `i03` |
| M19 | expiry is never counted, so a question lives forever | `a12` |
| M20 | a question about another candidate is closed | `a09` |
| M21 | the predicate uses the R3-guarded resolver and loses the refusals | `d01`, `d02`, `e01` |

### 11.1b The mutants OC-R2 added, and the three that survived first

| # | mutant | killed by |
|---|---|---|
| **M22** | `sent` is treated as delivery | `m02`, `m11`, `m13` |
| **M23** | acceptance creates an ACTIVE question again — *the original defect* | `m01`, `m10`, `c05` |
| **M24** | a `PENDING_DELIVERY` question counts as live | `m01`, `m10` |
| **M25** | activation is not conditional, so a late `delivered` resurrects a failure | **`l03b`, `l03c`** (written for it) |
| **M26** | `failed` is not terminal | `m05`, `m07` |
| **M27** | the early-callback reconciliation is dropped | **`m08d`, `m08e`** (written for it) |
| **M28** | the webhook no longer activates anything | `m12` |
| **M29** | R1 is allowed without R2 | `h04`, `h04b` |
| **M30** | R1+R2 are allowed without R3 | `h04`, `h06` |
| **M31** | R2 is allowed without R1 | `h04`, `h05` |
| **M32** | the interlock is bypassed at the ADMISSION site | `h04b` |
| **M33** | the interlock is bypassed at the QUESTION site | `h04b`, `h05` |
| **M34** | the interlock is bypassed at the BINDING site | **`e04d`** (written for it) |

### 11.1c The three OC-R2-R1 added for the ordering

| # | mutant | killed by |
|---|---|---|
| **M35** | the newer-question ordering guard never refuses a late delivery — *the defect the owner found* | `n01c`, `n01d` in SQLite, **and `k06b`, `k06c` on PostgreSQL** |
| **M36** | accepting a re-ask no longer retires the older pending row | `n04`, `n10`, `n11` |
| **M37** | the ordering becomes the **clock** (`asked_at`) instead of the row id | `n08` |

### 11.1d The two OC-R2-R3 added for the incumbent contract

| # | mutant | killed by |
|---|---|---|
| **M38** | **accepting** a re-ask retires the `OUTSTANDING` incumbent (`include_outstanding=True` at the acceptance call) | `a04`, `n02`, **`p01`** |
| **M39** | a **failed** re-ask retires the `OUTSTANDING` incumbent (the `failed` branch sweeps older rows) | **`p02`**, **`p07`** — and nothing else |

**M39 would have survived the previous revision, and that is the measurable form of the
owner's finding.** Nothing in the suite asserted that a failed re-ask leaves an incumbent
alone — which is precisely the property the two contradictory passages disagreed about. A
contradiction in the prose was a gap in the tests, every time; this is the fourth time this
milestone has found that, after M05, M27 and M34. M38 by contrast is killed by three tests
including two that predate this correction, so acceptance was already pinned; it is included
because the owner asked for both halves of the event pair, and because a mutant that dies
easily still documents which rule it attacks.

**M35 is the mutant the owner asked for, and it dies twice over.** In the sealed harness
`n01d` catches it: the guard is the only thing standing once `_unswept` has restored the
interleaved state, and without it the stale question activates. On PostgreSQL it dies again
in `k06b` (the older callback first, with both asks committed) and — the one that matters —
in **`k06c`**, where the newer question is already `ANSWERED` so the partial unique index is
satisfied either way and **only the guard** prevents a replaced question from becoming live:
with M35 planted, `rows[q1][0] == 'OUTSTANDING'`, measured. That distinction is worth the
sentence, because in `k06a` the mutant **passes** — there the correct outcome is also the one
the index and the supersession sweep would produce, so that test does not interrogate the
guard. Reporting the PG kill without saying which of the three parts did the killing would
have overstated it.

**M36 earns its place by catching something the guard alone would hide.** Without the
acceptance-time sweep the obsolete row survives as `PENDING_DELIVERY` and is merely
*unable* to activate — which satisfies the outcome but not the doctrine this milestone runs
on, that every state is written and none is inferred from unreachability. `n04` and `n10`
require the written terminal state.

**None of M35–M37 survived a first run** — the ordering tests were written with the mutants
in mind, which is the cheaper order when the rule is already understood. **M25, M27 and M34
did survive their first run, and all three were genuine holes rather than equivalent
mutants.**

**M25 — a guard only a race can exercise needs a race to test it, and the sealed harness
could not provide one.** The mutant removes the `status == PENDING_DELIVERY` condition from
the activation UPDATE. It survived because the SELECT immediately above already filters on
that status, so in a single process the condition is unreachable. It is not redundant across
connections: Meta can deliver `delivered` and `read` for one message close together, the
webhook handles each in its own transaction, and without the condition the second re-stamps
a row that has moved on — overwriting the activating provenance, and resurrecting a `failed`
question if the callbacks arrive in that order.

I wrote `l03b`/`l03c` for it first, with two SQLAlchemy sessions, and **they passed against
the mutant too**, because each session issues its own fresh SELECT which already excludes
the row. They are kept — they prove the service's idempotency contract — but they are not
what kills M25. `test_k03b`/`k03c` on the PostgreSQL rehearsal database are, and that kill
is measured rather than argued; see the table at the top of this section.

**M27 — a reconciliation nobody invokes is not a reconciliation.** The mutant replaces the
engine's `reconcile_delivery(...)` call with `pass`. It survived because `m08`–`m08c`
exercise the *function* directly and nothing asserted that the engine actually calls it.
`m08d` and `m08e` now drive a complete turn with the ledger already carrying `read` (and
`failed`) for the WAMID the turn's transport will return, so the question must come out of
the turn already active — or terminal — with no explicit delivery step.

**M34 — the guard that looks redundant until the day it is not, and it guards this
closeout's own rollback claim.** The mutant removes the interlock from the candidate-binding
site. It survived because with the flags off no evidence rows are produced, so
`bindable_locality` finds nothing and the guard appears to do no work. It does work in the
one situation that matters operationally: the flags were ON, evidence was persisted, and
then they were turned OFF — which is precisely the behavioural rollback §14 offers as the
recovery path. Without the guard a candidate created afterwards would consume a leftover row
and write a zone, and **"stale rows cannot change behaviour once the flags are OFF" would be
false exactly when someone is relying on it.** `e04d` arms the posture, persists a locality,
disarms, creates a candidate and requires that nothing binds. It is as much a test of §14 as
of the binding.

All three are the same lesson in three shapes, and it is the one this programme keeps
relearning: **a test that drives the unit proves the unit, not the wiring, and not the
claim the unit is cited in support of.** R1's M10 was a weak assertion, R2's M06 was a weak
assertion, M07 was a rule with two homes, M25 and M27 were untested wiring, and M34 was an
untested operational claim.

**One fixture leak the new tests exposed, repaired where it was created.** Seeding "the
ledger already holds a status for the message this turn will send" requires the WAMID the
harness's fake transport returns — and that is `fake-text-1` for the first send of *every*
turn. The first version of `m08d`/`m08e` left those rows behind, and three later tests
silently got a pre-delivered question. `_drop_ledger` is now an explicit `addCleanup`. The
engine was right; the fixture was not.

### 11.2 The three that survived first, and what each one was

Mutation testing earned its cost three times here, and none of the three was a cosmetic
miss. All three are fixed and all three now die.

**M05 — a missing test, under a claim this closeout was already making.** The mutant
replaces the `cycle_key` filter in `bindable_locality` with a tautology. The suite had a
test for a prior-cycle QUESTION (`d08`) and **none for prior-cycle EVIDENCE**, so the one
column that makes contamination checkable was unguarded — while §15 claimed `W1F-C-22`
closed. The claim was right and the proof was absent, which is the worse of the two
failures. `e03b` now moves the cycle the way `_execute_cycle_reset` does and asserts both the
service and the engine refuse.

**M06 — a weak assertion, not a missing test, and the same shape as R1's M10.** The mutant
drops the `outcome = ACCEPT` filter. A coverage or ambiguity refusal carries no normalized
value, so it is skipped anyway — but `CONFIRMED_VALUE_PRESENT` and `OUTRANKED_BY_EXPLICIT`
are refusals the validator RESOLVED first, and for those the outcome filter is the only thing
between a refused reading and a candidate's location. Only the first class was covered.
`e02` now pins both.

**M07 — one rule with two homes, which is the W1FRV-07 shape.** The mutant deleted the
`status == "current_focus"` condition at the `_apply_candidate` call site and survived,
because the isolation rule lived only there. It moved into
`_bind_evidence_to_candidate` — the one place that performs the write — the duplicate
call-site condition was deleted so the rule has a single home, and `e04b` calls the method
directly with a `mentioned` candidate, which is the only way to prove a rule cannot be
bypassed by a caller. The retargeted M07 attacks the rule itself and dies.

**And one mutant was retired rather than fixed: the original M21.** It disabled a
context-aware branch in the admission validator and survived — correctly, because that
branch was unreachable (§9.2). The branch was removed as dead code and M21 was replaced with
a reachable mutant that narrows the predicate to the R3-guarded resolver, which would lose
every coverage and ambiguity refusal. It dies.

The milestone's eleven required families map as follows: recording before confirmed delivery
(**M01**), accepting stale-cycle evidence (**M02**, **M05**), closing the wrong question type
(**M03**), closing without matching admitted evidence (**M04**, **M06**), binding evidence to
the wrong candidate (**M07**, **M08**, **M20**), duplicate open question creation (**M09**),
processing the same answer twice (**M10**, **M11**), bypassing the reconciler from the
location form (**M12**), allowing `buenas` to wait (**M13**, **M14**), allowing a partial
unsafe flag combination (**M15**, **M16**, **M17**), and letting an answered locality
question imply acceptance or scheduling (**M18**). **M19** and **M21** are this milestone's
own additions, **M22–M34** OC-R2's, **M35–M37** OC-R2-R1's delivery-ordering family, and
**M38–M39** OC-R2-R3's incumbent-state pair.

---

## 12. The pre-existing assertions changed

Both were found by the suites rather than anticipated, and both are proxies whose subject
still holds. Neither is a weakening: in each case the thing the assertion exists to protect
is still true and still asserted.

### 12.1 The R1 module's table-absence assertion

`test_w1f_w01_r1_evidence_admission.py::TestR1E_NotR2::test_e01_no_outstanding_question_is_persisted`
asserted that `whatsapp_outstanding_questions` was **absent from the model metadata**. That
stopped being the invariant the moment R2 added the table.

The invariant it was really protecting is unchanged and is now asserted directly: with
`OUTSTANDING_QUESTION_ENABLED` off — which is how the whole R1 module runs — admission
records no question and writes no row. R1 on its own still does not know what it asked. This
is the inversion R1's own closeout predicted ("when R2 lands, this assertion inverts"), and
`test_e02` — R1 alone still sends the qualification greeting — is left exactly as it was and
still passes.

### 12.2 The single-Alembic-head pin

`test_convergence_p4_r1.py::P4R1_DeclaredSchema::test_p4r1u_12_one_head_and_the_chain` pins
the single migration head and then asserts the whole chain below it. R2 adds a head, so it
failed — **with the flag OFF**, which is how the full-suite comparison caught it.

It is updated in the file's own documented idiom, which is itself an adaptation ladder: the
comment in place recorded "OLD: the single head was 20261016_notification_sent_at. NEW: one
additive revision, 20261022_operator_notifications, sits on it; the chain below it is
asserted unchanged." R2's revision now sits on *that*, the previous links are still asserted
one by one, and the chain below is untouched — which is what the test is actually for.

### 12.4 OC-R2 CORRECTION 2's consequence for the R1 suite

The interlock changes R1's committed flag semantics, so the R1 module changed with it. This
is the largest set of edits to a committed test file in the programme so far and it should be
read as such rather than buried:

* **the engine builder arms the interlock as one switch.** `_turn(admission=True)` now sets
  `outstanding_question_enabled` too (R3 was already armed by the harness's `safety`). Under
  the interlock, "admission armed" *means* the integrated posture;
* **`test_e01` moved for the second time.** It began as "the questions table does not
  exist", became "R1 with R2 off writes no question", and is now **"R1 alone is inert"** —
  no admission, no evidence row, no question, and the committed pre-R1 turn. Each move
  followed a real change in what the invariant was;
* **`test_e02` was renamed and re-scoped rather than simply inverted.** R1's closeout
  predicted it would invert when R2 landed. It did not invert bluntly: the greeting is
  suppressed only when the turn answered a *delivered* question, and `en flores` with no
  question outstanding is an unprompted fragment that still reaches step 9. The test now
  says that, and the suppression case lives in the R2 suite where a delivered question
  exists;
* **`test_b04`, `test_b06` and `test_g08` assert a STRONGER outcome.** They used to pin
  `INFERRED_NOT_EXPLICIT` and `STALE_TURN` refusal codes on bursts like `en flore` and
  `ya te lo dije`. Under the integrated posture the predicate declines to consult the
  interpreter at all on those bursts, so there is no claim to refuse — the assertion is now
  "no record, nothing written, and the wait was skipped". The boundary rules themselves are
  still proven directly by `TestR1A_Boundary`;
* **`test_g01`'s and `test_g06`'s comparison bases moved**, because the "R1 only" posture
  they compared against is now inert. `g01` states R1-PL-01 absolutely (neither the
  committed nor the integrated posture waits on `buenas`) and `g06` reproduces the La Boca
  defect by arming admission directly, which is the only way left to show what the interlock
  protects.

**One thing the predicate's widening restored.** Making the interlock mandatory meant every
locality-free burst stopped reaching the boundary — including `en cordoba` and `en rosario`,
real places the system must refuse. That would have cost G-11 visibility: the coverage
response still goes out, but "we were told Córdoba and refused it" would leave no record. So
the predicate's resolver is widened by `locality_text.is_outside_coverage`, R3's own DB-free
coverage list, already used by `_locality_verdict`. A coverage-listed city counts as
something to decide, and the refusal is recorded. That is a different certified rule, not a
second copy of the catalogue one.

### 12.5 OC-R2-R1 changed no further certified assertion — and one R2 fixture was lying

**No pre-existing assertion moved for the ordering correction.** `test_a04` — supersession is
applied at activation, not acceptance — still passes exactly as written; the ordering made it
*more* true, not different. The §12.1–§12.4 list is therefore unchanged by this revision.

**One of this milestone's own fixtures was wrong, and it was hiding engine behaviour.**
`_uniquify_wamids` rewrites the harness's `fake-text-1` WAMIDs so that one thread's delivery
callback cannot activate another thread's question (§11's fixture note). It rewrote
`wa_message_id` and **not** `identity` — and the identity is *derived from* the WAMID. So a
second turn that asked the same field recomputed `uuid5(…, 'fake-text-1')`, matched the first
turn's stored identity, and was treated as the same ask: **the fixture, not the engine, was
deciding that a re-ask produces no second row.** `n10` and `n11` failed on it immediately and
the fixture now rewrites both. The repair is where the collision is created, and the
committed harness is still untouched.

### 12.3 And one that was NOT changed, deliberately

`test_l4_7c_2_vehicle_location_reconciliation.py::TestScopeAndJustification::test_vl_10_pre_candidate_buffer_preserved`
is C2's evidence that `state.home_zone_*` was not repurposed, and it proves it by pinning the
literal source line `state.home_zone_group = db_zone.zone_group`. The W1FRV-04 work factored
that expression into a temporary and broke the assertion — **again with the flag OFF** —
while its subject still held: the buffer is still the buffer.

Rather than edit a third certified test for a refactor it was never about, the flag-OFF
branch of the location form now carries the committed expressions **verbatim** and the
assertion passes untouched. A source-literal break is a legitimate signal that something
moved, and the right response was to move it back.

## 13. Baseline identity comparison

Baseline captured on a **pristine `git archive a6aabece`** export, in the same image.
Identities are compared by **name**, with the filter `^(FAILED|ERROR|SUBFAILED)` and the
` - ` detail stripped — the corrected filter from R1 revision 2, where the previous arithmetic
had dropped the suite's single `SUBFAILED` line.

| tree | failed | passed | skipped | errors | subtests | identity lines |
|---|---|---|---|---|---|---|
| pristine `a6aabece` (baseline) | 62 | 6 812 | 646 | 9 | 6 889 | **71** |
| this tree, flags **OFF** (default) | 62 | 6 950 | 656 | 9 | 7 087 | **71** |
| this tree, integrated posture (R1+R2+R3 **ON**) | 62 | 6 950 | 656 | 9 | 7 087 | **71** |

```
NEW  (in candidate, not in baseline) = []
GONE (in baseline, not in candidate) = []
diff(ids_flag_off, ids_flag_on)      = empty
```

**No failure or error identity was introduced, none disappeared, and no signature changed, in
either posture.** The two candidate sets are equal to each other line for line, and the
focused set is equal across postures too (§10.1).

The deltas are this milestone's own tests and nothing else: **+138 passed**, **+10 skipped**
(exactly the PG-gated migration group, which skips without `R2_PG_URL` — now **ten** tests,
`k03b`/`k03c` and `k06a`/`k06b`/`k06c` among them) and **+198 subtests**. Re-measured for
each revision: +93 / +106 at revision 1, +115 / +181 after OC-R2, +130 / +186 after
OC-R2-R1, and **+138 / +198** now. **OC-R2-R3's own contribution is exactly +8 passed
(`TestR2P_Incumbent`), +0 skipped and +12 subtests** — and that is the arithmetic form of
"no code changed": a correction that only states a contract adds tests and moves nothing
else. OC-R2-R1's was +15 passed, +3 skipped, +5 subtests.

**The 62-failure baseline was not taken on trust.** It was captured in this run, on a
pristine `git archive a6aabece`, in the same image — and it reproduced R1's own committed
numbers exactly (62 / 6 812 / 646 / 9 / 6 889, 71 identities), which is the consistency check
that the baseline is the right tree.

**Two identities moved during the work and both were investigated rather than normalised**,
which is the whole point of comparing names:
* the full-suite OFF run first came back at **63** failures. The new identity was
  `test_convergence_p4_r1::test_p4r1u_12_one_head_and_the_chain` — the single-Alembic-head
  pin, which R2 legitimately moves. It is updated in the file's own idiom (§12.2), not
  normalised into the baseline;
* the focused run first came back at **17**. The extra identity was
  `test_vl_10_pre_candidate_buffer_preserved`, a C2 source-literal assertion my W1FRV-04
  refactor broke while its subject still held. The code was changed back to carry the
  literal, so no third certified test was edited (§12.3).

Neither was a count that happened to match something; each was a named identity that
appeared, was traced to its cause, and was resolved at the cause.

---

## 14. Rollback — the truth table, and the artifact it requires

**OC-R2 CORRECTION 3. The previous version of this section asserted two incompatible
things** and the owner is right that they cannot both hold:

* "a code rollback does not need a schema rollback"; and
* "`EXPECTED_HEAD` is bumped, so the old image refuses to start against the migrated
  database".

The second makes the first false for an **image** rollback. What the first statement was
actually true of is a **behavioural** rollback — turning the flags off on the *new* image —
and conflating the two would have left the operator believing a recovery path existed that
does not. Corrected:

| scenario | available? | what actually happens |
|---|---|---|
| **Behavioural rollback** — flags OFF on the NEW integrated image | **always** | the committed behaviour returns on the same image; both tables become unread; no restart ordering, no data loss. This is the rollback the flags buy, and §13 proves it is identity-clean |
| **Image rollback to `a6aabece`, before the migration runs** | yes | nothing has changed; the old image starts normally |
| **Image rollback to `a6aabece`, after the migration but before any row exists** | yes, in **two** steps | `alembic downgrade -1` first (it drops the empty tables and both candidate columns, rehearsed in §10.2), **then** start the old image. The old image's `EXPECTED_HEAD` is `20261022_operator_notifications`, so it cannot start until the head is back |
| **Image rollback to `a6aabece`, after either new table holds a row** | **NO** | the downgrade REFUSES (by design — those rows are the record of what the customer was told), so the head stays at `20261027`, and the old image's preflight refuses to start against it. **There is no direct path back to the `a6aabece` image once the integrated behaviour has been used.** |
| **Schema rollback with rows, deliberately** | only with an archive step | export the rows, delete them, then downgrade. Not a routine operation and not scripted here |

**So the honest statement is the one the owner asked for: direct rollback to the existing
`a6aabece` image is unavailable after first use.** Flags OFF is a behavioural rollback, not
an application-image rollback, and the two must not be offered as if they were the same
thing in a recovery window.

### 14.1 The compatibility rollback image — a BLOCKING requirement of the next milestone

The gap above has one clean remedy and it is not more flags: an image that is behaviourally
the accepted pre-R2 system **and** able to start against the additive `20261027` schema. It
is recorded here as a **blocking** prerequisite of
`PRELAUNCH-W1F-W01-INTEGRATED-CANDIDATE-BUILD-AND-MIGRATION`, with these properties:

1. built from the accepted pre-R2 behaviour (`a6aabece`), with `EXPECTED_HEAD` advanced to
   `20261027_w1f_conversation_memory` and **nothing else changed**;
2. starts safely against the migrated schema — the preflight passes because the head
   matches, and the two new tables are not in its model metadata at all;
3. **never reads or mutates** `whatsapp_outstanding_questions` or
   `whatsapp_thread_evidence`, and never writes the two candidate columns. Since that code
   does not exist in `a6aabece`, this is a property of the tree rather than a discipline —
   and it should be asserted, not assumed;
4. every new behaviour flag OFF, which for that tree means absent;
5. **independently certified before the live migration window**, not during it.

**It is not built here.** The correction says so explicitly and this milestone has no
authorisation to build an image; §18 carries it as an owner decision instead. Until it
exists, the operative recovery path during the migration window is the behavioural rollback
in row 1 of the table above, and the runbook language should say exactly that.

---

## 15. What closes W1F-W-01, and what does not

**Closed by R1+R2+R3 together, measured at CE level:**

| corpus case | state |
|---|---|
| `W1F-C-01` … `C-04` — the location question answered by `Flores` / `en Flores` / `está en Flores` / `en flores` | admitted, persisted, the question closed, the greeting suppressed, `must_not_ask` honoured |
| `W1F-C-05`, `C-07` — locality before the vehicle | the candidate created afterwards carries it, with `zone_evidence_id` |
| `W1F-C-11` — the question survives one off-topic turn | `a13`, `b08` |
| `W1F-C-12` — the wrong-slot answer | the locality is kept and the vehicle question stays open |
| `W1F-C-22` — prior-cycle evidence | not bindable, and a prior-cycle question does not close |
| `W1F-C-23` — the Flow form answer | reaches the candidate through the reconciler as HUMAN_CONFIRMED (W1FRV-04) |
| `W1F-C-24` — repeated deliveries of one answer | one admission, one close; `d10` for a true replay, `d11` for three distinct turns |
| `W1F-C-14` … `C-19` | R3, already committed as `83f45b2` |

**NOT closed, and R2 does not claim otherwise:**

1. **W1F-W-01 remains OPEN.** Every measurement here is a sealed-harness measurement. No
   conversation was run, no flag changed, no image built. The gate closes on a runtime
   checkpoint of the integrated candidate, which is the owner's to authorise.
2. **`buenas` is closed; "no locality-free turn ever waits" is not** (§9.3). Turns with
   inherited readers of the interpretation still wait, and removing those is not R2's.
3. **`LAYER_D_REGISTER_AND_CONTINUE_ENABLED` is left OFF.** The review's R2 plan item 5 asks
   for a decision on it "on the evidence of R1+R2". The evidence here is that the Wild's
   three turns pass with it off, so arming it would be a change with no measured need and its
   own regression surface (`W1F-C-21` is the floor it would have to hold). Recorded as an
   owner decision rather than taken.
4. **`VEHICLE_IDENTITY` questions are recordable but nothing asks one yet.** The kind, its
   `expected_slots` and its matching are implemented and tested (`a05`, `a06`, `d07`), and
   the recorder currently derives only the locality kind from the dispatch boundary. A
   vehicle question is asked today through the Flow attempt store, which has its own
   one-attempt contract; wiring it in would entangle the two, which §8.1 explicitly warns
   against. Left for the Flow-side milestone.
5. **The skipped semantic wait leaves no evidence row**, because no claim was produced to
   decide about. The decision log carries `W1F-R2 semantic wait skipped`; that is the only
   record, and it is not queryable like the evidence rows are.
6. **G-11 is closed for the locality family only** — the one family admission admits.
7. **The gate chain still reads raw customer text** (review §8.5, roadmap C6). Out of scope
   by the review's own non-goals: "routing stays; it is fed differently".
8. **A re-ask whose delivery never confirms leaves the field unasked — but only when there
   was no active incumbent** (§4.1, §4.2). Corrected at OC-R2-R3: the ordering rule governs
   which row may newly ACTIVATE, not which row stays active. If the first ask had already
   been delivered, it keeps the field through a pending or failed re-ask (§4.2 rows A–C). If
   it had not — it was still `PENDING_DELIVERY` when the re-ask was accepted, so it was
   retired as obsolete — then nothing is active, the greeting is not suppressed, and the next
   turn asks again (row E). That narrower case is the fail-closed direction and is
   deliberate; the universal version of this claim was wrong and is withdrawn.

---

## 16. Isolation — nothing live was touched

| claim | evidence |
|---|---|
| no commit, no push, no tag, no stash | HEAD is still `a6aabece08afbb8c508f5417e5bd3698b29d1cf3` and `origin/integration/prelaunch-booking-convergence` is the same commit (verified before editing, as the prompt requires); `git diff --cached` is empty; `git stash list` is empty |
| the dirty worktree is preserved | the only tracked modification outside this milestone is `docs/operations/RECOVERY_MANIFEST.md`, **unstaged and untouched** — still 20 421 bytes dated 2026-10-23 19:02. The 52 unrelated untracked documents are untouched |
| the running backend never saw this code | container `b340087e4cee`, image `ridecheck-crm-backend:prewild-6a14cdb`, **restart count 0**, started `2026-10-26T15:00:30Z` — before R3, let alone R1 or R2. The worktree is not mounted into it |
| flags unchanged | `OUTSTANDING_QUESTION_ENABLED`, `EVIDENCE_ADMISSION_ENABLED` and `LOCALITY_RESOLUTION_SAFETY_ENABLED` appear **0 times** in the container's environment |
| no live database migrated | `crm_test` read under `default_transaction_read_only=on` before and after **every** revision including this one: messages **52** (max id **6239**), traces **12**, candidates **1**, zones **211**, ai_events **7**, security_events **734**, `alembic_version = 20261022_operator_notifications` — every counter identical, and the version is still the PARENT of this revision. `pg_tables` contains **0** of the two new tables |
| the migration ran only on a disposable server | `r2-rehearsal-pg`, `postgres:16` (server version **16.13**, the production version), its own docker network `r2net`, database `r2_rehearsal`, created for this rehearsal and disposed by **exact name** afterwards — `docker stop` + `docker rm` + `docker network rm`, never a host-scope prune. Re-created and re-disposed for OC-R2-R1; 0 containers and 0 networks matching remain |
| no provider contact | every sealed test container ran `--network none`. The two exceptions are stated rather than glossed: the PostgreSQL group and the migration rehearsal run on the private docker network `r2net`, whose only other member is the disposable server — a bridge network does have outbound NAT, so for those runs the guarantee rests on the code and not on the sandbox, and what they execute is the migration, raw SQL and `outstanding_question`, none of which has a provider call path. 0 Meta, 0 OpenAI, 0 SMTP, 0 Resend, 0 n8n calls. `_turn_semantic_calls == 0` on every measured turn; in the PASS-B probe the interpreter invoked is a local fake class with no client, no HTTP and no key — `SemanticTurnInterpreter` is never constructed |
| no outbound message | `OutboundSafetyGate` is a mock in every test and `i05` asserts **0** `whatsapp_messages` rows with `direction='out'` in any tree |
| no image, no deploy | no `docker build`, no `docker compose`; the only non-ephemeral container created is the disposable PostgreSQL above |

---

## 17. Proposed commit — NOT executed

```
git add backend/app/models.py \
        backend/app/settings.py \
        backend/app/services/outstanding_question.py \
        backend/app/services/thread_evidence.py \
        backend/app/services/conversation_engine.py \
        backend/app/services/claim_projection.py \
        backend/app/services/schema_preflight.py \
        backend/app/services/tester_reset.py \
        backend/migrations/versions/20261027_w1f_conversation_memory.py \
        tests/test_w1f_w01_r2_outstanding_question.py \
        tests/test_w1f_w01_r1_evidence_admission.py \
        tests/test_convergence_p4_r1.py \
        backend/app/routes/whatsapp.py \
        docs/Launch/2026-10-27_RIDECHECK_CRM_PRELAUNCH-W1F-W01-R2-OUTSTANDING-QUESTION_CLOSEOUT_001.md
```

Fourteen paths — `backend/app/routes/whatsapp.py` joined the set with OC-R2's
activation hook. `docs/operations/RECOVERY_MANIFEST.md` and the 52 unrelated untracked
documents are **not** in that list.

**No scope change at OC-R2-R1 or OC-R2-R3, and none was needed.** The ordering rule and the
incumbent contract are both in `backend/app/services/outstanding_question.py`, which is
already path 3; their tests are in `tests/test_w1f_w01_r2_outstanding_question.py`, already
path 10; the two PostgreSQL race groups are in that same module because they belong to the
contract they test, not to a new file. **OC-R2-R3 changed no code at all** — the
implementation already satisfied the rule, so what changed is one module docstring, two
function docstrings, this document, and eight new tests. Fifteen paths would have meant a
new R2-owned test file, and splitting one contract's tests across two files is how a reader
comes to miss half of it.

```
feat(prelaunch): W1F-W-01 R2 outstanding question contract
```

---

## 18. Owner decisions required

**Recorded as already made by OC-R2 and ratified by OC-R2-R1** — the all-three-flags
interlock is accepted even though it changes R1's earlier standalone flag semantics; R3 alone
remains independently usable while R1 and R2 are inert unless all three flags are ON; the
six-state delivery vocabulary is accepted, subject to the ordering correction now made; the
additive migration may be committed while the `crm_test` migration waits for a fresh OC-0
window; the downgrade REFUSES while either table holds rows and no destructive downgrade is
implemented; the two certified assertion changes are accepted;
`LAYER_D_REGISTER_AND_CONTINUE_ENABLED` stays OFF; R1-PL-01 is accepted narrowly — no new
admission wait on `buenas`, `hola` and the locality-free FAQ, inherited certified readers
remain (§9.3); the compatibility rollback image built from `a6aabece` with only
`EXPECTED_HEAD` advanced is BLOCKING before live migration; all runtime flags remain OFF;
W1F-W-01 remains OPEN until integrated runtime and natural-Wild proof.

Item 4 below is therefore **closed by ratification** and is kept only because §9.4 and §12.4
are read against it.

What is still needed:

1. **Authorise the commit** — the **fourteen** paths and the subject in §17. OC-R2-R1
   required no fifteenth: the ordering correction lives in files already in the set, and its
   tests are in the R2-owned test module.
2. **The compatibility rollback image (§14.1) as a BLOCKING prerequisite** of
   `PRELAUNCH-W1F-W01-INTEGRATED-CANDIDATE-BUILD-AND-MIGRATION`. Not built here, as the
   correction instructs. Confirm it is blocking, and that it is built from `a6aabece` with
   `EXPECTED_HEAD` advanced and nothing else changed.
3. **The OC-0 migration window.** `EXPECTED_HEAD` is bumped, so the integrated image cannot
   start until `20261027_w1f_conversation_memory` has run against `crm_test`. The last
   window expired 2026-10-21.
4. **RATIFIED (OC-R2-R1) — the interlock's cost to R1's committed contract** (§9.4, §12.4).
   `EVIDENCE_ADMISSION_ENABLED` alone now does nothing, and six R1 assertions moved with it.
   Recorded here because §9.2's corrected wording depends on it: there is no supported
   posture in which R1 is active alone.
5. **The delivery-state vocabulary** (§3, §4): six states, two new columns
   (`delivery_status`, `activated_at`), one new index (`ix_outstanding_question_wamid`).
   The migration is not applied anywhere, so this was edited in place rather than chained.
6. **One residual risk I did not design away, named rather than hidden.** If Meta never
   sends a `delivered`/`read` callback for a question — the status is lost, the webhook is
   down, or the message genuinely never arrives — the question stays `PENDING_DELIVERY`
   forever and the greeting is never suppressed. That is the *safe* direction and it is
   deliberate: the system behaves exactly as the committed one does. But it means the
   contract's benefit depends on a callback this milestone cannot guarantee, and the
   measured 43-of-45 `read` rate on `crm_test` is evidence rather than a promise. A
   runtime checkpoint should measure the activation rate before the flag goes ON.
7. **The ordering rule's deliberate narrowing, correctly scoped** (§4.1, §4.2, §15 item 8).
   Only the newest **eligible** row may newly **activate**; an incumbent stays active until a
   newer ask genuinely activates. So the two residual risks **do not always compound**, and
   the previous revision's claim that they do was wrong:

   | situation | effect of a lost delivery callback |
   |---|---|
   | **no active incumbent** (first ask still pending when the re-ask was accepted) | the field is inactive until the next turn asks again — the real exposure, item 6 |
   | **an active incumbent exists** | **no inactive window at all.** A pending or failed re-ask changes nothing; the incumbent is answerable throughout (§4.2 A–C, `p01`–`p03`) |

   The runtime checkpoint should therefore measure **two** things separately — the activation
   rate of accepted questions, and how often a re-ask is issued while an incumbent is live —
   rather than one compounded risk. Only the first row above is an exposure.

NEXT: PRELAUNCH-W1F-W01-INTEGRATED-CANDIDATE-BUILD-AND-MIGRATION — OWNER AUTHORIZATION REQUIRED

STOP.
