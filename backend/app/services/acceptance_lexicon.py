"""Deterministic Spanish acceptance vocabulary, and the clause-scoped predicate over it.

L4.7W5-F7B. This lexicon used to live in `conversation_engine`. The canonical acceptance
producer in `claim_projection` needs it too, and putting it there would have been wrong:
`claim_projection` is the semantic layer, and L4.7C-3B holds it to a grammatical
invariant — no phrase lists in its executable code, so stance is read, never matched.
A test enforces that, and it caught this move immediately.

So the vocabulary gets its own home. `conversation_engine._is_acceptance` and
`claim_projection.acceptance_claims` both read from here: one lexicon, one predicate,
no second copy to drift, and the semantic layer stays phrase-free.
"""
from __future__ import annotations

import re
from typing import Iterable

ACCEPTANCE_KEYWORDS = frozenset({
    "sí", "si", "yes", "ok", "okay", "dale", "perfecto", "avancemos",
    "listo", "buenísimo", "me sirve", "bueno", "claro",
    "de acuerdo", "por supuesto", "quiero avanzar",
})

# A clause that declines. Narrow on purpose: it only SUPPRESSES a deterministic
# acceptance claim, and never creates a REJECT claim — that stays the semantic
# projection's job, so rejection authority is not duplicated either.
_DECLINE_RE = re.compile(
    r"\b(?:no\s+avancemos|no\s+quiero|no\s+me\s+sirve|mejor\s+no|cancel\w*|"
    r"dej[aá]moslo|otro\s+d[ií]a)\b", re.IGNORECASE)

_CLAUSE_SPLIT_RE = re.compile(r"[,.;!?\n]| pero | aunque ")


def acceptance_clauses(texts: Iterable[str]) -> list[str]:
    """Split a burst into clauses, on the boundaries `acceptance_modality` scopes by."""
    out: list[str] = []
    for text in texts:
        if not isinstance(text, str):
            continue
        out.extend(part.strip() for part in _CLAUSE_SPLIT_RE.split(text)
                   if part and part.strip())
    return out


def clause_is_acceptance(clause: str) -> bool:
    """Every word in this clause is an acceptance word — the `_is_acceptance` rule,
    applied to a clause instead of to the whole burst."""
    normalized = clause.lower().strip("!.¡¿? ").strip()
    if normalized in ACCEPTANCE_KEYWORDS:
        return True
    words = re.sub(r"[^\w\s]", " ", normalized).split()
    return bool(words) and all(w in ACCEPTANCE_KEYWORDS for w in words)


def declines(texts: Iterable[str]) -> bool:
    """True when any clause in the burst explicitly declines to advance."""
    return any(_DECLINE_RE.search(clause) for clause in acceptance_clauses(texts))


def may_be_acceptance(texts: Iterable[str]) -> bool:
    """Permissive ROUTING check: could this burst be an acceptance at all?

    L4.7W5-F7B. The QUOTED router gated on `_is_acceptance`, which demands the WHOLE burst
    be acceptance words. For "si" + "para cuando tenes" it returned False, so the engine
    never entered the acceptance branch and never called the authorizer — the canonical
    producer was unreachable on the live path no matter how correct it was. That is why
    F6 passed at helper level while the real conversation deadlocked.

    Routing and authority are now properly separated: this asks only whether some clause
    states acceptance outright, and `authorize.quote_acceptance@v1` still decides. A burst
    that routes here but does not authorise falls through to normal handling exactly as
    before, so widening the question widens no permission.
    """
    return any(clause_is_acceptance(clause) for clause in acceptance_clauses(texts))
