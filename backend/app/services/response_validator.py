"""L4.7D — canonical response validator.

The AI composes natural language; it must not assert business facts that canonical state
does not support.  This module sits between response composition and OutboundSafetyGate:

    COMPOSE → CANONICAL RESPONSE VALIDATE → OutboundSafetyGate → SEND

Design rules (general, not phrase-specific):

* **A question asserts nothing unless it proposes a transaction** (R6-C1, R6IRV-P01).
  Clarifications ("¿Es un Peugeot 2008?", "¿En qué zona está el auto?") survive; a concrete
  price, time, window, vehicle or place PROPOSED in question form ("¿Te reservo el jueves
  a las 11:30?") is checked like a statement.
* **Every claim class names the canonical proof that licenses it.**  Absence of proof means
  the claim is unsupported, never that the proof is assumed.
* **Surgery, not amputation.**  Unsupported sentences are rewritten or dropped; valid FAQ
  answers and required next questions are preserved.  Only when nothing survives does a
  deterministic fallback replace the message.
* This module is pure: it reads a `CanonicalFacts` snapshot and returns text plus findings.
  It never touches the DB, the ORM or any service.

Claim classes and their canonical proof:

| Claim | Proof that licenses it | Unresolved means |
|---|---|---|
| VEHICLE | current-focus candidate with marca/modelo | no candidate, or a different vehicle |
| LOCATION | candidate zone, or cycle-scoped `state.home_zone_*` | no zone, or a zone that is only customer origin |
| PRICE | a PricingService quote for the active candidate + zone | no quote, or a different amount |
| AVAILABILITY | ScheduleService evaluation this turn, or slots it produced — every day/date/time of the reply's scheduling proposition must be fully parsed and be the evaluated date and its slots (R6-C2, R6-C3) | never evaluated; another day; unresolved temporal wording |
| BOOKING | booked ThreadRevision / current_revision_id (Flow completed) | Flow merely sent |
| ACCEPTANCE | lead.flag = ACEPTADO or stage at/after SCHEDULING | conversational tone alone |
"""
from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from types import MappingProxyType
from typing import Optional
from zoneinfo import ZoneInfo

from .verified_generation import is_verified as _vg_is_verified  # R6-C4B-R1 bridge provenance

# ── Claim class identifiers ───────────────────────────────────────────────────

CLAIM_VEHICLE = "VEHICLE"
CLAIM_LOCATION = "LOCATION"
CLAIM_PRICE = "PRICE"
CLAIM_AVAILABILITY = "AVAILABILITY"
CLAIM_BOOKING = "BOOKING"
CLAIM_ACCEPTANCE = "ACCEPTANCE"

ACTION_ALLOWED = "allowed"
ACTION_REMOVED = "removed"
ACTION_REWRITTEN = "rewritten"
#: R6. An uncertain text observation: recorded, never a claim, never a state change.
ACTION_DIAGNOSTIC = "diagnostic"

_KNOWN_CLAIM_CLASSES = frozenset({CLAIM_VEHICLE, CLAIM_LOCATION, CLAIM_PRICE,
                                  CLAIM_AVAILABILITY, CLAIM_BOOKING, CLAIM_ACCEPTANCE})

# ── Detection lexicons (claim *classes*, not customer phrasings) ──────────────

_PRICE_AMOUNT_RE = re.compile(r"\$\s*\d[\d.,]*|\b\d[\d.,]{3,}\s*pesos\b", re.IGNORECASE)

_AVAILABILITY_RE = re.compile(
    r"\b(?:disponibilidad|disponibles?|hay\s+lugar|hay\s+turno|"
    r"horarios?\s+(?:disponibles?|libres?)|te\s+(?:ofrezco|puedo\s+ofrecer)|"
    r"queda\s+libre|tengo\s+(?:libre|el\s+horario|turno))\b",
    re.IGNORECASE,
)
# A booking claim needs a booking noun AND a completed-state participle in the same
# sentence.  Infinitives ("para confirmar el turno") are invitations, not claims.
_BOOKING_PARTICIPLE_RE = re.compile(
    r"\b(?:confirmad|reservad|agendad)[oa]s?\b", re.IGNORECASE
)
_BOOKING_NOUN_RE = re.compile(
    r"\b(?:turno|revisi[oó]n|inspecci[oó]n|reserva|cita|visita)\b", re.IGNORECASE
)

_ACCEPTANCE_RE = re.compile(
    r"\b(?:presupuesto\s+aceptad\w*|cotizaci[oó]n\s+aceptad\w*|"
    r"ya\s+(?:lo\s+)?aceptaste|aceptaste\s+(?:el|la)\s+\w+|"
    r"avanzamos\s+con\s+(?:la\s+)?(?:reserva|contrataci[oó]n))\b",
    re.IGNORECASE,
)

# A sentence that describes where the CUSTOMER is, not where the vehicle is.
_ORIGIN_MARKER_RE = re.compile(
    r"\b(?:sos\s+de|eres\s+de|viv[ií]s?\s+en|est[aá]s\s+en|te\s+encontr[aá]s\s+en|"
    r"desde\s+donde\s+viv)\w*\b",
    re.IGNORECASE,
)

# Words that make a sentence a statement about the inspection subject.
_INSPECTION_SUBJECT_RE = re.compile(
    r"\b(?:auto|veh[ií]culo|camioneta|revisi[oó]n|inspecci[oó]n|turno|cotizaci[oó]n)\b",
    re.IGNORECASE,
)

# R6-C3: a month abbreviation's period ("19 nov. a las 10") does not end the sentence;
# R6-C4: nor does a weekday abbreviation's ("el jue. 19") or a street prefix's ("Av. 9 de Julio").
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])(?<!\bene\.)(?<!\bfeb\.)(?<!\bmar\.)(?<!\babr\.)(?<!\bmay\.)(?<!\bjun\.)"
    r"(?<!\bjul\.)(?<!\bago\.)(?<!\bsep\.)(?<!\bsept\.)(?<!\bset\.)(?<!\boct\.)(?<!\bnov\.)"
    r"(?<!\bdic\.)(?<!\blun\.)(?<!\bmie\.)(?<!\bmier\.)(?<!\bjue\.)(?<!\bvie\.)(?<!\bsab\.)"
    r"(?<!\bdom\.)(?<!\bmié\.)(?<!\bsáb\.)(?<!\bmiér\.)(?<!\bav\.)(?<!\bavda\.)(?<!\bnro\.)(?<!\bpje\.)(?<!\bbv\.)\s+|\n+",
    re.IGNORECASE)

# ── R6 (R5F1RV-01): claim SHAPES for the rendered-text defence in depth ──────
#
# A claim is a construction, not a token occurrence. These describe the grammatical frame
# that turns a word into an assertion about THIS customer's vehicle, place or appointment.
# They name no catalogue model, no zone and no customer phrasing.

# A locative frame: the place is the complement of a locative preposition, optionally
# through a place noun ("en Palermo", "hasta Pilar", "en la zona de Pilar", "a zona Norte").
_LOCATIVE_PREPOSITIONS = frozenset({"en", "hasta", "a", "para", "por"})
_PLACE_NOUNS = frozenset({"zona", "barrio", "localidad", "partido", "ciudad"})
_ARTICLES = frozenset({"el", "la", "los", "las"})
# A conditional or concessive clause is not an assertion about the customer's vehicle:
# "Si el auto está en zona Norte, también vamos."
_CONDITIONAL_TOKENS = frozenset({"si", "cuando", "aunque", "donde"})
# Going to, or being at, a place: "vamos hasta Pilar", "el auto está en Pilar".
_PRESENCE_OR_MOTION_TOKENS = frozenset({
    "vamos", "voy", "va", "van", "vas", "ir", "llegamos", "llego", "llega", "llegan",
    "acercamos", "pasamos", "esta", "estan", "estara", "queda", "quedan", "ubicado",
    "ubicada"})

# ── R6-C1: structural frames (R6IRV-S01 / S02 / P01) ─────────────────────────
#
# Each names a grammatical ROLE (a day anchor, a transactional action, a general-hours
# frame), never a supplied sentence, a catalogue model or a zone. All match on `_norm` text.

_WEEKDAY = r"(?:lunes|martes|miercoles|jueves|viernes|sabados?|domingos?)"
_MONTH = (r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
          r"octubre|noviembre|diciembre)")
# General service hours: a weekday SPAN, a habitual weekday ("los sábados"), every day, or
# the business's own hours/attention vocabulary.
_GENERAL_HOURS_RE = re.compile(
    rf"\b{_WEEKDAY}\s+(?:a|al|hasta|y)\s+{_WEEKDAY}\b|\blos\s+{_WEEKDAY}\b|"
    rf"\btodos\s+los\s+dias\b|\bhorarios?\s+de\s+(?:atencion|trabajo)\b|"
    rf"\b(?:nuestro|el)\s+horario\b|\b(?:atendemos|atencion|trabajamos|abrimos|abierto|"
    rf"abiertos|habiles)\b")
# A concrete DAY: one date, hoy / mañana / pasado mañana (not "la mañana", the morning),
# or one weekday that is not part of a span or a habitual plural.
_DATE_ANCHOR_RE = re.compile(
    rf"\b\d{{4}}-\d{{1,2}}-\d{{1,2}}\b|"
    rf"\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b|\b\d{{1,2}}\s+de\s+{_MONTH}\b|"
    rf"\bel\s+dia\s+\d{{1,2}}\b|\bhoy\b|\bpasado\s+manana\b|(?<!\bla\s)(?<!\blas\s)"
    rf"(?<!\bde\s)(?<!\bpor\s)\bmanana\b")
_SINGLE_WEEKDAY_RE = re.compile(rf"\b{_WEEKDAY}\b")
# One weekday made DEFINITE: "el jueves", "este viernes", "el próximo lunes".
_DEFINITE_WEEKDAY_RE = re.compile(rf"\b(?:el|este|esta|proximo|proxima)\s+(?:proximo\s+)?"
                                  rf"{_WEEKDAY}\b")
# A transactional action: inspecting, booking, coordinating, offering, visiting, arriving.
# Stems cover every inflection ("revisamos", "revisión", "reservo", "agendamos"…).
_TRANSACTIONAL_STEMS = ("revis", "inspecc", "chequ", "reserv", "agend", "coordin",
                        "peritaj", "ofrec", "ofrezc")
_TRANSACTIONAL_WORDS = frozenset({
    "turno", "visita", "cita", "esperamos", "espero", "vamos", "voy", "pasamos",
    "llegamos", "llego", "acercamos", "ir", "venimos", "vengo"})
# First-person going-to-a-place: the location proposition of a question ("¿Vamos hasta…?").
_FIRST_PERSON_MOTION = frozenset({"vamos", "voy", "ir", "llegamos", "llego", "pasamos",
                                  "acercamos", "venimos", "vengo"})


def is_transactional_token(tok: str) -> bool:
    """A normalised token that names an inspection/booking/visit action. Public: the
    outbound vehicle claim shape (`reply_claim_shape`) uses the same lexicon."""
    return tok in _TRANSACTIONAL_WORDS or tok.startswith(_TRANSACTIONAL_STEMS)


# The NOUNS of the lexicon name the subject of a sentence, not a proposal: "¿La revisión es
# para un Peugeot 2008?" asks; "¿Coordinamos la revisión de tu Fiat Uno?" proposes.
_TRANSACTIONAL_NOUNS = frozenset({
    "revision", "revisiones", "inspeccion", "inspecciones", "turno", "turnos", "visita",
    "visitas", "cita", "citas", "reserva", "reservas", "peritaje", "peritajes", "chequeo",
    "chequeos", "coordinacion", "agenda", "oferta", "ofrecimiento"})


def _has_transactional_action(sentence: str) -> bool:
    return any(is_transactional_token(t) for t in _tokens(sentence))


def _has_transactional_verb(sentence: str) -> bool:
    """A question PROPOSES a transaction only through an action verb (R6IRV-P01)."""
    return any(is_transactional_token(t) and t not in _TRANSACTIONAL_NOUNS
               for t in _tokens(sentence))


# ── R6-C2 (R6C1RV-S01): the stated day/date is bound to the evaluated date ─────
#
# ScheduleService evaluates ONE calendar date; its slots are "HH:MM" values that are only
# true for that date. A reply that proposes a concrete time or window and also names a day
# is therefore supported only when that day resolves, deterministically and in the BUSINESS
# timezone, to the canonical evaluated date — matching times on another day are a false
# availability claim.

#: = `schedule.BUSINESS_TIMEZONE` (asserted by test; not imported, to keep this module pure).
BUSINESS_TIMEZONE = "America/Argentina/Buenos_Aires"


def _utcnow() -> datetime:
    """The one clock this module reads (patched by the timezone-rollover tests)."""
    return datetime.now(timezone.utc)


def business_date_at(moment: datetime) -> date:
    """The business calendar date of an aware instant. Never UTC, never the DB session."""
    if moment.tzinfo is None:
        raise ValueError("business_date_at requires an aware datetime")
    return moment.astimezone(ZoneInfo(BUSINESS_TIMEZONE)).date()


def business_today() -> date:
    return business_date_at(_utcnow())


_ISO_DATE_STRICT_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_HHMM_STRICT_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def canonical_date(value) -> Optional[date]:
    """A strictly validated canonical date: an exact `datetime.date` (never a `datetime`),
    or an exact `str` in ISO `YYYY-MM-DD` form that is a real calendar date. Anything else
    — free text, a datetime, a malformed or impossible date — is None (no canonical date)."""
    if type(value) is date:
        return value
    if type(value) is str and _ISO_DATE_STRICT_RE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _full_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


# ── R6-C3 (R6C2RV-S01): ONE temporal analysis of the COMPLETE reply ────────────
#
# R6-C2 read a fixed list of day/date forms out of each sentence on its own; anything else
# — "el lunes 23", "el 19", "19-11", "19 nov", "a las 10", "el finde", a day in the previous
# sentence — silently became "no anchor", and the day-blind endpoint rule then accepted
# matching times from another date. R6-C3 replaces that with a class-level contract:
#
#   * every temporal-LOOKING expression of the reply is consumed by a recogniser that fully
#     normalises it (a calendar date, a weekday, a day of month, a relative day, an HH:MM
#     time, a window endpoint, a part of the day), or it is left over and recorded as an
#     UNRESOLVED fragment by a structural residual scan (digits in a temporal position,
#     weekday/month words, semana/finde/mes/feriado, am/pm/hs) — a partial parse can never
#     be "no anchor";
#   * the reply is ONE scheduling proposition: every day, time and fragment of every sentence
#     that is not general business-hours information counts, and all of them must resolve,
#     agree with each other and agree with canonical state;
#   * only FULLY_PARSED_AND_CANONICAL authorises a concrete date/time proposition; an
#     unsupported or unresolved one is refused (the sentences that carry it are removed, and
#     the plan-appropriate fallback replaces a reply with nothing left).
#
# Every recogniser reads `_norm` text (lowercase ASCII); nothing here names a supplied
# sentence.


class TemporalState(enum.Enum):
    """The one outcome of the temporal analysis of an AI-composed reply. Closed set."""
    NO_TEMPORAL_EXPRESSION = "NO_TEMPORAL_EXPRESSION"
    FULLY_PARSED_AND_CANONICAL = "FULLY_PARSED_AND_CANONICAL"
    FULLY_PARSED_BUT_UNSUPPORTED = "FULLY_PARSED_BUT_UNSUPPORTED"
    TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED = "TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED"


@dataclass(frozen=True)
class DayRef:
    """One stated day. `kind`: date (a full calendar date), monthday (day + month, no year),
    relative (hoy / mañana / pasado mañana), day_of_month ("el 19", "el día 19"), weekday
    (a bare weekday, resolved to its next occurrence in [today + 1, today + 6], or the day it
    is in apposition to: "hoy lunes"; R6-C4) or weekday_date
    (a weekday with an explicit date or day number — the date is authoritative)."""
    kind: str
    text: str
    sentence: int
    resolved: Optional[date] = None
    month: Optional[int] = None
    day: Optional[int] = None
    weekday: Optional[int] = None

    def is_date(self, canonical: date) -> bool:
        if self.kind == "monthday":
            return (self.month, self.day) == (canonical.month, canonical.day)
        return self.resolved is not None and self.resolved == canonical


@dataclass(frozen=True)
class TimeRef:
    """One stated clock time, normalised to 24-hour "HH:MM"."""
    value: str
    text: str
    sentence: int
    endpoint: bool = False


@dataclass(frozen=True)
class DaypartRef:
    """A part of the day ("por la tarde"): a bounded hour interval [lo, hi)."""
    name: str
    sentence: int
    lo: int
    hi: int

    def contains(self, hhmm: str) -> bool:
        h = int(hhmm[:2])
        return self.lo <= h < self.hi


