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

echo "PREFLIGHT PASS — ${#REQUIRED[@]} required variables present in ${ENV_FILE}."
