PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-GATE3-G3-7R-R2-REFUSAL-PATH-BOUNDS

# Every booking-shaped turn answers inside its bound

| | |
|---|---|
| Date | 2026-10-13 |
| Commit | the single commit on top of the parent below — **see §11 on why its SHA is not printed here** |
| Parent | `9181966d907add21d1acb9d26f2e74c1c7de0bf0` (G3-7R) |
| Branch | `fix/m21.1.1-primary-flow-regression` (worktree `/opt/ridecheck-crm-release-candidate`) |
| Scope | 10 files, +1067 / −137 |
| Pushed | NO |
| Image built | NO |
| Deployed | NO |
| Migration applied to `crm` / `crm_test` | NO |
| `OUTBOUND_ENABLED` | `false`, unchanged |
| Answers | `L4.7W5-GATE3-G3-7R-INDEPENDENT-REVIEW` findings RV-01, RV-02, RV-04, RV-05, RV-07 |

---

## 1. What was wrong, as a measurement

G3-7R gave the offer authority one canonical, bounded acquisition order and deleted the
turn-entry lock. Its own suite passed, its PostgreSQL suite passed 132 tests, 12/12
mutations were detected, and the sealed regression was identity-identical to its parent.

The independent review then held `whatsapp_thread_states` for twelve seconds and submitted
a Flow response whose offer had been **withdrawn** — the ordinary outcome of a customer
correcting their vehicle or their address — and measured the turn at **42 seconds and still
waiting**, where the parent commit `98eff4d` answered `thread_contended` in **4.06s**.

G3-7R had made the live path **worse than the commit it was correcting**, on its most
common refusal, while every gate it defined passed.

This milestone re-measured the same hold before changing anything, and widened the probe to
the two classes the review never tried — a granted acceptance and a replay, both of which
also write:

| submission class | before | typed? | dedup cursor |
|---|---|---|---|
| unknown token | **72.002s** | no — `replied` | **consumed** |
| withdrawn offer | **72.009s** | no — `replied` | **consumed** |
| superseded offer | **72.007s** | no — `replied` | **consumed** |
| granted acceptance | 4.025s | yes — `thread_contended` | intact |
| replay | **72.005s** | no — `booking_replay` | **consumed** |

Two things in that table are worse than the review reported. The replay path is affected
too, and the four unbounded classes **consumed the inbound dedup cursor on the way out** —
so a turn that never completed also burned the redelivery that could have retried it.

### The root cause, and why the one passing row is not reassuring

`_bounded_lock_wait` read `SHOW lock_timeout` **before** issuing `SET LOCAL lock_timeout`.
`Session.execute` autoflushes, so that read could push out whatever the caller had pending
— on a booking turn, the dedup-cursor `UPDATE` on `whatsapp_thread_states` — **before the
bound existed**.

On the granted path it happened not to, and the row above is green. Nothing in the code said
it must not. The refusal and replay paths never reached `begin_offer_transaction()` at all,
so their first write — a counter `UPDATE` — arrived with no bound in scope.

**The error was treating admission's three answers as three paths.** All three *persist*
something: a refusal counter, a replay counter, or a booking. All three are therefore
writers, and a writer enters the order before it writes.

---

## 2. What changed

