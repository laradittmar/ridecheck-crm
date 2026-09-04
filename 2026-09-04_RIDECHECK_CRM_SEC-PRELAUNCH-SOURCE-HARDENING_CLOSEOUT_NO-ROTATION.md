PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: SEC-PRELAUNCH-SOURCE-HARDENING

# No working credential remains in tracked source — and nothing was rotated

Source and configuration only · **no deployment** · no container restarted · no database
touched · no credential value changed anywhere in the world.
Deployment status: **BLOCKED_PENDING_OWNER_CREDENTIAL_CONFIGURATION**

---

## 1. Verdict

**CONDITIONAL_PASS.** Tracked HEAD now contains **zero** real runtime credential literals,
and the two authentication fallbacks are gone. It is conditional for one reason that is not
a defect but a fact: **the live environment currently depends on the fallbacks this
milestone removed**, so the change is deliberately not deployable until the owner supplies
configuration. SEC-01 also stays open regardless, because the Maps key is still in Git
history.

## 2. A fourth finding, and why it changed the shape of the fix

The brief named SEC-01/02/03. While removing the admin-password fallback I found a second
hardcoded credential three lines above it in the same file:

```
def _secret() -> str:
    return os.getenv("AUTH_SECRET_KEY", os.getenv("SECRET_KEY", "dev-only-change-me"))
```

**SEC-04 — the session-signing key had a literal default, and `AUTH_SECRET_KEY` and
`SECRET_KEY` are both unset in the running backend.** The signed cookie *is* the credential
once issued. Anyone who read this public-shaped repository could mint a valid
`crm_session` cookie for `admin@ridecheck.local` and never touch the login form at all.

Fixing the password while leaving the signing key predictable would have been security
theatre: the password check is simply skipped by a forged cookie. Both defaults are
therefore removed together, and a test proves a cookie signed with the old default is
rejected — both when a real key is configured and when none is.

## 3. SEC-03 + SEC-04 — fail closed, no new secret invented

`backend/app/auth.py` now contains no credential and no key. Behaviour when configuration
is absent:

| path | before | after |
|---|---|---|
| `login_ok` with `ADMIN_PASSWORD` unset | accepted a literal password from the repo | returns **False**, logs `AUTH_CONFIGURATION_ERROR` |
| `_secret()` with no key set | returned a literal from the repo | raises `AuthConfigurationError` |
| `verify_session` with no key set | validated forgeable cookies | returns **None** — every session denied |
| `sign_session` with no key set | signed with the literal | raises; login renders a 503 "Authentication is not configured" |

Database-backed users (`User.hashed_password`) are untouched. Comparison is now
constant-time on both e-mail and password — a timing signal on either is still a signal.
`backend/app/main.py` catches the configuration error at login and renders a clear message
instead of a stack trace.

**No password was invented, chosen or set.**

## 4. SEC-02 — the database stops being an internet service

`docker-compose.yml`: `- "5432:5432"` → `- "127.0.0.1:5432:5432"`.

Impact analysis, checked rather than assumed:

| consumer | reaches Postgres via | affected by the change |
|---|---|---|
| backend | `postgres:5432` on the compose network | **no** |
| n8n | never connects to Postgres directly; it calls `http://backend:8000` | **no** |
| migrations | Alembic inside the backend container | **no** |
| backups | the `/opt/ridecheck-backups` dumps are produced through `docker exec`; no host-side script referencing 5432 exists | **no** |
| repository smoke tests | `localhost:5432` from the host | **no** — loopback binding preserves them |
| external monitoring | none found connecting to 5432 | **no** |

Removing the mapping entirely would have broken the host-side smoke tests, so loopback
binding is the correct choice rather than deletion.

The password itself is **unchanged**. What changed is that it is no longer written down:
`POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set in .env}` — with
**no default on purpose**, because a default would put the real password straight back into
tracked source, which is the entire problem. `DATABASE_URL` in both compose files now
interpolates it.

## 5. SEC-01 — key removed from HEAD, **not closed**

`N8N workflows/CRM - Ridecheck (Mar 5 at 08_59_04) (6).json` line 2540:
`&key=AIzaSy…` → `&key={{ $env.GOOGLE_MAPS_API_KEY }}`.

The URL is already an n8n expression, so this needs no structural change. Verified after
the edit: JSON still parses, **112 nodes** (unchanged), `&key=` preserved,
`maps.googleapis.com` preserved, zero `AIza` literals.

> **SEC-01 IS NOT CLOSED.** The old value remains in Git history and is still live at
> Google. This milestone only stops it propagating into current HEAD and future snapshots.
> Rotation is the only thing that closes it.

Note: the tracked export is not auto-imported, so the **running** n8n workflow still holds
the literal. Nothing was deployed, and the live behaviour is unchanged.

## 6. Other tracked credential literals

