PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W1-F2-FUZZY-VEHICLE-GOVERNANCE

# A greeting is not a car

crm_test only · OUTBOUND OFF · production untouched · no threshold changed · no phrase patched
C2 / C3B / C4 / C4A unchanged and still ON · broader authority cleanup not started

---

## 1. Verdict

**PASS.** Fuzzy vehicle matching keeps the one capability that justifies it — recovering a
misspelt or partial vehicle name — and loses every form of authority it had. The exact Wild
burst now resolves to **Peugeot 2008 / 2014 / SUV_4X4_DEPORTIVO**, and "Fiat Uno" appears
nowhere: not as a candidate, not as a pending key, not as a question.

## 2. The five questions, answered before any code moved

**1 — What legitimate problem is it for?** Recovering a vehicle the customer named but
mistyped, or that ASR garbled: *ford fiestah*, *toyota corola*, *renolt clio*,
*chevrolet crkz*, *ford ksl*.

**2 — Is free-prose ngram scoring useful for that?** No. It is *harmful* for it. The
scorer slid a window across every word in the message, including greetings and FAQ
questions, and compared each against catalog make+model forms. Measured:

| text | old outcome | what actually matched |
|---|---|---|
| `Hola, buen día. Bueno, ¿era para revisar un 2008 del 2014?` | **CONFIRM Fiat Uno 0.706** | window `dia bueno` |
| `Ya hice el Formulario 12 y ahora quiero revisar el auto antes de comprarlo` | **CONFIRM Toyota Hiace 0.737** | ordinary prose |

The second is not from the Wild. It is a **certified test fixture that was already
failing**, and the failure had been carried in the baseline attributed to something else.
The defect had been in the suite the whole time.

**3 — What depends on it?** 27 test files touch the fuzzy machinery. Every input actually
handed to the scorer is a bounded vehicle fragment — `ford ksl`, `toyota corola`,
`renolt clio` — never a sentence. Two are negative controls (`xyz abc`, `hola buenas`),
which is to say the suite already believed prose must not match.

**4 — Can the legitimate cases be handled more safely?** Yes, by the same algorithm given
better input. Every legitimate case carries **catalog vocabulary**: a make token
(`ford`, `toyota`, `chevrolet`) or a model token (`clio`). The Wild text carried none in
the window that won.

**5 — Retain, constrain, replace or remove?**

> **DECISION: CONSTRAIN.** Keep the matcher, remove its authority, and bound its input to
> catalog-anchored fragments.

Removal would have cost real typo recovery for no safety gain, because the danger was never
the similarity metric — it was *what it was allowed to read* and *what it was allowed to do*.

## 3. The invariant

Not a threshold, not a phrase list:

> **Fuzzy similarity may only be computed over text anchored by catalog vocabulary, and the
> winning window must itself contain a catalog token.**

`_CATALOG_TOKENS` is derived from the catalog itself — every make token and every model
token. `extract_vehicle_fragments()` returns bounded windows (±2 words) around each anchor;
text with no anchor yields **no fragments and cannot be scored at all**. Inside
`_best_ngram_score`, a window containing no catalog token is skipped at any score.

`dia bueno` has no catalog token → **0.0**, at any threshold, forever.
`un 2008` has one → scored normally.
`renolt clio` anchors on the *model*, so a misspelt make still works.

**Regression-free by measurement.** All ten legitimate cases keep their exact prior outcome,
hit and score:

```
ford fiestah          AUTO_ACCEPT Ford Fiesta     0.957   (unchanged)
toyota corola         AUTO_ACCEPT Toyota Corolla  0.963   (unchanged)
chevrolet crkz        CONFIRM     Chevrolet Cruze 0.897   (unchanged)
renolt clio           CONFIRM     Renault Clio    0.870   (unchanged)
ford foco 2019 …      CONFIRM     Ford Focus      0.842   (unchanged)
ford ksl (+3 variants) CONFIRM    Ford Ka         0.800   (unchanged)
honda ranger          UNRESOLVED  —               0.636   (cross-brand guard intact)
hola buenas           UNRESOLVED  —      0.538 → 0.000
xyz abc               UNRESOLVED  —      0.421 → 0.000
WILD msg 1            CONFIRM Fiat Uno 0.706 → UNRESOLVED 0.632
```

## 4. Governance — fuzzy can no longer act

