#!/usr/bin/env bash
# OPS-CRM-500 — deployment preflight. Run BEFORE replacing the backend container.
#
# Why this exists: SEC-PRELAUNCH-SOURCE-HARDENING removed the hardcoded ADMIN_PASSWORD and
# AUTH_SECRET_KEY fallbacks and marked itself BLOCKED_PENDING_OWNER_CREDENTIAL_CONFIGURATION.
# Three later milestones rebuilt from HEAD and deployed anyway, and the CRM login page was
# down for three days before anyone looked at it. A sentence in a closeout was not a strong
# enough mechanism. This is.
#
#   ./scripts/preflight_deploy.sh /opt/ridecheck-crm/.env  &&  docker compose up -d …
#
# Exits non-zero — and prints WHICH variable is missing, never its value — so the deploy
# command after the && never runs.
set -euo pipefail

ENV_FILE="${1:-/opt/ridecheck-crm/.env}"

# Variables the hardened runtime requires. Absent or empty means the container will start
# and then fail closed on real traffic, which is worse than not starting at all.
REQUIRED=(
  AUTH_SECRET_KEY        # session signing; absent => /login cannot render (OPS-CRM-500)
  ADMIN_PASSWORD         # environment admin login; absent => no operator can log in
  POSTGRES_PASSWORD      # interpolated into DATABASE_URL; absent => empty-password crash-loop
  WHATSAPP_VERIFY_TOKEN  # Meta webhook verification
  WHATSAPP_TOKEN
  OPENAI_API_KEY
  # OPS-CRM-500: the F4 internal-API boundary was silently disabled by a deploy that
  # read only .env, because enablement lived in a shell variable someone had to remember
  # to type. Configuration that exists only in a person's memory is not configuration.
  INTERNAL_API_AUTH_ENABLED
  INTERNAL_API_TRUSTED_CIDR
)

if [[ ! -f "$ENV_FILE" ]]; then
  echo "PREFLIGHT FAIL: env file not found: $ENV_FILE" >&2
  exit 2
fi

missing=()
for key in "${REQUIRED[@]}"; do
  value="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  if [[ -z "${value}" ]]; then
    missing+=("$key")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "PREFLIGHT FAIL — deployment STOPPED before container replacement." >&2
  echo "Missing or empty in ${ENV_FILE}:" >&2
  for key in "${missing[@]}"; do echo "  - ${key}" >&2; done
  echo "Set them and re-run. Values are never read or printed by this script." >&2
  exit 1
fi

# Presence in .env is NOT the same as presence in the container: compose only injects a
# variable the service declares. This gap is what kept /login at 503 even after the values
# were configured, so the compose wiring is checked too.
COMPOSE_FILE="${2:-/opt/ridecheck-crm-release-candidate/docker-compose.beta.yml}"
if [[ -f "$COMPOSE_FILE" ]]; then
  undeclared=()
  for key in AUTH_SECRET_KEY ADMIN_PASSWORD; do
    grep -qE "^[[:space:]]*${key}:" "$COMPOSE_FILE" || undeclared+=("$key")
  done
  if (( ${#undeclared[@]} > 0 )); then
    echo "PREFLIGHT FAIL — deployment STOPPED before container replacement." >&2
    echo "Present in ${ENV_FILE} but NOT declared in ${COMPOSE_FILE}," >&2
    echo "so the container would start without them:" >&2
    for key in "${undeclared[@]}"; do echo "  - ${key}" >&2; done
    exit 1
  fi
fi

echo "PREFLIGHT PASS — ${#REQUIRED[@]} required variables present in ${ENV_FILE}"
echo "                 and declared in $(basename "${COMPOSE_FILE}")."
