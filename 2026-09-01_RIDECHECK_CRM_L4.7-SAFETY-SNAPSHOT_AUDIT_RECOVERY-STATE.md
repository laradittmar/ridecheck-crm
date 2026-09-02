PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: L4.7-SAFETY-SNAPSHOT

# L4.7 — Recovery-state audit and pre-migration snapshot

Date: 2026-09-01 (snapshot timestamp `2026-09-02T002216Z`)
Read-only audit of what is recoverable, plus timestamped backups of every non-git state.
No application code changed, no DB data modified, OUTBOUND OFF throughout.

---

## 1. Git state

| Item | Value |
|---|---|
| Branch | `fix/m21.1.1-primary-flow-regression` |
| HEAD | `5af7f0dd6795246284c45d1d7a26c0edcb5f3fcc` |
| Remote | `origin` → https://github.com/laradittmar/ridecheck-crm.git (no credentials in URL) |
| Upstream for this branch | **none configured** |
| Branch on origin | **does not exist** |
| origin/main | `33caf3fc1ebce5001f3fb461e5f1f6558446b2a4` (2026-06-24) |
| Commits on HEAD not on origin/main | **107** |
| Modified tracked files | 17 |
| Untracked files | 68 |

### BLOCKER-SNAP-01 — nothing is pushed anywhere

The entire launch-certification effort (L1 → L4.7D, 107 commits) exists **only in this
worktree's local git**. The remote's newest branch is two months old. A disk loss on this
host destroys all of it.

### BLOCKER-SNAP-02 — production modules are not in git at all

Untracked, yet imported by the running backend:

```
backend/app/routes/flow_data_exchange.py
backend/app/services/booking_flow_service.py
backend/app/services/outbound_path_registry.py
backend/app/services/security_events.py
backend/app/services/travel.py
backend/migrations/versions/20260827_m21_3_thread_revision_zone_group.py
backend/migrations/versions/20260828_m2_authorized_path_monitoring.py
backend/migrations/versions/20260829_m21_4a_attribution.py
backend/migrations/versions/20260831_wild01_dedup_causal_inbound.py
```

plus 19 untracked test files (L1 semantic authority, M21.3 booking flow, ops dashboard,
scheduler, attribution, WILD-01 remediation, …). These are captured in the snapshot archive
below, but they are **not** version-controlled.

### Committed and recoverable from git

L4.3, L4.4, L4.6, L4.7 audit, L4.7D (code, tests, closeouts), `LAUNCH_TRUTH_ROADMAP.md`,
`CLAUDE.md`, `PROJECT_CONTEXT.md`, `docs/architecture/DOMAIN_MODEL.md`,
`docs/architecture/BOOKING_UX_CONTRACT.md`, `docker-compose.beta.yml`,
`scripts/preflight_memory_check.sh` — all committed. The 17 modified tracked files are
pre-existing UI/route/test deltas that predate this session.

---

## 2. Non-git state inventory

| State | Location | Notes |
|---|---|---|
| crm_test (certification DB) | volume `ridecheck-crm_pgdata` → `/var/lib/docker/volumes/ridecheck-crm_pgdata/_data`, database `crm_test` (10 MB) | target of all L4 work |
| **production DB** | same postgres instance, database `crm` (9.7 MB) | **read-only in this audit**; 8 leads / 31 messages / 4 revisions, unchanged |
| other DBs | `crm_smoke_test` (8.6 MB), `postgres` | |
| n8n | volume `ridecheck-crm_n8n_data` → `/home/node/.n8n`; `database.sqlite` 117 MB, 1 workflow, 3 credentials | sole inbound transport |
| RC-worktree volume | `ridecheck-crm-release-candidate_pgdata` | unused by the running stack |
| forensic evidence | `/opt/ridecheck-crm-forensics` (320 KB, 10 files: Wild A, Wild B, L4.2/L4.4 exports, hashes) | |
| Flow private key | `/opt/ridecheck-secrets/flow_booking_private.pem` (0600) + public PEM | bind-mounted read-only into the backend |
| runtime env/secrets | `/opt/ridecheck-crm/.env` (Meta token, phone id), `/opt/ridecheck-crm-runtime/closed-beta.env` (0600) | both gitignored |

---

## 3. Snapshots taken (all outside git, `/opt/ridecheck-backups`)

| Artifact | SHA256 |
|---|---|
| `crm_test_2026-09-02T002216Z.sql.gz` | `15312f9f55bda4b5e9ea88b1c5afda0e6b0dd243697d3a720a973207ce19f14f` |
| `crm_production_2026-09-02T002216Z.sql.gz` (read-only dump) | `2b2211e3ec3024cc9286ac52ca74d43533606e337d0541698903f39a0db3dd21` |
| `n8n_workflows_2026-09-02T002216Z.json` (logical export, 1 workflow) | `b1cbd6b7d09f782a690f19ee17e8a2352ffb7c25248c71086db29906d1f4b866` |
| `n8n_volume_2026-09-02T002216Z.tar.gz` (28 MB, read-only mount) | `5a9b5d7bf02731a5025b967465bc55aad8e1fe78cdbf32aa16d5c99930000fb7` |
| `forensics_2026-09-02T002216Z.tar.gz` | `f028ba0765bf585dd9ec3507da8d083f49e6753eb3d9c6c5cf1aef0411d901b7` |
| `untracked_source_2026-09-02T002216Z.tar.gz` (38 files not in git) | `8dfdc582b7e7502c17c2407825f2249a4780ff9ecfdc64d454555c79f02a0eea` |
| `secrets_env_2026-09-02T002216Z.tar.gz` (0600, **never in git**) | `fcf6a5443f465c5cc2337b34cd0e3ec434c9b968281e0daa8d60cb00ee366441` |

Method notes: `pg_dump --no-owner --no-privileges` for both databases (read-only, 21 COPY
blocks for crm_test); the n8n export used `n8n export:workflow --all` inside the container
(reads only — workflows untouched, container still up, sqlite mtime unchanged) and the
volume tar mounted `ridecheck-crm_n8n_data` **read-only**. The credentials export was
generated and then deleted inside the container rather than copied out; credential recovery
is covered by the volume tar, which also holds the n8n encryption key.

### SNAP-03 (MEDIUM) — snapshots are on the same host

`/opt/ridecheck-backups` lives on the same disk as the data it protects. It survives a
container or volume mistake, not a host loss. An off-host copy is still missing.

---

## 4. Secrets hygiene

- Tracked-file scan for Meta/OpenAI/Resend tokens and PEM headers: **0 matches**.
- `.env` is not tracked; `.gitignore` covers `.env`, `.env.*`, `*.env`.
- The secrets archive is 0600 and outside any git repository.
- Pre-existing note (L4-WILD-A audit): the live `WHATSAPP_TOKEN` remains in
  `/root/.bash_history` — not git-tracked; still worth scrubbing.

---

## 5. Recommendation before semantic migration

1. Push the branch: `git push -u origin fix/m21.1.1-primary-flow-regression` (owner action —
   it publishes 107 commits and needs owner authorization).
2. Commit the untracked production modules and migrations, or record explicitly why they
   stay out of git.
3. Copy `/opt/ridecheck-backups` off-host.
