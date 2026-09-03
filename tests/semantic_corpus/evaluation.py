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


# ── L4.7B.2B — one stance, one field ─────────────────────────────────────────
# turn-evidence/1.1 carries six acceptance signals. The harness used to flatten them to a
# boolean, so FUTURE_INTENT ("I'll come back when I've bought it") was indistinguishable
# from REJECT, and a false ACCEPT could not be counted at all. Stance is now scored on the
# signal itself.
ACCEPTANCE_SIGNALS = ("ACCEPT", "REJECT", "HESITATE", "FUTURE_INTENT",
                      "QUESTION_ONLY", "UNKNOWN")

# Legacy spellings of the same truth, accepted from any producer and canonicalised before
# scoring: an interpreter that still says `readiness=FUTURE_CONTACT_INTENDED` means the
# stance FUTURE_INTENT, and must be scored on meaning rather than wording.
_LEGACY_READINESS_AS_STANCE = {
    "FUTURE_CONTACT_INTENDED": "FUTURE_INTENT",
    "HESITANT_OR_DEFERRED": "HESITATE",
}
_LEGACY_BOOLEAN_AS_STANCE = {True: "ACCEPT", False: "REJECT"}

# `readiness` keeps only what stance cannot express: a fact about the customer's own
# purchase process. Everything else is stance.
CANONICAL_READINESS_VALUES = ("SEARCHING_NOT_READY",)

# Fields whose value is a set, not a sequence: order carries no meaning.
_SET_VALUED_FIELDS = frozenset({"faq_topics"})


def as_signal(value: Any) -> Optional[str]:
    """Canonical stance string for any accepted spelling, or None when not a stance."""
    if isinstance(value, bool):
        return _LEGACY_BOOLEAN_AS_STANCE[value]
    if isinstance(value, str):
        text = value.strip().upper()
        if text in ACCEPTANCE_SIGNALS:
            return text
        if text in _LEGACY_READINESS_AS_STANCE:
            return _LEGACY_READINESS_AS_STANCE[text]
    return None


def canonicalise_engagement(items: Iterable[dict]) -> list[dict]:
    """Collapse the two spellings of stance into one `acceptance` item.

    A `readiness` item whose value is a retired stance value becomes the stance itself,
    unless an explicit `acceptance` item is already present (which always wins). A
    `readiness` item that carries a genuine process fact is left alone.
    """
    items = list(_items(items))
    has_acceptance = any(str(i["field"]) == "acceptance" for i in items)
    out: list[dict] = []
    for item in items:
        field = str(item["field"])
        if field == "acceptance":
            signal = as_signal(item.get("value"))
            out.append({**item, "value": signal if signal else item.get("value")})
            continue
        if field == "readiness":
            signal = as_signal(item.get("value"))
            if signal is not None:
                if not has_acceptance:
                    out.append({**item, "field": "acceptance", "value": signal})
                continue          # retired spelling never survives as `readiness`
        out.append(item)
    return out


def _items(evidence: Iterable[dict]) -> list[dict]:
    return [e for e in (evidence or []) if isinstance(e, dict) and e.get("field")]


