"""Regression accounting that counts what pytest's summary identities do not.

L4.7W5-GATE3-G3-7F-R2, review finding G37FRR-09.

The G3-7F closeout certified "no change" by diffing the `FAILED`/`ERROR` lines of pytest's
short summary and finding 67 identities at both revisions. Both numbers were right and the
conclusion was wrong: the run reported `59 failed, 9 errors` against those 67 identities, and
58 + 9 = 67 while 59 + 9 = 68. One failure was a `SUBFAIL` — a `unittest.subTest` case —
which pytest reports on its own `SUBFAILED(...)` line AND still prints a `PASSED` line for
the enclosing test. A method that reads only `FAILED` and `ERROR` is structurally blind to
it, and a green `PASSED` line sat there to confirm the mistake.

So this module does two things the old method could not:

  1. counts FAILED, ERROR, SUBFAIL and collection errors as separate identity sets;
  2. RECONCILES them arithmetically against pytest's own totals, and refuses to certify a
     run whose numbers do not add up.

`failed + errors == |FAILED| + |ERROR| + |SUBFAIL|` is the invariant. A run that breaks it is
hiding something, and `reconciles` returns False rather than guessing which side is right.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: `59 failed, 5570 passed, 76 skipped, 1452 warnings, 9 errors, 3606 subtests passed in ...`
_TOTALS = re.compile(r"(\d+)\s+(failed|passed|skipped|errors|error|"
                     r"warnings|subtests passed|deselected|xfailed|xpassed)")
#: The identity is everything up to pytest's " - <reason>" separator, NOT the first
#: whitespace-delimited token: this repository has a test directory with a space in its name
#: (`tests/Whatsapp Test/Messages/`), and a `(\S+)` capture truncated all four of its
#: failures to one shared prefix, silently merging them into a single identity. That is the
#: same class of undercount this module exists to catch.
_SUMMARY_LINE = re.compile(
    r"^(FAILED|ERROR|SUBFAILED)\b(\([^)]*\))?\s+(.+?)(?:\s+-\s.*)?$")
_COLLECT_ERROR = re.compile(r"errors? during collection|ERROR collecting ")


@dataclass
class RegressionAccount:
    """Every identity a run produced, plus the totals pytest claimed."""
    failed: tuple = ()
    errors: tuple = ()
    subfailed: tuple = ()
    totals: dict = field(default_factory=dict)
    collection_errors: int = 0

    @property
    def identities(self) -> int:
        return len(self.failed) + len(self.errors) + len(self.subfailed)

    @property
    def claimed(self) -> int:
        """What pytest's own totals say went wrong. `failed` already includes subtests."""
        return int(self.totals.get("failed", 0)) + int(self.totals.get("errors", 0))

    @property
    def reconciles(self) -> bool:
        return self.claimed == self.identities

    @property
    def clean(self) -> bool:
        return self.identities == 0 and self.collection_errors == 0 and self.reconciles

    def explain(self) -> str:
        return (f"claimed={self.claimed} (failed={self.totals.get('failed', 0)} + "
                f"errors={self.totals.get('errors', 0)})  "
                f"identities={self.identities} (FAILED={len(self.failed)} "
                f"ERROR={len(self.errors)} SUBFAIL={len(self.subfailed)})  "
                f"collection_errors={self.collection_errors}  "
                f"reconciles={self.reconciles}")


def parse(output: str) -> RegressionAccount:
    """Read a pytest run. Only the short-summary section contributes identities.

    The body of a failure report also contains the word FAILED, so identities are taken from
    the summary section alone; the totals line is read wherever it appears.
    """
    lines = output.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if "short test summary info" in line:
            start = i + 1
    failed, errors, subfailed = set(), set(), set()
    for line in lines[start:]:
        m = _SUMMARY_LINE.match(line.strip())
        if not m:
            continue
        kind, case, ident = m.group(1), (m.group(2) or ""), m.group(3).strip()
        if kind == "FAILED":
            failed.add(ident)
        elif kind == "ERROR":
            errors.add(ident)
        else:
            # one test may fail several subtest cases; the case label is part of the identity
            subfailed.add(f"{ident}{case}")
    totals: dict = {}
    for line in reversed(lines):
        if " in " in line and ("passed" in line or "failed" in line or "error" in line):
            for count, label in _TOTALS.findall(line):
                key = "errors" if label in ("error", "errors") else label
                totals[key] = int(count)
            if totals:
                break
    return RegressionAccount(
        failed=tuple(sorted(failed)), errors=tuple(sorted(errors)),
        subfailed=tuple(sorted(subfailed)), totals=totals,
        collection_errors=sum(1 for l in lines if _COLLECT_ERROR.search(l)))


def compare(before: RegressionAccount, after: RegressionAccount) -> dict:
    """Added and removed identities, per kind. Counts are never compared on their own."""
    out = {}
    for kind in ("failed", "errors", "subfailed"):
        a, b = set(getattr(before, kind)), set(getattr(after, kind))
        out[f"added_{kind}"] = tuple(sorted(b - a))
        out[f"removed_{kind}"] = tuple(sorted(a - b))
    return out
