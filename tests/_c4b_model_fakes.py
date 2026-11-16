"""R6-C4B / R6-C4B-R1 — the ONE shared adapter for the historical reply-model fakes.

Before R6-C4B a test faked the reply model once (a canned decision JSON through
`urllib.request.urlopen` or `_call_openai`) and its `reply` field was the customer-facing
text. Since R6-C4B that body is the interpreter's and is discarded. Under R6-C4B-R1 the
customer message is composed from typed segments — an optional claim-free natural BRIDGE
from a separate generator call (approved only by an independent verifier call), then the
engine's deterministic canonical fact block and next action. A historical fake supplies
neither bridge call.

This module gives every historical fake both, deterministically and with no provider
contact, installed once per session by `tests/conftest.py` (after collection, so no test's
own module stubbing is disturbed):

* `replay_generate` — the generator proposes exactly the historical fake's body (the
  interpreter body the engine kept for its tripwire) as the bridge. A fake that supplied no
  body makes the generator fail.
* `replay_verify` — a DETERMINISTIC STAND-IN for the bridge verifier. Every historical body
  was written as a fact-bearing reply under the pre-R1 architecture, so none is a claim-free
  bridge: the stand-in reports each one as operational content (category OTHER) without
  reading it. The pipeline therefore regenerates once, rejects again, and the historical
  suites observe the canonical message alone — exactly what the owner's composition rule
  sends when no bridge is approved.

The stand-in parses nothing and authorises nothing. Bridge approval itself is proven by
`test_r6_c4b_verified_generation` with scripted, parser-free stubs, and by the bounded
real-provider evaluation.

A test that needs the real seams or scripted stubs sets instance attributes
(`eng._call_generator = ...`), which take precedence over the class-level replay, or binds
`ORIGINALS[...]` to the instance.
"""
from __future__ import annotations

import json
import sys

ORIGINALS: dict = {}
_INSTALLED = {"done": False}


class NoHistoricalBody(RuntimeError):
    """The historical fake supplied no reply body: the replayed generator fails."""


def replay_generate(self, messages, *, timeout):
    body = getattr(self, "_discarded_ai_reply", None)
    if not isinstance(body, str) or not body.strip():
        raise NoHistoricalBody("the historical fake supplied no reply body")
    self._replay_generator_calls = getattr(self, "_replay_generator_calls", 0) + 1
    return json.dumps({"bridge": body}, ensure_ascii=False)


def replay_verify(self, messages, *, timeout):
    self._replay_verifier_calls = getattr(self, "_replay_verifier_calls", 0) + 1
    return json.dumps({
        "purely_conversational": False, "contains_operational_claim": True,
        "findings": [{"category": "OTHER", "span": "historical fact-bearing reply body"}],
        "uncertain": []}, ensure_ascii=False)


def install() -> bool:
    """Install the replay pair on the already-imported engine class (idempotent)."""
    mod = sys.modules.get("app.services.conversation_engine")
    if mod is None:
        return False
    cls = mod.ConversationEngine
    if not ORIGINALS:
        ORIGINALS["_call_generator"] = cls.__dict__["_call_generator"]
        ORIGINALS["_call_verifier"] = cls.__dict__["_call_verifier"]
    cls._call_generator = replay_generate
    cls._call_verifier = replay_verify
    _INSTALLED["done"] = True
    return True
