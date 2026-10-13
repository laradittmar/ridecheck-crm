PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-GATE3-G3-7R-ACCEPTANCE-ADMISSION-AND-DATABASE-AUTHORITY

# One offer, at most one booking — guaranteed by the database

**Date:** 2026-10-13
**Branch:** `fix/m21.1.1-primary-flow-regression`
**Baseline:** `98eff4d` (parent `fb0947c`) — R3A retained, not amended
**Commit:** one local commit (see §25). Not pushed, not built, not deployed, no Wild.
**Flags:** `OUTBOUND_ENABLED=false` throughout. No flag changed. `crm_test` untouched.

---

## 0. The sentence this milestone exists to make true

The G3-7P-R3A independent review removed the two application guards the booking-offer
authority relied on — the `SELECT ... FOR UPDATE` on the offer row and the conditional
`UPDATE ... WHERE status = 'ISSUED'` — and measured the result on real PostgreSQL:

> two concurrent confirmations of **one** offer produced **two bookings** in **6 of 6**
> trials, at two different slots that were both genuinely free, and the entire
> 6,000-test suite stayed identity-identical while those guards were gone.

Two separate failures in one measurement. The system had no guarantee that one offer
yields one booking, and it had no oracle that could see the absence.

G3-7R closes both:

    The advisory lock is coordination. The exclusion constraint is the guarantee.

`UNIQUE (thread_revisions.offer_id) WHERE offer_id IS NOT NULL`. P3 drops that index in a
disposable mutation, with both application guards bypassed, and requires the duplicate to
appear — so the guarantee now has a direct oracle, and the oracle is checked.

---

## 1. Preflight (exact)

| Fact | Value |
|---|---|
| HEAD at start | `98eff4d354a2e15b57bcc6bf4ee94dc6192e7c1a` |
| Parent | `fb0947c1c821a627c007a9f871294d5548c471df` |
| Tracked tree before | clean |
| Untracked before | 154 |
| Stash entries | 0 (shared stack; nothing pushed or popped) |
| Ahead / behind `origin/main` | **276 / 0** — the branch is 276 commits AHEAD and 0 behind (`git rev-list --left-right --count HEAD...origin/main` → `276 0`). Earlier closeouts in this series state this the other way round; the orientation is corrected here, and nothing about the branch changed |
| Sealed baseline | 6190 passed, **69 failure identities**, 223 skipped |
| Migration head before | `20261010_offer_lifecycle` (single head, 48 files) |
| `OUTBOUND_ENABLED` | `false` |

Every executable probe loaded an egress blocker **before any other import**; it resolves
the `postgres` service once and refuses every other destination, hostname or literal. The
previous blocker admitted anything that parsed as an IP address, which is every address
there is — it is replaced, not reused. `OPENAI_API_KEY` was empty in every probe. No
Meta/OpenAI/Maps call was made. All mutations were applied to **copies** under the
scratchpad; the working tree was never mutated. No migration was applied to shared
`crm_test`. No tester was reset. n8n was not touched. Track B was read-only.

---

## 2. What was built — the ten items

| # | Item | Where |
|---|---|---|
| 1 | `thread_revisions.offer_id` + FK + partial UNIQUE; both transports write it; the violation is translated, never leaked | `models.py`, `20261013_booking_projection.py`, both writers |
| 2 | Turn-entry predicate and lock **deleted**; database-first admission at the acceptance point | `OfferLifecycle.admit()`, `_handle` |
| 3 | One candidate authority: the offer's own binding, resolved once, both transports | `_offer_bound_candidate` (×2), `withdraw_if_candidate_moved` |
| 4 | One lock order for **every** offer writer, ISSUE included; `40P01` classified apart | `_require_thread_state`, `OFFER_DEADLOCK` |
| 5 | CE final availability revalidation, before acceptance, same authority and advisory key as DX | `_revalidate_booking_slot` |
| 6 | Correct replay semantics in both directions | `ACTION_BOOKING_REPLAY`, `BookingAlreadyCompletedError`, `record_replay` |
| 7 | `_bounded_lock_wait` restores the **prior** value, nested, abort-safe | `offer_lifecycle.py` |
| 8 | Four obsolete claims corrected; `PATCH /api/revisions/{id}` documented as a distinct authority | `CLAUDE.md`, `BOOKING_UX_CONTRACT.md`, model + migration docstrings |
| 9 | Authenticated read-only offer-authority view + JSON | `offer_authority_view.py`, `/control/offers`, `/api/ops/offers` |
| 10 | Strict subset of Option C; no second booking authority; no name collisions with Track B | `OfferAdmission` / `admit()` |

---

## 3. PostgreSQL evidence — P1 to P8, all PASS

Disposable databases only, created and dropped per run, egress blocked, synthetic data.
`8 passed` on the final run.

### P1 — the database guarantee beneath the application locks

One offer, two concurrent confirmations, two **different** genuinely-free slots (asked of
`ScheduleService`, never a literal date), with the declared `_Bypass` removing exactly
three things by name: `_acquire_thread_state`, the offer row's `FOR UPDATE`, and the
conditional `UPDATE ... WHERE status = 'ISSUED'`.

