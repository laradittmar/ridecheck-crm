PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7-SOURCE-RECOVERY

# L4.7 — Source recovery and off-host safety

Date: 2026-09-01
No runtime business behaviour changed · no Wild run · OUTBOUND OFF · production DB not modified.

---

## 1. Worktree classification

Every modified and untracked entry was classified explicitly; nothing was added with `git add .`.

| Class | Count | Entries |
|---|---|---|
| **A REQUIRED_SOURCE** | 14 | `backend/app/routes/flow_data_exchange.py`, `backend/app/services/{booking_flow_service,outbound_path_registry,security_events,travel}.py`, `backend/app/routes/whatsapp.py`, `backend/app/schemas/leads.py`, `backend/app/settings.py`, `backend/app/ui/{components,kanban,kanban_view}.py`, `backend/app/static/bg.png`, `backend/app/static/branding/`, `backend/requirements.txt`, `docker-compose.yml` |
| **B REQUIRED_MIGRATION** | 4 | `20260827_m21_3_thread_revision_zone_group`, `20260828_m2_authorized_path_monitoring`, `20260829_m21_4a_attribution`, `20260831_wild01_dedup_causal_inbound` |
| **C REQUIRED_TEST** | 25 | 19 untracked suites (L1, M2, M21.3 ×10, M21.4A, WILD-01, RC baseline, 2 postgres smokes) + 7 modified suites (M18, M19 ×2, M21.2 ×3, backend services) |
| **D REQUIRED_DOC** | 36 | 34 milestone closeouts/audits/prompts, `docs/Launch/…SOURCE-OF-TRUTH.md`, `client_authoritative_data/tracking.md` |
| **E REQUIRED_N8N_SOURCE** | 1 dir | `N8N workflows/` — **stale**, see §4 |
| **F GENERATED_RUNTIME** | 12 | `__pycache__/`, `.pytest_cache/` (already ignored) |
| **G LOCAL_DB** | 2 | `test.db`, `backend/test_alembic.db` — now ignored |
| **H BACKUP** | 1 | `docker-compose.beta.yml.bak.20260728` — now ignored, file preserved on disk |
| **I DISPOSABLE** | 0 | nothing deleted |
| **J SECRET / NEVER COMMIT** | 3 | `/opt/ridecheck-secrets/*.pem`, `/opt/ridecheck-crm/.env`, `/opt/ridecheck-crm-runtime/closed-beta.env` — outside the repo, ignored, archived at 0600 outside git |
| **K UNKNOWN** | **0** | nothing was committed while unclassified |

## 2. Production source verification

All five previously untracked production modules were compared byte-for-byte against the
running image `ridecheck-crm-backend:l4.7d-validator-b889b67`: **5/5 identical**, 0 commits
in git history before this milestone. All four migrations are present in the image and the
`crm_test` alembic head is `20260831_wild01_dedup_causal_inbound` (with
`20260901_l4_1_meta_error_capture` already tracked). The eight modified tracked source
files were verified the same way: **8/8 identical to the running image**, so they are
current production source, not stale local edits. Nothing was reverted or discarded.

## 3. Secret finding (pre-existing, HIGH)

`docker-compose.yml` at the branch tip still carried a literal Meta token and a Gmail app
password; the working tree already had the `${WHATSAPP_TOKEN}` / `${SMTP_PASSWORD}`
substitution but it had never been committed. That fix is now committed.

**This does not undo the exposure.** Commit `ddcd03b` (2026-05-10) is an ancestor of
`origin/main` and already carries both literals **on GitHub**, so the credentials have been
published for months — the push performed in this milestone added no new exposure. The
exposed Meta token is an older token than the one in the current runtime (different
suffix); the Gmail app password belongs to `ridecheckassistance@gmail.com`, whose alerting
has since moved to Resend.

**Owner action: rotate both credentials.** History rewriting is out of scope here and would
not help — the values are already public.

Correction to the L4.7-SAFETY-SNAPSHOT report: it stated "SECRETS COMMITTED TO GIT: NO".
That scan searched the working tree, where the fix was present. At `HEAD` and in history the
literals were there. The accurate statement is the one above.

Two committed audit documents mention `EAAW5PLc…` — verified to be 8–13 character **masked
prefixes**, not usable credentials.

## 4. n8n source of truth

`N8N workflows/` holds March exports with 96 and 112 nodes. The live workflow has **164
nodes**, `active=true`, last updated 2026-08-14. The repository copies are therefore stale
and were **not** committed as parity: the **n8n database is the operational source of
truth**, and a fresh logical export of the live workflow is in the snapshot set
(`n8n_workflows_2026-09-02T002216Z.json`, 1 workflow).

## 5. Recovery commits

| SHA | Content |
|---|---|
| `f481048` | remove literal token/password from `docker-compose.yml`; `.gitignore` hygiene (PEM/keys, local SQLite, dumps, archives, editor noise) |
| `8e14347` | 5 production modules + 4 migrations + branding assets (1 454 insertions) |
| `f4e12c8` | 8 modified production source files matching the running image (568 insertions) |
| `acb47f4` | 25 certification test suites (12 852 insertions) |
| `4bdd73d` | 34 milestone closeouts/audits + launch source-of-truth doc + client tracking reference (13 898 insertions) |
| `ecaff10` | `docs/operations/RECOVERY_MANIFEST.md` |

Every staged diff was scanned for Meta/OpenAI/Resend token shapes and PEM headers: **0 matches**.

## 6. Push and fresh-clone verification

`git push -u origin fix/m21.1.1-primary-flow-regression` — new branch created, upstream
configured, **no force, no history rewrite**. `git ls-remote` and the local HEAD agree
(`0 0` ahead/behind).

A fresh `git clone --branch fix/m21.1.1-primary-flow-regression` was verified to contain:
12/12 required production modules (including `response_validator.py` and
`conversation_engine.py`), 43 migrations, 9/9 checked gate suites (L1, L2, L3, L4.3, L4.4,
L4.6, L4.7D, M21.3 booking flow, M2 authorized paths), all five architecture/launch docs,
the semantic truth model and corpus, and 53 milestone documents. **A fresh clone can rebuild
the current backend without any secret or database.**

## 7. Off-host backup

No off-host destination is configured on this host: no rclone/aws/s3cmd/doctl configuration,
no SSH private key for a backup target, no backup cron or timer — only the `rsync`/`scp`
binaries with nowhere to send. **OFF_HOST_BACKUP: BLOCKED — OWNER ACTION REQUIRED**; the
files and hashes to copy are listed in `docs/operations/RECOVERY_MANIFEST.md` §3. Git push
was not delayed for it.

## 8. Status

- Git source recoverability: **PASS** — the branch and all required source are on GitHub.
- Off-host data backup: **BLOCKED (MEDIUM)** — snapshots still share a disk with the data.
- Semantic migration: **PAUSED**. L4.7E may resume; its corpus and truth model are committed.
