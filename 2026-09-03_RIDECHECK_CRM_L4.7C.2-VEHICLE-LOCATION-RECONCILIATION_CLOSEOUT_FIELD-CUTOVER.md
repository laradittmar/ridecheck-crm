PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7C.2-VEHICLE-LOCATION-RECONCILIATION

# L4.7C.2 — the first authority cutover

Date: 2026-09-03
Two claim families only · flag-guarded and reversible · crm_test only · OUTBOUND OFF ·
production DB untouched · no pricing, scheduling, booking, acceptance or lifecycle change.

---

## 1. Verdict

**PASS.** Vehicle identity and inspection location are now written by a named rule that
records why, instead of by whichever code path reached the field first. The cutover is
reversible by configuration: with the flags off, the write path is the legacy assignment,
byte for byte, and the full regression is identical to baseline in both positions.

## 2. What moved, and what did not

| | |
|---|---|
| **Moved** | `candidate.marca` · `modelo` · `anio` · `tipo_vehiculo` · `zone_group` · `zone_detail` |
| **Not moved** | quote acceptance, quote readiness, pricing, scheduling, availability, booking, handoff, lead lifecycle, `state.home_zone_*` pre-candidate buffer |

Two flags, default **OFF** everywhere and set only in the crm_test compose:
`RECONCILER_VEHICLE_AUTHORITY_ENABLED`, `RECONCILER_LOCATION_AUTHORITY_ENABLED`.

## 3. Authority, stated per claim

Neither producer wins. `backend/app/services/field_reconciler.py` implements two rules:

**`reconcile.vehicle_identity.v1`** — the customer's words say *which* car; the **catalog**
says what that car is called and what category it is.

* an explicit model that the catalog resolves uniquely → **ACCEPT** the catalog's normalised
  identity (make, model and category all come from the catalog);
* an inferred make with no catalog confirmation → **cannot canonicalise**;
* an inferred make **is** a legitimate catalog *probe* — the canonical make is whatever the
  catalog answers, never the suggestion itself;
* model and year are independent claims and both survive;
* an ambiguous or unresolvable identity → **CLARIFY**, never an arbitrary pick;
* a lone year or category for a car already identified is a **refinement**, not a new
  identity decision.

**`reconcile.inspection_location.v1`** — the semantic layer reads the **role**; the zone
resolver says whether the locality **exists** and which zone it belongs to.

* inspection role + validated locality → **ACCEPT** with the resolver's zone;
* a locality the resolver cannot validate → **CLARIFY**;
* two competing inspection localities → `BOTH` → **CLARIFY**;
* origin and inspection coexist without conflict;
* **an origin claim can never populate the inspection location** — enforced by claim type,
  not by ordering.

Both authorities are **injected callables**, so the module imports no ORM, no engine, no
pricing and no scheduling (asserted by test VL-18).

## 4. The single write path

`ConversationEngine` gained two chokepoints, `_apply_vehicle_identity` and
`_apply_inspection_zone`, and **every** candidate vehicle/zone assignment now routes through
them — 5 vehicle sites and 8 location sites. Two AST tests (VL-06, VL-12) walk the whole
engine and fail if any assignment to `marca`, `modelo`, `anio`, `tipo_vehiculo`, `zone_group`
or `zone_detail` on a candidate appears outside the chokepoint.

With a flag **off** the chokepoint performs the legacy assignment. With it **on**, the
proposed value becomes a claim, the rule decides, and the field is written only on ACCEPT.
Legacy parsers keep producing evidence and validating; they have stopped writing.

## 5. Shadow-vs-authority comparison (Part 15)

Every corpus case carrying vehicle or location evidence, legacy proposal versus authority
decision:

| | AGREE | CLARIFY | HOLD |
|---|---|---|---|
| vehicle | **32** | 1 | 0 |
| location | **31** | 0 | 0 |

The single disagreement is **WILD-01-01** — a bare numeric model (`"2008"`) with no make at
all, which the catalog cannot place. The authority asks instead of guessing; the legacy path
would have written an unresolved identity. That is the safer behaviour and it is explained,
so Part 15's gate ("do not enable if unexplained disagreement remains") is met.

Origin-only cases that produced an inspection-location write: **0**.

## 6. Producer conflicts from C1 (Part 14)

C1 surfaced 7 `BOTH` states and 9 producer conflicts across 275 claims. Of those, the
vehicle/location ones now resolve by rule rather than by ordering:

| Raw | Semantic claim | Deterministic/catalog | Rule | Result |
|---|---|---|---|---|
| "un 2008 del 2014" | model 2008 + make Peugeot (inferred) | catalog: Peugeot 2008 / SUV_4X4_DEPORTIVO | `vehicle_identity.v1` | ACCEPT catalog identity + year 2014 |
| "Es un Ford Ka… no, es un Ford Kuga" | Kuga asserted, Ka superseded | catalog: Ford Kuga | `vehicle_identity.v1` | ACCEPT Kuga — **a replacement is not a contradiction** |
| "2008 o 208" | two models | — | `vehicle_identity.v1` | CLARIFY, no candidate |
| "Está en Berazategui, pero yo soy de Tigre" | Berazategui INSPECTION, Tigre ORIGIN | zone: Berazategui → Sur | `inspection_location.v1` | ACCEPT Berazategui/Sur; Tigre never written |
| origin-only locality | Tigre ORIGIN | zone: Tigre valid | `inspection_location.v1` | HOLD — validity is not a role |
| bare numeric model | model "2008", no make | catalog: no match | `vehicle_identity.v1` | CLARIFY |
| year-only correction | year 2015 + correction | — | `vehicle_identity.v1` | ACCEPT as a refinement |

