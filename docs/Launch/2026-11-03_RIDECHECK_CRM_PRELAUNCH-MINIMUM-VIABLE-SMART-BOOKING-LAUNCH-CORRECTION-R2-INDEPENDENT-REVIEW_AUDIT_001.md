PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R2-INDEPENDENT-REVIEW

================================================================================
1. VERDICT
================================================================================
ACCEPTED FOR CONTROLLED COMMIT

There is no HIGH finding. Every acceptance condition holds on evidence this reviewer produced:
  * No recovery provider call can begin while Smart Booking is OFF. This holds at every
    settings-read boundary, in every posture, and from every lease-expired state.
  * No recovery provider call begins after shutdown crosses the `_SEND_LOCK` ordering boundary.
  * An expired lease cannot get past the gate.
  * Terminal HANDOFF jobs never resume on their own, including on later ON passes.
  * The in-flight limitation is contained (stopped, recorded, never retried) and documented.
  * Worker shutdown is bounded.
  * No exploitable HTML remains in the reviewed surface.
  * No candidate-only regression exists, on SQLite or on PostgreSQL.

Two findings are MEDIUM/LOW and carry into the commit milestone as conditions (§14):
  * R2R-01: uvicorn drain ordering, plus wording in the closeout that overclaims.
  * R2R-02: some mutant kills are label-only, which is a coverage-honesty note.

Smart Booking stays NO-GO for deployment until preconditions P1–P3 are met (§13).

================================================================================
2. SCOPE AND FINGERPRINT
================================================================================
Target: /opt/ridecheck-crm-minimum-launch, branch integration/prelaunch-minimum-launch.
  * HEAD 6f20587da9af0d49fbe0b20078cff555521c293b, as expected.
  * Nothing staged, no stash.
  * origin/integration/prelaunch-minimum-launch does not exist (ls-remote is empty).
  * origin convergence branch = 6f20587.

Candidate: uncommitted working tree.
  * `git status --porcelain -uall` = 44 paths: 19 modified and 25 untracked. That is the R1
    candidate set plus the new R2 test, the R2 closeout and the R1 audit.
  * No placeholder or TODO markers in the added lines.

R2 delta against the frozen R1-review copy (`diff -rq`): exactly 6 files, plus the R1 audit,
which is untracked as expected.
  * backend/app/services/booking_slot_recovery.py (321 changed lines: send policy, stage
    guards, `_halt`, `bounded_call` under `_SEND_LOCK`, the lifecycle);
  * backend/app/main.py (startup/shutdown hooks);
  * backend/app/ui/kanban_view.py (label escaping, `_flag_class`);
  * backend/tests/test_minimum_launch_pg.py (additions only: class PG_R2_DispatchOff,
    pgo_01 and pgo_02);
  * backend/tests/test_minimum_launch_r2.py (new);
  * the R2 closeout (new).

The R1 audit is unchanged: sha256 af9c7af40198…, mtime 18:51:58Z.

Drift: a recursive diff of the live worktree against the reviewer's frozen R2 copy, taken at
the end of the review, is empty. The candidate did not change during the review.

Not in the R2 delta:
  * Compose, .env and allowlist files;
  * Meta Flow JSON;
  * the kit files, which are byte-identical to R1.

Return-to-origin: no added line in the R2 delta matches regreso, vuelta, return_to_origin or
return.?cutoff. There is no match in backend/app at all.

================================================================================
3. SOURCE TRACE (booking_slot_recovery.py, R2)
================================================================================
send_refusal():
  * returns WORKER_STOPPING when stopping();
  * otherwise returns DISPATCH_OFF unless the canonical
    booking_dispatch_allowed() returns `is True`;
  * fails closed on any exception.

_guard(stage, claim):
  * runs the `_STAGE_HOOK` barrier, then send_refusal(), then raises `_Halt`.

Stages:
  * claim (stop only, in run_once);
  * process;
  * text, text_resume, text_retry;
  * text_transport — the last line, evaluated inside `bounded_call` under `_SEND_LOCK`
    immediately before `t.start()`;
  * after_text;
  * offer;
  * flow_resume, flow;
  * flow_transport — the last line, as above.