@dataclass(frozen=True)
class SentenceTemporal:
    index: int
    text: str
    question: bool
    general_hours: bool
    negative: bool
    lexicon: bool
    conditional: bool
    transactional: bool
    scheduling: bool = False
    days: tuple = ()
    times: tuple = ()
    dayparts: tuple = ()
    unresolved: tuple = ()
    conflicts: tuple = ()
    ranges: tuple = ()
    #: habitual / period / leftover temporal-looking wording outside a general-hours frame
    vague: tuple = ()
    #: R6-C4: leftover fragments that are unresolved only in a sentence that carries temporal
    #: content or the availability lexicon (a small spelled count, an approximator, "mar")
    weak: tuple = ()
    #: R6-C4: the days/times/parts inside the scope of a negative availability core
    negated_days: tuple = ()
    negated_times: tuple = ()
    negated_dayparts: tuple = ()

    @property
    def has_temporal(self) -> bool:
        return bool(self.days or self.times or self.dayparts or self.unresolved
                    or self.conflicts or self.vague or self.negated_days
                    or self.negated_times or self.negated_dayparts)

    @property
    def temporal_weak(self) -> tuple:
        """The weak fragments that count: those of a sentence with temporal content."""
        return self.weak if (self.has_temporal or self.lexicon) else ()


@dataclass(frozen=True)
class TemporalAnalysis:
    """The temporal analysis of one complete reply (every sentence, one proposition)."""
    sentences: tuple
    today: date

    def _of(self, attr: str, sentences=None) -> tuple:
        rows = self.sentences if sentences is None else sentences
        return tuple(x for s in rows if not s.general_hours for x in getattr(s, attr))

    @property
    def days(self) -> tuple:
        return self._of("days")

    @property
    def dates(self) -> tuple:
        return tuple(d.resolved for d in self.days if d.resolved is not None)

    @property
    def weekdays(self) -> tuple:
        return tuple(d for d in self.days if d.weekday is not None)

    @property
    def days_of_month(self) -> tuple:
        return tuple(d for d in self.days if d.kind in ("day_of_month", "weekday_date")
                     and d.day is not None)

    @property
    def times(self) -> tuple:
        return self._of("times")

    @property
    def window_endpoints(self) -> tuple:
        return tuple(t for t in self.times if t.endpoint)

    @property
    def unresolved(self) -> tuple:
        return (self._of("unresolved") + self._of("vague") + self._of("conflicts")
                + self._of("temporal_weak"))

    @property
    def general_hours(self) -> bool:
        return any(s.general_hours for s in self.sentences)

    @property
    def transactional(self) -> bool:
        return any(s.scheduling or s.lexicon for s in self.sentences)

    @property
    def parse_state(self) -> TemporalState:
        """Parsing alone: NO_TEMPORAL_EXPRESSION, UNRESOLVED_OR_MALFORMED, or — before it is
        compared with canonical state — FULLY_PARSED_BUT_UNSUPPORTED (`temporal_verdict`
        decides whether a fully parsed proposition is canonical)."""
        if self.unresolved:
            return TemporalState.TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED
        if self.days or self.times or self._of("dayparts"):
            return TemporalState.FULLY_PARSED_BUT_UNSUPPORTED
        return TemporalState.NO_TEMPORAL_EXPRESSION


# Lexicons (normalised).
_WEEKDAY_NAMES = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
_WD_SING = r"(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)"
_WD_ANY = r"(?:lunes|martes|miercoles|jueves|viernes|sabados?|domingos?)"
#: R6-C4 (R6C3RV-S01). Weekday abbreviations ("lun 23", "el jue. 19"). They are read as a
#: weekday only in a day position (an article/qualifier in front, or a day number/date
#: after); anywhere else they are a leftover weekday FRAGMENT (default-deny residual).
_WD_ABBR = {"lun": 0, "mar": 1, "mie": 2, "mier": 2, "jue": 3, "vie": 4, "sab": 5, "dom": 6}
_WD_ABBR_ALT = "mier|mie|lun|mar|jue|vie|sab|dom"
_MONTH_FULL = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
               "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
               "noviembre": 11, "diciembre": 12}
_MONTH_ABBR = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7,
               "ago": 8, "sept": 9, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
_MONTH_WORDS = {**_MONTH_FULL, **_MONTH_ABBR}
_MON_ALT = "|".join(sorted(_MONTH_WORDS, key=len, reverse=True))
_MON = rf"(?:{_MON_ALT})"
#: Leftover fragments that are unambiguous (no other Spanish word) → always unresolved;
#: the ambiguous ones (mar = sea, dom, may, set, sep, ago) → unresolved inside a sentence
#: that carries temporal content.
_STRONG_FRAGMENT_WORDS = frozenset({"lun", "mie", "mier", "jue", "vie", "sab", "ene", "feb",
                                    "abr", "jun", "jul", "oct", "nov", "dic", "sept"})
_WEAK_FRAGMENT_WORDS = frozenset({"mar", "dom", "may", "set", "sep", "ago", "proximo",
                                  "proxima", "siguiente", "entrante", "temprano"})

# ── R6-C4 (R6C3RV-S02): a compositional Spanish cardinal parser ───────────────
#
# Days (1–31), hours (0–24) and minutes (1–59) are read from their parts — units, the
# teens, "dieci-" / "veinti-" compounds and "treinta/cuarenta/cincuenta y N" — never from a
# list of example phrases. A value outside the slot it fills is unresolved.
_CARD_UNIT_VALUES = MappingProxyType({"dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
                                      "seis": 6, "siete": 7, "ocho": 8, "nueve": 9})
_CARD_SIMPLE_VALUES = MappingProxyType({
    "cero": 0, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50})
_CARD_ONE_RE = re.compile(r"un[oa]?")                     # 1 (un / una / the masculine)
_CARD_U = r"(?:un[oa]?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)"
_CARD = (rf"(?:cero|diez|once|doce|trece|catorce|quince|dieci(?:seis|siete|ocho|nueve)|"
         rf"veinte|veinti{_CARD_U}|(?:treinta|cuarenta|cincuenta)(?:\s+y\s+{_CARD_U})?|{_CARD_U})")
#: A cardinal that can be a clock MINUTE or an hour count: everything but un/una/the
#: masculine one ("y un técnico" is not 10:01).
_CARD_NO_ONE = (rf"(?:cero|diez|once|doce|trece|catorce|quince|dieci(?:seis|siete|ocho|nueve)|"
                rf"veinte|veinti{_CARD_U}|(?:treinta|cuarenta|cincuenta)(?:\s+y\s+{_CARD_U})?|"
                rf"dos|tres|cuatro|cinco|seis|siete|ocho|nueve)")
_CARD_TOKEN_RE = re.compile(rf"{_CARD}")


def _card_value(words: str) -> Optional[int]:
    """The value of one Spanish cardinal (0–59), or None. Compositional, total."""
    w = " ".join((words or "").split())

    def unit(x: str) -> Optional[int]:
        return 1 if _CARD_ONE_RE.fullmatch(x) else _CARD_UNIT_VALUES.get(x)
    if w in _CARD_SIMPLE_VALUES:
        return _CARD_SIMPLE_VALUES[w]
    u = unit(w)
    if u is not None:
        return u
    if w.startswith("dieci"):
        u = unit(w[5:])
        return 10 + u if u is not None and u >= 6 else None
    if w.startswith("veinti"):
        u = unit(w[6:])
        return 20 + u if u is not None else None
    m = re.fullmatch(r"(treinta|cuarenta|cincuenta)\s+y\s+(\S+)", w)
    if m:
        u = unit(m.group(2))
        return _CARD_SIMPLE_VALUES[m.group(1)] + u if u is not None else None
    return None


def _number_of(word: str) -> Optional[int]:
    word = " ".join((word or "").split())
    return int(word) if word.isdigit() else _card_value(word)


_MERIDIEM = r"(?:a\.?\s?m\.?|p\.?\s?m\.?)(?![a-z])"
_HS = r"(?:hs|hrs|h)\.?(?![a-z0-9])"
_DAYPART_HOURS = {"manana": (5, 13), "tarde": (12, 21), "noche": (19, 24)}
#: "el día N" / "el N" resolve to the nearest such day within this horizon (business days).
_DAY_OF_MONTH_HORIZON_DAYS = 62
#: A bare weekday resolves to its next occurrence in [today + 1, today + 6]; a weekday that
#: IS today's is ambiguous (today, or the same day next week — CE's own reading) unless it
#: is in apposition to "hoy" / "mañana" (R6-C4, R6C3RV-S01 H1).
_WEEKDAY_HORIZON_DAYS = 7

# Placeholders the scanner leaves where it consumed a span. Private-use characters: never
# a digit, never a word character, never matched by any recogniser. Each placeholder of a
# day, a time or a part of the day carries an index character (R6-C4), so the negation
# scope can tell WHICH day/time a negative statement covers.
_P_DATE = "\ue000"          # a day (date, weekday, relative, day of month)   + U+E100+k
_P_TIME = "\ue001"          # a clock time (one, or a window's two)            + U+E200+k
_P_INERT = "\ue002"         # consumed and non-temporal (price, duration, courtesy, …)
_P_PART = "\ue003"          # a part of the day                                + U+E300+k
_PLACEHOLDERS = _P_DATE + _P_TIME + _P_INERT + _P_PART
_PT = _P_TIME + "[\ue200-\ue2ff]?"

_COURTESY_RE = re.compile(
    r"\b(?:buen(?:a|os|as)?|feliz|lindo|linda|excelente)\s+(?:inicio\s+de\s+|comienzo\s+de\s+)?"
    r"(?:fin\s+de\s+semana|finde|semana|feriado|dias?|tardes?|noches?|mes)\b")
_PRICE_NORM_RE = re.compile(r"\$\s*\d[\d.,]*|\b\d[\d.,]{3,}\s*pesos\b")
_DURATION_RE = re.compile(
    r"\b(?:dura|duran|demora|demoran|lleva|llevan|tarda|tardan|toma|toman|aprox(?:\.|imadamente)?|"
    r"unas?|alrededor\s+de|cerca\s+de|mas\s+o\s+menos|menos\s+de|mas\s+de|hasta)\s+(?:de\s+)?"
    r"(?:entre\s+)?\d{1,2}(?:\s*(?:y|a|-|o)\s*\d{1,2})?\s*(?:horas?|hs|hrs)\b\.?|"
    r"\bentre\s+\d{1,2}\s*(?:y|a|-)\s*\d{1,2}\s+horas?\b|\b\d{1,2}\s+horas?\s+de\s+duracion\b|"
    r"\b(?:una|media|dos|tres)\s+horas?\b")
# ── R6-C4: explicitly inert non-temporal number content (default-deny residual) ──
#: A relative offset ("en 12 días", "dentro de 5 días", "en 3 días hábiles", "en 2 horas")
#: names no calendar date or clock time: it is UNRESOLVED, never inert (R6C3RV-S01 W4).
_RELATIVE_OFFSET_RE = re.compile(
    rf"\b(?:en|dentro\s+de|de\s+aca\s+a|de\s+aqui\s+a|a\s+mas\s+tardar\s+en)\s+"
    rf"(?:(?:los|las|unos|unas|el|la|solo|apenas|mas\s+de|menos\s+de)\s+)?"
    rf"(?:\d{{1,3}}|{_CARD}|(?:un\s+)?par\s+de|pocos|pocas|algunos|algunas|unos|unas)\s+"
    rf"(?:dias?|semanas?|meses?|horas?|hs|hrs|minutos?|mins?|ratito|rato)\b"
    rf"(?:\s+(?:habiles|corridos|mas))?|"
    rf"\b(?:\d{{1,3}}|{_CARD})\s+dias?\s+(?:habiles|corridos)\b")
#: Street addresses, including the date-named streets ("Av. 9 de Julio 1500").
_ADDRESS_RE = re.compile(
    rf"\b(?:calle|av|avda|avenida|pasaje|pje|diagonal|diag|ruta|bv|boulevard|bulevar|camino|"
    rf"autopista|plaza|esquina)\.?\s+(?:(?:\d{{1,2}}|{_CARD})\s+de\s+{_MON}\b\.?|\d{{1,5}}\b)"
    rf"(?:\s+(?:n(?:ro|o|um|umero)?\.?\s*|al\s+)?\d{{1,5}}\b)?")
#: Phone numbers: a 3–4 digit group then a 4 digit group, or ≥ 8 contiguous digits.
_PHONE_RE = re.compile(
    r"(?<![\w/.:\-])(?:\+\s?\d{1,3}[\s\-]?)?(?:\(?\d{1,4}\)?[\s\-]?)?\d{3,4}[\s\-]\d{4}"
    r"(?![\w/.:\-])|(?<![\w/.:\-])\+?\d{8,}(?![\w/.:\-])")
#: Thousands ("80.000"), engine sizes ("1.6 nafta", "motor 2.0"), "4x4".
_THOUSANDS_RE = re.compile(r"(?<![\d:/.\-])\d{1,3}(?:\.\d{3})+(?:,\d+)?(?![\d:/\-]|\.\d)")
_ENGINE_RE = re.compile(
    r"\b\d+x\d+\b|\b\d\.\d\s*(?:l|lts?|litros?|cc|tdi|tsi|hdi|turbo|nafta|diesel|16v|8v)\b|"
    r"\bmotor\s+\d\.\d\b")
#: A count of things: "3 dueños", "150 puntos", "2 técnicos", "tengo 3 horarios",
#: "doce cuotas". (Not minutes after "y"/"menos": "a las 10 y 20 minutos" is a clock.)
_COUNT_NOUNS = (
    r"(?:por\s+ciento|minutos?|mins?|km|kms|kilometros?|cuadras?|metros?|mts?|anos?|cuotas?|"
    r"veces|autos?|vehiculos?|puntos?|duenos?|tecnicos?|mecanicos?|personas?|fotos?|"
    r"fotografias?|puertas?|plazas?|ruedas?|cilindros?|valvulas?|kilos?|kg|litros?|lts?|cc|cv|"
    r"hp|items?|pasos?|etapas?|partes?|informes?|revisiones?|inspecciones?|unidades?|modelos?|"
    r"versiones?|opciones?|alternativas?|horarios?|turnos?|lugares?|cupos?|mil|millones?|"
    r"pesos?|dolares?|usd|marchas?|asientos?|airbags?|velocidades?|neumaticos?|cubiertas?|"
    r"servicios?|preguntas?|consultas?|mensajes?|archivos?|documentos?|controles?|chequeos?|"
    r"cosas|aspectos|sistemas?|zonas?|barrios?|localidades|sucursales?|peritos?|clientes?)")
_COUNT_RE = re.compile(
    rf"(?<![\d:/.\-])(?:\d{{1,4}}(?:[.,]\d+)?|{_CARD})\s*(?:mil\s+)?{_COUNT_NOUNS}\b|"
    rf"(?<![\d:/.\-])\d{{1,3}}\s*%")
#: An enumeration label ("Opción 1:", "paso 2") and a list marker at the start ("1) …").
_ORDINAL_LABEL_RE = re.compile(
    r"\b(?:opcion|alternativa|paso|item|grupo|nivel|categoria)\s+(?:n(?:ro|o)?\.?\s*)?\d{1,2}\b")
_LIST_MARK_RE = re.compile(r"^\s*[1-9][).]\s+")
#: A pronoun count after the clock readers ran: "los dos", "cuál de las dos", "ambos".
_PRONOUN_COUNT_RE = re.compile(
    rf"\b(?:(?:cual(?:quiera)?|alguna?|ninguna?|ambas|ambos|cada\s+una?)\s+de\s+)?"
    rf"(?:los|las|estas|estos|esas|esos)\s+{_CARD_NO_ONE}\b"
    rf"(?!\s*(?:y\s|menos\s|en\s+punto|de\s+la|por\s+la|hs\b|hrs\b|horas\b|h\b|:|" + _P_TIME + r"))")
#: Counting aloud — three or more spelled cardinals in one coordinated run ("uno, dos y
#: tres"). Read AFTER the clock readers, so "a las diez, once y doce" is never one.
_ENUMERATION_RE = re.compile(
    rf"\b{_CARD}(?:\s*,\s*{_CARD}){{1,}}\s*,?\s*(?:y|e|o)\s+{_CARD}\b|"
    rf"\b{_CARD}(?:\s*,\s*{_CARD}){{2,}}\b")
#: A designator never follows a day word or an interval/temporal preposition ("el lunes
#: Navidad 25", "para Navidad 25"): there the number stays temporal.
_DESIGNATOR_BLOCKERS = frozenset({
    *_WEEKDAY_NAMES, "sabados", "domingos", *_WD_ABBR, *_MONTH_WORDS, "hoy", "manana", "dia",
    "dias", "fecha", "hora", "horas", "horario", "turno", "semana", "finde", "feriado", "mes",
    "proximo", "proxima", "siguiente", "otro", "otra", "tarde", "noche", "mediodia", "del",
    "al", "a", "para", "desde", "hasta", "entre", "las", "la", "antes", "despues", "sobre",
    "tipo", "cerca", "pasadas"})
#: Words that never make "<Capitalised word> <number>" a model / address designator.
_DESIGNATOR_EXCLUDED = frozenset({
    *_WEEKDAY_NAMES, "sabados", "domingos", *_WD_ABBR, *_MONTH_WORDS, "hoy", "manana", "dia",
    "dias", "fecha", "fechas", "hora", "horas", "horario", "horarios", "turno", "turnos",
    "semana", "semanas", "finde", "feriado", "mes", "meses", "proximo", "proxima", "siguiente",
    "otro", "otra", "que", "viene", "tarde", "noche", "mediodia", "el", "la", "los", "las",
    "del", "al", "de", "a", "para", "desde", "hasta", "entre", "en", "por", "y", "o", "te",
    "tu", "nos", "me", "lo", "le", "su", "sus", "mi", "mis", "este", "esta", "ese", "esa",
    "sobre", "tipo", "cerca", "antes", "despues", "pasadas", "las", "nro", "no", "numero",
    "num", "cita", "visita", "agenda", "reserva", "inspeccion", "revision", "entrega"})

