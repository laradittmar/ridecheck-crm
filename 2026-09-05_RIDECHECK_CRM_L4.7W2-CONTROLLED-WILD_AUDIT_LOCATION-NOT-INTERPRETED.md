PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7W2-CONTROLLED-WILD

# Why the location was not interpreted

Wild **STOPPED** by owner · no code changed · no tester reset · no outbound after the stop
Runtime `ridecheck-crm-backend:l4.7w1f4-prewild-23fbe02`, container unrestarted since 16:16:08Z

---

## 1. The conversation

| # | dir | transcript |
|---|---|---|
| 6065 | in | `Hola, ¿cómo andan? Quería revisar un 2008 del 2014. ¿Hacen eso?` |
| 6066 | in | `¿Trabajan con informe? ¿Cómo es? ¿Tengo que estar presente?` |
| 6067 | in | `¡Se paga con débito!` |
| 6068 | **out** | `¡Genial! Sí, hacemos revisiones del Peugeot 2008 del 2014. …informe detallado… más de 250 puntos… No es necesario que estés presente… aceptamos transferencia bancaria, Mercado Pago y efectivo, pero no débito ni tarjeta de crédito. ¿En qué zona o ciudad está el auto? Vamos hasta donde está el vehículo…` |
| 6069 | in | **`Ok, el auto está embarazado, Tegui.`** |
| 6070 | **out** | *(location Flow)* `Para calcular los viáticos de la revisión, completá dónde está el auto.` |

## 2. The answer, in one line

**The customer said "Berazategui". Whisper transcribed it as "embarazado, Tegui".** The word
never reached the system, so nothing downstream could resolve it.

`Berazategui` **is** in the catalog — one of 207 `zone_detail` values — and the intended
sentence resolves correctly. Measured on the deployed image:

```
'el auto está en Berazategui'            -> ViaticosZone(Berazategui)   ✓
'Ok, el auto está embarazado, Tegui.'    -> None                        ✗  (what arrived)
'Tegui'                                  -> None
'embarazado'                             -> None
```

This is an **ASR fragmentation** failure at the transport tier, not a reconciliation or
authority failure. `be-ra-za-TE-gui` was split into a real Spanish word plus an orphan
syllable.

## 3. But three layers had to line up for it to fail silently

**Layer 1 — the transcript.** `Berazategui` → `embarazado, Tegui`. n8n's Whisper node is
the only place the audio exists; the stored text *is* the transcript. Nothing later can
recover a word that was never written.

**Layer 2 — the semantic interpreter understood the situation and had no voice.** It got
the *structure* right:

```json
location_mentions[0] = {"value": "Tegui", "role": "INSPECTION_LOCATION",
                        "status": "PROPOSED",
                        "reason": "El cliente menciona que el auto está en Tegui."}
```

