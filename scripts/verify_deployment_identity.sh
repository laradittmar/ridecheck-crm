#!/usr/bin/env bash
# L4.7W4-F3 — assert the running backend can tell the truth about which commit it is.
#
# Run AFTER recreating the container. Checks three identities agree:
#   1. the SHA baked into the running container (GIT_SHA)
#   2. the SHA in the image tag it is running
#   3. the deployment_id the outbound gate will actually stamp
#
# A mismatch means new outbound rows will be misattributed. Exits non-zero.
set -euo pipefail

CONTAINER="${1:-ridecheck-crm-backend-1}"

IMAGE="$(docker inspect "$CONTAINER" --format '{{.Config.Image}}')"
BAKED="$(docker exec "$CONTAINER" sh -c 'printf %s "$GIT_SHA"')"
STAMPED="$(docker exec "$CONTAINER" python -c \
  'from app.services.outbound_path_registry import get_deployment_id; print(get_deployment_id())')"
TAG_SHA="${IMAGE##*-}"

echo "  image        : $IMAGE"
echo "  baked GIT_SHA: ${BAKED:-<empty>}"
echo "  stamped id   : $STAMPED"
echo "  tag suffix   : $TAG_SHA"

fail=0
if [[ -z "$BAKED" || "$BAKED" == "unknown" ]]; then
  echo "FAIL: container has no baked GIT_SHA — build with scripts/build_backend.sh" >&2; fail=1
fi
if [[ "$STAMPED" != "$BAKED" ]]; then
  echo "FAIL: gate would stamp '$STAMPED' but the image is '$BAKED'" >&2; fail=1
fi
if [[ "$TAG_SHA" != "$BAKED" ]]; then
  echo "FAIL: image tag says '$TAG_SHA' but the image was built from '$BAKED'" >&2; fail=1
fi

if (( fail )); then exit 1; fi
echo "DEPLOYMENT IDENTITY PASS — outbound records will carry $STAMPED."
