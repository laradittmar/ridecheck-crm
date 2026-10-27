"""W1F-W-01 R3 — locality text: one description, two consumers, three validators.

WHY THIS MODULE EXISTS
----------------------
The W1F-W-01 architectural review (2026-10-26) measured a duplication hazard, finding
W1FRV-07: the vehicle-location clause patterns, the customer-origin language, the
same-turn uncertainty markers and the locative-context rule existed TWICE — once in
`conversation_engine.py` and once in `field_evidence.py`, whose own comment read
"replicated from conversation_engine.py to avoid a circular import". Two copies of the
predicate that decides whether a customer's locality was understood is a defect waiting
for a one-sided fix.

This module is that description, once. It is a LEAF: it imports nothing from this package,
so both consumers import it with no cycle, and both bind the SAME objects — asserted by
identity (`is`), not by equality, in `tests/test_w1f_w01_r3_locality_safety.py`.

It also owns the three R3 validators. None of them routes a turn and none decides whether
evidence is retained by a router; each answers one question about a locality name:

  * `is_outside_coverage`        — is this a place we have already declared we do not serve?
  * `composite_name_conflict`    — is the matched catalogue name only a PART of a longer,
                                   connector-joined proper name present in this text?
                                   "mar del plata" is not "La Plata";
                                   "san miguel del monte" is not "San Miguel".
  * `is_cross_coverage_ambiguous` — is this name shared with a well-known locality OUTSIDE
                                   the coverage area, so that choosing silently is a guess?

Nothing here reads the viáticos catalogue: the catalogue remains the only authority on
whether a locality exists, and `PricingService` / `ScheduleService` / `BookingAuthority`
remain the only authorities on what it costs and when it can be visited. These functions
can refuse a locality; they can never invent one.
"""
from __future__ import annotations

import re
import unicodedata


# ── text normalisation ───────────────────────────────────────────────────────
def strip_accents(text: str) -> str:
    """Decompose text and drop combining diacritical marks → ASCII-safe lowercase.

    The single implementation. `conversation_engine._strip_accents` is bound to this
    function, so "Benavídez" and "Benavidez" can never normalise differently in one
    consumer and not the other.
    """
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def norm_locality_text(text: str) -> str:
    """Accent-stripped, lowercased, whitespace-collapsed."""
    return " ".join(strip_accents(text or "").split())


# ── vehicle-location clauses ─────────────────────────────────────────────────
# Each pattern captures a locality (1–4 words). The LAST pattern is the alt-location
# ("o puede ser X") contradiction signal: both consumers rely on it being last —
# `field_evidence` reads `[:-1]` for localities and `[-1]` for the contradiction — so
# `clause_patterns()` always returns it last, plural patterns included.
_SINGULAR_VEHICLE_LOCATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r'\bel\s+(?:auto|veh[ií]culo|coche|m[oó]vil)\s+(?:ahora\s+)?'
        r'est[aá](?:\s+ubicad[ao])?\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(r'\best[aá]\s+para\s+revisar\s+en\s+((?:\w+\s*){1,4})', re.IGNORECASE),
    re.compile(r'\bla\s+revisi[oó]n\s+ser[ií]a\s+en\s+((?:\w+\s*){1,4})', re.IGNORECASE),
    re.compile(r'\bhay\s+que\s+verlo\s+en\s+((?:\w+\s*){1,4})', re.IGNORECASE),
    re.compile(r'\blo\s+tienen\s+en\s+((?:\w+\s*){1,4})', re.IGNORECASE),
    re.compile(r'\best[aá]\s+ubicad[ao]\s+en\s+((?:\w+\s*){1,4})', re.IGNORECASE),
)

