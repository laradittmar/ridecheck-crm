PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R1-INDEPENDENT-REVIEW

Date: 2026-11-03. This was a fresh, read-only independent review of the R1 minimum-launch
candidate (/opt/ridecheck-crm-minimum-launch, branch integration/prelaunch-minimum-launch,
HEAD 6f20587). Nothing in the candidate was edited, staged, committed, pushed, tagged or
stashed. Nothing was built, deployed or migrated live. No live flag, environment, Compose file
or allowlist changed. No provider was contacted. Lead 169 and operator notification 2 were
read only. /opt/ridecheck-crm-integration was read only. The only file written in the worktree
is this audit.

Method: I rebuilt every claim from source, from probes, from re-runs and from read-only live
queries. The probes ran against a byte-identical frozen copy of the candidate in sealed
containers (`--network none`, or `--internal` for PostgreSQL 16, image 71e27bf60b70). The live
queries ran with `default_transaction_read_only=on`. I read all three earlier documents in
full and treated them as claims to test. Probe scripts and raw outputs are in the reviewer
scratchpad:
/tmp/claude-0/-root/5d03e4b7-6581-4be0-a3ce-5722b51f1146/scratchpad/rv/
(probe/, out/, pg/out/, sqlite/, rehearsal-20261103T184108Z/).

================================================================================
0. VERDICT
================================================================================
CORRECTION REQUIRED

The R1 corrections C-1 … C-7 are real and mostly correct. Each of the following was
reproduced independently:
  * the durable job and its UNIQUE admission;
  * flag ownership;
  * the lease/claim protocol;
  * all crash windows;
  * the bounded transport;
  * the Data Exchange running off the event loop;
  * detail persistence at confirm;
  * the seller_phone canonical copy;
  * the self-enforcing notification-2 SQL;
  * the kit (28/28, 38/38, rehearsal 89/0);
  * SQLite and PostgreSQL NEW 0 / GONE 0.

The verdict is nevertheless CORRECTION REQUIRED, because the prompt's explicit E trigger is
met:

  R1-01 HIGH  With SMART_BOOKING_CE_ENABLED=false (and OUTBOUND_ENABLED=true), the recovery
              worker still SENDS:
                * the recovery text, from PENDING or from a lease-expired SENDING_TEXT;
                * the Booking FLOW itself, when the job resumes from SENDING_FLOW (measured:
                  RECOVERED, Flow `sent`).
              The worker never consults booking_dispatch_allowed() before the text, nor on
              the SENDING_FLOW resume path. This reopens the WILD1-F1 invariant: "a Flow is
              dispatched while the flag says OFF".

Two further corrections are smaller and must ride along:

  R1-02 MED   The worker's ThreadPoolExecutor thread is non-daemon. A pass in progress keeps
              running after the event loop stops, and keeps claiming and sending until it
              finishes or Docker's SIGKILL arrives (`docker stop`, 10 s by default). So
              emergency OFF does not stop new recovery transport promptly, and process exit
              is not bounded by the application.
  R1-03 LOW   On the lead card page (the edit-latest-revision form inside the card),
              professional names/emails and agency names reach the <option> text unescaped.
              lead.flag reaches a class attribute unescaped. These are operator- or
              API-supplied values, not customer values, but the prompt's I check ("no raw …
              event handler … reaches the output") fails on them.

Everything else either passed or is acceptable launch debt (§16). The exact corrections are in
§18.

================================================================================
1. SOURCE, SCOPE AND ISOLATION (A)
================================================================================
  worktree           /opt/ridecheck-crm-minimum-launch
  branch             integration/prelaunch-minimum-launch
  HEAD               6f20587da9af0d49fbe0b20078cff555521c293b
  remote             origin https://github.com/laradittmar/ridecheck-crm.git
                     refs/heads/integration/prelaunch-booking-convergence = 6f20587…
                     refs/heads/integration/prelaunch-minimum-launch      absent (never pushed)
  staged 0 · stash 0

  Dirty path set: 41 = 19 M + 22 untracked (`git status --porcelain -uall`). Compared with
  the R1 closeout §18 exact-path list: IDENTICAL.
    EXTRA: none. MISSING: none.
    (This audit is the 42nd path and is outside the candidate.)

  Earlier documents, present and unchanged:
    2026-11-02 closeout     sha256 02cd9e5e…7637662   = the implementer's frozen copies
                                                       (pg/candidate*, dev*)
    2026-11-03 review audit sha256 43beeacb…ad11a     byte-identical to the reviewer's
                                                       AUDIT_FINAL_COPY.md
    2026-11-03 R1 closeout  sha256 1a2da4c8…0fdd
  The frozen candidate copy used here equals the implementer's final freeze
  (`diff -rq`: only the R1 closeout differs, as expected).

  Restored PostgreSQL test files:
    tests/test_convergence_p3_race_pg.py and tests/test_convergence_p4_r1_pg.py
    `git diff --quiet` → pristine.

  Not in the candidate (path and content grep):
    * Meta Flow JSON;
    * Compose files, .env files, allowlist files;
    * docs/operations/RECOVERY_MANIFEST.md.
  Return-to-origin: no match for return/origin cutoff, regreso, vuelta or return_to_origin in
  any added line or new file.

  Deferred return-cutoff candidate (/opt/ridecheck-crm-integration):
    HEAD 6f20587; worktree fingerprint a1985b38e079d797 (before = after);
    `git diff --binary` = evidence patch (IDENTICAL);
    dirty-path sha256 manifest OK;
    evidence MANIFEST_FINAL (+ .self) OK.

  Every R1 path traces to C-1…C-7 or to a named launch-exposed correction (closeout §1
  table, checked against each diff):
    main.py                    worker start                          C-2
    flow_data_exchange.py      run_in_threadpool                     C-2
    ops_dashboard.py, control_view.py   listing                      C-1
    booking_authority.py       seller_phone                          C-3
    20261103 migration                                               C-1 / C-2 / C-3 / C-7
    test_sb2a_booking_authority_pg.py   cleanup-only adaptation      §15
    test_minimum_launch_r1.py, test_kit_r1.sh                        proofs
  No unexplained path.

