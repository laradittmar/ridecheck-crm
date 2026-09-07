PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W2-F1-VOICE-LOCATION-RECOVERY

# A corrupted locality becomes a question, not a guess

crm_test only · OUTBOUND OFF · production DB untouched · no ASR change · no phrase patch
C2 / C3B / C4 / C4A / F2 / F3 / F4 all ON and unchanged · C5 not started

---

## 1. Verdict

**PASS.** The Wild W2 transcript now recovers `Berazategui` and **asks the customer to
confirm it**. Nothing canonical is written until they do, and the module that does the
matching cannot be handed a sentence.

## 2. W2 root cause

The customer said *"el auto está en Berazategui"*. Whisper stored
**"Ok, el auto está embarazado, Tegui."** — the locality split into a real Spanish word plus
an orphan syllable. Three things then had to line up:

1. the word never reached the system (ASR, transport tier);
2. the semantic interpreter got the structure right —
   `{"value": "Tegui", "role": "INSPECTION_LOCATION"}` — and **had no consumer**, because
   C4A wired same-turn evidence to scheduling only;
3. the deterministic resolver matches whole names, and `Tegui` is a suffix of
   `Berazategui`, not a match.

Result: no zone evidence, no `RECONCILE claim=inspection_location` anywhere in the session,
and CE fell through to the Location Flow — safely, but asking a question the customer had
just answered.

## 3. Why the obvious fix is the dangerous one

Measured against the real 207-name catalog:

```
'tegui'  contained in 'Berazategui'   the recovery we want
'esta'   contained in 'Floresta'      "el auto ESTÁ en…" becomes a neighbourhood
```

`_score("esta", "Floresta")` = **0.80**, above this module's own 0.72 threshold. And no
purely lexical rule separates the two: both are mid-word suffixes covering about half their
catalog name. Sentence-wide fuzzy matching would reproduce the F2 defect in the location
domain — and here it would fix a *price*, not just ask a question.

What separates them is that something first established `Tegui` is a place name and `está`
is a verb. So the protection cannot live in the scorer. It lives in what the scorer may see.

## 4. The invariant

> Approximate locality matching may only run on a span already established to name a place.

`resolve_locality_fragment()` takes a **`LocationFragment`**, never a string — passing raw
text raises `TypeError`. A fragment can only be built through `from_evidence()`, which
refuses unless **all** of the following hold:

| gate | why |
|---|---|
| ≤ 4 words | a phrase is not a name |
| ≥ 4 characters | below that a token carries no signal |
| role ∈ {INSPECTION_LOCATION, VEHICLE_LOCATION} | origin never becomes inspection location |
| proper-noun-shaped in the source message | Spanish localities are proper nouns; verbs and greetings are not |

The last gate is a property of the language, not a list of blocked words, and it is what
stops `está` even if some producer mislabels it. Verified on the deployed image:

```
'Tegui'                            -> APPROXIMATE  Berazategui / Sur  0.782
'Berazategui'                      -> EXACT        Berazategui / Sur  1.000
'Berazatgui' (typo)                -> APPROXIMATE  Berazategui        0.952
'está' / 'esta' / 'buen día'       -> FRAGMENT REFUSED
'Tigre' with role CUSTOMER_ORIGIN  -> NONE (role rejected before scoring)
'el auto está embarazado, Tegui'   -> FRAGMENT REFUSED (a sentence is not a name)
```

## 5. Unique match is not authority

ASR corruption is uncertainty about **what was said**. A confident match on a corrupted
token is still a guess, so `APPROXIMATE` never writes:

```
APPROXIMATE -> arm pending_location_proposal = "Sur||Berazategui||<cycle_id>"
            -> ask "¿El auto está en Berazategui?"
            -> customer confirms -> _apply_inspection_zone (C2, source=flow)
```

The cycle id is **inside the stored key**, so a proposal from a finished cycle can never be
confirmed by a later "Sí". Three outcomes clear it: an explicit new locality this turn
(correction wins), confirmation, and rejection — and only the confirmation branch reaches
the canonical writer. A cycle reset clears it with everything else.

`AMBIGUOUS` (two candidates within 0.08) and `NONE` both fall through to the certified
Location Flow. Safety before forced recovery.

## 6. Same-turn semantic evidence — the architecture question

**Minimum change, and it was small:** location now reads the *same*
`TurnSemanticEvidence` provider that C4A already dispatches at the top of the turn.
`_semantic_location_fragment()` calls `self._semantic_turn_evidence()` and nothing else —
no interpreter import, no `interpret()` call.

**Model calls per burst: 1**, unchanged. The provider is single-flight; the W2 evidence was
always there, it simply had no reader.

## 7. Live end-to-end, deployed image, real model and catalog

```
'Ok, el auto está embarazado, Tegui.'
   semantic  [('INSPECTION_LOCATION', 'Tegui')]
   fragment  'Tegui'          match APPROXIMATE  Berazategui/Sur 0.782
   canonical NO — confirmation required
   customer  ¿El auto está en Berazategui?

'Yo soy de Tigre pero el auto está en Berazategui'
   semantic  [('CUSTOMER_ORIGIN','Tigre'), ('INSPECTION_LOCATION','Berazategui')]
   fragment  'Berazategui'    match EXACT  →  origin never considered

'Hola, buen día. ¿Cómo trabajan ustedes?'
   semantic  []  →  no fragment  →  certified Location Flow
```

## 8. ASR / Whisper audit — read-only, nothing changed

The n8n `Transcribe Audio` node does not call OpenAI; it posts to
`/api/whatsapp/media/{id}/`, and `_transcribe_audio_bytes` builds the multipart request:
`model=whisper-1`, `language=es`, **and no `prompt` field**.

The Whisper API *does* support a `prompt` context hint, so a bounded locality hint is
technically available and could plausibly reduce this error class. **I did not add one.**
Proving it does not bias unrelated speech needs audio fixtures I do not have, and a hint
that nudges every transcript toward locality names is a new failure mode, not a fix. The
system is now robust to imperfect ASR regardless — which was the point.

Recorded for a future, evidence-backed milestone.

## 9. Boundaries and regression

New: `locality_resolver.py` (no ORM, no session, no candidate, no
`_apply_inspection_zone` — asserted by test), one nullable column
`pending_location_proposal`, migration `20260906_pending_location_proposal` re-parented onto
the true head `20260901_l4_1` after I found it would otherwise have **branched the migration
graph**. Applied to **crm_test only**; verified absent from production (`0` columns).

Unchanged: coverage, viáticos, zone pricing, travel fallback, ScheduleService, Booking Flow,
FAQ logic. The W2 MEDIUM (FAQ semantic-overlap duplication) is **recorded, not patched**.

Tests: `test_l4_7w2_f1_voice_location_recovery.py` **19/19** (VOICELOC-01…18 plus static
governance: the resolver refuses non-fragments, no caller passes raw prose, and it cannot
write canonical state).

Full regression: **3 633 passed / 57 failed / 9 errors** — failure set identical to the F4
baseline, **0 new**.

Runtime `ridecheck-crm-backend:l4.7w2f1-voiceloc-3f18f63`, restarts 0, parity MATCH,
`outbound=False`.

---

L1/L2/L3 FROZEN · L4 ACTIVE · Wild clean count **0/3**.
Next: **CONTROLLED OWNER WILD** — on your authorisation, not automatically.