Result: **1 booking, 1 ThreadRevision, 1 CRM Revision.** The loser received a typed offer
outcome. The two slots are on different days on purpose — a same-slot race would be caught
by the calendar, and a test that passes because of *that* has proved the calendar works.
Asserted explicitly: the loser's message contains no `IntegrityError`, no `23505`, no
constraint name, no `psycopg`, and not the slot-conflict wording.

### P2 — guard attribution, one protection at a time

| Protection | How it was isolated | What refused the loser |
|---|---|---|
| application (conditional ISSUED + row lock) | both guards present, sequential | offer ACCEPTED, second attempt recorded as **replay** (`replay_count=1`, `refused_count=0`) |
| **the partial unique index** | both guards bypassed, concurrent | `BookingAlreadyCompletedError` — the index fired and was translated |
| the calendar | occupying Revision at the same slot, guards present | `BookingSlotConflictError`, **0** bookings, and the offer stayed `ISSUED` |

The third row is the contrast the prompt asked for: a slot-conflict refusal is visibly a
different outcome, so no row-count assertion here can be satisfied by it.

### P1/P3 — how the bypass had to be completed, and why it is written down

The declared `_Bypass` removes **four** things, and arriving at that list took four
measurements. Each earlier version *looked* like it isolated the index and did not, which
is the exact failure mode this suite exists to prevent — a probe that reports the
application layer's work as the database's.

| Removed | Why the previous list was incomplete |
|---|---|
| `_acquire_thread_state` **and** `begin_offer_transaction` | the latter takes resource 1 directly, not through the helper, so the loser waited its bounded 4s on the winner's state-row lock and answered `OFFER_CONTENDED` without ever inserting |
| the offer row's `SELECT … FOR UPDATE` | — |
| the acceptance UPDATE **entirely**, not merely its `WHERE status = 'ISSUED'` | the UPDATE itself takes a row lock on the offer, so two writers serialise on it whether or not the condition is there. Measured: the loser sat in `Lock` on `UPDATE whatsapp_booking_offers` until the winner committed, so the two inserts could never overlap |
| `verify_bindings()`'s status pre-check | the same gate in the second place it lives since the checks were split out of `claim()` |

And one coordination point is **added**, because removing guards is not enough: the index is
reachable only if both writers pass admission before either commits. A plain barrier does
not guarantee that — the winner can finish its whole transaction first, and the loser is
then stopped by the transport's own mirror check. So `_race_for_the_index` holds the winner
*inside* `claim()` until the loser is at its own insert.

Earlier drafts tried to fix this by stubbing more and more copies of the status check,
including inside `admit()`. That was the wrong direction and is recorded as such: it broke
P2, P4 and P8, because admission's replay detection is what lets a legitimate second
delivery past the cleared mirror at all. The gate replaced it.

**Determinism was then measured, not assumed:** 8 passed in **4 of 4** independent runs on
four separate disposable databases. A probe whose result depends on an interleaving is not
a probe, and this one no longer does.

### P3 — the structural constraint has a direct oracle

With both application guards bypassed and the confirmations **concurrent**:

* index present → **1** booking
* index `DROP`ped in a disposable mutation → **2** bookings, at two different dates

Both measured. The restore then fails until the duplicates are removed — `CREATE UNIQUE
INDEX` refuses a table that already violates it, which is a second, independent
confirmation that the duplicate was real. Sequential confirmations do **not** isolate the
index (the replay path catches the second one first, correctly); that is why P3 races them,
and it is stated in the test.

### P4 — the transport race

Real `ConversationEngine` against real `BookingFlowService` on one offer: same submitted
slot and different slots, in both winner orders — 4 subtests. Asserted in every case: at
most one accepted offer, at most one booking, at most one ThreadRevision, at most one CRM
Revision, the booking names its offer, the winning offer carries **no** refusal, and
`40P01` / `OFFER_DEADLOCK` appear nowhere.

### P5 — ordinary-message isolation

At 1-, 6- and 12-second booking holds (real locks: `begin_offer_transaction` plus
`FOR UPDATE` on the offer rows), three ordinary text turns each:

* none was declined (`thread_contended` never appeared)
* each completed in **0.015–0.072 s** against holds of 1 s, 6 s and 12 s
* the session was instrumented, and the ordinary turns issued **zero** statements matching
  `FOR UPDATE`, `FOR NO KEY UPDATE` or `pg_try_advisory` — not a bounded wait, *no lock
  operation at all*
* every turn was owned by CE and produced a reply; none returned `error`

For comparison, the R3 review measured the turn-entry acquisition **declining** the second
turn after 4.05 s on a thread the parent commit served in 0.05–0.20 s.

### P6 — the ISSUE/acceptance race

Real ISSUE driven concurrently against real acceptance, **6 trials**. Zero `40P01`, zero
`OFFER_DEADLOCK`, valid terminal offer states only, at most one booking per trial, and no
committed booking lost. The architecture audit measured `40P01` in **3 of 3** trials here
with the acceptance writer the victim every time, misreported as `OFFER_CONTENDED`.

### P7 — CE stale-slot rejection (SAA-F1 inverted)

Premise asserted first: with an occupying Revision at the slot, `ScheduleService.check()`
returns `valid=False`. Then the real Flow response:

* action is **not** `booking_created`
* **0** bookings, and the CRM Revision count is unchanged — no second occupying revision
* the customer is not told it succeeded
* the offer is **not** `ACCEPTED`; `last_refusal = 'SLOT_NO_LONGER_AVAILABLE'`
* the offer remains `ISSUED`, so the same token can still book another slot