The same real database password was embedded in 32 further places — test fallbacks,
smoke-script defaults and quoted URLs inside historical milestone documents. All were the
*same working credential*, so all were removed:

* `tests/pg_dsn.py` (new) builds a DSN from `POSTGRES_PASSWORD` with **no default**; 11 test
  and smoke files now call it instead of carrying a literal;
* documentation occurrences became `${POSTGRES_PASSWORD}` placeholders — the meaning of each
  audit record is preserved, only the credential is gone;
* `WHATSAPP_VERIFY_TOKEN` literal → `${WHATSAPP_VERIFY_TOKEN}` in compose, and a placeholder
  in the one document that quoted it;
* `backend/README.md` no longer documents a default admin password; it now documents that
  both `ADMIN_PASSWORD` and `AUTH_SECRET_KEY` are required and that auth fails closed.

## 7. Static secret scan of tracked HEAD, after hardening

| classification | count |
|---|---|
| **REAL_LITERAL** | **0** |
| TEST_FIXTURE | 5 — Resend `re_…` strings in two e-mail tests, one literally named `_FAKE_API_KEY` |
| PLACEHOLDER | 2 — `***`-masked DSNs already redacted in earlier audit documents |
| ENV_REFERENCE | 43 |

Values are never printed by the scanner, and it excludes its own source (the test names the
patterns it forbids).

## 8. Temporary secret material deleted

Hashes recorded before deletion, so the record survives the file:

```
ca74db404d68d70e…  N8N workflows/N8N workflows/…(5).json      untracked duplicate
b80909b1b2d63758…  N8N workflows/N8N workflows/…(6).json      untracked, LIVE Maps key
ee061f7097cafad7…  docker-compose.beta.yml.bak.20260728       untracked, DB password
14f1225cb07869c2…  RIDECHECK_CRM_SOURCE_1bf47f3_2026-09-04.tar.gz   unsafe audit archive
                   /opt/ridecheck-independent-audit/sanitized-work/ (788 files)
```

The first three were found **by the new scan itself** — a nested `N8N workflows/N8N
workflows/` directory in the worktree still held the pre-redaction workflow with the live
key, and a stale compose backup held the database password. Neither was tracked; both were
sitting in the working tree.

Retained deliberately: the sanitized shareable archive and **both** manifests (including the
unsafe archive's, which preserves its SHA and findings), and everything under
`/opt/ridecheck-crm-forensics/`. No forensic or security evidence was deleted.

## 9. Tests and regression

`tests/test_sec_prelaunch_source_hardening.py` — SEC-SRC-01…10 plus a forged-cookie test,
a configured-key round trip, and a compose password-literal check.

Full regression: **3 567 passed / 59 failed / 9 errors** — the failure set is **identical**
to the L4.7C.4A baseline, with **0 new** and 0 disappeared. The env indirection changed no
application behaviour.

## 10. Deployment plan — execute none of this now

| # | step | category |
|---|---|---|
| A | Put the **existing** values in `/opt/ridecheck-crm/.env`: `POSTGRES_PASSWORD`, `WHATSAPP_VERIFY_TOKEN`, plus a **new** `ADMIN_PASSWORD` and `AUTH_SECRET_KEY` | **REQUIRES CREDENTIAL CONFIGURATION** |
| B | Deploy the fail-closed auth source | **blocked until A** — deploying first locks the owner out of the CRM |
| C | Apply the loopback Postgres binding (`docker compose up -d postgres`) | **CAN DEPLOY WITHOUT ROTATION**, but needs `POSTGRES_PASSWORD` in `.env` first because the `:?` guard now fails fast |
| D | Re-import the workflow with `GOOGLE_MAPS_API_KEY` set in the n8n environment | CAN DEPLOY WITHOUT ROTATION |
| E | Rotate the Google Maps key in Google Cloud | **REQUIRES ROTATION** — the only thing that closes SEC-01 |
| F | Purge historical credential exposure from Git history | REQUIRES ROTATION FIRST; optional afterwards |

A and B are genuinely coupled: `ADMIN_PASSWORD` and `AUTH_SECRET_KEY` are both currently
unset, so deploying B without A disables admin login entirely.

## 11. Deferred owner actions

1. Choose and set `ADMIN_PASSWORD` and `AUTH_SECRET_KEY` (new values — this milestone
   deliberately did not pick them).
2. Copy the existing `POSTGRES_PASSWORD` and `WHATSAPP_VERIFY_TOKEN` into `.env` unchanged.
3. Rotate the Google Maps API key — SEC-01 stays open until then.
4. Decide whether to also bind `8000` and `5678` to loopback. Both are proxied by nginx from
   localhost, and `https://n8n.ridecheck.ar/webhook/ridecheck-inbound` is currently
   internet-reachable with no allow-list of its own. Out of scope here; recorded, not changed.
5. Consider strengthening the database password once the `.env` indirection is deployed.

---

Nothing was rotated. Nothing was deployed. No database was touched.
