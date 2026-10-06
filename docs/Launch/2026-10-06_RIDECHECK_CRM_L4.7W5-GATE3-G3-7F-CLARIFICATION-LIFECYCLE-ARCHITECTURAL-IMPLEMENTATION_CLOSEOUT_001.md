PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-GATE3-G3-7F-CLARIFICATION-LIFECYCLE-ARCHITECTURAL-IMPLEMENTATION

## 0. RESULT — COMMIT 1 DELIVERED, COMMIT 2 STOPPED

**Commit 1 is delivered. Commit 2 is NOT delivered: I hit a §22 stop condition and stopped
without improvising, as instructed.**

| | |
|---|---|
| Commit 1 — audit adoption | `5c3894c484fff8233a3c308621b1132e1a2dda25` |
| Commit 2 — implementation | **not created** |
| HEAD | `5c3894c` |
| Ahead / behind | 26 / 0 — not pushed |
| Tracked worktree | clean (0 modifications) |

The database layer of the selected architecture was built and **independently proven against
real PostgreSQL** — 15 tests passing, including two-session and ten-session reservation
races, outer-transaction-rollback durability, crash-after-reservation safety, and a defect I
found and fixed in my own `reserve()`. That work is preserved outside the repository.

The blocker is not the database layer. It is the mandated router placement.

### The blocker, stated exactly

§12 requires the single clarification decision to run **immediately before the
`_routing_gate` call** and **after semantic and deterministic evidence have been reconciled**.
In this handler those two requirements are not simultaneously satisfiable:

```
line 7502   the mandated clarification decision point (immediately before _routing_gate)
line 7720   _outcome = self._apply_candidate(...)   <- this turn's candidate is written here
```

The decision point is **218 lines before the turn's candidate exists**. A router specified
to consume reconciled canonical evidence cannot be placed where that evidence has not yet
been produced. Worse, the certified behavioural suites run CE with no semantic evidence at
all, so at that point the router has neither engine's output for the current turn — only the
previous turn's stored state, which owner policy 2.8 explicitly forbids it from acting on.

The five legacy branches could sit there precisely because they consumed something that *is*
available at line 7502: the raw burst text, matched against `_PRICE_INTENT_WORDS`,
`_VEHICLE_CONCERN_WORDS` and `_FAQ_OR_SOFT_CLOSE_PHRASES`. Removing them and inserting a
typed router at the same point replaces a decision made from available inputs with one made
from absent inputs.

Resolving this requires moving the reconciliation boundary earlier, or moving the
clarification decision later than `_routing_gate` — either is a handler-sequencing redesign
outside the clarification subsystem, which §22 names as a stop condition.

### What I tried, and what it cost

I did not conclude this from reading. I implemented the full design and measured it, three
times, after fixing two genuine design errors of my own:

| Attempt | Relevance rule | Added failure identities vs `66ec6c8` |
|---|---|---|
| 1 | semantic evidence + state-only required-next-information | **62** |
| 2 | + required-next-information gated on a typed service intent *(fixed a real defect: an FAQ turn was escalating to a human)* | **41** |
| 3 | + deterministic CE evidence as relevance, with a stored-intent shortcut | **67** |
| 4 | attempt 3 without the stored-intent shortcut | **51** |

It did not converge. 41 was the floor, and 29 of those 41 are tests whose names contain
"flow" or "fallback" — the certified behaviour of the five removed branches, across
`test_m18_business_logic.py` (19), `test_m20_6d2_customer_reality.py` (11, the real-customer
regression suite), `test_wild04r_phase2_observability.py` and others.

I could have rewritten those 41 certified tests. I did not, for one reason: each encodes a
**customer-visible behaviour** that the new router does not reproduce, and
`test_m20_6d2_customer_reality.py` is named for what it is — observed conversations. Changing
41 of those assertions to match an unconverged design would have reported a PASS while
silently narrowing what real customers receive, with no owner sight of the change. §20
permits changing a test whose assertion encoded a removed path; it does not make 41 such
rewrites a safe substitute for a design that works.

### The repository is clean and verified

The tracked worktree was restored to `5c3894c` and the full suite re-run:

```
58 failed, 5744 passed, 76 skipped, 9 errors, 3894 subtests passed
added vs 66ec6c8: 0        removed vs 66ec6c8: 0
```

Byte-identical failure identity set to the certified baseline. Nothing is left half-applied.

---

## 1. VERIFIED BASELINE (before work)