On the parent, this exact scenario returned `booking_created` and wrote a second occupying
Revision at the identical date and time.

### P8 — replay, in both directions

DX books → CE receives the completion: action `booking_replay`, `reply_required=False`, no
duplicate booking, no duplicate CRM Revision, `refused_count` unchanged, `last_refusal`
still `NULL`, `replay_count=1`, and no handoff state written.

CE books → DX receives the confirmation again: `BookingAlreadyCompletedError` carrying the
authoritative booking, **not** a `BookingTokenError` (asserted as a non-subclass), the word
`expiró` absent, no duplicate booking, `refused_count=0`, `last_refusal` `NULL`.

---

## 4. Migration proof

Built on the **parent commit's real schema**, not from a from-scratch chain. The project's
alembic root already expects `revisions` to exist, so the chain is incremental over
`Base.metadata.create_all` and a from-scratch `upgrade` is not how this database is ever
built. The probe therefore creates the schema `98eff4d` declares, stamps it at
`20261010_offer_lifecycle`, seeds rows, and upgrades — the real transition.

| Clause | Result |
|---|---|
| version before → after | `20261010_offer_lifecycle` → `20261013_booking_projection` |
| new `thread_revisions` column | `offer_id`, `integer`, **nullable**, no default |
| new `whatsapp_booking_offers` columns | `replay_count` (`integer`, NOT NULL, default `0`), `last_replay_at` (`timestamptz`, nullable) |
| new index | `CREATE UNIQUE INDEX uq_thread_revision_one_booking_per_offer ON public.thread_revisions USING btree (offer_id) WHERE (offer_id IS NOT NULL)` |
| new FK | `fk_thread_revisions_offer_id FOREIGN KEY (offer_id) REFERENCES whatsapp_booking_offers(id)` |
| existing rows preserved | 3 → 3 |
| NULL historical rows legal | 3 NULL rows coexist; two more added → 5; and 6 alongside a bound one |
| the constraint bites | second booking for one offer **REFUSED, sqlstate 23505** |
| downgrade | `rc=0`, schema byte-for-byte equal to the pre-upgrade schema on **both** tables |
| re-upgrade | `rc=0`, schema byte-for-byte equal to the first upgrade on **both** tables |
| model vs database | no column missing, no column extra, on both tables |
| index name agreement | model and database both name `uq_thread_revision_one_booking_per_offer` |
| migration heads after | `20261013_booking_projection (head)` — exactly one |

Nothing was applied to shared `crm_test`.

---

## 5. Mutation credibility — 12 of 12 required mutations detected

| # | Mutation | Detector | Verdict |
|---|---|---|---|
| 1 | Drop the partial unique offer projection | P-suite (PostgreSQL) | **KILLED** |
| 2 | Remove the offer-row `FOR UPDATE` | sealed structural oracle | **KILLED** |
| 3 | Remove the conditional ISSUED transition | sealed structural oracle | **KILLED** |
| 4 | Remove **both** application guards | sealed structural oracle | **KILLED** |
| 5 | Reintroduce the turn-entry lock | sealed, `_handle` AST | **KILLED** |
| 6 | Restore separate candidate resolvers | BF-WM suite | **KILLED** (11 of 21) |
| 7 | Resolve the candidate twice inside CE | sealed | **KILLED** |
| 8 | Remove CE availability revalidation | P7 (PostgreSQL) | **KILLED** |
| 9 | Remove the per-date advisory coordination | sealed | **KILLED** |
| 10 | Reverse ISSUE's lock order | sealed structural oracle | **KILLED** |
| 11 | Return false expiry for a committed replay | P8 (PostgreSQL) | **KILLED** |
| 12 | Restore `DEFAULT` rather than the previous timeout | sealed | **KILLED** |

### Mutations 2–4: exactly which protection each detecting test exercises

The prompt asks this specifically, and the honest answer is more interesting than a clean
sweep. Four extra runs were performed to establish it:

| Run | What it does | Behavioural result on PostgreSQL |
|---|---|---|
| 2b | remove the row lock **only** | **SURVIVED** |
| 3b | remove the conditional UPDATE **only** | **SURVIVED** |
| 4 | remove **both** | **SURVIVED** |
| 10 | reverse ISSUE's lock order | **SURVIVED** |

None of those four survivals is a gap, and each has a mechanism:

* **2b / 3b — the two application guards are individually redundant.** With the row lock
  gone the conditional `UPDATE` still decides the race; with the conditional `UPDATE` gone
  the row lock still serialises it. No behavioural oracle for one of them can exist while
  the other stands, which is precisely why their oracles are **structural** and live in the
  default gate. That is the direct answer to the R3A finding: the removals are now visible.
* **4 — with both gone, exactly-one still holds, because the index holds it.** This
  mutation surviving the behavioural suite *is the milestone's deliverable*. On the parent,
  the same mutation produced two bookings in 6 of 6 trials; here it produces one, and P3
  shows that removing the index restores the duplicate. Its structural oracle kills it
  twice over (runs 2 and 3 each fire).
* **10 — defence in depth, measured.** Reversing the acquisition inside `issue()` changes
  nothing behaviourally because `_terminalise()` — which `supersede()` calls, inside
  `issue()` — now requires resource 1 in its own right. The order is enforced at two levels,
  so breaking one does not break the protocol. Its structural oracle kills it.

