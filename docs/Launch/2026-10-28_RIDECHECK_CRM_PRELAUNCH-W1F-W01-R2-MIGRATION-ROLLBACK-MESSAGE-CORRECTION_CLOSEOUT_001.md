PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-W1F-W01-R2-MIGRATION-ROLLBACK-MESSAGE-CORRECTION

# The downgrade refusal now tells an operator the truth

**Date:** 2026-10-28 · **Branch:** `integration/prelaunch-booking-convergence` ·
**HEAD = origin =** `e0e83c39c0ee3c1f1dc2b391499468bac76146ba`, verified against the real
remote with `git ls-remote` before editing · **NOT committed, not pushed, no image rebuilt,
nothing migrated or deployed.**

**The defect, in one sentence.** A recovery-time `RuntimeError` told the operator "A CODE
rollback does not need this downgrade", which is true of a *behavioural* rollback and false
of an *image* rollback — and the operator reading it is deciding which image to start next.
The parent-head image cannot start against this schema; the message implied it could.

---

## 1. Scope — wording only, three paths

| path | change |
|---|---|
| `backend/migrations/versions/20261027_w1f_conversation_memory.py` | the refusal message, and the module docstring that made the same claim. **No logic, no DDL, no condition, no revision identifier touched** |
| `tests/test_w1f_w01_r2_outstanding_question.py` | `k02b` and `k02c` added (they execute the real paths), `k02`'s docstring corrected, and `_pg_drop` repaired |
| this closeout | new |

**No fourth path was necessary.** The observation that raised this was §13 item 1 of the
build-preparation closeout; it is closed here rather than by editing that document, which
would have been a fourth path for no gain.

---

## 2. The corrected message, as the operator now receives it

Produced by running the real `alembic downgrade -1` against a PostgreSQL 16.13 restore of
live `crm_test` at `20261027`, with one row in each table:

```
RuntimeError: 20261027_w1f_conversation_memory downgrade REFUSED:
whatsapp_outstanding_questions holds 1 row(s), whatsapp_thread_evidence holds 1 row(s).
Those rows are the record of what the customer was told and what the system decided about
what they said, and `superseded_by_id` means the history is not reconstructible from
canonical state. NO EVIDENCE ROW WAS DELETED and nothing was dropped. LEAVE THE DATABASE
AT 20261027_w1f_conversation_memory. A behavioural rollback keeps this schema and changes
the APPLICATION: it requires an image whose schema preflight accepts this head. Two are
valid fallbacks — the integrated image with EVIDENCE_ADMISSION_ENABLED,
OUTSTANDING_QUESTION_ENABLED and LOCALITY_RESOLUTION_SAFETY_ENABLED off, and the certified
compatibility image built for this head. The parent-head image
(20261022_operator_notifications) is NOT valid against this schema and will refuse to
start. Returning to 20261022_operator_notifications is a separate, separately authorised
operation: archive and remove the evidence rows first, then run this downgrade again.
```

Each required element, and where it is in that text:

| required | present as |
|---|---|
| refused because evidence rows exist | `downgrade REFUSED: … holds 1 row(s)`, per table, with counts |
| no evidence row was deleted | `NO EVIDENCE ROW WAS DELETED and nothing was dropped` |
| keep the database at `20261027` | `LEAVE THE DATABASE AT 20261027_w1f_conversation_memory` |
| behavioural rollback needs a schema-compatible image | `it requires an image whose schema preflight accepts this head` |
| both valid application fallbacks | the integrated image with the three flags off, **and** the certified compatibility image |
| the parent-head image is not valid | `The parent-head image (20261022_operator_notifications) is NOT valid against this schema and will refuse to start` |
| returning to `20261022` needs separate authorisation | `a separate, separately authorised operation: archive and remove the evidence rows first, then run this downgrade again` |

The revision identifiers are interpolated from `revision` and `down_revision`, so the
message cannot drift from the migration it belongs to.

---

## 3. The sweep

| occurrence | verdict |
|---|---|
| migration, refusal message | **corrected** (§2) |
| migration, module docstring — "so a CODE rollback needs no schema rollback at all" | **corrected**: it now says the flags restore committed behaviour **on an image that accepts this head**, that this does not make the parent-head image startable, and that returning to the parent schema means authorised archival first |
| `tests/…r2_outstanding_question.py`, `k02` docstring — "A CODE rollback does not need it" | **corrected**: `k02` now says it guards the *shape of the source* so a refusal cannot become a `DELETE`, and points at `k02b` for the executed contract |
| R2 closeout §3.5 — "(OC-R2-R1 struck 'a code rollback needs no schema rollback at all')" | **left**: it quotes the struck phrase in order to correct it |
| R2 closeout §14 — "'a code rollback does not need a schema rollback'; and" | **left**: §14 quotes both incompatible statements and resolves them; removing the quotation would remove the correction |
| build-preparation closeout §13 item 1 | **left**: it is the report of this defect, now closed by this milestone |
| `grep -ri "code rollback"` in the migration | **0 occurrences remain** |

No implementation was broadened: the refusal conditions, the row counts, the lock, the
candidate-column refusal and the drop order are unchanged.

---

## 4. The tests, which execute the path rather than read it

