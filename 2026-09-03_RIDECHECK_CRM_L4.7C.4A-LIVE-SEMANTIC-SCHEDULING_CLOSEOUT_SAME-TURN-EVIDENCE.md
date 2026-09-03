PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7C.4A-LIVE-SEMANTIC-SCHEDULING

# L4.7C.4A — the interpretation reaches the turn it is about, and costs one model call

Date: 2026-09-03 · crm_test only · OUTBOUND OFF · production untouched · no migration · no Wild
C2 and C3B unchanged and still ON · ScheduleService and Booking Flow untouched

---

## 1. Verdict

**PASS.** Same-turn semantic scheduling is live behind a flag, the branches L4.7C.4 could not
recover are recovered, and the whole thing costs **one** model call per inbound burst —
the number that decides whether this is an architecture or an expense.

The single condition recorded by L4.7C.4 is closed. Two things are reported rather than
smoothed over: the added wait on a scheduling turn (§7) and the travel-fallback discrepancy
that is still an owner decision (§11).

## 2. The gap, exactly (Part 1)

`_run_shadow_understand` runs at the very top of the turn, before any deterministic gate can
early-return. It captured provenance **synchronously** and handed the model call to a bounded
worker, because L4.7B measured ~2.4 s mean / 3.7 s p95 added to *every* turn when the call was
inline. Correct decision, with one consequence: the `TurnEvidence` landed on a worker thread
after CE had already chosen and answered. Nothing read it except the append-only record.

Measured proof of the cost, on the deployed image, today = Monday 2026-08-31:

```
'mañana 15 o jueves'  deterministic → [('2026-09-03', None)]
                      semantic      → [(TOMORROW, 15:00), (THURSDAY, open)]
```

The parser does not merely collapse the burst — it **drops the PRIMARY entirely** and keeps
only the fallback. The customer asked for tomorrow at 15:00 first, and the certified path
answered about Thursday.

### What C2, C3B and C4 actually consume — the question asked, answered plainly

**B — deterministic evidence only, all three.** Not prior semantic evidence, not another path:

| chokepoint | claim producer | evidence class |
|---|---|---|
| `_apply_vehicle_identity` (C2) | legacy parsers / flow / form / catalog | `DETERMINISTIC_EXTRACTED`, `HUMAN_CONFIRMED`, `CATALOG_CONFIRMED` |
| `_apply_inspection_zone` (C2) | zone resolver | same |
| `_authorize_acceptance` (C3B) | `ce:_is_acceptance` | `DETERMINISTIC_EXTRACTED` |
| `_scheduling_claims_from_texts` (C4) | `ce:_parse_scheduling_requests` | `DETERMINISTIC_EXTRACTED` |

**C2 affected materially: NO. C3B affected materially: NO** — and the reason matters. Neither
was certified on a claim that it consumed semantic evidence; both were certified on
deterministic producers plus named reconciliation rules, and every gate they passed remains
true. The async boundary bounded their *potential*, it did not invalidate their *evidence*.
So this was not a STOP condition, and scope was not broadened: the same interface is now
available to them, and using it is a later milestone's decision, not this one's.

## 3. The design (Part 2)

```
RAW BURST → TurnSemanticEvidence.start() → ONE interpret() → { the turn · claims ·
                                                               reconciler · shadow record }
```

`backend/app/services/semantic_turn_evidence.py` is a single-flight future. `start()`
dispatches once. `get()` returns *that* result to whoever asks. The rejected alternative — a
synchronous call for CE plus the async shadow call — was excluded by the milestone and is
worse than double cost: it lets the forensic record disagree with the decision it records.

CE now submits **one** job per burst which runs the interpretation and then writes the record,
so a turn that needs the evidence waits on the model and not on the append-only write behind
it. Whoever arrives first pays for the wait; nobody pays twice.

## 4. Single-call proof (Part 8)

`calls` is a counter on the provider, incremented inside the run guard.

* **16/16 live bursts: `MAX_MODEL_CALLS_PER_BURST = 1`** — with authority consuming the
  evidence, the claim projection consuming it, the reconciler consuming it and the shadow
  recorder writing it.
