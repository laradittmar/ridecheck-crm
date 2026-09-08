#!/usr/bin/env bash
# L4.7W4-F3 — build the backend image with its own commit baked in.
#
# Why: outbound records stamp `deployment_id` from GIT_SHA. That value used to come from a
# compose default pinned to d5f89b3, so every message sent by three later images claimed to
# come from that commit. The ledger could not answer "which deployment sent this", which is
# the whole point of CONTAINER-INDEPENDENT TRACEABILITY.
#
#   ./scripts/build_backend.sh <label>      # e.g. ./scripts/build_backend.sh w4f3-bookprice
#
# Prints the tag it built. Nothing to remember and nothing to export.
set -euo pipefail

LABEL="${1:?usage: build_backend.sh <label>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SHA="$(git rev-parse --short HEAD)"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "BUILD WARNING: working tree is dirty — ${SHA} does not fully describe this image." >&2
fi

TAG="ridecheck-crm-backend:${LABEL}-${SHA}"
docker build -q --build-arg "GIT_SHA=${SHA}" -t "$TAG" -f backend/Dockerfile backend >/dev/null
echo "$TAG"