================================================================================
2. FINDINGS, BY SEVERITY
================================================================================
HIGH
  R1-01  Smart Booking OFF does not stop recovery transport.
         Probe probe/test_rv_off.py, production run_once, provider fakes at the edge only.
         Result in out/off.json:

           case                                       result
           PENDING job, SB=false, OUTBOUND=true       text SENT, then HANDOFF
                                                      FLOW_DISPATCH_OFF
           lease-expired SENDING_TEXT, SB=false       text SENT (resume), HANDOFF
                                                      FLOW_DISPATCH_OFF
           lease-expired SENDING_FLOW, SB=false       Flow SENT → RECOVERED   ← a Booking Flow
                                                      dispatched with the flag OFF
           TEXT_SENT, SB=false                        no Flow, HANDOFF FLOW_DISPATCH_OFF (safe)

         Cause:
           * `_flow_phase` checks booking_dispatch_allowed() only on the TEXT_SENT branch.
           * The SENDING_FLOW resume reuses the already-issued offer, so the
             OfferLifecycle.issue backstop is never reached.
           * `_text_phase` has no dispatch check at all.
           * The outbound gate checks the kill switch (OUTBOUND_ENABLED), not
             SMART_BOOKING_CE_ENABLED.
         The text promises "Podés elegir otro horario" while booking is disabled.
         An SB-only rollback (OUTBOUND on for FAQ/quoting, Smart Booking off) is a realistic
         posture, and it is exactly the posture WILD1-F1 was built to make inert.