* LIVESEM-02 asks five more times after the turn: still 1.
* LIVESEM-02b races four threads at a cold provider: still 1.
* LIVESEM-13 walks the whole turn — authority, projection, reconciliation, record: still 1.

## 5. Live value (Parts 6, 7, 12)

Legacy vs reconciled, identical inputs, real model, deployed image, 16 utterances:

| | |
|---|---|
| AGREE | **14** |
| **NEW_SAFER** | **2** |
| **LEGACY_SAFER** | **0** |
| **UNEXPLAINED** | **0** |

The two NEW_SAFER cases are the whole point:

```
'mañana 15 o jueves'      legacy [('2026-09-03', None)]
                          live   [('2026-09-01','15:00'), ('2026-09-03', None)]
'mañana 15 o jueves 10'   legacy [('2026-09-03', None)]
                          live   [('2026-09-01','15:00'), ('2026-09-03','10:00')]
```

The second is the stronger result: two branches, each with **its own** time. The 15:00 is not
transplanted onto Thursday and the 10:00 is not transplanted onto tomorrow.

**WILD-A-04** — *"Mñ 15hs? O nose jueves que tenes"* — same-turn, on the deployed image:

```
PRIMARY   2026-09-01  15:00  flexible=False
FALLBACK  2026-09-03  —      flexible=True     source=semantic   calls=1
```

Nothing was invented where nothing was said: `qué horarios tienen?`, `después te confirmo`,
`Si avancemos`, `cuánto sale la revisión?` and `un Focus 2019 en Palermo` all produce
**no scheduling request at all** (`source=none`), on both paths.

## 6. Producer precedence — the rule that is not "the model wins" (Part 5)

Both producers read the **same** burst, so "last writer wins" is meaningless: neither is
later. `reconcile.scheduling_preference.v2` asks the only answerable question — do they
disagree?

* every deterministic branch must appear in the semantic reading, same resolved date, **same
  relative order**;
* a deterministic branch with no stated time is satisfied by any time — *absence is never a
  contradiction*, the same invariant the information-state model rests on;
* a deterministic branch with a **different** stated time, a deterministic branch the model
  **lost**, or a **reordering**, is a real contradiction.

Enrichment is accepted (`source=semantic`). A contradiction keeps the certified deterministic
reading and is recorded as `source=deterministic_conflict` — never silently resolved in the
model's favour. The legacy parsers keep running as evidence producers and validators, and
none of them was deleted; retirement is C6.

One related hardening: flexibility is now derived from the **fact** that no time was stated,
not taken on the model's word, and priority is compared by value.

## 7. Latency, not hidden behind an average (Parts 3, 12)

**Which turns wait:** only a turn that reaches the scheduling chokepoint with the flag on.
Every FAQ, motorcycle, phone-call, vehicle, location, pricing and acceptance turn returns
without waiting because it never gets there. **Which turns do not wait:** all of those, plus
any turn where the interpretation has already landed.

Semantic call, n=12 on crm_test: **mean 1 700 ms · p50 1 767 ms · p95 2 055 ms · max 2 055 ms**
(min 1 134 ms). Better than the 2.4 s / 3.7 s L4.7B measured inline.

End-to-end impact, async shape, n=16 — the wait actually added at the chokepoint:

| | |
|---|---|
| turn-top dispatch cost | 0–2 ms (61 ms on the first call, thread spin-up) |
| mean added wait | **1 717 ms** |
| p50 | **1 477 ms** |
| p95 | **4 548 ms** — and that is the single cold-start observation |
| p95 excluding cold start | **2 263 ms** |
| max | 4 548 ms |

Stated plainly: one turn in this sample cost 4.5 s, and it was the first — worker spin-up plus
connection setup. Every other turn cost 1.1–2.3 s. **Acceptable for WhatsApp UX**: the n8n
transport already holds a 20-second debounce before CE is called at all, so ~1.5 s of
interpretation is a fraction of a delay the customer is already inside. Timeout is 6.0 s,
about 2.9× the measured p95.

