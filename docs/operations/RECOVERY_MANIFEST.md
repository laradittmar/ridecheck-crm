# RideCheck CRM — Recovery Manifest

Generated: 2026-09-01 (snapshot set `2026-09-02T002216Z`) by L4.7-SOURCE-RECOVERY.
**No secrets appear in this document** — only locations. Update it whenever a new snapshot
set is taken or the deployed image changes.

---

## 1. Source of truth

| Item | Value |
|---|---|
| Repository | `https://github.com/laradittmar/ridecheck-crm.git` (remote `origin`) |
| Active branch | `fix/m21.1.1-primary-flow-regression` (upstream configured) |
| Recovered commit | `4bdd73dc468136df43ac430e47a7b955c53d38d7` |
| Contents | all backend source, migrations, certification tests, architecture docs, launch roadmap, milestone closeouts, semantic corpus |
| Not in git, by design | `.env` files, PEM keys, databases, backup archives, n8n runtime data |

## 2. Runtime

| Item | Value |
|---|---|
| Deployed image | `ridecheck-crm-backend:l4.7d-validator-b889b67` |
| deployment_id / GIT_SHA | `b889b6763681` |
| Compose | `/opt/ridecheck-crm/docker-compose.yml` + `docker-compose.beta.yml` (in git) |
| Database in use | `crm_test` (certification); production database is `crm` |
| OUTBOUND | OFF |

## 3. Snapshot set `2026-09-02T002216Z` — `/opt/ridecheck-backups/`

| Artifact | SHA256 |
|---|---|
| `crm_test_2026-09-02T002216Z.sql.gz` | `15312f9f55bda4b5e9ea88b1c5afda0e6b0dd243697d3a720a973207ce19f14f` |
| `crm_production_2026-09-02T002216Z.sql.gz` | `2b2211e3ec3024cc9286ac52ca74d43533606e337d0541698903f39a0db3dd21` |
| `n8n_volume_2026-09-02T002216Z.tar.gz` | `5a9b5d7bf02731a5025b967465bc55aad8e1fe78cdbf32aa16d5c99930000fb7` |
| `n8n_workflows_2026-09-02T002216Z.json` | `b1cbd6b7d09f782a690f19ee17e8a2352ffb7c25248c71086db29906d1f4b866` |
| `forensics_2026-09-02T002216Z.tar.gz` | `f028ba0765bf585dd9ec3507da8d083f49e6753eb3d9c6c5cf1aef0411d901b7` |
| `untracked_source_2026-09-02T002216Z.tar.gz` (now redundant — contents are in git) | `8dfdc582b7e7502c17c2407825f2249a4780ff9ecfdc64d454555c79f02a0eea` |
| `secrets_env_2026-09-02T002216Z.tar.gz` (mode 0600, **never committed**) | `fcf6a5443f465c5cc2337b34cd0e3ec434c9b968281e0daa8d60cb00ee366441` |

Secret material lives at `/opt/ridecheck-secrets/` (Flow RSA key pair),
`/opt/ridecheck-crm/.env` and `/opt/ridecheck-crm-runtime/closed-beta.env`.
Contents are never recorded here or in git.

## 4. Restore order

1. **Source** — `git clone -b fix/m21.1.1-primary-flow-regression https://github.com/laradittmar/ridecheck-crm.git`, then check out `4bdd73d` (or later).
2. **Secrets** — restore `secrets_env_2026-09-02T002216Z.tar.gz` to `/opt/ridecheck-secrets`, `/opt/ridecheck-crm/.env`, `/opt/ridecheck-crm-runtime/closed-beta.env` (mode 0600). Rotate anything suspected compromised **before** the stack is started.
3. **Postgres** — start the postgres service, create `crm` and `crm_test`, then
   `gunzip -c crm_production_2026-09-02T002216Z.sql.gz | psql -U crm -d crm` and the same for `crm_test`.
4. **Backend image** — `docker build -t ridecheck-crm-backend:<tag> backend` from the cloned source; pin the tag and `GIT_SHA` in `docker-compose.beta.yml`. **Start with `OUTBOUND_ENABLED=false`.**
5. **n8n** — restore `n8n_volume_2026-09-02T002216Z.tar.gz` into the `ridecheck-crm_n8n_data` volume (it contains the sqlite database *and* the encryption key). `n8n_workflows_2026-09-02T002216Z.json` is the human-readable copy of the single active workflow.
6. **Forensics** — unpack `forensics_2026-09-02T002216Z.tar.gz` to `/opt/ridecheck-crm-forensics`.
7. **Verify before any traffic** — `scripts/preflight_memory_check.sh`; `OUTBOUND_ENABLED=false`; source/runtime parity for `conversation_engine.py`, `response_validator.py`, `schedule.py`, `booking_flow_service.py`, `main.py`; alembic head `20260901_l4_1_meta_error_capture`; relevant gate suites green.

## 5. Known gaps

| Gap | Severity | Owner action |
|---|---|---|
| No off-host copy of `/opt/ridecheck-backups` — it shares the disk with the data it protects | **MEDIUM** | copy the snapshot set to object storage or another machine |
| Commit `ddcd03b` (2026-05-10), an ancestor of `origin/main`, contains a literal Meta token and a Gmail app password already published to GitHub | **HIGH** | rotate both credentials; history rewrite is not planned |
| n8n workflow JSON in `N8N workflows/` is a stale March export (96/112 nodes) versus the live workflow (164 nodes, updated 2026-08-14) | LOW | the n8n database is the operational source of truth; treat repo copies as historical |
