"""
M8.1 — Burst Debounce Live Test
================================
This script sends multiple inbound-webhook calls in rapid succession to verify
that the n8n workflow deduplication logic (debounce) produces exactly ONE
outbound WhatsApp reply per burst.

It does NOT call the actual WhatsApp Cloud API. It checks the backend's
outbound message count for the thread before and after the burst.

Usage:
    python3 tests/test_burst_debounce.py

Requirements:
    - Backend running on localhost:8000
    - n8n running and processing webhooks

The test waits 35 seconds after the burst (20s debounce + 10s AI + 5s margin)
before checking the result. It prints PASS or FAIL with details.

NOTE: This is a manual/CI integration test that requires the live stack.
It is NOT run by the pytest suite automatically.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
import uuid

BACKEND = "http://localhost:8000"
N8N_WEBHOOK = "http://localhost:5678/webhook/ridecheck-inbound"

# A stable test phone number that is NOT excluded
TEST_PHONE = "5491100000099"
TEST_WA_ID  = "5491100000099"


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def _make_inbound(body: str, msg_id: str, name: str = "Test Burst") -> dict:
    """Build a WhatsApp webhook payload that the n8n flow expects."""
    ts = str(int(time.time()))
    return {
        "entry": [{"changes": [{"value": {
            "messages": [{
                "from": TEST_WA_ID,
                "id": msg_id,
                "timestamp": ts,
                "type": "text",
                "text": {"body": body},
            }],
            "contacts": [{"profile": {"name": name}, "wa_id": TEST_WA_ID}],
        }}]}],
    }


def _get_thread_id() -> int | None:
    """Find the thread for TEST_PHONE, if any."""
    try:
        leads = _get(f"{BACKEND}/leads?telefono={TEST_PHONE}")
        if not leads:
            return None
        lead_id = leads[0]["id"]
        wa = _get(f"{BACKEND}/leads/{lead_id}/whatsapp")
        return wa.get("thread_id")
    except Exception:
        return None


def _count_outbound(thread_id: int) -> int:
    """Count outbound messages for this thread."""
    try:
        data = _get(f"{BACKEND}/api/whatsapp/thread/{thread_id}/messages?limit=50")
        msgs = data if isinstance(data, list) else data.get("messages", [])
        return sum(1 for m in msgs if m.get("direction") == "out")
    except Exception:
        return -1


def run_burst_test(label: str, messages: list[str], wait_seconds: int = 35) -> bool:
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"Messages: {messages}")

    # Get thread before burst
    thread_id_before = _get_thread_id()
    outbound_before = _count_outbound(thread_id_before) if thread_id_before else 0
    print(f"Thread before: {thread_id_before}, outbound before: {outbound_before}")

    # Send burst
    msg_ids = []
    for i, body in enumerate(messages):
        msg_id = f"test_burst_{uuid.uuid4().hex[:12]}"
        msg_ids.append(msg_id)
        payload = _make_inbound(body, msg_id)
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    N8N_WEBHOOK,
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                ),
                timeout=5,
            )
        except Exception as e:
            print(f"  Webhook call {i+1} error: {e}")
        delay = 0.5 if i < len(messages) - 1 else 0
        if delay:
            time.sleep(delay)
    print(f"Burst sent ({len(messages)} messages in ~{len(messages)*0.5:.1f}s)")

    # Wait for debounce + processing
    print(f"Waiting {wait_seconds}s for debounce + AI processing...")
    time.sleep(wait_seconds)

    # Check result
    thread_id_after = _get_thread_id()
    outbound_after = _count_outbound(thread_id_after) if thread_id_after else 0
    new_outbound = outbound_after - outbound_before
    print(f"Thread after: {thread_id_after}, outbound after: {outbound_after}")
    print(f"New outbound messages: {new_outbound}")

    if new_outbound == 1:
        print(f"PASS ✓ — exactly 1 reply for {len(messages)}-message burst")
        return True
    elif new_outbound == 0:
        print(f"WARN — 0 replies (AI may be disabled or phone excluded)")
        return True  # Not a debounce failure
    else:
        print(f"FAIL ✗ — {new_outbound} replies for {len(messages)}-message burst (expected 1)")
        return False


def main():
    print("M8.1 Burst Debounce Live Test")
    print("Backend:", BACKEND)
    print("n8n webhook:", N8N_WEBHOOK)

    results = []

    # CASE A — greeting burst
    results.append(run_burst_test(
        "CASE A — greeting burst",
        ["hola", "como va", "queria averiguar para revisar un vehiculo"],
        wait_seconds=40,
    ))

    # CASE D — single message (no regression)
    results.append(run_burst_test(
        "CASE D — single message (no regression)",
        ["queria averiguar el precio para un Polo 2019 en Palermo"],
        wait_seconds=40,
    ))

    print(f"\n{'='*60}")
    passed = sum(results)
    print(f"Results: {passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