MEDIUM
  R1-02  The non-daemon worker executor outlives shutdown.
         `_EXECUTOR = ThreadPoolExecutor(max_workers=1)` is joined at interpreter exit
         (concurrent.futures' atexit), and `run_once` checks no stop signal.
         Measured with the module's own executor: a 6-second pass kept running after the
         main thread exited, and the process took 6.7 s to exit.
         A real pass can be ≥ 10 jobs × (two K1 waits + up to three 8 s transports).
         KILL.sh runs `docker stop` (10 s grace, then SIGKILL). Until the SIGKILL, the old
         process, whose environment is still ON, may claim the next job and send.
         The window is bounded by Docker, not by the application.

LOW
  R1-03  Remaining HTML sinks on the lead card page.
         Probe probe/test_rv_xss*.py put `Q"'><svg/onload=pwn(7)><b>Z</b>` in every string
         column of lead, revision, professional, agency, thread_revision and candidate, and
         `javascript:` in every link/url column, then rendered the full board through
         render_page with lead_booking_details.
         Raw hits: 5.
           * 4 are the <option> text of the professional select (`_profesional_label`:
             nombre, apellido, email) and the agency select (`_txt(nombre_agencia)`) in
             render_edit_latest_revision_form (kanban_view.py:5769/5776). The same professional
             option sink exists in the filters form (line 1210).
           * 1 is `class="cardHeaderRow flag-{flag_val}"` (lead.flag is writable by
             PATCH /leads with max_length 40, a machine-credential API).
         Every customer-derived field: escaped once (54 escaped occurrences, 0 double-escaped,
         0 `href="javascript:`).
         Pre-existing on the pristine tree. The writers are operators (quick-create agency,
         professionals page) or the n8n API, never a customer.

INFORMATIONAL / RECOMMENDATIONS (not blocking)
  R1-04  origin_delivery_hash and inbound_marker are 16-hex truncations (§3). Sufficient
         here, because neither guards a booking or a send. Since 20261103 is not deployed,
         storing the full 64 hex costs nothing.
  R1-05  The WINNER's booking acknowledgement is still a synchronous provider call inside the
         Data Exchange request. It is pre-existing, now runs on the threadpool rather than the
         event loop, and is bounded per socket operation only (urlopen 15 s).
         A Meta deadline miss is answered by the replay path (SUCCESS, no second ack).
         The closeout's condition 2 is worded "recovery transport", which is accurate. The
         prompt's broader "no provider call inside Data Exchange" holds only for the
         recovery.
  R1-06  `_withdraw` (offer → WITHDRAWN after a definite Flow failure) and `_fail_safe`'s lead
         flag write are not conditioned on the claim token. Harmless with one process and an
         8 s deadline against a 60 s lease, but the only post-claim writes outside the token
         rule.
  R1-07  The lead-169 deletion procedure (audit 001 §10) still pins the unsalted digest
         literal 'ad98b5c31a4e8ed8', and the owner authorisation text quotes it. The
         169 = 170 identity equality check alone is sufficient. Drop the literal from the
         deployment copy (F-09 spirit).

================================================================================
3. RECOVERY-JOB SCHEMA (B)
================================================================================
  Read from migration 20261103_slot_recovery_job, the ORM model, and the live-dump upgrade on
  disposable PostgreSQL 16 ("20261103_slot_recovery_job: CREATED").

  * One job per losing offer: recovery_identity = sha256("slot-conflict-recovery|v1|offer:N"),
    UNIQUE (uq_slot_recovery_jobs_identity), admitted by INSERT … ON CONFLICT DO NOTHING.
    DB-enforced. PG pga_01 (3 offers × 6 racers → 3 jobs) re-run: pass.
  * No unnecessary PII: ids, states, typed codes, delivery identities (digests), WAMIDs.
    No phone, name or text.
  * CHECKs are internally consistent with the code:
      - state vocabulary = TERMINAL ∪ NONTERMINAL;
      - terminal ⇔ (outcome AND completed_at), which every terminal writer sets (`_finish`,
        `_recovered`, exhausted claim);
      - attempts 0..6 vs SLOT_RECOVERY_MAX_CLAIMS = 6 (claim_next fails a job at ≥ 6 before
        incrementing, so the CHECK is never hit);
      - text 0..2 vs MAX_TEXT_ATTEMPTS 2;
      - flow 0..1.
  * Lease ownership: every `_owned` UPDATE has `claim_token = :token AND state IN
    NONTERMINAL`. `_issue_fresh_offer`, `_ensure_attention` and `_recovered` re-read the row
    under K1 and require the token. Exceptions: R1-06.
  * Terminal jobs are never reclaimed (claim_next selects NONTERMINAL only).
  * Expired leases are reclaimed (`claim_token IS NOT NULL AND lease_expires_at < now`).
    The claim is a CAS on (id, state, attempts, due).
  * Downgrade refuses while either table holds a row, or while any seller_phone is set
    (pgm_01, re-run: pass, rows kept, version unchanged).
  * Adoption:
      - malformed table or column → refuse (pgm_02);
      - partial set holding data → refuse;
      - an EMPTY canonical partial set is adopted and completed.
    That is slightly broader than "only the create_all bootstrap shape", but every adopted
    object is exactly canonical and empty, so nothing of unknown provenance is accepted.
    Acceptable.
  * Revision ids: 20261103_slot_recovery_job = 26 chars, 20261102_lead_phone_identity = 28 chars.
    Both fit 32. Live version_num is varchar(128) (read on the restored live dump).
  * schema_preflight.EXPECTED_HEAD = 20261103_slot_recovery_job.

  The 16-hex fields, at their decision boundaries:
    origin_delivery_hash = sha256(dx_attempt_ref)[:16]. dx_attempt_ref is already a digest of
      the IV and ciphertext. It is read only by `_replayed_slot_refusal`, whose two outcomes
      (the same refusal screen, or the fail-closed "session expired" screen) both write
      nothing, book nothing and send nothing. A collision changes which error screen a
      superseded-offer delivery sees: 2^-64 accidental, 2^64 work for a targeted second
      preimage, and a valid booking token is needed anyway.
      SUFFICIENT.
    inbound_marker = sha256(last_processed_inbound_wa_message_id)[:16]. It is compared at
      RECOVERED to decide whether a turn ran since admission. A collision would clear the
      flags a newer turn may have relied on. WAMIDs are Meta-issued, not customer-chosen;
      the probability is 2^-64 per turn.
      SUFFICIENT. A full identifier is recommended (R1-04), not required.
      The real limit of the marker is structural, not cryptographic: a non-turn writer that
      sets needs_human=true while it is already true (an operator, another service) is
      invisible to it. The boolean flags carry no reason. This is accepted as the
      conservative-flag debt (§16).

================================================================================
4. ADMISSION AND HUMAN-ATTENTION OWNERSHIP (C)
================================================================================
  Admission transaction, reconstructed from booking_flow_service `_confirm_booking` and
  `admit_recovery`:
    1. The authority returns BookingOutcome.SLOT_CONFLICT.
    2. The typed FINALISE refusal is durable, and the ledger re-read missed.
    3. `_recover_slot_conflict` rolls back the request session (it only read).
    4. `admit_recovery`, on its own isolated session:
         K1 (1.5 s × 2)
         → optional pending-details write in a SAVEPOINT
         → INSERT job ON CONFLICT DO NOTHING RETURNING id
         → `_apply_attention` (SCHEDULING, state.needs_human, lead.necesita_humano; records
           owns_state_flag / owns_lead_flag / inbound_marker)
         → COMMIT.
       No provider call.
  Only the SLOT_CONFLICT branch calls it. The loser writes no Revision and no ThreadRevision
  (asserted in every W test; re-run).

  K1 contention: the job is still inserted (attention_applied=false) and is listed at once
  with SLOT_RECOVERY_FLAGS_NOT_RAISED. `process()` calls `_ensure_attention` BEFORE `_ctx`
  and before any phase, so nothing is sent until the flags are up. If K1 stays contended, the
  job is deferred, then FAILED HANDOFF_LOCK_CONTENDED with the lead flag raised and listed
  (w8a; PG pgr_01 with a real held K1: pass).

  Clearing at RECOVERED (`_recovered`, one K1 transaction):
    * only the flags this job turned false → true;
    * only if the inbound marker is unchanged;
    * only if no other nonterminal / 7-day HANDOFF / FAILED job, and no 7-day attention
      condition, exists on the thread or lead;
    * the lead flag is also kept while another thread of the lead has needs_human.
  K1 contended → kept (FLAGS_KEPT_CONTENDED). HANDOFF and FAILED never clear.
  f1–f6 and mutants M1/M5 re-verified by the R1 suite run (48/48).

  Operator surface:
    * /api/ops/booking-attention returns slot_recoveries + attention_conditions.
    * /control renders each with an escaped link to /whatsapp/thread/<id>.
    * Every NONTERMINAL job is listed, plus HANDOFF/FAILED for 7 days.
  o1–o3: pass.

  Concurrency, re-run on PostgreSQL 16:
    pga_01   concurrent admissions for one losing offer → one job
    pgr_01   K1 held at admission and at the worker
    pgr_02   two concurrent workers → one claimant, one text, one Flow
    pga_02   real two-customer race; no recovery send inside the requests
  All pass.

================================================================================
5. WORKER, LEASE AND CRASH RECOVERY (D)
================================================================================
  Startup: one @app.on_event("startup") creates one `slot_recovery_loop` task per process.
  Dockerfile CMD: uvicorn, no --workers, so one process. The loop sleeps 5 s, then runs
  `_run_check` on the dedicated single-thread executor with its own SessionLocal session,
  closed in finally.

  Shutdown: NOT bounded by the application (R1-02). The transport threads are daemon and do
  not block exit. The executor thread is non-daemon and does.

  Claim → text → fresh offer → Flow → terminal, as described in the closeout §3/§4, verified
  in source:
    * every write after the claim is token-conditional (except R1-06);
    * no session transaction or K1 is open across a provider call: `_owned`, the K1
      sessions and the gate's pending row all commit before transport;
    * a stale worker cannot overwrite a newer one's terminal state (`_owned` requires the
      token and a NONTERMINAL state; `_recovered` re-checks under K1);
    * a duplicate send across lease expiry is stopped by the gate's once-ever delivery
      identity (BLOCKED_DUPLICATE → AMBIGUOUS → HANDOFF, never a second transport).
  Lease 60 s ≫ one step (two K1 waits + one 8 s deadline). Expiry behaviour is deterministic:
  the gate row for the step's identity decides between resume, record or HANDOFF.

  No job is silent:
    * nonterminal → listed (PENDING / IN_PROGRESS, plus LEASE_EXPIRED, STALE after 120 s,
      INTERNAL, LOCK_CONTENDED, FLAGS_NOT_RAISED);
    * INTERNAL and exhausted contention → FAILED, listed with a typed reason;
    * HANDOFF → listed.

  Crash windows — the committed W suite was re-run, all pass:
    window  case                                 result
    1       death before admission               self-heals on the Meta retry
    2       death before text                    resumes → RECOVERED
    3a      Meta accepted text, nothing recorded HANDOFF TEXT_UNCERTAIN_LOST_SENDER, 0 re-sends
    3b      gate recorded the text               RECOVERED with one text
    4       text sent, before fresh offer        resumes → Flow → RECOVERED
    5       fresh offer committed, before Flow   same offer reused, no third offer
    6a      Flow recorded                        RECOVERED, 0 re-sends
    6b      Flow accepted, not recorded          HANDOFF FLOW_UNCERTAIN_LOST_SENDER
    7       unexpected exception                 FAILED INTERNAL_ERROR, listed
    8a      K1 contended                         FAILED LOCK_CONTENDED, lead flagged
    8b      K1 contended once                    RECOVERED
    9       replay after any window              0 sends

  For windows 2–8, the shared assertions (assert_owed_and_visible / assert_settled) check:
    * one job;
    * no loser booking;
    * the thread or lead flagged;
    * operator listing with thread_url;
    * terminal after resume;
    * ≤ 1 SENT row per delivery identity;
    * a degraded end still listed.
  I read the helpers line by line. They assert what the closeout says.