| # | Change | Finding |
|---|---|---|
| S1 | `_bounded_lock_wait` suppresses autoflush around its **own** preparatory `SHOW`/`SET` pair and around the restore, then flushes the caller's pending writes **deliberately, inside the bound** | RV-01 |
| S2 | `ConversationEngine._process_flow_response` takes the bounded entry **before** the replay and refusal branches. One acquisition serves all three outcomes | RV-01 |
| S3 | A contended turn writes **nothing**: no counter, no cursor, no `needs_human`, and it no longer records an out-of-band refusal. It returns `ACTION_THREAD_CONTENDED` | RV-01 |
| S4 | `BookingFlowService.handle_confirm_booking` takes the same entry before admission, and its redundant second entry is gone | RV-01 |
| S5 | `OFFER_WRITERS` declares all **ten** persisted writers of `whatsapp_booking_offers`; `record_replay`, `_refuse`, `record_refusal` and `record_refusal_out_of_band` now enter the order | RV-02 |
| S6 | `record_refusal_out_of_band` gets a **clean** bounded transaction: rollback first, `no_autoflush` read to learn the thread, bounded acquisition, then write — or write nothing and answer `False` | RV-02 |
| S7 | `BOOKING_PROJECTION_FK` is declared once beside the model and imported by the migration | RV-04 |
| S8 | `_OFFER_FIELDS` no longer contains `token_fingerprint`; the shared accessor emits `token_fingerprint_prefix` | RV-05 |
| S9 | `resolve_context` is read-only again; `_count_replay_at_command_boundary` counts the replay at the one caller that is a command | RV-07 |

Two further notes on S5. `claim` is on the list because it issues the ACCEPTED transition
itself; it enters through `_claim_checks`, which is on the list in its own right because
`_refuse` writes from inside it. And the list is now **executable**: a test walks it, so a
tenth writer added without the acquisition fails the gate instead of silently widening the
surface.

On the two replay paths that re-enter after a rollback (`_booking_projection_replay` and the
Data Exchange equivalent) the counter is **skipped on contention** and the authoritative
booking is still returned. The booking is the fact the customer depends on; the counter is
bookkeeping, and waiting for it would reintroduce the wait this milestone closes.

---

## 3. After

Same probe, same 12-second hold, same five classes:

| submission class | before | after | typed | cursor | offer | sent |
|---|---|---|---|---|---|---|
| unknown token | 72.002s | **4.172s** | `thread_contended` | intact | untouched | nothing |
| withdrawn offer | 72.009s | **4.015s** | `thread_contended` | intact | untouched | nothing |
| superseded offer | 72.007s | **4.016s** | `thread_contended` | intact | untouched | nothing |
| granted acceptance | 4.025s | **4.050s** | `thread_contended` | intact | untouched | nothing |
| replay | 72.005s | **4.016s** | `thread_contended` | intact | untouched | nothing |

`DEFAULT_OFFER_LOCK_TIMEOUT_MS` is 4000. Every class now answers at the bound.

And the turn is **declined, not lost**: after the holder releases, the same submission
retried in a fresh session produced `booking_created` and exactly one booking.

---

## 4. Verdicts

Twenty-eight, stated separately.

### A — the defect, per submission class

**V-01 — unknown token. CLOSED.** 72.002s → 4.172s, typed, cursor intact.

**V-02 — withdrawn offer. CLOSED.** 72.009s → 4.015s. This is the review's case and the
common one: withdrawal follows any vehicle or location correction.

**V-03 — superseded offer. CLOSED.** 72.007s → 4.016s.

**V-04 — granted acceptance. HELD, and no longer by accident.** 4.025s → 4.050s. G3-7R's
one green row was a property of which statement SQLAlchemy chose to flush on. The ordering
is now structural, and `test_r2_pg_04` is the regression guard for it.

**V-05 — replay. CLOSED. Not in the review's scope; found here.** 72.005s → 4.016s. The
review measured three classes; the probe built for this milestone measured five, because
a replay persists a counter and is therefore a writer too.

### B — the contention contract

**V-06 — the outcome is typed. CONFIRMED.** All five classes return
`ACTION_THREAD_CONTENDED`. Contention is not a refusal and not an error.

**V-07 — the dedup cursor is not consumed. CONFIRMED.** Four of the five classes consumed
it before; none does now. A redelivery is free to try again against the offer's then-current
state.

**V-08 — the offer is not mutated. CONFIRMED.** No status change and no counter movement on
any contended turn, on either transport. Asserted on persisted rows.

**V-09 — nothing is booked and no canonical revision is written. CONFIRMED.**