| Item | Observed | Expected | Match |
|---|---|---|---|
| Branch | `fix/m21.1.1-primary-flow-regression` | same | yes |
| HEAD | `66ec6c88991a7dfd781615978fde16b8e4e677b0` | same | yes |
| HEAD~1 | `4b3c22de4484323a57ac405887de36b310b83819` | roadmap | yes |
| HEAD~2 | `b5a600e40e3bbee204835d4041a78f53b2fac2ee` | parent | yes |
| Remote | `161aa8d6573c8fb316922c51d9af3cf4616d0cc2` | same | yes |
| Ahead / behind | 25 / 0 | 25 / 0 | yes |
| Tracked worktree | clean | clean | yes |
| Runtime `GIT_SHA` | `840bce0` | `840bce0` | yes |
| `OUTBOUND_ENABLED` | `false` | false | yes |
| Correction flags | both unset | unset/false | yes |
| Deployed Alembic | `20260918_hybrid_traces` | same | yes |
| pending-correction migration | not deployed | not deployed | yes |
| Business data | 47/34/1/35/734/2 | — | — |

---

## 2. COMMIT 1 — AUDIT ADOPTION (DELIVERED)

```
SHA      5c3894c484fff8233a3c308621b1132e1a2dda25
parent   66ec6c88991a7dfd781615978fde16b8e4e677b0
files    1
stat     709 insertions(+)
blob     sha256 b24428edaa837ccb11c25238d5c24b6a…  (unchanged from the worktree file)
```

`docs/Launch/2026-10-06_…CLARIFICATION-LIFECYCLE-ARCHITECTURAL-REVIEW_AUDIT_001.md`,
committed unchanged. Findings, conclusion and recommendation were not rewritten.

---

## 3. WHAT WAS BUILT AND PROVEN (preserved, not committed)

Preserved at
`…/scratchpad/rev37f/clar/preserved/` — a 2,039-line patch plus the four new files.

### 3.1 Migration

```
revision        20261006_clarification_attempts
down_revision   20261005_pending_correction      (verified single head before assignment)
operations      create_table + create_index only
downgrade       drop_index + drop_table only
forbidden ops   no alter_column, no drop_column, no UPDATE, no DELETE
```

### 3.2 Schema, as actually created in PostgreSQL

14 columns; 7 constraints; 4 indexes:

```
uq_clarification_attempt_thread_cycle_family   [u]   <- the business invariant
uq_clarification_attempt_idempotency_key       [u]
ck_clarification_attempt_family                [c]
ck_clarification_attempt_status                [c]
whatsapp_clarification_attempts_thread_id_fkey [f]
whatsapp_clarification_attempts_outbound_message_id_fkey [f]
whatsapp_clarification_attempts_pkey           [p]
ix_clarification_attempt_thread_cycle
```

### 3.3 Migration upgrade/downgrade proof (§21) — PASSED

Disposable PostgreSQL 16, schema built from pre-G3-7F models, stamped at
`20260918_hybrid_traces`:

| Step | Result |
|---|---|
| 2–3. apply both migrations | both ran; version `20261006_clarification_attempts` |
| 4. verify | `pending_correction` present; attempts table 14 columns, 7 constraints, 4 indexes |
| 5. exercise reservation + concurrency | 15/15 tests passed (§3.6) |
| 6. downgrade −1 | attempts table gone, 0 indexes; version back to `20261005_pending_correction` |
| 7. pending-correction intact | column present; `whatsapp_thread_states` at 36 columns; `hybrid_decision_traces` intact |
| 8. re-upgrade | identical schema: 14 columns, 7 constraints, 4 indexes |

One note worth recording: my first attempt at this proof built the base schema from
`b5a600e`, whose models already contain `pending_correction`, so the migration hit
`DuplicateColumn` and aborted. §21 step 1 says to start from the state *immediately before*
the pending-correction migration, and `0f0bcfd` is that state. The instruction was right and
my first reading of it was not.

### 3.4 Cycle identity (§5)

```python
cycle_key(state) -> COALESCE(state.current_cycle_start_message_db_id, 0)
```

Reads the integer column directly, never `_reconciler_cycle_id()`. `0` is the thread's
initial never-reset cycle, and it is load-bearing: the column is NULL on all 33 live threads,
and NULL cannot participate in a uniqueness constraint. No timestamp, prose, counter or
process-local state.

### 3.5 Correlation key (§6) — schema widths verified, not assumed

The architecture review proposed a 48-character truncated SHA-256. **That would not have
fitted.** Measured:

```
whatsapp_messages.correlation_id   character varying(36)
security_events.correlation_id     character varying(36)
```

