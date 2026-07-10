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


def lookup_vehicle(text: str) -> VehicleMatch | None:
    """Scan *text* for a known vehicle alias and return a VehicleMatch or None.

    Checks aliases longest-first so brand+model beats bare model name.
    Returns confidence="high" for direct matches, "medium" for typo variants.
    Returns recognition_only=True when the vehicle is known but has no
    deterministic pricing category — caller must not quote without confirmation.
    """
    n = _norm(text)
    for key in _SORTED_KEYS:
        if _KEY_RE[key].search(n) or n == key:
            entry = _CAT[key]
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