No mutation's detection rests on a row-count assertion produced by an unrelated
slot-conflict guard: P2's third row isolates that guard and shows it produces a visibly
different outcome (`BookingSlotConflictError`, 0 bookings, offer still `ISSUED`).

---

## 6. Three defects found and fixed inside this milestone

All three were introduced by the first draft of this work and all three were found by
measurement, not by reading. They are recorded in full because the independent review
should know they were there, how they surfaced, and that two of them were *hangs* — the
failure mode a passing test suite is least likely to show you.

### G37R-D1 — the terminal transitions ignored the contention signal (HIGH, fixed)

`_acquire_thread_state()` returns a typed refusal when resource 1 cannot be taken within
the bound. The first draft had `_terminalise()`, `consume()` and `issue()` **discard** that
return and write to the offer table anyway. Two consequences, and the second is worse than
the first:

1. the declared lock order was not actually enforced for those writers — they could reach
   the offer table without resource 1, which is the exact precondition of the `40P01` this
   milestone closes;
2. the offer-table write that followed had **no bound**, so a contended booking turn would
   wait indefinitely instead of answering.

**Measured, not reasoned:** the G3-7P PostgreSQL suite stalled indefinitely at the same
test, with `pg_stat_activity` showing the `TRUNCATE` blocked behind a session
`idle in transaction`. Fixed by `_require_thread_state()`, which raises
`OfferProtocolContended` rather than proceeding, and by bounding every offer-table write in
`_bounded_lock_wait` as a second line. `claim()` deliberately keeps the non-raising form:
it is the acceptance boundary and owes its caller a typed domain outcome, not an exception.
Oracles: `test_g37r_lock_01b`, `test_g37r_lock_01c`.

**Reachability of the raise, stated for the reviewer.** On both acceptance paths it is
unreachable: `begin_offer_transaction()` takes resource 1 before any writer runs and a row
lock is held to the end of its transaction, so it cannot be lost mid-way. It is reachable
from the CE **text** paths that withdraw or supersede without having entered the order —
a vehicle correction racing the other booking writer. There it surfaces as `action="error"`
with the transaction rolled back, which is honest: CE keeps ownership of the turn, nothing
is written, and a redelivery retries. Deliberately **not** swallowed into "withdrew 0
offers": a withdrawal that silently failed would leave a stale offer live, which is the
defect class G3-7N closed.

### G37R-D2 — the new FK made the PostgreSQL fixtures sensitive to a leaked session (fixed)

`thread_revisions.offer_id` points **into** `whatsapp_booking_offers`, so
`TRUNCATE whatsapp_booking_offers ... CASCADE` now needs an `ACCESS EXCLUSIVE` lock on
`thread_revisions` as well. Any pooled connection left `idle in transaction` after reading
a ThreadRevision therefore blocks the fixture, where before the FK existed it did not. The
leak is pre-existing and was harmless; the FK made it fatal. Fixed in the fixtures
(`self.engine.dispose()` before truncating), with the reason written where the next author
will read it — the fix removes the fixture's dependence on every test closing its session,
rather than hunting the specific leak.

### G37R-D3 — deleting the turn-entry gate left the cursor write unbounded (HIGH, fixed)

The subtlest of the three, and the one that justifies the independent review on its own.

R3A kept its turn-entry acquisition partly for a reason its own documentation did not
state: a booking turn's dedup-cursor write to `whatsapp_thread_states` then landed on a row
the turn **already held**. Deleting the gate (S2) is required — SB2B-1 forbids entering the
canonical authority holding that row — but it left that write to be flushed wherever
SQLAlchemy's autoflush first fired. That turned out to be `admit()`'s read, which runs
*before* `begin_offer_transaction()` and therefore outside any `lock_timeout`. A second
booking turn on the same thread then waited on the first one's state-row lock **with no
bound at all**, against R3's own standing requirement that the losing writer wait a bounded
~4s.

**Measured, not reasoned:** `pg_stat_activity` showed
`UPDATE whatsapp_thread_states SET last_processed_inbound_wa_message_id = …` in state
`active / Lock` at 36 seconds and climbing, behind a holder `idle in transaction`.

Two fixes, because there were two paths:

1. **The authority's reads no longer autoflush.** `live_offer`, `offer_by_token` and
   `latest_offer` run under `no_autoflush`, so a caller's pending write is not dragged out
   by a read. The cursor write then flushes where a bound exists — inside
   `begin_offer_transaction()`. None of those reads depends on unflushed caller state, so
   this costs nothing. It is a lock-ordering property, not tidiness.
2. **Contention no longer takes the refusal path.** The first draft routed every refusal,
   contention included, through the warm-handoff helper — which writes `needs_human` and
   commits, and that commit needs the row the other writer holds, unbounded, because
   `begin_offer_transaction` has already abandoned its bounded attempt. Measured at 21s.
   A contended turn now returns `ACTION_THREAD_CONTENDED` and writes nothing, which is also
   the honest answer: the turn did not complete, so declaring a human handoff for it would
   be a statement about a submission nobody has judged. It is what that action was defined
   for, and what its own contract in `schemas/conversation.py` already said.