**V-10 — no handoff is declared. CONFIRMED.** `needs_human` is unchanged on both the thread
state and the lead. Declaring a human handoff for a submission nobody has judged would be a
false statement, and that path's own commit would block on the row the other writer holds.

**V-11 — nothing is sent. CONFIRMED.** Zero send attempts and zero new `direction='out'`
ledger rows during contention.

**V-12 — the turn is declined, not lost. CONFIRMED.** The retry after release books, once.
This is the property that separates a bound from a dropped customer turn.

### C — the review's findings

**V-13 — G37R-RV-01 (HIGH, blocking). CLOSED.** By measurement, on five classes, on real
PostgreSQL locks, against the same hold the review used.

**V-14 — G37R-RV-02 (MEDIUM). CLOSED.** The claim "every writer takes resource 1" is now
true of all ten, declared in `OFFER_WRITERS`, enforced by each writer, and checked both
structurally and behaviourally.

**V-15 — G37R-RV-04 (LOW). CLOSED.** `create_all` and Alembic now both produce
`fk_thread_revisions_offer_id`, verified on two separately built databases.

**V-16 — G37R-RV-05 (LOW). CLOSED.** `_OFFER_FIELDS` cannot carry the full fingerprint, so
neither the page nor `/api/ops/offers` can emit it.

**V-17 — G37R-RV-07 (LOW). CLOSED.** `resolve_context` neither counts nor commits; the
command boundary does both, inside the canonical order.

### D — proof

**V-18 — new PostgreSQL suite. 9 passed, 0 failed.**
`tests/test_l4_7w5_g3_7r_r2_refusal_bounds_pg.py`, R1–R9: the five classes, the retry, the
declared-writer walk, the real refusal turn's statement order, and the Data Exchange command.

**V-19 — all three PostgreSQL suites together. 122 passed, 0 failed, 0 skipped**
(new 9 + G3-7R projection 8 + G3-7P 105), 74 subtests passed.

**V-20 — sealed oracles. 9 added, 2 strengthened.** `R2BoundsCoverEveryExit` (9) plus
`test_g37r_view_02b` for RV-05, and `test_g37r_13` extended to assert that the caller's
pending writes flush *under* the bound and that both autoflush suppressions exist.

**V-21 — required mutations, sealed detection. 9 of 9 detected.** Each is a revert of one
line of this fix, applied to a `git archive` of the commit. See §5.

**V-22 — required mutations, PostgreSQL detection. 3 of 6 attempted. PARTIAL, AND
DISCLOSED.** Mutations 3, 6 and 7 are caught behaviourally. Mutations 1, 2 and 4 survive the
new PostgreSQL suite, for a reason worth reading before this commit is trusted: §5 states
it, names what is therefore proven, and names what is only structurally guaranteed.

**V-23 — migration. CONFIRMED.** On a parent-schema database stamped at
`20261010_offer_lifecycle`: upgrade creates `fk_thread_revisions_offer_id` and
`uq_thread_revision_one_booking_per_offer`; downgrade removes the constraint and the column;
re-upgrade succeeds; one head, `20261013_booking_projection`.

### E — invariants and scope

**V-24 — sealed regression. IDENTITY-IDENTICAL to `9181966`.** See §6, including a
correction to how the G3-7R baseline was extracted.

**V-25 — M2 outbound invariants. UNTOUCHED.** No new writer of `direction='out'` outside
`outbound_safety_gate.py` (the two other matches are comments), no `gate.attempt()` call
site changed, no `path_id` changed, no secret added to a tracked file,
`CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED` untouched, `OUTBOUND_ENABLED=false`.

**V-26 — nothing was deployed. CONFIRMED.** No push, no image build, no migration applied to
`crm` or `crm_test`, no container restarted (all restart counts 0; backend still
`ridecheck-crm-backend:w5g3-1-840bce0`). All work used disposable databases
(`r2_before`, `r2_after`, `r2_mig`).

