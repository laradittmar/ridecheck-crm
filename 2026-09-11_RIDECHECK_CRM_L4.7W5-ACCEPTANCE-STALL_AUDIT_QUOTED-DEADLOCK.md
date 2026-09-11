PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W5-ACCEPTANCE-STALL (aborted rescue Wild, 2026-09-11)

IMAGE: ridecheck-crm-backend:w5f5-raw-location-6bee616 — unchanged, no code edited
SCOPE: crm_test only. Outbound disarmed at stop. Production untouched.
EVIDENCE: /opt/ridecheck-crm-forensics/L4.7W5-ACCEPTANCE-STALL_*.tar.gz
          sha256 f6bd54e71d6decc73b36b3123f5f512bddfbdf5a86b0d552ce7a4994ab55905c

RESULT: THREE DEFECTS. The conversation deadlocked at QUOTED and repeated itself verbatim.

---

## 1. The session

```
19:46:50  "hola quiero revisar un sandero en paternal"
19:46:56  "para hoy que tenes?"
19:47:20  → quote: Renault Sandero en La Paternal, $140.000
19:47:37  "si"
19:47:47  "para cuando tenes"
19:48:11  → "¿Qué día y horario te viene mejor para la revisión del Renault Sandero?"
19:48:52  "y te estoy preguntando para cuando tenes? lo antes posible"
19:49:16  → "¿Qué día y horario te viene mejor para la revisión del Renault Sandero?"
```

**The same sentence, twice, after the customer pointed out they had already asked.**

## 2. What worked

- **"en paternal" resolved** → La Paternal. The L4.7W5-F5 fix works on live traffic.
- The quote is correct for that zone: `$140.000` (AUTO base 140000 + CABA viáticos 0).
- `authorize.scheduling_progression@v1` → **ALLOW**, all four prerequisites satisfied.
- `reconcile.scheduling_preference@v2` → resolved **`day=TODAY, resolved_date=2026-09-11,
  flexible_time=True`**. The same-day request was understood.

The system understood the customer and was authorised to act. It then did nothing with either.

## 3. DEFECT-01 (BLOCKER) — a bare "si" does not accept the quote

```
authorize.quote_acceptance@v1  result=HOLD  stance=ACCEPT
  satisfied=['stance_is_accept']
  failed=['acceptance_is_present_and_factual']
  reason="acceptance is conditional or about the future"
```

The stance was read as ACCEPT. What failed is prerequisite 2 in
`acceptance_authorizer.py:239` — no `QUOTE_ACCEPTED` claim was `is_actionable_now`, so the
acceptance was classified as conditional or future-facing.

The burst was **"si"** followed by **"para cuando tenes"**. A bare "si" answering "¿Si te
parece bien, podemos avanzar?" is as present and factual as acceptance gets; the following
sentence is a *separate* scheduling question. The future-tense reading of the second claim
appears to have contaminated the first.

Consequence, in canonical state:

```
whatsapp_thread_states.last_stage = QUOTED        (never advanced to SCHEDULING)
leads.flag                        = PRESUPUESTO_ENVIADO   (never ACEPTADO)
leads.estado                      = CONSULTA_NUEVA
```

Nothing downstream can proceed, because everything downstream keys on SCHEDULING.

## 4. DEFECT-02 (HIGH) — delegated scheduling is unreachable outside SCHEDULING

The L4.7W5-F2 forward-search entry point sits inside:

```python
if (state.last_stage == STAGE_SCHEDULING
        and not state.needs_human and not state.flow_booking_token):
    ...
    # 1b. delegated / earliest request
```

At `last_stage = QUOTED` that branch is never entered, so `_wants_next_available` was **never
consulted** — even on the turn where it would have matched, and even though the reconciler had
already resolved `day=TODAY` and the authorizer had already returned ALLOW.

This is a placement error in my own F2 work: I hooked the capability to a stage rather than to
the intent. Both live Wilds that reached delegated scheduling did so from SCHEDULING, so the
gap never showed.

The turn fell through to the AI path (`answer_source=CE_AI`), which produced a plausible
question and no state change — which is why the identical reply appeared twice.

## 5. DEFECT-03 (MEDIUM) — "para cuando tenes" is not recognised

```
"para cuando tenes"                                    wants_next=False
"y te estoy preguntando para cuando tenes? lo antes posible"   wants_next=True
"lo antes posible"                                     wants_next=True
```

`_NEXT_AVAILABLE_PATTERNS` covers "cuando puedan / cuando tengan" but not **"para cuando
tenes"** — the plainest way in Argentine Spanish to ask when you have availability. So even in
the right stage, the customer's first delegated request would have missed; only the frustrated
repetition would have matched.

Third time a pattern set of mine has been narrower than ordinary speech: presence, service
scope, locality — now scheduling intent.

## 6. Same-day was understood and dropped

`reconcile.scheduling_preference@v2` resolved `day=TODAY … flexible_time=True` on the 19:48
turn. The same-day capability shipped in F5 was never invoked, for the same reason as
DEFECT-02: wrong stage, no hook. The customer asked "para hoy que tenes?" in their second
message and never received an answer about today.

## 7. Safety

| metric | count |
|---|---|
| outbound sent | 3 |
| blocked | 0 |
| unattributed | 0 |
| bookings created | 0 |
| security events | 0 |
| wrong vehicle / zone / quote asserted | 0 |

Nothing false was said. The vehicle, the locality and the price were all correct. The failure
is a deadlock and a repetition, not an incorrect claim — but a customer who says "si" and then
asks twice for a date, and is asked the same question back, has been failed commercially.

## 8. Why these three are one story

The authorizer, the reconciler and the scheduler each did their job. **The acceptance gate
refused to advance the stage, and the scheduling capability was wired to the stage rather than
to the intent.** Either alone would have been recoverable; together they produce a loop with
no exit, because every retry lands in the same place.

## 9. Rescue path

Still unproven. Fourth Wild without reaching it — this one never got past acceptance.

## 10. Recommendation

1. **DEFECT-01 first.** A present-tense "si" in a mixed burst must accept. The claim-level
   `is_actionable_now` reading should not inherit the tense of a neighbouring scheduling
   question. This is the blocker; the other two are only reachable behind it.
2. **DEFECT-02.** Hook delegated/earliest scheduling to the *intent* and the
   `authorize.scheduling_progression` ALLOW, not to `last_stage == SCHEDULING`. The evidence
   for acting was present on both turns.
3. **DEFECT-03.** Extend the pattern set, and — the recurring lesson — test it against the
   customer sentences already recorded in these audits rather than against phrasings I choose.