The reviewer should treat this as the load-bearing consequence of S2 and attack it
directly: the claim is that **no** booking-path write to `whatsapp_thread_states` can now
wait unbounded, and that claim rests on the cursor flushing inside
`begin_offer_transaction()` rather than on any turn classification.

---

## 7. One pre-existing observation, reported not fixed

### G37R-O1 — an ordinary FAQ/service-gate turn consumes no dedup cursor (LOW, pre-existing)

While writing P5 the natural assertion was "every ordinary message was consumed". It fails,
and it fails for a reason that has nothing to do with this milestone:

| Tree | Action | `last_processed_inbound_wa_message_id` |
|---|---|---|
| this branch | `replied` | `NULL` |
| parent `98eff4d` | `replied` | `NULL` |

Measured with **no booking holder present at all**, on both trees, identical. An ordinary
text turn on that path answers the customer and leaves the cursor unset, so a redelivery of
the same `wa_message_id` would be processed again rather than deduped.

Not this milestone's to fix — G3-7R neither owns nor changed that path, and changing dedup
behaviour here would be unrelated feature work during active launch certification. P5
therefore asserts the property it does own (ownership and an answer for every turn, no
decline, no `error`) and names this observation in the test, so the next reader is not
misled into thinking the cursor was checked. **Recommended for G3-7Q or its own milestone.**

---

## 8. Regression — failure identities, not totals

| Run | Identities | Delta |
|---|---|---|
| baseline `98eff4d` (sealed) | **69** | — |
| after implementation, before test maintenance | 107 | +38 NEW, 0 GONE |
| **final (sealed)** | **69** | **0 NEW, 0 GONE** — 6252 passed, up from 6190 |
| G3-7P PostgreSQL suite, parent `98eff4d` | **0 failures** | baseline established on a fresh disposable database built from the parent's own models |
| G3-7P PostgreSQL suite, final | **132 passed, 0 failed** | equal to the parent baseline |
| G3-7R `P1–P8`, final | **8 passed**, in **4 of 4** independent trials | determinism measured, not assumed |

Every one of the 41 interim identities was triaged individually; none was dismissed as
baseline noise. They fall into six groups, and each group's resolution is stated so a
reviewer can disagree with a specific one rather than with a count.

The PostgreSQL suite deserves its own line because it has no sealed baseline to compare
against: it skips without `G7P_PG_URL`. So the parent commit was run first, on its own
disposable database built from the parent's models — **0 failures** — and that is the
baseline the final run is compared to.

| Group | Count | What it was | Resolution |
|---|---|---|---|
| mock-session unit suites | 8 | `admit()`, `_offer_bound_candidate` and `_revalidate_booking_slot` are database decisions, and these suites' session is a `MagicMock` | the ONE declared fixture (`offer_fixture.start_offer_claim_stub`) now stubs all four controls, each naming where it is really proven; `_offer_bound_candidate`'s stub applies the **real** S3 rule in memory, so the field-mapping assertions still mean something |
| DX candidate-window suite (BF-WM) | 21 | tested `_load_focus_candidate` and its "most recently updated row wins" tie-break — the rule S3 **deletes** | rewritten against the new resolver. Every cycle-window property (BF-WM-01, 02, 04, 05, 06) is preserved and extended with the refusals the old rule could not express (deleted row, foreign row, exact boundary, one microsecond before it). BF-WM-03 now asserts the replacement **in the negative**: the offer's binding decides and the most recently updated row does **not** win |
| source-order guards (G7N ×4, BOOKPRICE-10) | 5 | pinned a literal order (`claim(` before `buyer_email`) or a commit **count** | re-anchored to the property. G7N now requires admission before any field **and** acceptance before any booking row — strictly stronger. BOOKPRICE-10 now identifies the booking commit (the first commit after the booked ThreadRevision, on the success path only) instead of counting commits |
| shared-slot fixture leak | 1 | `_clean_all` omitted `revisions`, so one test's booking occupied the next test's slot — invisible until CE began revalidating | `revisions` and `whatsapp_booking_offers` added to the list. The previous behaviour was the engine double-booking an occupied slot without checking |
| G3-7P PostgreSQL suite | 3 | the literal fixture slot `2026-11-10 10:00` stopped being bookable once CE revalidates availability; one test required `last_refusal='OFFER_ALREADY_ACCEPTED'` for a replay; one admitted only two typed outcomes for the loser of a transport race | the fixture now asks `ScheduleService` for a genuinely open slot, cached per class (the file already carried `_open_slot()` with the rule "Never a literal date" — it is now applied to the fixture, which is where the literal was); the replay test asserts `replay_count=1` with `refused_count=0` and `last_refusal` NULL; the race test admits `booking_replay` as the third typed loser outcome, because a loser whose winner has already committed has not been refused — the booking exists and is returned to it |
| global migration-count guards | 2 + 1 route guard | froze a global migration total, bumped by four consecutive unrelated milestones | the global count is **removed**. One guard names each migration with its authorising milestone; the other asserts the structural fact worth keeping — the graph still has exactly **one head**. The `/api/ops` guard's ungated list is byte-for-byte unchanged, which is the half that carries the invariant |

**No test was weakened to pass.** Three were made stronger (G7N ordering, BOOKPRICE-10
anchoring, BF-WM refusals), two brittle global pins were replaced with assertions about the
thing they were supposed to protect, and one fixture bug was fixed.