**No silent overwrite occurred in any of them.**

The Ka/Kuga case exposed a real defect in the C1 primitive, found by C2's first use of it:
`information_state` treated *any* negation as contradicting *any* assertion, so a replacement
read as `BOTH`. A negation now contradicts only the value it denies — "it is not a Ka, it is
a Kuga" leaves Kuga standing, with the supersession recorded separately.

## 7. Justifications (Part 8)

Every accepted or refused decision produces a `ReconciliationRecord` carrying `claim_type`,
`evidence_ids`, `candidate_values`, `rule_id`, `rule_version`, `information_state`, `outcome`,
`risk_tier`, `cycle_id`, `revision_id` and `depends_on`. Records are appended to
`reconciliation_records.jsonl` beside the shadow record — **append-only, no new table, no
migration** (Part 16). A live probe produced five of them:

```
vehicle.model        CLARIFY  TRUE_ONLY  reconcile.vehicle_identity@v1     not resolvable by the catalog
inspection_location  ACCEPT   TRUE_ONLY  reconcile.inspection_location@v1  role supported, locality validated
customer_origin      HOLD     NEITHER    reconcile.inspection_location@v1  an origin is never an inspection location
```

This was itself a defect found and fixed during the milestone: justifications first existed
only in the log stream, which is not durable enough to audit a decision.

## 8. Live runtime probe, flags ON

On `ridecheck-crm-backend:l4.7c2-fieldauth-7d61c40` in crm_test:

| Path | Result |
|---|---|
| full identity — `modelo="2008"`, `anio=2014` | **written**: Peugeot / 2008 / 2014 / SUV_4X4_DEPORTIVO |
| year refinement on an identified car | **written**: 2017 |
| unresolvable model `"Cosita"` | **not written** — CLARIFY |
| inspection locality Berazategui | **written**: Sur / Berazategui |
| customer origin Tigre | **not written** |

`crm_test.whatsapp_messages` 6 → 6 · `whatsapp_thread_candidates` 0 → 0 · OUTBOUND OFF.

## 9. Legacy parsers (Part 11), vehicle/location only

| Classification | Branches |
|---|---|
| **EVIDENCE_PRODUCER** | `_extract_year_from_text`, `_has_customer_origin_clause`, `_strip_customer_origin_clauses`, AI/Flow/form extraction feeding the chokepoints |
| **VALIDATOR (authoritative)** | `vehicle_catalog.lookup_vehicle`, `fuzzy_lookup_vehicle`, `_contextual_numeric_model_lookup`, `extract_model_del_year`, `_catalog_tipo_for`, `_extract_zone_from_text` (ViaticosZone) |
| **TEMPORARY_COMPATIBILITY** | `state.home_zone_*` pre-candidate buffer writes — unchanged by design (Part 6 rule F); the L4.6 origin-stripping still protects it |
| **NO_LONGER_WRITES_CANONICAL_STATE** (flags ON) | the five vehicle assignment sites and the eight candidate zone assignment sites, now routed through the chokepoints |

Nothing was deleted. Retirement belongs to C6.

## 10. Dependency invalidation (Part 9)

C2 invented **no** invalidation system. Current behaviour is preserved exactly: a vehicle or
location change continues to flow through the existing quote/scheduling recomputation paths
untouched. Traces recorded for C5: a vehicle correction after a quote, and a location
correction after a quote, both invalidate the quote through the existing mechanism, and the
`depends_on` field on every record now names the dependency explicitly
(`quote_accepted → vehicle.category, inspection_location`;
`scheduling_preference → inspection_location`).

## 11. Tests and regression

`tests/test_l4_7c_2_vehicle_location_reconciliation.py` — **26/26 PASS** (VL-01…18 plus the
catalog-overrules-inference case, unresolvable-model, origin/inspection coexistence, the
year-refinement pair, and authority-on write behaviour).

Full regression, **flags OFF**: 3 448 passed / 60 failed / 9 errors. **Flags ON via env**:
identical. Failure set byte-identical to baseline in both — **zero new failures**.
Launch-gate suites plus the vehicle/location/candidate/reset suites: 694 passed, with only
the 4 pre-existing `test_m21_1_1_service_intent_gate` failures that are in the baseline set.
Unknown: 0.

## 12. Safety

No pricing, scheduling, booking, acceptance or lead-lifecycle authority changed — asserted
statically (the reconciler imports none of them) and behaviourally (the chokepoints write no
such field). No DB migration. No new canonical table. Production untouched. OUTBOUND OFF.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.

Next: **L4.7C.3-INTENT-STANCE-ACCEPTANCE-RECONCILIATION** — the high-risk family, not to be
started automatically.