================================================================================
6. OFF-POSTURE AND FEATURE-GATE SAFETY (E) — the priority check
================================================================================
  How flags reach the worker:
    * OUTBOUND_ENABLED is read from os.environ by `enforce_outbound_enabled`, inside the real
      senders and by the gate's kill-switch check.
    * SMART_BOOKING_CE_ENABLED is read from the cached get_settings().
  Neither can change inside a running process; "turning OFF" means recreating the container
  (KILL). So "OFF between text and Flow" is a restart with a lease-expired job, which is what
  the probe exercises.

  Measured (probe/test_rv_off.py, out/off.json):
    OUTBOUND=false, PENDING          gate blocked_kill_switch ×2 → HANDOFF TEXT_NOT_DELIVERED;
                                     0 transport. Re-run later with ON: terminal, 0 sends.
                                     SAFE
    both false, PENDING              same                                   SAFE
    both false, SENDING_FLOW resume  gate blocked → offer WITHDRAWN → HANDOFF FLOW_NOT_SENT
                                                                            SAFE
    SB=false only, PENDING           recovery TEXT SENT                     UNSAFE (R1-01)
    SB=false only, SENDING_TEXT      recovery TEXT SENT (resume)            UNSAFE (R1-01)
    SB=false only, SENDING_FLOW      BOOKING FLOW SENT, RECOVERED           UNSAFE (R1-01)
    SB=false only, TEXT_SENT         HANDOFF FLOW_DISPATCH_OFF, no Flow     SAFE

  Requirement by requirement:
    * no text or Flow while dispatch is disabled:
        FAILS for SB=false. Holds for OUTBOUND=false and for both false.
    * OFF not bypassed because the job was admitted while ON:
        FAILS for SB=false. Holds for OUTBOUND=false.
    * a job interrupted by OFF becomes visible / handed off:
        holds (HANDOFF, flags up, listed).
    * ON later does not send an old job:
        holds. Jobs are consumed to terminal HANDOFF during OFF, and terminal jobs are never
        claimed. This should be written down as the policy (R1-01 correction).
    * emergency OFF stops new recovery transport promptly:
        PARTIAL. After the restart, nothing can send (all seven false). Before SIGKILL, the
        old process's pass can continue for up to Docker's 10 s grace (R1-02).
    * no stale lease creates a delayed send after the window closes:
        holds after the restart (the old process is gone; the new one is gated by the kill
        switch).
  Admission while SB=false is impossible: the Data Exchange confirm OFF switch refuses before
  the authority, so no SLOT_CONFLICT occurs.

  Prompt rule: "If OFF posture can still emit a recovery message or Flow, return CORRECTION
  REQUIRED". It can, under SMART_BOOKING_CE_ENABLED=false → CORRECTION REQUIRED.