**`k02b` — the refusal, for real.** It seeds one row in *each* table, runs
`alembic command.downgrade(cfg, "-1")` in-process against the real server, and asserts on
the exception the operator would see: the refusal names both tables with counts; **both rows
survive**; the Alembic head is still `20261027`; both tables still exist; the message
contains all ten fallback-boundary phrases (as subtests, so a failure says which one went
missing); and `"code rollback"` appears neither in the message **nor in the migration
source**.

`migrations/env.py` takes `DATABASE_URL` from the environment and permits no fallback, so
the test sets it with `patch.dict` for the duration of that one call and nowhere else.

**`k02c` — the other side.** With both tables empty it runs the downgrade for real (tables
dropped, head back to `20261022`, both candidate columns gone), then re-upgrades and asserts
the column fingerprint is **identical** and `whatsapp_messages` is unchanged. Both schemas
are produced by the migration itself; an `addCleanup` re-upgrades unconditionally so a
failure cannot leave the shared database downgraded.

### 4.1 Two defects in my own fixtures, found by running them

1. **`_pg_drop` did not delete evidence rows.** `whatsapp_thread_evidence.thread_id` and
   `whatsapp_outstanding_questions.thread_id` are `NO ACTION` keys — deliberately, per the
   migration's docstring, because the certified tester reset must delete them explicitly. The
   helper knew about questions only, so `k02b`'s cleanup failed `23503` on the thread delete.
   Both tables are now cleared before the thread, and the helper says why.
2. **`k02b` asserted on table-wide counts.** The stale row left by that failed cleanup made
   the next run read `(2, 1)` and fail — correctly. The refusal message quotes the *table's*
   count, so the assertion is exact only if the fixture's rows are the only ones. `k02b` now
   requires both tables empty up front and **skips** otherwise: it must never be the thing
   that deletes somebody's evidence to make its own arithmetic work.

---

## 5. Results

| run | result |
|---|---|
| the real PostgreSQL downgrade-with-rows refusal | **refused**, both rows intact, head unmoved (`k02b`) |
| downgrade-empty and re-upgrade | **dropped, re-created, fingerprint identical**, business data unchanged (`k02c`) |
| complete R2 suite **with PostgreSQL** | **150 passed, 196 subtests passed** (was 148/186; +2 tests, +10 subtests) |
| R1 + both R3 suites, sealed | **154 passed, 668 subtests passed** |
| migration structure: single head, additive, vocabulary | **16 passed, 8 subtests** (`test_convergence_p4_r1`, `test_convergence_p4_strict_schema`) |
| `git diff --check` | clean, exit 0 |
| secret / live-identifier scan of the diff | **0** token shapes, **0** live secret values, **0** real WAMIDs, **0** non-synthetic destinations, **0** live host/IP |

---

## 6. An implication of authorising this commit

**The certified integrated image `w1fintegrated-e0e83c39` predates this correction and still
carries the old message.** Verified: running `alembic downgrade -1` from that image prints
the "A CODE rollback does not need this downgrade" text; running it from the corrected
worktree prints the text in §2. So authorising this commit means **rebuilding the integrated
candidate image** before the OC-0 window, or the artifact in the recovery path would be the
one with the misleading error.

**The compatibility rollback image is unaffected and remains certified and retained.** It is
built from `a6aabece`, which never contained this migration — the file is not in that tree at
all, so nothing in it changed and its `sha256:7e6779a5acce…` is still the certified artifact.

---

## 7. Isolation

| claim | state |
|---|---|
| `crm_test` | **`20261022_operator_notifications`**, 52 messages, max id 6239, 0 of the two new tables, 0 candidate provenance columns |
| live backend | `b340087e4cee`, `prewild-6a14cdb`, `sha256:73f835d3…`, started 2026-10-26T15:00:30Z, **restart 0** |
| live flags | the three behaviour flags **absent**; `OUTBOUND_ENABLED=false`, direct webhook `false`, Smart Booking `false` |
| Compose | not edited — both files at their prior sizes and mtimes |
| providers | none contacted; every test container ran `--network none` or on the `--internal` network `w1fnet2`, whose only other member was the disposable server |
| images | nothing rebuilt, nothing retagged; both certified images retained |
| `RECOVERY_MANIFEST.md` | untouched, 20 421 bytes, 2026-10-23 19:02 |
| unrelated untracked documents | 52, untouched |
| git | HEAD = origin = `e0e83c39`; 0 staged, 0 stash, 0 tags at HEAD; **no commit** |
| disposable resources | `w1f-msg-pg` and the network `w1fnet2`, removed by exact name |

---

## 8. Proposed commit — NOT executed

```
git add backend/migrations/versions/20261027_w1f_conversation_memory.py \
        tests/test_w1f_w01_r2_outstanding_question.py \
        docs/Launch/2026-10-28_RIDECHECK_CRM_PRELAUNCH-W1F-W01-R2-MIGRATION-ROLLBACK-MESSAGE-CORRECTION_CLOSEOUT_001.md
```

```
fix(prelaunch): clarify R2 migration rollback boundary
```

Three paths, exactly as scoped. **W1F-W-01 remains OPEN.** November 1 remains fixed.

NEXT: PRELAUNCH-W1F-W01-OC-0-MIGRATION-WINDOW-AND-INTEGRATED-DEPLOYMENT (with an integrated
image rebuilt on top of this correction) — OWNER AUTHORIZATION REQUIRED

STOP.
