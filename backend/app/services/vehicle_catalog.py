"""Deterministic vehicle catalog — loaded from backend/app/data/vehicle_catalog.json.

lookup_vehicle(text) scans text for known vehicle aliases (longest-first, regex
word-boundary) and returns a VehicleMatch or None.  Always runs before any AI
call so tipo_vehiculo is never guessed by the model.

Version 2.0 — catalog sourced from RideCheck reviewed vehicles (181) and
Argentina 2026 market catalog (279).  Brand-only input always returns None.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path


@dataclass(frozen=True)
class VehicleMatch:
    marca: str
    modelo: str
    tipo_vehiculo: str      # AUTO | SUV_4X4_DEPORTIVO | SUV/4x4 | "" (recognition-only)
    confidence: str         # "high" | "medium"
    matched_alias: str
    needs_confirmation: bool = False
    recognition_only: bool = False


def _norm(s: str) -> str:
    """Normalize text for alias matching — same pipeline used by n8n Vehicle Normalizer."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ── Catalog loading ────────────────────────────────────────────────────────
_CATALOG_PATH = Path(__file__).parent.parent / "data" / "vehicle_catalog.json"


def _load_catalog() -> dict[str, dict]:
    """Build normalized-alias → entry dict from vehicle_catalog.json.

    All alias keys are normalized through _norm() before insertion so that
    hyphen/accent/case differences in alias strings don't create dead patterns.
    Longest-first sort is applied after loading.
    """
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        data = json.load(f)

    cat: dict[str, dict] = {}

    for v in data["vehicles"]:
        brand    = v["brand"]
        model    = v["model"]
        tipo     = v.get("tipo_vehiculo", "")
        rec_only = v.get("recognition_only", False)

        base: dict = {
            "marca": brand,
            "modelo": model,
            "t": tipo,
            "recognition_only": rec_only,
        }

        for alias in v.get("aliases", []):
            key = _norm(alias)
            if key:
                cat[key] = base

        for typo_entry in v.get("typo_aliases", []):
            key = _norm(typo_entry["alias"])
            if key:
                cat[key] = {**base, "typo": typo_entry.get("canonical", "")}

    return cat


_CAT: dict[str, dict] = _load_catalog()

# Sort aliases longest-first so multi-word aliases match before single-word ones.
_SORTED_KEYS: list[str] = sorted(_CAT.keys(), key=len, reverse=True)

# Pre-compiled regexes — keys are already normalized so patterns match correctly.
_KEY_RE: dict[str, re.Pattern] = {
    k: re.compile(r"(?:^|\s)" + re.escape(k) + r"(?:\s|$)")
    for k in _SORTED_KEYS
}


@dataclass(frozen=True)
class FuzzyLookupResult:
    """Result of fuzzy_lookup_vehicle()."""
    outcome: str          # "AUTO_ACCEPT" | "CONFIRM" | "UNRESOLVED"
    hit: VehicleMatch | None
    score: float
    second_hit: VehicleMatch | None
    second_score: float
    gap: float
    make_constrained: bool


# ── Fuzzy lookup constants ─────────────────────────────────────────────────────
_HIGH_THRESHOLD: float = 0.87
_MEDIUM_THRESHOLD: float = 0.70
_GAP_THRESHOLD: float = 0.15

# Sorted longest-first so multi-word brands ("alfa romeo") beat shorter ones.
_BRAND_NORMS: list[tuple[str, str]] = sorted(
    [
        (_norm(entry["marca"]), entry["marca"])
        for entry in _CAT.values()
        if entry.get("marca")
    ],
    key=lambda x: len(x[0]),
    reverse=True,
)
# Deduplicate while preserving order.
_seen_brands: set[str] = set()
_BRAND_NORMS_DEDUP: list[tuple[str, str]] = []
for _nb, _ob in _BRAND_NORMS:
    if _nb not in _seen_brands:
        _seen_brands.add(_nb)
        _BRAND_NORMS_DEDUP.append((_nb, _ob))
_BRAND_NORMS = _BRAND_NORMS_DEDUP
del _seen_brands, _BRAND_NORMS_DEDUP, _nb, _ob

# Pre-build a mapping from normalized(brand + model) → catalog entry for O(1) lookup.
_FULL_FORM_TO_ENTRY: dict[str, dict] = {}
_FULL_FORM_ORDER: list[str] = []
for _key in _SORTED_KEYS:
    _entry = _CAT[_key]
    _nfull = _norm(f"{_entry['marca']} {_entry['modelo']}")
    if _nfull not in _FULL_FORM_TO_ENTRY:
        _FULL_FORM_TO_ENTRY[_nfull] = _entry
        _FULL_FORM_ORDER.append(_nfull)
del _key, _entry, _nfull


def _detect_make(normalized_text: str) -> tuple[str | None, bool]:
    """Return (original_brand, constrained) if exactly one known brand is found."""
    matches: list[str] = []
    for nbrand, brand in _BRAND_NORMS:
        pattern = r"(?:^|\s)" + re.escape(nbrand) + r"(?:\s|$)"
        if re.search(pattern, normalized_text):
            matches.append(brand)
    if len(matches) == 1:
        return matches[0], True
    return None, False


