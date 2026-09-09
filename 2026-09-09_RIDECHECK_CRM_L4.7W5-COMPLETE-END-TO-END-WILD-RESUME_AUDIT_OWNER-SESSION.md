PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W5-COMPLETE-END-TO-END-WILD-RESUME

DATE: 2026-09-09
IMAGE: ridecheck-crm-backend:w5f2-next-available-3e97654 — unchanged throughout, no code edited
SCOPE: crm_test only. Production untouched. Outbound armed for the tester only, disarmed at close.
EVIDENCE: /opt/ridecheck-crm-forensics/L4.7W5-WILD-RESUME_20260909T205918Z.tar.gz
          sha256 dce9bbe2b2ac5aedffb2dd26ed5949e4e23bf38e98fa8f59553ecbec969baf57

---

## 1. The session

Six bursts, first contact to confirmed booking, on a tester at true zero state. Four voice
notes, then text. 20:47:26 → 20:54:30, about seven minutes.

```
20:47:26  voice ×3  "para revisar un 2008 del 2014" / "¿mandan informe? ¿tengo que estar
                     presente?" / "se paga con débito"
20:48:06  reply     vehicle acknowledged, report + presence + payment answered, zone asked
20:48:44  voice     "el auto está en verazategui pero yo soy de tigre"
20:49:09  reply     quote $240.000 for Peugeot 2008 2014 in Berazategui
20:49:28  text ×2   "Si dale avancemos" / "Que horarios hacen!?"
20:50:04  reply     operating hours, day requested
20:50:30  text ×2   "para no que tenes" / "Digo mañana"
20:50:59  reply     "Para mañana jueves 10/09 no hay horarios libres. ¿Tenés otro día?"
20:51:27  text ×2   "bueno para cuando tenes?" / "Lo antes posible que me venden el auto"
20:51:54  FLOW      "Para sábado 12/09 tengo 2 horarios disponibles…"   ← forward search
20:52:26  INIT → DATE_SELECTED → SUMMARY → REVALIDATION_PASS → BOOKING_CREATED
20:54:30  flow_response received; booking complete
```

**The turn that mattered.** "bueno para cuando tenes? / lo antes posible que me venden el
auto" is precisely the sentence that dead-ended the previous Wild. This time the delegation
was recognised, ScheduleService searched forward, skipped Thursday and Friday as genuinely
empty for Berazategui, found Saturday, and the Booking Flow opened **on a day the customer
never named**. That is L4.7W5-F2 working in production on the exact input that produced the
defect.

## 2. Three fixes earning live evidence for the first time

| fix | evidence |
|---|---|
| **L4.7W5-F2** next-available | delegated turn → forward search → Flow on 2026-09-12 |
| **L4.7W4-F3** booking price | `BOOKING_CREATED … precio_total=240000`; Revision 55 carries 150000 + 90000 |
| **L4.7W5-F1** presence + reset | "No es necesario que estés presente durante la revisión"; **0** false unknown-WAMID events despite 5 archived WAMIDs from prior resets |
| **L4.7W5-F2** burst counting | opening burst recorded **3**, not 1 |

## 3. Booking and CRM

```
thread_revision 5   booked   2026-09-12 13:30   candidate 137
                    Peugeot 2008 2014 SUV_4X4_DEPORTIVO
                    Calle 11 4150, Berazategui (Sur)   approval PENDING
revision 55         lead 150   turno 2026-09-12 13:30   estado PENDIENTE
                    precio_base 150000 · viaticos 90000 · precio_total 240000
lead 150            AGENDADO / ACEPTADO / necesita_humano=true
state               last_stage=BOOKED   current_revision_id=5
```

13:30 was one of the two slots ScheduleService offered — no invented time. CRM shows
**Total presupuestado: $240.000**. The calendar week renders the appointment with the right
date, time, vehicle and zone, seated correctly among the two seeded Saturday jobs
(09:30 San Isidro, 11:00 Florida, **13:30 Berazategui**). One candidate, one thread_revision,
one revision — no duplicates anywhere.