================================================================================
7. DATA EXCHANGE LATENCY AND SESSION / THREAD SAFETY (F)
================================================================================
  * No provider call in the recovery path inside Data Exchange: l1, l6 and the race() helper
    (asserts m.sent == m.flows == [] for the loser) re-run. The winner's acknowledgement is
    still in-request (R1-05).
  * Event loop: l5 re-run; and my real-PostgreSQL probe ticked 35 times during 12 concurrent
    requests in 0.9 s.
  * Exception order, raw encryptor and typed 422: unchanged in the route. The try/except
    chain is unchanged and wraps `await run_in_threadpool(_dispatch)`. The SQLite structural
    tests (http_01-03, r3_c03, r2_r05) pass in the full run.

  Session boundary (FastAPI 0.115.6 / Starlette / anyio, image ridecheck-crm-test:l4.7b2 and
  the requirements pin):
    * `get_db` is a sync generator dependency. FastAPI enters it in a threadpool thread
      (Session created there, lazily, with no connection yet).
    * The route body runs on the event loop.
    * `_dispatch` runs in a threadpool thread.
    * The generator's exit (db.close()) runs in a threadpool thread after the route returns.
    * `run_in_threadpool` → anyio.to_thread.run_sync without abandon_on_cancel: a cancelled
      request still waits for the thread, so close() can never race an executing dispatch.
    * The same cross-thread, strictly sequential pattern existed before R1 (the sync body ran
      on the loop thread with a Session created in a pool thread). SQLAlchemy Sessions and
      psycopg connections are safe for sequential hand-off. Nothing relies on thread-locals
      (no scoped_session on this path).
  Real PostgreSQL probe (probe/test_rv_route_pg.py; production route + router + the
  get_db-shaped dependency + real BookingFlowService; 12 concurrent date_selected requests
  via httpx ASGITransport):
      status 200 ×12 · request Sessions 12 · cross-thread overlapping use 0 · same-thread
      re-entrant ORM loads 12 (selectin loads, expected) · request Sessions closed 12/12 ·
      pool checked-out after 0.
    Each request Session executed on at most 2 threads, sequentially. In one of the 12, one
    statement ran on the loop thread; I did not localise it. It is sequential and not
    concurrent.
  → No unsafe Session crossing. PASS.

================================================================================
8. TRANSPORT DEADLINE AND EXHAUSTION (G)
================================================================================
  * `bounded_call` runs the sender on a daemon thread and waits for at most 8.0 s. Past that
    it raises TransportDeadlineExceeded → gate.mark_failed → DEADLINE → HANDOFF
    *_TRANSPORT_DEADLINE. It is never retried (l2, l7).
  * The DX event loop is not involved: the worker has its own thread.
  * One stuck job does not block a healthy one or the notification loop (l3).
  * MAX_INFLIGHT_TRANSPORTS = 4 is enforced by a non-blocking BoundedSemaphore acquire,
    which is atomic. The capacity pre-checks are only an optimisation; there is a single
    consumer thread. With no slot free, run_once breaks before claiming, and the step defers
    with _Defer TRANSPORT_CAPACITY. If the semaphore is lost between check and call, the
    result is a definite pre-dispatch refusal (l4).
  * Abandoned threads run only the sender function. I checked the senders: no Session, no
    write. Late results are discarded; the gate row is already `failed`.
  * No second send after deadline ambiguity: DEADLINE → HANDOFF, and the gate identity is
    once-ever.

  Exhaustion behaviour:
    * Four permanently stuck threads: no claim happens. Jobs stay NONTERMINAL and listed
      (PENDING → STALE after 120 s, flags raised at admission or FLAGS_NOT_RAISED shown).
      They are visible, never silent, but not terminal until a slot frees or the process
      restarts.
    * No executor slot: same as above.
    * Worker restart: the semaphore resets; leases expire; resume semantics apply (§5).
    * Application shutdown: R1-02.
    * Repeated deadlines: each job ends at its first deadline (HANDOFF). The slots refill as
      urllib's per-operation timeouts end the orphans.
  The delivery gate remains the final duplicate barrier (BLOCKED_DUPLICATE is treated as
  AMBIGUOUS).

================================================================================
9. DETAIL PERSISTENCE (H)
================================================================================
  * prepare_summary persists as before.
  * confirm_booking persists again, through the same write_detail_rows, before the authority,
    with K1 tried for 50 ms. The rows are bound to:
      - the token-resolved thread;
      - the admitted offer (turn_id "offer:<id>");
      - the offer-bound candidate.
  * Replay: identity = value + predecessor, so a replay writes nothing.
  * Correction: supersedes the predecessor.
  * Winner/loser isolation: rows are thread-scoped and the card is read by thread.
  * seller_phone is written in the booking's own transaction (ThreadRevisionAttachment →
    ThreadRevision.seller_phone).
  * The card falls back to the latest booked/completed ThreadRevision for every missing
    field (lead_booking_details).
  * Failure → one DETAILS_NOT_PERSISTED row per (thread, delivery), lead flag (+ thread flag
    if K1 is free), listed.
  * `_other_attention` sees the condition, so RECOVERED keeps the flags (g3).
  * Reasons are CONFIRM_<typed cause or exception class>; logs carry ids only.
  g1–g8, PG pgr_03 and pristine p3r_10b: pass.

  The contended-confirmation exception: a confirmation whose authority answers "contended"
  writes no detail rows and no condition (the pre-existing p3r_10b contract), and the
  customer gets the typed retry screen.
    * A retry re-submits the same Flow payload (the same screen data).
    * prepare_summary's rows normally already hold those values; they are lost only if
      prepare's write ALSO failed.
    * If the customer then abandons, nothing was booked and nobody is flagged.
  Practical severity: LOW. It needs a prepare persistence failure AND contention AND
  abandonment, and no booking is owed. Acceptable for the minimum launch, as recorded.

================================================================================
10. CRM HTML SAFETY (I)
================================================================================
  All of the following are escaped exactly once:
    * customer- and Flow-derived values: lead title (including the Flow-name fallback),
      contact line, attribution, flag label, vehicle badge and pill, the
      "Datos de reserva" block (all nine fields), revision block (every _esc_txt sink);
    * edit modal value="…" (quote-safe) and the edit form values and textarea;
    * search data-attribute.
  `_url_link`: only ^https?:// becomes a link, with the href attribute-escaped; anything else
  is shown as escaped text.
  Structure is intact (the e3 test plus my render).
  Independent payload, every string column (§2 R1-03):
    * customer-derived: 0 raw;
    * 0 javascript hrefs;
    * 0 double escapes;
    * 5 raw operator/API sinks (R1-03).
  The /control recovery list escapes via esc().
  CORRECTION (LOW) required for the operator/API sinks.