# ── R3 item 4: plural vehicle-location clauses ───────────────────────────────
# W1F-W-01's own turn 1 said "los autos están en Flores" and NO clause pattern matched it
# (finding W1FRV-05): the singular set above requires "el auto". A plural subject is the
# same claim about the same inspection location — a customer with two cars at one address
# is ordinary — so the plural forms capture the locality the same way, with the same
# 1–4-word bound and the same single capture group. These are EVIDENCE patterns: a match
# contributes a locality that still has to survive the catalogue and the validators below.
_PLURAL_VEHICLE_LOCATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r'\b(?:los|las|mis|ambos|ambas|dos|tres)?\s*'
        r'(?:autos|veh[ií]culos|coches|m[oó]viles|camionetas|utilitarios)\s+'
        r'(?:ahora\s+)?est[aá]n(?:\s+ubicad[ao]s)?\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(
        r'\b(?:autos|veh[ií]culos|coches|camionetas)\s+'
        r'est[aá]n\s+para\s+revisar\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(
        r'\bhay\s+que\s+verlos\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
    re.compile(
        r'\blos\s+tienen\s+en\s+((?:\w+\s*){1,4})',
        re.IGNORECASE,
    ),
)

_ALT_LOCATION_PATTERN: re.Pattern[str] = re.compile(
    r'\bo\s+puede\s+(?:ser|estar)\s+(?:en\s+)?((?:\w+\s*){1,3})', re.IGNORECASE)


def clause_patterns(*, plural: bool = False) -> tuple[re.Pattern[str], ...]:
    """The vehicle-location clause patterns, alt-location pattern always LAST.

    `plural=False` returns exactly the inherited seven patterns, in the inherited order,
    so the behaviour with `LOCALITY_RESOLUTION_SAFETY_ENABLED` off is the parent's.
    """
    middle = _PLURAL_VEHICLE_LOCATION_PATTERNS if plural else ()
    return _SINGULAR_VEHICLE_LOCATION_PATTERNS + middle + (_ALT_LOCATION_PATTERN,)


#: The inherited list, for the consumers' module-level names and for existing tests.
VEHICLE_LOCATION_CLAUSE_PATTERNS: list[re.Pattern[str]] = list(clause_patterns())

# ── customer-origin language (LR-2) ──────────────────────────────────────────
# One phrase set; the two regex shapes the consumers need are built from it, so a phrase
# can never be added to the detector and forgotten in the capture.
_CUSTOMER_ORIGIN_PHRASES = r'soy\s+de|vivo\s+en|estoy\s+en|vengo\s+de|me\s+encuentro\s+en'

CUSTOMER_ORIGIN_RE = re.compile(rf'\b(?:{_CUSTOMER_ORIGIN_PHRASES})\b', re.IGNORECASE)
CUSTOMER_ORIGIN_CAPTURE_RE = re.compile(
    rf'\b(?:{_CUSTOMER_ORIGIN_PHRASES})\s+((?:\w+\s*){{1,3}})', re.IGNORECASE)

LOCATION_UNCERTAINTY_RE = re.compile(
    r'\b(?:o\s+puede\s+(?:ser|estar)|no\s+s[eé]|no\s+estoy\s+segur[ao]|'
    r'tal\s+vez|quiz[aá]s)\b',
    re.IGNORECASE,
)

ORIGIN_CLAUSE_SPLIT_RE = re.compile(r'(?:[,.;]|\bpero\b|\baunque\b)', re.IGNORECASE)


def has_customer_origin_clause(text: str) -> bool:
    """True when text contains explicit customer-origin language (LR-2)."""
    return bool(CUSTOMER_ORIGIN_RE.search(text or ""))


def strip_customer_origin_clauses(text: str) -> str:
    """Return *text* without the clauses that state where the CUSTOMER is."""
    parts = ORIGIN_CLAUSE_SPLIT_RE.split(text or "")
    kept = [p.strip() for p in parts if p.strip() and not CUSTOMER_ORIGIN_RE.search(p)]
    return ". ".join(kept)


# ── article-prefixed localities (L4.7W5-F5) ──────────────────────────────────
ARTICLE_PREFIX_RE = re.compile(r"^(?:la|el|los|las)\s+")

