#!/usr/bin/env python3
"""Meta Flow JSON component-schema checks — the ones Ejecutar actually enforces.

Gate B preparation reported the v7.4 prefill candidate as contract-PASS on the strength
of two checks: the file parsed, and every `${data.*}` reference resolved against its
screen's declared data. Meta then rejected it with four component-schema errors. Neither
check could ever have caught them — a property can be perfectly well-formed JSON,
reference perfectly declared data, and still be disallowed on that component type.

This validator encodes what Meta told us, plus one precedent rule. It is deliberately
NOT a reimplementation of Meta's schema: nothing here is invented. Rules come only from

  * the four authoritative errors returned by Ejecutar on 2026-09-16, and
  * the live PUBLISHED asset, which is the only Flow JSON we hold that Meta has
    demonstrably accepted.

`--precedent` compares component properties against that published asset and reports
anything it has never accepted as UNPROVEN — a warning, not a verdict. Only Meta can
turn UNPROVEN into supported.

Local PASS is necessary and never sufficient. Meta Ejecutar remains the authority.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ── Authoritative rules, each traceable to a Meta error on 2026-09-16 ─────────
# 1/2. "Property 'init-value' is not allowed in 'Dropdown' component."
# 4.   "Property 'init-value' is not allowed in 'TextInput' component."
FORBIDDEN_PROPERTIES = {
    "Dropdown": {"init-value"},
    "TextInput": {"init-value"},
}
# 3.   "Invalid value found for property 'name'. Expected 'update_data'."
#      at screens[0].layout.children[3].children[0]['on-select-action'].name
REQUIRED_ACTION_NAME = {
    ("Dropdown", "on-select-action"): "update_data",
}


def walk(node, path="", out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        if "type" in node:
            out.append((path, node))
        for k, v in node.items():
            walk(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, f"{path}[{i}]", out)
    return out


def check(doc, precedent=None):
    findings = []
    for path, comp in walk(doc):
        ctype = comp.get("type")
        if not isinstance(ctype, str):
            continue
        for prop in sorted(FORBIDDEN_PROPERTIES.get(ctype, ())):
            if prop in comp:
                findings.append(("ERROR", f"{path}.{prop}",
                                 f"Property '{prop}' is not allowed in '{ctype}' component"))
        for (t, action), expected in REQUIRED_ACTION_NAME.items():
            if ctype == t and isinstance(comp.get(action), dict):
                actual = comp[action].get("name")
                if actual != expected:
                    findings.append(("ERROR", f"{path}['{action}'].name",
                                     f"Invalid value found for property 'name'. "
                                     f"Expected '{expected}', found {actual!r}"))
    if precedent is not None:
        seen = {}
        for _, comp in walk(precedent):
            t = comp.get("type")
            if isinstance(t, str):
                seen.setdefault(t, set()).update(comp.keys())
        for path, comp in walk(doc):
            t = comp.get("type")
            if not isinstance(t, str):
                continue
            if t not in seen:
                findings.append(("UNPROVEN", path,
                                 f"component type '{t}' does not appear in the published asset"))
                continue
            for prop in sorted(set(comp) - seen[t]):
                findings.append(("UNPROVEN", f"{path}.{prop}",
                                 f"property '{prop}' never seen on '{t}' in the published asset"))
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("flow_json")
    ap.add_argument("--precedent", help="a Meta-accepted asset to compare component properties against")
    ap.add_argument("--warn-only", action="store_true", help="exit 0 even with UNPROVEN findings")
    a = ap.parse_args()

    doc = json.loads(Path(a.flow_json).read_text(encoding="utf-8"))
    prec = json.loads(Path(a.precedent).read_text(encoding="utf-8")) if a.precedent else None
    findings = check(doc, prec)

    errors = [f for f in findings if f[0] == "ERROR"]
    unproven = [f for f in findings if f[0] == "UNPROVEN"]
    for level, where, msg in findings:
        print(f"{level:9} {where}\n          {msg}")
    print(f"\n{len(errors)} error(s), {len(unproven)} unproven")
    print("NOTE: local PASS is not Meta validation. Only Ejecutar can confirm the asset.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