================================================================================
11. OPERATOR NOTIFICATION (J)
================================================================================
  Reconfirmed from source:
    * Eligibility = a COMMITTED CREATE occupying ledger row. SLOT_CONFLICT writes no ledger
      row, so a refusal creates nothing.
    * UNIQUE(booking_command_id) and UNIQUE(identity) mean a replay creates nothing.
    * build_summary → BOOKING_NOT_OCCUPYING holds a cancelled or moved booking at send time.
    * ≤ 2 attempts (CHECK), retry only after a definite failure, and the gate identity per
      attempt, so at most one successful notice.
    * No code moves HELD → PENDING. The only PENDING write is record() on a row the claim
      holds in SENDING; enqueue writes HELD or PENDING only at insert.
  PG test_w1f_operator_notification_pg: 1 F + 4 E on both trees (the inherited head literal;
  identical identities).

  The notification-2 SQL (closeout §9, extracted verbatim), executed only on a disposable
  PostgreSQL restored from the live schema-only dump, with synthetic rows 82 / 2 / 3:
    matching (HELD, 0, cmd 9, rev 82 CANCELADO)   rc 0 · row 2 → FAILED
                                                  SUPERSEDED_BOOKING_CANCELLED,
                                                  next_attempt NULL · row 3 untouched
    re-run on the result                          rc 3 · "not in the expected state" ·
                                                  unchanged
    attempts=1 · status PENDING · cmd 8 · claim_token set
                                                  rc 3 · nothing changed
    revision occupying (PENDIENTE) · CONFIRMADO   rc 3 · "revision 82 is not cancelled" ·
                                                  nothing changed
  Verified:
    * it locks both rows (FOR SHARE / FOR UPDATE);
    * it pins command 9 and revision 82;
    * it validates the states;
    * GET DIAGNOSTICS requires exactly 1 row;
    * any mismatch aborts by itself (RAISE + ON_ERROR_STOP);
    * FAILED is a terminal, non-sendable evidence row.
  It was NOT run live.

  Live deployment settings still required, read now as presence only:
    OPERATOR_WHATSAPP_TO                ABSENT (operator_to_set=False)
    OPERATOR_BOOKING_TEMPLATE_NAME      required (config_problem holds without it)
    OPERATOR_BOOKING_TEMPLATE_LANGUAGE  required
    OPERATOR_WHATSAPP_NOTIFY_ENABLED    false
    WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID   provider credentials (set, by design)
    an approved template whose 10 parameters follow TEMPLATE_PARAMETERS order:
      event, name, wa.me, vehicle, place, when, professional, status, ids, crm

================================================================================
12. LEAD IDENTITY AND MIGRATION (K)
================================================================================
  One rule, read in phone_utils, the models Index (expression + postgresql_where), the
  migration (IDENTITY / PREDICATE / CANONICAL_INDEXDEF), the ON CONFLICT arbiter
  (index_elements + index_where) and phone_identity_clause:
      regexp_replace(telefono,'[^0-9]','','g')  WHERE  … <> ''
  These are identical, and canonical_phone_identity matches them (ASCII digits, None when
  empty).

  Re-run on PostgreSQL 16 (all pass):
    pgd_01   index present and canonical
    pgd_02   the + variant collides; '-', 's/n', '(  )', NULL and '' all repeat
    pgd_03   6/6 concurrent opening messages → one lead
    pgd_04   many simultaneous creates → one lead
    pgr_04   update/create → typed 409; two racing assign_phone_if_free → exactly one
             ASSIGNED
    pgi_01   full rehearsal
    pgi_02   malformed adoption ×3 incl. the pre-R1 predicate → refused
    pgi_03   duplicate groups refuse, lead ids only, no digits or sha16
    pgi_04   foreign index under the name → downgrade refuses
    pgi_10   create_all builds the identical index
    pgm_01/02  migration rehearsals (§3)
  POST /leads stays idempotent (find_or_create_lead).
  The CE path uses assign_phone_if_free: an isolated conditional UPDATE, IntegrityError →
  HELD_BY_OTHER, never raised into the turn.
  Downgrade / re-upgrade keeps every lead row (pgi_01).
  Revision ids fit both 32 and 128.
  Upgrade from the live schema-only dump on disposable PG:
    20261102 CREATED → 20261103 CREATED.

  Leads 169/170, read live now:
    169   CONSULTA_NUEVA, flag NULL, nombre/email NULL, necesita_humano false, 13 digits
    170   COORDINAR_DISPONIBILIDAD / ACEPTADO, name + email, necesita_humano true
    The same identity (equality checked in SQL, no digits printed). 169 was created 0.29 s
    before 170.
    Referents (169 / 170):
      whatsapp_threads 0/1 · revisions 0/1 · booking_commands 0/2 · hybrid_decision_traces 0/5
      · operator_notifications.hist_lead_id 0/1 · feedback_post_revision 0/0
    FKs into leads: revisions.lead_id and feedback_post_revision.lead_id only.
    No user trigger on leads. Duplicate groups: 1. Digit-free phones: 0. NULL/blank: 6.
  → The proposed deletion of 169 remains safe (apply R1-07 to its text). NOT executed.

================================================================================
13. EMERGENCY OFF (L)
================================================================================
  Re-run on the frozen copy, TMPDIR in the scratchpad:
    test_kit.sh      28 passed, 0 failed (K6 "commands only" = zero hits)
    test_kit_r1.sh   38 passed, 0 failed
    rehearse.sh      89 passed, 0 failed
                     (rehearsal-20261103T184108Z; live image wild1-r3align-b31020ac
                     unmodified, my own schema-only dump, --internal; live containers
                     identical; no rce-*/rv5d-* object left)
  Verified, in the rehearsal or the kit tests:
    * WATCH is pre-armed before ENABLE (timeline order);
    * ENABLE refuses without an owned pre-armed watcher (RUN 0; R2);
    * the race profile accepts the measured shape, and a 4th Flow breaches (RUN 3; R3);
    * the KILL deadline gives a loud, durable INCIDENT_UNRECOVERED, KILL's pid retained and
      not signalled, no OFF claim (RUN 5; R1);
    * inability to start KILL → exit 4, BLOCKER, "KILL exit status: 127" (RUN 4; K2);
    * exact-PID ownership (K7);
    * no pattern kill (K6, R5);
    * the sentinel with a matching argv and the unrelated sentinel survive;
    * seven flags false, allowlist count + digest restored, dispatch disallowed, readiness
      200 (every run);
    * credential wording: "rehearsal base env has blank WhatsApp transport credentials
      (rehearsal-only fact)". No false claim about live credentials.
  Kit gap, application side: R1-02. The kit's `docker stop` grace bounds it at 10 s.