# A locality whose name is also a common noun is only a locality when the sentence places
# something there. "en la boca" is a place; "me mordí la boca" is not.
LOCATIVE_MARKERS = (r"(?:en|de|del|desde|hacia|para|por|zona|barrio|localidad|partido|"
                    r"ciudad|esta|estoy|queda|vivo)")


def locality_in_context(normalized_text: str, form: str) -> bool:
    """True when `form` appears as a locality, not as incidental vocabulary.

    Accepted when the form is preceded by a locative marker (optionally with its article),
    or when it is effectively the whole message — a bare "paternal" answering "¿en qué zona
    está el auto?" is a location, and demanding a preposition there would be pedantic.

    Word boundaries throughout: an alias must never fire from inside a longer word.
    """
    escaped = re.escape(form)
    if re.fullmatch(rf"(?:la|el|los|las)?\s*{escaped}\s*[.!?]*", normalized_text.strip()):
        return True
    pattern = rf"\b{LOCATIVE_MARKERS}\b\s+(?:la|el|los|las)?\s*\b{escaped}\b"
    return re.search(pattern, normalized_text) is not None


# ── validator 1: declared coverage boundary ──────────────────────────────────
# Clearly out-of-coverage cities/provinces. Accent-stripped lowercase forms. GBA
# localities are NOT listed here. This is the inherited set, unchanged; R3 changes only
# WHEN it is consulted — before a zone is written, instead of after.
OUTSIDE_COVERAGE_CITIES: frozenset[str] = frozenset({
    "cordoba", "rosario", "mendoza", "tucuman", "santa fe",
    "mar del plata", "salta", "jujuy", "san juan", "san luis",
    "neuquen", "bariloche", "bahia blanca", "resistencia",
    "corrientes", "posadas", "formosa", "rio cuarto",
    "comodoro rivadavia", "rio gallegos", "santa rosa", "rawson", "ushuaia",
})


def is_outside_coverage(text: str) -> bool:
    return any(city in norm_locality_text(text) for city in OUTSIDE_COVERAGE_CITIES)


# ── validator 2: composite-name integrity ────────────────────────────────────
# The connectors that join the words of a multi-word Argentine place name.
_NAME_CONNECTORS: frozenset[str] = frozenset({
    "de", "del", "de la", "de los", "de las", "la", "el", "los", "las",
})

# Words that can sit immediately before or after a connector WITHOUT being part of a
# proper name: function words, the verbs customers use to place a thing somewhere, the
# vehicle nouns, and the operational nouns. "soy de Tigre" is Tigre; "mar del Plata" is
# not La Plata. This list decides which of those two shapes a text has, and it never
# names a locality — a new city never has to be added here.
_NAME_STOP_TOKENS: frozenset[str] = frozenset({
    # function words and pronouns
    "en", "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "al",
    "y", "o", "u", "pero", "aunque", "que", "qué", "mi", "mis", "su", "sus", "tu", "tus",
    "me", "te", "se", "yo", "vos", "usted", "nosotros", "ellos", "ella", "este", "esta",
    "esto", "ese", "esa", "eso", "aca", "aqui", "alla", "alli", "ahi", "muy", "mas",
    "para", "por", "con", "sin", "desde", "hasta", "hacia", "sobre", "entre", "tambien",
    # the verbs that place something
    "soy", "somos", "sos", "es", "son", "era", "eran", "fui", "fue", "estoy", "estamos",
    "esta", "estan", "estaba", "estaban", "vivo", "vivimos", "vive", "viven", "vengo",
    "venimos", "viene", "vienen", "queda", "quedan", "tengo", "tenemos", "tiene",
    "tienen", "ubicado", "ubicada", "ubicados", "ubicadas", "trabajo", "trabaja",
    "salgo", "voy", "vamos", "puedo", "podemos", "necesito", "necesitamos", "quiero",
    "queremos", "hay", "seria", "serian", "revisar", "verlo", "verlos", "ver",
    # the vehicle and the service
    "auto", "autos", "vehiculo", "vehiculos", "coche", "coches", "movil", "moviles",
    "moto", "motos", "camioneta", "camionetas", "utilitario", "utilitarios", "chata",
    "revision", "revisiones", "turno", "turnos", "precio", "precios", "costo", "presupuesto",
    # the geography words that qualify rather than name
    "zona", "zonas", "barrio", "barrios", "localidad", "localidades", "partido",
    "provincia", "pcia", "ciudad", "capital", "federal", "caba", "gba", "amba",
    "centro", "norte", "sur", "este", "oeste", "afuera", "cerca", "lejos", "lado",
    "calle", "avenida", "av", "ruta", "km", "kilometro", "kilometros", "altura",
    # people and places customers refer to
    "casa", "trabajo", "laburo", "garage", "garaje", "cochera", "taller", "agencia",
    "concesionaria", "dueno", "duena", "vendedor", "vendedora", "cliente", "amigo",
    "amiga", "hermano", "hermana", "padre", "madre", "novia", "novio", "suegro",
    # time words
    "hoy", "manana", "tarde", "noche", "dia", "dias", "semana", "lunes", "martes",
    "miercoles", "jueves", "viernes", "sabado", "domingo", "hora", "horario",
})

