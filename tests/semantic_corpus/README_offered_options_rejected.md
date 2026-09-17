# `offered_options_rejected.jsonl` — what this corpus is, and what it is NOT

**It is** an authored regression corpus for the deterministic grammar
`conversation_engine._rejects_every_offered_option` and the routing it feeds. 19 cases,
9 positive / 10 negative, each labelled with the business group it exercises.

**It is NOT** a held-out evaluation set for a semantic interpreter, and it must never be
presented as one. It was authored during L4.7W5-F7D while an LLM-based detector was being
tuned, and the prompt was revised three times against these same cases. Any score an LLM
obtains on it is therefore contaminated. That contamination is exactly why F7D was refused
certification and why L4.7W5-F7D-R2 shipped a deterministic grammar instead.

If semantic option-rejection modelling is revisited (deferred to the real-client
corpus/hybrid-engine phase), it needs a fresh corpus with a development set and a genuinely
held-out evaluation set, scored once, through `tests/semantic_corpus/evaluation.py`.

## Rules

* All 19 original cases and labels are preserved. Difficult cases are never deleted because
  a detector misses them.
* Additions require a note saying why.
* `OOR-P04` ("Esos horarios no me sirven.") is deliberately kept although the F7D-R2 grammar
  returns False for it: it carries no universal quantifier. The live path still escalates it
  through the pre-existing `_ESCALATION_KEYWORDS` floor, and the routing test asserts that.
  The case documents the boundary between the two detectors.
* `OOR-N07` ("Ninguno de esos **autos** me sirve.") is the discriminator: same quantifier,
  different noun. A rule that passes the positives and fails this one is wrong.

## Additions after the original 19

* `OOR-N11` "Capaz que ninguno, después te confirmo." — hedged; required by the
  F7D-R2 milestone as a negative control for conditional/hypothetical modality.
* `OOR-N12` "El viernes no puedo; el sábado a las 14 sí." — required by the F7D-R2
  milestone: partial rejection with an explicit alternative selected in the same turn.
* `OOR-N13`/`N14`/`N15` "No me sirve ese auto." / "…esa forma de pago." / "…el informe." —
  added by L4.7W5-F7E. Each was a **proven** false handoff: the rejection predicate
  `no me sirve` carried no object, so it escalated whatever the customer disliked. Together
  with `OOR-N06` (a phone call) and `OOR-N08` (the price) they are the five sentences that
  motivated the scope correction, and the F7E suite asserts each end to end.

* `OOR-P10` "Mmm, no me sirve" — the **elliptical rejection** from the live Wild of
  2026-09-17 that F7E failed to escalate. Positive **only while a scheduling offer is
  outstanding**: with nothing on the table it carries no object and means nothing
  schedulable. The F7G suite asserts both directions.
* `OOR-N17` "No tenés algo más temprano?" — the Wild burst's **second** message, and
  **NEGATIVE**. It is an alternative request, not a rejection: it asks for an earlier slot,
  and its correct answer is an earlier appointment — the separate open **F-03** defect, not a
  human handoff. It carries `belongs_to_burst: OOR-P10` and `burst_position: 2`, so it is
  evidence of the ordered burst and never an independently positive rejection utterance.
  F7G-R2 corrected this: the first cut escalated on either message alone, which absorbed
  F-03 and made the rescue rule too broad.
* `OOR-N16` "No me sirve la garantía." — added by F7G to prove the discriminator is
  **ellipsis, not vocabulary**: "garantía" appears in no lexicon, and the case is still
  negative because the rejection states an object.

## Which layer a case exercises

The corpus is scored at three levels, and a closeout must not report one as another:

1. **pure F7D-R2 grammar** — `_rejects_every_offered_option`
2. **legacy detectors** — `_earliest_option_rejected`, `_ESCALATION_KEYWORDS`
3. **complete live router** — `ConversationEngine.handle()`

A case may be negative at level 1 and positive at level 3 (or the reverse). The F7D-R2
closeout reported level-1 negatives as if they were level-3 negatives; F7E's suite
separates them explicitly.
