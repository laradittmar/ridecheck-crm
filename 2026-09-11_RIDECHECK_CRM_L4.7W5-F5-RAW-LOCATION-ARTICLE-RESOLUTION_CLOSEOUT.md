PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-F5-RAW-LOCATION-ARTICLE-RESOLUTION

STATUS: PASS
DATE: 2026-09-11
CODE COMMIT: 6bee616
IMAGE: ridecheck-crm-backend:w5f5-raw-location-6bee616
SCOPE: crm_test only. Production untouched. OUTBOUND OFF. No Wild run.

---

## 1. Part 1 — the raw location path, traced

```
raw message
  → ConversationEngine._extract_zone_from_text      ← stage 1, text → zone
        Dock Sud fast path
        CABA synonym fast path
        canonical zone_detail substring containment   ← failed here
        compact / ASR-fuzzy fallback
  → PricingRepository.find_zone_by_group_and_detail  ← stage 2, string → catalog row
```

Stage 1 compared `_n(zone.zone_detail)` — `"la paternal"` — against the message. The customer
wrote "en paternal", so containment failed, and **stage 2 was never called with "paternal"
at all.** The ASR-fuzzy fallback compares the whole compacted message
(`quierorevisarunautoenpaternal`) against compact zone names and scores far below its 0.85
threshold.

L4.7W5-F4 made stage 2 article-aware and I certified it by calling stage 2 directly. That is
the failure worth recording: **I tested the layer I had changed instead of the behaviour that
was reported**, and the customer's sentence was sitting in an audit I had written myself the
day before. No F4 test fed raw text through stage 1.

## 2. Parts 2/3/4 — one rule, both directions

The live evidence pointed two ways:

```
"quiero revisar un auto en paternal"  -> None                    (customer dropped "La")
"me mordí la boca"                    -> ('CABA','La Boca')      (pre-existing false positive)
```

Same cause: **an article-prefixed locality is a common noun wearing a proper name.** Plain
containment cannot separate "en la boca" from "me mordí la boca".

So for those names only, a **locative context** is required — a preposition or zone word
(`en, de, desde, por, zona, barrio, localidad, partido, ciudad, está, vivo…`), optionally with
its article, or the locality standing alone as the whole answer, since a bare "paternal"
replying to "¿en qué zona está el auto?" is plainly a location.

- word boundaries throughout; an alias never fires from inside a longer word
- the bare alias is derived from the **canonical catalog entry**, never by editing the
  customer's text, and the sentence is never rewritten
- an alias owned by two canonical localities resolves to **neither**; the canonical forms
  keep working
- proper-noun localities (Palermo, Villa Urquiza, Berazategui) keep plain containment and are
  untouched — they do not occur by accident

## 3. Part 5 — verified through the raw path, on the deployed image

```
Un 3008 en paternal                            -> ('CABA', 'La Paternal')
quiero revisar un auto en paternal             -> ('CABA', 'La Paternal')
el auto está en paternal pero yo soy de tigre  -> ('CABA', 'La Paternal')
tengo plata para pagar                         -> None
me mordí la boca                               -> None
```

Every test in the new suite enters through `_extract_zone_from_text` — the function WhatsApp
traffic uses — and none through the repository. Negative coverage includes "no tengo plata",
"quiero pagar", "hay que talar un árbol", "la boca del tanque", "el auto está en la puerta",
"estoy en casa", plus boundary cases ("paternalismo", "plataforma").

**Two pre-existing false positives are fixed as a side effect**: "me mordí la boca" and "la
boca del tanque" resolved to La Boca before this milestone.

## 4. Part 6 — role safety

"el auto está en paternal pero yo soy de tigre" → **La Paternal**. The vehicle's location wins
over the customer's origin.

Stated honestly: `_extract_zone_from_text` returns **one** zone from free text and is not the
inspection-vs-origin authority — that is the reconciler's job (RISK-03). The test asserts what
this layer actually guarantees rather than overstating it.

## 5. Part 7 — same-day scheduling

**Audit first.** Before the owner's decision arrived, the executable facts were:

| question | answer |
|---|---|
| does ScheduleService evaluate today? | **yes** — it returned `['17:00']` for today and applies no "not today" rule |
| does "hoy" parse? | **yes** — `"para hoy tenes?"` → `('2026-09-11', None)` |
| what excluded today? | two places: `find_next_available` started at `today + 1` (mine, F2) and `_available_dates` iterated `range(1, …)` |
| why did the customer get no answer? | the burst arrived at **QUALIFYING with no vehicle**; qualification came first and scheduling was never reached. It was not refused — it was never evaluated |

Same-day was never stated policy; it was excluded by two independent implementations of an
unwritten rule.

**Owner decision (2026-09-11): same-day booking is allowed and is roughly 60% of demand.**
Implemented:

- `find_next_available` starts at **today**
- the Flow picker iterates from **delta 0**, so today is offerable
- `ScheduleService._suggest_slots` seeds a same-day search from **now + 90 minutes**, rounded
  to the 30-minute grid, instead of opening time — otherwise it would offer 09:00 at four in
  the afternoon, which is worse than saying there is nothing left
- travel validity is measured from that **effective start**, so the operator is not assumed to
  be at the depot when the day is half spent

Nothing else relaxes. Occupancy, business hours, travel feasibility, Sunday closure and
`confirm_booking` revalidation are unchanged, and an exhausted today rolls forward rather than
dead-ending. Urgency still changes only the ordering objective.

Verified with a frozen local clock: at 09:00 the day opens up, at 16:00 only later slots
remain, at 17:45 today yields nothing and the search moves to the next day. No offered slot
ever precedes now + lead.

## 6. BF08 — a certified test changed deliberately

`test_bf08_available_dates_respects_horizon` asserted every offered date is **strictly after
today**, encoding a no-same-day rule that was never stated policy. Relaxed to
`>= today` (a past date is still never offered); the 14-day horizon bound the test exists for
is unchanged.

## 7. Tests and regression

**23 tests** — LOC-ART-01…10, alias ambiguity, role safety, and TODAY-01…10 on a frozen clock.

**Regression: 3857 passed / 57 failed / 9 errors** — identical to the W5-F4 baseline,
**0 new**, +23 passing. Booking location, price persistence, vehicle authority, FAQ authority,
next-available, exact-time Flow-first, Booking Flow, human rescue, WAMID attribution and
outbound safety all preserved.

## 8. What remains unproven

No handset has seen any of this. The human-rescue path is still unproven live — three Wilds
have now ended without reaching it.

Recorded for the next Wild: the same-day lead buffer is **90 minutes**, chosen so a first
offer is reachable rather than theoretical. It is not a stated business rule, and the owner
may want it shorter or longer once same-day traffic is observed.