## 4. Safety — all zero

| metric | count |
|---|---|
| wrong vehicle / year / category | 0 |
| wrong inspection location / zone | 0 |
| wrong quote · stale quote accepted | 0 |
| false acceptance | 0 |
| wrong scheduling progression | 0 |
| unavailable slot offered | 0 |
| false booking · duplicate booking | 0 |
| history / cycle leakage | 0 |
| unauthorized state write | 0 |
| unauthorized outbound | 0 |
| unknown / unattributed outbound | 0 |
| runaway duplicate reply | 0 |
| invented business fact | 0 |
| security events during session | 0 |

Vehicle resolved from a voice transcript ("un 2008 del 2014" → Peugeot 2008 2014
SUV_4X4_DEPORTIVO). Location correctly took the **car's** zone (Berazategui) and not the
customer's own ("yo soy de tigre") — the role distinction held. The quote came from
PricingService; "250 puntos de control" is prompt-supplied business truth, not invented.

## 5. Product quality — two findings

**FINDING-01 (MEDIUM) — the scope redundancy returned, and it is a miss in my own W5-F1 fix.**

Reply 6129 says *"Revisamos el auto en el lugar donde está y al finalizar, te enviamos un
informe detallado…"* and then appends the canonical paragraph verbatim: *"Vamos hasta donde
está el vehículo y hacemos la revisión pre-compra en el lugar. Al terminar te enviamos el
informe con todo lo que encontramos."* The same fact, twice, in one message.

Verified against the live text, not inferred: none of my
`_FAQ_TOPIC_SATISFIED["service_scope"]` patterns match it. I required the verbs
`vamos|nos acercamos|se realiza|la hacemos`, and the model wrote *"Revisamos"*; my second
pattern expects "en el lugar donde está **el auto**" with the noun last, and the prose put it
first. I wrote predicates that fit my test fixture rather than the space of natural
phrasings — the same class of narrowness that caused the original presence defect.

Not a safety issue: nothing false was said, and presence and payment were both correct.

**FINDING-02 (LOW) — one avoidable round trip.** After "para mañana" came back empty, the
customer had to ask again before the forward search ran. Forward search fires only on an
explicit delegation, which is the deliberate design — but an empty named day is the moment
where proactively naming the next available date would remove a turn.

Everything else: 0 unnecessary clarifications, 0 duplicate FAQ answers, 0 incorrect FAQ, 0
dead-end scheduling, 0 missed handoffs, 0 silent failures, 0 unnecessary or missing Flows.

## 6. Performance

**Model calls: 1 per burst, 5 total**, all `ok=True` on `gpt-4o-mini` (2455, 2105, 1374, 1266,
1367 ms). The single-flight contract held for every burst.

**Latency**: 25.2–36.3 s inbound→reply, of which ~20 s is the designed n8n debounce. CE
compute 0.9–6.4 s (median ≈ 2.9 s); the Flow dispatch turn was the fastest at 866 ms.

**Voice**: 4 transcriptions, 4 successes, 0 failures, 0 retries.

## 7. Not exercised

The human-rescue handoff never triggered — correctly, because the customer accepted the
earliest option. Its preconditions, routing and email remain proven only by test, not by a
live session. The same is true of the no-capacity-in-horizon route.

## 8. Assessment

This is the first session that completed the full commercial journey: first contact → vehicle
→ location → quote → acceptance → delegated scheduling → Flow → confirmed booking → CRM and
calendar, with every safety counter at zero and the price on the booking.

The one blemish is a duplicated paragraph in the opening reply, caused by a predicate I wrote
too narrowly a milestone ago. It is cosmetic to the customer and structural to me: the second
time a "have we already said this?" check has been too literal.

Whether this counts as a clean Wild is the owner's call, not mine — the safety evidence
supports it; FINDING-01 is a product-quality blemish that a reader may or may not accept in a
clean count.