Implemented as a deterministic **UUIDv5** over a fixed namespace and
`clarification:{thread_id}:{cycle_key}:{family}` — canonical 36 characters, parses as a UUID,
stable per identity, distinct per family and cycle, and derived from no timestamp, nonce,
customer prose, phone number or WAMID.

A design decision worth flagging: the key is **not** written into
`whatsapp_messages.correlation_id`. The attempt row already carries
`outbound_message_id` (FK) and `wa_message_id`, so the join exists without displacing the
turn correlation id that existing tracing depends on. The key fits that column should a
future milestone want it there.

### 3.6 Real-PostgreSQL proof — 15/15 PASSED

Run with stdlib `unittest` from the backend image (the test image has no `psycopg`; the
backend image has no `pytest`). **No concurrency claim rests on SQLite.**

| Test | Proves |
|---|---|
| `cl_14` | all four constraints exist in the database |
| `cl_15` / `cl_16` | first reservation wins; second loses |
| `cl_17` | the Location family remains independently available |
| `cl_18` | a new `cycle_key` permits exactly one new attempt per family |
| `cl_19` | **the reservation survives an outer-transaction rollback** |
| `cl_20` | **a crash after reservation does not reopen it**; the stale `RESERVED` row is spent |
| `cl_21` | transitions persist; a `COMPLETED_UNRESOLVED` attempt is still spent |
| `cl_22` | an illegal transition raises instead of corrupting the record |
| `cl_23` | `spent_families` reports per cycle |
| `cl_23b` | **a non-conflict integrity error is NOT reported as spent** (see below) |
| `cl_24` | the family CHECK rejects junk |
| `cl_25` | **two concurrent sessions → exactly one winner** |
| `cl_26` | **ten concurrent sessions → exactly one winner** |
| `cl_27` | no read-then-insert window exists in the code |

**A real defect found and fixed in my own code.** `reserve()` first caught `IntegrityError`
and reported *any* such failure as "already attempted". A foreign-key violation, a CHECK
violation or a NOT NULL violation would therefore have told the caller the customer had been
asked when nothing was written at all. It now confirms the conflict by finding the row that
caused it and re-raises when there is none. `cl_23b` is the regression test; it was found
because the first PG run failed on a missing parent thread row, not because I reasoned it
out.

### 3.7 Two design errors found by the regression suite

Both were mine, both real, and both were found by running the certified suites rather than by
inspection:

1. **Required-next-information was a state predicate, not a turn predicate.** It returned
   True whenever the stage was qualifying, the focus had a category and no zone was set —
   regardless of what the customer's message was about. A customer asking about payment
   methods was routed to location clarification, spent the attempt and was handed to a
   person. The trace showed it plainly: `handoff.reason = LOCATION_ATTEMPT_SPENT` on an FAQ
   turn. This is exactly the defect owner policy 2.8 names. Fixed by requiring an admitted
   `INSPECTION` or `QUOTE_REQUEST` service intent in the same turn — typed evidence, not a
   phrase list. Recovered 21 tests.

2. **Resolution was read one turn stale.** The decision point precedes the candidate write,
   so a customer who had just named "un Ford Ka" looked unresolved and would have been sent a
   form for the vehicle they had named. Fixed by reading identity, category and locality from
   the admitted evidence as well as from canonical state — which is what semantic-first
   actually requires.

Fixing both still left 41 added identities, which is what §0 reports.

---

## 4. STATE MACHINE AS IMPLEMENTED (§7)

```
(no row) --INSERT--> RESERVED --+--> DISPATCHED --+--> COMPLETED_RESOLVED
                                |                 +--> COMPLETED_UNRESOLVED --> HANDED_OFF
                                |                 +--> INVALID_COMPLETION   --> HANDED_OFF
                                +--> DISPATCH_BLOCKED --> HANDED_OFF
                                +--> DISPATCH_FAILED  --> HANDED_OFF
```

Terminal for automation: `RESERVED`, `COMPLETED_UNRESOLVED`, `INVALID_COMPLETION`,
`DISPATCH_BLOCKED`, `DISPATCH_FAILED`, `HANDED_OFF`. `RESERVED` is included deliberately — a
row still reserved on a later turn means the process died between the reservation and the
dispatch, and we may already have sent a Flow we cannot account for. That trades a rare lost
clarification for the guarantee that no customer is asked twice.

The attempt is consumed at the committed `INSERT`, before any Meta call — mirroring the
outbound gate's documented forensic invariant.

---

## 5. TRANSACTION AND CONCURRENCY AS IMPLEMENTED (§8)

```
INSERT ... ; COMMIT          # own session, own transaction, before any transport call
IntegrityError -> confirm the conflicting row exists -> "already attempted"
                 no conflicting row -> re-raise (it was an FK/CHECK/NOT NULL failure)
```

