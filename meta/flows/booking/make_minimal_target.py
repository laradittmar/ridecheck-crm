#!/usr/bin/env python3
"""SUPERSEDED AND NEUTRALISED — this script no longer generates anything.

WILD1-R3-C1, 2026-11-01. Until today this file was a working generator that derived
`TARGET_v7.6-minimal-refusal.json` from the published v7.3 artifact. It was an active
hazard for two independent reasons, and it exited 0 while being both:

  1. It was derived from the WRONG BASELINE. The owner's 2026-10-30 Meta export proved the
     live Flow is r3, not v7.3. Publishing v7.6 would have removed five APPOINTMENT fields
     and three DETAILS fields, re-added a required `Teléfono` input and reverted two action
     payloads — a regression of a published, handset-certified feature.

  2. Its routing graph is META-INVALID. It wrote two backward edges into `routing_model`
     (DETAILS to APPOINTMENT, and SUMMARY to APPOINTMENT), which makes the graph cyclic and
     leaves no entry screen. Meta's own validator rejected exactly that shape in an
     owner-present run on 2026-10-30:

       "Backward route [DETAILS->APPOINTMENT] corresponding to forward route
        [APPOINTMENT->DETAILS] is not allowed in the routing model. Only forward routes
        can be specified."
       "No entry screen found in the routing model. Expected a screen with no inbound
        edges as the entry screen."

     `routing_model` is a forward-only DAG with exactly one entry screen. Those edges
     cannot exist in any artifact, so `TARGET_v7.6-minimal-refusal.json` (canonical
     `501d4aab43502730a0b176f3fb9cc37173f85e18ed2ecf265ace39795c6745dc`) is inadmissible
     on routing grounds alone, quite apart from the baseline.

THE AUTHORITATIVE ARTIFACT AND GENERATOR

    artifact   meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json
    raw        4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d
    canonical  dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d
    generator  meta/flows/booking/make_r3_refusal_target.py

That generator derives the same four fields and two components from r3, leaves
`routing_model` untouched, and calls the canonical forward-routing validator in
`backend/app/services/flow_contract.py` before it writes. Use it.

WHY THE BODY IS GONE RATHER THAN COMMENTED OUT

Git history preserves the original generator in full: blob
`8b93343d835d099819f8d161c86172ea00bcad80`, last written at commit `c26f980`. Recover it
with `git show c26f980:meta/flows/booking/make_minimal_target.py` if the historical record
is ever needed.

**The working tree must not reproduce it.** A commented-out or flag-guarded body is still a
body — it can be uncommented, copied, or run by a future reader who skims the header. The
executable behaviour is therefore removed outright, and `TARGET_v7.6-minimal-refusal.json`
is retained beside it as historical evidence only, never as a target or a rollback point.

This script writes no file, emits no JSON, and exits 2 for every invocation, with or
without arguments, whatever output path is supplied.
"""
import sys

MESSAGE = """STOP: TARGET_v7.6 is superseded and Meta-invalid.

  Its routing graph is cyclic and backward — it declares DETAILS to APPOINTMENT and
  SUMMARY to APPOINTMENT — and Meta rejected that shape on 2026-10-30: only forward
  routes are allowed, and the cycle leaves no entry screen. It was also derived from the
  wrong baseline (v7.3, not the live r3).

  Nothing was generated and no file was written.

  Use instead:
    meta/flows/booking/make_r3_refusal_target.py

  The authoritative artifact is:
    meta/flows/booking/TARGET_v7.7-r3-prefill-refusal.json
    raw sha256        4ce27122a55f3efd4fbae117d620abf34024c5a86343bb23ce878182ba2bbd9d
    canonical sha256  dccd3bc973b1b51526c5064027b5d8f61ee885293dfa9815a272609092aca17d

  The original generator is preserved in Git history (blob
  8b93343d835d099819f8d161c86172ea00bcad80, commit c26f980); the working tree must not
  reproduce it.
"""

if __name__ == "__main__":
    sys.stderr.write(MESSAGE)
    raise SystemExit(2)

# Imported rather than run: fail just as loudly, so no caller can obtain a generator here.
raise SystemExit(2)
