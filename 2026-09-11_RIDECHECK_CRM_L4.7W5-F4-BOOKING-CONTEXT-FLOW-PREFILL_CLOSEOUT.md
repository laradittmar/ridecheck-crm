PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-F4-BOOKING-CONTEXT-FLOW-PREFILL

STATUS: CONDITIONAL_PASS
DATE: 2026-09-11
CODE COMMIT: dcc5ec1
IMAGE: ridecheck-crm-backend:w5f4-booking-context-dcc5ec1
SCOPE: crm_test only. Production untouched. OUTBOUND OFF. No Wild run.
CONDITION: Flow prefill of phone/name and true slot preselection are blocked by the
published Flow definition on Meta — an owner action, not a code change. See §6.

---

## 1. Part 1 — location authority, traced

| place | rule |
|---|---|
| CE `_get_active_inspection_location` | candidate authoritative **only if it has a zone**; missing half filled from `state.home_zone_*` |
| CE `_compute_price_quote` | focus candidate zone, else `state.home_zone_*` |
| PricingService.quote | `(tipo_vehiculo, zone_group, zone_detail)` — nothing else |
| **BookingFlowService `_location_from_candidate`** | **candidate zones whenever a candidate exists — even NULL** |
| `handle_confirm_booking` | writes `ctx.zone_group/zone_detail` onto the Revision |

**Why CE quoted CABA/Paternal while booking wrote NULL/NULL:** candidate 138 existed with
both zone fields NULL. CE skipped it and used `state.home_zone_group='CABA'`,
`home_zone_detail='Paternal'` — enough to quote $150.000. BookingFlowService saw a candidate,
returned `(None, None)`, and never looked at state. The Revision was created with no zone, so
`recalculate_revision_if_possible` returned early and no price was written.

Two near-duplicate implementations of one rule, and the booking path had the weaker one.

## 2. Part 2 — article-normalised localities

The catalog holds "La Paternal". The lookup normalised case and whitespace but not the
article, so `paternal → None` while `la paternal → match`. Dropping the article is ordinary
usage, and the failure was silent: it degraded into a location Flow.

Resolution now compares the customer's words against **canonical entries with the article
stripped** — arbitrary text is never edited. Ambiguity is refused: if two localities reduce to
the same bare form, or the bare form is itself canonical, nothing resolves and the Flow still
asks.

Collision survey over the live table — 207 localities, 13 article-prefixed, **0 collisions**:

```
boca · hornos · jaguel · lucila · matanza este · matanza oeste · palomar
paternal · plata · polvorines · reja · tablada · talar
```

The guard remains so that adding a colliding locality later cannot silently start
mis-resolving. `Palermo` (canonical without an article) is unaffected, and a contradicting
`zone_group` blocks the match.

Observation, not fixed: normalisation still does not fold accents, so "jaguel" will not reach
"El Jagüel". Same class, different axis; recorded rather than silently widened.

## 3. Parts 3/4 — one resolver, and no unpriced bookings

`_location_from_candidate` now mirrors CE exactly: a candidate is authoritative only when it
has a zone, a missing half comes from thread state, and customer origin is never used.

And a booking that still cannot resolve a canonical location is **refused** —
`BOOKING_REFUSED_NO_CANONICAL_LOCATION`, returning the customer to the APPOINTMENT screen —
rather than written unpriced. An operator opening a lead with an appointment and no figure,
while the customer holds a number that exists nowhere, is a commercial defect.

## 4. Part 5 — year: audited, not decided

- `PricingService.quote` and `recalculate_revision_if_possible`: **do not read `anio`**
- `ScheduleService.list_slots` / `check`: **do not read `anio`**
- no documented rule requires make/model/year before quote or booking
- but **42 of 43** existing revisions carry a year; the one without is the Wild's own

**Finding: year gates nothing commercially and blocks nothing today, yet a booking without it
is an operational outlier an inspector would notice.** No policy was imposed. If the owner
wants it required, the right place is vehicle qualification before the quote — not a booking
guard, which would fail the customer at the last step over data nobody asked for.

## 5. Part 15 — Flow-first on a rejected exact time

The live reply recited seven slots and waited for another text turn. The picker now opens on
the same day — **except when the burst also owes a canonical FAQ answer**, because the Flow
body cannot carry one and losing "¿aceptan débito?" to save a round trip is a bad trade.

That exception was found by regression, not foresight: 12 certified tests failed and showed
the FAQ answer disappearing. Without them this would have shipped as a silent loss.

## 6. Parts 6/7/10/11/12 — what the published Flow actually permits

Flow `28104222025943520` v7.3 was fetched **read-only from the Meta Flows API** rather than
guessed:

```
APPOINTMENT  Dropdown date / Dropdown time      — no init-value
DETAILS      TextInput name, phone(required), email, inspection_address, seller_*, listing_url
                                                — no init-value on any of them
SUMMARY      read-only recap + "Solicitar turno"
```

**A component renders a starting value only from `init-value`, and none is declared.** So no
data the back end sends can preselect a slot or prefill the phone. Adding keys like
`selected_date` or `phone` would change nothing on screen — that needs the Flow JSON edited
and republished on Meta, which is an owner action.

What is ours is the **option list**. When an exact slot is already agreed and still free, the
picker is narrowed to that one date and one time: the customer confirms instead of
re-choosing, the date→time round trip disappears, and no wrong slot can be selected. A
day-only or NEXT_AVAILABLE dispatch keeps the full picker, because there the choice is real.

Exact JSON change for the owner is recorded in
`docs/operations/BOOKING_FLOW_PREFILL_CONSTRAINT.md`.

## 7. Part 13 — safety unchanged

Narrowing is UX only. `handle_confirm_booking` still acquires the advisory lock, calls
`ScheduleService.check`, and raises `BookingSlotConflictError` on a stale slot. If the agreed
slot is no longer free at INIT, the full picker is offered instead.

## 8. Part 14 — the exact Wild, on the deployed image

```
'paternal' -> ('CABA', 'La Paternal')
Peugeot 3008 (SUV/4x4) @ La Paternal: 150000 + 0 = 150000
```

The quote the customer was given is now derivable from canonical inputs and will be persisted
on the Revision. No value is hardcoded — base comes from the catalog, viáticos from the zone.

## 9. Tests and regression

**25 new tests**: LOC-ARTICLE-01…05, BOOKCTX-01…06, FLOW-PREFILL-01…10, EXACT-REJECT-01…03,
plus the rewritten FLOW-05 pair.

**Regression: 3834 passed / 57 failed / 9 errors** — identical to the W5-F3 baseline,
**0 new**, +25 passing. Human rescue asserted intact (Part 17).

## 10. What remains unproven

None of this has been seen by a handset. Article resolution, the priced booking, the narrowed
picker and Flow-first-on-rejection are proven deterministically and against the live catalog,
but outbound stayed off. The **human-rescue path is still unproven live** — two Wilds have now
ended in bookings instead.
