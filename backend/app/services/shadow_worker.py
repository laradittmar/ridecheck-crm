"""L4.7B.2 — bounded in-process worker for the shadow UNDERSTAND pass.

L4.7B measured ~2.4 s mean (p95 3.7 s) of model latency added to *every* crm_test turn,
because the shadow call ran inline. That is unacceptable in front of a customer turn, so
the work is split:

* **synchronously**, on the request thread: the burst, its ids and the bounded context are
  captured — provenance can never be reconstructed later, so it is never deferred;
* **asynchronously**, on one bounded worker thread: the model call and the append-only
  record, neither of which the customer turn depends on.

Bounded means bounded: a fixed-size queue, a single worker, and an explicit drop (counted
and logged) when the queue is full. A shadow backlog must degrade into *fewer shadow
records*, never into memory growth, thread growth, or a slower customer turn.

Nothing here has business authority, writes canonical state, or raises into a caller.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_QUEUE = 32
DEFAULT_JOIN_TIMEOUT = 5.0


@dataclass
class ShadowJob:
    """Everything the deferred call needs, captured at turn time."""
    run: Callable[[], Any]
    thread_id: Optional[int] = None
    burst_id: Optional[str] = None
    meta: dict = field(default_factory=dict)


class ShadowWorker:
    """One daemon thread draining a bounded queue. Idempotent start, safe stop."""

    def __init__(self, max_queue: int = DEFAULT_MAX_QUEUE, name: str = "shadow-understand"):
        self._queue: "queue.Queue[Optional[ShadowJob]]" = queue.Queue(maxsize=max(1, max_queue))
        self._name = name
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stopping = False
        self.submitted = 0
        self.dropped = 0
        self.completed = 0
        self.failed = 0

    # ── lifecycle ────────────────────────────────────────────────────────────

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stopping = False
            self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:                     # stop sentinel
                    return
                job.run()
                self.completed += 1
            except Exception as exc:                # a shadow failure stays in the shadow
                self.failed += 1
                try:
                    logger.warning("CE_SHADOW_WORKER job failed thread_id=%s burst=%s: %s",
                                   getattr(job, "thread_id", None),
                                   getattr(job, "burst_id", None), exc)
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    def stop(self, timeout: float = DEFAULT_JOIN_TIMEOUT) -> None:
        """Ask the worker to finish the queue and exit. Never raises."""
        with self._lock:
            thread, self._stopping = self._thread, True
        if thread is None:
            return
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            thread.join(timeout)
        except Exception:
            pass

    def drain(self, timeout: float = DEFAULT_JOIN_TIMEOUT) -> bool:
        """Block until the queue is empty (tests and shutdown). Returns True if drained."""
        deadline = threading.Event()
        waiter = threading.Thread(target=lambda: (self._queue.join(), deadline.set()),
                                  daemon=True)
        waiter.start()
        return deadline.wait(timeout)

    # ── submission ───────────────────────────────────────────────────────────

    def submit(self, job: ShadowJob) -> bool:
        """Queue one job. Returns False when it was dropped — never blocks, never raises."""
        try:
            self._ensure_thread()
            self._queue.put_nowait(job)
            self.submitted += 1
            return True
        except queue.Full:
            self.dropped += 1
            try:
                logger.warning("CE_SHADOW_DROPPED queue_full thread_id=%s burst=%s dropped=%s",
                               job.thread_id, job.burst_id, self.dropped)
            except Exception:
                pass
            return False
        except Exception as exc:
            self.dropped += 1
            try:
                logger.warning("CE_SHADOW_DROPPED error thread_id=%s: %s", job.thread_id, exc)
            except Exception:
                pass
            return False

    def stats(self) -> dict:
        return {"submitted": self.submitted, "dropped": self.dropped,
                "completed": self.completed, "failed": self.failed,
                "queued": self._queue.qsize()}


_WORKER: Optional[ShadowWorker] = None
_WORKER_LOCK = threading.Lock()


def get_worker(max_queue: int = DEFAULT_MAX_QUEUE) -> ShadowWorker:
    """The process-wide shadow worker. One thread for the whole process, not per turn."""
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None:
            _WORKER = ShadowWorker(max_queue=max_queue)
        return _WORKER


def reset_worker() -> None:
    """Test helper: stop and forget the process worker."""
    global _WORKER
    with _WORKER_LOCK:
        worker, _WORKER = _WORKER, None
    if worker is not None:
        worker.stop(timeout=2.0)
