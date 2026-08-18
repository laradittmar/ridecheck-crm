#!/usr/bin/env bash
# m20_live_beta_runtime_preflight.sh
#
# Preflight guard for M20 closed-beta live vehicle-catalog validation.
# Fails closed: any single check failure aborts and shuts containers back down.
#
# Run from the release-candidate directory:
#   bash scripts/m20_live_beta_runtime_preflight.sh
#
# On PASS: prints the activation gate token and leaves containers running
#           in OUTBOUND_ENABLED=false mode, ready for APPROVE_D4V_R_LIVE.
# On FAIL: shuts containers down and prints the failing check.

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
RC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROD_DIR="/opt/ridecheck-crm"
BETA_COMPOSE_OVERRIDE="$RC_DIR/docker-compose.beta.yml"
N8N_DB="/var/lib/docker/volumes/ridecheck-crm_n8n_data/_data/database.sqlite"
WORKFLOW_ID="DaFqDIzVi1f92Hvz"
EXPECTED_IMAGE="ridecheck-crm-backend:m20.6d5.2-093074a"
EXPECTED_DB_SUFFIX="crm_test"
EXPECTED_ALLOWLIST_SUFFIX="8330"
EXPECTED_ALLOWLIST_COUNT=1
BACKEND_CONTAINER="ridecheck-crm-backend-1"
N8N_CONTAINER="ridecheck-crm-n8n-1"

PASS=0
FAIL=0

pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }

shutdown_on_fail() {
    if [ "$FAIL" -gt 0 ]; then
        echo ""
        echo "PREFLIGHT FAILED ($FAIL check(s)) — shutting containers back down"
        docker stop "$BACKEND_CONTAINER" 2>/dev/null || true
        docker stop "$N8N_CONTAINER" 2>/dev/null || true
        exit 1
    fi
}

echo "═══════════════════════════════════════════════════════"
echo "  M20.6D.4V-R RUNTIME PREFLIGHT"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "═══════════════════════════════════════════════════════"
echo ""

# ── PRE-START CHECKS (no containers required) ──────────────────────────────────
echo "── PRE-START CHECKS ──────────────────────────────────"

# 1. Image exists
if docker image inspect "$EXPECTED_IMAGE" > /dev/null 2>&1; then
    pass "1. Image $EXPECTED_IMAGE exists"
else
    fail "1. Image $EXPECTED_IMAGE NOT FOUND — build required"
fi

# 2. Compose override file exists
if [ -f "$BETA_COMPOSE_OVERRIDE" ]; then
    pass "2. docker-compose.beta.yml present"
else
    fail "2. docker-compose.beta.yml MISSING at $BETA_COMPOSE_OVERRIDE"
fi