_halt is terminal.
  * It writes HANDOFF with outcome `{TEXT_|FLOW_|}<reason>` and last_error `AT_<STAGE>`.
  * When the job is SENDING_FLOW, it withdraws the fresh offer.
  * If K1 is refused, `_fail_safe` writes FAILED HANDOFF_LOCK_CONTENDED.

bounded_call:
  * holds `_SEND_LOCK` over the guard and `t.start()`;
  * the wait loop polls every 50 ms;
  * when the stop event is set it raises TransportDeadlineExceeded(shutdown=True);
  * uses the MAX_INFLIGHT_TRANSPORTS=4 semaphore.

start_worker:
  * idempotent;
  * starts a daemon thread "slot-recovery-worker" with a fresh Event per start.

stop_worker(timeout=3.0):
  * takes `_LIFECYCLE`, then `_SEND_LOCK`;
  * sets `_STOP` and the thread's event;
  * joins with a bound;
  * returns False without joining when called from the worker itself.

main.py:
  * `@app.on_event("startup")` → `_start_slot_recovery_worker`;
  * `@app.on_event("shutdown")` → `_stop_slot_recovery_worker`.
  * This is the only entry point that starts the worker.

================================================================================
4. SMART BOOKING OFF AT EVERY BOUNDARY (section B)
================================================================================
Instrument: probe/rvw.py "World".
  * Only urllib.request.urlopen is faked; that is the true network edge.
  * Everything above it is real: gate, enforce_outbound_enabled, senders, worker.
  * SMART_BOOKING_CE_ENABLED is switched through the cached-settings factory that
    booking_dispatch_allowed() reads.
  * Every dispatch evaluation and every network call is sequenced on one counter.
  * A violation is any network call sequenced after the first False.

Settings reads on full ON paths: PENDING 13, PENDING_RETRY 17, SENDING_TEXT 13,
TEXT_SENT 9, SENDING_FLOW 6. All five end RECOVERED.

Sweep (b1): SB flips OFF after the N-th settings read, for every N from 0 to reads+1, on
every path.
  * 68 runs.
  * 0 sends after a refusal, 0 non-terminal refusals, 0 lost flags, 0 unlisted terminals,
    0 bookings, 0 resurrections on three later ON passes, 0 fresh offers left ISSUED.

Postures (b2), across PENDING, SENDING_TEXT, TEXT_SENT and SENDING_FLOW:

  | posture             | network | state / outcome / location                |
  |---------------------|---------|-------------------------------------------|
  | OUTBOUND on, SB off | 0       | HANDOFF / DISPATCH_OFF / AT_PROCESS       |
  | seven-OFF           | 0       | HANDOFF / DISPATCH_OFF / AT_PROCESS       |
  | both ON             | text+flow | RECOVERED (control)                     |

  In both OFF postures:
  * flags are (True, True);
  * the job is listed with [SLOT_RECOVERY_HANDOFF, SLOT_RECOVERY_DISPATCH_OFF];
  * the SENDING_FLOW fresh offer is WITHDRAWN;
  * no booking is made;
  * later ON passes send 0.

  With the 4 starting states and 3 postures, these are the 12 postures the brief requires.

Real daemon worker restarted in OFF (b3), against lease-expired PENDING, SENDING_TEXT,
TEXT_SENT and SENDING_FLOW:
  * all four: 0 network calls, HANDOFF, 0 sends on later ON passes;
  * the worker is a daemon and stop_worker() returns True.

Meta resubmission (b4): same attempt ref and fresh-IV ref, with SB on and with SB off.
  * Each gives 1 job, 0 network calls, HANDOFF, and no booking.
  * The confirm raises BookingSlotConflictError in all four cases.
  * Note: test h2 is named "refused before the authority". With SB off the confirm is in fact
    the authority's own replay conflict, not a dispatch refusal. The outcome is safe; the name
    is inaccurate (INFO).

Observation: OfferLifecycle.issue only detects and logs dispatch-OFF; it does not refuse.
This is safe because the worker's offer and flow guards refuse first (INFO).

