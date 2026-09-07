PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W3-CONTROLLED-WILD

# Everything canonical was right; the conversation still repeated itself

Wild stopped by owner · no code changed · no tester reset · no outbound after the stop
Runtime `ridecheck-crm-backend:ops500-loginfix-afdb8b1`, container unrestarted since 14:56:07Z

---

## 1. Conversation

| # | dir | content |
|---|---|---|
| 6071 | in (voice) | `Hola, buen día, ¿cómo están? Quería hacer una revisión de un 2008 del 2014. ¿Ustedes hacen eso?` |
| 6072 | in (voice) | `¿Cómo trabajan? ¿Mandan un informe? ¿Qué tiene el informe? ¿Tengo que estar presente?` |
| 6073 | in (voice) | `¡Se paga con débito!` |
| 6074 | **out** | service + report + presence + payment answered … **then** `Vamos hasta donde está el vehículo y hacemos la revisión pre-compra en el lugar. Al terminar te enviamos el informe con todo lo que encontramos.` |
| 6075 | in (voice) | `Ah, ok, ok, bueno, buenísimo. El auto está en Berazategui.` |
| 6076 | **out** | `La cotización … Peugeot 2008 2014 en Berazategui es de $240.000. Si te parece bien, podemos avanzar.` |
| 6077 | in | `Bueno dale avancemos` |
| 6078 | in | `Que horarios tienen?` |
| 6079 | **out** | `¿qué día y horario te viene mejor?` **+ the full weekday table** |
| 6080 | **out** | `no se que tenes mñ?` — **`path_id=MANUAL_CRM`**: the owner's own CRM send |
| 6081 | in | `Nose que tenes mañana ?` |
| 6082 | **out** | `Para mañana martes 08/09 tengo disponible: 11:00, 11:30, 12:00, 12:30 o 13:00. ¿A qué hora te viene bien?` **+ the full weekday table again** |

## 2. What was correct — and it is most of the machinery

```
vehicle    Peugeot / 2008 / 2014 / SUV_4X4_DEPORTIVO   (model_del_year, C2 ACCEPT)
location   Sur / Berazategui                            (C2 ACCEPT: "inspection role
                                                          supported and locality validated
                                                          by the zone resolver")
quote      $240.000 on the correct vehicle + zone
lead       flag=ACEPTADO, estado=CONSULTA_NUEVA, needs_human=false
stage      SCHEDULING, preferred_day 2026-09-08, 5 slots offered
```

**0 wrong canonical writes · 0 false progression · 0 unauthorized outbound · 0 legacy n8n
executions · 0 security events · 1 model call per burst** (four bursts, four shadow records).

Two things proved themselves incidentally: the **F3 gated manual CRM send** worked
end-to-end from the restored UI and is correctly attributed `MANUAL_CRM` in the ledger — 6080
is not a bug, it is the owner typing — and **voice worked cleanly**, with `Berazategui`
transcribed correctly, so W2-F1's recovery path was not needed this time.

## 3. Redundancy — and it is my F4 change that caused the worst of it

### 3a. The weekday table sent twice, one turn apart (HIGH)

Turn 4, `"Nose que tenes mañana ?"`:

```
deterministic hours detection : NONE   ('que horarios' etc. do not match)
semantic faq_intents          : ['business_hours']     ← added by L4.7W1-F4
_FAQ_HOURS_PROBE = 'lunes'    : NOT in the slots reply
=> hours supplement appended
```

Two defects compound:

* **Over-classification.** *"¿Qué tenés mañana?"* asked in `stage=SCHEDULING`, one turn after
  the hours were given, is a **request for availability**, not a business-hours FAQ. The
  interpreter labelled it `business_hours`, and my F4 cutover wired that label straight to
  the answer table without asking whether the stage or the conversation made it redundant.
* **No cross-turn memory.** `_compose_secondary_answers` de-duplicates by probing *the
  current primary reply only*. The hours were in the **previous** message (6079), which the
  probe cannot see. The literal probe `'lunes'` is absent from a slots list, so it fired.

Before F4 the deterministic phrase sets would have missed this turn and no hours would have
been appended. I broadened detection and did not broaden the de-duplication with it.

### 3b. Service description duplicated inside one message (MEDIUM, recurring)

In 6074 the AI had already said *"Revisamos el auto en el lugar donde está… te enviamos un
informe detallado"*. Probe results against that reply:

```
report   'informe'              already present  -> not appended  ✓
presence 'presente'             already present  -> not appended  ✓
payment  'efectivo'             already present  -> not appended  ✓
service_scope 'revisión pre-compra'  ABSENT      -> APPENDED      ✗ (says the same thing)
```

Probe de-duplication is **literal**, so it catches a repeated word and misses a repeated
*meaning*. This is exactly the MEDIUM I recorded in W2 and did not fix; it recurred, and the
owner noticed it before I did.

## 4. Booking Flow — not sent, and the contract says that is correct

`thread_revisions = 0`, `flow_booking_token = null`, no `flow_button_sent`. The Flow never
dispatched because **no concrete slot ever existed**:

```
6077+6078  SCHEDULING branches=[]                       (no day, no time)
6081       branches=[{date 2026-09-08, time None, flexible}]   day only
```

The Booking UX contract dispatches `BOOKING_FLOW` *once a concrete valid slot exists*; text
negotiates availability until then. The customer named a day, was offered five times, and
the session ended before choosing one. **NOT_REACHED, not a failure.**

There is a real product question underneath, and it is a design decision rather than a
defect: offering five slots as free text and asking *"¿A qué hora te viene bien?"* is a
picker rendered as prose. Whether the Flow should dispatch at the *offer* moment rather than
after a time is spoken is worth deciding deliberately.

## 5. A third finding the owner did not ask about (HIGH)

`"Bueno dale avancemos"` is an unambiguous acceptance. C3B did **not** see it:

```
AUTHORIZE result=HOLD rule=authorize.quote_acceptance stance=None
          failed=['stance_is_accept'] reason=no acceptance evidence in this turn
```

Because the burst was *two* messages — acceptance **plus** an FAQ — and
`_is_acceptance()` requires the turn to be acceptance throughout:

```
_is_acceptance(['Bueno dale avancemos', 'Que horarios tienen?'])  -> False
_is_acceptance(['Bueno dale avancemos'])                          -> True
semantic acceptance for that same burst                            -> ACCEPT
```

The lead still reached `ACEPTADO`, but through
`authorize.scheduling_progression` (quote delivered + concrete request), **not** through the
acceptance authorizer. The outcome was right for the wrong reason, and the ledger records
`stance=None` for a turn where the customer plainly said yes.

This is the **same shape as W2's location finding**: the semantic layer had the answer, a
narrow deterministic predicate did not, and the semantic evidence has no consumer. It is
also the exact regression I backed out in F4 for the opposite reason — there, widening the
predicate broke acceptance+FAQ turns. The predicate is the wrong instrument; the authorizer
should read the semantic stance the way scheduling and location now do.

## 6. Findings

| id | severity | finding |
|---|---|---|
| W3-1 | **HIGH** | Business hours re-sent one turn after being sent. F4 semantic topic detection classified a scheduling question as `business_hours`; probe de-duplication has no cross-turn memory. |
| W3-2 | **HIGH** | C3B did not recognise `"Bueno dale avancemos"` because the burst also contained an FAQ. Semantic evidence said ACCEPT and is not consumed. |
| W3-3 | MEDIUM | `service_scope` answer duplicates content the AI reply already gave; probe de-duplication is literal, not semantic. Recurrence of W2 MEDIUM-1. |
| W3-4 | LOW | Turn-1 latency 7 252 ms (`latency_ce_ms`), the slowest of the session. |
| — | — | Booking Flow **NOT_REACHED** — correct per contract; no concrete slot was ever named. |

**BLOCKER: NONE.** No wrong canonical write, no false progression, no unauthorized outbound.

## 7. Evidence

```
/opt/ridecheck-crm-forensics/L4.7W3_backend_stdout_2026-09-07T150311Z.log
   sha256 1330504f7850200c49c3ca6ba44b957b34ef512460b741caadd2d97d553488bd
/opt/ridecheck-crm-forensics/L4.7W3_tester_export_pre_reset_2026-09-07T145533Z.txt
   sha256 4e3f7b3735127de13215a1b7f3af03809a0971c6e4bc8366f98640b91821ad3b
shadow_turn_evidence 50 -> 54   reconciliation_records 7 -> 9
authorization_records 12 -> 16  security_events 734 -> 734
```

Thread 2040, candidate 132, lead 126 left untouched for inspection. Outbound remains armed
as the owner set it. Wild clean count remains **0/3** — this session is not clean.