_ISO_RE = re.compile(
    r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?:[t\s](\d{1,2}):(\d{2})(?::\d{2})?(?!\d)"
    r"(?:\s*(?:hs|hrs|h)\b\.?)?)?(?!\d)")
#: R6-C4 (R6C3RV-S01 W3): year-first "2026/11/23", "2026.11.23".
_YEAR_FIRST_RE = re.compile(r"(?<![\d/.\-])(\d{4})([/.])(\d{1,2})\2(\d{1,2})(?!\d|[/.\-]\d)")
_NUMERIC_DATE_RE = re.compile(
    r"(?<![\d])(?<!\d[/.\-:])(\d{1,2})([/.\-])(\d{1,2})(?:\2(\d+))?(?!\d|[/\-:.]\d)")
_MONTH_FWD_RE = re.compile(
    rf"(?<![\d:/.\-])(\d{{1,2}})(?:ro|o)?(?:\s+(?:de\s+)?|\s*[-/.]\s*)({_MON})\b\.?"
    rf"(?:\s*,?\s*(?:(?:de|del)\s+)?(\d{{4}})(?!\d))?")
_MONTH_REV_RE = re.compile(
    rf"\b({_MON})\b\.?\s+(\d{{1,2}})(?:ro|o)?(?!\d|[:/.\-]\d)(?:\s*,?\s*(?:(?:de|del)\s+)?(\d{{4}})(?!\d))?")
_TIME_RANGE_RE = re.compile(
    rf"\b(?:de|desde|entre)\s+(?:las?\s+)?(\d{{1,2}})(?!\d)(?:(?::|h|\.)(\d{{2}}))?\s*(?:{_HS}\s*)?"
    rf"(?:a|y|hasta|al|-)\s*(?:las?\s+)?(\d{{1,2}})(?!\d)(?:(?::|h|\.)(\d{{2}}))?(?:\s*{_HS})?"
    rf"(?!\s*(?:[/.\-:]\d|dias?\b|semanas?\b|mes|anos?\b|horas?\b|personas?\b|de\s+{_MON}\b|{_MON}\b))")
_HHMM_RANGE_RE = re.compile(
    rf"(?<![\d:/.\-])(\d{{1,2}})(?::|h)(\d{{2}})\s*(?:{_HS}\s*)?(?:a|hasta|-)\s*"
    rf"(\d{{1,2}})(?::|h)(\d{{2}})(?!\d)(?:\s*{_HS})?")
_HHMM_RE = re.compile(
    rf"(?<![\d:/.\-])(\d{{1,2}})(?::|h)(\d{{2}})(?!\d)(?:\s*({_MERIDIEM}))?(?:\s*(?:{_HS}|horas\b))?")
#: "9.30 hs" (the business-hours rendering `schedule.format_business_hours_es` uses).
_DOT_HS_RE = re.compile(r"(?<![\d:/.\-])(\d{1,2})\.(\d{2})\s*" + _HS)
_LAS_DOT_RE = re.compile(r"\b(?:a\s+)?las?\s+(\d{1,2})\.(\d{2})(?!\d)(?:\s*" + _HS + r")?")
_MERIDIEM_RE = re.compile(rf"(?<![\d:/.\-])(\d{{1,2}})\s*({_MERIDIEM})")
_HOUR_HS_RE = re.compile(rf"(?<![\d:/.\-])(\d{{1,2}})\s*{_HS}")
#: Minute tails. Unanchored readings keep R6-C3's closed set; an hour introduced by
#: "a/para/desde/hasta las" (or an approximator) also takes any minute: "y veinte", "menos
#: diez", "y 20" (R6-C4, R6C3RV-S02 T2).
_QUARTER = r"(?:y\s+media|y\s+cuarto|menos\s+cuarto|en\s+punto|y\s+(?:15|30|45)(?![\d:]))"
_MIN_TAIL = (rf"(?:y\s+media|y\s+cuarto|menos\s+cuarto|en\s+punto|"
             rf"(?:y|menos)\s+(?:{_CARD_NO_ONE}|\d{{1,2}})(?![\d:a-z]))")
_PART_SUFFIX = r"(?:\s+(?:de|por)\s+la\s+(?P<part>manana|tarde|noche))?"
#: R6-C4: any preposition introduces a clock ("para las doce", "desde las nueve", "hasta
#: las once"); approximators introduce one too, and then make it unresolved.
_CLOCK_ANCHOR = (r"(?P<anchor>a|para|desde|hasta|sobre|tipo|eso\s+de|cerca\s+de|"
                 r"alrededor\s+de|antes\s+de|despues\s+de|pasadas|hacia)")
_LAS_RE = re.compile(
    rf"\b(?:{_CLOCK_ANCHOR}\s+)?(?P<clock>(?:las\s+(?P<hw>{_CARD_NO_ONE}|\d{{1,2}})|la\s+(?P<one>una|1))"
    rf"(?!\d|[:/.\-]\d)(?:\s+(?P<q>{_MIN_TAIL}))?{_PART_SUFFIX}(?:\s*(?P<mer>{_MERIDIEM}))?"
    rf"(?P<unit>\s*(?:horas|{_HS}))?)")
_QUARTER_RE = re.compile(
    rf"(?<![\d:/.\-])\b(?P<hw>{_CARD_NO_ONE}|\d{{1,2}})\s+(?P<q>{_QUARTER}){_PART_SUFFIX}")
_SPOKEN_PART_RE = re.compile(
    rf"(?<![\d:/.\-])\b(?P<hw>{_CARD_NO_ONE}|una|\d{{1,2}})\s+(?:de|por)\s+la\s+(?P<part>manana|tarde|noche)\b")
#: A window with spelled endpoints: "de las nueve a las doce", "entre las diez y las once",
#: "de nueve a doce hs".
_SP_RANGE_RE = re.compile(
    rf"\b(?:de|desde|entre)\s+las\s+(?P<h1>{_CARD_NO_ONE}|una|\d{{1,2}})(?:\s+(?P<q1>{_MIN_TAIL}))?"
    rf"\s+(?:a|y|hasta)\s+las\s+(?P<h2>{_CARD_NO_ONE}|una|\d{{1,2}})(?:\s+(?P<q2>{_MIN_TAIL}))?"
    rf"{_PART_SUFFIX}(?:\s*(?:horas|{_HS}))?|"
    rf"\b(?:de|desde|entre)\s+(?P<h3>{_CARD_NO_ONE}|una)\s+(?:a|y|hasta)\s+(?P<h4>{_CARD_NO_ONE})"
    rf"\s*(?:hs|hrs|horas)\b")
_NOON_RE = re.compile(r"\b(?:al\s+|a\s+|el\s+)?(mediodia|medianoche)\b")
_DAYPART_RE = re.compile(
    r"\b(?:(esta|esa)\s+(manana|tarde|noche)|(?:(?:por|a|en|durante)\s+)?la\s+(manana|tarde|noche)|"
    r"de\s+(manana|tarde|noche))\b")
_HABITUAL_PART_RE = re.compile(r"\b(?:por\s+|a\s+)?las\s+(?:mananas|tardes|noches)\b")
_PASADO_RE = re.compile(r"\bpasado\s+manana\b")
_PAST_DAY_RE = re.compile(r"\b(?:anteayer|antier|antes\s+de\s+ayer|ayer|anoche|antenoche)\b")
_HOY_RE2 = re.compile(r"\bhoy\b")
_MANANA_RE2 = re.compile(r"\bmanana\b")
_BARE_DOM_RE = re.compile(r"\b(?:(?:el\s+dia|dia|fecha)\s*:?\s*|(?:el|del|al|para\s+el)\s+)"
                          r"(\d{1,3})(?:ro|o)?(?!\d|[/.\-:]\d)")
_WD_SPAN_RE = re.compile(rf"\b(?:de\s+)?{_WD_ANY}\s+(?:a|al|hasta|y|e|o)\s+{_WD_ANY}\b")
_WD_HABITUAL_RE = re.compile(rf"\b(?:todos\s+)?los\s+{_WD_ANY}\b|\b(?:sabados|domingos)\b")
_WD_RE = re.compile(
    rf"\b(?:(?:el|este|esta|proximo|proxima|el\s+proximo|este\s+proximo|la\s+proxima)\s+)?"
    rf"({_WD_SING})\b")
#: R6-C4: one weekday, with its article, a qualifier ("otro", "próximo"; "siguiente", "que
#: viene"), and an attached day number or date — "lunes 23", "lunes (23)", "lunes [23]",
#: "lunes nº 23", "lun 23", "lunes 16/11".
_WD_FULL_RE = re.compile(
    r"\b(?:(?P<art>para\s+el|el|este|esta|del|al|la)\s+)?(?:(?P<qual>otro|otra|proximo|proxima|ultimo|"
    r"ultima|primer|primero|primera|segundo|segunda|tercer|tercero|tercera|cuarto|cuarta)\s+)?"
    rf"(?P<wd>{_WD_SING}|(?:{_WD_ABBR_ALT})\b\.?)(?![a-z])"
    r"(?:[\s,]*(?:[(\[]\s*)?(?:(?:n|no|nro|num|numero)\.?\s*|#\s*)?(?:el\s+)?(?:dia\s+)?"
    r"(?:(?P<dph>" + _P_DATE + r"[\ue100-\ue1ff])|(?P<num>\d{1,3})(?:ro|o)?(?!\d|[/.\-:]\d))"
    r"(?:\s*[)\]])?)?"
    r"(?:\s+(?P<tail>siguiente|que\s+viene|proximo|proxima|entrante|pasado|pasada|anterior|"
    r"de\s+la\s+(?:semana|otra\s+semana|proxima\s+semana)))?")
_PERIOD_RE = re.compile(
    r"\b(?:fin(?:es)?\s+de\s+semana|findes?|semanas?|quincenas?|mes(?:es)?|feriados?|"
    r"a\s+partir(?:\s+de)?|proximos\s+dias|estos\s+dias|dias\s+habiles|principios|mediados|"
    r"fin\s+de\s+mes|madrugada|y\s+pico|(?:a\s+)?(?:primera|ultima)\s+hora|"
    r"(?:la\s+)?otra\s+semana)\b")
# An approximate clock reading ("a eso de las 10", "tipo 10 hs", "más o menos a las 10", "sobre
# las 10", "después de las 10", "a las 10, más o menos", "10 aprox") is not one normalised
# time: the approximator before OR after a time makes it unresolved (R6-C4, S02).
_APPROX_TIME_RE = re.compile(
    r"\b(?:a\s+eso\s+de|eso\s+de|mas\s+o\s+menos|alrededor\s+de|cerca\s+de|tipo|"
    r"aprox(?:imadamente)?|sobre|antes\s+de|despues\s+de|pasadas|hacia|maso)"
    r"\s+(?:a\s+)?(?:las?\s+)?" + _PT + "|" + _PT +
    r"\s*,?\s*(?:mas\s+o\s+menos|aprox(?:\.|imadamente)?|o\s+algo\s+asi|o\s+antes|o\s+despues|"
    r"o\s+mas\s+tarde|en\s+adelante|para\s+adelante|como\s+mucho|como\s+maximo|como\s+minimo|"
    r"maso|y\s+pico|o\s+por\s+ahi|mas\s+tarde|mas\s+temprano)\b")
#: Approximators anywhere in a temporal sentence (weak residual).
_APPROX_WORD_RE = re.compile(r"\b(?:mas\s+o\s+menos|aprox(?:imadamente)?|o\s+algo\s+asi|maso|"
                             r"mas\s+tarde|mas\s+temprano)\b")
_RESIDUAL_TOKEN_RE = re.compile(
    r"[\ue000-\ue003][\ue100-\ue3ff]?|[a-z]+|\d+(?:[:/.\-]\d+)*|[^\s\w]")
_NUMBER_INTRODUCERS = frozenset({"el", "del", "al", "dia", "las", "tipo"})
_SPELLED_INTRODUCERS = frozenset({"el", "del", "al", "dia"})
_NUMBER_CONNECTORS = frozenset({"a", "y", "o", "hasta", "desde", "entre", "e", "u"})
_TIME_WORDS = frozenset({"hs", "hrs", "am", "pm"})
#: A line made only of these words and numbers ("Turno:\n19\n10:00", "Día: 19") states a
#: day or time by layout alone: its numbers are temporal.
_LABEL_WORDS = frozenset({"dia", "fecha", "hora", "horario", "turno", "el", "para", "y", "o",
                          "a", "las", "la", "mejor", "sino", "tambien", "entonces"})
#: Scheduling nouns: with temporal content they make the reply a scheduling proposition.
_SCHEDULING_CUES = frozenset({"horario", "horarios", "fecha", "fechas", "turno", "turnos",
                              "cita", "citas", "visita", "visitas", "agenda"})
_SECOND_PERSON = frozenset({"te", "tu", "tus", "vos", "contigo", "usted", "ustedes", "su", "sus"})
#: R6-C4: a layout label ("Día: …", "• Hora: …") states a schedule by layout alone.
_LABEL_COLON_RE = re.compile(r"\b(?:dia|hora|fecha|turno|horario|cuando)\s*:")

# General / habitual business-hours frames. Unlike R6-C1's `_GENERAL_HOURS_RE`, a bare
# "el horario" is NOT habitual ("El horario sería a las 10" proposes a time).
_HABITUAL_FRAME_RE = re.compile(
    rf"\b{_WD_ANY}\s+(?:a|al|hasta|y)\s+{_WD_ANY}\b|\blos\s+{_WD_ANY}\b|"
    rf"\btodos\s+los\s+dias\b|\bhorarios?\s+de\s+(?:atencion|trabajo)\b|"
    rf"\bnuestro\s+horario\b|\b(?:atendemos|atencion|trabajamos|abrimos|abierto|"
    rf"abiertos|habiles|respondemos)\b")
# A NEGATIVE availability core: a negated existence verb over an availability noun. It is a
# RECOGNISER (opt-in): a negative it does not recognise is simply validated.
_NEGATION_RE = re.compile(
    r"\b(?:no|tampoco|ya\s+no)\s+(?:(?:me|nos|te|le)\s+)?(?:tenemos|tengo|hay|habria|queda|"
    r"quedan|contamos\s+con|cuento\s+con)\b(?:\s+[^\s]+){0,4}?\s+(?:disponibilidad|disponibles?|"
    r"lugar(?:es)?|turnos?|horarios?|cupos?|operaciones|libres?|nada)\b|"
    r"\bno\s+(?:esta|estan|estamos)\s+disponibles?\b|\bningun\s+horario(?:\s+libre)?\b|"
    r"\bsin\s+(?:disponibilidad|turnos?|horarios?(?:\s+libres?)?|lugar(?:es)?|cupos?)\b")
#: R6-C4 (R6C3RV-S01 (a)). The SCOPE of a negative core: the day/time placeholders bound to
#: it only through these function words. Anything else — a comma, "sí", "excepto",
#: "salvo", "solo", "pero", "en cambio", a verb — ends the scope, so a positive offer outside
#: it is never sheltered. This is a closed list of GLUE words, not of positive markers: a
#: word it lacks only narrows the scope (fail-closed).
_NEG_SCOPE_GLUE = frozenset({
    "para", "el", "la", "los", "las", "de", "del", "a", "al", "en", "por", "ese", "esa", "este",
    "esta", "dia", "ni", "disponible", "disponibles", "libre", "libres", "horario", "horarios",
    "turno", "turnos", "lugar", "lugares", "cupo", "cupos", "ya", "todavia", "aun", "mas",
    "nada", "ningun", "ninguno", "ninguna", "tampoco", "lamentablemente", "momento", "ahora"})


def _hhmm(h: int, m: int) -> Optional[str]:
    return f"{h:02d}:{m:02d}" if 0 <= h <= 23 and 0 <= m <= 59 else None


def _hour_of(word: str) -> Optional[int]:
    v = _number_of(word)
    return v if v is not None and 0 <= v <= 24 else None


def _clock(h: int, m: int, meridiem: Optional[str], part: Optional[str],
           hour_only: bool) -> Optional[str]:
    """Normalise one clock reading to 24-hour HH:MM, or None when it is not a real time."""
    if meridiem:
        pm = meridiem.replace(".", "").replace(" ", "").startswith("p")
        if not 1 <= h <= 12:
            return None
        h = (0 if h == 12 else h) + (12 if pm else 0)
        if pm and h == 24:
            return None
        return _hhmm(h, m)
    if part:
        if part == "manana":
            return _hhmm(h, m) if 1 <= h <= 11 else None
        if part == "tarde":
            return _hhmm(12 if h == 12 else h + 12, m) if 1 <= h <= 12 else None
        return _hhmm(h + 12, m) if 6 <= h <= 11 else None        # noche
    if hour_only and 1 <= h <= 7:
        return _hhmm(h + 12, m)          # "a las 3" in business hours is 15:00
    return _hhmm(h, m)