def _by_field(evidence: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in _items(evidence):
        out.setdefault(str(item["field"]), item)
    return out


def values_match(expected: Any, produced: Any, field: Optional[str] = None) -> bool:
    """True when a produced value means the same as the expected one.

    Scheduling alternatives are compared as ordered lists of (day, time, rank) so that a
    transplanted time or a swapped primary/fallback is a mismatch, not a near-miss.
    Set-valued fields (FAQ topics) are compared without order, because the order in which
    a customer asks two questions carries no meaning. Stance is compared on the canonical
    signal, so `True` and `"ACCEPT"` are the same answer (L4.7B.2B).
    """
    if field == "acceptance":
        exp_signal, got_signal = as_signal(expected), as_signal(produced)
        if exp_signal or got_signal:
            return exp_signal == got_signal
    if field in _SET_VALUED_FIELDS and isinstance(expected, list) and isinstance(produced, list):
        return sorted(normalize(expected)) == sorted(normalize(produced))
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
    # L4.7B.2B — stance scoring, distinct per signal
    stance_expected: int = 0
    stance_correct: int = 0
    false_accepts: int = 0            # produced ACCEPT where the customer did not accept
    future_intent_expected: int = 0
    future_intent_recalled: int = 0
    hesitate_expected: int = 0
    hesitate_recalled: int = 0
    notes: list[str] = dc_field(default_factory=list)

    @property
    def clean(self) -> bool:
        """No wrong item, no missing item, no unsupported inference."""
        return (
            self.false_positives == 0
            and self.false_negatives == 0
            and not self.unsupported_inferences
        )


def _score_stance(result: "CaseResult", expected_items: list[dict],
                  produced_map: dict[str, dict]) -> None:
    """Score the conversational stance on its own terms (L4.7B.2B).

    Separate from precision/recall on purpose: reading FUTURE_INTENT as ACCEPT is a
    different kind of failure from missing a locality, and the business consequence — a
    customer treated as having agreed when they did not — deserves its own number.
    """
    expected = next((i for i in expected_items if str(i["field"]) == "acceptance"), None)
    got = produced_map.get("acceptance")
    exp_signal = as_signal(expected.get("value")) if expected else None
    got_signal = as_signal(got.get("value")) if got else None

    if exp_signal is not None:
        result.stance_expected += 1
        if got_signal == exp_signal:
            result.stance_correct += 1
        else:
            result.notes.append(f"stance: expected {exp_signal}, got {got_signal}")
        if exp_signal == "FUTURE_INTENT":
            result.future_intent_expected += 1
            if got_signal == "FUTURE_INTENT":
                result.future_intent_recalled += 1
        if exp_signal == "HESITATE":
            result.hesitate_expected += 1
            if got_signal == "HESITATE":
                result.hesitate_recalled += 1

    # A false ACCEPT is claiming agreement the customer did not give — counted whenever
    # ACCEPT is produced and the corpus says the stance is anything else, including
    # "no stance at all".
    if got_signal == "ACCEPT" and exp_signal != "ACCEPT":
        result.false_accepts += 1
        result.notes.append("stance: produced ACCEPT where the customer did not accept")


def evaluate_case(case: dict, produced: dict) -> CaseResult:
    """Score one corpus case against one interpreter output."""
    result = CaseResult(
        case_id=case["id"],
        kind=case["provenance"]["kind"],
        groups=list(case.get("groups") or []),
    )

    # L4.7B.2B: both sides are canonicalised so that one truth has one spelling.
    expected_items = canonicalise_engagement(case.get("expected_turn_evidence"))
    produced_items = canonicalise_engagement(produced.get("turn_evidence"))
    produced_map = _by_field(produced_items)
    _score_stance(result, expected_items, produced_map)
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
        if values_match(exp.get("value"), got.get("value"), field=field_name):
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
            if forbidden == "__ANY__" or values_match(forbidden, value, field=name):
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
        stance_exp = self._sum("stance_expected", rs)
        fut_exp = self._sum("future_intent_expected", rs)
        hes_exp = self._sum("hesitate_expected", rs)
        return {
            "cases": len(rs),
            "field_precision": (tp / (tp + fp)) if (tp + fp) else None,
            "field_recall": (tp / (tp + fn)) if (tp + fn) else None,
            "role_accuracy": (role_ok / role_exp) if role_exp else None,
            "unsupported_inference_rate": (len(offenders) / len(rs)) if rs else None,
            "ambiguity_handling_accuracy": (amb_ok / amb_exp) if amb_exp else None,
            "missing_field_accuracy": (miss_ok / miss_exp) if miss_exp else None,
            # L4.7B.2B — stance metrics, reported separately from field precision/recall
            "stance_exact_accuracy": (self._sum("stance_correct", rs) / stance_exp) if stance_exp else None,
            "false_accept_rate": (self._sum("false_accepts", rs) / len(rs)) if rs else None,
            "future_intent_recall": (self._sum("future_intent_recalled", rs) / fut_exp) if fut_exp else None,
            "hesitate_recall": (self._sum("hesitate_recalled", rs) / hes_exp) if hes_exp else None,
            "clean_cases": sum(1 for r in rs if r.clean),
            "counts": {"tp": tp, "fp": fp, "fn": fn,
                       "role_expected": role_exp, "ambiguity_expected": amb_exp,
                       "missing_expected": miss_exp, "stance_expected": stance_exp,
                       "future_intent_expected": fut_exp, "hesitate_expected": hes_exp,
                       "false_accepts": self._sum("false_accepts", rs)},
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
                    "missing_field_accuracy", "stance_exact_accuracy",
                    "false_accept_rate", "future_intent_recall", "hesitate_recall",
                    "clean_cases"):
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