PostgreSQL: test_minimum_launch_pg has 31 cases, 0 failures and 0 errors on disposable PG,
including pgo_01 (PENDING and lease-expired SENDING_FLOW send nothing) and pgo_02 (the daemon
stops between text and Flow).

================================================================================
5. SHUTDOWN AND ORDERING (section C)
================================================================================
`_SEND_LOCK` is instrumented with a tracing lock. The `t.start` of each transport thread is
sequenced. Deterministic barriers stop the worker at three points:

  | stop point                   | result                                                |
  |------------------------------|-------------------------------------------------------|
  | before_lock                  | 0 sends; TEXT_WORKER_STOPPING AT_TEXT_TRANSPORT       |
  | holding_lock                 | see note below                                        |
  | after_thread_start           | text sent; Flow refused                               |

  holding_lock: the text `t.start` (seq 19) came before the stopper acquired `_SEND_LOCK`
  (seq 23). The stopper waited for the lock, and the Flow was refused with
  FLOW_WORKER_STOPPING AT_AFTER_TEXT.

Randomized race: 500 iterations with stop_worker() at a random offset.
  * 0 transport starts sequenced after the stopper's lock acquisition.
  * Classes: 55 stop before any send, 121 between text and Flow, 324 after all sends.

Conclusion: the ordering boundary is "stop_worker holds `_SEND_LOCK`". Every transport start
is either before it (already in flight; see §6) or refused.

================================================================================
6. IN-FLIGHT TEXT AND FLOW AT SHUTDOWN (section E)
================================================================================
The worker is stopped while the provider call is blocked. The call then either returns
late_ok or raises late_raise.

Text:
  * stop returns in about 0.06 s;
  * HANDOFF TEXT_TRANSPORT_DEADLINE, last_error TRANSPORT_ABANDONED_AT_SHUTDOWN;
  * inflight is 1 after stop and 0 after release.

Flow: the same, with FLOW_TRANSPORT_DEADLINE. The fresh offer stays ISSUED. This is the
documented limitation: Meta may have delivered it, and it cannot be withdrawn retroactively.

After the late return:
  * full-table snapshot equal, so there is no late DB mutation;
  * no retry, no Flow;
  * restarts in OFF and in ON send 0;
  * the final state stays HANDOFF.

Bound over 20 runs:
  * text max 0.064 s;
  * Flow median 0.057 s, max 0.9998 s. The single outlier came under host load; the 2-CPU host
    is shared with the live stack.

The R2 suite's own measurement, repeated here: SHUTDOWN_WITH_BLOCKED_TRANSPORT 0.059 s
(closeout 0.066 s).

Conclusion: the limitation is contained and documented. At most one request per job that was
already on the wire completes at Meta. It is recorded as uncertain HANDOFF and visible to the
operator.

================================================================================
7. LIFECYCLE AND PROCESS EXIT (section D)
================================================================================
  * start_worker is idempotent: one live worker, a daemon.
  * Each restart gets a fresh Event; the old Event stays set, so a finishing old worker is
    never "un-stopped".
  * Repeated stop is harmless, and `_WORKER` is None afterwards.
  * The signal comes before the join: join ended in about 0.004 s, ×10.
  * The join is bounded: about 0.50 s with timeout 0.5, ×5, and 0 sends.
  * Self-stop (called from the worker) returns False in 0.0 s. The second job stays PENDING
    with 0 attempts, so there is no new claim after the stop.
  * Sessions are closed on success, refusal and internal error. The only thread touching the
    DB is slot-recovery-worker; transport threads never touch the DB.

Subprocess process-exit (p1), 10 runs per mode, rc 0 in every run:

  | mode                     | ENDED | STOP     | exit after stop |
  |--------------------------|-------|----------|-----------------|
  | provider-blocked         | True  | ~0.06 s  | 0.37–0.52 s     |
  | non-provider-blocked     | False | 3.0 s    | 0.37–0.52 s     |

  The non-provider-blocked worker is released by daemon status.

