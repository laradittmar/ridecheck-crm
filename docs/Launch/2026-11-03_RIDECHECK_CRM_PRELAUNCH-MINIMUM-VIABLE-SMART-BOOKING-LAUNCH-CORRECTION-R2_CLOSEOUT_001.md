PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R2

Date: 2026-11-03. Implementation only, on /opt/ridecheck-crm-minimum-launch, branch
integration/prelaunch-minimum-launch, HEAD 6f20587da9af0d49fbe0b20078cff555521c293b (unchanged).
Nothing staged, committed, pushed, built, deployed or migrated live. No live flag, allowlist,
Compose file, environment or live database row was changed. Meta was not contacted and no
WhatsApp message was sent. /opt/ridecheck-crm-integration and the deferred return-journey
candidate were only read (fingerprints, §14). Smart Booking remains NO-GO.

Inputs read first:
  docs/Launch/2026-11-03_…-CORRECTION-R1_CLOSEOUT_001.md
  docs/Launch/2026-11-03_…-CORRECTION-R1-INDEPENDENT-REVIEW_AUDIT_001.md   (CORRECTION REQUIRED:
  R1-01 HIGH, R1-02 MEDIUM, R1-03 LOW; corrections C-R1, C-R2, C-R3 of its §18)
Scope: exactly those three findings. The review's should-fix items R1-04 … R1-07 were NOT
implemented (§13).

Post-review documentation correction (milestone …-CONTROLLED-COMMIT, 2026-11-03): the R2
independent review (…-CORRECTION-R2-INDEPENDENT-REVIEW_AUDIT_001.md, ACCEPTED FOR CONTROLLED
COMMIT) found that this closeout overclaimed the shutdown timing (R2R-01) and that four mutant
kills are label-only (R2R-02). The wording in §0, §4, §5.4, §5.5, §11 and §17 was corrected to
match the audit. No code, test, migration or script changed; the evidence numbers are the
ones produced at R2.

================================================================================
0. VERDICT
================================================================================
READY FOR NARROW FRESH INDEPENDENT REVIEW. Smart Booking remains NO-GO; deployment
preconditions P1 (lead 169), P2 (notification-2 retirement SQL, NOT run) and P3 (a
compatibility image at EXPECTED_HEAD 20261103_slot_recovery_job) are unchanged.

  R2-1 HIGH   fixed — every recovery send requires booking_dispatch_allowed() (and no stop
              signal) at eleven boundaries, the last one under a lock immediately before the
              provider thread starts. A refusal ends HANDOFF at once, listed, flags kept,
              never deferred: no recovery text or Flow can be sent while Smart Booking is OFF
              (OUTBOUND_ENABLED=true or not), on the first pass, on a lease-expired resume, on
              a retry, or after the flags return (§3, §5.1, §5.2).
  R2-2 MED    fixed — one application-owned daemon worker; shutdown sets the stop signal
              first (under the send lock), then a bounded 3 s join. No claim and no provider
              call can BEGIN after the signal crosses `_SEND_LOCK` (§5.3). A call already in
              flight is abandoned, not cancelled — stated, measured and handed off (§5.4).
              The signal is set by the FastAPI/Uvicorn shutdown handler, which Uvicorn runs
              only after its graceful drain: SIGTERM does not itself stop recovery (§4,
              R2R-01).
  R2-3 LOW    fixed — dropdown labels escaped once; lead.flag classes are an allowlist with a
              neutral default (§7).
  Evidence: R2 tests 30 + 33 subtests; R1/minimum-launch suites unchanged and green; PG 31/31;
  SQLite and PostgreSQL NEW 0 / GONE 0 against pristine and against R1; 26/26 mutants killed
  (four of them, MR1 MR2 MR3 MR6, are send-equivalent — §11, R2R-02); kit 28/28 + 38/38;
  rehearsal 89/0; live state identical before and after.
  No recovery provider call can begin while Smart Booking is OFF, at any boundary the
  application controls, and none can begin after the application's stop signal crosses
  `_SEND_LOCK`. A process whose cached posture is ON keeps operating until that signal is
  set, which happens only after Uvicorn's graceful drain (§4, R2R-01).

================================================================================
1. ROOT CAUSES
================================================================================
R2-1 (review R1-01, HIGH) — Smart Booking OFF did not stop recovery transport.
  The R1 worker reached the provider through two gates, neither of which reads
  SMART_BOOKING_CE_ENABLED:
    * the OutboundSafetyGate checks the kill switch (OUTBOUND_ENABLED), the allowlist and the
      delivery identity;
    * `enforce_outbound_enabled` inside the real senders checks OUTBOUND_ENABLED.
  `booking_dispatch_allowed()` (the WILD1-F1 policy) was consulted in exactly one worker
  place: the TEXT_SENT branch of `_flow_phase`, before the fresh offer. So:
    * `_text_phase` had no Smart Booking check → the recovery text was sent with SB=false;
    * the SENDING_FLOW resume branch reuses the fresh offer already issued, so neither the
      worker check nor the `OfferLifecycle.issue` backstop was on its path → a lease-expired
      SENDING_FLOW job sent the Booking Flow with SB=false and ended RECOVERED.
  The design error: the R1 worker treated "OUTBOUND is on" as permission to send booking
  traffic. That is the WILD1-F1 defect again, at a new dispatch site.

R2-2 (review R1-02, MEDIUM) — the worker could outlive shutdown.
  `_EXECUTOR = ThreadPoolExecutor(max_workers=1)`: its thread is non-daemon and is joined by
  concurrent.futures' atexit hook; `run_once` and the phases checked no stop signal; there
  was no shutdown handler. A pass in progress at SIGTERM kept claiming and sending (with the
  old, possibly ON, environment) and delayed exit until Docker's SIGKILL.

R2-3 (review R1-03, LOW) — three operator/API-writable values reached markup raw.
  R1 escaped every customer-derived value but not the option text built from
  `_profesional_label(p)` (Profesional.nombre / apellido / cargo) in the edit-latest-revision
  form and the filters form, nor `Agencia.nombre_agencia` in the edit form's agency select
  (it used `_txt`, which trims but does not escape). `lead.flag` (writable through
  PATCH /leads, max_length 40) was interpolated into `class="cardHeaderRow flag-{flag_val}"`.

