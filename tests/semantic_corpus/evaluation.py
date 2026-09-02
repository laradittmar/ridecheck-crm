"""L4.7E — non-production semantic evaluation harness.

Compares what a semantic interpreter *proposed* against the human-authored corpus truth in
`real_world_turns.jsonl`. It evaluates **meaning**, not parser-helper return values: an
interpreter is a callable that takes the raw messages of a burst and returns proposed
TurnEvidence, exactly as a future UNDERSTAND pass would.

    interpreter(messages: list[str]) -> {
        "turn_evidence": [{"field", "value", "status", "role"?}, ...],
        "canonical_state": {...},            # optional
    }

Nothing here calls OpenAI, touches the database, or imports ConversationEngine — the
harness is deliberately inert so it can be run against any interpreter version, including
historical replays (see docs/semantic/SEMANTIC_TRUTH_MODEL.md §6).

Metrics are reported **separately**; there is no single opaque score:

    field precision              proposed items that are correct
    field recall                 expected items that were proposed
    role accuracy                items with the right role (inspection location vs origin)
    unsupported-inference rate   cases violating a `must_not_infer` contract — target 0
    ambiguity handling accuracy  AMBIGUOUS/CONFLICT expectations honoured, not forced
    missing-field accuracy       fields expected to stay unknown that stayed unknown

Every metric can be sliced by provenance (REAL vs SYNTHETIC) and by equivalence group.
"""
from __future__ import annotations

import json
import pathlib
import unicodedata
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Iterable, Optional

CORPUS_PATH = pathlib.Path(__file__).with_name("real_world_turns.jsonl")

STATUS_CONFIRMED = "CONFIRMED"
STATUS_PROPOSED = "PROPOSED"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_CONFLICT = "CONFLICT"
VALID_STATUSES = {STATUS_CONFIRMED, STATUS_PROPOSED, STATUS_AMBIGUOUS, STATUS_CONFLICT}

# Statuses that assert something concrete enough to be checked as a value.
ASSERTIVE_STATUSES = {STATUS_CONFIRMED, STATUS_PROPOSED}
# Statuses that must NOT be collapsed into a canonical value.
UNRESOLVED_STATUSES = {STATUS_AMBIGUOUS, STATUS_CONFLICT}


# ── corpus loading ────────────────────────────────────────────────────────────

def load_corpus(path: pathlib.Path | str = CORPUS_PATH) -> list[dict]:
    """Read the committed corpus. Each line is one case."""
    with pathlib.Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ── value normalisation ───────────────────────────────────────────────────────

def normalize(value: Any) -> Any:
    """Comparable form of an evidence value.

    Strings are accent-folded, lowercased and whitespace-collapsed so that
    "Berazategui" == "berazategui" and "Peugeot  2008" == "peugeot 2008".
    Lists and dicts are normalised recursively; order is preserved for lists because
    order carries meaning for scheduling alternatives.
    """
    if value is None:
        return None
    if isinstance(value, str):
        folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
        return " ".join(folded.lower().split())
    if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
        return value
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in sorted(value.items())}
    return normalize(str(value))


def _items(evidence: Iterable[dict]) -> list[dict]:
    return [e for e in (evidence or []) if isinstance(e, dict) and e.get("field")]