**V-27 — out of scope, and confirmed untouched.** G37R-RV-03 (`tester_reset` `23503`),
G37R-O1 (three deliveries of one `wa_message_id`), G37R-RV-06 (the G3-7R closeout's own
omissions), G37R-RV-08 (the M4 observation), Track B / SB2B, the convergence decisions,
customer-facing copy, the data model beyond the FK name, and the phrase rules. No deploy, no
Wild.

**V-28 — defects I found in my own work, disclosed. 5, one of them still open.** See §7.
D1–D4 are closed. **D5 is open**: no test in this commit can fail on S4 alone.

---

## 5. Mutations

Nine required mutations, each a revert of one line of this fix, applied to a `git archive` of
the commit this closeout describes. "Sealed" is `test_l4_7w5_g3_7r_unification.py` + `test_l4_7w5_g3_7p_offer_authority.py`;
"PG" is the new bound suite against a disposable PostgreSQL database.

| # | Mutation | Sealed | PG |
|---|---|---|---|
| 1 | `_bounded_lock_wait`: preparatory statements autoflush again | **DETECTED** — `test_r2_09`, `test_g37r_13` | **survives** — 9 passed |
| 2 | `_bounded_lock_wait`: the in-bound `flush()` removed | **DETECTED** — `test_r2_09`, `test_g37r_13` | **survives** — 9 passed |
| 3 | CE: bounded entry moved back below the branches | **DETECTED** — `test_r2_03` | **DETECTED** — 6 failed |
| 4 | DX: bounded entry removed | **DETECTED** — `test_r2_05` | **survives** — 9 passed |
| 5 | CE: a contended turn records a refusal again | **DETECTED** — `test_r2_04` | not attempted |
| 6 | `record_replay` stops entering the order | **DETECTED** — `test_r2_01` | **DETECTED** — `test_r2_pg_07` |
| 7 | `record_refusal` stops entering the order | **DETECTED** — `test_r2_01` | **DETECTED** — `test_r2_pg_05`, `test_r2_pg_07` |
| 8 | the full fingerprint restored to `_OFFER_FIELDS` | **DETECTED** — `test_g37r_view_02b` | not attempted |
| 9 | `resolve_context` counts and commits again | **DETECTED** — `test_r2_06` | not attempted |

Mutations 5, 8 and 9 concern WHAT is written, not WHEN, so no PostgreSQL oracle was
attempted for them; their sealed oracles assert the thing itself.

### The honest part: three mutations survive the behavioural suite

Reverting either half of the `_bounded_lock_wait` hardening (1, 2), or the Data Exchange
entry (4), leaves the new PostgreSQL suite at **9 passed**. That is not a gap in the suite.
It is a fact about the fix, and it changes what this commit may claim.

**Mutations 1 and 2.** Once S2 and S4 are in place, every booking-shaped turn reaches
`begin_offer_transaction()` **before it has anything pending to flush**. So at today's call
sites the autoflush ordering decides nothing, and S1 is defence in depth rather than the
operative fix. The operative fix is S2 — and mutation 3 is caught behaviourally, with six
tests failing.

**Mutation 4.** With the Data Exchange entry removed, the command still answers
`OFFER_CONTENDED` in ~4s. Measured cause: the refusal is raised by the binding verification
further down, at `booking_flow_service.py:871`, which reaches resource 1 through
`_claim_checks`. And a Data Exchange submission that admission *refuses* is bounded too,
because S5 brought `record_refusal` itself under the order. So S4 is redundant **for
bounding**; what it still buys is answering before any write at all, and one acquisition per
transaction instead of several.

**What this commit therefore claims.** S2 is the proven correction, by measurement on five
submission classes. S5 is proven by `test_r2_pg_07`. S1 and S4 are **structural guarantees
whose behavioural necessity is not demonstrated at the present call sites** — kept because
the measurement that failed the review was produced by exactly the ordering S1 fixes, and a
future caller with pending work would reproduce it. A reviewer should read them that way and
not as load-bearing.

An earlier version of the `test_g37r_13` oracle asserted `autoflush_suppressed >= 1` and
**passed under mutation 1**, because the restore block still entered one suppression. It now
requires both. That is disclosed as D4 in §7.