================================================================================
2. IMPLEMENTATION — exact changes
================================================================================
backend/app/services/booking_slot_recovery.py
  Policy (new section "send refusal"):
    * `send_refusal()` → None or the typed reason WORKER_STOPPING | DISPATCH_OFF. Smart
      Booking is decided ONLY by the canonical `booking_dispatch_allowed()` (no second
      reading of the flag); any exception there fails CLOSED (DISPATCH_OFF).
    * `_guard(stage, claim)`: the deterministic test barrier `_STAGE_HOOK(stage, job_id)`
      (None in production), then `send_refusal()`; a refusal raises `_Halt(reason, stage)`.
    * `_halt(...)`: a refusal is TERMINAL at once — HANDOFF, outcome
      `{TEXT_|FLOW_|}{DISPATCH_OFF|WORKER_STOPPING}`, last_error `AT_<STAGE>`. A job in
      SENDING_FLOW has its fresh offer WITHDRAWN first (if K1 refuses that, FAILED
      HANDOFF_LOCK_CONTENDED with the lead flag raised, as R1's `_fail_safe`). Never a
      release/defer: nothing waits for the flags to come back.
  Boundaries (each a `_guard`; names are the stage names the tests switch at):
    claim            run_once, before every claim: stop → no claim (the pass ends)
    process          after the human flags are ensured, before ANY step of a claimed job —
                     this one covers every lease-expired state (SENDING_TEXT, TEXT_SENT,
                     SENDING_FLOW) and every OFF restart
    text             before the first text attempt
    text_resume      before re-sending a SENDING_TEXT whose identity never crossed the gate
    text_retry       before attempt 2 (after a definite non-delivery)
    text_transport   the last line: inside `bounded_call`, under `_SEND_LOCK`, immediately
                     before the provider thread starts (barrier just before the lock,
                     `text_transport_locked` inside it)
    after_text       TEXT_SENT, before anything of the Flow (replaces R1's single check)
    offer            before the fresh offer is issued
    flow_resume      SENDING_FLOW whose Flow identity never crossed the gate — before the
                     resumed send (R1's unguarded branch)
    flow             before the Flow (fresh or resumed)
    flow_transport   the last line, as text_transport (`flow_transport_locked` inside)
  `bounded_call(fn, kwargs, deadline, *, guard=None, stop=None)`:
    * `guard()` runs under `_SEND_LOCK` and, if it refuses, raises OutboundBlockedError
      (kind RECOVERY_SEND_REFUSED): a DEFINITE pre-dispatch non-send — the gate row is marked
      failed, the transport is never called; `_Deadline.refused` carries the reason and the
      phase raises `_Halt` (so a refusal is never mistaken for a deliverable failure and
      retried);
    * `stop` (the worker's Event) ends the WAIT early: TransportDeadlineExceeded(shutdown=True)
      → the step is UNCERTAIN, exactly as past the 8 s deadline (HANDOFF
      TEXT_/FLOW_TRANSPORT_DEADLINE, last_error TRANSPORT_ABANDONED_AT_SHUTDOWN). The
      provider call is NOT cancelled.
    * unchanged: TRANSPORT_DEADLINE_SECONDS = 8.0, MAX_INFLIGHT_TRANSPORTS = 4, the capacity
      refusal, result/exception delivery.
  `_send_flow` returns ("REFUSED", reason) for a last-line refusal; `_flow_settled` raises
  `_Halt` for it.
  Lifecycle (new section "worker lifecycle"; ThreadPoolExecutor, `slot_recovery_loop` and
  the asyncio import removed):
    * `start_worker()`: one daemon thread `slot-recovery-worker` running `_worker_main(stop)`
      = `while not stop.wait(CHECK_INTERVAL_SECONDS): _run_check()`. Idempotent: a live,
      unstopped worker is returned as is. Each start owns a NEW Event; a worker reads ITS OWN
      (`_PASS.stop`, thread-local), so a restart can never un-stop an old worker still
      finishing (s6b).
    * `stop_worker(timeout=JOIN_TIMEOUT_SECONDS=3.0)`: sets the signal FIRST, under
      `_SEND_LOCK`, then joins for at most `timeout`; repeated calls are harmless; called from
      inside the pass it only signals. Returns whether the thread ended.
    * `_run_check` (unchanged): one SessionLocal per pass, closed in `finally`.
  Ops surface: `recovery_attention` adds SLOT_RECOVERY_DISPATCH_OFF /
  SLOT_RECOVERY_WORKER_STOPPED to HANDOFF and FAILED rows whose outcome or last_error carries
  the refusal (`_refusal_reason`). Rows are listed for the existing 7-day lookback with
  recovery = CONTACT_CUSTOMER.
  Module docstring: the R2 policy and lifecycle written down.

backend/app/main.py
  * startup `_start_slot_recovery_worker()` → `start_slot_recovery_worker()` (sync handler;
    replaces the asyncio task `slot_recovery_loop`);
  * NEW `@app.on_event("shutdown") _stop_slot_recovery_worker()` → `stop_slot_recovery_worker()`.

backend/app/ui/kanban_view.py
  * `_filters_form_html` and `render_edit_latest_revision_form`: professional option text =
    `html_lib.escape(_profesional_label(p), quote=True)`;
  * edit form agency option text = `_esc_txt(a.nombre_agencia)`;
  * NEW `_flag_class(flag_val)`: `flag-<value>` only for a key of FLAG_LABELS, otherwise the
    neutral `flag-other` (no style, same look as an unknown flag had before);
  * card header `class="cardHeaderRow {_flag_class(flag_val)}"`; the table view's flag pill
    uses `_flag_class` and escapes its label (see §9: that view is unreachable today).

tests/test_minimum_launch_r2.py (NEW, 30 tests, 33 subtests) — §6–§9.
tests/test_minimum_launch_pg.py — NEW class PG_R2_DispatchOff (pgo_01, pgo_02), §10.
No other file changed relative to the R1 frozen candidate (byte comparison, §12).

================================================================================
3. RECOVERY STATE TRANSITIONS UNDER EVERY FLAG / SHUTDOWN BOUNDARY
================================================================================
"OFF" = booking_dispatch_allowed() is not True (OUTBOUND_ENABLED may be true).
"STOP" = the worker's stop signal is set. Each row is a committed test (§6).

  job state at the boundary      boundary           result                                     sends
  ─────────────────────────────  ─────────────────  ─────────────────────────────────────────  ─────
  PENDING (due)                  claim + STOP       not claimed; stays PENDING, listed;         0
                                                    next process: OFF → HANDOFF DISPATCH_OFF
  PENDING                        process + OFF      HANDOFF DISPATCH_OFF @PROCESS               0
  PENDING                        process + STOP     HANDOFF WORKER_STOPPING @PROCESS            0
  PENDING                        text               HANDOFF TEXT_<reason> @TEXT; no gate row    0
  SENDING_TEXT (claimed)         text_transport     HANDOFF TEXT_<reason> @TEXT_TRANSPORT;      0
                                                    gate row `failed`, transport never called
  PENDING after a definite fail  text_retry         HANDOFF TEXT_<reason> @TEXT_RETRY;          1 text
                                                    attempt-2 identity never reaches the gate   (the 1st)
  TEXT_SENT                      after_text         HANDOFF FLOW_<reason> @AFTER_TEXT; losing   1 text
                                                    offer untouched (no fresh offer)
  TEXT_SENT                      offer              same, @OFFER                                1 text
  SENDING_FLOW (fresh offer)     flow               HANDOFF FLOW_<reason> @FLOW; fresh offer    1 text
                                                    WITHDRAWN
  SENDING_FLOW                   flow_transport     same, @FLOW_TRANSPORT; Flow gate row        1 text
                                                    `failed`, transport never called
  lease-expired SENDING_TEXT     process + OFF      HANDOFF DISPATCH_OFF                        0
  lease-expired SENDING_TEXT     text_resume        HANDOFF TEXT_DISPATCH_OFF @TEXT_RESUME      0
  lease-expired TEXT_SENT        process + OFF      HANDOFF DISPATCH_OFF; no fresh offer        0
  lease-expired SENDING_FLOW     process + OFF      HANDOFF DISPATCH_OFF; fresh offer WITHDRAWN 0
  lease-expired SENDING_FLOW     flow_resume / flow HANDOFF FLOW_DISPATCH_OFF; WITHDRAWN         0
                                 / flow_transport
  text transport blocked         STOP               wait ends ≤ 50 ms: HANDOFF                  the 1
                                                    TEXT_TRANSPORT_DEADLINE /                   in flight
                                                    TRANSPORT_ABANDONED_AT_SHUTDOWN; no Flow,   (not
                                                    no retry; call abandoned, not cancelled     cancelled)
  any HANDOFF above              flags return (ON)  terminal jobs are never claimed: nothing    0
  both ON                        every boundary     the R1 path, unchanged: claim → process →   1 + 1
                                                    text → text_transport → after_text →
                                                    offer → flow → flow_transport → RECOVERED
  "<reason>" = DISPATCH_OFF or WORKER_STOPPING. Every HANDOFF keeps the human flags
  (thread needs_human, lead necesita_humano — raised at admission or by the worker before
  any boundary), is listed with SLOT_RECOVERY_HANDOFF + SLOT_RECOVERY_DISPATCH_OFF /
  SLOT_RECOVERY_WORKER_STOPPED, keeps the job's losing_offer_id / recovery_identity (the
  SLOT_CONFLICT evidence), books nothing.
  A SENDING_FLOW job met by OFF at `process` is handed off BEFORE its gate row is inspected:
  if a lost claimant's Flow had in fact been accepted, R1 would have recorded RECOVERED; R2
  records HANDOFF and withdraws that offer (a person continues; Smart Booking is off anyway,
  so the Flow could not book). Conservative by design.

Policy written down (module docstring): a job met by OFF or by shutdown ends HANDOFF; it is
never deferred, and never sent when the flags or the application return.
Shutdown BEFORE a claim deliberately leaves the job PENDING (nothing of it has started): the
next process handles it under ITS posture — OFF → HANDOFF (s1, s5), ON → the normal R1
recovery.

================================================================================
4. WORKER STARTUP / SHUTDOWN OWNERSHIP
================================================================================
  owner          the FastAPI application: startup handler `_start_slot_recovery_worker`,
                 shutdown handler `_stop_slot_recovery_worker` (main.py).
  thread         one, `slot-recovery-worker`, daemon=True. It cannot keep the process alive.
  startup        idempotent (s6: three starts → one thread). After a stop, a start creates a
                 NEW thread with a NEW Event; the old thread keeps its set Event (s6b).
  shutdown       1. under `_SEND_LOCK`: set the module and the worker's Event;
                 2. bounded join (3.0 s);
                 3. a thread still in a pass is abandoned (daemon) — it can begin no further
                    provider call and no further claim; its lease expires if it never
                    records.
  stop checks    run_once before each claim; `_guard` at every boundary above; the last
                 check under the lock; `bounded_call`'s wait.
  resources      one SessionLocal per pass, closed in `finally` (also on an internal error,
                 s9); the executor is gone; transport threads are daemon and never touch the
                 database.
  Docker         (corrected after review, R2R-01) KILL.sh's `docker stop` sends SIGTERM and
                 gives the container approximately 10 s (Docker's default) before SIGKILL.
                 SIGTERM does NOT itself stop recovery activity. The stop signal is set by
                 the FastAPI/Uvicorn shutdown handler `_stop_slot_recovery_worker`, and
                 Uvicorn 0.30.6 invokes that handler only after it has closed its listeners
                 and drained active connections and tasks. The container command
                 (`uvicorn app.main:app --host 0.0.0.0 --port 8000`) sets no
                 `--timeout-graceful-shutdown`, so the drain has no explicit Uvicorn timeout;
                 Docker's SIGKILL is the only bound, and if it comes first the handler never
                 runs. During the drain the old process keeps operating under its cached ON
                 posture: the worker can still claim and begin recovery sends. The review
                 measured a SIGTERM with a six-second request in flight: the shutdown
                 handler ran approximately 5.2 s after the signal. No maximum shorter than
                 Docker's current 10 s stop window is claimed.
                 What does hold: once the stop signal crosses `_SEND_LOCK`, no new recovery
                 provider call can begin; a provider request that had already started may
                 still complete (§5.4). Emergency OFF is complete only when the old
                 container has exited AND the replacement container is ready with the
                 required flags verified OFF (KILL.sh's exit 0). This is an operational
                 limitation of the old ON process's drain window, not a Smart Booking gate
                 bypass: the new process starts OFF and refuses every recovery send.

================================================================================
5. PROOFS
================================================================================
5.1 SMART_BOOKING_CE_ENABLED=false prevents BOTH text and Flow sends with OUTBOUND_ENABLED=true
  * The Meta fakes set OUTBOUND_ENABLED=true (asserted in a1); Smart Booking is switched
    through the patched `get_settings`, i.e. read by the canonical
    `booking_dispatch_allowed()`.
  * a1 PENDING: zero provider calls, zero gate rows, HANDOFF DISPATCH_OFF, listed, flags
    up, no booking; two later ON passes: still zero.
  * a2 lease-expired SENDING_TEXT / TEXT_SENT / SENDING_FLOW: zero calls, no new gate row,
    HANDOFF, the fresh offer WITHDRAWN; ON afterwards: zero.
  * b1 / c1 / d1 / d2 / e1 switch OFF at every boundary in turn: no later provider call
    begins (edge call list), and the boundary that refused is recorded (AT_<STAGE>).
  * MR9 (the policy ignores Smart Booking — R1's behaviour) is killed by a1 a2 b1 c1 d1 d2 e1 g1 h1 h2 s1 s5.
  * PG16: pgo_01 (PENDING and lease-expired SENDING_FLOW, OUTBOUND on, SB off): zero sends,
    HANDOFF, offer WITHDRAWN, listed with SLOT_RECOVERY_DISPATCH_OFF; ON afterwards: zero.
5.2 An expired lease cannot resume a Flow while Smart Booking is OFF
  * Two independent refusals precede the resumed send: `process` (before the gate row is
    even inspected) and `flow_resume`; then `flow` and the last line.
  * d1 switches OFF at each of process / flow_resume / flow / flow_transport on a real
    lease-expired SENDING_FLOW job: zero Flow sends, no second offer, HANDOFF, WITHDRAWN.
  * s5 does it through the REAL daemon worker with Smart Booking OFF from the start (the
    "restart after a forced termination" shape) for SENDING_TEXT, TEXT_SENT, SENDING_FLOW.
  * MR3 (no check on the resume) — killed by d1; MR2 (no check before the Flow) — killed by
    c1 d1 s3.
5.3 Shutdown cannot begin another provider call
  * Every provider call goes through `bounded_call`; its last check and the provider-thread
    start are ONE critical section under `_SEND_LOCK`; `stop_worker` sets the signal inside
    the same lock. So for any call, either it started before `stop_worker` returned (it is
    the in-flight call), or it sees the signal and is refused. s10 proves the ordering with a
    barrier inside the lock: a stopper thread started there does not return until the
    provider thread has started; after it returns, the Flow is refused (AT_AFTER_TEXT) and
    every edge call timestamp precedes the stop.
  * s2 / s3 stop at each boundary: no later call begins (`assert_nothing_after_stop`).
  * MR4 (stop ignored) — killed by s1 s2 s3 s6b s9 s10; MR11 (signal set outside the lock) — killed by s10;
    MR6 (no check before the retry) — killed by e1.
5.4 A provider call already in progress — honest account
  * It is NOT cancelled: Python cannot interrupt a blocking socket operation inside urllib.
  * At the stop the worker stops WAITING for it (≤ 50 ms poll): s4 measured
    SHUTDOWN_WITH_BLOCKED_TRANSPORT_SECONDS=0.066 against an 8 s deadline; the job is HANDOFF
    TEXT_TRANSPORT_DEADLINE / TRANSPORT_ABANDONED_AT_SHUTDOWN (uncertain — it may still be
    delivered); no Flow and no retry follow; the transport slot stays taken until the call
    ends by itself (inflight_transports() = 1), its late result is discarded and nothing is
    recorded for it (outbound rows unchanged).
    (Post-review, R2R-05: 0.066 s is one measurement, not a maximum. Over 20 independent
    review runs the text stop peaked at 0.064 s; the Flow stop had a median of 0.057 s and a
    maximum of approximately 1.0 s (0.9998 s, 1 of 20, under shared-host load). These times
    are measured from the stop signal, not from SIGTERM (§4).)
  * The thread running it is a daemon: it cannot hold the process (s8: a pass blocked for
    30 s in a NON-provider wait; PROCESS_EXIT_SECONDS=0.930 with STOP_SECONDS 0.201, ENDED
    False). A Flow in flight keeps its offer live (it may be on the handset); Smart Booking
    OFF then refuses its confirmation at the Data Exchange OFF switch.
  * If the process is force-terminated before the HANDOFF is written, the job keeps a lease
    that expires (60 s); the next process re-checks the policy before any resumed step (s5).
5.5 Race boundaries, accounted
  * In production `get_settings()` is lru_cached: SMART_BOOKING_CE_ENABLED cannot change
    inside a running process. "OFF mid-recovery" is a container recreate: SIGTERM, then
    Uvicorn's graceful drain (during which the old process still operates under its cached
    ON posture), then the shutdown handler's stop signal (§4) — or SIGKILL at approximately
    10 s, whichever comes first — and then a NEW process that starts OFF (process boundary). The tests switch
    the effective flag at every boundary anyway, through the barrier, not by timing.
  * The stop signal has no window between the last check and the provider-thread start
    (one lock). Between `_guard` and the next `_guard` no provider call exists.
  * Not covered by any lock: a provider call that ALREADY started (5.4).

================================================================================
6. TESTS — tests/test_minimum_launch_r2.py (production worker, fakes at the network edge)
================================================================================
  A a1 pending · a2 every lease-expired state (3 subtests)
  B b1 OFF at claim / process / text / text_transport / text_transport_locked (5)
  C c1 OFF at after_text / offer / flow / flow_transport / flow_transport_locked (5)
  D d1 resumed Flow: process / flow_resume / flow / flow_transport (4) ·
    d2 resumed text: process / text_resume / text_transport (3)
  E e1 OFF before the retry · e2 the retry with the gate ON is unchanged
  F f1 both ON: the exact boundary sequence, RECOVERED, flags cleared · f2 resume ON
  G g1 the emergency-OFF posture (OUTBOUND unset + SB false) for all four states, then
    three ON passes: zero sends, nothing stale
  H h1 Meta retries the original delivery with SB ON after the halt: the R1 replay
    (BookingSlotConflictError), one job, no send, no booking, offers unchanged ·
    h2 the retry while SB is OFF: refused before the authority (not SUCCESS), no send
  S s1 before claim · s2 after claim before text (3) · s3 after text before Flow (4) ·
    s4 blocked transport · s5 expired lease + restart OFF (3) · s6 repeated startup ·
    s6b restart while the old pass is still finishing · s7 repeated shutdown · s8
    process-exit harness (subprocess) · s9 Session closure (tracked Session class, normal
    and internal-error pass) · s10 no send after the signal (lock ordering) · s11 the
    application owns the lifecycle (main.py handlers; no executor)
  X x1 edit form options · x2 filters form option · x3 ordinary labels unchanged ·
    x4 flag class allowlist (known, hostile, quotes, angle brackets, ampersand, case,
    prefix tricks, empty, None, non-string) on the rendered card · x6 the R1 customer-field
    test (e1) re-run inside — no double escaping
  Lifecycle tests wait on events raised by the barrier (a pass reaching its next claim), not
  on sleeps or on database polling beside the worker.
  Result: 30 passed, 33 subtests passed (SQLite, sealed container); with the R1 and minimum-launch suites 119 passed, 0 failed.

================================================================================
7. HTML ESCAPING AND THE FLAG ALLOWLIST
================================================================================
  * professional option text: `Q"'><svg/onload=pwn(7)><b>Z</b> & <i>` renders
    `Q&quot;&#x27;&gt;&lt;svg/onload=pwn(7)&gt;&lt;b&gt;Z&lt;/b&gt; &amp; &lt;i&gt;` — once, in
    both forms; the agency option likewise; the `value="<id>"` attributes are integers.
  * ordinary labels: "Ana Pérez (Inspectora)" unchanged; "Autos José & Hijos" →
    "Autos José &amp; Hijos" (the correct single escape); select names and `selected`
    unchanged.
  * lead.flag: only the six FLAG_LABELS keys produce `flag-<KEY>` (exact match, case
    sensitive); anything else → `flag-other`, which has no style rule (an unknown flag had
    none before either). The visible flag label was already escaped by R1 (`_t`).
  * R1's e1 (every customer-derived value escaped exactly once, 0 double-escaped) passes
    unchanged, standalone and inside x6.
  * MR7 (unescaped dropdown label) — killed by x1; MR8 (arbitrary flag class) — killed by x4.

================================================================================
8. TARGETED, SQLITE AND POSTGRESQL RESULTS
================================================================================
8.1 Targeted (frozen R2 snapshot 4c55906dac3b637cffde700cf2a3b8e05a010c08a1dd8925ea21d76593642751,
    sealed container ridecheck-crm-test:l4.7b2, --network none)
  tests/test_minimum_launch_r2.py                        30 passed, 33 subtests
  + test_minimum_launch_r1.py + test_minimum_launch.py   119 passed, 33 subtests, 0 failed
    (the R1 crash-window W1–W9, lease, retry, transport-deadline L1–L7, flag-ownership,
     ops-attention, detail-persistence G1–G8, HTML E1–E4, lead-identity K1–K7 and fresh-IV M1
     suites, plus the minimum-launch A/C/D… suites — operator-notification C01, lead identity
     D01–D05, Data Exchange)
  measured: SHUTDOWN_WITH_BLOCKED_TRANSPORT_SECONDS=0.066 · PROCESS_EXIT_SECONDS=0.930
            (STOP_SECONDS 0.201, ENDED False) · R1's DX latency test l1 unchanged (passes)
  tests/test_minimum_launch_pg.py on PostgreSQL 16       19 passed + 12 subtests = 31 junit
    cases, 0 failed (R1's 17 + pgo_01, pgo_02; includes the lead-identity migration and
    concurrent-upsert, recovery K1 / two-worker and recovery-migration tests)
  scripts/emergency_off/test_kit.sh 28/28 · test_kit_r1.sh 38/38 (kit files byte-identical
    to R1)
  sealed rehearsal /opt/ridecheck-builds/emergency-off-rehearsal-r2-20261103T203831Z:
    SUMMARY 89 passed, 0 failed; live containers identical (id, image, restarts, start time);
    every rce-* resource removed. (It runs the deployed image, not candidate code; re-run
    because the prompt asks for it, the kit being unchanged.)

8.2 SQLite — full suite (../tests), one container per side
  PRISTINE  (HEAD 6f20587, R1's baseline run, same image)   62 failed, 7136 passed, 9 errors
  R1 CANDIDATE (49204628…2964)                              62 failed, 7226 passed, 675 skipped, 9 errors, 7129 subtests
  R2 CANDIDATE (4c55906d…2751)                              62 failed, 7256 passed, 677 skipped, 9 errors, 7162 subtests (7:12)
    (+30 passed = the R2 tests; +2 skipped = pgo_01/pgo_02 without PostgreSQL)
  unique failing identities (failures + errors):
    pristine vs R2   pristine 71 · R2 71 · NEW 0 · GONE 0 · COMMON 71 · messages differ 0
    R1 vs R2         R1 71 · R2 71 · NEW 0 · GONE 0 · COMMON 71 · messages differ 0

8.3 PostgreSQL 16 — the full PG-gated inventory (27 files, one per process, fresh clones of
    the R2-§5 templates built from the live schema-only dump; disposable r1h-* container and
    --internal network, removed)
  junit cases   pristine 778 · R1 795 · R2 797 (+2 = pgo_01, pgo_02); 26 F + 4 E on each side
  per file (R2): identical F/E counts to the R1 table for every file; test_minimum_launch_pg
    0 F / 31
  unique failing identities:
    pristine vs R2   30 · 30 · NEW 0 · GONE 0 · COMMON 30 · 21 messages differ — exactly R1's
                     21 (the inherited stale head literal ×17; four strict-schema assertions
                     whose captured migration output includes the two new revisions' lines)
    R1 vs R2         30 · 30 · NEW 0 · GONE 0 · COMMON 30 · messages differ 0
  No assertion was weakened, deleted, skipped or changed; no existing test file other than
  test_minimum_launch_pg.py (additions only) was touched.

================================================================================
9. OBSERVED, NOT CHANGED
================================================================================
  * `render_table_page` (kanban_view.py) raises TypeError on the PRISTINE tree
    (`_filters_form_html() got an unexpected keyword argument 'turno_fecha_from'`) and has
    no caller in backend/app. Its flag pill now uses `_flag_class`, but it cannot be
    rendered, so no test claims it. Not fixed (out of scope).
  * The test fixture `seed_live_offer` issues the losing offer outside a Smart Booking ON
    patch, so `BOOKING_OFFER_ISSUED_WHILE_DISPATCH_OFF` is logged by the FIXTURE in every
    seeded test (also in R1's suites). It is not the worker: the worker's offer boundary
    refuses before `OfferLifecycle.issue` (c1 @OFFER: offers stay ["ISSUED"]).
  * Harness incidents (no production effect): (1) a first test draft called stop_worker from
    a barrier that runs under _SEND_LOCK and deadlocked; its container (10efeeb1cf99, image
    ridecheck-crm-test:l4.7b2, this worktree mounted read-only) was identified by image and
    mount and stopped; the barrier was split into `<stage>` (before the lock) and
    `<stage>_locked` (inside it). (2) Lifecycle tests first polled the SQLite StaticPool
    connection beside the worker thread and flaked; they now wait on barrier events. (3) A
    hook calling stop_worker from inside the worker raised "cannot join current thread";
    stop_worker now only signals when called from the worker itself (production change,
    covered by s9).

================================================================================
10. POSTGRESQL R2 TESTS (tests/test_minimum_launch_pg.py, PG_R2_DispatchOff)
================================================================================
  pgo_01  PENDING + SB off (OUTBOUND on): zero sends, HANDOFF DISPATCH_OFF @PROCESS; ON pass:
          zero. Lease-expired SENDING_FLOW (a real dead claimant) + SB off: zero, HANDOFF,
          offers [SUPERSEDED, WITHDRAWN], reasons [SLOT_RECOVERY_HANDOFF,
          SLOT_RECOVERY_DISPATCH_OFF]; ON pass: zero.
  pgo_02  the real daemon worker against PG16, stop at after_text: exactly the recovery
          text, no Flow, HANDOFF FLOW_WORKER_STOPPING @AFTER_TEXT; every Session it opened
          was closed.

================================================================================
11. MUTATION RESULTS (each on a fresh copy of the frozen R2 snapshot; R2 + R1 +
    minimum-launch SQLite suites; KILLED = at least one behavioural failure)
================================================================================
  MR1_no_check_before_text             KILLED  4 failed   test_b1 test_d2 test_e1 test_s2
  MR2_no_check_before_flow             KILLED  3 failed   test_c1 test_d1 test_s3
  MR3_no_check_on_flow_resume          KILLED  1 failed   test_d1
  MR4_send_after_shutdown              KILLED  11 failed  test_s1 test_s10 test_s2 test_s3 test_s6b test_s9
  MR5_non_daemon_worker                KILLED  3 failed   test_s6 test_s6b test_s8
  MR6_no_check_before_retry            KILLED  1 failed   test_e1
  MR7_unescaped_dropdown_label         KILLED  1 failed   test_x1
  MR8_arbitrary_flag_class             KILLED  1 failed   test_x4
  MR9_policy_ignores_smart_booking     KILLED  29 failed  test_a1 test_a2 test_b1 test_c1 test_d1 test_d2 test_e1 test_g1 test_h1 test_h2 … (+2)
  MR10_no_last_line_check              KILLED  10 failed  test_b1 test_c1 test_d1 test_d2 test_f1 test_s10 test_s2 test_s3
  MR11_stop_outside_send_lock          KILLED  1 failed   test_s10
  MR12_wait_ignores_stop               KILLED  3 failed   test_l2 test_s4 test_s6
  MR13_refusal_defers                  KILLED  39 failed  test_a1 test_a2 test_b1 test_c1 test_d1 test_d2 test_e1 test_g1 test_h1 test_h2 … (+7)
  M1_no_flags_at_admission             KILLED  6 failed   test_f1 test_f4 test_o1 test_w1 test_w2 test_w8b
  M2_transport_in_dx                   KILLED  81 failed  test_a01 test_a02 test_a03 test_a04 test_a05 test_a06 test_a07 test_a08 test_a1 test_a13 … (+47)
  M3_resend_after_lost_sender          KILLED  1 failed   test_w3a
  M4_unbounded_transport               KILLED  14 failed  test_b1 test_c1 test_d1 test_d2 test_f1 test_l2 test_l3 test_s10 test_s2 test_s3 … (+2)
  M5_clear_despite_other_attention     KILLED  3 failed   test_f4 test_f5 test_g3
  M6_no_confirm_persistence            KILLED  8 failed   test_g1 test_g2 test_g3 test_g4 test_g5 test_g6 test_g7 test_g8
  M7_unescaped_name                    KILLED  3 failed   test_e1 test_e2 test_x6
  M8_unescaped_attribute               KILLED  2 failed   test_e1 test_x6
  M9_ops_omits_pending                 KILLED  13 failed  test_o1 test_o2 test_s1 test_w1 test_w2 test_w3a test_w3b test_w4 test_w5 test_w6a … (+3)
  M10_replay_any_delivery              KILLED  1 failed   test_m1
  M11_raw_column_predicate             KILLED  2 failed   test_d05 test_k6
  M12_ce_blind_assignment              KILLED  1 failed   test_k5
  M13_admission_drops_details          KILLED  1 failed   test_g7
  26 of 26 KILLED. Killer names are test-method prefixes across the three suites (R2 test_a1 …
  test_x6; R1 test_w*/l*/f*/o*/g*/e*/k*/m1; minimum-launch test_a01 …). The primary,
  behavioural killer of each R2 mutant:
    MR1  b1 (OFF at `text`: a gate row appears / the refusal moves to the last line) · d2 · e1
    MR2  c1 (OFF at `flow`) · d1 · s3
    MR3  d1 (OFF at `flow_resume` on a real lease-expired SENDING_FLOW)
    MR4  s1 s2 s3 s10 (a claim / a send after the stop signal)
    MR5  s8 (the process-exit harness: a non-daemon worker holds exit for 30 s) · s6 · s6b
    MR6  e1 (the retry is reached after OFF)
    MR7  x1 · MR8 x4 · MR9 a1 a2 b1 c1 d1 d2 e1 g1 h1 h2 s1 s5
    MR10 b1 c1 d1 d2 s10 (the last line under the lock is gone) · MR11 s10 (stop_worker
         returns while the last check holds the lock) · MR12 s4 l2 (shutdown waits for the
         blocked call) · MR13 a1 … s9 (a refusal deferred → a later, stale send)
  R1's 13 mutants (M4 re-anchored to R2's bounded_call call) are all still killed.
  Post-review qualification (R2R-02): 26/26 killed stands, but not every kill proves send
  prevention. MR1, MR2, MR3 and MR6 are SEND-EQUIVALENT: removing one intermediate guard
  leaves the process guard and the last-line guard, so no network call escapes (review probe
  sweep: 0 violations under each). Their kills pin the refusal LOCATION (AT_<STAGE> labels),
  stage names and gate-row behaviour — legitimate regression pins, not send-safety evidence.
  Send safety is demonstrated behaviourally by the process-guard, last-line-guard and
  terminal-HANDOFF mutants, above all MR9 (policy ignores Smart Booking), MR10 (no last-line
  check), MR11 (stop outside `_SEND_LOCK`) and MR13 (refusal defers), each of which lets a
  real provider call or a stale send through, and which both this suite and the review's
  probes kill.

================================================================================
12. PRECISE PATH MANIFEST
================================================================================
  R2 changed, relative to the R1 frozen candidate (49204628…2964), by byte comparison of every
  tracked + untracked file:
    M backend/app/services/booking_slot_recovery.py   (+280 / −41) R2-1, R2-2
    M backend/app/main.py                            (+12 / −3)   R2-2 lifecycle handlers
    M backend/app/ui/kanban_view.py                  (+12 / −5)   R2-3
    M tests/test_minimum_launch_pg.py                (+129 / −0)  PG_R2_DispatchOff
    A tests/test_minimum_launch_r2.py                (841 lines)
    A docs/Launch/…-CORRECTION-R2_CLOSEOUT_001.md    (this file)
  Nothing else differs (no NEW, no GONE file). Not touched: migrations, Meta Flow JSON,
  scripts/emergency_off/*, operator notification, lead identity, detail persistence,
  docs/operations/RECOVERY_MANIFEST.md, Compose, environment, allowlists.
  The commit path set = R1's 41 paths + the R1 independent-review audit (preserved, untracked)
  + tests/test_minimum_launch_r2.py + this closeout = 44 paths. git status: 35 porcelain
  entries (scripts/emergency_off/ is one untracked directory entry = its 9 files).
  Overlap with the deferred return-journey candidate: unchanged from R1 — the same nine
  filenames. Of the R2-changed files only backend/app/ui/kanban_view.py is among them
  (main.py, booking_slot_recovery.py and the test files are not): expect R1's textual
  conflicts plus kanban_view.py's three small R2 hunks.

================================================================================
13. NOT DONE (by scope)
================================================================================
  The review's should-fix items were not implemented: R1-04 (64-hex origin hash /
  inbound marker), R1-05 (bounding the winner's in-request acknowledgement), R1-06
  (`_withdraw` / `_fail_safe` lead-flag write not token-conditional — R2's `_halt` reuses
  both, so the same note applies to it), R1-07 (the lead-169 procedure's digest literal).
  No migration, Flow JSON, kit, notification, lead-identity or detail-persistence file
  changed.

================================================================================
14. LIVE ISOLATION
================================================================================
  Nothing written to live. Reads only, with R1's instrument (live_state.sh): docker inspect;
  docker exec reading flag booleans, counts and an HMAC of the allowlist under a per-session
  key; read-only psql (default_transaction_read_only=on); readiness from inside the
  container; git ls-remote; read-only git on /opt/ridecheck-crm-integration
  (GIT_OPTIONAL_LOCKS=0). Every container/network created was disposable (r2-*, r1h-*,
  rce-*), on --network none or --internal networks, and removed by exact name. The only
  container stopped by me was my own hung test container (10efeeb1cf99, image
  ridecheck-crm-test:l4.7b2, mount = this worktree, read-only) — §9.
  Proven before and after:
    all seven live flags false · allowlist count 1, HMAC unchanged · live image ids and
    deployment unchanged · restart counts 0 / unchanged · schema head
    20261027_w1f_conversation_memory unchanged · pending_outbound 0 and max message id
    unchanged (no WhatsApp send) · no Meta contact · row counts unchanged (no database
    write) · no build, no deployment · lead 169 row HMAC and notification 2 unchanged ·
    integration worktree fingerprint, diff and dirty paths = its evidence manifest ·
    minimum-launch HEAD 6f20587, staged 0, stash 0.
  Read 2026-11-03T19:50:29Z and 20:45:22Z — IDENTICAL in every field (diff empty), and equal
  to the R1 closeout's 16:14:56Z read:
    container /ridecheck-crm-backend-1 id=122d61f95493 image=sha256:19b3cec208b4d97d0618eec6035c7a9fb3cfd1dc9ebb2000c52e09e425eb44f8 restarts=0 started=2026-11-01T23:34:11.834969498Z
    container /ridecheck-crm-postgres-1 id=5246fd86d829 image=sha256:71e27bf60b70bded003791b5573f8b808365613f341df20ffcf0c1ed7bc13ddf restarts=0 started=2026-10-21T23:31:05.793593905Z
    container /ridecheck-crm-n8n-1 id=6ea80570b67b image=sha256:2d70fafea7c0c397f40df30daa31243c0bff590e9e87e0430398e7cb4d3d8d0b restarts=0 started=2026-10-21T23:31:12.210634184Z
    flag OUTBOUND_ENABLED=false
    flag SMART_BOOKING_CE_ENABLED=false
    flag OPERATOR_WHATSAPP_NOTIFY_ENABLED=false
    flag CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=false
    flag EVIDENCE_ADMISSION_ENABLED=false
    flag OUTSTANDING_QUESTION_ENABLED=false
    flag LOCALITY_RESOLUTION_SAFETY_ENABLED=false
    allowlist_count=1
    operator_to_set=False
    allowlist_hmac16=cb953429eb1d3bd5
    readiness=200
    alembic=20261027_w1f_conversation_memory
    max_message_id=6268
    pending_outbound=0
    max_security_event_id=735
    leads=55/171
    whatsapp_contacts=36
    whatsapp_threads=36
    thread_revisions=1
    revisions=61
    whatsapp_booking_offers=2
    booking_attempt_events=7
    booking_commands=10
    whatsapp_thread_evidence=20
    users=0
    operator_notifications=1:SENT:1: 2:HELD:0:NOT_CONFIGURED_DISABLED
    notification_2=HELD:0:NULL:NULL:9:82
    revision_82=CANCELADO:false
    lead_169_row_hmac16=5c2b3e26394fc7e0
    dup_phone_groups=1
    recovery_table_present=f
    integration_worktree_fingerprint=a1985b38e079d797
    integration_head=6f20587da9af0d49fbe0b20078cff555521c293b
    return_cutoff_evidence_manifest=OK
    integration_dirty_paths_vs_evidence=OK
    integration_diff_vs_evidence=IDENTICAL
    origin_convergence=6f20587da9af0d49fbe0b20078cff555521c293b
    origin_minimum_launch=
    minimum_launch_head=6f20587da9af0d49fbe0b20078cff555521c293b branch=integration/prelaunch-minimum-launch staged=0 stash=0

================================================================================
15. PROPOSED git add (NOT EXECUTED)
================================================================================
  git -C /opt/ridecheck-crm-minimum-launch add -- \
      backend/app/api/leads.py \
      backend/app/main.py \
      backend/app/models.py \
      backend/app/routes/flow_data_exchange.py \
      backend/app/routes/ops_dashboard.py \
      backend/app/services/booking_acknowledgement.py \
      backend/app/services/booking_authority.py \
      backend/app/services/booking_flow_details.py \
      backend/app/services/booking_flow_service.py \
      backend/app/services/booking_slot_recovery.py \
      backend/app/services/conversation_engine.py \
      backend/app/services/lead_booking_details.py \
      backend/app/services/lead_identity.py \
      backend/app/services/operator_notification.py \
      backend/app/services/phone_utils.py \
      backend/app/services/schema_preflight.py \
      backend/app/ui/control_view.py \
      backend/app/ui/kanban.py \
      backend/app/ui/kanban_actions.py \
      backend/app/ui/kanban_view.py \
      backend/migrations/versions/20261102_lead_phone_identity.py \
      backend/migrations/versions/20261103_slot_recovery_job.py \
      docs/Launch/2026-11-02_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION_CLOSEOUT_001.md \
      docs/Launch/2026-11-03_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-INDEPENDENT-REVIEW_AUDIT_001.md \
      docs/Launch/2026-11-03_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R1-INDEPENDENT-REVIEW_AUDIT_001.md \
      docs/Launch/2026-11-03_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R1_CLOSEOUT_001.md \
      docs/Launch/2026-11-03_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R2_CLOSEOUT_001.md \
      scripts/emergency_off/ENABLE.sh \
      scripts/emergency_off/KILL.sh \
      scripts/emergency_off/README.md \
      scripts/emergency_off/WATCH.sh \
      scripts/emergency_off/common.sh \
      scripts/emergency_off/rehearse.sh \
      scripts/emergency_off/test_kit.sh \
      scripts/emergency_off/test_kit_r1.sh \
      scripts/emergency_off/watcher_pid.sh \
      tests/test_convergence_p4_r1.py \
      tests/test_minimum_launch.py \
      tests/test_minimum_launch_e2e.py \
      tests/test_minimum_launch_pg.py \
      tests/test_minimum_launch_r1.py \
      tests/test_minimum_launch_r2.py \
      tests/test_sb2a_booking_authority_pg.py \
      tests/test_w1f_w01_r2_outstanding_question.py
  44 explicit paths; no `git add .`, no directory argument. Not executed.

================================================================================
16. PROPOSED COMMIT SUBJECT
================================================================================
  fix(launch-r2): recovery sends only while Smart Booking is on, bounded daemon worker shutdown, escape lead-card dropdowns and flag class

================================================================================
17. REMAINING ACCEPTED LAUNCH DEBT
================================================================================
  New in R2:
  * A provider call already in flight at shutdown is abandoned, not cancelled (§5.4); it
    may still be delivered after the job says HANDOFF (uncertain) — a person is flagged and
    nothing is re-sent.
  * Every OFF/stop refusal is a HANDOFF, never a resumption: a routine deploy restart that
    lands mid-recovery hands that customer to a person even when Smart Booking comes back ON
    (a false positive for a person, never a stale send). A job not yet claimed at shutdown
    is left PENDING for the next process (§3).
  * Under OFF, a SENDING_FLOW job whose Flow had in fact been accepted is HANDOFF with the
    offer withdrawn instead of RECOVERED (§3).
  * A last-line refusal consumes the attempt's delivery identity at the gate (row `failed`);
    harmless, because the job is terminal.
  Recorded after the R2 independent review (carried, NOT fixed, NOT reclassified):
  * R2R-01 MEDIUM — accepted operational limitation, corrected in wording only (§4): an old
    process whose cached posture is ON can keep claiming and beginning recovery sends during
    Uvicorn's graceful-drain window, up to Docker's approximately 10 s stop window, because
    the stop signal is set only by the shutdown handler that runs after the drain. Emergency
    OFF is complete only once the old container has exited and the replacement is ready with
    the flags verified OFF. Not a Smart Booking gate bypass. KILL.sh behaviour unchanged.
    The code alternatives the review offered (stop on SIGTERM / early lifespan, or
    `--timeout-graceful-shutdown` below 10 s) were not taken; either needs a narrow review.
  * R2R-02 LOW — MR1, MR2, MR3, MR6 are send-equivalent; their kills are label/location
    pins (§11).
  * R2R-03 LOW — a rare `_INFLIGHT` capacity-slot leak in `bounded_call` if `guard()` raises
    (rather than returning a reason) or `Thread.start()` raises (e.g. thread exhaustion);
    reachable triggers are the test-only `_STAGE_HOOK` and thread exhaustion. Up to
    MAX_INFLIGHT_TRANSPORTS=4 leaks give TRANSPORT_CAPACITY refusals (fail-closed non-sends)
    until restart. Fix (try/finally) deferred; it needs a narrow review.
  * R2R-05 LOW — one Flow-shutdown measurement reached approximately 1.0 s (1 of 20) under
    shared-host load; median 0.057 s (§5.4). No timing claim may quote the median as a max.
  * INFO, as recorded by the audit: R2R-04 pre-existing operator-only raw sinks (revisions
    chip, agencias table), identical on HEAD; R2R-06 test h2's name; R2R-07 s10 sleep / s11
    source-text / s8 `_run_check` patch; R2R-08 OfferLifecycle.issue logs dispatch-OFF without
    refusing; R2R-09 render_table_page dead and broken identically on HEAD; R2R-10 an
    in-flight Flow at shutdown leaves its fresh offer ISSUED.
  Carried unchanged from the R1 closeout §16: F3a; window 1 until Meta retries;
  the second-losing-delivery replay; conservative flag clearing; 7-day listing without a
  resolve action; contended confirmation writes no details; F-14; return-to-origin cutoff
  (deferred); single-resource availability / one professional; payment copy authority;
  NP-06/NP-07; the CE nfm_reply SLOT_CONFLICT handoff; the flood gate; Instagram; three
  clean Wilds; review should-fix R1-04 … R1-07. Deployment preconditions unchanged: P1 lead
  169, P2 the notification-2 retirement SQL (self-enforcing, NOT run), P3 a compatibility
  image at EXPECTED_HEAD 20261103_slot_recovery_job.

================================================================================
18. STATEMENT
================================================================================
  Nothing was staged, committed, pushed, built, deployed or migrated live. STOP.