### Suites run

Gate 3 targeted · offer-lifecycle (sealed + PostgreSQL) · CE · Data Exchange ·
scheduling/availability · dashboard/auth/privacy · migration · PostgreSQL concurrency ·
full sealed regression. New suites: `test_l4_7w5_g3_7r_unification.py` (52 sealed) and
`test_l4_7w5_g3_7r_booking_projection_pg.py` (P1–P8).

---

## 9. Separated verdicts

| # | Subject | Verdict | Basis |
|---|---|---|---|
| 1 | Baseline integrity | **PASS** | `98eff4d` on `fb0947c`, tracked tree clean before and after, 154 untracked preserved, 0 stash, 0 ahead / 276 behind, `crm_test` untouched |
| 2 | R3A retention | **PASS** | `98eff4d` not amended; one new commit on top. R3A's narrowing of the booking lock is superseded by deleting the gate, which is strictly stronger — the lock order moved into `offer_lifecycle` and now covers writers R3A never reached |
| 3 | Turn-entry predicate deletion | **PASS** | `_is_offer_acceptance_turn` and `_begin_thread_transaction` deleted; `_handle`'s AST contains no lock acquisition. Mutation 5 reintroducing it is KILLED |
| 4 | Database-first admission | **PASS** | `admit()` = one indexed read on `token_fingerprint`, no lock, no mirror, no payload shape, no prefix, no prose. 9 sealed tests cover every refusal shape including no token at all |
| 5 | Ordinary-message isolation | **PASS** | P5: 0.015–0.072 s against 1/6/12 s holds, zero lock statements issued, none declined, every turn owned and answered |
| 6 | Candidate authority | **PASS** | the offer's `candidate_id`, resolved once; no `order_by`, no `updated_at`, no newest-row fallback; focus move withdraws at the pointer AND is refused at the boundary |
| 7 | CE / Data Exchange candidate equivalence | **PASS** | same rule, same four refusals, same boundary guard, asserted clause-by-clause on both implementations |
| 8 | ISSUE lock order | **PASS** | `issue()` requires resource 1 **before** `supersede()`; mutation 10 KILLED structurally; `_terminalise` enforces it independently (defence in depth, measured) |
| 9 | Acceptance lock order | **PASS** | every transition requires or acquires resource 1; every offer-table write is bounded |
| 10 | Deadlock elimination | **PASS** | P6: 0 × `40P01` in 6 trials where the audit measured 3 of 3. `40P01` now has its own refusal and logs at ERROR |
| 11 | CE availability revalidation | **PASS** | P7: `ScheduleService` refuses, CE creates 0 bookings, offer stays `ISSUED`, `last_refusal='SLOT_NO_LONGER_AVAILABLE'`, customer not told it succeeded |
| 12 | One-offer / one-booking database guarantee | **PASS** | P1 (1 booking from 2 concurrent confirmations with the application guards bypassed) and P3 (index dropped → 2 bookings) |
| 13 | Database-guard observability | **PASS** | P2 attributes each protection separately; P3 is a direct oracle for the index; mutations 2/3/4 KILLED by the default gate — the R3A invisibility finding is closed |
| 14 | Cross-transport race | **PASS** | P4, 4 subtests: ≤1 accepted offer, ≤1 booking, ≤1 ThreadRevision, ≤1 Revision, no deadlock, no contradictory refusal on the winner |
| 15 | Replay correctness | **PASS** | P8 both directions; `booking_replay` / `BookingAlreadyCompletedError`; no false expiry, no counter, no duplicate ack, no duplicate booking |
| 16 | Refusal-state consistency | **PASS** | one helper owns every refusal on the CE path; `replay_count` is a separate column from `refused_count`; a replay writes neither `last_refusal` nor a handoff |
| 17 | Timeout restoration | **PASS** | `SHOW lock_timeout` on entry, exact value restored, nested bounds compose, original exception preserved after abort, non-literal values never re-inlined. Mutation 12 KILLED |
| 18 | Operator-view auditability | **PASS** | every required field rendered; ISSUED / ACCEPTED / WITHDRAWN / SUPERSEDED / EXPIRED / replay / refusal / booking-committed all read differently; an acceptance with no booking is flagged `bad` |
| 19 | Privacy / authentication | **PASS** | `/control` protected prefix; `/api/ops/offers` and `/api/ops/offer/{id}` behind `require_crm_session`; the ungated `/api/ops` list is byte-for-byte unchanged; no raw token (none is stored), fingerprint truncated to 12 chars, no WAMID, no phone, no customer prose; the accessor selects declared fields only |
| 20 | Migration safety | **PASS** | §4 — additive, rows preserved, NULLs legal, constraint bites, downgrade and re-upgrade byte-identical, model/migration names agree, one head |
| 21 | Mutation credibility | **PASS** | 12 of 12 required mutations detected; the four principled survivals are explained and each has a structural detector |
| 22 | PostgreSQL evidence | **PASS** | P1–P8: **8 passed** on disposable databases with egress blocked, repeated in **4 of 4** independent trials. The established G3-7P suite: **132 passed, 0 failed**, equal to its parent-commit baseline of 0 failures |
| 23 | Full-regression neutrality | **PASS** | 69 → 69 identities, 0 NEW, 0 GONE; all 38 interim identities triaged individually, none dismissed |
| 24 | Compatibility with future SB2B convergence | **PASS** | strict subset of Option C; no second booking authority; `OfferAdmission` is frozen and carries everything an executor needs; no name collides with `BookingCommand`, `BookingResult`, `BookingOutcome`, `flow_idempotency_key`, `AckClaim`, `AvailabilityRevalidation` or `BookingAuthority` |
| 25 | Commit safety | **PASS** | exactly one local commit, whose parent is `98eff4d` (its own SHA is deliberately not
quoted here: this file is inside that commit, so naming it would be stale the moment it
is written — `git log -1` is the authority) — R3A not amended; tracked tree clean, 154 untracked files preserved unchanged, 0 stash operations; 277 ahead / 0 behind `origin/main` (one new commit, nothing pushed); nothing built or deployed; no flag changed; no shared-service restart; `crm_test` verified unchanged after the work (`20260918_hybrid_traces`, `whatsapp_booking_offers` absent, leads 47 / revisions 54); deployed image still `840bce0` |
| 26 | Readiness for independent G3-7R review | **PASS** | implementation, proofs, mutations and this document are complete; three self-found defects (§6) and one pre-existing observation (§7) are disclosed rather than left for the reviewer to find. §6's G37R-D3 is named as the load-bearing consequence of S2 and is where the review should start |
| 27 | G3-7Q readiness | **BLOCKED** | G3-7Q was blocked behind the acceptance architecture and remains behind the independent review of this commit. Nothing here unblocks it on its own |
| 28 | Partial-Wild readiness | **FAIL** | not authorized here and not earned: the live path is unchanged in production (deployed image is still `840bce0`), this commit is unreviewed, and a Wild on an unreviewed acceptance boundary is the sequence this programme exists to prevent |
| 29 | Deployment readiness | **FAIL** | not authorized, and correctly so. The migration must be applied before the image that needs it; the sequencing is safe in that direction only (§4) and is a deployment milestone's decision, not this one's |
| 30 | **Gate 3 status** | **OPEN — materially advanced, not closed** | see §10 |

