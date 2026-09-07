PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: OPS-CRM-500-FORENSIC

# The CRM cannot render its own login page

Read-only audit · no code changed · no container restarted · no env changed · no DB write
Audit window 2026-09-07T13:11Z – 13:20Z

---

## 1. What is broken, and it is mine

`GET /login` raises before it renders. The proven chain, from a fresh request:

```
main.py:334  login_page
main.py:192  _render_login          question, token = _new_captcha()
main.py:121  _new_captcha           token = sign_session({...})
auth.py:93   sign_session           sig = hmac.new(_secret()...)
auth.py:76   _secret                raise AuthConfigurationError
app.auth.AuthConfigurationError: Session signing key is not configured.
                                  Set AUTH_SECRET_KEY (or SECRET_KEY).
```

**The CAPTCHA token is signed with the session key.** When I made `_secret()` fail closed in
SEC-PRELAUNCH-SOURCE-HARDENING (finding SEC-04), I guarded `verify_session` and the **POST**
login handler — `main.py:371` is the only `AuthConfigurationError` catch in the file — and I
did not anticipate that *rendering* the page also needs a signature. So the login page cannot
be drawn at all.

## 2. The first failure is a deployment, not a config drift

Nothing about the configuration changed. `AUTH_SECRET_KEY` and `SECRET_KEY` have been unset
throughout; that was true before the hardening and is true now. What changed is that code
requiring them was **deployed**.

The SEC closeout said, in its own words, *"Do NOT deploy this change yet because the live
environment currently relies on the fallback"*, and marked deployment
`BLOCKED_PENDING_OWNER_CREDENTIAL_CONFIGURATION`. I then built and deployed three images
from HEAD — which contained the hardening — without re-reading that blocker:

```
l4.7c4a-livesem-4ec8c43     fallback present   /login OK      ← last working runtime
l4.7w1f2-fuzzygov-e142a50   HARDENED           /login 500     ← breakage starts here
l4.7w1f3-authority-eeec87d  HARDENED           /login 500
l4.7w1f4-prewild-23fbe02    HARDENED           /login 500
l4.7w2f1-voiceloc-3f18f63   HARDENED           /login 500     ← currently running
```

SEC commit `3252226` and F2 commit `e142a50` are both dated **2026-09-04**; the F2 compose
pin (`801d5ef`) put the first hardened image into crm_test. **The CRM UI has been down since
2026-09-04 — three milestones and three deploys — and none of us noticed**, because every
verification I ran afterwards checked `/docs`, `/api/*` and the conversation path, never the
operator UI.

## 3. Two defects, not one

**Primary — DEPLOYMENT_MISMATCH.** Code explicitly marked not-deployable was deployed. With
the intended discipline the 500 never occurs.

**Secondary — APPLICATION_EXCEPTION (also mine).** Even deployed correctly, an unconfigured
server should render a clear "authentication is not configured" page, not a stack trace. My
guard covered POST and missed GET. This one is real regardless of the deployment error.

**Proximate condition — CONFIG_MISSING.** `AUTH_SECRET_KEY` / `SECRET_KEY` unset. Unchanged
for the life of the system; only newly *mandatory*.

## 4. Config presence (values never read)

| variable | state |
|---|---|
| `ADMIN_PASSWORD` | **UNSET** |
| `ADMIN_EMAIL` | UNSET (code default applies) |
| `AUTH_SECRET_KEY` | **UNSET** |
| `SECRET_KEY` | **UNSET** |
| `POSTGRES_PASSWORD` | UNSET *(as a variable)* |
| `DATABASE_URL` | SET, 51 chars |
| `WHATSAPP_VERIFY_TOKEN` / `WHATSAPP_TOKEN` / `OPENAI_API_KEY` | SET |

`POSTGRES_PASSWORD` being unset inside the container is **not** a fault: compose interpolated
`${POSTGRES_PASSWORD}` into `DATABASE_URL` at deploy time, so the credential is present where
it is used. This is the same coupling F3 hit as a crash-loop and is behaving correctly here.

