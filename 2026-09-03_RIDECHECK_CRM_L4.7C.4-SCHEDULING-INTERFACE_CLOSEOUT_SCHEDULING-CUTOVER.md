PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7C.4-SCHEDULING-INTERFACE

# L4.7C.4 — one interpretation of the request, one resolver for the date

Date: 2026-09-03
Scheduling *language* only · flag-guarded and reversible · crm_test only · C2 and C3B
authority unchanged and still ON · availability, business hours, travel and booking untouched
· OUTBOUND OFF · production untouched.

---

## 1. Verdict

**CONDITIONAL_PASS.** Every gate line passes and the interpretation layer is authoritative,
single-pathed and reversible. The condition is one of scope, stated plainly rather than
buried: **the synchronous evidence producer is still the deterministic parser**, so the
semantic interpreter's advantages on scheduling language are not yet realised live (§3).

## 2. What moved

| | |
|---|---|
| **Moved** | requested branches, PRIMARY/FALLBACK/ADDITIONAL order, relative-day meaning, stated time, flexible-time meaning, time bands, scheduling corrections, **and the calendar arithmetic** |
| **Not moved** | business hours, travel, technician constraints, occupied slots, slot offers, booking creation |

`backend/app/services/scheduling_reconciler.py` imports no ORM, no `ScheduleService`, no
travel provider and no booking symbol — asserted by test. It is structurally incapable of
saying a slot is available or that anything is booked.

The four stages stay apart:

```
SEMANTIC   what preference was expressed      RESOLVER   what date that expression means
SCHEDULE   what is actually possible          BOOKING    what was confirmed
```

## 3. The honest boundary

The semantic interpreter runs **asynchronously off the customer turn** (L4.7B.2, and
deliberately so — it added 2.4 s to every turn when it was inline). It is therefore not
available synchronously inside the CE turn, and C4 does not block the turn to wait for it.

So today the chokepoint is fed by the deterministic extractor, projected into the *same*
claim shape, and the reconciler applies the ordering, clause-locality, flexibility and
resolution rules to it. When the semantic producer becomes synchronously available it feeds
the identical interface as a `SEMANTIC_INFERRED` claim and the reconciler treats it the same.

**What this milestone genuinely delivers:** one interpretation path, one deterministic
resolver, ordered branches that no later stage may reorder, and a reconciler that cannot
express availability. **What it does not yet deliver:** semantic-only readings such as bare
"mañana" beside a weekday name — the legacy extractor suppresses that pairing, so
"mañana 15 o jueves" still collapses to one branch, exactly as it does today. That is
unchanged behaviour, not a regression, and it is the first item for whoever makes the
semantic producer synchronous.

## 4. Legacy versus reconciled (Part 12)

16 utterances, identical inputs, both paths:

| | |
|---|---|
| AGREE | **16** |
| NEW_SAFER | 0 |
| **LEGACY_SAFER** | **0** |
| **UNEXPLAINED** | **0** |

Branch order, resolved date, time and flexibility match on every case, including
`qué horarios tienen?` and `después te confirmo`, which produce **no request at all** in both.

## 5. The deterministic resolver (Part 2 & Part 15)

One function, no model reasoning, verified across boundaries:

| From | Expression | Resolved |
|---|---|---|
| Sat 2026-09-05 | TOMORROW | 2026-09-06 (Sunday — resolution is not availability) |
| 2026-09-30 | TOMORROW | 2026-10-01 |
| 2026-12-31 | TOMORROW | 2027-01-01 |
| 2028-02-28 | TOMORROW | 2028-02-29 |
| Wed 2026-09-02 | WEDNESDAY | 2026-09-09 (a weekday name never means today) |
| Wed 2026-09-02 | THURSDAY | 2026-09-03 |
| Mon 2026-08-31 | DAY_AFTER_TOMORROW | 2026-09-02 |

Resolving a Sunday is correct behaviour: the resolver says *which date the customer meant*;
`ScheduleService` says it is closed.

## 6. WILD-A-04 (Part 13)

