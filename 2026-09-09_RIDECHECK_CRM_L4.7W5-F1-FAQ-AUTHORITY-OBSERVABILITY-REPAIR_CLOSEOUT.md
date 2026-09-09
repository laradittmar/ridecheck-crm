PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-F1-FAQ-AUTHORITY-OBSERVABILITY-REPAIR

STATUS: PASS
DATE: 2026-09-09
CODE COMMIT: a228995
IMAGE: ridecheck-crm-backend:w5f1-faq-authority-a228995
SCOPE: crm_test only. Production untouched. OUTBOUND OFF throughout. No live session required.

---

## 1. Part 1 — the traced path, and why presence failed twice

Executable path for every FAQ topic:

```
burst text
  → _explicit_faq_topics()      literal/meaning detection
  → _semantic_faq_topics()      LLM proposal (topic only, never the answer)
  → _faq_topics_for_burst()     reconciliation (explicit wins, scheduling defers hours,
                                already-answered-this-cycle suppressed)
  → _FAQ_TOPIC_ANSWERS[topic]() canonical constant / ScheduleService-derived
  → _compose_secondary_answers() emission decision
  → _send_text_to_wa → gate
```

For the failing burst, proven rather than assumed:

| question | answer |
|---|---|
| was presence detected? | **No.** `_PRESENCE_FAQ_DETECTION` held exactly three literal strings — `tengo que estar presente`, `hay que estar presente`, `necesito estar presente`. "¿Te va a estar presente?" matches none, so the topic never entered the set. |
| why was the canonical answer not emitted? | Two reasons, and the second is worse. Even had it been detected, the emission guard was `if _FAQ_TOPIC_PROBES[topic] in primary: skip`. The probe for presence is the bare substring `"presente"`, and the model's **wrong** sentence contains it. The canonical correction would have been discarded as a duplicate of the falsehood it existed to replace. |
| why was scope emitted? | Its probe is `"revisión pre-compra"`. The reply expressed the same fact in different words ("la revisión se realiza en el lugar donde está el auto"), the literal probe missed, and the paragraph was appended — the W3 redundancy class, reproduced exactly. |
| why the duplicate scope language? | Same cause: substring identity is not fact identity. |

**Root cause in one line: a substring cannot distinguish agreement from contradiction.**

## 2. Part 2/3 — canonical authority

Detection is now by meaning, from both directions, because presence is asked either way and
both are the same business question:

```
customer-side : tengo/debo/necesito/hace falta que estar|ir|acompañar, es necesario que esté…
inspector-side: va(n) a estar presente, están presentes, puede ir el inspector solo,
                quién hace la revisión, va alguien, mandan a alguien…
```

Emission is now governed by two predicate sets per topic:

- **SATISFIED** — the reply already states the fact *correctly* → do not repeat it
- **CONTRADICTS** — the reply states it *wrongly* → strip that sentence, emit the canonical answer

Priority is absolute: **canonical truth > generated prose**, and the two never both survive.
Leaving the model's sentence next to the correction would ship a message that contradicts
itself, which is worse than either half alone. A `FAQ CANONICAL OVERRIDE` warning is logged
whenever this fires, so the substitution is never silent.

Scope satisfaction is by meaning too — "vamos / se realiza … donde está el auto" now counts
as the scope answer, so the canonical paragraph stops following prose that already said it.

The architecture is unchanged in the direction that matters: the LLM still only proposes
topics, and every emitted business fact is a deterministic constant or ScheduleService-derived.
Nothing was added to the system prompt; the fix does not depend on prompt text.

## 3. Part 4 — payment

**"Se paga con débito." was detected by nothing at all** — it matched no phrase in
`_PAYMENT_FAQ_DETECTION`. A declarative sentence about our policy is not self-authorising, so
`_PAYMENT_FAQ_PATTERNS` now routes both the question and the claim forms to the deterministic
authority. Canonical policy is unchanged: efectivo, transferencia, Mercado Pago accepted;
débito not; crédito never advertised.

A contradiction here is read clause by clause, with the negation required **before** the
method — "no se acepta débito" and "aceptamos débito" differ only in that position. My first
implementation used a lookahead, got it backwards, and deleted the one *correct* payment
sentence in the reply. The test caught it; commas are deliberately not clause breaks, because
"aceptamos efectivo, transferencia y débito" is a single claim and splitting it would hide
the falsehood.

## 4. Part 7 — the failed burst, reproduced