def _by_field(evidence: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in _items(evidence):
        out.setdefault(str(item["field"]), item)
    return out


def values_match(expected: Any, produced: Any) -> bool:
    """True when a produced value means the same as the expected one.

    Scheduling alternatives are compared as ordered lists of (day, time, rank) so that a
    transplanted time or a swapped primary/fallback is a mismatch, not a near-miss.
    """
    return normalize(expected) == normalize(produced)


# ── per-case result ───────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    case_id: str
    kind: str
    groups: list[str]
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    role_expected: int = 0
    role_correct: int = 0
    unsupported_inferences: list[str] = dc_field(default_factory=list)
    ambiguity_expected: int = 0
    ambiguity_honoured: int = 0
    missing_expected: int = 0
    missing_honoured: int = 0
    notes: list[str] = dc_field(default_factory=list)

    @property
    def clean(self) -> bool:
        """No wrong item, no missing item, no unsupported inference."""
        return (
            self.false_positives == 0
            and self.false_negatives == 0
            and not self.unsupported_inferences
        )


def evaluate_case(case: dict, produced: dict) -> CaseResult:
    """Score one corpus case against one interpreter output."""
    result = CaseResult(
        case_id=case["id"],
        kind=case["provenance"]["kind"],
        groups=list(case.get("groups") or []),
    )

    expected_items = _items(case.get("expected_turn_evidence"))
    produced_items = _items(produced.get("turn_evidence"))
    produced_map = _by_field(produced_items)
    produced_canonical = produced.get("canonical_state") or {}

    matched_produced_fields: set[str] = set()

    for exp in expected_items:
        field_name = str(exp["field"])
        status = exp.get("status", STATUS_PROPOSED)
        got = produced_map.get(field_name)

        if status in UNRESOLVED_STATUSES:
            # AMBIGUOUS / CONFLICT: the interpreter must NOT force a value.
            result.ambiguity_expected += 1
            forced = (
                (got is not None and got.get("value") is not None
                 and got.get("status") in ASSERTIVE_STATUSES)
                or produced_canonical.get(field_name) not in (None, "", [], {})
            )
            if forced:
                result.notes.append(f"{field_name}: forced a value for {status}")
            else:
                result.ambiguity_honoured += 1
            if got is not None:
                matched_produced_fields.add(field_name)
            continue

        # CONFIRMED / PROPOSED: the value itself is scored.
        if got is None:
            result.false_negatives += 1
            result.notes.append(f"{field_name}: expected but not proposed")
            continue

        matched_produced_fields.add(field_name)
        if values_match(exp.get("value"), got.get("value")):
            result.true_positives += 1
        else:
            result.false_positives += 1
            result.false_negatives += 1
            result.notes.append(
                f"{field_name}: expected {exp.get('value')!r}, got {got.get('value')!r}"
            )

        if exp.get("role"):
            result.role_expected += 1
            if normalize(exp["role"]) == normalize(got.get("role")):
                result.role_correct += 1
            else:
                result.notes.append(
                    f"{field_name}: role {got.get('role')!r} != {exp['role']!r}"
                )

    # Items the interpreter invented that the corpus does not expect at all.
    for item in produced_items:
        name = str(item["field"])
        if name in matched_produced_fields:
            continue
        if item.get("value") in (None, "", [], {}):
            continue  # proposing "unknown" is not an invention
        result.false_positives += 1
        result.notes.append(f"{name}: proposed but not expected")

    # Anti-hallucination contract.
    for rule in case.get("must_not_infer") or []:
        name = str(rule.get("field"))
        forbidden = rule.get("value", "__ANY__")
        candidates = []
        if name in produced_map:
            candidates.append(produced_map[name].get("value"))
        if name in produced_canonical:
            candidates.append(produced_canonical.get(name))
        for value in candidates:
            if value in (None, "", [], {}):
                continue
            if forbidden == "__ANY__" or values_match(forbidden, value):
                result.unsupported_inferences.append(
                    f"{name}={value!r} ({rule.get('reason', 'forbidden')})"
                )
                break

    # Fields that must remain unknown after this turn.
    for name in case.get("expected_missing_fields") or []:
        result.missing_expected += 1
        produced_value = None
        if name in produced_map:
            produced_value = produced_map[name].get("value")
        if produced_value in (None, "", [], {}) and produced_canonical.get(name) in (None, "", [], {}):
            result.missing_honoured += 1
        else:
            result.notes.append(f"{name}: expected to stay unknown, got {produced_value!r}")

    return result


# ── aggregate report ──────────────────────────────────────────────────────────

@dataclass
class EvaluationReport:
    results: list[CaseResult]

    def _sum(self, attr: str, results: Optional[list[CaseResult]] = None) -> int:
        return sum(getattr(r, attr) for r in (results if results is not None else self.results))

    def metrics(self, results: Optional[list[CaseResult]] = None) -> dict[str, Any]:
        rs = self.results if results is None else results
        tp, fp, fn = self._sum("true_positives", rs), self._sum("false_positives", rs), self._sum("false_negatives", rs)
        role_exp, role_ok = self._sum("role_expected", rs), self._sum("role_correct", rs)
        amb_exp, amb_ok = self._sum("ambiguity_expected", rs), self._sum("ambiguity_honoured", rs)
        miss_exp, miss_ok = self._sum("missing_expected", rs), self._sum("missing_honoured", rs)
        offenders = [r for r in rs if r.unsupported_inferences]
        return {
            "cases": len(rs),
            "field_precision": (tp / (tp + fp)) if (tp + fp) else None,
            "field_recall": (tp / (tp + fn)) if (tp + fn) else None,
            "role_accuracy": (role_ok / role_exp) if role_exp else None,
            "unsupported_inference_rate": (len(offenders) / len(rs)) if rs else None,
            "ambiguity_handling_accuracy": (amb_ok / amb_exp) if amb_exp else None,
            "missing_field_accuracy": (miss_ok / miss_exp) if miss_exp else None,
            "clean_cases": sum(1 for r in rs if r.clean),
            "counts": {"tp": tp, "fp": fp, "fn": fn,
                       "role_expected": role_exp, "ambiguity_expected": amb_exp,
                       "missing_expected": miss_exp},
        }

    def by_kind(self) -> dict[str, dict]:
        return {
            kind: self.metrics([r for r in self.results if r.kind == kind])
            for kind in sorted({r.kind for r in self.results})
        }

    def by_group(self) -> dict[str, dict]:
        groups = sorted({g for r in self.results for g in r.groups})
        return {g: self.metrics([r for r in self.results if g in r.groups]) for g in groups}

    def offenders(self) -> list[CaseResult]:
        """Cases that invented something the corpus forbids — always inspect these."""
        return [r for r in self.results if r.unsupported_inferences]

    def render(self) -> str:
        lines = ["SEMANTIC EVALUATION", "=" * 60]
        overall = self.metrics()
        for key in ("cases", "field_precision", "field_recall", "role_accuracy",
                    "unsupported_inference_rate", "ambiguity_handling_accuracy",
                    "missing_field_accuracy", "clean_cases"):
            value = overall[key]
            lines.append(f"  {key:32} {value if value is None else (round(value, 4) if isinstance(value, float) else value)}")
        lines.append("-" * 60)
        for kind, m in self.by_kind().items():
            lines.append(f"  [{kind}] cases={m['cases']} precision={m['field_precision']} "
                         f"recall={m['field_recall']} unsupported={m['unsupported_inference_rate']}")
        for case in self.offenders():
            lines.append(f"  !! {case.case_id}: {case.unsupported_inferences}")
        return "\n".join(lines)


Interpreter = Callable[[list[str]], dict]


def evaluate(
    interpreter: Interpreter,
    corpus: Optional[list[dict]] = None,
    kinds: Optional[set[str]] = None,
    groups: Optional[set[str]] = None,
) -> EvaluationReport:
    """Run *interpreter* over the corpus and return a sliceable report.

    The interpreter never sees the expected answers, and this function never mutates the
    corpus, the database or any runtime state.
    """
    cases = corpus if corpus is not None else load_corpus()
    selected = [
        c for c in cases
        if (kinds is None or c["provenance"]["kind"] in kinds)
        and (groups is None or set(c.get("groups") or []) & groups)
    ]
    results = []
    for case in selected:
        produced = interpreter(list(case["raw"]["messages"])) or {}
        results.append(evaluate_case(case, produced))
    return EvaluationReport(results=results)


# ── replay support (offline, non-mutating) ────────────────────────────────────

def reconstruct_burst(messages: Iterable[Any]) -> list[str]:
    """Rebuild the customer side of a burst from stored inbound message rows.

    Accepts anything with `.direction`, `.text` and `.timestamp` (SQLAlchemy rows or plain
    namespaces) and returns the texts in chronological order — the same shape the corpus
    stores and the interpreter consumes. Read-only by construction: it neither writes nor
    flushes anything.
    """
    inbound = [
        m for m in messages
        if getattr(m, "direction", None) == "in" and getattr(m, "text", None)
    ]
    inbound.sort(key=lambda m: (getattr(m, "timestamp", None) or getattr(m, "created_at", None),
                                getattr(m, "id", 0)))
    return [m.text for m in inbound]