================================================================================
14. TEST INTEGRITY AND BASELINES (M)
================================================================================
  Adapted historical tests, line by line:
    * test_convergence_p4_r1: the head pin moves to 20261103. The two new down_revisions are
      asserted, and the original chain assertions below are unchanged.
    * test_w1f_w01_r2 j12: head pin + chain. K02/K03: `downgrade -1` becomes an explicit
      PARENT, so 20261027's own downgrade still runs (20261103/20261102 downgrade empty in
      the same Alembic transaction). Its refusal needles, its data assertions and the
      re-upgrade fingerprint are unchanged; only the expected version moves to TREE_HEAD.
      If an upper revision refused instead, the needles would fail. The test still proves
      what it was for.
    * test_sb2a_booking_authority_pg pg17: one cleanup DELETE (whatsapp_thread_evidence
      before threads). Setup/cleanup only.
    * p3_race_pg and p4_r1_pg: pristine. Fresh-IV coverage is genuine: pristine p4r1_12 runs
      unchanged, and SQLite m1 asserts refused_count 1 → 2, then no count on the superseded
      offer.
  No skip / xfail / expectedFailure was added. The only skips are the new PG suite's own
  skipUnless gates.

  Re-runs (frozen trees; pristine = git archive 6f20587; candidate = worktree minus nothing
  but this audit):
    SQLite, full suite (--network none):
      PRISTINE   62 failed, 7136 passed, 658 skipped, 9 errors, 7100 subtests (6:11)
      CANDIDATE  62 failed, 7226 passed, 675 skipped, 9 errors, 7129 subtests (6:41)
      failing identities 71 / 71 · NEW 0 · GONE 0 · common messages differing 0
      milestone files: test_minimum_launch 41 · _e2e 1 · _r1 48 (W/L/F/O/G/E/K/M) all pass;
      _pg 17 skipped (no PG)
    PostgreSQL 16, every PG-gated file, fresh template clones per file:
      cases 778 / 795 · failing identities 30 / 30 · NEW 0 · GONE 0 · COMMON 30
      21 common messages differ. All are the inherited stale head literal
      (…20261027 vs …20261103) or the two new revisions' upgrade lines appended to the same
      failing migration-output assertion (ssa_02/05/41/53).
      test_minimum_launch_pg 29/29 (17 tests + subtests).
      Per-file figures equal closeout §14.2 exactly.
    Crash-window, latency and event-loop tests: within the R1 suite above.
    Reviewer probes: OFF (7 cases), XSS (2 renders), route/Session on real PG, notification
      SQL (8 cases), executor-shutdown demo.
  NEW/GONE claims independently confirmed.

================================================================================
15. ACCEPTED DEBT (N)
================================================================================
  The candidate does not claim to solve:
    Meta terminal-screen rendering (F3a), return-to-origin cutoff, multi-professional
    availability, FAQ repetition beyond payment, recurring origins, Instagram.
  No code in the diff addresses them. Confirmed.

  Assessment:
    generic Meta error before the chat explanation      ACCEPTABLE. The explanation comes
                                                        from the worker (≤ 5 s + sends) and
                                                        a person is flagged at admission.
    death before admission depends on Meta's retry      ACCEPTABLE. The refusal is durable;
                                                        a retry self-heals (w1).
    abandoned provider call finishes late, no state     ACCEPTABLE. A person is already
                                                        flagged; nothing is re-sent.
    conservative human flags (false positives)          ACCEPTABLE. Never a lost alert.
    Flow details thread-scoped, not cycle-scoped        ACCEPTABLE (F-14, informational).
    conditions listed 7 days; no resolve action         ACCEPTABLE for launch. Note: they
                                                        also block flag clearing for 7 days
                                                        (conservative).
    contended confirmation + abandonment                ACCEPTABLE, LOW (§9).
    second losing delivery after supersession fails     ACCEPTABLE. No booking or send;
    closed                                              "session expired" copy only.
  Not acceptable as debt: R1-01 and R1-02, which belong to the E/D safety contract.

================================================================================
16. LIVE ISOLATION (O)
================================================================================
  Same read-only instrument as the R1 closeout. The allowlist and lead rows are compared
  through an HMAC under that session's key, so the digests are comparable with closeout §19
  (cb953429eb1d3bd5 / 5c2b3e26394fc7e0, equal).
  before 2026-11-03T17:50:20Z · after 18:48:05Z — IDENTICAL in every field (diff empty):
    container ridecheck-crm-backend-1  122d61f95493 image sha256:19b3cec2…44f8 restarts 0
                                       started 2026-11-01T23:34:11.834969498Z
    container ridecheck-crm-postgres-1 5246fd86d829 image sha256:71e27bf6…3ddf restarts 0
                                       started 2026-10-21T23:31:05.793593905Z
    container ridecheck-crm-n8n-1      6ea80570b67b image sha256:2d70fafe…8d0b restarts 0
                                       started 2026-10-21T23:31:12.210634184Z
    flags: all seven = false
    allowlist_count 1 · allowlist_hmac16 cb953429eb1d3bd5 · operator_to_set False
    readiness 200 · alembic 20261027_w1f_conversation_memory
    max_message_id 6268 · pending_outbound 0 · max_security_event_id 735
    leads 55/171 · contacts 36 · threads 36 · thread_revisions 1 · revisions 61
    booking_offers 2 · booking_attempt_events 7 · booking_commands 10 · thread_evidence 20
    users 0
    operator_notifications 1:SENT:1 2:HELD:0:NOT_CONFIGURED_DISABLED
    notification_2 HELD:0:NULL:NULL:9:82 · revision_82 CANCELADO:false
    lead_169_row_hmac16 5c2b3e26394fc7e0 · lead_170_row_hmac16 87f22747202754e9
    dup_phone_groups 1 · slot_recovery_jobs / booking_attention_conditions absent
    integration fingerprint a1985b38e079d797 · integration head 6f20587 ·
      return-cutoff evidence manifest OK · dirty paths vs evidence OK · diff vs evidence
      IDENTICAL
    minimum-launch fingerprint (excluding this audit) 41a9780141d4b3a9 · staged 0 · stash 0
      (Re-taken after this audit was written, with the audit's porcelain line also
      excluded: 41a9780141d4b3a9 again. `diff -rq` of the worktree against the frozen review
      copy shows only this audit.)
    origin convergence 6f20587 · origin minimum-launch absent
  Disposable objects remaining (rv5d-*, rce-*): 0.
  The pg_dump -s of crm_test was a read-only operation; its structure is identical to the
  implementer's dump apart from OWNER lines.

