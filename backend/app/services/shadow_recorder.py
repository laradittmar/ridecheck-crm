"""L4.7B — append-only recorder for shadow TurnEvidence.

Shadow interpretation must be durable enough to replay and analyse (L4.7B.1) without
touching canonical state. This recorder therefore:

* appends one JSON object per line to a file — never rewrites, never deletes;
* also emits a structured `CE_SHADOW_UNDERSTAND` log line, so records survive even when
  the file path is unavailable;
* stores no raw message text and no secrets — only the interpretation, its provenance and
  the call telemetry;
* never raises. A recording failure must not affect a customer turn.

No migration, no ORM, no canonical write: this is deliberately additive and test-safe.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_PATH = "/opt/ridecheck-crm-forensics/shadow_turn_evidence.jsonl"
RECORD_VERSION = "shadow-record/1.1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def build_record(
    *,
    thread_id: Optional[int],
    burst_id: Optional[str],
    message_ids: tuple[str, ...] = (),
    result: Any = None,
    deployment_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    dispatch: str = "sync",
) -> dict:
    """Shape one append-only shadow record. Pure — no I/O."""
    evidence = getattr(result, "evidence", None)
    return {
        "record_version": RECORD_VERSION,
        "shadow": True,
        "recorded_at": _now_iso(),
        "thread_id": thread_id,
        "burst_id": burst_id,
        "message_ids": list(message_ids),
        "deployment_id": deployment_id,
        "correlation_id": correlation_id,
        "ok": bool(getattr(result, "ok", False)),
        "error": getattr(result, "error", None),
        "model": getattr(result, "model", None),
        "prompt_version": getattr(result, "prompt_version", None),
        "schema_version": getattr(result, "schema_version", None),
        "latency_ms": getattr(result, "latency_ms", None),
        "prompt_tokens": getattr(result, "prompt_tokens", None),
        "completion_tokens": getattr(result, "completion_tokens", None),
        "total_tokens": getattr(result, "total_tokens", None),
        "dispatch": dispatch,
        # WHICH context slots were supplied — never their values, so no raw text is stored.
        "context_keys": list(getattr(result, "context_keys", ()) or ()),
        "sanitized_items": getattr(result, "sanitized_items", 0),
        "turn_evidence": (json.loads(evidence.to_canonical_json())
                          if evidence is not None else None),
    }


def record_shadow(
    record: dict,
    path: Optional[str] = None,
) -> bool:
    """Append one record. Returns True when the file write succeeded.

    Logging happens either way, so a missing/unwritable path degrades to log-only.
    """
    try:
        logger.info(
            "CE_SHADOW_UNDERSTAND thread_id=%s burst=%s ok=%s model=%s latency_ms=%s "
            "tokens=%s items=%s",
            record.get("thread_id"), record.get("burst_id"), record.get("ok"),
            record.get("model"), record.get("latency_ms"), record.get("total_tokens"),
            _item_count(record),
        )
    except Exception:
        pass

    # A path must be a real string. A test double (or any non-string) degrades to
    # log-only: L4.7B wrote a `MagicMock/...` tree into the repo before this guard.
    candidate = path if isinstance(path, str) else None
    env_path = os.environ.get("SHADOW_EVIDENCE_PATH")
    target = (candidate or (env_path if isinstance(env_path, str) else "") or DEFAULT_PATH).strip()
    if not target:
        return False
    try:
        file_path = pathlib.Path(target)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("a", encoding="utf-8") as fh:      # append-only
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        logger.warning("L4.7B shadow record write failed (%s): %s", target, exc)
        return False


def _item_count(record: dict) -> int:
    evidence = record.get("turn_evidence") or {}
    total = 0
    for key in ("service_intents", "vehicle_mentions", "location_mentions", "faq_intents",
                "scheduling_requests", "corrections", "identity_mentions"):
        total += len(evidence.get(key) or [])
    total += 1 if evidence.get("acceptance") else 0
    total += 1 if evidence.get("handoff") else 0
    return total


def read_records(path: Optional[str] = None) -> list[dict]:
    """Read back the append-only log (for L4.7B.1 analysis). Read-only."""
    target = (path or os.environ.get("SHADOW_EVIDENCE_PATH") or DEFAULT_PATH).strip()
    file_path = pathlib.Path(target)
    if not file_path.exists():
        return []
    out: list[dict] = []
    with file_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