### 30 in full

Every FAIL the architecture audit recorded against the acceptance boundary is now closed
with measurement: exactly-one-booking has a database guarantee with a direct oracle (3, 4,
5), the transports agree about the candidate and about availability (7, 15, 16), the
`40P01` is gone (17), the protocol covers every writer including ISSUE (21), replay is not
a refusal (22), and the decisions have an operator surface (23).

Gate 3 does **not** close here, for three reasons that are not this milestone's to fix:

1. **The convergence is still owed.** This milestone is deliberately a subset: Gate 3 owns
   admission, and Smart Booking's `BookingAuthority` is to own execution.
   `PRELAUNCH-SB2B-OFFER-ACCEPTANCE-CONVERGENCE` is authorized and not begun. Until it
   lands there are still two booking-writing code paths, even though they now share one
   authority, one candidate, one availability check and one guarantee.
2. **This commit is unreviewed.** Gate 3 has an explicit exit criterion and an independent
   review is part of it. Two defects in §6 were found by measurement *after* the code was
   written and read; that is exactly the argument for the review, not against it.
3. **No live evidence exists.** Nothing here has run against a handset. The deployed image
   is `840bce0`, which does not know this table or this column exists.

The open findings this milestone does **not** claim to close: SAA-F5 (the second candidate
resolver is unified, but the G3-7R2 split for the remaining resolution questions remains
authorized), SAA-F10's index-name drift on `whatsapp_booking_offers`, R3-F1/F3/F4/F5/F6/F9,
R3A-F1/F2/F3, B-03 and B-04. R3A-F4 and R3A-F5 rode along as intended (the deadlock and the
candidate resolvers).

---

## 10. The architecture, as implemented

```
                 submitted Flow token
                          │
            ┌─────────────▼─────────────┐
            │  OfferLifecycle.admit()   │   ← S2. ONE indexed read. No lock.
            │  "is there an ISSUED      │      No mirror, no payload shape,
            │   offer for this thread   │      no token prefix, no prose.
            │   and this token?"        │
            └──────┬───────┬────────────┘
         REFUSED   │       │   REPLAY ──→ return the authoritative booking
         (typed,   │       │              (no counter, no ack, no handoff)
          durable) │       │
                   │   GRANTED  ─ carries candidate_id, category, zone pair,
                   │              cycle_key, and offered_date/time as HISTORY
                   ▼
        resource 1: whatsapp_thread_states        ← S1/S4. EVERY writer.
                   ▼                                 ISSUE included.
        the offer-bound candidate (S3)             ← one authority, one resolution
                   ▼
        advisory lock on the date + ScheduleService ← S5. Same authority and the
                   ▼                                  SAME key as Data Exchange.
        claim()  ── row lock + conditional UPDATE   ← coordination
                   ▼
        INSERT thread_revisions(offer_id)           ← THE GUARANTEE
                   │                                   UNIQUE(offer_id)
                   │                                   WHERE offer_id IS NOT NULL
                   ├── 23505 ──→ translated to a REPLAY, never leaked
                   ▼
        commit, then external I/O
```

Two things are worth stating plainly because they are what the review should attack.