## 8. Failure is absence, never a guess (Part 4)

| failure | behaviour |
|---|---|
| timeout | `get()` returns None → deterministic evidence only → the L4.7C.4 certified reading |
| model error / HTTP failure | interpretation is None → same |
| invalid schema (`ok=False`) | not evidence → same |
| shadow queue full | the turn that needs it runs it inline; a backlog costs records, never correctness |
| late result | never applied — a turn already decided is not re-decided |

There is no retry, no second model, no "we didn't detect X, therefore proceed". A timeout does
not become a booking, an offer, or an assumed day.

## 9. Boundaries (Parts 9, 10, 11)

`scheduling_reconciler.py` and `semantic_turn_evidence.py` contain no `ScheduleService`, no
travel provider, no ORM session, no `ThreadRevision`, no booking symbol — asserted by test on
docstring-stripped code. The semantic layer supplies branches, relative day, time, priority,
flexibility and correction; it **cannot express** availability, business hours, travel,
occupied slots or booking state.

`REQUESTED` (reconciled preference) · `AVAILABLE` (ScheduleService) · `BOOKED` (Flow) stay
three layers: every branch is `is_request_only`, the record carries `requested_only: True` and
has no availability key, and `status="booked"` is still written by `_process_flow_response`
alone. Context stays bounded to the current cycle — date, weekday, timezone, stage, pending
clarification, offered slots and the previous **in-cycle** customer turn; claims carry
`cycle_id`, so a finished cycle can never be read as this one.

## 10. Tests and regression (Parts 14, 17)

`tests/test_l4_7c_4a_live_semantic_scheduling.py` — **27/27 PASS** (LIVESEM-01…18 plus
concurrency, declined dispatch, precedence, flexibility-is-a-fact and defaults-are-off).

Full regression, **both flag positions** (all four authority flags + C4A ON, and all OFF):
**3 553 passed / 59 failed / 9 errors**, failure sets **byte-identical between the two runs**.
**0 new failures.** Launch-relevant failures: 0. Unknown: 0.

One honest note on the count: the C4 baseline was 60 failures, this run has 59. The single
difference is `test_d1_reset_tool_compiles`, an environment-dependent `py_compile` of a script
this milestone does not touch — a `__pycache__` artefact of the container, not a fix. It is
not claimed as one.

## 11. Travel fallback — recorded, unchanged (Part 13)

| | |
|---|---|
| brief/canonical expectation stated previously | missing-group fallback = **30** |
| current certified implementation | missing group → **0** ("no constraint"); unknown pair → **90** |
| changed in this milestone | **no** |

`TravelProvider.get_travel_minutes` returns 0 when either group is empty, 30 for same-group,
the explicit CABA/NORTE/OESTE/SUR matrix otherwise, and 90 for an unrecognised pair. Business
policy is out of scope here. **OWNER DECISION PENDING.**

## 12. Runtime (Part 18)

Image `ridecheck-crm-backend:l4.7c4a-livesem-4ec8c43`, restarts 0, crm_test only.
`OUTBOUND_ENABLED=false` · `DATABASE_URL=…/crm_test` · vehicle, location, acceptance and
scheduling authority all `true` · `SEMANTIC_SAME_TURN_ENABLED=true` ·
`SEMANTIC_SAME_TURN_TIMEOUT_SECONDS=6.0` · `GIT_SHA=4ec8c43`.
sha256 source/runtime parity **MATCH** on all five touched modules.

crm_test rows across every probe: candidates 0 → 0 · messages 6 → 6 · thread_revisions 0 → 0.
Production database untouched; no migration; no Wild.

## 13. Rollback (Part 15)

`SEMANTIC_SAME_TURN_ENABLED=false` restores L4.7C.4's producer timing exactly: the turn never
waits, `_semantic_turn_evidence()` returns None, no semantic claim is built, and the shadow
record is still produced. Verified live (LIVESEM-18) and by the identical both-positions
regression. Default OFF everywhere; a `MagicMock` settings object cannot enable it.

---

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.

Next: **L4.7C.5-DERIVED-STATE-INVALIDATION** — not automatic.
