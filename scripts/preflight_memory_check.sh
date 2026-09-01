#!/usr/bin/env bash
# L4.3 Phase G — memory preflight for heavy workloads (INFRA-OOM-01).
#
# On 2026-09-01 a host-wide OOM on a ~4 GB zero-swap server killed the n8n container
# (the sole inbound WhatsApp transport) and the local agent process. Run this before
# any full regression suite, image build, or controlled Wild session.
#
#   ./scripts/preflight_memory_check.sh          # assert and exit non-zero on failure
#
# Thresholds: swap >= 2 GB active, available RAM >= 1 GB.
set -euo pipefail

MIN_SWAP_MB=2048
MIN_AVAIL_MB=1024

swap_total_mb=$(free -m | awk '/^Swap:/ {print $2}')
avail_mb=$(free -m | awk '/^Mem:/ {print $7}')

echo "PREFLIGHT MEMORY CHECK"
echo "  swap total     : ${swap_total_mb} MB (required >= ${MIN_SWAP_MB} MB)"
echo "  available RAM  : ${avail_mb} MB (required >= ${MIN_AVAIL_MB} MB)"

status=0
if [ "${swap_total_mb}" -lt "${MIN_SWAP_MB}" ]; then
  echo "  FAIL: swap below threshold — create/activate /swapfile before continuing"
  status=1
fi
if [ "${avail_mb}" -lt "${MIN_AVAIL_MB}" ]; then
  echo "  FAIL: available RAM below threshold — stop other workloads before continuing"
  status=1
fi

if [ "${status}" -eq 0 ]; then
  echo "  RESULT: PASS"
else
  echo "  RESULT: FAIL — do NOT start heavy suites or a Wild session"
fi
exit "${status}"