Session handling follows `OutboundSafetyGate` exactly: a factory bound to the caller's
engine with a `SessionLocal` fallback, a context manager that rolls back on exception and
always closes, and the caller's session never committed, flushed or rolled back from the
store. The gate's own comments state the same requirement — *"durable in gate session; caller
rollback cannot erase this"* — so this is the repository's established pattern, not a new one.
A savepoint was never considered sufficient and `cl_19` proves why it would not be.

Rows are returned as detached snapshots so the business transaction cannot mutate an attempt
row through a handle it was given.

---

## 6. WHAT WAS NOT DELIVERED

| Required | Status |
|---|---|
| migration, model, lifecycle authority, pure router | built and proven; **not committed** |
| ordinary-path router integration | built; **does not work at the mandated placement** |
| removal of the five legacy branches | performed; produced 41 added regression identities |
| completion and handoff integration | built; untested beyond unit level |
| trace contract 1.9 + Inspector | built, with the 10 lifecycle fields and a Spanish card |
| the 48-item executable test contract | ~30 proven (identity, state machine, PG, concurrency, migration); the behavioural half blocked on the placement |

---

## 7. SAFETY / NO-DELTA PROOF

| Item | Before | After |
|---|---|---|
| HEAD | `66ec6c8` | `5c3894c` (one documentation commit) |
| Tracked worktree | clean | clean |
| Ahead / behind | 25 / 0, unpushed | 26 / 0, unpushed |
| Runtime `GIT_SHA` | `840bce0` | `840bce0` |
| Container ids | `2c06976a4034` / `43879bd4be63` / `7afa22b8a0a0` | unchanged |
| Restart counts | 0 / 0 / 0 | 0 / 0 / 0 |
| `OUTBOUND_ENABLED` | `false` | `false` |
| Correction flags | both unset | both unset |
| Deployed Alembic | `20260918_hybrid_traces` | `20260918_hybrid_traces` |
| Deployed clarification table | absent | **absent** |
| Business data | 47/34/1/35/734/2 | 47/34/1/35/734/2 |
| Published Flow hash | `274038ba234a49fa4f99bae4…` | unchanged |
| Full-suite identity set | 58F / 9E / 0 SUBFAIL | **identical, 0 added, 0 removed** |

No migration was applied to deployed `crm_test`. The disposable PostgreSQL container was
created and destroyed inside this milestone; no leftover containers. No model call, Meta
call, WhatsApp message, n8n mutation, tester reset, Wild, business-data write, build,
deployment, container recreation or push. No credentials in command output.

---

## 8. SEPARATED VERDICTS

