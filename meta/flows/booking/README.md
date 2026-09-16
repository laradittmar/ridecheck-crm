# Meta Booking Flow — version control

The authoritative Flow definition lives on Meta and is **not** otherwise under version
control. That gap is why a defect in it can only be discussed from memory. These files close
it.

| file | meaning |
|---|---|
| `PUBLISHED_28104222025943520_v7.3.json` | **Byte-exact copy of what is PUBLISHED right now.** Never edit. Fetched read-only from the Flows API on 2026-09-11 and re-verified byte-identical the same day. |
| `CANDIDATE_v7.4-prefill.json` | **REJECTED by Meta on 2026-09-16.** Kept as forensic evidence. Never upload. |
| `CANDIDATE_v7.4-prefill-r1.json` | The current proposal: v7.4 minus the three `init-value` properties Meta disallows. Not uploaded, not published. |
| `CANDIDATE_DIFF.md` | What v7.4 changed against the published asset, and why. |

Flow id `28104222025943520` ("RideCheck Booking"), json_version 7.3, data_api_version 3.0.

`PUBLISHED_*.json` is the rollback artifact: if Gate B goes wrong, this is the definition to
restore. A test asserts it stays byte-identical, so it cannot drift silently.

**Two things learned on 2026-09-16, when Meta rejected v7.4.** First, a Flow that parses and
whose `${data.*}` references all resolve can still be invalid — Meta checks which properties
are allowed on each component type, and nothing local had been checking that.
`scripts/validate_flow_json.py` now encodes the errors Meta actually returned; run it before
staging any candidate. Local PASS is still not Meta validation, and only Ejecutar decides.

Second, and more awkward: Meta's current validator rejects
`on-select-action.name = "data_exchange"` on the date Dropdown — which is present in the
PUBLISHED asset above and is working in production today. The published Flow would not
validate if re-submitted as-is. That finding is inherited by every candidate derived from it
and is an owner decision, not something to patch away: `update_data` is a client-side
update and cannot call `handle_date_selected`, which is what loads the time slots for a
chosen date.

Neither file contains a token, a signed URL or a key — a Flow definition carries none.
