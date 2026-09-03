PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7C.3A-ACCEPTANCE-AUTHORIZATION-SHADOW

# L4.7C.3A — the acceptance predicate, proven before it is trusted

Date: 2026-09-03
Shadow only · legacy CE acceptance path untouched and authoritative · C2 vehicle/location
authority ON in crm_test · OUTBOUND OFF · production DB untouched · no schema change.

---

## 1. Verdict

**PASS.** The authorization predicate exists, is deterministic, and refuses every adversarial
case in the false-progression suite while still authorising 90.5 % of genuine acceptances.
It has written nothing: `ConversationEngine`'s legacy acceptance path remains the only thing
that advances commercial state.

## 2. Audit first — what "a quote" actually is (Parts 4 & 5)

**Quote identity.** RideCheck has no quote version or id column, and the audit concluded it
does not need one. A quote is identified by **what it was computed from**:

```
quote_identity = sha256(cycle · revision_id · candidate_id · tipo_vehiculo ·
                        zone_group + zone_detail · precio_total)[:16]
```

Change any input — the candidate, the category, the zone, the amount — and the identity
changes. That *is* the staleness test acceptance needs, and it is derivable from
`Revision.precio_base/viaticos/precio_total` plus the inputs already stored on the revision
and the candidate. **No migration, no new column, additive by construction.**

**Delivery proof.** "A price was computed" is not "the customer was told". The authoritative
evidence is the **outbound ledger**: a `whatsapp_messages` row with `direction='out'` in the
current cycle whose text carries the amount — the same source L4.7D already uses for
`previously_quoted`. `lead.flag == "PRESUPUESTO_ENVIADO"` and `state.last_stage == "QUOTED"`
corroborate but are not sufficient on their own: both are set in the same transaction as the
send *attempt*, so a failed send would leave them true. The predicate therefore requires the
amount to appear in `delivered_amounts`.

## 3. The predicate (Part 2)

```
ALLOW ⟺  stance == ACCEPT
       ∧ the acceptance was READ from the customer, not DERIVED from other facts
       ∧ polarity ASSERTED ∧ temporality PRESENT ∧ modality FACTUAL
       ∧ a quote exists for this cycle
       ∧ that quote was DELIVERED (its amount is in the outbound ledger)
       ∧ acceptance and quote belong to the CURRENT cycle
       ∧ the quote's inputs are unchanged (candidate, category, zone)
       ∧ ¬ SEARCHING_NOT_READY (blocker)
       ∧ ¬ unresolved candidate conflict (blocker)
       ∧ ¬ unresolved inspection-location conflict (blocker)
```

Every clause above the blockers is a **positive prerequisite**: it must be proven, never
inferred from silence. The three blockers below are the opposite: they block when present and
prove nothing when absent.

**Why absence of a blocker is safe here (Part 3).** `SEARCHING_NOT_READY = NEITHER` does not
mean "ready", and the predicate never treats it as such — readiness is not a prerequisite for
ALLOW at all. What carries ALLOW is the conjunction of positive facts that no interpreter can
fabricate: a quote in the ledger, an unchanged identity, a present-tense acceptance in this
cycle. The interpreter's known weakness — sometimes omitting `SEARCHING_NOT_READY` beside
`FUTURE_INTENT` (L4.7B.4) — therefore cannot produce a false ALLOW: it can only fail to add a
block that the positive prerequisites already withhold.

One prerequisite was restated during implementation. The first version required the
acceptance claim to be `EXPLICIT_CUSTOMER`, which no stance can ever be: a stance is a reading
of words, not a substring found in them, so the rule refused **every** genuine acceptance.
The correct exclusion is narrower and sharper: an acceptance whose `explicitness` is
**DERIVED** — concluded from other facts, such as a state machine reading a proposed day as
agreement — can never authorise.

## 4. Legacy acceptance paths (Part 8)

| Site | Classification |
|---|---|
| `_is_acceptance`, `_has_acceptance_word` | **EVIDENCE_PRODUCER** (and today also the gate) |
| `conversation_engine.py:2811` `STAGE_QUOTED and _is_acceptance(...)` → `_handle_quoted_acceptance` | **WRITE_PATH** — sets `flag=ACEPTADO`, `stage=SCHEDULING` |
| `:2826 / :2836 / :2847` day-proposal-in-QUOTED → `flag=ACEPTADO` | **WRITE_PATH** (implicit acceptance) |
| `:2746` `_has_acceptance_word` guard on the AI branch | **BUSINESS_PRECONDITION** |
| `:3490–3503` AI-driven flag transitions with the deterministic-price guard | **BUSINESS_PRECONDITION + WRITE_PATH** |
| `:1934` Flow submitted → `flag=ACEPTADO`, `estado=COORDINAR_DISPONIBILIDAD` | **WRITE_PATH** (Flow is human-confirmed; stays) |
| `:5831` human-handoff path → `flag=ACEPTADO`, `estado=ATENCION_HUMANA` | **BUSINESS_PRECONDITION** |
| `_ALLOWED_FLAGS`, `PRESUPUESTO_ENVIADO` transitions | **TEMPORARY_COMPATIBILITY** |
| `_is_acceptance` / `_has_acceptance_word` as *gates* | **RETIRE_IN_C6** |

