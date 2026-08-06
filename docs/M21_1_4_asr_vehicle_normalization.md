# M21.1.4 — ASR Vehicle Normalization and Confidence Handling

**Approved:** 2026-08-06
**Owner:** Lara Dittmar
**Status:** Source of truth (locked)

---

## VN-1 — Exact lookup always wins

Run existing `lookup_vehicle()` first. When it succeeds, use the exact result, do not invoke
fuzzy lookup, and do not ask for confirmation.

## VN-2 — Conservative fallback

Call `fuzzy_lookup_vehicle()` only when exact lookup returns `None`.

## VN-3 — Current-turn evidence only

Use the current inbound burst/current-turn text, not unrestricted historical conversation text.

## VN-4 — Candidate comparison strings

Compare normalized input against source-verified catalog forms:

- make + model;
- model;
- exact aliases;
- source-verified variants already represented in the catalog.

Normalization may lowercase, remove accents, normalize punctuation, collapse whitespace, and
safely tokenize. Preserve meaningful digits and alphanumeric model names such as `3008`, `208`,
`A3`, `C4`, `X1`, and `Q5`.

## VN-5 — Make constraint

When the input contains a confidently recognized make, restrict candidates to that make before
ranking.

Examples:

- `ford ksl` may rank Ford models only.
- `peugeot 300` must not become a non-Peugeot model.

Unknown or unreliable make tokens must not impose a false constraint.

## VN-6 — Confidence bands

Approved thresholds:

```text
high_threshold   = 0.87
medium_threshold = 0.70
gap_threshold    = 0.15
```

Rules:

1. `AUTO_ACCEPT`
   - top score >= 0.87;
   - top-minus-second >= 0.15.

2. `CONFIRM`
   - top score >= 0.70 but below 0.87; or
   - top score >= 0.87 but gap < 0.15.

3. `UNRESOLVED`
   - top score < 0.70.

Thresholds may be tightened if the catalog audit proves the starting values unsafe. Do not
loosen without owner approval.

## VN-7 — Corrected Ford KSL behavior

Measured values for `ford ksl`:

```text
Normalized input:     ford ksl
Ford Ka score:        0.8000
Ford Kuga score:      0.7059
Gap:                  0.0941
```

Because the gap is below 0.15 the outcome is `CONFIRM`.

Exact reply:

```text
¿Es un Ford Ka?
```

Do not create a Ford Ka candidate before confirmation.

## VN-8 — Confirmation handling

Acceptance examples:

- `Sí.`
- `Sí, es ese.`
- `Exacto.`
- `Es un Ka.`

Required:

- persist the proposed vehicle once;
- clear `pending_fuzzy_catalog_key`;
- continue normal qualification;
- do not ask for the vehicle again.

Rejection examples:

- `No.`
- `No, es un Kuga.`
- `Nada que ver.`

Required:

- do not persist the proposed candidate;
- clear or replace `pending_fuzzy_catalog_key`;
- use exact current-turn evidence when rejection supplies a clear vehicle;
- otherwise ask for make and model once.

## VN-9 — Persistence design

Pending fuzzy proposal is persisted on `WhatsAppThreadState` in a new nullable field:

```text
pending_fuzzy_catalog_key: str | null
```

Value format: `"{marca}||{modelo}"` (double pipe separator, verbatim brand/model from catalog).

`NULL` means no pending proposal.

This requires a single Alembic migration — see Phase 4 gate for approval record.

Do not repurpose semantically unrelated fields.

## VN-10 — Short-token protection

- 1- and 2-character fragments are unresolved unless exact aliases.
- 3-character fragments require a reliable make constraint and threshold evidence.
- `Ka` remains exact (handled by `lookup_vehicle()`).
- Ordinary words must not become vehicle candidates.

## VN-11 — No cross-domain false positives

Do not fuzzy-match locations, years alone, prices, greetings, FAQ text, scheduling text,
unsupported services, or motorcycle terms already handled by higher-priority gates.

## VN-12 — Mutation safety

For `CONFIRM` and `UNRESOLVED`:

- no guessed candidate creation/update;
- no guessed `tipo_vehiculo`;
- no pricing;
- no scheduling;
- no revision;
- no location/booking Flow caused by the guessed vehicle;
- no overwrite of an existing focused candidate.

## VN-13 — Existing candidate protection

A fuzzy phrase must not replace an existing focused candidate unless the user clearly
introduces/corrects the vehicle and the confirmation rules are satisfied.

Do not implement full multi-candidate switching.

## VN-14 — Kill switch

A fuzzy confirmation reply blocked by outbound must return `handled=True`, preventing n8n
legacy fallback.

New handled action: `vehicle_fuzzy_blocked`.

---

## Catalog measurements (2026-08-06)

Input normalization: lowercase, remove accents/punctuation, collapse whitespace.

Candidate forms scored: normalize(brand + model) + all aliases (662 total).

### SC09 — Ford KSL near-collision

| Form | Score |
|---|---|
| ford ka | 0.8000 |
| ford kuga | 0.7059 |
| gap | **0.0941** |

Outcome: `CONFIRM` → `¿Es un Ford Ka?`

### SC07 — ford fiestah (AUTO_ACCEPT reference)

| Form | Score |
|---|---|
| ford fiesta | 0.9565 |
| ford escort | 0.6957 |
| gap | **0.2609** |

Outcome: `AUTO_ACCEPT`

### SC18 — honda ranger (make-constrained)

| Unconstrained | Score |
|---|---|
| ford ranger | 0.7826 |

| Honda-only | Score |
|---|---|
| honda hr-v | 0.6667 |

Without make constraint: `CONFIRM` → wrong brand (Ford Ranger).
With Honda make constraint: 0.6667 < 0.70 → `UNRESOLVED` → no false positive.

### SC08 — garbled unresolvable (reference)

Input `xyz abc`: top score 0.4286 → `UNRESOLVED`.

### Selected additional inputs

| Input | Top score | Top vehicle | Gap | Outcome |
|---|---|---|---|---|
| renolt clio | 0.8696 | Renault Clio | 0.2029 | CONFIRM (score < 0.87) |
| toyota corola | 0.9630 | Toyota Corolla | 0.1751 | AUTO_ACCEPT |
| hiunday tucson | 0.8571 | Hyundai Tucson | 0.2418 | CONFIRM (score < 0.87) |
| chevrolet crkz | 0.8966 | Chevrolet Cruze | 0.1224 | CONFIRM (gap < 0.15) |
| hola buenas | 0.5926 | Volkswagen Taos | 0.0541 | UNRESOLVED |
| vw | 0.5714 | Volkswagen Up | 0.0714 | UNRESOLVED (short token) |
| a | 0.6667 | Ford Ka | 0.0000 | UNRESOLVED (1 char) |
