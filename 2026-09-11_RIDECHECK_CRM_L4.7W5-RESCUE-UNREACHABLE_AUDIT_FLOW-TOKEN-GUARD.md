PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W5-RESCUE-UNREACHABLE (owner session, 2026-09-11)

IMAGE: ridecheck-crm-backend:w5f6-acceptance-37485ae — unchanged, no code edited
SCOPE: crm_test only. Outbound disarmed at stop. Production untouched.
EVIDENCE: /opt/ridecheck-crm-forensics/L4.7W5-RESCUE-UNREACHABLE_*.tar.gz
          sha256 c277908d2ab583ad1a1932f2a77edfbad29e4aafc6fbd3c388a2f41b03e00726

RESULT: NOT CLEAN. Two defects. One is a BLOCKER that makes the human-rescue path
structurally unreachable in its primary scenario. The other is my own F6 fix landing on a
producer the live path does not use — the second time in three milestones.

---

## 1. The session

```
20:10:41  "hola quiero revisar un sandero en paternal"
20:10:47  "para hoy que tenes"
20:11:11  → quote: Renault Sandero en La Paternal, $140.000
20:11:56  "si"
20:12:03  "para cuando tenes?"
20:12:27  → "¿Qué día y horario te viene mejor…?"            ← DEFECT-01
20:12:52  "te dije para cuando tenes?"
20:13:05  "yo no se tus horarios"
20:13:28  → "Entiendo, disculpá la confusión. ¿Qué día y horario…?" + hours
20:13:51  "mñ?"
20:14:13  → BOOKING_FLOW, sábado 12/09, 3 horarios              ← worked
20:14:50  "no me sirve mañana me lo venden"
20:15:13  → "Entiendo, ¿qué día y horario te viene mejor…?"   ← DEFECT-02 (BLOCKER)
```

Working: **"en paternal" → La Paternal** (F5 holds), the quote, and Flow-first on "mñ?".

## 2. DEFECT-02 (BLOCKER) — the rescue is unreachable once a Flow has been offered

The customer said **"no me sirve mañana me lo venden"** — rejection *and* urgency, the exact
scenario the rescue exists for. Every signal matched:

```
_earliest_option_rejected(["no me sirve mañana me lo venden"])  -> True
_urgency_signalled(...)                                        -> True
"no me sirve" in _ESCALATION_KEYWORDS                          -> True
```

**None of them could be read.** The entire deterministic scheduling block — including
branch 1a, the rescue escalation — is gated on:

```python
if (state.last_stage == STAGE_SCHEDULING
        and not state.needs_human
        and not state.flow_booking_token):     # ← this
```

State at that moment:

```
last_stage = SCHEDULING     needs_human = f     flow_booking_token = SET
```

The Flow dispatched 37 seconds earlier mints and stores that token. So the customer's
rejection arrives with the guard already closed, the block is skipped, and the turn falls to
the AI — which produced a sympathetic sentence and no state change.

**This is structural, not incidental.** Flow-first (W3-F1, W5-F2, W5-F4) made "offer the
picker" the normal response to any scheduling request. A customer can therefore only reject an
offer *after* a token exists — which is exactly when the rescue cannot hear them. The rescue
branch is reachable only in the shrinking case where no Flow was ever opened.

Four Wilds have failed to reach this path. This is why.

## 3. DEFECT-01 (HIGH) — F6 fixed a producer the live path does not use

`authorize.quote_acceptance@v1` **still returned HOLD** on "si" + "para cuando tenes?":

```
17:12:27 AUTHORIZE result=HOLD rule=authorize.quote_acceptance@v1 stance=ACCEPT
         failed=['acceptance_is_present_and_factual']
         reason=acceptance is conditional or about the future
```

There are **two** producers of the `QUOTE_ACCEPTED` claim:

| producer | modality source | fixed in F6? | on the live path? |
|---|---|---|---|
| `claim_projection.claims_from_turn_evidence` | `acceptance_modality` | **yes** | no |
| `conversation_engine._authorize_acceptance` (~5266) | `turn_modality(texts)` | no | **yes** |

Measured side by side:

```
turn_modality(["si","para cuando tenes?"])        -> FUTURE  / CONDITIONAL   (live, holds)
acceptance_modality([...], True)                  -> PRESENT / FACTUAL       (fixed, unused)
```

My F6 verification called `acceptance_modality` directly and reported the burst as
actionable. It was — in the copy I had just written. The decision that matters is made in CE,
which builds its own claim and stamps the coarse burst reading onto it.

**This is the same mistake as L4.7W5-F4**, where I made the catalog lookup article-aware,
verified the catalog lookup, and the live path never called it. Two of the last three
milestones have ended this way: a correct fix, applied to one of several implementations,
verified at the layer I touched rather than through the behaviour reported.

Consequence in this session: the acceptance never authorised, the F6 consumption hook lives
in `_handle_quoted_acceptance`, and that handler was never reached — so the customer asked
twice more ("te dije para cuando tenes?", "yo no se tus horarios") before naming a day
himself. The lead reached `flag=ACEPTADO` only later, through the scheduling-progression
path when "mñ?" was parsed.

## 4. Safety

| metric | count |
|---|---|
| outbound sent | 5 |
| blocked | 0 |
| unattributed | 0 |
| bookings created | 0 |
| security events | 0 |
| wrong vehicle / zone / quote asserted | 0 |

Nothing false was said. The vehicle, the locality, the price and the offered slots were all
correct. The failures are unreachable code and a repeated question — commercially damaging,
not unsafe.

## 5. What these two share

Both are capabilities guarded by state that an earlier step in the same conversation has just
changed:

- F6's consumption hook was behind `last_stage == SCHEDULING`, and the stage advances inside
  the acceptance handler — fixed in F6.
- The rescue is behind `not flow_booking_token`, and the token is set by the Flow dispatch
  that creates the very offer being rejected — **not fixed**.

The pattern is a guard written for one situation being load-bearing for another. Worth
treating as a class rather than two incidents.

## 6. Recommendation

1. **DEFECT-02 first (BLOCKER).** The rejection/escalation branch must be reachable when a
   Flow token exists. A customer rejecting an offered slot is the *normal* path now, not an
   edge case. The guard's original purpose — don't re-run deterministic scheduling while a
   Flow is mid-flight — does not extend to refusing to hear "no me sirve".
2. **DEFECT-01.** Make `_authorize_acceptance` use the scoped acceptance modality, and delete
   or converge the duplicate producer so there is one place a QUOTE_ACCEPTED claim is built.
3. **Verification rule for both.** Prove the fix by driving the decision that failed —
   `authorize.quote_acceptance@v1` and the escalation branch — from the recorded customer
   sentences, not by calling the function I edited.