It correctly identified that **a location was named**, that its **role is the inspection
location** (not the customer's origin), and it correctly declined to invent a canonical
zone. That reading was recorded 1 989 ms *after* the reply was sent, and — by design since
L4.7C.4A — **no canonical path consumes semantic location evidence**. Same-turn semantic
evidence was wired to scheduling only. C2 location still consumes deterministic evidence
exclusively.

So the one component that understood "there is an inspection location here, and its name
starts with something like *Tegui*" had no way to say so.

**Layer 3 — the deterministic resolver matches whole names only.** `_extract_zone_from_text`
normalises accents and scans the sentence for catalog names and aliases. `Tegui` is a
*suffix* of `Berazategui`, not a match. There is no partial or fuzzy zone resolution — the
location domain has no equivalent of the vehicle catalog's fuzzy path.

Result: no zone evidence → `_apply_inspection_zone` never called → no
`L4.7C.2 RECONCILE claim=inspection_location` line in the whole session → CE fell through to
`M20 location_flow_direct` and asked via the Flow.

## 4. The system behaved *safely*, and that matters

It did not invent a zone. It did not guess "Tegui". It did not use the customer's origin.
It did not price anything. It asked, through the certified location Flow, with a correct
`CE_FLOW` attribution and a clean gate record. **Zero security events. Zero wrong canonical
writes.**

This is a **capability** failure, not a safety failure. The customer answered the question
and was asked it again — which is a bad experience, but not a dangerous one.

## 5. The trap that makes the obvious fix dangerous

The tempting fix is "match fragments of catalog names". Measured against the real catalog:

```
'tegui'        -> Berazategui (1.000)   ← unique, unambiguous, correct
'esta'         -> Floresta    (1.000)   ← "el auto ESTÁ en…" becomes a neighbourhood
'auto'         -> San Justo   (0.615)
'embarazado'   -> Burzaco     (0.588)
```

`"está"`, accent-stripped, is contained in **Floresta**. A naive substring or fuzzy rule
over whole sentences would resolve *"el auto está en …"* to Floresta — **exactly the defect
class L4.7W1-F2 closed for vehicles**, reproduced in the location domain, and this time it
would write a canonical inspection zone and a price rather than ask a question.

So: the resolver's strictness is not a bug. It is what stopped a worse outcome.

## 6. The architecturally correct direction (NOT implemented here)

F2 established the pattern and this Wild is the location-domain instance of it:

> the semantic layer identifies **what** was named and bounds it; the deterministic catalog
> decides **which canonical value** it is.

The interpreter already isolated the token `"Tegui"` and labelled it `INSPECTION_LOCATION`.
Matching *that bounded fragment* against the catalog is safe — it is a location mention by
construction. Matching `"está"` inside a sentence is not, because nothing established that
it was a place. The risk inverts entirely depending on whether the input is bounded.

Two prerequisites, both real work, neither done here:

1. **Consume semantic location evidence in the canonical location path** — the C4A cutover
   extended to `inspection_location`, with the same flag discipline and the same
   reconcile-before-write rule.
2. **Bounded fragment resolution for zones** — the F2 invariant applied to the zone
   catalog: resolve only within a span the semantic layer marked as a location, never over
   free prose, and require a unique catalog containment before proposing anything.

Even then this specific turn would produce a **confirmation** ("¿Berazategui?"), never a
silent canonical write — the customer's word was mangled, and unique-containment is
evidence, not proof.

## 7. What worked, and is worth recording

* **Vehicle**: `Peugeot / 2008 / 2014 / SUV_4X4_DEPORTIVO` persisted on turn 1 via
  `model_del_year`, reconciled `ACCEPT` under `reconcile.vehicle_identity@v1`. No Fiat Uno,
  no fuzzy advisory — F2 held on a *different* greeting from the one it was built against.
* **FAQ (F4 cutover, first live proof)**: report, presence and payment all answered, and the
  deterministic `service_scope` answer was appended. `250 puntos` and `no débito ni tarjeta
  de crédito` are **authoritative business truth from the CE prompt** (lines 6591–6592), not
  model invention — I checked before reporting.
* **Authority**: 2 outbound rows, both gated, `CE_TEXT` and `CE_FLOW`, deployment `23fbe02`.
  0 security events. 0 legacy n8n executions. **1 model call per burst**, both bursts.
* **Latency**: turn 1 `latency_ce_ms=5672`, turn 2 `947`.

## 8. Findings

**HIGH-1 — inspection location not interpreted from a fragmented transcript.** Root cause
ASR fragmentation; compounded by semantic location evidence being unconsumed and by the
absence of bounded fragment resolution for zones. Customer was asked to repeat information
already given. Not a safety defect.

**MEDIUM-1 — FAQ supplement duplicated content already in the reply.** The AI reply already
said *"incluye un informe detallado tras la revisión"*; the deterministic `service_scope`
answer was still appended because its probe (`revisión pre-compra`) was absent. Probe-based
de-duplication is per-topic and literal, so it misses a *semantic* overlap. The reply is
longer and slightly repetitive.

**LOW-1 — turn-1 latency 5.7 s.** Within tolerance behind the 20 s debounce, recorded.

**BLOCKER: NONE. Wrong canonical writes: 0. False progression: 0. Unauthorized outbound: 0.**

## 9. Evidence

```
/opt/ridecheck-crm-forensics/L4.7W2_backend_stdout_2026-09-05T165904Z.log
   sha256 91a2f1190eac05fe0b97885a1883d3e6e8d61162d75e1401849a546b706b7d99
/opt/ridecheck-crm-forensics/L4.7W2_tester_export_pre_reset_2026-09-05T161532Z.txt
   sha256 16b93ef6a9f5849ba16c9455e4a2ec3f55ef8a494ea6cf0761bf8afe6be7d492
shadow_turn_evidence.jsonl 48 -> 50   reconciliation_records.jsonl 5 -> 7
authorization_records.jsonl 12 -> 12  security_events 734 -> 734
```

Thread 2039, candidate 131, lead 125 (`PRESUPUESTANDO`) left **untouched** for inspection.
Outbound remains armed as the owner set it; the kill switch is one command.

Wild clean count remains **0/3** — this session is not clean.
