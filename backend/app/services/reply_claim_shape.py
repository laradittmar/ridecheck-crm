"""R6 (R5F1RV-01) — when does outbound prose CLAIM a vehicle?

The catalogue resolver answers "does this text contain a catalogue alias?". That is the
right question for a customer's message and the wrong one for our own outbound prose: the
catalogue's model words are also ordinary Spanish ("uno", "punto", "idea", "clase"…), so a
token occurrence is not a claim. The R6 owner decision forbids a list of such words; the
rule here is a CONSTRUCTION rule and names no model:

A catalogue match in outbound text is a vehicle CLAIM when, and only when,
  (a) the make is written with the model           "Fiat Uno", "un Toyota Corolla";
  (b) the model carries a model year                "un Uno 2015", "un 2008 del 2014"; or
  (c) the model is written as a proper noun (capitalised, not sentence-initial, or a
      numeric model) and introduced by a determiner  "Es un Corolla", "revisamos tu Gol";
  (d) R6-C1 (R6IRV-S02): whatever its case, the model is the POSSESSIVE OBJECT of an
      inspection/booking action in the same clause  "Perfecto, revisamos tu gol mañana",
      "coordinamos la revisión de tu gol"; or
  (e) R6-C1 (R6IRV-S02): the model is a left-dislocated TOPIC — first in the sentence, set
      off by a comma — resumed by an object clitic of an inspection/booking action
                                                      "Gol, perfecto, lo revisamos."
Rules (d) and (e) are frames, not words: "tu idea" outside an action clause, "Uno nunca
sabe", "uno por uno", "punto por punto" and "buena idea" match neither.
Anything else is a MENTION: the validator records it as a diagnostic and it is neither a
claim nor a state change ("uno por uno", "uno de los dos autos", "a las 10 en punto",
"buena idea", "punto por punto", "Uno nunca sabe…").

This module is used only for text WE send. Customer evidence keeps its own interpreters.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional

from .response_validator import is_transactional_token
from .vehicle_catalog import _norm as _catalog_norm

_POSSESSIVES = frozenset({"tu", "su", "mi", "nuestro", "nuestra", "vuestro", "vuestra"})
_OBJECT_CLITICS = frozenset({"lo", "la", "los", "las"})
_DETERMINERS = frozenset({"un", "una", "el", "la", "mi", "tu", "su", "este", "esta",
                          "ese", "esa", "del", "al", "nuestro", "nuestra"})
_YEAR_MIN, _YEAR_MAX = 1990, 2035


@dataclass(frozen=True)
class VehicleMention:
    """A catalogue match in outbound text, with whether its construction is a claim."""
    marca: str
    modelo: str
    claim_shaped: bool
    reason: str
    match: object = None


def _orig_tokens(fragment: str) -> list[str]:
    """Tokens aligned 1:1 with `vehicle_catalog._norm(fragment).split()`, case kept."""
    s = unicodedata.normalize("NFD", fragment or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.findall(r"[A-Za-z0-9]+", s)


def _is_year(tok: str) -> bool:
    return tok.isdigit() and len(tok) == 4 and _YEAR_MIN <= int(tok) <= _YEAR_MAX


def _sentence_initial(orig: str, fragment: str, index: int, tokens: list[str]) -> bool:
    if index == 0:
        return True
    # the previous non-space character before this token in the original text
    stripped = unicodedata.normalize("NFD", fragment or "")
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    pos = 0
    for i, t in enumerate(tokens):
        pos = stripped.find(t, pos)
        if i == index:
            break
        pos += len(t)
    before = stripped[:max(pos, 0)].rstrip()
    return not before or before[-1] in ".!?¡¿\n"


def _clause_of_tokens(fragment: str, n: int) -> list[int]:
    """Clause index (split at , ; : and dashes) of each of the `n` normalised tokens.

    If the per-clause tokenisation does not align with the whole-sentence one, every
    token is put in ONE clause — the stricter reading (more frames can match)."""
    out: list[int] = []
    for ci, piece in enumerate(re.split(r"[,;:\u2013\u2014]", fragment or "")):
        out.extend([ci] * len(_catalog_norm(piece).split()))
    return out if len(out) == n else [0] * n


def _possessive_action_object(lower: list[str], clause: list[int], i: int) -> bool:
    """(d) `lower[i]` (the model) follows a possessive, and an inspection/booking action
    precedes it in the same clause."""
    if i < 1 or lower[i - 1] not in _POSSESSIVES:
        return False
    return any(clause[j] == clause[i] and is_transactional_token(lower[j])
               for j in range(i - 1))


def _dislocated_topic(fragment: str, lower: list[str], clause: list[int], i: int) -> bool:
    """(e) `lower[i]` opens the sentence, a comma follows it, and a LATER clause holds an
    object clitic immediately followed by an inspection/booking action."""
    if i != 0 or not re.match(r"\s*[\w\u00c0-\u024f]+\s*,", fragment or ""):
        return False
    return any(clause[j] > clause[i] and lower[j] in _OBJECT_CLITICS
               and is_transactional_token(lower[j + 1]) for j in range(1, len(lower) - 1))


def classify_vehicle_mention(fragment: str, match) -> Optional[VehicleMention]:
    """Classify one resolver match in one outbound sentence. Never raises."""
    if match is None:
        return None
    marca = getattr(match, "marca", "") or ""
    modelo = str(getattr(match, "modelo", "") or "")
    try:
        lower = _catalog_norm(fragment).split()
        orig = _orig_tokens(fragment)
        if len(orig) != len(lower):          # defensive: alignment failed → be conservative
            orig = lower
        make_toks = _catalog_norm(marca).split()
        model_toks = _catalog_norm(modelo).split()
        alias = (_catalog_norm(getattr(match, "matched_alias", "") or "") or
                 " ".join(model_toks)).split()

        # where is the model written?
        def _find(needle):
            k = len(needle)
            return [i for i in range(len(lower) - k + 1) if k and lower[i:i + k] == needle]

        model_at = _find(model_toks) or _find(alias[-1:]) or _find(alias)
        if not model_at:
            return VehicleMention(marca, modelo, False, "model not located", match)

        # (a) make written with the model: a brand-prefixed alias, or the make right before
        if len(alias) > len(model_toks) and alias[-len(model_toks):] == model_toks \
                and _find(alias):
            return VehicleMention(marca, modelo, True, "make and model", match)
        for i in model_at:
            k = len(make_toks)
            if k and i - k >= 0 and lower[i - k:i] == make_toks:
                return VehicleMention(marca, modelo, True, "make and model", match)

        for i in model_at:
            end = i + max(len(model_toks), 1)
            # (b) a model year: "Uno 2015", "2008 del 2014", "Corolla modelo 2018"
            nxt = lower[end:end + 3]
            if nxt and (_is_year(nxt[0]) or (len(nxt) > 1 and nxt[0] in ("del", "de", "modelo")
                                             and _is_year(nxt[1]))):
                return VehicleMention(marca, modelo, True, "model with model year", match)
            # (c) a proper noun introduced by a determiner
            prev = lower[i - 1] if i > 0 else ""
            first = orig[i] if i < len(orig) else ""
            proper = bool(first) and (first[0].isdigit() or first[0].isupper())
            if prev in _DETERMINERS and proper and not _sentence_initial(
                    first, fragment, i, orig):
                return VehicleMention(marca, modelo, True, "proper noun after a determiner",
                                      match)
        clause = _clause_of_tokens(fragment, len(lower))
        for i in model_at:
            if _possessive_action_object(lower, clause, i):
                return VehicleMention(marca, modelo, True,
                                      "possessive object of an inspection/booking action",
                                      match)
            if _dislocated_topic(fragment, lower, clause, i):
                return VehicleMention(marca, modelo, True,
                                      "dislocated topic resumed by an action clitic", match)
        return VehicleMention(marca, modelo, False, "token without a vehicle claim frame",
                              match)
    except Exception:
        # A classification defect must not turn a mention into a claim, nor a claim into
        # silence: treat it as a claim (the stricter reading) so the validator checks it.
        return VehicleMention(marca, modelo, True, "classification failed (strict)", match)


def outbound_vehicle_claim(fragment: str,
                           raw_resolver: Callable[[str], object]) -> Optional[VehicleMention]:
    """The resolver CE gives the validator: raw catalogue match + claim-shape verdict."""
    return classify_vehicle_mention(fragment, raw_resolver(fragment))