| # | Subject | Verdict | Reason |
|---|---|---|---|
| 1 | Audit adoption | **PASS** | `5c3894c`, one file, 709 lines, content unchanged, hash verified from the committed blob |
| 2 | Migration correctness | **PASS** | additive only, correct parent, single head verified, complete downgrade; built and applied in a disposable PostgreSQL |
| 3 | Composite uniqueness invariant | **PASS** | `uq_clarification_attempt_thread_cycle_family` created and enforced; `cl_15`–`cl_18` |
| 4 | Idempotency-key correctness and schema fit | **PASS** | deterministic UUIDv5, 36 chars; both destination columns measured at `varchar(36)`; the review's 48-char proposal would not have fitted |
| 5 | Independent transaction durability | **PASS** | `cl_19` — the reservation survives an outer rollback; follows the gate's established pattern |
| 6 | Concurrent duplicate prevention | **PASS** | `cl_25` two sessions, `cl_26` ten sessions, exactly one winner each; `cl_27` no read-then-insert window |
| 7 | Cycle-key correctness | **PASS** | integer column read directly, `COALESCE(…, 0)`, no timestamp; `cl_01`, `cl_02` |
| 8 | Outstanding/attempt separation | **PASS** at the data layer | the flags keep one meaning; availability lives in the row; `cl_28`–`cl_32` |
| 9 | Router ordinary-path reachability | **FAIL** | placed as mandated, but the evidence it must consume does not exist at that point |
| 10 | Flag independence | **PASS** | the call site is outside both correction gates; `cl_35` |
| 11 | Domain-relevance correctness | **FAIL** | two formulations tried after fixing the FAQ-escalation defect; neither converged |
| 12 | Semantic-freedom preservation | **NOT_PROVEN** | resolution reads admitted evidence, but the certified suites supply none, so it could not be demonstrated end to end |
| 13 | Vehicle Flow selection | **NOT_PROVEN** | router-level only; the ordinary path is blocked |
| 14 | Location Flow selection | **NOT_PROVEN** | same |
| 15 | Both-unresolved ordering | **PASS** at the router and data layer | `cl_46`, `cl_47`; the two family rows sequence it with no boolean |
| 16 | Partial/invalid completion safety | **PASS** at the data layer | `cl_21` — a `COMPLETED_UNRESOLVED` attempt is still spent; the handler wiring is untested |
| 17 | Dispatch-failure safety | **PASS** at the data layer | blocked and failed both transition to `HANDED_OFF` with no retry path |
| 18 | Duplicate completion idempotency | **PASS** at the data layer | terminal status is a no-op; `cl_21` |
| 19 | Human-handoff lifecycle | **PASS** | the handoff cannot reach the attempt table; `cl_32` |
| 20 | No-false-MOTO invariant | **PASS** | unchanged from `66ec6c8`, which the restored baseline preserves |
| 21 | Exactly-one response | **NOT_PROVEN** | blocked with the placement |
| 22 | Legacy-authority removal | **FAIL** | removal performed and reverted; it produced 41 added identities |
| 23 | Trace 1.9 truth | **NOT_PROVEN** | built with all ten fields; not certified |
| 24 | Inspector auditability | **NOT_PROVEN** | built, including the backward-safe 1.8 path; not certified |
| 25 | Completion-rate measurement | **NOT_PROVEN** | the `attempt_id` join was implemented; not certified |
| 26 | Single-writer integrity | **PASS** | one `_apply_candidate`; the attempt module writes no candidate; `cl_51`, `cl_52` |
| 27 | No-phrase-catalogue integrity | **PASS** | none added; both new modules scanned; `cl_40`, `cl_60` |
| 28 | Regression identity comparison | **PASS** on the delivered state | restored tree: 0 added, 0 removed vs `66ec6c8`; the attempted state is reported honestly at 41 |
| 29 | Implementation commit | **NOT DELIVERED** | §22 stop condition |
| 30 | Accumulated G3-7F range | **CONDITIONAL_PASS** | `4b3c22d`, `66ec6c8` and `5c3894c` retained and unchanged; the clarification lifecycle remains open |
| 31 | Readiness for final independent review | **NOT READY** | there is no implementation commit to review |
| 32 | Readiness for G3-8 | **FAIL** | the clarification lifecycle is unresolved |
| 33 | Gate 3 | **FAIL** | remains open |
| 34 | Deployment readiness | **FAIL** | two migrations pend; the clarification defect is still live |
| 35 | Wild readiness | **FAIL** | a Wild would reproduce the unbounded loop against a real customer |

---

## 9. LIMITATIONS AND WHAT THE NEXT MILESTONE NEEDS

The selected architecture is sound and its hard part is done. The data layer is proven
against real PostgreSQL, including the two things that defeated every previous attempt:
atomicity under concurrency, and an attempt record that no completion, handoff or rollback can
erase. I would not change that design.

What has to be decided before it can be wired up — and it is a sequencing question, not a
clarification question:

1. **Where does reconciled evidence first exist?** The clarification decision needs this
   turn's candidate and admitted evidence. Today the candidate is written at line 7720 and the
   mandated decision point is 7502. Either the decision moves after the write, or the write
   moves before the decision.
2. **If the decision moves later than `_routing_gate`**, the five legacy branches must be
   removed *and* their behaviour re-specified, because `_routing_gate` would otherwise answer
   first. That re-specification is a product decision: 41 certified tests describe what those
   branches currently send to real customers, and the owner should see that change rather than
   have it absorbed into a test rewrite.
3. **What happens when the semantic layer is silent?** The certified suites run CE alone, and
   in production the model can be unavailable. An evidence-only router loses the ability to
   clarify at all in that state. My attempt to admit deterministic CE evidence as relevance
   made things worse (67 added identities) because the CE signals available at that point are
   not turn-scoped. This needs a deliberate answer, not a patch.

The preserved patch and the four new files are at
`…/scratchpad/rev37f/clar/preserved/`, with `implementation.patch` (2,039 lines) and the PG
test suite that proves the data layer.

### Deferred, unchanged

R2R-07 remains owned by G3-8: a correction applied after booking can leave
`ThreadRevision`/CRM `Revision` with stale vehicle data. Nothing in this milestone touched
booked revisions, and the attempt entity is orthogonal to them — it records whether a
clarification was owed and spent, never vehicle data, and adds no revision writer.

NEXT: FINAL INDEPENDENT REVIEW OF G3-7F CLARIFICATION-LIFECYCLE ARCHITECTURAL IMPLEMENTATION
STOP.