---

## 6. Regression

Sealed runner, SQLite in-memory, `--network none`:

```
docker run --rm --network none -e DATABASE_URL="sqlite://" \
  -v /opt/ridecheck-crm-release-candidate:/work:ro -w /work/backend \
  ridecheck-crm-test:l4.7b2 python -m pytest /work/tests -q -rA -p no:cacheprovider
```

| | `9181966` (G3-7R) | `3a2eeb6` (this commit) |
|---|---|---|
| summary line | 61 failed, 6252 passed, 231 skipped, 9 errors | 61 failed, **6262** passed, **240** skipped, 9 errors |
| failure identities | **70** | **70** |
| NEW | — | **0** |
| GONE | — | **0** |

The ten extra passes are this milestone's sealed tests (nine in `R2BoundsCoverEveryExit`
plus `test_g37r_view_02b`). The nine extra skips are the new PostgreSQL suite declining to
run without `G37R_PG_URL` — which is the point of skipping loudly: a concurrency claim that
did not run is not a weaker claim, it is no claim at all.

### A correction to the G3-7R baseline figure

The G3-7R closeout and its independent review both reported **69** failure identities. That
number was produced by an extraction that matched `^FAILED` and `^ERROR` only, and the
baseline log also contains one `SUBFAILED` line:

```
SUBFAILED(fn='_withdraw_dependent_proposals')
  tests/test_l4_7w5_g3_7f_owner_resolution.py::J_AccountingAndArchitecture::test_or_73_no_booking_revision_is_modified
```

The correct baseline is **70**. Re-extracted with `^(FAILED|ERROR|SUBFAIL)` on both sides,
the comparison is identity-identical. Had the first run of this milestone's regression been
compared against the published 69, it would have reported a false new failure — and the
function in question is byte-identical between the two commits, which is how the extraction
error was caught rather than chased.

The `69 = 69` claim in the G3-7R closeout and in the independent review is therefore
**an undercount of one on both sides**, not a changed result.

---

## 7. Defects I found in my own work

Five. Four closed; D5 is disclosed and open.

**D1 — the review's scope was too narrow, and I nearly inherited it.** The findings named
three refusal classes. The probe written for this milestone measured five, and the replay
path turned out to be affected identically (72.005s). Had I fixed only what was reported,
`c5` would still be open. **Closed.**

**D2 — the one green row in the before-table was an accident, and a plan built on it would
have been wrong.** The granted path was bounded because `SHOW lock_timeout` happened not to
autoflush. My first reading of the root cause implicated the granted path too; the
measurement contradicted it. The fix is written for what the measurement showed, and the
accident is now a regression guard. **Closed.**

**D3 — `claim` was missing from my own declared writer list.** The first `OFFER_WRITERS` had
nine names, derived from the review's inventory. A test that re-derives the list from the
code found `claim` writing the offer table directly. The list has ten names, and the
derivation test is now part of the suite. **Closed.**

**D4 — my first mutation oracle for S1 was too weak and passed under its own mutation.**
`autoflush_suppressed >= 1` was satisfied by the restore block alone. Strengthened to
require both suppressions, and re-run. **Closed.**

**D5 — my first Data Exchange bound test did not test what I thought it did.**
`test_r2_pg_09` submits a GRANTED token, and under mutation 4 it still passed because the
bound arrives later, from the binding verification's own acquisition. I measured that cause
(`booking_flow_service.py:871`, `OFFER_CONTENDED` in ~4s) rather than leaving the green tick
to stand for a claim it does not support. The test is kept — the Data Exchange command IS
bounded, which is worth asserting — and §5 now states plainly that S4 is not what bounds it.
**Disclosed, not closed:** no test in this commit can fail on S4 alone, because S5 makes it
redundant.

---

## 8. Known-open, carried forward

Not touched by this milestone, and not claimed closed by it:

