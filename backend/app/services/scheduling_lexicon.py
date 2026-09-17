"""L4.7W5-F7D-R2 — the vocabulary the scheduling grammar needs, kept out of the engine.

`claim_projection` is held to a no-phrase-lists invariant (L4.7C-3B) and `acceptance_lexicon`
is the precedent for where words go instead. This module is the same idea for scheduling:
the ENGINE owns the grammar, this file owns the nouns the grammar quantifies over.

Nothing here is a sentence. These are two noun classes and one quantifier shape. The rule
that uses them — "a universal negative quantifier scoped to a scheduling object means the
customer rejected every option we offered" — lives in `conversation_engine`, and reading
these lists tells you nothing about which customer sentences pass.
"""
from __future__ import annotations

import re

# Accent-stripped, lowercase. Matched as whole words after `_norm_lower`.

# What a scheduling offer is made of. The object a universal quantifier must scope for the
# rejection to be about our calendar rather than about something else entirely.
SCHEDULING_OBJECTS: frozenset[str] = frozenset({
    "horario", "horarios", "turno", "turnos", "opcion", "opciones",
    "franja", "franjas", "fecha", "fechas", "hora", "horas",
})

# Objects a customer can also reject, which are NOT the offered slots. Their presence in the
# quantifier's sentence is what stops "ninguno de esos autos me sirve" from being read as a
# scheduling rejection — the discriminator is the noun, not the verb.
COMPETING_OBJECTS: frozenset[str] = frozenset({
    "auto", "autos", "vehiculo", "vehiculos", "camioneta", "camionetas",
    "modelo", "modelos", "marca", "marcas",
    "precio", "precios", "presupuesto", "presupuestos", "monto", "costo",
    "llamada", "llamadas", "mecanico", "mecanicos", "taller", "talleres",
})

# Universal negative quantifier: ningun / ninguno / ninguna (+ plural, which people write).
# "ningún" arrives accent-stripped as "ningun". This is a morphological shape, not a phrase.
UNIVERSAL_NEGATIVE = re.compile(r"\bningun(?:o|a|os|as)?\b")


def has_universal_negative(normalized: str) -> bool:
    return bool(UNIVERSAL_NEGATIVE.search(normalized))


def names_scheduling_object(normalized: str) -> bool:
    return any(w in SCHEDULING_OBJECTS for w in normalized.split())


def names_competing_object(normalized: str) -> bool:
    return any(w in COMPETING_OBJECTS for w in normalized.split())