================================================================================
17. SCORECARD OF THE PROMPT'S SECTIONS
================================================================================
  A source / scope / isolation       PASS
  B recovery-job schema              PASS (R1-04 recommendation)
  C admission / attention            PASS
  D worker / lease / crash           PASS, except shutdown (R1-02)
  E OFF posture                      FAIL (R1-01); partial (R1-02)
  F Data Exchange / Session          PASS (R1-05 noted)
  G transport deadline               PASS
  H detail persistence               PASS
  I HTML safety                      customer values PASS; operator/API labels FAIL (R1-03)
  J operator notification            PASS
  K lead identity / migration        PASS (R1-07 recommendation)
  L emergency OFF                    PASS (kit); application side R1-02
  M test integrity / baselines       PASS
  N accepted debt                    PASS
  O live isolation                   PASS

================================================================================
18. EXACT CORRECTIONS REQUIRED
================================================================================
  C-R1 (R1-01, HIGH) Gate the whole recovery worker on booking dispatch:
    * In `process()`, after `_ensure_attention` and before `_ctx`/any phase: if
      `booking_dispatch_allowed()` is not True, `_finish(HANDOFF, "DISPATCH_OFF")`. Flags are
      kept and the job is listed. Nothing is sent and no offer is issued.
    * Repeat the check immediately before every transport:
        - before `_send_text`;
        - before `_issue_fresh_offer`;
        - and, on the SENDING_FLOW resume branch, before `_send_flow`. There, a disabled
          dispatch must withdraw the live fresh offer (as `_flow_settled` DEFINITE does) and
          end HANDOFF.
    * Optionally also short-circuit when `os.environ.get("OUTBOUND_ENABLED") != "true"`, to
      avoid burning gate rows. Today's behaviour there is already safe.
    * Document the policy: a job met by OFF ends HANDOFF and is never sent when the flags
      return.
    * Tests (SQLite + one PG):
        - SB=false with PENDING, lease-expired SENDING_TEXT, TEXT_SENT and lease-expired
          SENDING_FLOW → zero provider calls, terminal HANDOFF, listed, flags kept, the fresh
          offer not left ISSUED;
        - each followed by an ON pass → still zero calls;
        - a mutant removing the check must be killed.
  C-R2 (R1-02, MED) Make shutdown bounded and stop new transport on shutdown:
    * Replace the ThreadPoolExecutor with a daemon worker thread (or keep the executor but
      make the pass cooperative).
    * Add a module-level stop Event, set by an @app.on_event("shutdown") handler, checked
      before each claim and before each transport (`run_once`, `_text_phase` loop,
      `_flow_phase`). An in-flight call is abandoned as today.
    * Test: with a pass in progress, setting the stop event yields no further claim or
      transport; the process exits within a small bound (measured, as in this audit's demo).
  C-R3 (R1-03, LOW) Escape the remaining lead-card-page sinks:
    * html-escape `_profesional_label(p)` and `a.nombre_agencia` in
      render_edit_latest_revision_form (kanban_view.py:5769/5776) and the filters form
      (line 1210);
    * escape or whitelist `flag_val` in the class attribute (whitelist against FLAG_LABELS
      preferred).
    * Extend e1 with a markup payload in Profesional.nombre/apellido/email,
      Agencia.nombre_agencia and lead.flag.
  Should-fix, not blocking:
    * R1-04 widen origin_delivery_hash / inbound_marker to 64 hex while 20261103 is
      undeployed (update the frozen fingerprint);
    * R1-06 make `_withdraw` and the `_fail_safe` flag write token-conditional, or document
      them;
    * R1-07 drop the unsalted digest literal from the lead-169 procedure and its
      authorisation text;
    * R1-05 later: bound the winner's in-request acknowledgement with the same bounded_call.
  After the corrections:
    * re-run the R1 + minimum-launch suites;
    * re-run the full SQLite and PostgreSQL pristine-vs-candidate comparisons;
    * re-run test_kit / test_kit_r1;
    * re-run the rehearsal only if a kit file changes.

================================================================================
19. NEXT MILESTONE
================================================================================
PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R2: implement C-R1, C-R2 and C-R3 on
integration/prelaunch-minimum-launch, still uncommitted, with the tests above. Then a fresh,
narrow independent review of R2:
  * the OFF matrix of §6;
  * shutdown;
  * HTML sinks;
  * baselines.
Controlled commit preparation and the deployment milestone stay blocked until that review
accepts:
  * P1 lead 169;
  * P2 notification-2 SQL;
  * P3 compatibility image at EXPECTED_HEAD 20261103_slot_recovery_job.
Smart Booking remains NO-GO.

Nothing was edited in the candidate, staged, committed, pushed, built, deployed or migrated
live. STOP.