**The predicate is gone, not narrowed.** R3 locked on every turn; R3A narrowed with a
predicate that answered from the mirror and the payload and documented its fallback as
answering True. G3-7R removes the gate. The boundary question is answered exactly, at the
point of acceptance, from immutable facts — so there is nothing left for an ordinary turn
to be mis-classified into, and the lock order now covers writers no turn-entry gate ever
reached. Removing the turn-entry hold is also a **precondition** for the authorized
convergence: SB2B-1 forbids entering `BookingAuthority` with uncommitted locks on
`whatsapp_thread_states`, and the audit measured the adapter's own session refused after
5.02 s with `55P03` while the turn held that row.

**The guarantee is not application code.** The row lock and the conditional UPDATE are
still here and still do useful work — they turn a race into a typed refusal instead of an
exception. But they are coordination. What makes one offer yield at most one booking is a
partial unique index, and the measurements in §3 and §5 are arranged so that claim can be
checked rather than believed.

---

## 11. Owner booking-slot policy — how it was honoured

The policy (2026-10-13) is implemented exactly as written, and its two halves pull in
opposite directions, so each is stated with where it is enforced:

| Policy clause | Where |
|---|---|
| `offered_date` / `offered_time` remain historical/audit metadata | never compared by any writer; `admit()` carries them as `offered_*` and the operator view labels them "histórico: el cliente puede haber elegido otro horario" |
| they are **not** a fixed single-slot acceptance binding | no comparison exists to remove — asserted by the absence of any `offered_date` comparison in either writer |
| a legitimate active token may produce exactly one booking | the partial unique index; P1, P3, P4 |
| the submitted slot must pass authoritative final revalidation | `_revalidate_booking_slot` (CE) and the existing check (DX), same `ScheduleService`, same advisory key; P7 |
| once one booking commits, the same offer/token cannot create another **at any slot** | P1 and P3 race **different** slots on purpose, so no calendar conflict can be mistaken for the guarantee |
| arbitrary text must never act as a booking acceptance | admission requires an ISSUED offer row matching the token's fingerprint; no phrase catalogue exists anywhere on the path (asserted) |
| the decision must not weaken revalidation, idempotency or uniqueness | revalidation moved **earlier** (before acceptance), replay became typed, uniqueness became a constraint |

One consequence worth flagging to the owner: because the submitted slot is now revalidated
on the conversation path as well, a booking whose negotiated slot has gone is **refused**
where it previously succeeded. That is the SAA-F1 defect inverted and it is the intended
behaviour — but it is a live-behaviour change, and it is the single thing in this commit
most worth watching on the first Wild after deployment.

---

## 12. Files

**New (4):** `backend/migrations/versions/20261013_booking_projection.py` ·
`backend/app/ui/offer_authority_view.py` ·
`tests/test_l4_7w5_g3_7r_unification.py` ·
`tests/test_l4_7w5_g3_7r_booking_projection_pg.py`

**Changed — product (8):** `offer_lifecycle.py` · `conversation_engine.py` ·
`booking_flow_service.py` · `models.py` · `schemas/conversation.py` ·
`routes/flow_data_exchange.py` · `routes/ops_dashboard.py` · `ui/kanban.py`

**Changed — documentation (2):** `CLAUDE.md` · `docs/architecture/BOOKING_UX_CONTRACT.md`

**Changed — tests (11):** `offer_fixture.py` and ten suites, each change explained in §8.

---

## 13. What was explicitly not done

No push. No merge. No tag. No build. No deploy. No shared-service restart. No migration
applied to `crm_test`. No flag changed. `OUTBOUND_ENABLED` stayed `false`. No WhatsApp
message. No Meta, OpenAI or Maps call. No tester reset. No n8n change. No Wild. R3A not
amended. Track B's SB2B work at `870b4e3` was read only and not merged. G3-7R2, the
convergence, deployment and Wild testing were **not** begun.

---

## 14. Owner request — the two general reference documents

Asked for alongside this closeout: an `scp` command for `BACKEND_ARCHITECTURE.md` and
`TESTING_GUIDE.md`.

**Neither file exists.** Searched the release-candidate worktree, the main checkout at
`/opt/ridecheck-crm`, and every `.md` under `docs/`: no file of either name, and no
document references either name. They appear to have been *proposed* rather than written —
nothing in this session's work created them and nothing in the repository points at them.

The command is given anyway, so it works the moment they exist:

```bash
scp root@147.182.172.47:/opt/ridecheck-crm-release-candidate/docs/{BACKEND_ARCHITECTURE.md,TESTING_GUIDE.md} .
```

The architecture references that **do** exist, and are the nearest equivalents:

```bash
scp root@147.182.172.47:/opt/ridecheck-crm-release-candidate/docs/architecture/*.md .
```

which retrieves `BOOKING_UX_CONTRACT.md` (corrected by this milestone),
`CONVERSATION_RUNTIME_CONTRACT.md`, `DOMAIN_MODEL.md` and
`INBOUND_MESSAGE_LIFECYCLE.md`. Testing conventions currently live in `CLAUDE.md`
(§ Test Environment) and in the suite headers themselves; there is no standalone guide.

Writing the two documents is outside this milestone's authorization (it permits correcting
*inaccurate* documentation, not authoring new general references), so they were not
created. Say the word and they become their own small milestone.

---

NEXT: L4.7W5-GATE3-G3-7R-INDEPENDENT-REVIEW
