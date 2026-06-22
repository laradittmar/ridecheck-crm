"""Deterministic vehicle catalog — Python port of the n8n Vehicle Normalizer.

lookup_vehicle(text) scans text for known vehicle aliases (longest-first, regex
word-boundary) and returns a VehicleMatch or None.  Always runs before any AI
call so tipo_vehiculo is never guessed by the model.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class VehicleMatch:
    marca: str
    modelo: str
    tipo_vehiculo: str          # canonical: AUTO | SUV_4X4_DEPORTIVO | SUV/4x4 | CLASICO | MOTO | ESCANEO_MOTOR
    confidence: str             # "high" | "medium"
    matched_alias: str
    needs_confirmation: bool = False


def _norm(s: str) -> str:
    """Same normalisation used by the n8n Vehicle Normalizer."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ── Catalog ────────────────────────────────────────────────────────────────
# Structure: norm_alias → dict with keys marca, modelo, t (tipo_vehiculo),
# and optional typo (canonical alias name when this entry is a typo variant).
# Ported verbatim from the n8n Vehicle Normalizer node.

_CAT: dict[str, dict] = {
    # ── AUTO ────────────────────────────────────────────────────────────────
    "suran":              {"marca": "Volkswagen",  "modelo": "Suran",    "t": "AUTO"},
    "siran":              {"marca": "Volkswagen",  "modelo": "Suran",    "t": "AUTO", "typo": "suran"},
    "wv suran":           {"marca": "Volkswagen",  "modelo": "Suran",    "t": "AUTO"},
    "vw suran":           {"marca": "Volkswagen",  "modelo": "Suran",    "t": "AUTO"},
    "volkswagen suran":   {"marca": "Volkswagen",  "modelo": "Suran",    "t": "AUTO"},
    "206":                {"marca": "Peugeot",     "modelo": "206",      "t": "AUTO"},
    "peugeot 206":        {"marca": "Peugeot",     "modelo": "206",      "t": "AUTO"},
    "peugeot 208":        {"marca": "Peugeot",     "modelo": "208",      "t": "AUTO"},
    "etios":              {"marca": "Toyota",      "modelo": "Etios",    "t": "AUTO"},
    "toyota etios":       {"marca": "Toyota",      "modelo": "Etios",    "t": "AUTO"},
    "polo":               {"marca": "Volkswagen",  "modelo": "Polo",     "t": "AUTO"},
    "vw polo":            {"marca": "Volkswagen",  "modelo": "Polo",     "t": "AUTO"},
    "volkswagen polo":    {"marca": "Volkswagen",  "modelo": "Polo",     "t": "AUTO"},
    "gol":                {"marca": "Volkswagen",  "modelo": "Gol",      "t": "AUTO"},
    "vw gol":             {"marca": "Volkswagen",  "modelo": "Gol",      "t": "AUTO"},
    "volkswagen gol":     {"marca": "Volkswagen",  "modelo": "Gol",      "t": "AUTO"},
    "onix":               {"marca": "Chevrolet",   "modelo": "Onix",     "t": "AUTO"},
    "chevrolet onix":     {"marca": "Chevrolet",   "modelo": "Onix",     "t": "AUTO"},
    "focus":              {"marca": "Ford",        "modelo": "Focus",    "t": "AUTO"},
    "ford focus":         {"marca": "Ford",        "modelo": "Focus",    "t": "AUTO"},
    "fit":                {"marca": "Honda",       "modelo": "Fit",      "t": "AUTO"},
    "honda fit":          {"marca": "Honda",       "modelo": "Fit",      "t": "AUTO"},
    "corolla":            {"marca": "Toyota",      "modelo": "Corolla",  "t": "AUTO"},
    "toyota corolla":     {"marca": "Toyota",      "modelo": "Corolla",  "t": "AUTO"},
    "civic":              {"marca": "Honda",       "modelo": "Civic",    "t": "AUTO"},
    "honda civic":        {"marca": "Honda",       "modelo": "Civic",    "t": "AUTO"},
    "sandero":            {"marca": "Renault",     "modelo": "Sandero",  "t": "AUTO"},
    "renault sandero":    {"marca": "Renault",     "modelo": "Sandero",  "t": "AUTO"},
    "cronos":             {"marca": "Fiat",        "modelo": "Cronos",   "t": "AUTO"},
    "fiat cronos":        {"marca": "Fiat",        "modelo": "Cronos",   "t": "AUTO"},
    "208":                {"marca": "Peugeot",     "modelo": "208",      "t": "AUTO"},
    "3008":               {"marca": "Peugeot",     "modelo": "3008",     "t": "SUV/4x4"},
    "peugeot 3008":       {"marca": "Peugeot",     "modelo": "3008",     "t": "SUV/4x4"},
    "5008":               {"marca": "Peugeot",     "modelo": "5008",     "t": "SUV/4x4"},
    "peugeot 5008":       {"marca": "Peugeot",     "modelo": "5008",     "t": "SUV/4x4"},
    # AUTO — M13.2 additions
    "fox":                {"marca": "Volkswagen",  "modelo": "Fox",      "t": "AUTO"},
    "vw fox":             {"marca": "Volkswagen",  "modelo": "Fox",      "t": "AUTO"},
    "volkswagen fox":     {"marca": "Volkswagen",  "modelo": "Fox",      "t": "AUTO"},
    "voyage":             {"marca": "Volkswagen",  "modelo": "Voyage",   "t": "AUTO"},
    "vw voyage":          {"marca": "Volkswagen",  "modelo": "Voyage",   "t": "AUTO"},
    "volkswagen voyage":  {"marca": "Volkswagen",  "modelo": "Voyage",   "t": "AUTO"},
    "virtus":             {"marca": "Volkswagen",  "modelo": "Virtus",   "t": "AUTO"},
    "vw virtus":          {"marca": "Volkswagen",  "modelo": "Virtus",   "t": "AUTO"},
    "volkswagen virtus":  {"marca": "Volkswagen",  "modelo": "Virtus",   "t": "AUTO"},
    "bora":               {"marca": "Volkswagen",  "modelo": "Bora",     "t": "AUTO"},
    "vw bora":            {"marca": "Volkswagen",  "modelo": "Bora",     "t": "AUTO"},
    "volkswagen bora":    {"marca": "Volkswagen",  "modelo": "Bora",     "t": "AUTO"},
    "corsa":              {"marca": "Chevrolet",   "modelo": "Corsa",    "t": "AUTO"},
    "chevrolet corsa":    {"marca": "Chevrolet",   "modelo": "Corsa",    "t": "AUTO"},
    "prisma":             {"marca": "Chevrolet",   "modelo": "Prisma",   "t": "AUTO"},
    "chevrolet prisma":   {"marca": "Chevrolet",   "modelo": "Prisma",   "t": "AUTO"},
    "argo":               {"marca": "Fiat",        "modelo": "Argo",     "t": "AUTO"},
    "fiat argo":          {"marca": "Fiat",        "modelo": "Argo",     "t": "AUTO"},
    "mobi":               {"marca": "Fiat",        "modelo": "Mobi",     "t": "AUTO"},
    "fiat mobi":          {"marca": "Fiat",        "modelo": "Mobi",     "t": "AUTO"},
    "clio":               {"marca": "Renault",     "modelo": "Clio",     "t": "AUTO"},
    "renault clio":       {"marca": "Renault",     "modelo": "Clio",     "t": "AUTO"},
    "march":              {"marca": "Nissan",      "modelo": "March",    "t": "AUTO"},
    "nissan march":       {"marca": "Nissan",      "modelo": "March",    "t": "AUTO"},
    "versa":              {"marca": "Nissan",      "modelo": "Versa",    "t": "AUTO"},
    "nissan versa":       {"marca": "Nissan",      "modelo": "Versa",    "t": "AUTO"},
    "ka":                 {"marca": "Ford",        "modelo": "Ka",       "t": "AUTO"},
    "ford ka":            {"marca": "Ford",        "modelo": "Ka",       "t": "AUTO"},
    "fiesta":             {"marca": "Ford",        "modelo": "Fiesta",   "t": "AUTO"},
    "ford fiesta":        {"marca": "Ford",        "modelo": "Fiesta",   "t": "AUTO"},
    "yaris":              {"marca": "Toyota",      "modelo": "Yaris",    "t": "AUTO"},
    "toyota yaris":       {"marca": "Toyota",      "modelo": "Yaris",    "t": "AUTO"},
    "camry":              {"marca": "Toyota",      "modelo": "Camry",    "t": "AUTO"},
    "toyota camry":       {"marca": "Toyota",      "modelo": "Camry",    "t": "AUTO"},
    "207":                {"marca": "Peugeot",     "modelo": "207",      "t": "AUTO"},
    "peugeot 207":        {"marca": "Peugeot",     "modelo": "207",      "t": "AUTO"},
    "308":                {"marca": "Peugeot",     "modelo": "308",      "t": "AUTO"},
    "peugeot 308":        {"marca": "Peugeot",     "modelo": "308",      "t": "AUTO"},
    "408":                {"marca": "Peugeot",     "modelo": "408",      "t": "AUTO"},
    "peugeot 408":        {"marca": "Peugeot",     "modelo": "408",      "t": "AUTO"},
    "c3":                 {"marca": "Citroën",     "modelo": "C3",       "t": "AUTO"},
    "citroen c3":         {"marca": "Citroën",     "modelo": "C3",       "t": "AUTO"},
    "c4":                 {"marca": "Citroën",     "modelo": "C4",       "t": "AUTO"},
    "citroen c4":         {"marca": "Citroën",     "modelo": "C4",       "t": "AUTO"},

    # ── SUV_4X4_DEPORTIVO ───────────────────────────────────────────────────
    "taos":               {"marca": "Volkswagen",  "modelo": "Taos",     "t": "SUV_4X4_DEPORTIVO"},
    "vw taos":            {"marca": "Volkswagen",  "modelo": "Taos",     "t": "SUV_4X4_DEPORTIVO"},
    "volkswagen taos":    {"marca": "Volkswagen",  "modelo": "Taos",     "t": "SUV_4X4_DEPORTIVO"},
    "volkswaguen taos":   {"marca": "Volkswagen",  "modelo": "Taos",     "t": "SUV_4X4_DEPORTIVO", "typo": "volkswagen taos"},
    "glb":                {"marca": "Mercedes-Benz", "modelo": "GLB",    "t": "SUV_4X4_DEPORTIVO"},
    "mercedes glb":       {"marca": "Mercedes-Benz", "modelo": "GLB",    "t": "SUV_4X4_DEPORTIVO"},
    "mercedes-benz glb":  {"marca": "Mercedes-Benz", "modelo": "GLB",    "t": "SUV_4X4_DEPORTIVO"},
    "renegade":           {"marca": "Jeep",        "modelo": "Renegade", "t": "SUV_4X4_DEPORTIVO"},
    "jeep renegade":      {"marca": "Jeep",        "modelo": "Renegade", "t": "SUV_4X4_DEPORTIVO"},
    "compass":            {"marca": "Jeep",        "modelo": "Compass",  "t": "SUV_4X4_DEPORTIVO"},
    "jeep compass":       {"marca": "Jeep",        "modelo": "Compass",  "t": "SUV_4X4_DEPORTIVO"},
    "ecosport":           {"marca": "Ford",        "modelo": "Ecosport", "t": "SUV_4X4_DEPORTIVO"},
    "ford ecosport":      {"marca": "Ford",        "modelo": "Ecosport", "t": "SUV_4X4_DEPORTIVO"},
    "hilux":              {"marca": "Toyota",      "modelo": "Hilux",    "t": "SUV_4X4_DEPORTIVO"},
    "toyota hilux":       {"marca": "Toyota",      "modelo": "Hilux",    "t": "SUV_4X4_DEPORTIVO"},
    "ranger":             {"marca": "Ford",        "modelo": "Ranger",   "t": "SUV_4X4_DEPORTIVO"},
    "ford ranger":        {"marca": "Ford",        "modelo": "Ranger",   "t": "SUV_4X4_DEPORTIVO"},
    "amarok":             {"marca": "Volkswagen",  "modelo": "Amarok",   "t": "SUV_4X4_DEPORTIVO"},
    "vw amarok":          {"marca": "Volkswagen",  "modelo": "Amarok",   "t": "SUV_4X4_DEPORTIVO"},
    "volkswagen amarok":  {"marca": "Volkswagen",  "modelo": "Amarok",   "t": "SUV_4X4_DEPORTIVO"},
    "toro":               {"marca": "Fiat",        "modelo": "Toro",     "t": "SUV_4X4_DEPORTIVO"},
    "fiat toro":          {"marca": "Fiat",        "modelo": "Toro",     "t": "SUV_4X4_DEPORTIVO"},
    "qashqai":            {"marca": "Nissan",      "modelo": "Qashqai",  "t": "SUV_4X4_DEPORTIVO"},
    "nissan qashqai":     {"marca": "Nissan",      "modelo": "Qashqai",  "t": "SUV_4X4_DEPORTIVO"},
    "tracker":            {"marca": "Chevrolet",   "modelo": "Tracker",  "t": "SUV_4X4_DEPORTIVO"},
    "chevrolet tracker":  {"marca": "Chevrolet",   "modelo": "Tracker",  "t": "SUV_4X4_DEPORTIVO"},
    "duster":             {"marca": "Renault",     "modelo": "Duster",   "t": "SUV_4X4_DEPORTIVO"},
    "renault duster":     {"marca": "Renault",     "modelo": "Duster",   "t": "SUV_4X4_DEPORTIVO"},
    "captiva":            {"marca": "Chevrolet",   "modelo": "Captiva",  "t": "SUV_4X4_DEPORTIVO"},
    "chevrolet captiva":  {"marca": "Chevrolet",   "modelo": "Captiva",  "t": "SUV_4X4_DEPORTIVO"},
    "captiva 2017":       {"marca": "Chevrolet",   "modelo": "Captiva",  "t": "SUV_4X4_DEPORTIVO"},
    # SUV_4X4_DEPORTIVO — M13.2 additions
    "spin":               {"marca": "Chevrolet",   "modelo": "Spin",     "t": "SUV_4X4_DEPORTIVO"},
    "chevrolet spin":     {"marca": "Chevrolet",   "modelo": "Spin",     "t": "SUV_4X4_DEPORTIVO"},
    "sw4":                {"marca": "Toyota",      "modelo": "SW4",      "t": "SUV_4X4_DEPORTIVO"},
    "toyota sw4":         {"marca": "Toyota",      "modelo": "SW4",      "t": "SUV_4X4_DEPORTIVO"},
    "s10":                {"marca": "Chevrolet",   "modelo": "S10",      "t": "SUV_4X4_DEPORTIVO"},
    "chevrolet s10":      {"marca": "Chevrolet",   "modelo": "S10",      "t": "SUV_4X4_DEPORTIVO"},
    "frontier":           {"marca": "Nissan",      "modelo": "Frontier", "t": "SUV_4X4_DEPORTIVO"},
    "nissan frontier":    {"marca": "Nissan",      "modelo": "Frontier", "t": "SUV_4X4_DEPORTIVO"},
    "kangoo":             {"marca": "Renault",     "modelo": "Kangoo",   "t": "SUV_4X4_DEPORTIVO"},
    "renault kangoo":     {"marca": "Renault",     "modelo": "Kangoo",   "t": "SUV_4X4_DEPORTIVO"},
    "partner":            {"marca": "Peugeot",     "modelo": "Partner",  "t": "SUV_4X4_DEPORTIVO"},
    "peugeot partner":    {"marca": "Peugeot",     "modelo": "Partner",  "t": "SUV_4X4_DEPORTIVO"},
    "berlingo":           {"marca": "Citroën",     "modelo": "Berlingo", "t": "SUV_4X4_DEPORTIVO"},
    "citroen berlingo":   {"marca": "Citroën",     "modelo": "Berlingo", "t": "SUV_4X4_DEPORTIVO"},
    "saveiro":            {"marca": "Volkswagen",  "modelo": "Saveiro",  "t": "SUV_4X4_DEPORTIVO"},
    "vw saveiro":         {"marca": "Volkswagen",  "modelo": "Saveiro",  "t": "SUV_4X4_DEPORTIVO"},
    "volkswagen saveiro": {"marca": "Volkswagen",  "modelo": "Saveiro",  "t": "SUV_4X4_DEPORTIVO"},
    "tiguan":             {"marca": "Volkswagen",  "modelo": "Tiguan",   "t": "SUV_4X4_DEPORTIVO"},
    "vw tiguan":          {"marca": "Volkswagen",  "modelo": "Tiguan",   "t": "SUV_4X4_DEPORTIVO"},
    "volkswagen tiguan":  {"marca": "Volkswagen",  "modelo": "Tiguan",   "t": "SUV_4X4_DEPORTIVO"},
    "oroch":              {"marca": "Renault",     "modelo": "Oroch",    "t": "SUV_4X4_DEPORTIVO"},
    "renault oroch":      {"marca": "Renault",     "modelo": "Oroch",    "t": "SUV_4X4_DEPORTIVO"},
    "hrv":                {"marca": "Honda",       "modelo": "HR-V",     "t": "SUV_4X4_DEPORTIVO"},
    "hr v":               {"marca": "Honda",       "modelo": "HR-V",     "t": "SUV_4X4_DEPORTIVO"},
    "honda hrv":          {"marca": "Honda",       "modelo": "HR-V",     "t": "SUV_4X4_DEPORTIVO"},
    "honda hr v":         {"marca": "Honda",       "modelo": "HR-V",     "t": "SUV_4X4_DEPORTIVO"},
    "crv":                {"marca": "Honda",       "modelo": "CR-V",     "t": "SUV_4X4_DEPORTIVO"},
    "cr v":               {"marca": "Honda",       "modelo": "CR-V",     "t": "SUV_4X4_DEPORTIVO"},
    "honda crv":          {"marca": "Honda",       "modelo": "CR-V",     "t": "SUV_4X4_DEPORTIVO"},
    "honda cr v":         {"marca": "Honda",       "modelo": "CR-V",     "t": "SUV_4X4_DEPORTIVO"},
    "kicks":              {"marca": "Nissan",      "modelo": "Kicks",    "t": "SUV_4X4_DEPORTIVO"},
    "nissan kicks":       {"marca": "Nissan",      "modelo": "Kicks",    "t": "SUV_4X4_DEPORTIVO"},
}

# Sort aliases longest-first so multi-word aliases ("chevrolet captiva")
# match before single-word aliases ("captiva").
_SORTED_KEYS: list[str] = sorted(_CAT.keys(), key=len, reverse=True)

# Pre-compiled regexes for each alias (word-boundary matching)
_KEY_RE: dict[str, re.Pattern] = {
    k: re.compile(r"(?:^|\s)" + re.escape(k) + r"(?:\s|$)")
    for k in _SORTED_KEYS
}


def lookup_vehicle(text: str) -> VehicleMatch | None:
    """Scan *text* for a known vehicle alias and return a VehicleMatch or None.

    Checks aliases longest-first so brand+model beats bare model name.
    Returns confidence="high" for direct matches, "medium" for typo variants.
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
            )
    return None