def _entry_to_match(entry: dict) -> VehicleMatch:
    return VehicleMatch(
        marca=entry["marca"],
        modelo=entry["modelo"],
        tipo_vehiculo=entry.get("t", ""),
        confidence="high",
        matched_alias=_norm(f"{entry['marca']} {entry['modelo']}"),
        recognition_only=entry.get("recognition_only", False),
    )


def _best_ngram_score(n: str, form: str) -> float:
    """Best SequenceMatcher ratio between form and any same-length word window in n.

    Handles the case where a vehicle name is embedded in a longer message that
    includes year/location tokens — e.g. "ford ksl 2019 en palermo" contains the
    2-word window "ford ksl" which scores 0.80 against "ford ka" (CONFIRM), while
    the full-text score of 0.45 would be UNRESOLVED.
    """
    words_n = n.split()
    words_f = form.split()
    k = len(words_f)
    if not words_n or not words_f or k > len(words_n):
        return 0.0
    best = 0.0
    for i in range(len(words_n) - k + 1):
        window = " ".join(words_n[i : i + k])
        r = SequenceMatcher(None, window, form).ratio()
        if r > best:
            best = r
    return best


def fuzzy_lookup_vehicle(text: str) -> FuzzyLookupResult:
    """Conservative fuzzy vehicle lookup using SequenceMatcher.

    Exact lookup (lookup_vehicle) must always be called first (VN-1).
    Call this function only on exact miss (VN-2).
    Uses current-turn text only (VN-3).

    Returns FuzzyLookupResult with outcome AUTO_ACCEPT | CONFIRM | UNRESOLVED.
    """
    _unresolved = FuzzyLookupResult("UNRESOLVED", None, 0.0, None, 0.0, 0.0, False)

    n = _norm(text)
    if not n:
        return _unresolved

    # Short-token protection (VN-10): 1-2 char inputs are always UNRESOLVED.
    if len(n) <= 2:
        return _unresolved

    detected_make, make_constrained = _detect_make(n)

    # Build candidate forms, optionally filtered by make.
    candidates: list[tuple[str, dict]] = []
    for nfull, entry in _FULL_FORM_TO_ENTRY.items():
        if make_constrained and entry["marca"] != detected_make:
            continue
        candidates.append((nfull, entry))

    if not candidates:
        return FuzzyLookupResult("UNRESOLVED", None, 0.0, None, 0.0, 0.0, make_constrained)

    # Score all candidate forms — take the better of the full-text score and the
    # best same-length word-window score.  The window score handles messages where
    # a vehicle name is followed by a year or location ("ford ksl 2019 en palermo").
    scored = sorted(
        [(max(
              SequenceMatcher(None, n, form).ratio(),
              _best_ngram_score(n, form),
          ), form, entry)
         for form, entry in candidates],
        key=lambda x: x[0],
        reverse=True,
    )

    s1, _f1, entry1 = scored[0]
    s2, _f2, entry2 = scored[1] if len(scored) > 1 else (0.0, "", {})
    gap = s1 - s2

    if s1 < _MEDIUM_THRESHOLD:
        return FuzzyLookupResult("UNRESOLVED", None, s1, None, s2, gap, make_constrained)

    hit = _entry_to_match(entry1)
    second_hit = _entry_to_match(entry2) if entry2 else None

    if s1 >= _HIGH_THRESHOLD and gap >= _GAP_THRESHOLD:
        outcome = "AUTO_ACCEPT"
    else:
        outcome = "CONFIRM"

    return FuzzyLookupResult(outcome, hit, s1, second_hit, s2, gap, make_constrained)


def lookup_vehicle(text: str) -> VehicleMatch | None:
    """Scan *text* for a known vehicle alias and return a VehicleMatch or None.

    Checks aliases longest-first so brand+model beats bare model name.
    Returns confidence="high" for direct matches, "medium" for typo variants.
    Returns recognition_only=True when the vehicle is known but has no
    deterministic pricing category — caller must not quote without confirmation.

    Brand-contradiction guard: when exactly one brand is explicitly present in the
    text and it differs from the matched alias's brand, the alias is skipped.
    Prevents "honda ranger" → Ford Ranger, "ford civic" → Honda Civic, etc.
    Model-only inputs ("ranger", "civic") are unaffected because _detect_make
    returns make_constrained=False when no brand is present.
    """
    n = _norm(text)
    detected_make, make_constrained = _detect_make(n)

    for key in _SORTED_KEYS:
        if _KEY_RE[key].search(n) or n == key:
            entry = _CAT[key]
            if make_constrained and entry["marca"] != detected_make:
                continue
            is_typo = "typo" in entry
            return VehicleMatch(
                marca=entry["marca"],
                modelo=entry["modelo"],
                tipo_vehiculo=entry["t"],
                confidence="medium" if is_typo else "high",
                matched_alias=key,
                needs_confirmation=is_typo,
                recognition_only=entry.get("recognition_only", False),
            )
    return None
