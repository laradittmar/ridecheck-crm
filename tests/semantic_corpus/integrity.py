"""L4.7B.2B — corpus integrity audit.

The corpus is the ruler. A ruler with a bent edge produces confident, wrong numbers, so
this module checks the corpus against itself and against the `turn-evidence/1.1` schema:

* every expected value must be *emittable* — no sentinel that no interpreter can produce;
* evidence written in a fixture's own raw text must not be missing from its expectation;
* an expectation must not assert what the raw text does not contain;
* one truth must have one field — stance lives in `acceptance`, never twice;
* the `must_not_infer` contract must not contradict the expectation it accompanies.

Read-only: it imports no interpreter, calls no model, and never rewrites the corpus.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .evaluation import (  # type: ignore[import-not-found]
    ACCEPTANCE_SIGNALS,
    CANONICAL_READINESS_VALUES,
    as_signal,
    load_corpus,
    normalize,
)

# Vocabularies the schema can actually emit (turn-evidence/1.1 + interpreter contract).
FAQ_TOPICS = ("service_scope", "report", "presence", "payment", "business_hours",
              "duration", "coverage")
DAY_EXPRESSIONS = ("TODAY", "TOMORROW", "DAY_AFTER_TOMORROW", "MONDAY", "TUESDAY",
                   "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY",
                   "EXPLICIT_DATE")
SERVICE_INTENT_VALUES = ("PREPURCHASE_INSPECTION",)
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-9]\d)\b")


def _fold(text: str) -> str:
    return normalize(text or "")


def _fields(case: dict) -> dict[str, dict]:
    return {str(e["field"]): e for e in case.get("expected_turn_evidence") or []}


def _raw(case: dict) -> str:
    return " ".join(case.get("raw", {}).get("messages") or [])


def _known_values(corpus: Iterable[dict], field: str) -> set[str]:
    """Every value the corpus itself uses for a field — its own working vocabulary."""
    out: set[str] = set()
    for case in corpus:
        for item in case.get("expected_turn_evidence") or []:
            if str(item["field"]) == field and isinstance(item.get("value"), str):
                out.add(item["value"])
    return out


def audit_corpus(corpus: list[dict] | None = None) -> list[dict]:
    """Return one finding per defect. Empty list means the instrument is consistent."""
    cases = corpus if corpus is not None else load_corpus()
    vehicles = _known_values(cases, "vehicle")
    localities = _known_values(cases, "inspection_location") | _known_values(cases, "customer_origin")
    findings: list[dict] = []

    def flag(case: dict, kind: str, detail: str) -> None:
        findings.append({"case_id": case["id"], "kind": kind, "detail": detail,
                         "provenance": case["provenance"]["kind"]})

    for case in cases:
        fields = _fields(case)
        raw = _raw(case)
        folded = _fold(raw)

        # ── emittable vocabulary ────────────────────────────────────────────
        topics = fields.get("faq_topics", {}).get("value")
        if isinstance(topics, list):
            for topic in topics:
                if topic not in FAQ_TOPICS:
                    flag(case, "UNEMITTABLE_SENTINEL",
                         f"faq_topics contains {topic!r}, absent from the FAQ vocabulary")
        stance = fields.get("acceptance")
        if stance is not None:
            if isinstance(stance.get("value"), bool):
                flag(case, "STALE_1_0_ASSUMPTION",
                     "acceptance is a boolean; turn-evidence/1.1 carries a signal")
            elif as_signal(stance.get("value")) not in ACCEPTANCE_SIGNALS:
                flag(case, "UNEMITTABLE_SENTINEL",
                     f"acceptance {stance.get('value')!r} is not an AcceptanceSignal")
        readiness = fields.get("readiness")
        if readiness is not None and readiness.get("value") not in CANONICAL_READINESS_VALUES:
            flag(case, "DUPLICATE_TRUTH",
                 f"readiness {readiness.get('value')!r} restates a stance; "
                 "stance belongs to `acceptance`")
        if readiness is not None and stance is not None and as_signal(readiness.get("value")):
            flag(case, "DUPLICATE_TRUTH", "stance expressed in both readiness and acceptance")
        intent = fields.get("service_intent")
        if intent is not None and intent.get("value") not in SERVICE_INTENT_VALUES:
            flag(case, "UNEMITTABLE_SENTINEL",
                 f"service_intent {intent.get('value')!r} outside the vocabulary")
        for request in (fields.get("scheduling_preference", {}).get("value") or []):
            if isinstance(request, dict) and request.get("day") not in (None, *DAY_EXPRESSIONS):
                flag(case, "UNEMITTABLE_SENTINEL",
                     f"scheduling day {request.get('day')!r} outside the vocabulary")

        # ── evidence present in raw but absent from the expectation ─────────
        if "vehicle" not in fields and "vehicle" not in (case.get("expected_missing_fields") or []):
            for vehicle in vehicles:
                model = vehicle.split()[-1]
                if len(model) > 3 and re.search(rf"\b{re.escape(_fold(model))}\b", folded):
                    flag(case, "RAW_EVIDENCE_OMITTED",
                         f"raw names a vehicle ({model}) with no vehicle expectation")
                    break
        if "vehicle" in fields and fields["vehicle"].get("value") and "vehicle_year" not in fields:
            years = YEAR_RE.findall(raw)
            if years and "vehicle_year" not in (case.get("expected_missing_fields") or []):
                flag(case, "RAW_EVIDENCE_OMITTED",
                     f"raw states a year ({years[0]}) with no vehicle_year expectation")
        if not {"inspection_location", "customer_origin"} & set(fields):
            missing = set(case.get("expected_missing_fields") or [])
            if "inspection_location" not in missing:
                for locality in localities:
                    if re.search(rf"\b{re.escape(_fold(locality))}\b", folded):
                        flag(case, "RAW_EVIDENCE_OMITTED",
                             f"raw names a locality ({locality}) with no location expectation")
                        break

        # ── expectation asserting what the raw text does not contain ────────
        for name in ("inspection_location", "customer_origin", "vehicle"):
            item = fields.get(name)
            value = item.get("value") if item else None
            if not isinstance(value, str) or item.get("status") in ("AMBIGUOUS", "CONFLICT"):
                continue
            # Catalog-canonical values legitimately add the make to a model-only mention
            # ("un fox" -> "Volkswagen Fox"), so a match on any part is enough.
            tokens = [t for t in _fold(value).split() if len(t) >= 3]
            if tokens and not any(t in folded for t in tokens):
                flag(case, "EXPECTATION_NOT_IN_RAW",
                     f"{name}={value!r} does not appear in the raw text")

        # ── internal contradictions ─────────────────────────────────────────
        missing = set(case.get("expected_missing_fields") or [])
        for name in missing & set(fields):
            if fields[name].get("value") not in (None, "", [], {}):
                flag(case, "IMPOSSIBLE_COMBINATION",
                     f"{name} is expected as a value and as a missing field")
        for rule in case.get("must_not_infer") or []:
            name = str(rule.get("field"))
            if name not in fields:
                continue
            forbidden = rule.get("value", "__ANY__")
            value = fields[name].get("value")
            if value in (None, "", [], {}):
                continue
            if forbidden == "__ANY__" or normalize(forbidden) == normalize(value):
                flag(case, "IMPOSSIBLE_COMBINATION",
                     f"must_not_infer forbids {name}={forbidden!r} while it is expected")

    return findings


def render(findings: list[dict]) -> str:
    if not findings:
        return "CORPUS INTEGRITY: clean (0 findings)"
    lines = [f"CORPUS INTEGRITY: {len(findings)} finding(s)"]
    for f in findings:
        lines.append(f"  [{f['kind']}] {f['case_id']} ({f['provenance']}): {f['detail']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(audit_corpus()))
