"""L4.7E — offline, non-mutating replay demonstration.

Proves the historical replay path end to end:

    historical raw messages (whatsapp_messages, direction='in')
        → reconstruct_burst()            chronological, inbound only
        → semantic interpreter version N (any callable; a stub here)
        → TurnEvidence
        → evaluation against corpus truth
        → NO CRM state mutation

Run (read-only; requires DATABASE_URL pointing at a NON-production database):

    DATABASE_URL=postgresql+psycopg://crm:***@localhost:5432/crm_test \
        python tests/semantic_corpus/replay_demo.py --thread 2037

The script opens a read-only session, never writes, never commits, and never imports
ConversationEngine. It exists to show that a future interpreter can be scored against real
historical conversations without touching the CRM.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

from semantic_corpus.evaluation import (  # noqa: E402
    evaluate_case,
    load_corpus,
    reconstruct_burst,
)


def fetch_inbound(thread_id: int) -> list:
    """Read inbound rows for a thread. SELECT only — no write, no commit."""
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.models import WhatsAppMessage

    db = SessionLocal()
    try:
        rows = db.execute(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.thread_id == thread_id)
            .order_by(WhatsAppMessage.timestamp, WhatsAppMessage.id)
        ).scalars().all()
        # Detach plain copies so nothing can be flushed back by accident.
        from types import SimpleNamespace
        return [
            SimpleNamespace(id=r.id, direction=r.direction, text=r.text,
                            timestamp=r.timestamp, message_type=r.message_type,
                            wa_message_id=r.wa_message_id)
            for r in rows
        ]
    finally:
        db.rollback()   # explicit: this session never writes
        db.close()


def stub_interpreter(messages: list[str]) -> dict:
    """Placeholder for interpreter version N.

    Returns nothing — the point of the demo is the *path*, not the quality. Replace with a
    real UNDERSTAND pass at L4.7B and the same code scores it.
    """
    return {"turn_evidence": [], "canonical_state": {}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread", type=int, required=True, help="thread id to replay")
    parser.add_argument("--case", help="corpus case id to score the burst against")
    args = parser.parse_args()

    rows = fetch_inbound(args.thread)
    burst = reconstruct_burst(rows)
    print(f"thread {args.thread}: {len(rows)} rows, {len(burst)} inbound messages")
    for i, text in enumerate(burst, 1):
        print(f"  [{i}] {text[:88]}")

    produced = stub_interpreter(burst)
    print(f"interpreter proposed {len(produced.get('turn_evidence') or [])} evidence items")

    if args.case:
        case = next((c for c in load_corpus() if c["id"] == args.case), None)
        if case is None:
            print(f"corpus case {args.case} not found")
            return 1
        result = evaluate_case(case, produced)
        print(f"scored against {case['id']}: tp={result.true_positives} "
              f"fp={result.false_positives} fn={result.false_negatives} "
              f"unsupported={len(result.unsupported_inferences)}")
        for note in result.notes:
            print(f"    - {note}")

    print("NO CRM state was mutated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
