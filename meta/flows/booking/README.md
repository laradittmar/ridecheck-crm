# Meta Booking Flow — version control

The authoritative Flow definition lives on Meta and is **not** otherwise under version
control. That gap is why a defect in it can only be discussed from memory. These files close
it.

| file | meaning |
|---|---|
| `PUBLISHED_28104222025943520_v7.3.json` | **Byte-exact copy of what is PUBLISHED right now.** Never edit. Fetched read-only from the Flows API on 2026-09-11 and re-verified byte-identical the same day. |
| `CANDIDATE_v7.4-prefill.json` | The proposed next version. Not uploaded, not published. |
| `CANDIDATE_DIFF.md` | Exactly what changes between the two, and why. |

Flow id `28104222025943520` ("RideCheck Booking"), json_version 7.3, data_api_version 3.0.

`PUBLISHED_*.json` is the rollback artifact: if Gate B goes wrong, this is the definition to
restore. A test asserts it stays byte-identical, so it cannot drift silently.

Neither file contains a token, a signed URL or a key — a Flow definition carries none.
