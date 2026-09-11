# Gate B — publication plan (NOT executed)

Gate A performed **zero** Meta writes. Everything below is written down, not run.

**Whether a PUBLISHED Flow accepts a new JSON asset is UNPROVEN.** The Graph API exposes no
per-object permitted methods; `/versions` does not exist on v20.0; the assets edge holds a
single `flow.json`. The only way to find out is a write, which Gate A forbids. So both
branches are specified and the first Meta call decides which one applies.

**Gate B stops for owner review immediately before the first Meta write.**

## Shared preflight (no writes)

1. `git status` clean; `PUBLISHED_28104222025943520_v7.3.json` still sha256
   `274038ba234a49fa4f99bae47c00f6cac640e248d56a373d40ae8fba6c1afb15` (a test asserts this).
2. Re-fetch the live Flow and confirm it is still byte-identical — if it is not, someone
   edited it in Flow Builder and this plan must be re-derived.
3. Backend deployed with the Gate A contract, **outbound OFF**.
4. `scripts/preflight_deploy.sh` PASS; `scripts/verify_deployment_identity.sh` PASS.
5. Confirm the Data Exchange endpoint answers (`421` to an unauthenticated probe) and that
   the private key is mounted.

## BRANCH A — the published Flow accepts a new asset

1. Upload `CANDIDATE_v7.4-prefill.json` as the `FLOW_JSON` asset of `28104222025943520`.
2. Read `validation_errors` on the Flow object. **Any entry ⇒ stop and roll back.**
3. Confirm `json_version` reports the new version and the asset sha changed.
4. Smoke: dispatch one Flow to the authorized tester with outbound armed for that number
   only — INIT reaches the endpoint, APPOINTMENT renders with the agreed slot **selected**,
   DETAILS shows the name prefilled and the masked phone with no editable phone field.
5. Stop before final booking unless the owner authorizes completion.
6. **Rollback:** re-upload `PUBLISHED_28104222025943520_v7.3.json`. Its byte-exactness is what
   makes this possible. *Limitation:* a customer holding an already-delivered Flow message may
   still be on the new version until they reopen it — rollback is not instantaneous for
   in-flight sessions.

## BRANCH B — the published Flow is immutable

1. Create a **new Flow in DRAFT**, same categories (`APPOINTMENT_BOOKING`).
2. Upload `CANDIDATE_v7.4-prefill.json`; read `validation_errors`; stop on any entry.
3. Register the **same** Data Exchange endpoint and the **same** public key — reused, not
   regenerated. The private key stays where it is; nothing is rotated.
4. Publish the replacement and verify `status=PUBLISHED`, `validation_errors: []`.
5. **Only then** change `WHATSAPP_BOOKING_FLOW_ID` in `/opt/ridecheck-crm/.env` and the beta
   compose; redeploy with **outbound OFF**; verify deployment identity.
6. Controlled tester smoke as in Branch A.
7. **Rollback:** restore the previous `WHATSAPP_BOOKING_FLOW_ID` and redeploy. This is the
   cleaner rollback of the two — the old Flow is untouched and still published.
8. **Do not deprecate the old Flow** until the replacement has completed a real booking.

## Notes

- No token, signed URL or key appears in any artifact or command here; credentials come from
  the environment at execution time.
- A republish creates a new version — smoke-test before any Wild, not during one.
- `WHATSAPP_BOOKING_FLOW_ID` changes in Branch B only, and only after publication proof.