# 3. Production .env has OPENAI_API_KEY
OPENAI_STATUS=$(python3 -c "
env = {}
with open('$PROD_DIR/.env') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            env[k.strip()] = v.strip()
v = env.get('OPENAI_API_KEY', '')
print('present/non-empty' if v else 'EMPTY/ABSENT')
" 2>/dev/null || echo "ERROR reading .env")
if [ "$OPENAI_STATUS" = "present/non-empty" ]; then
    pass "3. OPENAI_API_KEY in $PROD_DIR/.env: present/non-empty"
else
    fail "3. OPENAI_API_KEY in $PROD_DIR/.env: $OPENAI_STATUS"
fi

# 4. n8n stopped before activation
N8N_RUNNING=$(docker inspect "$N8N_CONTAINER" --format '{{.State.Running}}' 2>/dev/null || echo "false")
if [ "$N8N_RUNNING" = "false" ]; then
    pass "4. n8n stopped"
else
    fail "4. n8n is RUNNING — must be stopped before activation"
fi

# 5. Workflow inactive
WORKFLOW_ACTIVE=$(sqlite3 "$N8N_DB" "SELECT active FROM workflow_entity WHERE id='$WORKFLOW_ID';" 2>/dev/null || echo "?")
if [ "$WORKFLOW_ACTIVE" = "0" ]; then
    pass "5. Workflow $WORKFLOW_ID inactive"
else
    fail "5. Workflow $WORKFLOW_ID active=$WORKFLOW_ACTIVE — must be inactive"
fi

# 6. 0 active/waiting executions
EXEC_COUNT=$(sqlite3 "$N8N_DB" "SELECT COUNT(*) FROM execution_entity WHERE status IN ('running','waiting');" 2>/dev/null || echo "?")
if [ "$EXEC_COUNT" = "0" ]; then
    pass "6. 0 active/waiting n8n executions"
else
    fail "6. $EXEC_COUNT active/waiting executions — must be 0"
fi

# 7. n8n has 0 direct Meta/Graph API send nodes
META_NODES=$(sqlite3 "$N8N_DB" "SELECT nodes FROM workflow_entity WHERE id='$WORKFLOW_ID';" 2>/dev/null | python3 -c "
import sys, json
data = json.loads(sys.stdin.read() or '[]')
if isinstance(data, str): data = json.loads(data)
nodes = data if isinstance(data, list) else data.get('nodes', [])
meta = [n for n in nodes if any(k in n.get('type','').lower() for k in ('whatsapp','meta','graph'))]
print(len(meta))
" 2>/dev/null || echo "?")
if [ "$META_NODES" = "0" ]; then
    pass "7. Workflow has 0 direct Meta/Graph API nodes"
else
    fail "7. Workflow has $META_NODES direct Meta/Graph API nodes"
fi

echo ""
shutdown_on_fail

# ── START CONTAINERS IN SAFE MODE ─────────────────────────────────────────────
echo "── STARTING CONTAINERS (OUTBOUND=false) ──────────────"

# Tear down any existing backend
docker stop "$BACKEND_CONTAINER" 2>/dev/null && echo "  Stopped existing backend" || true
docker rm   "$BACKEND_CONTAINER" 2>/dev/null && echo "  Removed existing backend" || true

# Start backend via compose override (registers 'backend' alias, loads .env for OPENAI_API_KEY)
cd "$PROD_DIR"
docker compose \
    -f docker-compose.yml \
    -f "$BETA_COMPOSE_OVERRIDE" \
    up -d --force-recreate backend
echo "  Backend started via compose (OUTBOUND_ENABLED=false)"

# Start n8n via compose (registers 'n8n' alias)
docker compose up -d --force-recreate n8n
echo "  n8n started via compose"

echo "  Waiting 8s for services to become healthy..."
sleep 8

# ── POST-START CHECKS (containers must be running) ────────────────────────────
echo ""
echo "── POST-START CHECKS ─────────────────────────────────"

# 8. Backend image
ACTUAL_IMAGE=$(docker inspect "$BACKEND_CONTAINER" --format '{{.Config.Image}}' 2>/dev/null || echo "MISSING")
if [ "$ACTUAL_IMAGE" = "$EXPECTED_IMAGE" ]; then
    pass "8. Backend image: $ACTUAL_IMAGE"
else
    fail "8. Backend image: got $ACTUAL_IMAGE, expected $EXPECTED_IMAGE"
fi

# 9. DATABASE_URL targets crm_test
DB_URL=$(docker inspect "$BACKEND_CONTAINER" --format '{{json .Config.Env}}' | python3 -c "
import sys, json
for e in json.loads(sys.stdin.read()):
    if e.startswith('DATABASE_URL='):
        print(e.split('=',1)[1])
" 2>/dev/null || echo "")
if echo "$DB_URL" | grep -q "$EXPECTED_DB_SUFFIX"; then
    pass "9. DATABASE_URL targets $EXPECTED_DB_SUFFIX"
else
    fail "9. DATABASE_URL=$DB_URL does not target $EXPECTED_DB_SUFFIX"
fi

# 10. OUTBOUND_ENABLED=false
OUTBOUND=$(docker inspect "$BACKEND_CONTAINER" --format '{{json .Config.Env}}' | python3 -c "
import sys, json
for e in json.loads(sys.stdin.read()):
    if e.startswith('OUTBOUND_ENABLED='):
        print(e.split('=',1)[1])
" 2>/dev/null || echo "")
if [ "$OUTBOUND" = "false" ]; then
    pass "10. OUTBOUND_ENABLED=false"
else
    fail "10. OUTBOUND_ENABLED=$OUTBOUND (expected false)"
fi

# 11. Allowlist count=1 suffix=...8330
ALLOWLIST=$(docker inspect "$BACKEND_CONTAINER" --format '{{json .Config.Env}}' | python3 -c "
import sys, json
for e in json.loads(sys.stdin.read()):
    if e.startswith('CLOSED_BETA_ALLOWED_WA_IDS='):
        val = e.split('=',1)[1]
        ids = [x for x in val.split(',') if x.strip()]
        print(f'{len(ids)}:{ids[0][-4:] if ids else \"?\"}')
" 2>/dev/null || echo "0:?")
ALLOWLIST_COUNT=$(echo "$ALLOWLIST" | cut -d: -f1)
ALLOWLIST_SUFFIX=$(echo "$ALLOWLIST" | cut -d: -f2)
if [ "$ALLOWLIST_COUNT" = "$EXPECTED_ALLOWLIST_COUNT" ] && [ "$ALLOWLIST_SUFFIX" = "$EXPECTED_ALLOWLIST_SUFFIX" ]; then
    pass "11. Allowlist count=$ALLOWLIST_COUNT suffix=...$ALLOWLIST_SUFFIX"
else
    fail "11. Allowlist count=$ALLOWLIST_COUNT suffix=...$ALLOWLIST_SUFFIX (expected count=$EXPECTED_ALLOWLIST_COUNT suffix=...$EXPECTED_ALLOWLIST_SUFFIX)"
fi

# 12. OPENAI_API_KEY present/non-empty in running container
OPENAI_IN_CONTAINER=$(docker inspect "$BACKEND_CONTAINER" --format '{{json .Config.Env}}' | python3 -c "
import sys, json
for e in json.loads(sys.stdin.read()):
    if e.startswith('OPENAI_API_KEY='):
        v = e.split('=',1)[1]
        print('present/non-empty' if v.strip() else 'EMPTY')
        exit()
print('ABSENT')
" 2>/dev/null || echo "ERROR")
if [ "$OPENAI_IN_CONTAINER" = "present/non-empty" ]; then
    pass "12. OPENAI_API_KEY in running backend: present/non-empty"
else
    fail "12. OPENAI_API_KEY in running backend: $OPENAI_IN_CONTAINER"
fi

# 13. DNS: n8n → backend (GET /api/settings/ai-enabled)
AI_STATUS=$(docker exec "$N8N_CONTAINER" wget -qO- http://backend:8000/api/settings/ai-enabled 2>/dev/null || echo "FAIL")
if echo "$AI_STATUS" | grep -q "ai_enabled"; then
    pass "13. DNS n8n→backend: GET /api/settings/ai-enabled → $AI_STATUS"
else
    fail "13. DNS n8n→backend: FAILED (got: $AI_STATUS)"
fi

# 14. DNS: backend → n8n (GET /healthz)
N8N_HEALTH=$(docker exec "$BACKEND_CONTAINER" python3 -c "
import urllib.request
try:
    r = urllib.request.urlopen('http://n8n:5678/healthz', timeout=5)
    print(r.read().decode())
except Exception as e:
    print(f'FAIL:{e}')
" 2>/dev/null || echo "FAIL")
if echo "$N8N_HEALTH" | grep -q "ok"; then
    pass "14. DNS backend→n8n: GET /healthz → $N8N_HEALTH"
else
    fail "14. DNS backend→n8n: FAILED (got: $N8N_HEALTH)"
fi

# 15. n8n can reach backend engine endpoint (/api/conversation/handle reachable — not called)
# Use python3 from n8n container for a clean HTTP check without wget header-parsing quirks
ENGINE_CHECK=$(docker exec "$N8N_CONTAINER" node -e "
const http = require('http');
const opts = { hostname:'backend', port:8000, path:'/api/conversation/handle',
               method:'POST', headers:{'Content-Type':'application/json'} };
const req = http.request(opts, (res) => {
  process.stdout.write('HTTP_' + res.statusCode);
  process.exit(0);
});
req.on('error', (e) => { process.stdout.write('FAIL:' + e.message); process.exit(1); });
req.write('{}');
req.end();
" 2>/dev/null || echo "FAIL")
if echo "$ENGINE_CHECK" | grep -qE "^HTTP_(422|200|400|401)$"; then
    pass "15. n8n→backend /api/conversation/handle: $ENGINE_CHECK (endpoint reachable)"
else
    fail "15. n8n→backend /api/conversation/handle: $ENGINE_CHECK"
fi

# 16. Zero outbound sends during preflight startup
OUTBOUND_COUNT=$(docker exec ridecheck-crm-postgres-1 psql -U crm -d crm_test -tAc \
    "SELECT COUNT(*) FROM whatsapp_messages WHERE direction='out' AND created_at > NOW() - INTERVAL '10 minutes';" \
    2>/dev/null | tr -d ' ' || echo "?")
if [ "$OUTBOUND_COUNT" = "0" ]; then
    pass "16. Zero outbound sends during preflight startup"
else
    fail "16. $OUTBOUND_COUNT outbound sends detected during preflight startup"
fi

echo ""
shutdown_on_fail

# ── SUMMARY ────────────────────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════════"
echo ""
echo "M20.6D.4V-R PREFLIGHT PASS — awaiting APPROVE_D4V_R_LIVE"
echo ""
echo "  Backend:  $EXPECTED_IMAGE  OUTBOUND=false  DB=crm_test"
echo "  n8n:      running (workflow inactive)"
echo "  DNS:      n8n→backend ✓  backend→n8n ✓"
echo "  AI key:   present/non-empty ✓"
echo "  Allowlist: count=1 suffix=...$EXPECTED_ALLOWLIST_SUFFIX ✓"
echo ""
echo "═══════════════════════════════════════════════════════"
