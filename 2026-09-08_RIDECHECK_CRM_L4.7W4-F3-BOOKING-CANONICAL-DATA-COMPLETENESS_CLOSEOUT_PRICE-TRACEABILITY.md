PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W4-F3-BOOKING-CANONICAL-DATA-COMPLETENESS

STATUS: PASS
DATE: 2026-09-08
CODE COMMIT: 2e10469
IMAGE: ridecheck-crm-backend:w4f3-bookprice-2e10469
SCOPE: crm_test only. No schema change. No production write. OUTBOUND OFF throughout.

---

## 1. Part 1 — the booking write, traced

Path: `POST /integrations/whatsapp/flows/booking/data-exchange` → `action=confirm_booking`
→ `BookingFlowService.handle_confirm_booking()` → advisory lock → `ScheduleService.check()`
→ `ThreadRevision` + `Revision` + lead/state mutation → single commit.

The cause is plain and needs no theory: `Revision(...)` was constructed with
`lead_id, tipo_vehiculo, marca, modelo, anio, zone_group, zone_detail, direccion_texto,
vendedor_tipo, tipo_vendedor, turno_fecha, turno_hora` — and **no price fields at all**.
They were never written, so they stayed NULL and the CRM rendered "-".

Where the accepted quote lives: **nowhere, as a stored object.** There is no quote table,
no quote id, no price column on the candidate, and `_decision_log` writes to the log, not
the database. CE derives the amount per turn in `_compute_price_quote()` from the focus
candidate's `tipo_vehiculo` plus its `zone_group`/`zone_detail`, and holds it only in
`self._turn_price_quote` for the duration of that turn.

## 2. Part 2 — authoritative source

**BOOKING PRICE SOURCE: `PricingService.quote(tipo_vehiculo, zone_group, zone_detail)`,
evaluated on the booking's own live cycle-bounded focus candidate.**

The price is a deterministic function of three inputs over a fixed catalog
(`pricing_base.csv` + `viaticos_zones`). The Revision already persists all three beside the
price. **The quote identity therefore IS the input triple** — recording a separate quote id
would add a second thing that can disagree with the first.

`resolve_context()` loads the candidate live via `_load_focus_candidate()`, bounded by the
same `current_cycle_started_at` watermark CE uses, so the booking prices the same candidate
CE priced. This is not a new estimate: the same function over the same inputs returns the
same number, which is why no recalculation-versus-preservation choice arises.

Verified against the real W4 case before any code changed:

```
revision 37 inputs : SUV_4X4_DEPORTIVO | Sur | Berazategui
catalog quote      : base=150000 viaticos=90000 total=240000
matches accepted   : True
```

## 3. Parts 3–5 — the canonical write

`handle_confirm_booking` now stamps the price through that same service, before the
booking commit:

- `BookingFlowService.__init__` holds one `PricingService` — the conversation's authority,
  not a second one.
- After `crm_rev` is flushed, an input-consistency check compares the Revision's
  `tipo_vehiculo`/`zone_group`/`zone_detail` with the resolved candidate's. On mismatch it
  logs `BOOKING_PRICE_INPUT_MISMATCH` and **writes no price** — a wrong number is worse
  than an absent one. The live cycle-bounded lookup makes this unreachable today; it is
  defence in depth and is asserted structurally rather than faked.
- Otherwise `recalculate_revision_if_possible()` fills the three fields. It is
  null-filling and idempotent, so it can never overwrite an operator's manual price.
- Priceable-looking inputs the catalog cannot price emit `BOOKING_PRICE_UNRESOLVED`
  instead of a silent dash.
- `BOOKING_CREATED` now carries `precio_total`.

Everything lands in the existing single commit — there is no window in which a booked
Revision exists without its price.

**Part 5:** `ThreadRevision` has no commercial columns and gains none. One stored
commercial value, on the CRM `Revision`. A second copy could disagree; a booking whose two
representations disagree about money is worse than one that stores it once.

## 4. Part 6 — CRM UI

`kanban_view.py:4907` already reads persisted data —
`total_vals = [r.precio_total for r in revs if r.precio_total is not None]` — and prints
"-" when empty. No view change was needed and no amount is hardcoded. Live, after repair:

```
revisions on lead 127 : [(37, 240000)]
Total presupuestado   : $240.000
```

## 5. Part 7 — the preserved W4 booking

Provenance was confirmed **before** touching it: the catalog reproduces 150000 + 90000 =
240000 from revision 37's own persisted inputs, exactly the accepted amount. The repair was
then the same deterministic function the fix now applies at booking — not a hand-typed
number — and it only fills nulls.

```
BEFORE: None None None
AFTER : 150000 90000 240000
```

The booking itself is untouched: `thread_rev=4 booked 2026-09-08 12:30`,
`last_stage=BOOKED`, `needs_human=true`, `current_revision_id=4`. crm_test only.

## 6. Part 8 — deployment attribution

`deployment_id` came from `docker-compose.beta.yml`'s `GIT_SHA: "${GIT_SHA:-d5f89b3}"`.
That default was three milestones stale, and compose `environment:` outranks image ENV, so
**every** outbound row claimed a commit that had not been deployed for days.

- `backend/Dockerfile` takes `ARG GIT_SHA` and bakes `ENV GIT_SHA`. An image knows what it
  was built from.
- The compose line is **removed**, not corrected — any value there would override the
  image's own truth. A comment records why the absence is deliberate.
- `scripts/build_backend.sh <label>` passes the SHA from git and tags the image to match,
  warning when the tree is dirty. Nothing to remember, nothing to export.
- `scripts/verify_deployment_identity.sh` asserts image tag, baked SHA and the id the gate
  will actually stamp all agree, and exits non-zero otherwise.

```
image        : ridecheck-crm-backend:w4f3-bookprice-2e10469
baked GIT_SHA: 2e10469
stamped id   : 2e10469
tag suffix   : 2e10469
DEPLOYMENT IDENTITY PASS
```

## 7. Part 9 — blocked path attribution

The finding was broader than reported: **three** of the four gate writers that record a
blocked attempt dropped `path_id` — kill switch, flood and dedup. Only the
unauthorized-path blocker kept it. All four now carry the attempted path and deployment id.
The block itself is unchanged; a blocked attempt still makes no Meta call.

Live before/after, same code path, outbound OFF:

```
id 6094 (before)  status=blocked  path_id=<EMPTY>       deployment_id=
id 6097 (after)   status=blocked  path_id=BOOKING_FLOW  deployment_id=2e10469
```

Control dashboard: `id=6097 dir=out status=blocked path=BOOKING_FLOW`.

## 8. Parts 10–11 — tests and regression

19 tests in `tests/test_l4_7w4_f3_booking_price_completeness.py`: BOOKPRICE-01…12,
TRACE-01…03, plus a ThreadRevision no-second-copy check, a priceless-renders-dash check,
a guard-ordering check and a kill-switch-not-loosened check.

BOOKPRICE-06/07 are stated honestly as what they are: an untyped vehicle or an unknown zone
must leave the price **empty**, because a base price without a real viáticos figure is a
fabricated total. BOOKPRICE-05 walks the AST of every function in the service to prove it
neither sums a total itself nor reads a second price table.

**Regression: 3718 passed / 57 failed / 9 errors.** Failures and errors identical to the
W4-F1 baseline — **0 new**, 0 fixed, +19 passing. W3 acceptance, FAQ dedup, flow-first
scheduling, Booking Flow `data_exchange`, vehicle/location authority, Pricing/Schedule
authority, booking transactionality and the CRM auth boundary all unchanged.

## 9. Part 12 — runtime

crm_test only (`current_database() = crm_test`). Outbound OFF for every automated test and
at close. Preflight run before recreation (PASS, 8 variables). Production untouched.

## 10. Gate

| criterion | result |
|---|---|
| Accepted quote persisted on the booked Revision | MET |
| Single pricing authority | MET |
| No invented values | MET |
| No new schema | MET |
| Quote identity traceable | MET (inputs stored beside the price) |
| CRM shows the total from persisted data | MET ($240.000) |
| W4 booking repaired from confirmed provenance | MET |
| deployment_id current | MET (2e10469 everywhere) |
| Blocked attempts keep path | MET (all four writers) |
| 0 new launch failures | MET |