Composed from the three real transcripts (not hardcoded — the burst text is fed through the
same detection and composition path):

| requirement | result |
|---|---|
| presence answered correctly | ✅ "No es necesario que estés presente durante la inspección." |
| no contradictory presence statement | ✅ "no estaremos presentes" removed |
| debit rejected | ✅ "no se acepta débito" preserved |
| report answered | ✅ |
| location still requested | ✅ "¿En qué zona o ciudad está el auto?" |
| no duplicated scope paragraph | ✅ |
| no invented business fact | ✅ ("250 puntos" is prompt-supplied business truth, not invented) |
| one coherent response | ✅ no self-contradiction survives |
| model-call contract | ✅ untouched — composition is deterministic and adds no calls |

## 5. Part 5 — debounce-aware alert, proven on the real data

The detector I shipped yesterday alerted on events 141 and 142 from your actual burst. Both
were handled correctly: n8n debounces ~20 s and calls CE once with the last message, so
earlier events stay `triggered` for ever **by design**. "Old and triggered" is not "stalled".

An event is now *explained* when a later event on the same thread reached CE within a bounded
120 s window. Run read-only against the live rows:

```
events: 141 triggered 15:45:31 · 142 triggered 15:45:40 · 143 processed 15:45:44

OLD logic → flags 141, 142      ← the false positives you saw
NEW logic → flags nothing        ← 143 reached CE 13s later; the burst was answered
```

The suppression is conditional, never blanket: a single genuinely stalled message still
alerts, the final message of a burst still alerts if it stalls, and the window is short
enough that an unrelated message an hour later cannot excuse a real failure.

## 6. Part 6 — reset preserves callback attribution

The old reset deleted outbound ledger rows for messages still live on your handset. Meta
does not know that, kept sending callbacks, and the read status for a Flow we had legitimately
sent landed on an empty ledger — raising a HIGH `META_STATUS_FOR_UNKNOWN_WAMID` for our own
message. **The detector was right; the reset destroyed the evidence it needs.**

`services/tester_reset.py` now separates two things with different lifetimes: conversation
state is deleted, transmitted outbound rows are re-parented to a permanent archive thread.
Status resolution matches on `wa_message_id` alone (`routes/whatsapp.py:531`), never on
thread, so attribution survives while the conversation returns to zero. Rows that were
blocked and never transmitted carry no wamid and are simply deleted — there is no callback to
answer. A WAMID we never sent remains genuinely unknown and still raises its event:
**detection is not weakened, only stopped from firing on our own history.**

Run live on the real tester:

```
archived outbound: 0   (thread 2045's only outbound was kill-switch blocked — no wamid)
archive thread   : 2046
deleted          : ai_events 3, messages 4, candidates 1, thread_states 1,
                   threads 1, leads 1, contacts 1
```

## 7. Part 8/9 — tests and validation

**29 tests** in `tests/test_l4_7w5_f1_faq_authority_observability.py`: FAQ-PRES-01…06,
FAQ-DEDUP-01…04, PAY-01…05, DEBOUNCE-01…05, RESET-WAMID-01…04, plus the exact-burst
regression and guards that the canonical answers never self-flag as contradictions.

**Regression: 3764 passed / 57 failed / 9 errors** — identical to the audio-resilience
baseline, **0 new**, +29 passing.

Live validation (Part 9), no owner session required:

| | |
|---|---|
| tester clean | ✅ zero across contacts/threads/messages/ai_events |
| outbound | ✅ OFF throughout |
| FAQ behaviour | ✅ deterministic tests, including the real burst |
| debounce alert | ✅ proven read-only on the live rows (old flags 2, new flags 0) |
| WAMID reset | ✅ executed live; agenda intact (14 appointments, 14 demo leads) |
| deployment identity | ✅ `a228995` across image, baked SHA, stamped id |

## 8. Part 10 — no scope creep

Untouched: pricing, vehicle authority, location authority, acceptance authority, scheduler,
Booking Flow, BookingService, calendar, agenda fixtures, C5, credentials, OpenAI billing,
n8n workflow nodes. The changes are confined to FAQ composition, the alert query and a new
reset service.

## 9. One thing left unproven

The corrected presence answer has not been seen by a real handset — this milestone required
no live session and outbound stayed off. The behaviour is proven deterministically against
the exact transcripts that failed, which is stronger than a single live sample, but it is
not the same as having watched it arrive.