| Item | Severity | Owner |
|---|---|---|
| G37R-RV-03 — `reset_tester_to_zero_state` fails `23503` once an offer exists; the new FK closes the cycle so a reorder cannot fix it | MEDIUM | required before any Wild |
| G37R-O1 — a fresh session per delivery means three deliveries of one `wa_message_id` produce three identical engine send attempts; cursor never written | MEDIUM | launch blocker, pre-existing on the parent |
| G37R-RV-06 / RV-08 — the G3-7R closeout's own omissions and the M4 claim | LOW | artifact corrections |
| S1's behavioural necessity | observation | §5 |
| SB2B convergence: idempotency root (`token_fingerprint` vs `flow_idempotency_key`) and the replay source of truth | decision | after this review |

---

## 9. Files

```
backend/app/models.py                                    BOOKING_PROJECTION_FK, named FK
backend/app/routes/ops_dashboard.py                      fingerprint minimised in the accessor
backend/app/services/booking_flow_service.py             bounded entry, read-only resolution,
                                                         command-boundary replay counter
backend/app/services/conversation_engine.py              one bounded entry before all three
                                                         admission outcomes
backend/app/services/offer_lifecycle.py                  autoflush ordering, OFFER_WRITERS,
                                                         four writers brought under the order
backend/app/ui/offer_authority_view.py                   reads the minimised prefix
backend/migrations/versions/20261013_booking_projection.py  imports the shared FK name
tests/test_l4_7w5_g3_7p_offer_authority.py               strengthened bound oracles
tests/test_l4_7w5_g3_7r_unification.py                   R2BoundsCoverEveryExit, view_02b
tests/test_l4_7w5_g3_7r_r2_refusal_bounds_pg.py          NEW — R1–R9 on real PostgreSQL
```

## 10. How to reproduce

```bash
# sealed
docker run --rm --network none -e DATABASE_URL="sqlite://" \
  -v /opt/ridecheck-crm-release-candidate:/work:ro -w /work/backend \
  ridecheck-crm-test:l4.7b2 python -m pytest /work/tests -q -rA -p no:cacheprovider

# the bounds, on a DISPOSABLE database (never crm or crm_test)
createdb r2_after   # then build the schema with Base.metadata.create_all
docker run --rm --network ridecheck-crm_default \
  -e DATABASE_URL="$DISPOSABLE" -e G37R_PG_URL="$DISPOSABLE" \
  -e OPENAI_API_KEY="" -e OUTBOUND_ENABLED=false \
  -v /opt/ridecheck-crm-release-candidate:/work:ro -w /work/backend \
  ridecheck-crm-test:l4.7b2 \
  python -m pytest /work/tests/test_l4_7w5_g3_7r_r2_refusal_bounds_pg.py -q -rA
```

`-w /work/backend` is required: without it a baked `/app/app` package shadows the worktree.

---

## 11. This artifact

| | |
|---|---|
| Parent | `9181966d907add21d1acb9d26f2e74c1c7de0bf0` |
| Commit | the ONE commit on top of that parent on `fix/m21.1.1-primary-flow-regression` |
| Resolve it with | `git rev-parse 9181966..HEAD` (or `git log --oneline -1`) |
| Closeout path | `docs/Launch/2026-10-13_RIDECHECK_CRM_L4.7W5-GATE3-G3-7R-R2-REFUSAL-PATH-BOUNDS_CLOSEOUT_001.md` |
| Verdicts | 28, §4 |
| Disclosed defects | 5, §7 (D5 open) |

G37R-RV-06 found the G3-7R closeout omitting its own SHA and not ending with `STOP`. The
`STOP` is below. The SHA is **not printable inside the commit that contains this file**: a
commit's hash covers its own tree, so writing the hash in changes it. Naming the parent and
the single-commit relationship is the resolvable form, and the SHA itself is reported in the
delivery message and recorded in memory.

---

NEXT: L4.7W5-GATE3-G3-7R-R2-INDEPENDENT-REVIEW

STOP
