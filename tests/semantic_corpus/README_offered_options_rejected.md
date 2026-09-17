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