Nothing was changed. The map is the prerequisite for C3B.

## 5. Legacy versus new (Parts 9 & 10)

Every corpus case carrying stance, readiness or quote-request evidence, evaluated under four
quote scenarios:

| Scenario | AGREE_DENY | AGREE_ALLOW | NEW_SAFER | LEGACY_SAFER |
|---|---|---|---|---|
| quote delivered & current | 26 | 14 | 3 | **0** |
| quote computed, not delivered | 31 | — | 17 | **0** |
| quote stale (location moved) | 31 | — | 17 | **0** |
| quote from a previous cycle | 31 | — | 17 | **0** |
| **total** | **119** | **14** | **54** | **0** |

**LEGACY_SAFER: 0. UNEXPLAINED_DISAGREEMENT: 0.**

Five cases (`SYN-ACC-08/10/12/16/20` — "Buenísimo, seguimos", "Me parece bien", "Vamos con
eso", "Genial, coordinemos", "Bien, sigamos") are **new-allows-where-legacy-did-not**: all
five are corpus-labelled acceptances with a delivered, current quote that the legacy word
matcher does not recognise. Reviewed individually; none is a safety regression.

The most interesting NEW_SAFER case is **WILD-A-01** — *"Hola, ¿cómo están? **Bueno**, quería
revisar una 2008…"*. The legacy `_has_acceptance_word` guard fires on "bueno" in a greeting
with no acceptance in it. The authorizer holds: no acceptance stance exists. **That is a
false-progression risk in the system as it stands today**, found by running the two side by
side.

## 6. Safety metrics (Part 19 gate)

| Metric | Result |
|---|---|
| false progression (adversarial suite, 15 scenarios) | **0** |
| quote-staleness violations | **0** |
| prior-cycle progression | **0** |
| computed-but-not-delivered acceptance | **0** |
| unsupported acceptance authorization | **0** |
| LEGACY_SAFER (unexplained) | **0** |
| UNEXPLAINED_DISAGREEMENT | **0** |
| valid acceptance coverage | **19/21 = 90.5 %** |

The two coverage misses are both conservative failures, and both are named rather than
smoothed over:

* **WILD-A-03** *"Si avancemos | Que horarios hacen?"* — the unaccented "Si" trips the
  conditional cue, so a genuine acceptance reads as conditional and holds. A false negative,
  not a false progression, and the first thing C3B must fix.
* **SYN-ACC-14** *"Ok, cuándo pueden?"* — the interpreter emitted no ACCEPT stance at all;
  interpreter-side, not authorizer-side.

## 7. Live runtime probe

On `ridecheck-crm-backend:l4.7c3a-authshadow-c3e82ec` in crm_test:

```
quote identity                     781334181f929b74
explicit accept + delivered quote  ALLOW  explicit present acceptance of a delivered, current quote
conditional accept                 HOLD   acceptance is conditional or about the future
accept, quote never delivered      DENY   the quote was computed but never delivered
accept, location moved             DENY   the quote is stale: candidate, category or zone changed
no acceptance evidence             HOLD   no acceptance evidence in this turn
```

`whatsapp_thread_candidates` 0 → 0 · `whatsapp_messages` 6 → 6 · OUTBOUND OFF.

## 8. Shadow-only proof

The authorizer imports no ORM, no engine, no pricing, no scheduling, no booking, no outbound
gate (asserted), and its source contains no `.add(`, `.commit(`, `last_stage =`, `lead.flag`,
`ACEPTADO` or `_send_`. A test asserts that `conversation_engine.py` **does not reference
`authorize_quote_acceptance` at all** — C3A must not wire it into a write path, and does not.
Every decision record carries `shadow: true` and contains decisions and identifiers only: a
test asserts no customer text or vehicle/locality name reaches it.

## 9. Tests and regression

`tests/test_l4_7c_3a_acceptance_authorization_shadow.py` — **27/27 PASS** (AUTH-01…20 plus
derived-acceptance, rejection, blocker-with-acceptance, absent-readiness and PII checks).

Full regression: **3 475 passed / 60 failed / 9 errors** — failure set identical to baseline,
**zero new failures**. Launch-relevant failures: 0 new. Unknown: 0.

## 10. What C3A is not

It is not authority. `quote_accepted` is still decided by the legacy path; scheduling and
booking authority are untouched; `PricingService` remains the exclusive numeric authority and
the response validator still rejects unsupported acceptance and scheduling claims from the
text side. C2's vehicle and location authority remains enabled in crm_test and unchanged.

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.

Next: **L4.7C.3B-ACCEPTANCE-AUTHORITY-CUTOVER** — not automatic, and it should carry the
WILD-A-03 conditional-cue fix as its first item.
