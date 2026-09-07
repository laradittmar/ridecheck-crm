PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: OPS-CRM-500-REMEDIATION

# The CRM is back, and the way it broke can no longer happen quietly

crm_test only · production DB untouched · no credential rotated · no schema change
n8n workflow, WhatsApp routing, authority flags and Booking Flow all untouched

---

## 1. Verdict

**PASS.** `https://crm.ridecheck.ar/login` returns 200, login succeeds, all four protected
routes open, and an unconfigured server now answers 503 with an explanation instead of a
stack trace. Two things went wrong during the remediation itself; both are recorded below
rather than smoothed over, because each is the same failure mode as the incident.

## 2. What was configured

`/opt/ridecheck-crm/.env` (backed up first, now mode `600`, git-ignored):

| variable | action |
|---|---|
| `AUTH_SECRET_KEY` | **new**, generated, 64 chars |
| `ADMIN_PASSWORD` | **new**, generated, 24 chars |
| `POSTGRES_PASSWORD` | **existing value preserved** — not rotated |
| `WHATSAPP_VERIFY_TOKEN` | **existing value preserved** — not rotated |
| `INTERNAL_API_AUTH_ENABLED` / `INTERNAL_API_TRUSTED_CIDR` | added — see §5 |

Neither new value is printed anywhere: not in this document, not in the commit, not in any
log. The removed literals `admin123` and `dev-only-change-me` were **not** reused, and a
test proves the old default secret cannot sign a valid session.

**To read the admin password**, on the server:
`! grep '^ADMIN_PASSWORD=' /opt/ridecheck-crm/.env`

## 3. The code fix

`_render_login` now catches `AuthConfigurationError` and returns an explicit **503** page
naming `AUTH_SECRET_KEY`, and the POST handler uses the same response. Fail-closed behaviour
is unchanged — no default secret is restored and authentication is not made permissive. The
only thing that changed is that an unconfigured server now *says so*.

The original defect was narrow and easy to miss: the login page **signs its own CAPTCHA
token** with the session key, so making that key mandatory broke *rendering*, not just
authenticating. I had guarded `verify_session` and the POST path and never considered that
drawing the page needs a signature.

## 4. Two failures during this remediation, both worth reporting

**(a) `.env` presence is not container environment.** After configuring both values,
`/login` was still 503: compose only injects variables a service *declares*, and the beta
override declared neither. My first preflight validated `.env` and passed — while the
container started without the values. Fixed by declaring `AUTH_SECRET_KEY`, `ADMIN_PASSWORD`
and `ADMIN_EMAIL` in the override, and by extending the preflight to check the compose
wiring, not just the file.

**(b) I disabled the F4 internal-API boundary.** Deploying with `set -a && . ./.env` dropped
`INTERNAL_API_AUTH_ENABLED`, which had only ever lived in a shell variable I typed by hand
on each previous deploy. External `POST /api/conversation/handle` briefly returned 422
instead of 401. Caught by the Phase-6 regression check in this milestone, fixed by moving
both settings into `.env`, and the preflight now requires them.

Both are the incident's own lesson repeating: **configuration that exists only in someone's
memory is not configuration.**

## 5. The deployment gate

`scripts/preflight_deploy.sh` is the mechanism a sentence in a closeout was not:

```
./scripts/preflight_deploy.sh /opt/ridecheck-crm/.env && docker compose … up -d backend
```

It checks eight variables for presence, verifies the compose file actually declares the auth
ones, names anything missing, **never reads or prints a value**, and exits non-zero so the
command after the `&&` never runs. Proven in both directions — it passed for the real `.env`
and stopped a deliberately incomplete one, naming `ADMIN_PASSWORD` without echoing the key
that was present.

It was used for every deploy in this milestone.

## 6. Verification

**Operator UI** — end-to-end, real captcha, real credentials read from `.env` at call time:

```
GET /login              200      captcha question + token present
POST /login  correct    303      session issued
  /kanban   200    /table  200    /calendar  200    /control  200
POST /login  admin123   401      the removed default is rejected
missing AUTH_SECRET_KEY 503      explicit configuration page, not a 500
```

**WhatsApp / CE — no customer message was sent:**

```
n8n -> /api/conversation/handle      422  (auth passed, empty body)
external -> /api/conversation/handle 401  blocked
external -> /api/whatsapp/.../send-text 401 blocked
Meta webhook (public)                403  reachable, wrong test token
Booking Flow data-exchange           421  alive
OUTBOUND_ENABLED=false · V/L/A/S/C4A authority flags all true
```

Regression: **3 640 passed / 57 failed / 9 errors**, failure set identical to the W2-F1
baseline, **0 new**. `tests/test_ops_crm_500_remediation.py` 7/7.

Runtime `ridecheck-crm-backend:ops500-loginfix-afdb8b1`, restarts 0.

## 7. Migration drift — audited, not touched

I got this wrong on first inspection and corrected it. The columns from the two unrecorded
revisions **all exist**:

| | |
|---|---|
| schema data state | **complete** — `pending_location_proposal`, `meta_http_status` and `meta_error_payload` all present in crm_test |
| alembic recorded head | `20260831_wild01_dedup_causal_inbound` |
| repo head | `20260906_pending_location_proposal` |
| unrecorded revisions | `20260901_l4_1`, `20260906_pending_location_proposal` |
| production `crm` head | `20260624_group_default_viaticos` (separate, older, untouched) |

My audit note yesterday implied a column might be missing; it isn't. `meta_error_code` is an
in-memory attribute on a Python exception, never a database column — my probe checked a name
that never existed.

So the drift is **bookkeeping only**: the schema is right, `alembic_version` does not know
it. Cause: W2-F1 applied its column with a direct `ALTER TABLE … IF NOT EXISTS` rather than
through Alembic.

**Safe remediation, not executed here:** `alembic stamp head` against crm_test records the
two revisions without altering any schema — valid precisely because every column they add is
verified present. It should be a milestone of its own, and production's own head should be
reviewed separately before anything is stamped there.

## 8. State

`OUTBOUND_ENABLED=false`. The tester thread from W2 (thread 2039, candidate 131, lead 125)
is untouched. Production database untouched. No credential rotated. No schema changed.

READY FOR NEXT OWNER WILD: **YES** — outbound is off and would need re-enabling for the
authorized tester as in W2 preparation.
