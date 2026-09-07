"""L4.7W2-F1 — bounded locality recovery for imperfect speech.

Controlled Wild W2 failed on a voice message. The customer said *"el auto está en
Berazategui"*; Whisper stored **"Ok, el auto está embarazado, Tegui."** — the locality was
split into a real Spanish word plus an orphan syllable. `Berazategui` is in the catalog and
the intended sentence resolves; the transcript does not, so CE safely asked again.

The tempting fix is fuzzy matching over the message. Measured against the real 207-name
catalog, that is not a small risk — it is the same defect class L4.7W1-F2 closed for
vehicles, and worse here because a locality write also fixes a price:

    'tegui'  -> Berazategui (1.000)     the recovery we want
    'esta'   -> Floresta    (1.000)     "el auto ESTÁ en…" becomes a neighbourhood
    'auto'   -> San Justo   (0.615)

No purely lexical rule separates those two: both are mid-word suffixes covering about half
their catalog name. What separates them is that something first established *"Tegui" is a
place name* and *"está" is a verb*. So the protection cannot live in the scorer — it has to
live in what the scorer is allowed to see.

Hence: this module cannot be handed a sentence. Its entry point takes a `LocationFragment`,
which can only be built from evidence that a location was named, and which additionally
requires the token to be proper-noun-shaped in the source text. A raw string has no way in.

And an approximate hit is never an answer. ASR corruption is uncertainty about *what was
said*; a confident match on a corrupted token is still a guess. Approximate results are
`PROPOSED` and must be confirmed by the customer before anything canonical is written.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

RULE_ID = "resolve.locality_fragment"
RULE_VERSION = "v1"

# A fragment is a NAME, not a phrase. Four words is generous for "San Miguel del Monte".
MAX_FRAGMENT_WORDS = 4
# Below four characters a token carries too little signal to be worth matching at all.
MIN_FRAGMENT_CHARS = 4
# A candidate must look like the same word, not merely rhyme with it.
MIN_SCORE = 0.72
# Two candidates this close together are not a recovery, they are a coin toss.
MIN_GAP = 0.08

# Roles that may carry an inspection locality. CUSTOMER_ORIGIN is deliberately absent:
# where the customer lives never becomes where the car is (rule E).
INSPECTION_ROLES = frozenset({"INSPECTION_LOCATION", "VEHICLE_LOCATION"})


def _norm(value: str) -> str:
    stripped = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def _is_proper_noun_shaped(token: str, source_text: str) -> bool:
    """Did this token appear capitalised in the message it came from?

    Spanish locality names are proper nouns; verbs and greetings are not. This is a
    property of the language, not a list of blocked words, and it is what stops "está"
    from ever being offered to the scorer even if some producer mislabels it.
    """
    if not token or not source_text:
        return False
    for word in token.split():
        pattern = re.compile(r"(?<![\w])" + re.escape(word) + r"(?![\w])", re.IGNORECASE)
        if not any(m.group(0)[:1].isupper() for m in pattern.finditer(source_text)):
            return False
    return True


@dataclass(frozen=True)
class LocationFragment:
    """A span already established to name a place. The only way into this module."""
    text: str
    role: str
    producer: str
    source_text: str = ""

    @property
    def is_inspection_role(self) -> bool:
        return self.role in INSPECTION_ROLES

    @staticmethod
    def from_evidence(text: str, role: str, producer: str,
                      source_text: str = "") -> Optional["LocationFragment"]:
        """Build a fragment, or refuse. Refusal is the common, safe outcome."""
        candidate = (text or "").strip(" ,.;:!?¿¡\"'")
        if not candidate:
            return None
        if len(candidate.split()) > MAX_FRAGMENT_WORDS:
            return None                      # a phrase is not a name
        if len(_norm(candidate).replace(" ", "")) < MIN_FRAGMENT_CHARS:
            return None
        if source_text and not _is_proper_noun_shaped(candidate, source_text):
            return None                      # "está" is not a place, whoever said it was
        return LocationFragment(text=candidate, role=(role or "").upper(),
                                producer=producer, source_text=source_text)


@dataclass(frozen=True)
class LocalityCandidate:
    zone_group: Optional[str]
    zone_detail: str
    score: float


@dataclass(frozen=True)
class LocalityMatch:
    """Advisory. `EXACT` may proceed; `APPROXIMATE` must be confirmed by the customer."""
    status: str = "NONE"                     # EXACT | APPROXIMATE | AMBIGUOUS | NONE
    best: Optional[LocalityCandidate] = None
    alternatives: tuple[LocalityCandidate, ...] = ()
    reason: str = ""
    rule_id: str = RULE_ID
    rule_version: str = RULE_VERSION

    @property
    def needs_confirmation(self) -> bool:
        """A corrupted token that matched well is still a corrupted token."""
        return self.status == "APPROXIMATE"

    @property
    def is_canonical(self) -> bool:
        return self.status == "EXACT"


def _score(fragment: str, name: str) -> float:
    a, b = _norm(fragment), _norm(name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # Containment: "tegui" inside "berazategui". Scaled by how much of the name it covers,
    # so a two-letter coincidence cannot reach the threshold.
    if a in b:
        ratio = max(ratio, 0.60 + 0.40 * (len(a) / len(b)))
    elif b in a:
        ratio = max(ratio, 0.60 + 0.40 * (len(b) / len(a)))
    return ratio


def resolve_locality_fragment(
    fragment: LocationFragment,
    catalog: Iterable,
) -> LocalityMatch:
    """Match a bounded location fragment against the authoritative locality catalog.

    `catalog` is any iterable of objects exposing `zone_group` / `zone_detail` — the
    deterministic catalog stays the only source of canonical values. This function never
    writes anything and never decides a zone; it proposes.
    """
    if not isinstance(fragment, LocationFragment):
        raise TypeError(
            "resolve_locality_fragment requires a LocationFragment. Passing raw message "
            "text is the defect this module exists to prevent."
        )
    if not fragment.is_inspection_role:
        return LocalityMatch(reason=f"role {fragment.role!r} cannot carry an inspection locality")

    scored: list[LocalityCandidate] = []
    seen: set[str] = set()
    for zone in catalog or ():
        detail = (getattr(zone, "zone_detail", "") or "").strip()
        if not detail or detail in seen:
            continue
        seen.add(detail)
        scored.append(LocalityCandidate(
            zone_group=(getattr(zone, "zone_group", None) or None),
            zone_detail=detail,
            score=_score(fragment.text, detail)))
    if not scored:
        return LocalityMatch(reason="empty catalog")

    scored.sort(key=lambda c: c.score, reverse=True)
    best = scored[0]
    runner_up = scored[1] if len(scored) > 1 else None

    if best.score >= 1.0:
        return LocalityMatch(status="EXACT", best=best, reason="exact catalog name")
    if best.score < MIN_SCORE:
        return LocalityMatch(status="NONE", alternatives=tuple(scored[:3]),
                             reason=f"best score {best.score:.3f} below {MIN_SCORE}")
    if runner_up is not None and (best.score - runner_up.score) < MIN_GAP:
        return LocalityMatch(status="AMBIGUOUS", best=None,
                             alternatives=tuple(scored[:3]),
                             reason="two candidates too close to choose between")
    return LocalityMatch(status="APPROXIMATE", best=best,
                         alternatives=tuple(scored[1:3]),
                         reason="single strong candidate — requires customer confirmation")