Running code is confirmed **post-hardening**: `auth.py` in the container has 3 occurrences of
`AuthConfigurationError` and **0** of the removed fallbacks. `GIT_SHA=3f18f63`.

## 5. Database is not involved

```
connect                        PASS
database                       crm_test
alembic_version                20260831_wild01_dedup_causal_inbound
whatsapp_thread_states         33 rows
pending_location_proposal      present
```

`/login` performs **no database access** — `_render_login` → `_new_captcha` → `sign_session`
and nothing else. DB connectivity is healthy and irrelevant to this failure.

**Separate observation (MEDIUM, not the cause):** `alembic_version` lags the repository head
by two revisions (`20260901_l4_1`, `20260906_pending_location_proposal`). The
`pending_location_proposal` column exists because W2-F1 applied it with a direct
`ALTER TABLE … IF NOT EXISTS` on crm_test rather than through Alembic, so the schema is
correct but the version table does not record it. Worth reconciling before anything depends
on `alembic current`.

## 6. Blast radius

| surface | status | meaning |
|---|---|---|
| `/login` | **500** | cannot render |
| `/kanban` `/table` `/calendar` `/control` | 303 → `/login` | **all redirect into the 500** |
| `/docs` `/openapi.json` | 200 | app process healthy |
| Meta webhook (public) | 403 on a deliberately wrong verify token | endpoint alive and correct |
| Booking Flow data-exchange | 421 on an unencrypted probe | endpoint alive |
| n8n → CE `/api/conversation/handle` | 422 (auth passed, empty body) | **conversation path unaffected** |

**The entire operator UI is unusable — no one can log in.** WhatsApp ingestion, CE
processing, the Booking Flow and the outbound gate are all unaffected. crm_test is the
deployed target; the production `crm` database was not touched by this audit.

No WhatsApp message was sent during this audit. Every probe was a GET or a deliberately
invalid POST to an integration endpoint.

## 7. Remediation — proposed, NOT executed

Also relevant: **crm_test has 0 rows in `users`** (production `crm` has 1), so on this
deployment there is no database-backed operator to fall back on. Environment admin login is
the only route in, which makes `ADMIN_PASSWORD` genuinely required here, not optional.

| # | action | category |
|---|---|---|
| 1 | Owner sets `AUTH_SECRET_KEY` in `/opt/ridecheck-crm/.env` — a **new** value; the removed literal must not be reused | **C — owner must choose** |
| 2 | Owner sets `ADMIN_PASSWORD` in the same file — likewise new | **C — owner must choose** |
| 3 | Redeploy the backend with those present (`POSTGRES_PASSWORD` must also be supplied, as F3 learned) | **D — deploy/restart** |
| 4 | Guard `_render_login` so a missing key renders a clear 503 configuration page instead of a 500 | **A — code only, no credential** |
| 5 | Reconcile `alembic_version` with the repo head | A, separate |

**Smallest change that restores the CRM: steps 1 + 2 + 3.** Step 4 is the correct fix for the
*code defect* and should follow, but it alone converts a 500 into an honest 503 — it does not
restore login, because fail-closed is the intended behaviour once configured.

Setting `AUTH_SECRET_KEY` invalidates any existing session cookie; operators will need to log
in again. **No credential is rotated by any of this** — both values are for variables that
have never been set.

A rollback to `l4.7c4a-livesem-4ec8c43` would restore `/login` immediately but would discard
F2 fuzzy governance, F3 authority closure, F4 blocker closure and W2-F1 location recovery.
Not recommended.

## 8. Process finding

The real lesson is not the missing variable. It is that **a milestone declared its own change
undeployable and three later milestones deployed it anyway**, because each of them rebuilt
from HEAD and verified only the surfaces it had changed. A deploy-blocked commit needs a
mechanism stronger than a sentence in a closeout — a flag default, a startup assertion, or a
smoke check on `/login` in the post-deploy verification I run every time.