| capability | before | after |
|---|---|---|
| send to the customer | `_handle_fuzzy_confirm` sent directly | **impossible** — one composer, reached later |
| end the turn | `return self._handle_fuzzy_confirm(...)` | **removed** |
| write canonical identity | AUTO_ACCEPT wrote a candidate directly | must pass `reconcile_vehicle_identity` first |
| arm deferred authority | `pending_fuzzy_catalog_key` armed from a raw guess | armed only for a **reconciled, catalog-supported** identity |
| advance stage | set `last_intent` before anything checked it | only on an accepted identity |
| outrank other evidence | competed on score alone | `FUZZY_SUGGESTED`, the weakest evidence class |

`_handle_fuzzy_confirm` was **deleted, not unwired** — a dead terminal path is a path
waiting to be re-attached. A CONFIRM now sets `self._fuzzy_advisory` and falls through.

**Ordering was the other half of the bug.** The fuzzy branch returned ~120 lines *above*
the numeric-model rule and the `model del year` extractor — the certified producers for
"2008 del 2014". The advisory is now consumed at a single clarification point placed
*after* both, so deterministic evidence always gets its turn first.

## 5. Clarification safety

`_ask_vehicle_clarification` asks `¿Es un <Marca> <Modelo>?` **only** when the suggestion is
in the catalog *and* survives reconciliation. Otherwise the customer is asked
`¿Me podés indicar la marca y el modelo?` — a neutral question, not a guess to react to.
The pending key is armed only on the supported branch, so the Wild's "a later *Sí* creates
Fiat Uno" hazard is structurally impossible rather than merely unlikely.

## 6. Live reproduction, deployed image (Phase 9)

`ridecheck-crm-backend:l4.7w1f2-fuzzygov-e142a50`, restarts 0, parity MATCH on all three
touched modules:

```
msg 1       fuzzy=UNRESOLVED  score=0.632   fragments=('revisar un 2008 del 2014',)
msg 2       fuzzy=UNRESOLVED  score=0.000   fragments=()
msg 3       fuzzy=UNRESOLVED  score=0.000   fragments=()
full burst  fuzzy=UNRESOLVED  score=0.632   fragments=('revisar un 2008 del 2014',)

model_del_year(full burst) -> ('Peugeot', '2008', 'SUV_4X4_DEPORTIVO', 2014)
Fiat Uno anywhere            -> False
```

The greeting is excluded from the fragment; the vehicle region is kept. Required result met
exactly. **No diagnostic Fiat Uno is emitted anywhere** — the window scores 0.0, so there is
nothing to log.

## 7. FAQ coexistence (Phase 8, observed only)

Removing the early return means the burst now continues into the normal multi-intent
composition path instead of terminating at a vehicle question. That is the structural
precondition the milestone asked for, and it is met.

**Not fixed here, and not phrase-patched:** the deterministic FAQ detectors still miss all
four of the customer's questions (`mandan informe` ≠ "Mandan **un** informe";
`tengo que estar presente` ≠ "Deben estar presentes"; `aceptan debito` split across two
sentences; no `service_scope` phrase exists). The semantic layer detected `service_scope`
and `payment` correctly. Recorded for the authority-path work, per instruction.

## 8. Tests and regression

`tests/test_l4_7w1_f2_fuzzy_vehicle_governance.py` — **21/21**, FUZZGOV-01…16 plus the
invariant stated directly, fragment bounding, and the evidence-class check. They enter
through `_process_text`, the production chokepoint, because the previous certified fixture
missed this defect precisely by not doing so.

Two static governance tests: no send call inside the branch that contains the fuzzy call
(AST-scoped to the enclosing branch, not the 3 000-line function), and
`_handle_fuzzy_confirm` absent from the module.

Full regression: **3 591 passed / 57 failed / 9 errors** — **zero new failures**, and
**two resolved**: `test_si08_f12_past_context_pre_purchase_wins` and
`test_si_mx3_f12_past_context_plus_pre_purchase`, both of which were failing because
"Ya hice el Formulario 12…" scored CONFIRM *Toyota Hiace* 0.737 and hijacked the turn before
the AI path could run. Same defect class as the Wild, proven by direct measurement, not
assumed.

## 9. Boundaries held

C2, C3B, C4 and C4A unchanged and still ON. Pricing, scheduling policy, Booking Flow and
credentials untouched. `field_evidence.py`'s two fuzzy call sites inherit the same boundary
automatically and remain AUTO_ACCEPT-only evidence producers. crm_test rows unchanged
(4 / 0 / 0 — the F1 Wild evidence is preserved, nothing reset).

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.

Next: **WAIT FOR INDEPENDENT AUTHORITY AUDITS.**
