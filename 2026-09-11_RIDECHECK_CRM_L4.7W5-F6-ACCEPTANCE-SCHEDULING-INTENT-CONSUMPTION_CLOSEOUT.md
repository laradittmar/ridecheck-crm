PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-F6-ACCEPTANCE-SCHEDULING-INTENT-CONSUMPTION

STATUS: PASS
DATE: 2026-09-11
CODE COMMIT: 37485ae
IMAGE: ridecheck-crm-backend:w5f6-acceptance-37485ae
SCOPE: crm_test only. Production untouched. OUTBOUND OFF. No Wild run.

---

## 1. Part 1 — how the burst was represented

`claim_projection.turn_modality(texts)` computes **one** temporality/modality from the
combined burst and every claim in the turn shares it. Its own docstring says so: *"Deliberately
coarse and deliberately shared by every claim in the turn."* That is right for most evidence
and wrong for acceptance.

Measured on the live burst:

```
["si"]                        -> PRESENT  FACTUAL       actionable
["para cuando tenes"]         -> FUTURE   CONDITIONAL
["si", "para cuando tenes"]   -> FUTURE   CONDITIONAL   NOT actionable
```

Three markers fired, all from the second sentence: `_FUTURE` matched, `_CONDITIONAL_MARKERS`
matched *cuando*, and `_si_is_conditional` read the bare "si" as an *if*. The
`QUOTE_ACCEPTED` claim inherited FUTURE/CONDITIONAL, failed `is_actionable_now`
(`claims.py:207`), and `authorize.quote_acceptance@v1` returned HOLD at
`acceptance_authorizer.py:239`.

The acceptance and the scheduling request were already separate evidence items —
`evidence.acceptance` and `evidence.scheduling_requests`. Only the temporality computation
collapsed them.

**`Provenance.spans` would have been the natural scope, but the interpreter sets `spans=()`
with the comment "spans are not fabricated when unavailable".** Scoping therefore had to be
textual.

## 2. Part 2/3 — acceptance evidence scoping

`acceptance_modality(texts, has_scheduling_evidence)` evaluates the acceptance claim over the
**clause** that accepts. Clauses rather than messages, because `"si, para cuando tenes?"`
arrives as a single message and must behave like the two-message form.

The relaxation is deliberately narrow: **it applies only when the turn carries separate
scheduling evidence**, because that is what explains the future markers as belonging to
something other than the acceptance. Without it the coarse reading stands unchanged.

| burst | result |
|---|---|
| "si" / "dale" / "bueno si" / "avancemos" / "ok" | **actionable** |
| "si" + "para cuando tenes" | **actionable** |
| "si, para cuando tenes?" (one message) | **actionable** |
| "si" + "para hoy que tenes?" | **actionable** |
| "si consigo la plata" | HOLD |
| "capaz que si" | HOLD |
| "si despues te aviso" | HOLD |
| "si consigo la plata" + "para cuando tenes" | HOLD |
| "si consigo la plata" + "hola" (no scheduling evidence) | HOLD |

The last two matter most: a conditional acceptance is not laundered by a neighbouring
sentence, and the gate cannot be opened by unrelated present-tense text.

Quote safety is otherwise untouched — stance, explicitness, quote currency, candidate and
location prerequisites are all unchanged, and only the acceptance claim is scoped.

## 3. Parts 4/5/6 — consuming the intent

The forward-search hook sat inside `if last_stage == SCHEDULING`, and the stage advances
**inside the acceptance handler itself** — so an intent spoken in the same breath always
arrived one turn early. That is a placement error in my own F2 work: I wired the capability
to a stage rather than to the intent.

`_handle_quoted_acceptance` now transitions the stage and then calls
`_consume_scheduling_intent`, which routes from the intent:

1. delegated / earliest — needs no date, answers "para cuando tenes"
2. exact day **and** time → `_try_schedule_and_flow`
3. named day, **including "hoy"** → `_handle_day_only_request`

It returns `None` when the burst carries no scheduling intent, so the ordinary acceptance
reply still goes out. A consumption failure is caught and logged — acceptance is never
blocked by it, and `OutboundBlockedError` still propagates.

A second gap surfaced here and was caught before shipping: `"para hoy que tenes?"` is an
**explicit day**, so `_wants_next_available` correctly declines it. Routing only the delegated
case would have left same-day — core business — still dropped at acceptance.

Nothing is bypassed. ScheduleService still owns availability, and
`_dispatch_booking_flow_for_day` still applies `_authorize_scheduling_progression`. Only the
round trip is removed.

## 4. Part 7 — the availability question

"para cuando tenes" is the plainest way to ask this in Argentine Spanish and matched nothing.
Added **by shape** — *when/what* + *have/is there* + *availability* — rather than as the
observed sentence:

```
para cuando tenes · cuando tienen lugar · cuando hay turno · qué disponibilidad tienen
cuándo pueden · qué tenés disponible · para cuándo hay · qué es lo primero que tenés
```

Negative: "el viernes", "mañana a las 11", "cuánto sale", "sí dale", "hola buenas",
"tengo que estar presente?", "el auto está en paternal" — none fire.

This is the fourth pattern set of mine that ordinary speech outran (presence, service scope,
locality, scheduling intent). The test list is drawn from the prompt's own examples plus the
recorded customer sentences, not from phrasings I invented.

## 5. Parts 8/9 — same-day and the exact Wild

Same-day behaviour from F5 is preserved and now reachable at acceptance: "si" + "para hoy que
tenes?" routes to the named-day handler for today, which searches from now plus the lead
buffer, excludes passed slots, and respects travel, occupancy and business hours.

Verified on the deployed image:

```
burst reading (old)       : FUTURE / CONDITIONAL
acceptance actionable now : True
scheduling intent seen    : True
```

and the exact handler test: the Wild burst accepts, consumes, and **never sends the generic
question** — `_send_text_to_wa` is not called.

## 6. Parts 10/11/12 — duplication

One acceptance write, one scheduling call, one outbound: the consumption path returns early,
so the generic reply cannot also be sent. `_handle_next_available_request` contains no model
call — consumption is a pattern match plus ScheduleService — so the single-flight model-call
contract is unchanged.

## 7. Tests and regression

**23 tests**: ACC-01…11, SCHED-01…10, the exact Wild burst through the real handler, and
guards that the scoping is gated and that only the acceptance claim is scoped.

**Regression: 3880 passed / 57 failed / 9 errors** — identical to the W5-F5 baseline,
**0 new**, +23 passing. Vehicle authority, raw locality, inspection-location role, pricing,
FAQ authority, same-day, next-available, urgency, exact-time Flow-first, Booking Flow,
booking context, human rescue, WAMID attribution and outbound safety all preserved.

## 8. What remains unproven

No handset has seen any of this. The human-rescue path is still unproven live after four
Wilds — the last one never got past acceptance, which is exactly what this milestone repairs.
