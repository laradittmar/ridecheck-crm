"""L4.7C.4A — one semantic interpretation per inbound burst, shared by every consumer.

L4.7C.4 left a single architectural gap: the reconciled scheduling interface was
authoritative, but the semantic interpretation ran *after* the customer turn had already
been answered. A reading the model got right could not reach the turn it was about.

The naive fix — call the model synchronously for CE and keep the async shadow call for the
record — doubles cost and, worse, allows the record to disagree with the decision. This
module makes that impossible by construction:

    RAW BURST → TurnSemanticEvidence.start() → exactly one interpret() → every consumer

`start()` dispatches the interpretation once. `get()` returns *that* result to whoever asks
— the customer turn (bounded by a timeout), the claim projection, the reconciler, the shadow
recorder. Whoever arrives first pays for the wait; nobody pays twice. The run is guarded so
that even a simultaneous inline `get()` and worker execution produce **one** model call:
`calls` is the proof, and a test asserts it never exceeds 1.

Timeout is not an error path with a clever fallback — it is simply *no semantic evidence*.
The caller then has deterministic evidence only, which is the behaviour certified in
L4.7C.4. Nothing here guesses, retries, or lets a late result change a turn that has already
been decided.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Bounded by design: ~1.6x the p95 measured in L4.7B (3.7 s). Long enough that a normal
# call lands, short enough that a stalled model degrades to deterministic rather than
# holding a customer turn open.
DEFAULT_SYNC_TIMEOUT = 6.0


class TurnSemanticEvidence:
    """The single interpretation of one burst. At most one model call, ever."""

    __slots__ = ("_run", "_lock", "_done", "_result", "_started", "_ran", "_calls",
                 "_dispatched", "_timeouts", "thread_id", "burst_id")

    def __init__(self, run: Callable[[], Any], *, thread_id: Optional[int] = None,
                 burst_id: Optional[str] = None) -> None:
        self._run = run
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._result: Any = None
        self._started = False
        self._ran = False
        self._calls = 0
        self._dispatched = True
        self._timeouts = 0
        self.thread_id = thread_id
        self.burst_id = burst_id

    # ── the single execution ─────────────────────────────────────────────────

    def _execute(self) -> None:
        """Run the interpretation at most once, whichever thread gets here first."""
        with self._lock:
            if self._ran:
                return
            self._ran = True
            self._calls += 1
        try:
            self._result = self._run()
        except Exception as exc:            # a failed interpretation is *no* evidence
            self._result = None
            try:
                logger.warning("L4.7C.4A interpretation failed thread_id=%s burst=%s: %s",
                               self.thread_id, self.burst_id, exc)
            except Exception:
                pass
        finally:
            self._done.set()

    def execute(self) -> None:
        """Run the one interpretation on this thread. Idempotent; safe to race."""
        self._execute()

    def start(self, submit: Optional[Callable[[Callable[[], None]], bool]] = None) -> None:
        """Dispatch the one interpretation. Idempotent; never raises.

        `submit` hands the work to the bounded shadow worker. When it declines (queue full)
        nothing is run here — a turn that actually needs the evidence will run it inline in
        `get()`, and a turn that does not simply has no semantic record. A shadow backlog
        must never turn into a slower customer turn.
        """
        with self._lock:
            if self._started:
                return
            self._started = True
            deferred = submit is not None
        if not deferred:
            self._execute()
            return
        try:
            accepted = bool(submit(self._execute))
        except Exception:
            accepted = False
        if not accepted:
            self._dispatched = False

    def get(self, timeout: Optional[float] = None) -> Any:
        """The one result, or None. Waits at most `timeout` seconds; never raises.

        When dispatch was declined, the caller that actually needs the evidence runs it on
        its own thread — bounded by the interpreter's own HTTP timeout — rather than waiting
        for a worker that was never given the job.
        """
        if self._done.is_set():
            return self._result
        if not self._dispatched or not self._started:
            self._execute()
            return self._result
        if timeout is None:
            self._done.wait()
            return self._result
        if not self._done.wait(timeout):
            self._timeouts += 1
            try:
                logger.info("L4.7C.4A semantic wait timed out thread_id=%s burst=%s "
                            "timeout_s=%s — deterministic evidence only",
                            self.thread_id, self.burst_id, timeout)
            except Exception:
                pass
            return None                      # absent evidence, not a guess
        return self._result

    # ── proof surface (tests and observability) ──────────────────────────────

    @property
    def calls(self) -> int:
        """How many model calls this burst has cost. The single-call invariant."""
        return self._calls

    @property
    def ready(self) -> bool:
        return self._done.is_set()

    @property
    def timed_out(self) -> bool:
        return self._timeouts > 0