The R2 suite's PROCESS_EXIT measured 0.898 s (ENDED False, STOP 0.202); the closeout says
0.930 s.

================================================================================
8. LEASES AND STATES (section F)
================================================================================
Lease-expired SENDING_TEXT, TEXT_SENT and SENDING_FLOW, resumed with SB OFF, all refuse at
AT_PROCESS, before any resume path (§4 b2, b3; R2 d1 and d2; pgo_01).

The resume-specific guards (text_resume, flow_resume) and the last-line guards refuse
independently of the process guard (MR3 and MR10 analysis, §10).

Terminal states (HANDOFF, FAILED, RECOVERED):
  * claim never selects them;
  * 0 resurrections across the sweep and on 3 ON passes per run.

`_halt` keeps both attention flags and lists the job. Unchanged R1 attention logic is
re-verified by M1, M5 and M9.

================================================================================
9. FASTAPI / UVICORN AND SETTINGS REALITY (section G)
================================================================================
Versions: FastAPI 0.115.6, Starlette 0.41.3, uvicorn 0.30.6.
  * `on_event` startup/shutdown is valid and fires in this version.
  * Container command: `uvicorn app.main:app --host 0.0.0.0 --port 8000`, as PID 1, with no
    `--workers` and no init process.
  * Docker stop has the default 10 s grace period.

Settings:
  * get_settings is lru_cached, so a flag change needs a container recreate, which is a new
    process.
  * booking_dispatch_allowed() reads `smart_booking_ce_enabled is True`.

Uvicorn 0.30.6 shutdown order (source):
  1. close servers;
  2. ask connections to shut down;
  3. sleep 0.1 s;
  4. wait for tasks, with timeout_graceful_shutdown None (unbounded);
  5. only then run lifespan shutdown, which is skipped on force_exit.

Experiment (scratch uvicorn app): SIGTERM at t0, with an in-flight 6 s request. The shutdown
handler ran at +5.2 s and the process exited at +5.22 s.

→ Finding R2R-01 (§14).

================================================================================
10. HTML SAFETY (section H)
================================================================================
kanban_view.py R2 changes:
  * filters dropdown (line 1210) and edit-form professional option (line 5776):
    `html_lib.escape(_profesional_label(p), quote=True)`;
  * agency option (line 5783): `_esc_txt(a.nombre_agencia)`;
  * `_flag_class(v)` returns `flag-<v>` only when v is a str in FLAG_LABELS, and `flag-other`
    otherwise. It is used on the card header and the table pill (the pill label is escaped).

