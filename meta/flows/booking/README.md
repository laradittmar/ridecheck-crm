# Meta Booking Flow — version control

The authoritative Flow definition lives on Meta and is **not** otherwise under version
control. That gap is why a defect in it can only be discussed from memory. These files close
it.

| file | meaning |
|---|---|
| `PUBLISHED_28104222025943520_v7.3.json` | **Byte-exact copy of what is PUBLISHED right now.** Never edit. Fetched read-only from the Flows API on 2026-09-11 and re-verified byte-identical the same day. |
| `CANDIDATE_v7.4-prefill.json` | **REJECTED by Meta on 2026-09-16.** Kept as forensic evidence. Never upload. |
| `CANDIDATE_v7.4-prefill-r1.json` | v7.4 minus the three `init-value` properties. **Ejecutar PASS, zero errors** (2026-09-16). Not traversable in static preview. |
| `CANDIDATE_v7.4-prefill-r2.json` | The current proposal: r1 with `is_time_enabled.__example__` set to `true` so the preview can be walked. Runtime behaviour identical to r1. Not uploaded, not published. |
| `CANDIDATE_DIFF.md` | What v7.4 changed against the published asset, and why. |

Flow id `28104222025943520` ("RideCheck Booking"), json_version 7.3, data_api_version 3.0.

`PUBLISHED_*.json` is the rollback artifact: if Gate B goes wrong, this is the definition to
restore. A test asserts it stays byte-identical, so it cannot drift silently.

**What 2026-09-16 actually established.** A Flow that parses and whose `${data.*}`
references all resolve can still be invalid — Meta checks which properties are allowed on
each component type, and nothing local had been checking that. The real defect in v7.4 was
three `init-value` properties: unsupported on `Dropdown` and on `TextInput`.

Owner Ejecutar results that day, which are the authority:

| asset | result |
|---|---|
| published v7.3 | PASS, zero errors |
| candidate v7.4 | FAIL — three `init-value` properties |
| candidate r1 | PASS, zero errors |

Meta also reported an error on the date Dropdown's `on-select-action` while those invalid
properties were present. A local rule was written from it, which then condemned the
published asset — a Flow working in production — and prompted a proposed backend-contract
milestone. The clean r1 run disproved it: r1 carries that exact action. **The action was
never the defect and no backend change is needed.** `scripts/validate_flow_json.py` no
longer contains that rule, and a test now asserts the published asset passes.

Run the validator before staging any candidate. Local PASS is still not Meta validation —
only Ejecutar decides.

Neither file contains a token, a signed URL or a key — a Flow definition carries none.