*"Mñ 15hs? O nose jueves que tenes"*, from Monday 2026-08-31, on the deployed image:

```
PRIMARY   2026-09-01  15:00   flexible_time = false
FALLBACK  2026-09-03  —       flexible_time = true
```

Order preserved, time clause-local, no booking, no availability claim. **PASS.**

## 7. Live runtime probe, all four authority flags ON

```
Mñ 15hs? O nose jueves que tenes   [(2026-09-01, 15:00), (2026-09-03, None)]
mañana a las 15                    [(2026-09-01, 15:00)]
jueves                             [(2026-09-03, None)]
jueves a la tarde                  [(2026-09-03, None)]      ← a band is not a time
qué horarios tienen?               (no request)              ← no day invented
después te confirmo                (no request)              ← future intent is not a slot
```

`whatsapp_thread_candidates` 0 → 0 · `whatsapp_messages` 6 → 6 · `thread_revisions` 0 → 0.

## 8. Business configuration preserved (Part 8)

Asserted against the real implementation, not restated from the brief: `SERVICE_MINUTES = 45`;
Monday 13–18, Tuesday 09:30–14, Wednesday 09–18, Thursday 09–14, Friday 09–18, Saturday
09–15, Sunday closed; travel same-group 30, CABA↔other 60, cross-GBA 90.

**One discrepancy with the brief, reported rather than "fixed":** the brief lists "missing
groups fallback = 30"; the certified implementation returns **0** for a missing group ("no
constraint applied"), and an *unknown pair* falls back to 90. L4.7C.4 changed neither — this
is schedule policy and out of scope — but the difference should be settled deliberately.

## 9. Single interpretation path (Part 11)

Both multi-branch call sites route through `_reconciled_scheduling_requests`, and an AST test
walks the whole engine and fails if `_parse_scheduling_requests` is called anywhere outside
the chokepoint. The legacy helpers keep running **inside** it as the evidence producer:
`_parse_scheduling_requests`, `_scan_day_tokens`, `_scan_time_tokens`, `_detect_time_period`
are all EVIDENCE_PRODUCER / VALIDATOR, none of them writes a competing canonical request, and
none was deleted — retirement is C6.

With the flag off, `_reconciled_scheduling_requests` returns the legacy parse unchanged, and
both positions produce identical branches on every live example.

## 10. A fourth reload-identity defect, found and fixed

The stance projection compared `AcceptanceSignal` **by identity**, so after another suite
reloaded the schema module every stance silently vanished and acceptance authorization
returned HOLD for a valid "Sí avancemos". Caught only because the full-suite run disagreed
with the isolated run. Now compared by value — the fourth occurrence of this hazard in the
programme (`AcceptanceSignal` in L4.7B.2, `CorrectionRelation` in L4.7B.4, the record classes
in L4.7C.1, and now this). **Any future enum or class captured at import time should be
treated as suspect.**

## 11. Tests and regression

`tests/test_l4_7c_4_scheduling_interface.py` — **22/22 PASS** (SCHEDCUT-01…20 plus
availability-question and both-flag-positions-agree).

Regression: **3 525 passed / 60 failed / 9 errors** with the C4 flag **OFF** and with **all
four authority flags ON** — failure set identical to baseline in both. Launch-relevant
failures: 0 new. Unknown: 0.

One C3B assertion was realigned deliberately: it pinned the direct `_parse_scheduling_requests`
call that this cutover routes through the chokepoint. It now asserts the parser still produces
the branches and that C3B still only gates the progression.

## 12. Boundaries held

C2 vehicle/location authority unchanged and still ON. C3B acceptance authority unchanged and
still ON — acceptance and scheduling coexist in one turn ("Sí avancemos, mañana a las 15
puede ser?" authorises acceptance **and** keeps the 15:00 request). Booking authority
unchanged: `ThreadRevision(status="booked")` still only from the Flow path. Pricing untouched.
L4.7D still grounds availability claims in canonical state.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.

Next: **L4.7C.5-DERIVED-STATE-INVALIDATION** — not automatic.