Results:
  * Matrix: 112 label renderings (nombre / apellido / cargo / agency × hostile values × both
    sinks). All are escaped exactly once.
  * Flag class: all 18 variants (case, prefix, quote, injection) give a known class or
    flag-other. Empty or None give no flag class.
  * Board injection probe (R1's, re-run): 0 raw sinks (R1 had 5), 65 escaped, 0
    double-escaped.

render_table_page: identical on pristine and candidate. It raises the same TypeError
(`_filters_form_html() got an unexpected keyword argument 'turno_fecha_from'`) and has no
caller, so it is unreachable dead code and outside the reachable surface (INFO).

Out-of-scope operator-only raw sinks are present identically on HEAD and not touched by R2
(INFO, R2R-04):
  * render_revisions_table_page chip `Profesional: {label}`;
  * render_agencias_page `<td>{_txt(a.nombre_agencia)}`;
  * the render_table_page chips (unreachable).

================================================================================
11. TESTS AND MUTATION AUDIT (section I)
================================================================================
Targeted suites on the candidate (R2 + R1 + minimum_launch + e2e): 120 passed, 33 subtests,
0 failed.

Method: each mutant is applied to a copy of the frozen R2 candidate, its anchor verified to
match once, and py_compile run. Then the targeted suites run (suites.xml), followed by the
reviewer probes (probes.xml: sweep, postures, restart, resubmission, barriers, 60-iteration
race, lifecycle, join, self-stop, in-flight, bound, process exit, HTML matrix).

Coverage: 18 mutants re-run here. The remaining 8 R1 mutants (M2, M6, M7, M8, M10–M13) touch
code that is byte-identical to R1, so their R1 kills carry over. 26 of 26 are killed.

  | mutant | suite kills (selected) | probe kills | send-relevant? |
  |--------|------------------------|-------------|----------------|
  | MR1 text guard→stage | b1 gate-row count, d2/s2 AT_ label, e1 | 0 | no — equivalent |
  | MR2 flow guard→stage | c1 count, d1/s3 AT_ label | 0 | no — equivalent |
  | MR3 flow_resume guard→stage | d1 AT_FLOW label only | 0 | no — equivalent |
  | MR4 stopping()=False | s1 s2 s3 s6b s9 s10 (real sends) | c1 c2 d2 d3 | yes |
  | MR5 daemon=False | s6 s6b s8 (subprocess hang) | d1, p1 HUNG | yes |
  | MR6 retry guard removed | e1 count 2≠1 | 0 | no — equivalent |
  | MR7 edit label raw | x1 | matrix | n/a (HTML) |
  | MR8 flag class raw | x4 | matrix | n/a (HTML) |
  | MR9 allowed=True | 12 tests, real text+flow | b1 b2 b3 b4 | yes |
  | MR10 no last-line check | b1 c1 d1 d2 s2 s3 (real sends) | c1, c2 | yes |
  | MR11 stop outside lock | s10 | c1 (text started after stop) | yes |
  | MR12 wait ignores stop | s4 s6 l2 | e1 e2 | yes (bound) |
  | MR13 halt→release | 17 tests | b1 (NOT_TERMINAL, RESURRECTED) b2 b3 b4, d2 | yes |
  | M1 no flags at admission | 6 (w1 w2 w8b f1 f4 o1) | 0 | attention |
  | M3 resend after lost sender | w3a label | 0 | label |
  | M4 unbounded transport | l2 l3 | e1 (0.63 s > 0.5) | bound |
  | M5 clear despite attention | f5 g3 | 0 | attention |
  | M9 ops omits pending | 13 | 0 | attention |

Focus mutants:
  * MR4, MR5, MR9–MR13: each is killed by at least one behavioral test in the R2 suite and,
    independently, by a reviewer probe that observes a real network call, hang or
    non-termination.
    - MR10 escapes the monotonic sweep, because the earlier guards still catch a flip. It is
      killed by the concurrent barrier and race probes. This is correct: the last line exists
      for concurrent flips.
    - MR11 is killed deterministically by c1 holding_lock: with the mutant, the text started
      after the stop signal.
  * MR1, MR2, MR3, MR6 are send-equivalent. Removing one intermediate guard leaves the process
    guard and the last-line guard, so no network call ever escapes (probe sweep: 0 violations
    under each). The R2 suite kills them with refusal-location labels (AT_<STAGE>), gate-row
    counts and stage names. These kills are legitimate as regression pins but do not
    demonstrate send safety → R2R-02.
  * M3 is killed by an outcome label only (unchanged from R1).

Test-quality notes (INFO):
  * s10 uses a 0.05 s sleep;
  * s11 checks source text only;
  * s8 patches `_run_check`.
The probes above cover the same properties behaviorally.

================================================================================
12. BASELINES — SQLITE AND POSTGRESQL (section J)
================================================================================
Identity = (classname, test name). Messages are normalized for addresses, durations, /tmp
paths and ids.

SQLite full suite (disposable, in-container):

  | run | failed | passed | skipped | errors | subtests |
  |-----|--------|--------|---------|--------|----------|
  | pristine HEAD | 62 | 7136 | 658 | 9 | 7100 |
  | R1 candidate | 62 | 7226 | 675 | 9 | 7129 |
  | R2 candidate | 62 | 7256 | 677 | 9 | 7162 |

  | comparison | failing ids (each side) | NEW | GONE | COMMON | message differences |
  |------------|-------------------------|-----|------|--------|---------------------|
  | R2 vs pristine | 71 / 71 | 0 | 0 | 71 | 0 |
  | R2 vs R1 | 71 / 71 | 0 | 0 | 71 | 0 |

PostgreSQL (disposable rv2pg-* container on an --internal network, schema from the R1
harness; 34 test modules):

  | comparison | cases | failing ids | NEW | GONE | COMMON | message differences |
  |------------|-------|-------------|-----|------|--------|---------------------|
  | R2 vs pristine (ML_pg excluded) | 778 / 778 | 30 / 30 | 0 | 0 | 30 | 21 |
  | R2 vs R1 (all modules) | 795 / 797 | 30 / 30 | 0 | 0 | 30 | 0 |

  The 21 message differences against pristine:
  * 17 contain the stale head literal, now '20261103_slot_recovery_job' where it was
    '20261027_w1f_conversation_memory';
  * 4 (ssa_02, ssa_05, ssa_41, ssa_53) are migration-output assertions whose captured output
    gained the new revision line.

  This is the same explanation as R1 §(baselines). Against R1 the messages are identical.

  test_minimum_launch_pg: 31 cases, 0 failures (pristine has no such module).

There is no unexplained candidate-only regression.

================================================================================
13. LIVE ISOLATION AND PRECONDITIONS (sections K, L)
================================================================================
The live read was taken before (21:03:51Z) and after (22:39:30Z). The diff excluding taken_at
is IDENTICAL:

Containers:
  * backend 122d61f95493, postgres 5246fd86d829, n8n 6ea80570b67b;
  * images unchanged, restarts 0, start times unchanged.

Flags and allowlist:
  * all 7 flags false;
  * allowlist_count=1, hmac16 cb953429eb1d3bd5;
  * readiness 200.

Database:
  * alembic 20261027_w1f_conversation_memory;
  * max_message_id 6268; pending_outbound 0; max_security_event_id 735;
  * slot_recovery_jobs absent;
  * lead_169_row_hmac16 5c2b3e26394fc7e0; lead_170 87f22747202754e9;
  * notification_2 HELD:0:NULL:NULL:9:82; revision_82 CANCELADO.

Other worktrees:
  * integration worktree fingerprint a1985b38e079d797, HEAD 6f20587;
  * return-cutoff evidence manifest OK, dirty paths OK, diff vs evidence IDENTICAL;
  * minimum-launch fingerprint (excluding this audit) f74d06bdae764204. It was re-taken after
    this audit was written, with the audit's own `git status` line and file both excluded:
    still f74d06bdae764204. A recursive diff against the frozen copy shows only this audit.

No rv2-* or rv2pg-* container, network or volume remains.

Preconditions (state only; none executed):
  * P1, lead 169: open and unmodified (row hmac unchanged).
  * P2, notification 2: open. The retirement SQL was not run; the row is HELD:0.
  * P3, compatibility image at 20261103_slot_recovery_job: open.
    - The candidate's EXPECTED_HEAD is 20261103_slot_recovery_job; the live DB is at
      20261027 and the live image is unchanged.
    - The schema preflight would refuse a candidate image against the current live DB until
      the migration runs.
  * One professional only: live `profesionales` count = 1. The single-resource availability
    assumption holds today and must be re-checked at deployment.
  * Return cutoff: absent from the candidate (§2). The deferred candidate in
    /opt/ridecheck-crm-integration was not touched (fingerprint and manifests identical).

================================================================================
14. FINDINGS BY SEVERITY
================================================================================
HIGH: none.

MEDIUM
  R2R-01 Worker stop runs only after uvicorn's connection drain, and the closeout overclaims.
    * `_stop_slot_recovery_worker` is a lifespan shutdown handler. Uvicorn 0.30.6 runs it only
      after closing servers and draining connections and tasks, which is unbounded with the
      default `timeout_graceful_shutdown`. It is skipped entirely if Docker's 10 s SIGKILL
      comes first.
    * During that window the old process keeps claiming and sending under its own, still-ON
      cached posture. Measured: the handler ran 5.2 s after SIGTERM when a 6 s request was in
      flight.
    * This is not an OFF bypass: the new container's posture governs new sends, and the old
      process is ON by construction. The bounded-stop mechanism itself is correct (§5–§7).
    * But the R2 closeout's statement that KILL.sh's `docker stop` "now ends recovery
      transport at the signal, not at SIGKILL" is inaccurate. It ends at drain completion, or
      at SIGKILL if the drain exceeds 10 s.
    * Condition for the commit milestone, choose one: correct the closeout and KILL wording;
      or set the stop event from a SIGTERM hook or an early lifespan path; or add
      `--timeout-graceful-shutdown` below 10 s to the run command.
    * The operator runbook must state that turning Smart Booking OFF by recreate can let the
      old process send for up to about 10 s.

LOW
  R2R-02 Mutants MR1, MR2, MR3 and MR6 are send-equivalent.
    * Their R2 kills rest on AT_<STAGE> labels, gate-row counts and stage names, not on a
      prevented send.
    * The closeout's "26/26 killed" is true, but four of those kills do not show send safety.
      Safety rests on the process guard plus the last-line guard, both killed behaviorally
      (MR9, MR10, MR13).
    * Record this in the commit closeout.
  R2R-03 bounded_call slot leak on an exception.
    * `_INFLIGHT` is acquired before `_SEND_LOCK`. A refusal (guard returns a reason) correctly
      releases the slot.
    * But if `guard()` raises instead of returning a reason, or `t.start()` raises (for
      example "can't start new thread"), the slot is never released and stays leaked until
      the process restarts.
    * send_refusal() itself fails closed without raising, so the reachable triggers are the
      test-only `_STAGE_HOOK` and thread exhaustion.
    * Impact: up to MAX_INFLIGHT_TRANSPORTS=4 leaks lead to TRANSPORT_CAPACITY refusals, which
      are fail-closed non-sends, not unsafe sends.
    * A try/finally around the locked block is recommended.
  R2R-05 Flow shutdown bound outlier.
    * Max 0.9998 s in 1 of 20 runs under shared-host load; median 0.057 s.
    * Bounded, but a deployment-time timing claim should not quote the median as the maximum.

INFO
  R2R-04 Pre-existing operator-only raw sinks (revisions-table professional chip, agencias
    table name). Identical on HEAD and outside R2's surface; track separately.
  R2R-06 Test h2's name says "refused before the authority". The observed SB-off outcome is
    the authority's replay BookingSlotConflictError. Safe, but the name is inaccurate.
  R2R-07 s10 depends on a sleep, s11 is source-text only, s8 patches `_run_check`. Behavioral
    equivalents exist in this review's probes.
  R2R-08 OfferLifecycle.issue logs dispatch-OFF without refusing; the worker guards refuse
    first.
  R2R-09 render_table_page is dead and broken identically on HEAD (TypeError, no caller).
  R2R-10 An in-flight Flow at shutdown leaves its fresh offer ISSUED. Documented, recorded as
    FLOW_TRANSPORT_DEADLINE HANDOFF, and listed for the operator.

R1 findings: R1-01 (HIGH), R1-02 (MED) and R1-03 (LOW) are closed by R2 (§4, §5–§7, §10).

================================================================================
15. NEXT MILESTONE
================================================================================
PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CONTROLLED-COMMIT

Commit the reviewed 44-path set, unchanged except for documentation, on
integration/prelaunch-minimum-launch, after:
  * R2R-01 is resolved, by wording correction at minimum, recorded in the commit closeout;
  * R2R-02 is recorded.
Any code change for R2R-01 or R2R-03 requires a narrow re-review of that delta.

Deployment remains blocked on:
  * P1 lead 169;
  * P2 notification-2 retirement SQL;
  * P3 compatibility image at EXPECTED_HEAD 20261103_slot_recovery_job;
  * a deploy-time re-check of the single professional.

Smart Booking remains NO-GO until the deployment milestone.

Nothing in the candidate was edited, staged, committed, pushed, built, deployed or migrated
live. No flag or allowlist was changed, Meta was not contacted, and no WhatsApp message was
sent. /opt/ridecheck-crm-integration was not touched. The only file written is this audit.
STOP.