_TOKEN_TRIM = ".,;:!?¡¿()[]\"'"


def _tokens(normalized_text: str) -> list[str]:
    return [t.strip(_TOKEN_TRIM) for t in normalized_text.split() if t.strip(_TOKEN_TRIM)]


def composite_name_conflict(
    normalized_text: str,
    matched_form: str,
    catalog_names: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> bool:
    """True when `matched_form` occurs ONLY as part of a longer foreign proper name.

    The failure this refuses was measured on the deployed resolver (finding W1FRV-03 and
    its San Miguel sibling):

        "en mar del plata"            -> ('Sur', 'La Plata')     — 400 km away, 180 000 viáticos
        "en san miguel del monte"     -> ('Oeste', 'San Miguel') — a different town

    Both are the same mistake: a catalogue name is a proper part of a longer name joined by
    "de"/"del"/"de los", and plain containment cannot see the difference. The rule is
    structural and names no city: a match is refused when, at EVERY place it occurs, it is
    joined by a name connector to a neighbouring token that is not a function word, and the
    longer phrase that results is NOT itself in the catalogue. One occurrence standing on
    its own is enough to accept — "el auto está en Flores, no en mar del plata" keeps Flores.

    `catalog_names` are normalised catalogue localities; passing them lets a legitimate
    connector name ("Lomas de Zamora", "Villa del Parque", "3 de Febrero") through by
    construction rather than by exception.
    """
    form = norm_locality_text(matched_form)
    if not form:
        return False
    catalog = {norm_locality_text(n) for n in catalog_names if n}
    tokens = _tokens(norm_locality_text(normalized_text))
    form_tokens = form.split()
    width = len(form_tokens)
    if not tokens or width == 0:
        return False

    occurrences = [i for i in range(len(tokens) - width + 1)
                   if tokens[i:i + width] == form_tokens]
    if not occurrences:
        # The form was matched by a rule that does not align to whole tokens (compact or
        # ASR-fuzzy matching). Nothing to extend, so nothing to refuse.
        return False

    for i in occurrences:
        end = i + width
        conflict = False
        # extend LEFT: <head> <connector> <form>
        for conn_len in (1, 2, 3):
            head_idx = i - conn_len - 1
            if head_idx < 0:
                continue
            connector = " ".join(tokens[i - conn_len:i])
            head = tokens[head_idx]
            if connector in _NAME_CONNECTORS and head not in _NAME_STOP_TOKENS:
                phrase = " ".join(tokens[head_idx:end])
                if phrase not in catalog:
                    conflict = True
        # extend RIGHT: <form> <connector> <tail>
        for conn_len in (1, 2, 3):
            tail_idx = end + conn_len
            if tail_idx >= len(tokens):
                continue
            connector = " ".join(tokens[end:end + conn_len])
            tail = tokens[tail_idx]
            if connector in _NAME_CONNECTORS and tail not in _NAME_STOP_TOKENS:
                phrase = " ".join(tokens[i:tail_idx + 1])
                if phrase not in catalog:
                    conflict = True
        if not conflict:
            return False
    return True


# ── validator 3: names shared across the coverage boundary ───────────────────
# A catalogue locality whose name is ALSO a well-known locality outside the coverage area.
# Choosing between them silently is a guess about which one the customer means, and the
# guess is not symmetric: the in-coverage reading produces a quote and a bookable slot.
#
# OWNER-EXTENSIBLE DATA, deliberately minimal. Each entry must name a real homonym and a
# real distance, because every entry costs a customer one extra question. Candidates
# considered and NOT added, for want of owner or geographic confirmation: Belgrano,
# San Martín, Mercedes, Olivos, Santa Rosa (already in the coverage list as a city).
CROSS_COVERAGE_AMBIGUOUS: dict[str, str] = {
    "san nicolas": ("CABA barrio San Nicolás vs San Nicolás de los Arroyos, "
                    "Buenos Aires province, ~240 km and outside the coverage area"),
}

# Text that settles which reading the customer means: the coverage area's own names.
_COVERAGE_QUALIFIERS: frozenset[str] = frozenset({
    "caba", "capital federal", "capital", "ciudad autonoma", "microcentro", "porteno",
    "ciudad de buenos aires", "gba", "amba", "zona norte", "zona sur", "zona oeste",
})


def is_cross_coverage_ambiguous(matched_form: str) -> bool:
    """True when this locality name is shared with a locality outside the coverage area."""
    return norm_locality_text(matched_form) in CROSS_COVERAGE_AMBIGUOUS


def ambiguity_reason(matched_form: str) -> str | None:
    return CROSS_COVERAGE_AMBIGUOUS.get(norm_locality_text(matched_form))


def disambiguated_by_text(normalized_text: str, zone_group: str | None = None,
                          *, area_hint: str | None = None) -> bool:
    """True when something in this turn settles which reading the customer means.

    Two settlers, and both are structural rather than lexical:

      * the text itself names the coverage area ("San Nicolás, CABA"), or
      * `area_hint` — an EXPLICIT area selection the customer made, which today means the
        Location Flow's own `zona_general` dropdown — names the same area the matched
        locality belongs to.

    An area selection that names a DIFFERENT area settles nothing. It contradicts the
    locality rather than qualifying it, and a contradiction is not an answer: the reading
    stays ambiguous and a person owns the outcome. This is why the hint is compared to the
    matched locality's own `zone_group` instead of merely being trusted.

    OWNER-REVIEW-R1 R1RV-01. Without the hint the guard in
    `_process_location_fallback_response` read only the `localidad` field, so a customer who
    answered the question we had just asked — area CABA, locality San Nicolás — was refused
    and handed to a person. The measured outcome was `needs_human=True`, no zone and no
    quote for a locality that is inside the coverage area and 0 viáticos.
    """
    if (area_hint and zone_group
            and norm_locality_text(area_hint) == norm_locality_text(zone_group)):
        return True
    text = norm_locality_text(normalized_text)
    if zone_group and norm_locality_text(zone_group) in text:
        return True
    return any(q in text for q in _COVERAGE_QUALIFIERS)


# ── the verdict a caller acts on ─────────────────────────────────────────────
class LocalityVerdict:
    """What a locality reading is allowed to become. Strings, so a trace never says "other"."""

    RESOLVED = "RESOLVED"        # a catalogue locality, safe to persist
    UNSUPPORTED = "UNSUPPORTED"  # declared outside the coverage area — never persist
    AMBIGUOUS = "AMBIGUOUS"      # a real name with more than one real place — ask, never persist
    UNRESOLVED = "UNRESOLVED"    # no catalogue locality, or only part of a longer foreign name

    ALL = (RESOLVED, UNSUPPORTED, AMBIGUOUS, UNRESOLVED)
    #: The verdicts that may never reach a candidate, a thread zone, PricingService or
    #: ScheduleService.
    REFUSED = (UNSUPPORTED, AMBIGUOUS, UNRESOLVED)