def _minute_offset(quarter: Optional[str]) -> Optional[int]:
    """The minutes a spoken tail adds to its hour: "y media" +30, "menos cuarto" −15, "y
    veinte" +20, "menos diez" −10, "en punto" 0. None when the tail is not a real minute."""
    q = " ".join((quarter or "").split())
    if not q or q == "en punto":
        return 0
    if q == "y media":
        return 30
    if q == "y cuarto":
        return 15
    if q == "menos cuarto":
        return -15
    op, _, word = q.partition(" ")
    n = _number_of(word)
    if n is None:
        return None
    if op == "y":
        return n if 1 <= n <= 59 else None
    return -n if 1 <= n <= 30 else None


def _spoken_clock(h: Optional[int], quarter: Optional[str], part: Optional[str],
                  hour_only: bool = True) -> Optional[str]:
    """One spoken clock: the stated hour is placed on the 24-hour clock FIRST (part of the
    day, or the business-hours reading of 1–7), then its minute tail applies — "8 menos
    cuarto" is 07:45, "2 menos cuarto" is 13:45. None when anything is not a real time."""
    if h is None:
        return None
    base = _clock(h, 0, None, part, hour_only=hour_only)
    off = _minute_offset(quarter)
    if base is None or off is None:
        return None
    total = int(base[:2]) * 60 + off
    return _hhmm(total // 60, total % 60) if 0 <= total < 24 * 60 else None


def _quarter_minutes(h: int, quarter: Optional[str]) -> tuple[int, int]:
    """R6-C3 compatibility view (hour, minute) of a spoken tail on a literal hour."""
    off = _minute_offset(quarter)
    if off is None:
        return -1, 0
    total = h * 60 + off
    return (total // 60, total % 60) if total >= 0 else (-1, 0)


def _nearest_with_day(today: date, day: int) -> Optional[date]:
    return next((today + timedelta(days=k) for k in range(_DAY_OF_MONTH_HORIZON_DAYS)
                 if (today + timedelta(days=k)).day == day), None)


def _monthday_resolved(today: date, month: int, day: int) -> Optional[date]:
    """The next occurrence of a day+month on the business calendar (weekday agreement)."""
    for y in range(today.year, today.year + 5):
        d = _full_date(y, month, day)
        if d is not None and d >= today:
            return d
    return None


def _designator_numbers(raw: str) -> list:
    """R6-C4. "<Capitalised word> <number>" inside a sentence — a model ("Renault 12", "Gol
    1.6"), a street ("Corrientes 1500") — as normalised bigrams. Never sentence-initial,
    never after a temporal, scheduling or function word."""
    out = []
    for m in re.finditer(r"(?<=\S)\s+([A-ZÁÉÍÓÚÑ][A-Za-zÁÉÍÓÚÑáéíóúñü]*)\s+(\d{1,4}(?:\.\d)?)"
                         r"(?![\d:/\-]|\.\d)", raw or ""):
        word = _norm(m.group(1))
        before = _tokens((raw or "")[:m.start()])
        last = before[-1] if before else ""
        if not word or word in _DESIGNATOR_EXCLUDED or last in _DESIGNATOR_BLOCKERS \
                or _card_value(last) is not None or last.isdigit():
            continue
        out.append((word, m.group(2)))
    return out


def _scan_sentence(raw: str, index: int, today: date, _nested: bool = False) -> SentenceTemporal:
    """Consume every temporal expression of ONE sentence; return what it states.

    R6-C4: default-deny. After every recogniser and every explicit inert class ran, each
    remaining digit token, spelled cardinal, weekday/month fragment, am/pm/hs and
    approximator is recorded as an unresolved fragment; nothing temporal-looking is
    silently dropped. A negative availability core exempts only the placeholders inside its
    scope (`_NEG_SCOPE_GLUE`)."""
    # A parenthetical that is general business-hours information (CE's own rejection reason
    # "(ese día trabajamos de 9.30 a 14 hs)", "(los domingos no trabajamos)") or that has no
    # temporal content is information: it is read on its own and does not join the sentence.
    info_ranges: list = []
    if not _nested and "(" in (raw or ""):
        def _paren(m):
            sub = _scan_sentence(m.group(1), index, today, _nested=True)
            if sub.general_hours or not (sub.has_temporal or sub.lexicon or sub.weak):
                info_ranges.extend(sub.ranges)
                return " "
            return m.group(0)
        body = re.sub(r"\(([^()]*)\)", _paren, raw)
    else:
        body = raw
    # R6-C4: punctuation that `_norm` would drop (—, –, …, •, «») is a clause boundary here.
    s = " " + _norm("".join(" , " if ord(ch) > 127 and unicodedata.category(ch)[0] in "PS"
                            else ch for ch in body)) + " "
    days: list = []
    times: list = []
    parts: list = []
    unresolved: list = []
    conflicts: list = []
    ranges: list = list(info_ranges)
    vague: list = []                      # habitual/period wording: information or unresolved
    weak: list = []                       # R6-C4: unresolved when the sentence is temporal
    date_slots: list = []                 # index → DayRef awaiting a possible weekday
    time_groups: list = []                # index → (TimeRef indices) of one time placeholder
    part_marks: list = []                 # index → part indices of one part placeholder
    owned: dict = {}                      # placeholder → (time indices, part indices)

    def sweep(rx, fn):
        """Replace every match by the placeholder `fn` returns (False: not this form). `fn`
        may return (mark, (start, end)) to replace only part of its match."""
        nonlocal s
        pos = 0
        while True:
            m = rx.search(s, pos)
            if m is None:
                return
            nt, np_ = len(times), len(parts)
            res = fn(m)
            span = None
            if isinstance(res, tuple):
                res, span = res
            if res is False:
                pos = m.end()
                continue
            mark = res if res is not None else _P_INERT
            if mark == _P_TIME:
                time_groups.append(tuple(range(nt, len(times))))
                mark = _P_TIME + chr(0xE200 + len(time_groups) - 1)
            elif mark == _P_PART:
                part_marks.append(tuple(range(np_, len(parts))))
                mark = _P_PART + chr(0xE300 + len(part_marks) - 1)
            else:
                prev = owned.get(mark, ((), ()))
                owned[mark] = (prev[0] + tuple(range(nt, len(times))),
                               prev[1] + tuple(range(np_, len(parts))))
            a, b = span or m.span()
            s = s[:a] + f" {mark} " + s[b:]
            pos = a

    def date_mark(ref: Optional[DayRef], text: str) -> str:
        if ref is None:
            unresolved.append(text.strip())
            return _P_INERT
        date_slots.append(ref)
        return _P_DATE + chr(0xE100 + len(date_slots) - 1)

    def add_time(value: Optional[str], text: str, endpoint: bool = False):
        if value is None:
            unresolved.append(text.strip())
        else:
            times.append(TimeRef(value, text.strip(), index, endpoint))

    sweep(_COURTESY_RE, lambda m: _P_INERT)
    sweep(_PRICE_NORM_RE, lambda m: _P_INERT)
    # R6-C4: a relative offset is unresolved — read before durations, so "en unas 2 horas"
    # is never mistaken for one.
    sweep(_RELATIVE_OFFSET_RE, lambda m: (unresolved.append(m.group(0).strip()), _P_INERT)[1])
    sweep(_DURATION_RE, lambda m: _P_INERT)
    # R6-C4: explicitly inert number content — addresses, phones, thousands, engine sizes,
    # enumeration labels, list markers, counts, and "<Model|Street> <number>" designators.
    sweep(_ADDRESS_RE, lambda m: _P_INERT)
    sweep(_PHONE_RE, lambda m: _P_INERT)
    sweep(_THOUSANDS_RE, lambda m: _P_INERT)
    sweep(_ENGINE_RE, lambda m: _P_INERT)
    sweep(_ORDINAL_LABEL_RE, lambda m: _P_INERT)
    sweep(_LIST_MARK_RE, lambda m: _P_INERT)
    for word, number in _designator_numbers(body):
        sweep(re.compile(rf"\b{re.escape(word)}\s+{re.escape(number)}(?![\d:/\-]|\.\d)"),
              lambda m, w=word: (f"{w} {_P_INERT}", None)[0])

    def count(m):
        # "a las 10 y 20 minutos" is a clock, not a count of minutes; "el lunes 23 opciones"
        # is not a count either — a number right after a day word stays temporal
        before = s[:m.start()].split()
        last = before[-1] if before else ""
        if last in ("y", "menos", "el", "dia", "del", "al", "las") or last in _WEEKDAY_NAMES \
                or last.rstrip(".") in _WD_ABBR or last in _MONTH_WORDS \
                or (last and last[0] in _PLACEHOLDERS):
            return False
        return _P_INERT
    sweep(_COUNT_RE, count)

    # Dates. ISO first (a datetime string carries its time: "2026-11-19T10:00").
    def iso(m):
        d = _full_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        ref = DayRef("date", m.group(0), index, d, d.month if d else None,
                     d.day if d else None) if d else None
        mark = date_mark(ref, m.group(0))
        if m.group(4) is not None:
            add_time(_hhmm(int(m.group(4)), int(m.group(5))), m.group(0))
        return mark
    sweep(_ISO_RE, iso)

    def year_first(m):
        d = _full_date(int(m.group(1)), int(m.group(3)), int(m.group(4)))
        return date_mark(DayRef("date", m.group(0), index, d, d.month, d.day) if d else None,
                         m.group(0))
    sweep(_YEAR_FIRST_RE, year_first)
    # Windows next — their endpoints are times, and "de 9.30 a 14 hs" is hours, not the 9th
    # of month 30 — then "9.30 hs" and "a las 10.30", all before the numeric dates.
    def time_range(m, hour_only_ok=True):
        h1, m1, h2, m2 = m.group(1), m.group(2), m.group(3), m.group(4)
        for h, mm in ((h1, m1), (h2, m2)):
            v = _clock(int(h), int(mm or 0), None, None, hour_only=mm is None)
            add_time(v, m.group(0), endpoint=True)
        ranges.append(m.group(0).strip())
        return _P_TIME
    sweep(_HHMM_RANGE_RE, time_range)

    def spelled_range(m):
        part = m.group("part")
        pairs = (((m.group("h1"), m.group("q1")), (m.group("h2"), m.group("q2")))
                 if m.group("h1") else ((m.group("h3"), None), (m.group("h4"), None)))
        for hw, q in pairs:
            add_time(_spoken_clock(_hour_of(hw), q, part), m.group(0), endpoint=True)
        ranges.append(m.group(0).strip())
        return _P_TIME
    sweep(_SP_RANGE_RE, spelled_range)
    sweep(_TIME_RANGE_RE, time_range)
    sweep(_DOT_HS_RE, lambda m: (add_time(_hhmm(int(m.group(1)), int(m.group(2))), m.group(0)),
                                 _P_TIME)[1])
    # "a las 10.30" is a time, not the 10th of month 30.
    sweep(_LAS_DOT_RE, lambda m: (add_time(_hhmm(int(m.group(1)), int(m.group(2))), m.group(0)),
                                  _P_TIME)[1])

    def numeric(m):
        day, month, year = int(m.group(1)), int(m.group(3)), m.group(4)
        if year is not None:
            y = int(year) + (2000 if len(year) == 2 else 0)
            d = _full_date(y, month, day) if len(year) in (2, 4) else None
            return date_mark(DayRef("date", m.group(0), index, d, month, day) if d else None,
                             m.group(0))
        if _full_date(2000, month, day) is None:      # 2000 is a leap year: 29/2 is real
            return date_mark(None, m.group(0))
        return date_mark(DayRef("monthday", m.group(0), index, None, month, day), m.group(0))
    sweep(_NUMERIC_DATE_RE, numeric)

    def month_named(day_s, mon_s, year_s, text):
        day, month = int(day_s), _MONTH_WORDS[mon_s]
        if year_s is not None:
            d = _full_date(int(year_s), month, day)
            return date_mark(DayRef("date", text, index, d, month, day) if d else None, text)
        if _full_date(2000, month, day) is None:
            return date_mark(None, text)
        return date_mark(DayRef("monthday", text, index, None, month, day), text)
    sweep(_MONTH_FWD_RE, lambda m: month_named(m.group(1), m.group(2), m.group(3), m.group(0)))
    sweep(_MONTH_REV_RE, lambda m: month_named(m.group(2), m.group(1), m.group(3), m.group(0)))

    # Times (windows were read before the dates).

    def hhmm(m):
        add_time(_clock(int(m.group(1)), int(m.group(2)), m.group(3), None, hour_only=False),
                 m.group(0))
        return _P_TIME
    sweep(_HHMM_RE, hhmm)
    sweep(_MERIDIEM_RE, lambda m: (add_time(_clock(int(m.group(1)), 0, m.group(2), None, False),
                                            m.group(0)), _P_TIME)[1])
    sweep(_HOUR_HS_RE, lambda m: (add_time(_clock(int(m.group(1)), 0, None, None, True),
                                           m.group(0)), _P_TIME)[1])

    def las(m):
        word = m.group("hw") or m.group("one")
        quarter, part, unit = m.group("q"), m.group("part"), m.group("unit")
        anchored = m.group("anchor") is not None
        spelled = not word.isdigit()
        if m.group("one") and not anchored:
            return False                  # "la una" alone: an article and a numeral
        mer = m.group("mer")
        if spelled and not (anchored or quarter or part or unit or mer):
            return False                  # "las dos opciones": a count, not a clock
        if mer:
            h, off = _hour_of(word), _minute_offset(quarter)
            value = None
            if h is not None and off is not None and not part:
                base = _clock(h, 0, mer, None, hour_only=False)
                if base is not None:
                    total = int(base[:2]) * 60 + off
                    value = _hhmm(total // 60, total % 60) if 0 <= total < 1440 else None
            add_time(value, m.group("clock"))
        else:
            add_time(_spoken_clock(_hour_of(word), quarter, part), m.group("clock"))
        return _P_TIME, m.span("clock")
    sweep(_LAS_RE, las)
    sweep(_QUARTER_RE, lambda m: (add_time(_spoken_clock(_hour_of(m.group("hw")), m.group("q"),
                                                         m.group("part")), m.group(0)), _P_TIME)[1])
    sweep(_SPOKEN_PART_RE, lambda m: (add_time(_spoken_clock(_hour_of(m.group("hw")), None,
                                                             m.group("part")), m.group(0)),
                                      _P_TIME)[1])
    sweep(_NOON_RE, lambda m: (add_time("12:00" if m.group(1) == "mediodia" else "00:00",
                                        m.group(0)), _P_TIME)[1])
    sweep(_PRONOUN_COUNT_RE, lambda m: _P_INERT)

    def enumeration(m):
        before = s[:m.start()].split()
        if before and (before[-1][0] in _PLACEHOLDERS or before[-1] in _NUMBER_INTRODUCERS
                       or before[-1] in _WEEKDAY_NAMES or before[-1] in _WD_ABBR):
            return False
        return _P_INERT
    sweep(_ENUMERATION_RE, enumeration)

    # Parts of the day; "esta tarde" is also today.
    def daypart(m):
        which = m.group(1)
        name = m.group(2) or m.group(3) or m.group(4)
        lo, hi = _DAYPART_HOURS[name]
        parts.append(DaypartRef(name, index, lo, hi))
        if which == "esta":
            return date_mark(DayRef("relative", m.group(0), index, today), m.group(0))
        return _P_PART
    sweep(_HABITUAL_PART_RE, lambda m: (vague.append(m.group(0).strip()), _P_INERT)[1])
    sweep(_DAYPART_RE, daypart)

    # Relative days.
    sweep(_PASADO_RE, lambda m: date_mark(DayRef("relative", m.group(0), index,
                                                 today + timedelta(days=2)), m.group(0)))
    # R6-C4: the past is a day too ("ayer a las 10" is never today's 10:00 slot)
    sweep(_PAST_DAY_RE, lambda m: date_mark(DayRef(
        "relative", m.group(0), index,
        today - timedelta(days=2 if m.group(0).startswith(("ante", "antes")) else 1)),
        m.group(0)))
    sweep(_HOY_RE2, lambda m: date_mark(DayRef("relative", m.group(0), index, today),
                                        m.group(0)))
    sweep(_MANANA_RE2, lambda m: date_mark(DayRef("relative", m.group(0), index,
                                                  today + timedelta(days=1)), m.group(0)))

    # Weekday spans and habitual plurals: information inside a general-hours sentence; in a
    # proposition they name no single day (recorded; `general_hours` decides).
    habitual: list = []
    sweep(_WD_SPAN_RE, lambda m: (habitual.append(m.group(0).strip()), _P_INERT)[1])
    sweep(_WD_HABITUAL_RE, lambda m: (habitual.append(m.group(0).strip()), _P_INERT)[1])
    vague.extend(habitual)

    # A weekday: with an explicit date or day number (the date is authoritative and the
    # weekday must agree with it), in apposition to hoy/mañana, else its next occurrence in
    # [today + 1, today + 6]. A qualifier that is not implemented ("otro", "siguiente", "que
    # viene") and today's own weekday are unresolved (R6-C4, R6C3RV-S01 W6/H1).
    definite: list = []
    bare_weekdays: list = []

    def weekday(m):
        word = m.group("wd").rstrip(".")
        abbr = word not in _WEEKDAY_NAMES
        wd = _WD_ABBR[word] if abbr else _WEEKDAY_NAMES.index(word)
        art, qual, tail = m.group("art"), m.group("qual"), m.group("tail")
        dph, num = m.group("dph"), m.group("num")
        text = m.group(0).strip()
        if abbr and not (art in ("el", "este", "esta", "para el") or qual or dph or num):
            return False                  # "mar", "dom" alone: a fragment, not a day
        if (qual and qual not in ("proximo", "proxima")) or tail:
            return date_mark(None, text)   # a qualifier this module does not resolve
        if dph:                                                 # + explicit date
            k = ord(dph[1]) - 0xE100
            ref = date_slots[k]
            date_slots[k] = replace(ref, weekday=wd, text=text)
            target = ref.resolved or (_monthday_resolved(today, ref.month, ref.day)
                                      if ref.month else None)
            if target is None or target.weekday() != wd:
                conflicts.append(f"{word} ≠ {ref.text}")
            return dph                                          # keep the date's marker
        if num:                                                 # + day number
            n = int(num)
            hit = _nearest_with_day(today, n) if 1 <= n <= 31 else None
            if hit is None:
                return date_mark(None, text)
            if hit.weekday() != wd:
                conflicts.append(f"{word} ≠ día {n}")
            return date_mark(DayRef("weekday_date", text, index, hit, hit.month, n, wd), text)
        (definite if (art or qual) else bare_weekdays).append(text)
        before = s[:m.start()].rstrip()
        if len(before) >= 2 and before[-2] == _P_DATE and "\ue100" <= before[-1] <= "\ue1ff":
            ref = date_slots[ord(before[-1]) - 0xE100]
            if ref.kind == "relative" and ref.resolved is not None:   # "hoy lunes"
                if ref.resolved.weekday() != wd:
                    conflicts.append(f"{word} ≠ {ref.text}")
                return date_mark(DayRef("weekday", text, index, ref.resolved, weekday=wd),
                                 text)
        if wd == today.weekday():
            return date_mark(None, text)   # today, or the same weekday next week
        nxt = next(today + timedelta(days=k) for k in range(1, _WEEKDAY_HORIZON_DAYS)
                   if (today + timedelta(days=k)).weekday() == wd)
        return date_mark(DayRef("weekday", text, index, nxt, weekday=wd), text)
    sweep(_WD_FULL_RE, weekday)

    def bare_day(m):
        n = int(m.group(1))
        hit = _nearest_with_day(today, n) if 1 <= n <= 31 else None
        text = m.group(0).strip()
        if hit is None:
            return date_mark(None, text) if len(m.group(1)) <= 2 or "dia" in text else _P_INERT
        return date_mark(DayRef("day_of_month", text, index, hit, hit.month, n), text)
    sweep(_BARE_DOM_RE, bare_day)

    # ── The default-deny residual: everything temporal-looking that nothing consumed. ──
    frag_spans: list = []                 # (start, end) of every fragment, for the scope

    def frag(bucket, text, a, b):
        bucket.append(text)
        frag_spans.append((a, b))
    for m in _PERIOD_RE.finditer(s):
        frag(vague, m.group(0), *m.span())
    for m in _APPROX_TIME_RE.finditer(s):
        frag(vague, re.sub("[\ue000-\ue3ff]+", "<time>", m.group(0)).strip(), *m.span())
    for m in _APPROX_WORD_RE.finditer(s):
        frag(weak, m.group(0), *m.span())
    for m in re.finditer(r"(?<![a-z])" + _MERIDIEM, s):
        frag(vague, m.group(0), *m.span())            # a meridiem no clock consumed
    # a day as an interval bound ("antes del lunes", "hasta el 16", "desde el lunes") is not
    # that day
    for m in re.finditer(r"\b(?:antes|despues|luego|hasta|desde|pasado|previo)\s+(?:(?:del?|al|el|la)\s+)?"
                         + _P_DATE + "[\ue100-\ue1ff]", s):
        frag(vague, re.sub("[\ue000-\ue3ff]+", "<day>", m.group(0)).strip(), *m.span())
    # "a las <word>" that no clock reader consumed (a misspelt hour: "a las dies")
    for m in re.finditer(r"\b(?:a|para|desde|hasta)\s+las\s+([a-z]+)\b", s):
        frag(weak, m.group(0), *m.span())
    toks = [(t.group(0), t.start(), t.end()) for t in _RESIDUAL_TOKEN_RE.finditer(s)]
    words = [t[0] for t in toks]
    content = [t for t in words if t[0] not in _PLACEHOLDERS and (t[0].isalnum())]
    label_line = bool(content) and all(
        t.isdigit() or t in _LABEL_WORDS or _card_value(t) is not None for t in content)

    def is_ph(t, kinds=_P_DATE + _P_TIME + _P_PART):
        return bool(t) and t[0] in kinds

    for i, (tok, a, b) in enumerate(toks):
        prev = words[i - 1] if i else ""
        prev2 = words[i - 2] if i > 1 else ""
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if tok in _TIME_WORDS:
            frag(vague, tok, a, b)
        elif tok[0].isdigit():
            if re.search(r"[:/.\-]", tok):
                parts_ = re.split(r"[:/.\-]", tok)
                datelike = all(len(p) <= 2 for p in parts_) or (
                    len(parts_) == 3 and sum(len(p) == 4 for p in parts_) == 1
                    and all(len(p) <= 2 or len(p) == 4 for p in parts_))
                frag(vague if datelike else weak, tok, a, b)
            elif len(tok) <= 2:
                frag(unresolved, tok, a, b)            # default-deny: a bare day/hour number
            elif prev in ("las", "la", "dia") or nxt in _TIME_WORDS or is_ph(prev, _P_DATE) \
                    or prev in _WEEKDAY_NAMES or prev.rstrip(".") in _WD_ABBR:
                frag(vague, tok, a, b)       # "el día 1611", "lunes 1611"; "el 3008" is a model
        elif tok in _MONTH_FULL or tok in _STRONG_FRAGMENT_WORDS:
            frag(vague, tok, a, b)
        elif tok in _WEAK_FRAGMENT_WORDS:
            frag(weak, tok, a, b)
        elif tok in ("y", "menos") and nxt in ("cuarto", "media") and is_ph(prev, _P_TIME):
            frag(vague, f"{tok} {nxt}", a, b)
        else:
            value = _card_value(tok)
            if value is None:
                continue
            day_position = (prev in _SPELLED_INTRODUCERS or is_ph(prev) or prev in _WEEKDAY_NAMES
                            or prev in _MONTH_WORDS or prev in _WD_ABBR
                            or (prev in ("y", "menos") and is_ph(prev2, _P_TIME)))
            if _CARD_ONE_RE.fullmatch(tok):
                if day_position:
                    frag(unresolved, tok, a, b)
                continue
            standalone = not (nxt[:1].isalpha() and nxt not in ("y", "o", "a", "de", "hs",
                                                                "hrs", "horas", "en", "menos"))
            if value >= 13 or day_position or label_line or standalone:
                frag(unresolved, tok, a, b)
            else:
                frag(weak, tok, a, b)

    n_raw = _norm(body)
    question = _is_question(raw)
    plain = _CONDITIONAL_AVAILABILITY_RE.sub(" ", body)
    lexicon = (not question) and bool(_AVAILABILITY_RE.search(plain))
    conditional = bool(_CONDITIONAL_AVAILABILITY_RE.search(raw))
    toks_t = _tokens(body)
    transactional = any(is_transactional_token(t) for t in toks_t)
    transactional_verb = any(is_transactional_token(t) and t not in _TRANSACTIONAL_NOUNS
                             for t in toks_t) or "turno" in toks_t
    concrete_day = any(d.kind != "weekday" for d in date_slots) or bool(definite)
    habitual_frame = bool(_HABITUAL_FRAME_RE.search(n_raw)) or bool(habitual)
    # A customer-specific proposal ("te esperamos", "te reservo", "tu turno") is never
    # general information, whatever frame surrounds it.
    customer_specific = transactional_verb and any(t in _SECOND_PERSON for t in toks_t)
    general = (habitual_frame and not concrete_day and not customer_specific
               and all(t.endpoint for t in times) and not unresolved and not conflicts)

    # ── R6-C4: the negative scope. ──
    neg_spans = [m.span() for m in _NEGATION_RE.finditer(s)]
    scopes: list = []
    for na, nb in neg_spans:
        inside = [i for i, (_t, a, b) in enumerate(toks) if a >= na and b <= nb]
        if not inside:
            continue
        lo, hi = inside[0], inside[-1]
        while lo > 0 and (words[lo - 1] in _NEG_SCOPE_GLUE or words[lo - 1][0] in _PLACEHOLDERS):
            lo -= 1
        while hi + 1 < len(words) and (words[hi + 1] in _NEG_SCOPE_GLUE
                                       or words[hi + 1][0] in _PLACEHOLDERS):
            hi += 1
        scopes.append((toks[lo][1], toks[hi][2]))

    def in_scope(a, b):
        return any(sa <= a and b <= sb for sa, sb in scopes)
    negated_marks = set()
    positive_marks = set()
    for tok, a, b in toks:
        if tok[0] in (_P_DATE, _P_TIME, _P_PART) and len(tok) == 2:
            (negated_marks if in_scope(a, b) else positive_marks).add(tok)
    lexicon_outside = any(not in_scope(*m.span()) for m in _AVAILABILITY_RE.finditer(s))
    frag_outside = any(not in_scope(a, b) for a, b in frag_spans)
    # (A weekday/date conflict inside the scope only malforms the NEGATED day; it is still
    # refused under a confirmed booking, where nothing is exempt.)
    negative = bool(scopes) and not positive_marks and not lexicon_outside and not frag_outside \
        and not unresolved

    def split_marks(kind_items, mark_of):
        pos_, neg_ = [], []
        for i, item in enumerate(kind_items):
            (neg_ if mark_of(i) in negated_marks else pos_).append(item)
        return pos_, neg_

    def date_mark_of(k):
        return _P_DATE + chr(0xE100 + k)

    def time_mark_of(ti):
        for g, idxs in enumerate(time_groups):
            if ti in idxs:
                return _P_TIME + chr(0xE200 + g)
        return next((mk for mk, (tis, _p) in owned.items() if ti in tis), None)

    def part_mark_of(pi):
        for g, idxs in enumerate(part_marks):
            if pi in idxs:
                return _P_PART + chr(0xE300 + g)
        return next((mk for mk, (_t, pis) in owned.items() if pi in pis), None)

    if scopes:
        days_pos, days_neg = split_marks(date_slots, date_mark_of)
        times_pos, times_neg = split_marks(times, time_mark_of)
        parts_pos, parts_neg = split_marks(parts, part_mark_of)
    else:
        days_pos, days_neg = list(date_slots), []
        times_pos, times_neg = list(times), []
        parts_pos, parts_neg = list(parts), []
    if general:
        days_pos, days_neg = [], []       # a bare weekday in a general sentence is habitual
        vague = []
        weak = []
    return SentenceTemporal(
        index=index, text=raw, question=question, general_hours=general, negative=negative,
        lexicon=lexicon, conditional=conditional, transactional=transactional,
        scheduling=(transactional or any(t in _SCHEDULING_CUES for t in toks_t)
                    or bool(_LABEL_COLON_RE.search(n_raw))),
        days=tuple(days_pos), times=tuple(times_pos), dayparts=tuple(parts_pos),
        unresolved=tuple(dict.fromkeys(unresolved)), conflicts=tuple(conflicts),
        ranges=tuple(ranges), vague=tuple(dict.fromkeys(vague)),
        weak=tuple(dict.fromkeys(weak)), negated_days=tuple(days_neg),
        negated_times=tuple(times_neg), negated_dayparts=tuple(parts_neg))


def analyze_temporal(text: str, today: Optional[date] = None) -> TemporalAnalysis:
    """R6-C3. The temporal analysis of the COMPLETE reply, sentence by sentence, as one
    proposition. Pure; never raises on any text."""
    today = today if type(today) is date else business_today()
    rows = tuple(_scan_sentence(s, i, today) for i, s in enumerate(_sentences(text or "")))
    return TemporalAnalysis(sentences=rows, today=today)


@dataclass(frozen=True)
class TemporalVerdict:
    """The availability ruling for a whole reply: which sentences carry its scheduling
    proposition, the state, and — when refused — why."""
    state: TemporalState
    involved: frozenset
    allowed: bool
    proof: str
    detail: str = ""
    slot_sentences: frozenset = frozenset()


def temporal_verdict(analysis: TemporalAnalysis, facts: "CanonicalFacts") -> Optional[TemporalVerdict]:
    """None when the reply carries no scheduling/availability proposition; else the one
    ruling for every sentence that carries it. Only FULLY_PARSED_AND_CANONICAL is allowed.

    R6-C4: only an EXCLUSIVELY negative sentence (every day/time it names inside the scope
    of its negative core, nothing else temporal, no availability wording outside it) is left
    out of the positive proposition — and never under a confirmed booking, which may restate
    only itself. A sentence that negates one day and offers another joins the proposition
    with its positive items; and a positive time stated with no day beside a negated other
    day is refused (its day would be read from the negation)."""
    booking = bool(facts.booking_confirmed)
    rel = [s for s in analysis.sentences if not s.general_hours and (booking or not s.negative)]

    def days_of(s):
        return s.days + (s.negated_days if booking else ())

    def times_of(s):
        return s.times + (s.negated_times if booking else ())

    def parts_of(s):
        return s.dayparts + (s.negated_dayparts if booking else ())
    proposition = (any(times_of(s) for s in rel) or any(s.lexicon for s in rel)
                   or (any(s.has_temporal for s in rel) and analysis.transactional))
    if not proposition:
        return None
    # Every sentence that carries the proposition — its days, times, fragments, the
    # availability lexicon, and its scheduling wording ("Turno:", "Te confirmo la visita").
    involved = frozenset(s.index for s in rel if s.has_temporal or s.lexicon or s.scheduling)
    slot_sentences = frozenset(s.index for s in rel if times_of(s) or s.lexicon)
    form = " (question-form proposition)" if any(s.question for s in rel
                                                  if s.index in involved) else ""

    def refuse(state, proof, detail):
        return TemporalVerdict(state, involved, False, proof, detail + form, slot_sentences)

    U = TemporalState.FULLY_PARSED_BUT_UNSUPPORTED
    today = analysis.today
    if booking:
        canonical = facts.booked_date if type(facts.booked_date) is date else None
        allowed_times = tuple(facts.booked_times)
        source, proof = "committed booking", "booked ThreadRevision"
    else:
        if not facts.availability_checked:
            return refuse(U, "no ScheduleService evaluation",
                          "availability stated without evaluation")
        canonical = (facts.availability_date if type(facts.availability_date) is date
                     else None)
        if canonical is not None and canonical < today:
            canonical = None                          # a past evaluation is stale
        allowed_times = tuple(facts.offered_slots)
        source, proof = "evaluated", "ScheduleService evaluated date"
    fragments = tuple(x for s in rel for x in s.unresolved + s.vague + s.temporal_weak)
    conflicts = tuple(x for s in rel for x in s.conflicts)
    if fragments:
        return refuse(TemporalState.TEMPORAL_EXPRESSION_UNRESOLVED_OR_MALFORMED,
                      "temporal analysis", f"unresolved or malformed temporal expression "
                      f"{list(fragments)}: not a canonical {source} date/time")
    if conflicts:
        return refuse(U, "temporal analysis", f"weekday and date disagree {list(conflicts)}")
    days = [d for s in rel for d in days_of(s)]
    if days:
        if canonical is None:
            return refuse(U, proof, f"day/date stated without a canonical {source} date")
        wrong = [d.text for d in days if not d.is_date(canonical)]
        if wrong:
            return refuse(U, proof, f"stated day/date {wrong} is not the {source} date "
                                    f"{canonical.isoformat()}")
    stated = [t.value for s in rel for t in times_of(s)]
    parts = [p for s in rel for p in parts_of(s)]
    if not booking and not days and (stated or parts or any(s.lexicon for s in rel)):
        negated = [d for s in analysis.sentences if not s.general_hours for d in s.negated_days]
        other = [d.text for d in negated if canonical is None or not d.is_date(canonical)]
        if other:
            return refuse(U, proof, f"time/availability stated with no day beside the "
                                    f"negated day {other}: its day is not the {source} date")
    if booking:
        unknown = [t for t in stated if t not in allowed_times]
        if unknown:
            return refuse(U, proof, f"time {unknown} is not the committed booking's")
        start = allowed_times[0] if allowed_times else None
        if parts and (start is None or not all(p.contains(start) for p in parts)):
            return refuse(U, proof, "part of the day is not the committed booking's")
        return TemporalVerdict(TemporalState.FULLY_PARSED_AND_CANONICAL, involved, True,
                               "confirmed booking", "", slot_sentences)
    unknown = [t for t in stated if t not in allowed_times]
    if stated and (not allowed_times or unknown):
        return refuse(U, "ScheduleService slots",
                      ("time on the evaluated date not among the evaluated slots: "
                       if days else "slots not offered / not among evaluated slots: ")
                      + f"{unknown or stated}")
    for p in parts:
        mine = [t.value for s in rel if s.index == p.sentence for t in s.times] or stated
        if mine and not all(p.contains(t) for t in mine):
            return refuse(U, "ScheduleService slots", f"time not in the stated part of the "
                                                      f"day ({p.name})")
        if not mine and not any(p.contains(t) for t in allowed_times):
            return refuse(U, "ScheduleService slots", f"no evaluated slot in the stated "
                                                      f"part of the day ({p.name})")
    state = (TemporalState.FULLY_PARSED_AND_CANONICAL if (days or stated or parts)
             else TemporalState.NO_TEMPORAL_EXPRESSION)
    return TemporalVerdict(state, involved, True, "ScheduleService evaluation", "",
                           slot_sentences)


# R6-C2 compatibility views over the R6-C3 analysis (one implementation).

def _day_anchors(sentence: str, today: date) -> tuple[list, bool]:
    """Every day/date ONE sentence names: ("date", d) for a full or resolved date,
    ("monthday", month, day) for a date without a year, ("weekday", i) for a bare weekday;
    and whether anything temporal in it is unresolved, malformed or self-contradictory."""
    row = _scan_sentence(sentence, 0, today)
    out: list = []
    for d in row.days + row.negated_days:
        if d.kind == "monthday":
            out.append(("monthday", d.month, d.day))
        elif d.kind == "weekday":
            out.append(("weekday", d.weekday))
        else:
            out.append(("date", d.resolved))
    return out, bool(row.unresolved or row.vague or row.conflicts or row.temporal_weak)


def _anchor_is(anchor: tuple, canonical: date) -> bool:
    kind = anchor[0]
    if kind == "date":
        return anchor[1] == canonical
    if kind == "monthday":
        return (anchor[1], anchor[2]) == (canonical.month, canonical.day)
    if kind == "weekday":
        return anchor[1] == canonical.weekday()
    return False


def stated_day_is(text: str, canonical, today: Optional[date] = None) -> bool:
    """Public (CE producer gate). True when the COMPLETE `text` names no day and nothing
    temporal in it is unresolved, or when EVERY day/date it names — positive or negated, in
    any sentence — resolves (business calendar) to the exact canonical `date`. A named day
    with no canonical date, an unresolved or contradictory temporal expression, a second
    day, or another day is False. R6-C4: the same `analyze_temporal` the outbound
    validator uses, so every form the validator cannot read is False here too."""
    today = today if type(today) is date else business_today()
    canonical = canonical if type(canonical) is date else None
    an = analyze_temporal(text or "", today)
    if an.unresolved:
        return False
    named = [d for s in an.sentences if not s.general_hours for d in s.days + s.negated_days]
    if not named:
        return True
    return canonical is not None and all(d.is_date(canonical) for d in named)


def stated_times_of(text: str, today: Optional[date] = None) -> tuple:
    """Public (CE producer gate, R6-C4 P01). The normalised "HH:MM" times the COMPLETE
    `text` positively states, read by the same analysis — or () when anything temporal in
    it is unresolved. An AI-proposed time may be held only when it is one of these."""
    today = today if type(today) is date else business_today()
    an = analyze_temporal(text or "", today)
    if an.unresolved:
        return ()
    return tuple(dict.fromkeys(t.value for t in an.times))


# Availability named only as the condition of coordination is not a claim that a concrete
# slot exists: "coordinamos según la disponibilidad de agenda".
_CONDITIONAL_AVAILABILITY_RE = re.compile(
    r"\b(?:seg[uú]n|sujeto\s+a|sujeta\s+a|dependiendo\s+de|de\s+acuerdo\s+(?:a|con)|"
    r"en\s+funci[oó]n\s+de|conforme\s+a|a\s+confirmar\s+seg[uú]n)\s+(?:la\s+|nuestra\s+|tu\s+)?"
    r"disponibilidad\b", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", _norm(text)).split()


def _find_subsequence(tokens: list[str], needle: list[str]) -> list[int]:
    if not needle:
        return []
    k = len(needle)
    return [i for i in range(len(tokens) - k + 1) if tokens[i:i + k] == needle]


def _in_conditional_clause(sentence: str, start: int) -> bool:
    """Whether the token at `start` sits inside a conditional clause of the sentence.

    The clause is the run of tokens since the last comma/semicolon/colon; a conditional
    marker anywhere earlier in that clause makes it hypothetical, not an assertion.
    """
    clauses = re.split(r"[,;:]", _norm(sentence))
    seen = 0
    for clause in clauses:
        toks = re.sub(r"[^a-z0-9]+", " ", clause).split()
        if seen + len(toks) > start:
            return any(t in _CONDITIONAL_TOKENS for t in toks[:start - seen])
        seen += len(toks)
    return False


def _locative_frame_before(tokens: list[str], start: int) -> bool:
    """`tokens[start]` begins a place name: is it the complement of a locative frame?"""
    i = start - 1
    if i >= 0 and tokens[i] in _ARTICLES:
        i -= 1
    if i >= 0 and tokens[i] == "de":
        i -= 1
    if i >= 0 and tokens[i] in _PLACE_NOUNS:
        i -= 1
        if i >= 0 and tokens[i] in _ARTICLES:
            i -= 1
    return i >= 0 and tokens[i] in _LOCATIVE_PREPOSITIONS

_FALLBACK_REPLY = (
    "Para poder avanzar necesito confirmar algunos datos. "
    "¿Me contás qué vehículo querés revisar y en qué zona está?"
)


# ── R6: the structured outbound response contract (R5F1RV-01) ────────────────
#
# Owner decision (FINAL-CANDIDATE-CONVERGENCE-R6). The deterministic final layer validates a
# STRUCTURED response contract; it does not reconstruct domain meaning from rendered Spanish.
# Authority order:
#   1. canonical conversation/customer state            (CanonicalFacts)
#   2. structured response plan and structured claims   (ResponseDeclaration on the value)
#   3. deterministic validation of those claims         (validate_envelope)
#   4. rendered customer text
#   5. conservative text checks, as defence in depth only (validate_response, claim-shaped)
# Rendered prose is never a source of canonical vehicle, location, availability, question or
# booking state: an uncertain text observation is a DIAGNOSTIC finding and nothing more.


class ResponseProvenance(enum.Enum):
    """WHO authored the words of one outbound value. Closed set."""
    #: an owner-approved deterministic template with a registered contract (R5-F1)
    APPROVED_TEMPLATE = "APPROVED_TEMPLATE"
    #: deterministic text composed from canonical values, carrying its own declaration
    DETERMINISTIC_COMPOSED = "DETERMINISTIC_COMPOSED"
    #: the reply model's prose — and anything that does not declare otherwise (fail closed:
    #: an undeclared value gets the least trust, never more)
    AI_COMPOSED = "AI_COMPOSED"
    #: one of the registered validation fallbacks, chosen from canonical state
    VALIDATION_FALLBACK = "VALIDATION_FALLBACK"
    #: R6-C4B-R1: an approved claim-free natural BRIDGE (`verified_generation.
    #: run_bridge_generation`), checked ALONE before it is composed with canonical text.
    #: Its approval comes from that pipeline; here it gets the same full defence-in-depth
    #: analysis as any undeclared text and no structured trust of its own.
    VERIFIED_GENERATED = "VERIFIED_GENERATED"


class DomainClaim(enum.Enum):
    """The business facts an outbound value may assert. Values are the claim-class ids."""
    VEHICLE = CLAIM_VEHICLE
    LOCATION = CLAIM_LOCATION
    AVAILABILITY = CLAIM_AVAILABILITY
    PRICE = CLAIM_PRICE
    BOOKING = CLAIM_BOOKING
    ACCEPTANCE = CLAIM_ACCEPTANCE


class QuestionIntent(enum.Enum):
    """What a delivered message asks the customer for. Only a declared intent may change
    what the system expects the customer's next message to be."""
    VEHICLE_IDENTITY = "VEHICLE_IDENTITY"
    INSPECTION_LOCATION = "INSPECTION_LOCATION"


class StateUpdate(enum.Enum):
    """State an outbound value may cause to be written once it is delivered."""
    #: record an outstanding question for a declared, validated QuestionIntent
    RECORD_OUTSTANDING_QUESTION = "RECORD_OUTSTANDING_QUESTION"


@dataclass(frozen=True)
class StructuredClaim:
    """One declared business fact, by canonical identity — never by its wording.

    value: VEHICLE (marca, modelo) · LOCATION (zone name,) · PRICE (amount int, ...) ·
    AVAILABILITY ("HH:MM", ... [, "YYYY-MM-DD"]) · BOOKING () · ACCEPTANCE ().
    """
    kind: DomainClaim
    value: tuple = ()


@dataclass(frozen=True)
class ResponseDeclaration:
    claims: frozenset = frozenset()
    question_intents: frozenset = frozenset()
    state_updates: frozenset = frozenset()


_EMPTY_DECLARATION = ResponseDeclaration()


def declaration_is_well_formed(decl) -> bool:
    """Exact types all the way down. Anything else is malformed and fails closed."""
    try:
        if type(decl) is not ResponseDeclaration:
            return False
        for coll, member in ((decl.claims, StructuredClaim),
                             (decl.question_intents, QuestionIntent),
                             (decl.state_updates, StateUpdate)):
            if type(coll) is not frozenset or not all(type(x) is member for x in coll):
                return False
        for c in decl.claims:
            if type(c.kind) is not DomainClaim or type(c.value) is not tuple:
                return False
        if decl.state_updates and not decl.question_intents:
            return False                 # a state update with nothing declared to justify it
        return True
    except Exception:
        return False


# ── R5-F1: approved deterministic template contracts ─────────────────────────
#
# The vehicle check reads a claim out of prose with the catalogue resolver. That is the
# right tool for AI-composed prose, and the wrong one for an owner-approved deterministic
# template: the resolver read "uno de nuestros asesores" as Fiat Uno and replaced the
# motorcycle handoff with the vehicle/zone question (R5-F1). A template's meaning is known
# when it is written, so it is DECLARED, not reconstructed from the rendered words.
#
# The boundary is closed on every side:
#   * the template is named by a member of `ApprovedTemplateId` — not a string, not a flag;
#   * the contract registry is a read-only mapping; an id without a contract gets nothing;
#   * the contract names its policy from `TemplateValidationPolicy`; a policy that is not a
#     member, or that has no entry in `_POLICY_SKIPPED_CLAIMS`, skips nothing;
#   * the text sent must EQUAL the registered text, so the contract cannot be lent to other
#     words, and editing a template without editing its contract withdraws the exemption;
#   * a policy can skip only the claim classes it lists; every other check still runs.
# Every refusal falls back to FULL validation — the pre-R5-F1 behaviour — and is recorded
# as a finding. Nothing here can make validation weaker than it was for arbitrary text.

CLAIM_TEMPLATE_CONTRACT = "TEMPLATE_CONTRACT"


class TemplateValidationPolicy(enum.Enum):
    """How the validator treats one approved deterministic template. Closed set."""
    FULL_VALIDATION = "FULL_VALIDATION"
    # The template asserts no vehicle, so the vehicle comparison is not applied to it.
    NO_VEHICLE_CLAIM = "NO_VEHICLE_CLAIM"


#: The claim classes each policy exempts. Read with `.get()`: an unknown policy skips none.
_POLICY_SKIPPED_CLAIMS = MappingProxyType({
    TemplateValidationPolicy.FULL_VALIDATION: frozenset(),
    TemplateValidationPolicy.NO_VEHICLE_CLAIM: frozenset({CLAIM_VEHICLE}),
})


class ApprovedTemplateId(enum.Enum):
    """Owner-approved deterministic customer-facing templates that carry a contract."""
    MOTORCYCLE_ADVISOR_REPLY = "MOTORCYCLE_ADVISOR_REPLY"
    MOTORCYCLE_HANDOFF_REPLY = "MOTORCYCLE_HANDOFF_REPLY"


@dataclass(frozen=True)
class ApprovedTemplateContract:
    template_id: ApprovedTemplateId
    text: str
    policy: TemplateValidationPolicy
    reason: str


#: Exact owner-approved text. `conversation_engine` holds the same text as the constant it
#: sends; a structural test pins the two equal, and at run time a difference refuses the
#: exemption, so a copy change has to update this contract deliberately.
_APPROVED_TEMPLATE_CONTRACTS = MappingProxyType({
    ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY: ApprovedTemplateContract(
        template_id=ApprovedTemplateId.MOTORCYCLE_ADVISOR_REPLY,
        text="A la brevedad uno de nuestros asesores se estará contactando con vos.",
        policy=TemplateValidationPolicy.NO_VEHICLE_CLAIM,
        reason="motorcycle contact-form handoff; names no vehicle",
    ),
    ApprovedTemplateId.MOTORCYCLE_HANDOFF_REPLY: ApprovedTemplateContract(
        template_id=ApprovedTemplateId.MOTORCYCLE_HANDOFF_REPLY,
        text=("Las motos las revisamos de forma manual. "
              "Un asesor de Ridecheck se va a contactar con vos para continuar."),
        policy=TemplateValidationPolicy.NO_VEHICLE_CLAIM,
        reason="motorcycle handoff without a contact Flow; names no vehicle",
    ),
})


class ApprovedTemplateText(str):
    """A deterministic template's text, carrying WHICH approved template it is.

    The provenance travels with the value, so a call site keeps sending a plain constant
    (`_send_text_to_wa(ctx, _MOTORCYCLE_ADVISOR_REPLY)`) and nothing about the words is
    inferred. Any string operation — concatenation, formatting, `strip()`, `join` —
    returns a plain `str` and so drops the tag: composed text can never inherit it, and AI
    output is always a plain `str`. Construction never raises (it runs at import time); a
    text that differs from its contract is refused when it is validated.
    """

    def __new__(cls, template_id: ApprovedTemplateId, text: str):
        obj = super().__new__(cls, text)
        object.__setattr__(obj, "_template_id", template_id)
        return obj

    @property
    def template_id(self):
        return self._template_id

    def __setattr__(self, name, value):
        raise AttributeError("ApprovedTemplateText is immutable")

    def __reduce__(self):
        # Pickling/copying yields a plain str: provenance is never serialised and restored.
        return (str, (str(self),))

    # CPython returns the object itself from these when there is nothing to replace or no
    # separator is found, subclass included; every derived value must be a plain `str`.
    # (Even so the tag would earn nothing on changed words: the contract compares text.)
    def format(self, *args, **kwargs):
        return str.format(str(self), *args, **kwargs)

    def format_map(self, mapping):
        return str.format_map(str(self), mapping)

    def __mod__(self, values):
        return str.__mod__(str(self), values)

    def partition(self, sep):
        return str.partition(str(self), sep)

    def rpartition(self, sep):
        return str.rpartition(str(self), sep)


class ComposedText(str):
    """R6. Deterministic text composed from canonical values, with its DECLARATION.

    The composer — the code that read the canonical quote, focus vehicle, zone or slots —
    declares the claims and question intents its words carry, at the moment it writes them.
    The validator checks the declaration against canonical state (it never trusts it), and
    nothing reconstructs the meaning from the rendered sentence. Exactly like
    `ApprovedTemplateText`, every derived value is a plain `str` and so declares nothing:
    concatenating or editing composed text silently returns it to AI_COMPOSED trust.
    """

    def __new__(cls, text: str, declaration: ResponseDeclaration):
        obj = super().__new__(cls, text)
        object.__setattr__(obj, "_declaration", declaration)
        return obj

    @property
    def declaration(self):
        return self._declaration

    def __setattr__(self, name, value):
        raise AttributeError("ComposedText is immutable")

    def __reduce__(self):
        return (str, (str(self),))

    def format(self, *args, **kwargs):
        return str.format(str(self), *args, **kwargs)

    def format_map(self, mapping):
        return str.format_map(str(self), mapping)

    def __mod__(self, values):
        return str.__mod__(str(self), values)

    def partition(self, sep):
        return str.partition(str(self), sep)

    def rpartition(self, sep):
        return str.rpartition(str(self), sep)


def composed_text(text: str, *, claims=(), questions=()) -> ComposedText:
    """Declare deterministic composed text. A declared question may be recorded once sent."""
    questions = frozenset(questions)
    return ComposedText(text, ResponseDeclaration(
        claims=frozenset(claims), question_intents=questions,
        state_updates=(frozenset({StateUpdate.RECORD_OUTSTANDING_QUESTION})
                       if questions else frozenset())))


def template_tag_of(text):
    """The tag an outbound value carries, or None — exact type only, never a subclass.

    Returned UNCHECKED on purpose: `validate_response` is the one place that decides
    whether a tag earns anything, and it records a refusal for a tag that does not.
    """
    if type(text) is not ApprovedTemplateText:
        return None
    return getattr(text, "_template_id", None)


def approved_template_contract(template_id) -> Optional[ApprovedTemplateContract]:
    """The registered contract for a template id, or None. Never raises."""
    if not isinstance(template_id, ApprovedTemplateId):
        return None
    return _APPROVED_TEMPLATE_CONTRACTS.get(template_id)


def _exact_text_equal(text, registered) -> bool:
    """R5F1RV-03. Exact-type, base-`str` equality: never a subclass's own `__eq__`/`__ne__`."""
    if type(registered) is not str:
        return False
    if type(text) is not str and type(text) is not ApprovedTemplateText:
        return False
    return str.__eq__(text, registered) is True


def _type_name(value) -> str:
    """A refusal detail that cannot run arbitrary `__repr__`/`__str__` (R5F1RV-02)."""
    try:
        return type(value).__name__
    except Exception:                                   # pragma: no cover - defensive
        return "<unnamed>"


def _template_exemptions(template, text: str) -> tuple[frozenset, Optional["ClaimFinding"]]:
    """Resolve the claim classes an approved template may skip. Total: never raises.

    Returns (skipped claim classes, finding). Any doubt returns an EMPTY set — full
    validation — with a refusal finding naming why. R5F1RV-02: no refusal interpolates an
    arbitrary object's `__repr__`, the skipped-claims value is type-checked before it is
    consumed, and any unforeseen defect in registry data is itself a refusal — it can never
    become the exception that makes the caller deliver raw text.
    """
    if template is None:
        return frozenset(), None

    def _refused(detail: str):
        return frozenset(), ClaimFinding(CLAIM_TEMPLATE_CONTRACT, False,
                                         "approved template contract", ACTION_ALLOWED,
                                         f"exemption refused, full validation: {detail}")

    try:
        if type(template) is not ApprovedTemplateId:
            return _refused(f"not an ApprovedTemplateId ({_type_name(template)})")
        contract = _APPROVED_TEMPLATE_CONTRACTS.get(template)
        if type(contract) is not ApprovedTemplateContract or contract.template_id is not template:
            return _refused(f"no registered contract for {template.name}")
        policy = contract.policy
        if type(policy) is not TemplateValidationPolicy:
            return _refused(f"unknown policy ({_type_name(policy)}) for {template.name}")
        skipped = _POLICY_SKIPPED_CLAIMS.get(policy)
        if type(skipped) is not frozenset or not all(
                type(c) is str and c in _KNOWN_CLAIM_CLASSES for c in skipped):
            return _refused(f"malformed skipped-claims ({_type_name(skipped)}) for {template.name}")
        if not _exact_text_equal(text, contract.text):
            return _refused(f"text differs from the registered {template.name}")
        return skipped, ClaimFinding(
            CLAIM_TEMPLATE_CONTRACT, True, f"approved template {template.name}",
            ACTION_ALLOWED, f"policy {policy.name}; skipped {sorted(skipped)}")
    except Exception as exc:                            # registry corruption: refuse, never raise
        return _refused(f"contract resolution failed ({_type_name(exc)})")


def _norm(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return " ".join(stripped.lower().split())


def _is_question(sentence: str) -> bool:
    """A question never asserts a business fact."""
    s = (sentence or "").strip()
    return s.endswith("?") or s.startswith("¿")


# ── Canonical snapshot ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CanonicalFacts:
    """Everything the validator is allowed to treat as proven."""
    vehicle_marca: Optional[str] = None
    vehicle_modelo: Optional[str] = None
    inspection_zone_detail: Optional[str] = None
    inspection_zone_group: Optional[str] = None
    customer_origin_zones: tuple[str, ...] = ()
    known_zone_names: tuple[str, ...] = ()
    quote_total: Optional[int] = None
    quote_base: Optional[int] = None
    quote_viaticos: Optional[int] = None
    # Amounts already sent to this customer in the active cycle. Restating a quote the
    # customer has already received is legitimate ("¿cuánto salía?"), and such an amount
    # was itself validated when it was first sent — the AI cannot introduce a new one.
    previously_quoted: tuple[int, ...] = ()
    availability_checked: bool = False
    offered_slots: tuple[str, ...] = ()
    #: R6-C2 (R6C1RV-S01). The calendar date ScheduleService evaluated `offered_slots` for,
    #: read from the SAME evaluation state (`active_requested_date` written together with
    #: `last_offered_slots`). An exact `datetime.date`; None when absent, invalid, stale or
    #: inconsistent with the slot set — and then no reply may name a day for those slots.
    availability_date: Optional[date] = None
    #: R6-C2. "Today" on the business calendar (America/Argentina/Buenos_Aires) when the
    #: snapshot was taken; resolves hoy / mañana / pasado mañana / "el día N". None → now.
    business_today: Optional[date] = None
    booking_confirmed: bool = False
    #: R6-C2. The committed booking's own service date and its "HH:MM" start/end — the only
    #: day and times a confirmed booking may restate (never the last REQUESTED date).
    booked_date: Optional[date] = None
    booked_times: tuple[str, ...] = ()
    acceptance_confirmed: bool = False

    @property
    def vehicle_known(self) -> bool:
        return bool(self.vehicle_marca or self.vehicle_modelo)

    @property
    def location_known(self) -> bool:
        return bool(self.inspection_zone_detail or self.inspection_zone_group)

    @property
    def quote_known(self) -> bool:
        return self.quote_total is not None


@dataclass
class ClaimFinding:
    claim: str
    allowed: bool
    proof: str
    action: str
    detail: str = ""


@dataclass
class ValidationResult:
    text: str
    findings: list[ClaimFinding] = field(default_factory=list)
    #: R6. True when nothing survived and the fallback text was returned instead.
    fell_back: bool = False

    @property
    def blocked(self) -> list[ClaimFinding]:
        return [f for f in self.findings if not f.allowed]

    @property
    def modified(self) -> bool:
        return any(f.action not in (ACTION_ALLOWED, ACTION_DIAGNOSTIC) for f in self.findings)

    @property
    def diagnostics(self) -> list[ClaimFinding]:
        return [f for f in self.findings if f.action == ACTION_DIAGNOSTIC]


def _plain(text) -> str:
    """R5F1RV-04. Validator output is always an exact `str`, never a tagged value."""
    return str.__str__(text) if isinstance(text, str) else ("" if text is None else str(text))


def _diagnostic(claim: str, detail: str) -> ClaimFinding:
    return ClaimFinding(claim, True, "text observation (not a claim)", ACTION_DIAGNOSTIC, detail)


# ── Per-claim sentence checks ─────────────────────────────────────────────────

def _check_price(sentence: str, facts: CanonicalFacts,
                 question: bool = False) -> Optional[ClaimFinding]:
    # R6-C1 (R6IRV-P01): a concrete amount PROPOSED in a question ("¿Te parece bien $12.345
    # por la revisión?") is a price proposition and is checked like a statement. A
    # question-form mismatch is refused (removed), never rewritten into a proposal.
    amounts = _PRICE_AMOUNT_RE.findall(sentence)
    if not amounts:
        return None
    if not facts.quote_known:
        return ClaimFinding(CLAIM_PRICE, False, "no PricingService quote", ACTION_REMOVED,
                            "amount stated without a deterministic quote"
                            + (" (question-form proposition)" if question else ""))
    digits = {int(re.sub(r"\D", "", a) or 0) for a in amounts}
    allowed = {v for v in (facts.quote_total, facts.quote_base, facts.quote_viaticos)
               if v is not None} | set(facts.previously_quoted)
    if digits <= allowed:
        return ClaimFinding(CLAIM_PRICE, True, "PricingService quote / prior quote",
                            ACTION_ALLOWED)
    if question:
        return ClaimFinding(CLAIM_PRICE, False, "PricingService quote", ACTION_REMOVED,
                            "proposed amount does not match the canonical quote "
                            "(question-form proposition)")
    return ClaimFinding(CLAIM_PRICE, False, "PricingService quote", ACTION_REWRITTEN,
                        "amount does not match the canonical quote")


def _rewrite_price(sentence: str, facts: CanonicalFacts) -> str:
    """Replace every non-canonical amount with the canonical total."""
    canonical = f"${facts.quote_total:,.0f}".replace(",", ".")
    allowed = {v for v in (facts.quote_total, facts.quote_base, facts.quote_viaticos)
               if v is not None} | set(facts.previously_quoted)

    def _sub(m: re.Match) -> str:
        value = int(re.sub(r"\D", "", m.group(0)) or 0)
        return m.group(0) if value in allowed else canonical

    return _PRICE_AMOUNT_RE.sub(_sub, sentence)


def _check_vehicle(sentence: str, facts: CanonicalFacts,
                   resolver, question: bool = False) -> Optional[ClaimFinding]:
    hit = resolver(sentence) if resolver else None
    if hit is None:
        return None
    named = _norm(f"{getattr(hit, 'marca', '')} {getattr(hit, 'modelo', '')}")
    if getattr(hit, "claim_shaped", True) is not True:
        # R6: a catalogue token outside any vehicle-identity construction ("uno por uno",
        # "buena idea", "a las 10 en punto"). Recorded; never a claim, never a fallback.
        return _diagnostic(CLAIM_VEHICLE, f"catalogue token {named!r} without a vehicle "
                                          f"claim shape ({getattr(hit, 'reason', 'no frame')})")
    if question and not _has_transactional_verb(sentence):
        # R6-C1 (R6IRV-P01): an identity question with no inspection/booking proposition
        # ("¿Es un Peugeot 2008?") seeks information; it asserts nothing.
        return _diagnostic(CLAIM_VEHICLE, f"vehicle {named!r} in an information-seeking "
                                          "question (no transactional proposition)")
    if not facts.vehicle_known:
        return ClaimFinding(CLAIM_VEHICLE, False, "no current-focus candidate",
                            ACTION_REMOVED, f"named vehicle {named!r} is not canonical")
    canonical = _norm(f"{facts.vehicle_marca or ''} {facts.vehicle_modelo or ''}")
    if named and canonical and named not in canonical and canonical not in named:
        return ClaimFinding(CLAIM_VEHICLE, False, "current-focus candidate",
                            ACTION_REMOVED,
                            f"named vehicle {named!r} differs from canonical {canonical!r}")
    return ClaimFinding(CLAIM_VEHICLE, True, "current-focus candidate", ACTION_ALLOWED)


def _location_mentions(sentence: str, facts: CanonicalFacts) -> tuple[list[str], list[str]]:
    """(claimed zones, diagnostic-only zone words) for one sentence. R6, R5F1RV-01.

    A zone is CLAIMED only as a whole-word place name in a locative frame and outside a
    conditional clause. A substring ("pilares", "norteamericano"), a bare word with no
    locative frame ("Gracias Pilar", "unas flores") or a hypothetical ("si el auto está en
    zona Norte") is not a claim about where this vehicle is.
    """
    tokens = _tokens(sentence)
    claimed: list[str] = []
    observed: list[str] = []
    # The sentence must speak of the inspection subject, or of going to / being at a place;
    # "Trabajamos en CABA y GBA" is coverage information, not where this vehicle is.
    about_subject = bool(_INSPECTION_SUBJECT_RE.search(sentence)) or any(
        t in _PRESENCE_OR_MOTION_TOKENS for t in tokens)
    for zone in facts.known_zone_names:
        if not zone:
            continue
        needle = _tokens(zone)
        starts = _find_subsequence(tokens, needle)
        if not starts:
            continue
        if about_subject and any(_locative_frame_before(tokens, s)
                                 and not _in_conditional_clause(sentence, s) for s in starts):
            if zone not in claimed:
                claimed.append(zone)
        elif zone not in observed:
            observed.append(zone)
    return claimed, observed


def _check_location(sentence: str, facts: CanonicalFacts,
                    question: bool = False) -> Optional[ClaimFinding]:
    mentioned, observed = _location_mentions(sentence, facts)
    if not mentioned:
        if observed:
            return _diagnostic(CLAIM_LOCATION, f"place word(s) {observed!r} without a "
                                               f"locative claim frame")
        return None
    if question:
        # R6-C1 (R6IRV-P01): a location question is a proposition only when it proposes
        # the inspection there — an inspection/booking action or our own going there
        # ("¿Coordinamos la revisión en Pilar?", "¿Vamos hasta Pilar mañana?"). Asking where
        # the vehicle is ("¿El auto está en Pilar?") seeks information.
        toks = _tokens(sentence)
        if not (any(t in _FIRST_PERSON_MOTION for t in toks)
                or _has_transactional_verb(sentence)):
            return _diagnostic(CLAIM_LOCATION, f"place {mentioned!r} in an information-"
                                               "seeking question")
    if _ORIGIN_MARKER_RE.search(sentence):
        # Explicitly about the customer, not the vehicle.
        return ClaimFinding(CLAIM_LOCATION, True, "customer-origin statement", ACTION_ALLOWED)
    canonical = {_norm(z) for z in (facts.inspection_zone_detail, facts.inspection_zone_group) if z}
    if not canonical:
        return ClaimFinding(CLAIM_LOCATION, False, "no canonical inspection location",
                            ACTION_REMOVED, f"stated {mentioned!r} with no canonical zone")
    for zone in mentioned:
        if _norm(zone) not in canonical:
            origins = {_norm(z) for z in facts.customer_origin_zones}
            reason = ("customer origin stated as inspection location"
                      if _norm(zone) in origins else "zone differs from canonical")
            return ClaimFinding(CLAIM_LOCATION, False, "candidate/state zone",
                                ACTION_REMOVED, f"{zone}: {reason}")
    return ClaimFinding(CLAIM_LOCATION, True, "candidate/state zone", ACTION_ALLOWED)


def _check_availability(index: int, facts: CanonicalFacts, analysis: TemporalAnalysis,
                        verdict: Optional[TemporalVerdict], phase: str) -> Optional[ClaimFinding]:
    """R6-C3. The AVAILABILITY finding for sentence `index` of the reply.

    The ruling is made ONCE for the complete reply (`temporal_verdict`): every sentence that
    carries its scheduling proposition gets the same finding, so a day stated in one sentence
    and a time in another are one proposition, and one valid anchor cannot hide another.
    `phase` "slot" covers sentences with a time or the availability lexicon (checked where
    R6 checked availability); "day" covers sentences whose proposition is only a day, a
    period or an unresolved temporal fragment (checked after the booking/acceptance claims,
    so a completed-state claim is still reported as itself).

    * General business-hours information (a habitual frame with no concrete day, no
      customer-specific proposal and only ranges) asserts no slot: a range is a diagnostic,
      the availability lexicon still needs an evaluation.
    * A negative availability statement names an unavailable day/time on purpose: it needs
      an evaluation and a fully parsed temporal expression, never the offered list.
    """
    row = analysis.sentences[index]
    form = " (question-form proposition)" if row.question else ""
    booking = bool(facts.booking_confirmed)
    if row.general_hours:
        if phase != "slot":
            return None
        if row.lexicon:
            if booking:
                return ClaimFinding(CLAIM_AVAILABILITY, True, "confirmed booking", ACTION_ALLOWED)
            if not facts.availability_checked:
                return ClaimFinding(CLAIM_AVAILABILITY, False, "no ScheduleService evaluation",
                                    ACTION_REMOVED, "availability stated without evaluation")
            return ClaimFinding(CLAIM_AVAILABILITY, True, "ScheduleService evaluation",
                                ACTION_ALLOWED)
        if row.ranges:
            return _diagnostic(CLAIM_AVAILABILITY, f"business-hours range {list(row.ranges)} "
                                                   "(information, not a slot)")
        if row.conditional:
            return _diagnostic(CLAIM_AVAILABILITY, "availability named as a condition, "
                                                   "no concrete slot")
        return None
    if row.negative and not booking:
        if phase != "slot" or not (row.has_temporal or row.lexicon):
            return None
        if not facts.availability_checked:
            return ClaimFinding(CLAIM_AVAILABILITY, False, "no ScheduleService evaluation",
                                ACTION_REMOVED, "availability stated without evaluation" + form)
        if row.unresolved:
            # a malformed date/time is never read, even in a negative statement
            return ClaimFinding(CLAIM_AVAILABILITY, False, "temporal analysis", ACTION_REMOVED,
                                f"malformed temporal expression {list(row.unresolved)}" + form)
        return ClaimFinding(CLAIM_AVAILABILITY, True, "ScheduleService evaluation (negative)",
                            ACTION_ALLOWED)
    if verdict is None or index not in verdict.involved:
        if phase == "slot" and row.conditional:
            return _diagnostic(CLAIM_AVAILABILITY, "availability named as a condition, "
                                                   "no concrete slot")
        return None
    if (phase == "slot") != (index in verdict.slot_sentences):
        return None
    if verdict.allowed:
        return ClaimFinding(CLAIM_AVAILABILITY, True, verdict.proof, ACTION_ALLOWED)
    return ClaimFinding(CLAIM_AVAILABILITY, False, verdict.proof, ACTION_REMOVED, verdict.detail)


def _check_booking(sentence: str, facts: CanonicalFacts) -> Optional[ClaimFinding]:
    if not (_BOOKING_PARTICIPLE_RE.search(sentence) and _BOOKING_NOUN_RE.search(sentence)):
        return None
    if facts.booking_confirmed:
        return ClaimFinding(CLAIM_BOOKING, True, "booked ThreadRevision", ACTION_ALLOWED)
    return ClaimFinding(CLAIM_BOOKING, False, "no booked ThreadRevision", ACTION_REMOVED,
                        "booking asserted before the Flow completed")


def _check_acceptance(sentence: str, facts: CanonicalFacts) -> Optional[ClaimFinding]:
    if not _ACCEPTANCE_RE.search(sentence):
        return None
    if facts.acceptance_confirmed:
        return ClaimFinding(CLAIM_ACCEPTANCE, True, "lead flag / stage", ACTION_ALLOWED)
    return ClaimFinding(CLAIM_ACCEPTANCE, False, "no canonical acceptance", ACTION_REMOVED,
                        "commercial state asserted without acceptance")


# ── Public entry point ────────────────────────────────────────────────────────

def validate_response(
    text: str,
    facts: CanonicalFacts,
    vehicle_resolver=None,
    fallback: str = _FALLBACK_REPLY,
    *,
    approved_template: Optional[ApprovedTemplateId] = None,
) -> ValidationResult:
    """Validate a composed reply against canonical state.

    `vehicle_resolver` is an optional callable returning a catalog match for a text
    fragment (CE passes its deterministic resolver chain).  Injected rather than imported
    so this module stays free of catalog/DB dependencies.

    `approved_template` (R5-F1) names an owner-approved deterministic template. Only a
    registered `ApprovedTemplateId` whose contract text equals `text` exactly exempts the
    claim classes its policy lists; anything else is validated in full.
    """
    if not text or not text.strip():
        return ValidationResult(text=_plain(text), findings=[])

    skipped_claims, contract_finding = _template_exemptions(approved_template, text)

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s and s.strip()]
    kept: list[str] = []
    findings: list[ClaimFinding] = [contract_finding] if contract_finding else []

    # R6-C1 (R6IRV-P01): a question is no longer exempt from validation. Its TRANSACTIONAL
    # propositions — a concrete price, a concrete time or window, a vehicle or a place
    # inside an inspection/booking proposal — are checked by claim type against canonical
    # state; an information-seeking question ("¿Es un Peugeot 2008?", "¿En qué zona está el
    # auto?", "¿Hay disponibilidad?") still asserts nothing. Booking and acceptance are
    # completed-state claims: a question cannot assert them.
    # R6-C3 (R6C2RV-S01): ONE temporal analysis of the complete reply, ONE ruling for its
    # scheduling proposition, made before any sentence is judged.
    today = facts.business_today if type(facts.business_today) is date else business_today()
    analysis = analyze_temporal(text, today)
    verdict = temporal_verdict(analysis, facts)
    checks = (
        (CLAIM_PRICE, lambda i, s, q: _check_price(s, facts, q)),
        (CLAIM_VEHICLE, lambda i, s, q: _check_vehicle(s, facts, vehicle_resolver, q)),
        (CLAIM_LOCATION, lambda i, s, q: _check_location(s, facts, q)),
        (CLAIM_AVAILABILITY, lambda i, s, q: _check_availability(i, facts, analysis, verdict,
                                                                 "slot")),
        (CLAIM_BOOKING, lambda i, s, q: None if q else _check_booking(s, facts)),
        (CLAIM_ACCEPTANCE, lambda i, s, q: None if q else _check_acceptance(s, facts)),
        (CLAIM_AVAILABILITY, lambda i, s, q: _check_availability(i, facts, analysis, verdict,
                                                                 "day")),
    )

    for index, sentence in enumerate(sentences):
        question = _is_question(sentence)
        sentence_out = sentence
        drop = False
        for claim, check in checks:
            if claim in skipped_claims:
                continue
            finding = check(index, sentence_out, question)
            if finding is None:
                continue
            findings.append(finding)
            if finding.allowed:
                continue
            if finding.claim == CLAIM_PRICE and finding.action == ACTION_REWRITTEN:
                sentence_out = _rewrite_price(sentence_out, facts)
                continue
            drop = True
            break

        if not drop:
            kept.append(sentence_out)

    if not kept:
        # Nothing survived — never send an unsupported message; send a safe deterministic one.
        return ValidationResult(text=_plain(fallback), findings=findings, fell_back=True)

    return ValidationResult(text=_plain(" ".join(_plain(s).strip() for s in kept)),
                            findings=findings)


# ── R6: plan-appropriate validation fallbacks, each with an explicit contract ─

class ValidationFallbackId(enum.Enum):
    ASK_VEHICLE_AND_LOCATION = "ASK_VEHICLE_AND_LOCATION"
    ASK_LOCATION = "ASK_LOCATION"
    ASK_VEHICLE = "ASK_VEHICLE"
    NO_QUESTION = "NO_QUESTION"


@dataclass(frozen=True)
class ValidationFallbackContract:
    fallback_id: ValidationFallbackId
    text: str
    question_intents: frozenset
    reason: str


_FALLBACK_LEAD = "Para poder avanzar necesito confirmar algunos datos."

#: The words are the existing approved fallback and the existing canonical location ask
#: (`_CANONICAL_LOCATION_ASK`), recombined; NO_QUESTION is new copy (owner review item).
_VALIDATION_FALLBACKS = MappingProxyType({
    ValidationFallbackId.ASK_VEHICLE_AND_LOCATION: ValidationFallbackContract(
        ValidationFallbackId.ASK_VEHICLE_AND_LOCATION, _FALLBACK_REPLY,
        frozenset({QuestionIntent.VEHICLE_IDENTITY, QuestionIntent.INSPECTION_LOCATION}),
        "vehicle and inspection location both genuinely missing"),
    ValidationFallbackId.ASK_LOCATION: ValidationFallbackContract(
        ValidationFallbackId.ASK_LOCATION,
        f"{_FALLBACK_LEAD} ¿En qué localidad o barrio está el auto?",
        frozenset({QuestionIntent.INSPECTION_LOCATION}),
        "vehicle known; inspection location genuinely missing"),
    ValidationFallbackId.ASK_VEHICLE: ValidationFallbackContract(
        ValidationFallbackId.ASK_VEHICLE,
        f"{_FALLBACK_LEAD} ¿Me contás qué vehículo querés revisar?",
        frozenset({QuestionIntent.VEHICLE_IDENTITY}),
        "inspection location known; vehicle genuinely missing"),
    ValidationFallbackId.NO_QUESTION: ValidationFallbackContract(
        ValidationFallbackId.NO_QUESTION,
        "Gracias por tu mensaje. Cualquier otra consulta, escribime por acá.",
        frozenset(),
        "vehicle and location known: nothing is missing, so nothing is asked"),
})


def select_validation_fallback(facts) -> ValidationFallbackContract:
    """The fallback for a refused reply asks ONLY for facts canonical state lacks.

    Total: malformed facts select the no-question fallback, which changes no expectation.
    """
    try:
        vehicle, location = bool(facts.vehicle_known), bool(facts.location_known)
    except Exception:
        return _VALIDATION_FALLBACKS[ValidationFallbackId.NO_QUESTION]
    key = (ValidationFallbackId.ASK_VEHICLE_AND_LOCATION if not vehicle and not location else
           ValidationFallbackId.ASK_LOCATION if not location else
           ValidationFallbackId.ASK_VEHICLE if not vehicle else
           ValidationFallbackId.NO_QUESTION)
    return _VALIDATION_FALLBACKS[key]


def safe_failure_text() -> str:
    """What a validator FAILURE may send: never the unvalidated text, never a question."""
    return _VALIDATION_FALLBACKS[ValidationFallbackId.NO_QUESTION].text


# ── R6: structured-claim validation against canonical state ──────────────────

def _validate_structured_claim(claim, facts: CanonicalFacts) -> Optional[str]:
    """None when the declared claim is proven by canonical state, else why not. Total."""
    try:
        if type(claim) is not StructuredClaim or type(claim.kind) is not DomainClaim \
                or type(claim.value) is not tuple:
            return "malformed structured claim"
        kind, value = claim.kind, claim.value
        if kind is DomainClaim.VEHICLE:
            if len(value) != 2 or not facts.vehicle_known:
                return "vehicle claim without a canonical focus vehicle"
            named = _norm(f"{value[0] or ''} {value[1] or ''}")
            canonical = _norm(f"{facts.vehicle_marca or ''} {facts.vehicle_modelo or ''}")
            return None if named and named == canonical else "vehicle differs from canonical"
        if kind is DomainClaim.LOCATION:
            canonical = {_norm(z) for z in (facts.inspection_zone_detail,
                                            facts.inspection_zone_group) if z}
            if len(value) != 1 or not canonical:
                return "location claim without a canonical inspection location"
            return None if _norm(str(value[0])) in canonical else "location differs from canonical"
        if kind is DomainClaim.PRICE:
            allowed = {v for v in (facts.quote_total, facts.quote_base, facts.quote_viaticos)
                       if v is not None} | set(facts.previously_quoted)
            if not value or not facts.quote_known:
                return "price claim without a PricingService quote"
            return None if all(type(v) is int and v in allowed for v in value) \
                else "amount differs from the canonical quote"
        if kind is DomainClaim.AVAILABILITY:
            if not value or not facts.availability_checked:
                return "availability claim without a ScheduleService evaluation"
            # R6-C3: a deterministic availability claim carries EXACTLY one canonical ISO
            # date and at least one normalised "HH:MM" time, and both are checked directly
            # against CanonicalFacts — the rendered Spanish is never the truth source.
            dates = [v for v in value if type(v) is str and _ISO_DATE_STRICT_RE.fullmatch(v)]
            times = [v for v in value if v not in dates]
            if len(dates) != 1:
                return "availability claim must declare exactly one ISO date"
            if not times or not all(type(v) is str and _HHMM_STRICT_RE.fullmatch(v)
                                    for v in times):
                return "availability claim must declare normalised HH:MM times"
            today = (facts.business_today if type(facts.business_today) is date
                     else business_today())
            evaluated = (facts.availability_date
                         if type(facts.availability_date) is date else None)
            if evaluated is None or evaluated < today or canonical_date(dates[0]) != evaluated:
                return "declared date is not the evaluated date"
            if not facts.offered_slots or any(v not in facts.offered_slots for v in times):
                return "slot not among the evaluated slots"
            return None
        if kind is DomainClaim.BOOKING:
            if not facts.booking_confirmed:
                return "booking claim without a booked revision"
            if not value:
                return None
            # R6-C4A: a booking STATUS that states a date and time must state the committed
            # revision's own date and start time — exactly, as values, never as prose.
            dates = [v for v in value if type(v) is str and _ISO_DATE_STRICT_RE.fullmatch(v)]
            times = [v for v in value if v not in dates]
            if len(dates) != 1 or len(times) != 1 or not (
                    type(times[0]) is str and _HHMM_STRICT_RE.fullmatch(times[0])):
                return "booking claim must declare exactly one ISO date and one HH:MM time"
            booked = facts.booked_date if type(facts.booked_date) is date else None
            if booked is None or canonical_date(dates[0]) != booked:
                return "declared date is not the committed booking's date"
            if not facts.booked_times or times[0] != facts.booked_times[0]:
                return "declared time is not the committed booking's start time"
            return None
        if kind is DomainClaim.ACCEPTANCE:
            return None if facts.acceptance_confirmed else "acceptance claim without acceptance"
        return "unknown claim kind"
    except Exception as exc:
        return f"claim validation failed ({_type_name(exc)})"


# ── R6: the envelope — one validated outbound value ──────────────────────────

@dataclass(frozen=True)
class ResponseEnvelope:
    """The validated outbound value and its contract. `text` is always an exact `str`."""
    text: str
    provenance: ResponseProvenance
    question_intents: frozenset
    state_updates: frozenset
    findings: tuple
    template_id: Optional[ApprovedTemplateId] = None
    fallback_id: Optional[ValidationFallbackId] = None
    declared: bool = False

    @property
    def fell_back(self) -> bool:
        return self.provenance is ResponseProvenance.VALIDATION_FALLBACK

    def may_record(self, intent: QuestionIntent) -> bool:
        return (type(intent) is QuestionIntent and intent in self.question_intents
                and StateUpdate.RECORD_OUTSTANDING_QUESTION in self.state_updates)


def _approved_template_of(value):
    """The ONE place an outbound value's template tag is read (R5-F1, d14)."""
    return template_tag_of(value)


def approved_text_if_exact(value) -> Optional[str]:
    """The plain text of an exact approved template equal to its contract, else None.

    Used only on the validator-FAILURE path: owner-approved words may still be sent when
    canonical facts cannot be read; anything else gets `safe_failure_text()`. Total."""
    try:
        if type(value) is not ApprovedTemplateText:
            return None
        _skipped, finding = _template_exemptions(_approved_template_of(value), value)
        return _plain(value) if (finding is not None and finding.allowed) else None
    except Exception:
        return None


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(text or "") if s and s.strip()]


def _fallback_envelope(facts, findings, reason: str) -> ResponseEnvelope:
    contract = select_validation_fallback(facts)
    findings = list(findings) + [ClaimFinding(
        "RESPONSE_CONTRACT", False, "validated response plan", ACTION_REMOVED,
        f"{reason}; fallback {contract.fallback_id.name} ({contract.reason})")]
    return ResponseEnvelope(
        text=_plain(contract.text), provenance=ResponseProvenance.VALIDATION_FALLBACK,
        question_intents=contract.question_intents,
        state_updates=(frozenset({StateUpdate.RECORD_OUTSTANDING_QUESTION})
                       if contract.question_intents else frozenset()),
        findings=tuple(findings), fallback_id=contract.fallback_id, declared=True)


def validate_envelope(value, facts: CanonicalFacts, *, vehicle_resolver=None,
                      location_question=None) -> ResponseEnvelope:
    """Validate ONE outbound value through the structured contract. Total: never raises.

    1. The provenance is read from the value's exact type — never from its words.
    2. Declared structured claims are checked against canonical state; a conflict refuses
       the WHOLE value (it is never edited) in favour of the plan-appropriate fallback.
    3. The rendered text then passes the claim-shaped defence in depth. If nothing survives
       it, the plan-appropriate fallback replaces it.
    4. Question intents: a template or composer declares its own (checked for a question
       actually being asked); a fallback's come from its contract; AI prose declares none
       of its own — the one it may carry is INSPECTION_LOCATION, and only when canonical
       state genuinely lacks the location (the reply model's rule 20 obliges it to ask)
       and its own surviving question asks it (`location_question`, the certified probe).
    """
    try:
        findings: list = []
        value_type = type(value)
        template_id = None
        declaration = _EMPTY_DECLARATION
        declared = False
        if value_type is ApprovedTemplateText:
            provenance = ResponseProvenance.APPROVED_TEMPLATE
            template_id = _approved_template_of(value)
            declared = True
        elif value_type is ComposedText:
            decl = getattr(value, "_declaration", None)
            if declaration_is_well_formed(decl):
                provenance, declaration, declared = (
                    ResponseProvenance.DETERMINISTIC_COMPOSED, decl, True)
            else:
                provenance = ResponseProvenance.AI_COMPOSED
                findings.append(ClaimFinding("RESPONSE_CONTRACT", False, "declaration",
                                             ACTION_ALLOWED, "malformed declaration: treated "
                                             "as undeclared text, full validation"))
        elif _vg_is_verified(value):
            provenance = ResponseProvenance.VERIFIED_GENERATED
        else:
            provenance = ResponseProvenance.AI_COMPOSED
        text = _plain(value)

        for claim in declaration.claims:
            why = _validate_structured_claim(claim, facts)
            if why is not None:
                findings.append(ClaimFinding(
                    getattr(getattr(claim, "kind", None), "value", "UNKNOWN"), False,
                    "structured claim vs canonical state", ACTION_REMOVED, why))
                return _fallback_envelope(facts, findings, "declared claim refused")

        result = validate_response(text, facts, vehicle_resolver=vehicle_resolver,
                                   approved_template=template_id)
        findings.extend(result.findings)
        if result.fell_back:
            return _fallback_envelope(facts, findings, "no sentence survived validation")

        out = _plain(result.text)
        questions = [s for s in _sentences(out) if _is_question(s)]
        intents: set = set()
        if provenance is ResponseProvenance.APPROVED_TEMPLATE:
            contract = approved_template_contract(template_id)
            declared_q = getattr(contract, "question_intents", frozenset()) if contract else frozenset()
            intents = set(declared_q) if questions else set()
        elif provenance is ResponseProvenance.DETERMINISTIC_COMPOSED:
            if declaration.question_intents and not questions:
                findings.append(ClaimFinding("RESPONSE_CONTRACT", False, "declaration",
                                             ACTION_ALLOWED, "declared question intent but "
                                             "no question delivered: intent withdrawn"))
            else:
                intents = set(declaration.question_intents)
        elif not facts.location_known and location_question is not None and any(
                location_question(q) for q in questions):
            intents = {QuestionIntent.INSPECTION_LOCATION}
        return ResponseEnvelope(
            text=out, provenance=provenance, question_intents=frozenset(intents),
            state_updates=(frozenset({StateUpdate.RECORD_OUTSTANDING_QUESTION})
                           if intents else frozenset()),
            findings=tuple(findings), template_id=template_id, declared=declared)
    except Exception as exc:
        return ResponseEnvelope(
            text=_plain(safe_failure_text()), provenance=ResponseProvenance.VALIDATION_FALLBACK,
            question_intents=frozenset(), state_updates=frozenset(),
            findings=(ClaimFinding("RESPONSE_CONTRACT", False, "validator", ACTION_REMOVED,
                                   f"validation failed ({_type_name(exc)}): safe line sent"),),
            fallback_id=ValidationFallbackId.NO_QUESTION, declared=True)
