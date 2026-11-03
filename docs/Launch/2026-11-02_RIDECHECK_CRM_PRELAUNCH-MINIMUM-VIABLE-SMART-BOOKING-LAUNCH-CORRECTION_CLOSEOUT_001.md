PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION

Date: 2026-11-02. Implementation only. Nothing staged, committed, pushed, built, deployed,
migrated or published. No live flag, allowlist, Compose file or environment changed. No Meta
or other provider contacted. The deferred return-cutoff worktree was not modified.

================================================================================
0. VERDICT
================================================================================
IMPLEMENTED AND VERIFIED ON A SEPARATE BRANCH; NOT DEPLOYED. GO for the deployment milestone
once its three preconditions (§13) are met. NOT a GO to enable anything today.

  A  slot-conflict recovery        DONE. One durable claim per losing offer, one Spanish
                                   explanation, then a fresh Booking Flow on current
                                   availability; a definite failure hands off to SCHEDULING +
                                   human with no unexplained Flow. Proven SQLite + PG (incl.
                                   a concurrent two-customer race).
  B  details kept before confirm   DONE. prepare_summary persists name, e-mail, address,
                                   seller, URL and slot as draft provenance in
                                   whatsapp_thread_evidence; the CRM card shows winner and
                                   loser by JOIN (nothing copied into leads).
  C  one operator notice           ALREADY GUARANTEED by the existing ledger; PROVEN here, plus
                                   one send-time guard (a booking that no longer occupies the
                                   agenda is never announced). HELD-row procedure prepared, not
                                   run.
  D  duplicate-lead race           DONE. Partial UNIQUE index on the digits-only phone
                                   identity + idempotent ON CONFLICT create. Proven 6/6 on
                                   real PostgreSQL concurrency, "+549…" vs "549…". Migration
                                   REFUSES on live today (group 169/170) — by design.
  E  emergency OFF                 DONE as a versioned kit (scripts/emergency_off/). Real
                                   breach restored to OFF in a sealed disposable rehearsal,
                                   39/39; kit unit tests 28/28.
  F  FAQ payment repetition        INCLUDED (small, isolated, no migration): payment only.
  G  return-cutoff work            NONE. Proven independent (§2.3).

  SQLite, frozen pristine vs candidate: NEW 0, GONE 0 (§10.1).
  PostgreSQL, all 26 gated files + the new suite, both trees: NEW 0, GONE 0 (§10.2).
  Live posture before and after: identical (§16).

================================================================================
1. SOURCE, BRANCH AND WORKTREE PROOF
================================================================================
  git ls-remote origin refs/heads/integration/prelaunch-booking-convergence
      6f20587da9af0d49fbe0b20078cff555521c293b      (before AND after creation)
  git ls-remote origin refs/heads/integration/prelaunch-minimum-launch   → (absent)
  local refs/heads/integration/prelaunch-minimum-launch before creation   → absent
  /opt/ridecheck-crm-minimum-launch before creation                       → absent

  git -C /opt/ridecheck-crm worktree add -b integration/prelaunch-minimum-launch \
      /opt/ridecheck-crm-minimum-launch 6f20587da9af0d49fbe0b20078cff555521c293b
  → HEAD 6f20587da9af0d49fbe0b20078cff555521c293b, branch integration/prelaunch-minimum-launch,
    0 dirty paths at creation. No file was copied from the dirty worktree.

  Refusal checks performed before creation (all passed): source SHA, origin SHA, branch
  absence (local and remote), target absence, and a fingerprint of the dirty worktree taken
  before and after the `worktree add`:
      4b930e52b8423b7be71c68029c495c3d78ea5ba48014340ff00d370187e79a51   (identical)
  (HEAD + porcelain status + sha256 of every dirty path + mtime/mode of every dirty path,
   all read with GIT_OPTIONAL_LOCKS=0 so git did not even refresh the index.)

================================================================================
2. THE RETURN-CUTOFF CANDIDATE — PRESERVED, UNCHANGED, NOT USED
================================================================================
2.1 EVIDENCE (read-only, before the worktree was created)
  /opt/ridecheck-builds/return-cutoff-candidate-evidence-20261102T211233Z   (0700, files 0400)
    HEAD.txt                       6f20587…
    git_status_porcelain.txt       git_status_long.txt
    git_diff_binary.patch          226452 bytes (tracked changes)
    untracked_files.tar            every untracked path (a diff does not carry them)
    candidate_42_paths.txt         the R2 closeout's git-add block minus the closeout
    candidate_42_paths.sha256      per-path sha256
    all_dirty_paths.{txt,sha256}   all 111 dirty paths
    R2_CLOSEOUT.md                 copy of the R2 closeout
    README.txt
    MANIFEST.sha256 + MANIFEST_FINAL.sha256 + MANIFEST_FINAL.sha256.self  (self-verifying)
  R2's content pin REPRODUCED: sha256 of (LC_ALL=C sorted 42 paths | xargs sha256sum) =
      f11711bd5c301f4f8429043cfb6e272b02f493fc0005174c51d99d54d4a5a062   (= R2 line 122)
  Verify: cd <dir> && sha256sum -c MANIFEST_FINAL.sha256 && sha256sum -c MANIFEST_FINAL.sha256.self

2.2 UNCHANGED AT THE END
  The same fingerprint re-measured at the end of the milestone:
      4b930e52b8423b7be71c68029c495c3d78ea5ba48014340ff00d370187e79a51   (identical)
  The evidence manifest re-verified: MANIFEST_FINAL.sha256 OK, .self OK.
  origin integration/prelaunch-booking-convergence still 6f20587; the new branch was never
  pushed (absent on origin).

2.3 INDEPENDENCE
  None of operational_origin.py, the 20261102_professional_operational_origin migration or
  test_p0_1_return_journey_cutoff.py exists here; no source mentions operational origin,
  return journey or professional origin. Seven FILENAMES are common to both candidates
  (models.py, conversation_engine.py, schema_preflight.py, kanban_actions.py, kanban_view.py,
  test_convergence_p4_r1.py, test_w1f_w01_r2_outstanding_question.py) — edited for different
  reasons. Of this branch's 168 distinct added lines (>25 chars) exactly ONE also appears
  among the candidate patch's 1273 — the parent-revision literal
  `"20261027_w1f_conversation_memory")`. Expect textual merge conflicts in those seven files if
  the candidate is ever rebased onto this branch, and in schema_preflight.EXPECTED_HEAD /
  alembic down_revision in particular: both candidates add a revision on 20261027.

================================================================================
3. IMPLEMENTATION INVENTORY (the verdict required before any code)
================================================================================
All references are to the stable source 6f20587.

  BookingSlotConflictError    booking_flow_service.py:93. Raised by `_confirm_booking` for every
                              non-committed outcome that returns a screen; the route turns it into
                              the APPOINTMENT refusal screen (routes/flow_data_exchange.py,
                              RECOVERY_SLOT_CONFLICT). After a terminal SUMMARY the handset cannot
                              render it (F3a) — so it is NOT a recovery mechanism.
  booking_attempt_events      services/booking_attempt_events.py:81 event_uid, :130
                              insert_attempt_event (INSERT … ON CONFLICT DO NOTHING RETURNING — the
                              insert IS the admission), :166 record_attempt_event (own bounded
                              session, never raises); models.py:778 (append-only trigger, UNIQUE
                              event_uid, CHECKed stage/outcome vocabularies, no FK).
                              booking_refusal.py:73 finalise_offer_refusal: records a slot refusal
                              on the offer (stays ISSUED) once per attempt.
  outbound deduplication      outbound_safety_gate.py:176 attempt(): per-recipient FOR UPDATE lock
                              (:360), flood gate, dedup (:619), then the dedup row + `pending`
                              outbound row committed BEFORE the transport. `delivery_identity`
                              (64 hex) replaces the text fingerprint and is admitted ONCE, at any
                              age. models.py:859.
  Flow dispatch               conversation_engine.py:13203 _send_booking_flow (dispatch gate →
                              OfferLifecycle.issue → resolve_context → :16806 _send_flow_button →
                              gate(message_type='flow', BOOKING_FLOW) → whatsapp_ui.py:427
                              _send_whatsapp_cloud_flow, data_exchange mode).
  session text sending        whatsapp_ui.py:136 _send_whatsapp_cloud_text; the classified,
                              gate-routed sender is booking_acknowledgement.py:108
                              _send_ledger_acknowledgement (definite vs ambiguous boundary).
  prepare_summary             booking_flow_service.py:753 handle_prepare_summary — PURE: validated,
                              echoed onto SUMMARY, discarded. Measured cause of B.
  whatsapp_thread_evidence    models.py:1451; thread_evidence.py:112 persist_admission, :172
                              bindable_locality (role=INSPECTION_LOCATION ∧ outcome=ACCEPT only),
                              :208 bind, :225 supersede; UNIQUE (thread_id, claim_id); producer,
                              status, cycle, turn, bound_to_candidate_id, superseded_by_id.
  CRM lead/candidate view     ui/kanban_view.py:5118 render_lead_card reads lead + revisions only;
                              no path to the thread, candidate or evidence (why the loser's card
                              was empty).
  operator_notifications      operator_notification.py:249 enqueue_committed_bookings (only
                              COMMITTED CREATE occupying ledger rows above the floor, UNIQUE per
                              command and identity; HELD when not configured), :315 claim_next
                              (PENDING only), :411 send_claimed (gate, per-attempt identity, two
                              attempts, retry only after a definite failure), :200 build_summary.
                              No code moves HELD back to PENDING.
  booking acknowledgement     booking_acknowledgement.py:270 acknowledge_committed_booking;
                              booking_authority.py:1128 claim_acknowledgement / :1171
                              record_acknowledgement (ledger lease, ≤2 attempts).
  lead find/create/link       api/leads.py:59 GET /leads?telefono (compared a '+'-preserving
                              display form), :41 POST /leads (plain insert, no uniqueness of any
                              kind), api/whatsapp.py:786 POST /api/whatsapp/thread/{id}/link
                              (sets thread.lead_id; last writer wins → orphan); the CRM's own
                              ui/kanban_actions.py:145 ui_lead_create.
  ENABLE / WATCH / KILL       /opt/ridecheck-builds/crm-wild1-natural-probe-20261101T225744Z/
                              scripts/. Defect: WATCH.sh:19 `"$(dirname "$0")/KILL.sh"` as a
                              command, files 0600, `set -uo pipefail` without -e, then `exit 3`,
                              trap removes the pidfile. Flow predicate counted every BOOKING_FLOW
                              row (NP-08); "operator notification" counted HELD rows.

================================================================================
4. ARCHITECTURE OF THE CORRECTION
================================================================================
  A  services/booking_slot_recovery.py (new) — called from booking_flow_service._confirm_booking
     ONLY for BookingOutcome.SLOT_CONFLICT, after the durable refusal (finaliser) and after the
     ledger re-read missed; a Meta retry of that same delivery is answered with the same
     screen and nothing else (_replayed_slot_refusal). Reuses: insert_attempt_event as the claim, the gate's
     delivery_identity, _send_ledger_acknowledgement's classification (given a `text`
     parameter; every existing caller unchanged), OfferLifecycle.issue/_terminalise, the
     dispatch gate, _send_whatsapp_cloud_flow. No migration.
  B  services/booking_flow_details.py (new) — persisted from handle_prepare_summary; read by
     services/lead_booking_details.py (new, read model) → ui/kanban.py → kanban_view
     (render_booking_details_block). booking_flow_service._identity_fields pre-fills the
     Flow's customer_name from it when the lead has none. No migration.
  C  operator_notification.build_summary: BOOKING_NOT_OCCUPYING (HELD) for a booking that no
     longer occupies the agenda. No migration.
  D  phone_utils (canonical identity), services/lead_identity.py (new), api/leads.py (POST
     idempotent, GET by identity), ui/kanban_actions.ui_lead_create, models.Lead index,
     migration 20261102_lead_phone_identity (28 chars), schema_preflight.EXPECTED_HEAD.
  E  scripts/emergency_off/ (new, 8 files).
  F  conversation_engine._topics_already_answered_this_cycle + _CROSS_TURN_PATTERN_TOPICS.

================================================================================
5. CUSTOMER RECOVERY STATE MACHINE (A)
================================================================================
  confirm_booking → BookingAuthority → SLOT_CONFLICT (nothing written; rolled back)
    → finaliser: FINALISE/REFUSED event, offer.refused_count, offer stays ISSUED
    → ledger re-read: a committed booking for this submission wins (SUCCESS)
    → RECOVERY, identity R = sha256("slot-conflict-recovery|v1|offer:<losing offer id>")

  CLAIM      insert booking_attempt_events (NOTIFICATION/HELD/SLOT_RECOVERY_CLAIMED,
             command_key "slot-recovery:R", once per attempt) in its own committed session.
             Not admitted → STOP, nothing sent (replay, Meta retry, re-tap, concurrent request).
             DB error → STOP (fail closed).
  TEXT 1     gate(BOOKING_FLOW, text, delivery_identity sha256(R|text|1)) → Meta
               SENT (WAMID)           → FLOW
               definite non-delivery  → TEXT 2 (identity …|text|2) → SENT → FLOW
                                                                  → definite → HANDOFF
               ambiguous              → no retry, no Flow → HANDOFF
  FLOW       dispatch allowed? a free day in the 14-day horizon? Flow id configured?
             else → HANDOFF (no Flow)
             K1 lock; the thread must still hold the LOSING offer, else STOP
             (SUPERSEDED_MEANWHILE: something newer owns the conversation; text stands)
             clear the agreed slot (picker not narrowed to the lost slot);
             OfferLifecycle.issue(new token; same candidate, category and zone) — supersedes
             the losing offer (its token now fails closed); last_stage = SCHEDULING; commit
             resolve_context(new token) must succeed, else withdraw → HANDOFF
             gate(BOOKING_FLOW, flow, identity sha256(R|flow|1)) → data_exchange Flow
               SENT       → RECOVERED
               definite   → withdraw the new offer → HANDOFF
               ambiguous  → keep the offer (it may be on the handset) → HANDOFF (flag a person)
  HANDOFF    K1 first: state.last_stage=SCHEDULING, state.needs_human=true, then
             lead.necesita_humano=true. Evidence row (FAILED/HELD + typed reason).

  Customer copy (fixed): "Ese horario acaba de dejar de estar disponible. No se confirmó ningún
  turno. Podés elegir otro horario."  Flow body: "Estos son los horarios disponibles ahora.
  Elegí uno y confirmá tu turno."  No code, reason, exception or SQL ever reaches a customer.
  The Data Exchange response is unchanged (the refusal APPOINTMENT screen); the published Flow
  JSON is untouched.

  Guarantees, and their proofs:
    one durable claim per losing offer      UNIQUE event_uid; PG test pga_01 (6 offers × 6
                                            concurrent claimants → exactly one True each)
    ≤1 successful send per delivery identity gate admits an identity once, ever; ≤2 text
                                            attempts, the 2nd only after a definite failure
                                            (a05, a06, a07); ≤1 Flow (a08)
    replay sends and books nothing          a03 (dead token re-confirmed; direct second
                                            recovery NOT_CLAIMED)
    no booking for the loser                a01, pga_02
    Meta exactly-once delivery              NOT claimed.

  Defect found by PostgreSQL and fixed: the first correlation id was 40 characters;
  whatsapp_messages.correlation_id is VARCHAR(36) and the gate's pending row failed
  (PRE_SEND_FAILURE). SQLite does not enforce widths, so a01 now asserts every recovery row
  fits the column.

================================================================================
6. CRM PERSISTENCE MAPPING (B)
================================================================================
  Flow field (prepare_summary)  claim_type                   shown on the card as
  name                          FLOW_CUSTOMER_NAME           card title if the lead has none;
                                                             "Nombre (Flow)"
  email                         FLOW_CUSTOMER_EMAIL          "Email (Flow)"
  inspection_address            FLOW_INSPECTION_ADDRESS      "Dirección de inspección"
  seller_name                   FLOW_SELLER_NAME             "Vendedor"
  seller_phone                  FLOW_SELLER_PHONE            "Tel. vendedor"
  listing_url                   FLOW_LISTING_URL             "Publicación"
  date + time                   FLOW_REQUESTED_SLOT          "Turno solicitado"
  (not from the Flow)           current-focus candidate      "Vehículo", "Zona"
                                (whatsapp_thread_candidates)

  Each row: thread_id; cycle_key (current); turn_id "offer:<id>"; bound_to_candidate_id =
  the offer-bound candidate; producer BOOKING_FLOW_DETAILS / prepare_summary/1;
  status DRAFT; outcome HOLD; reason UNCONFIRMED_UNTIL_BOOKING; role NULL (so the locality
  binder can never read it — b10); claim_id = sha256(thread, type, value, predecessor id).
  Replay → nothing (b02). Correction → new row, predecessor.superseded_by_id (b03); A→B→A is
  a new live row, not a resurrection. Empty optional field → previous value stands. K1-first
  short session; never raises; no booking, revision, offer, lead or message touched (b01).
  Logs carry counts only (b07). Values are HTML-escaped on the card (b06). Nothing is copied
  into leads (b05 asserts lead.nombre stays NULL).

================================================================================
7. OPERATOR-NOTIFICATION GUARANTEES (C)
================================================================================
  only a committed booking         enqueue reads COMMITTED CREATE occupying ledger rows only
  SLOT_CONFLICT → none             c01 (the race: one ledger row, one notice, the winner's)
  idempotent replay → none         c02 (replay + second pass: still one row, SENT, attempts 1)
  cancellation → none              c04 (cancelled after commit → HELD BOOKING_NOT_OCCUPYING, no
                                   provider call) — new send-time guard
  provider retry ≠ two successes   c03 (4xx then ok → SENT, attempts 2, exactly one success)
  HELD is never sent later         c05 (HELD while disabled, flag turned on → still HELD, 0 sent)
  content from canonical state     c01: vehicle, place, date/time, customer wa.me digits from the
                                   revision / lead / contact rows
  no live number in source         c06: no 549…-shaped number in the new production modules;
                                   the kit holds none (numbers live only in its 0600 config).
                                   Tests use synthetic fixtures (5491100000xxx, the existing
                                   harness's pattern; 5490000000001/2 in the rehearsal, not a
                                   valid AR mobile) and the existing synthetic operator
                                   destination

  DEPLOYMENT-TIME PROCEDURE for the historical HELD row (NOT RUN; run once, in the deployment
  window, BEFORE OPERATOR_WHATSAPP_NOTIFY_ENABLED is ever set):
    measured read-only today: operator_notifications id=2 HELD attempts=0 sent_at NULL
    hist_revision_id=82, revision 82 CANCELADO, agenda_occupying=false.

    BEGIN;
    SET LOCAL lock_timeout = '5s';
    SELECT id, status, attempts, sent_at, hist_revision_id
      FROM operator_notifications WHERE id = 2 FOR UPDATE;
      -- expect exactly: 2 | HELD | 0 | NULL | 82   (anything else: ROLLBACK, stop)
    SELECT estado_revision, agenda_occupying FROM revisions WHERE id = 82;
      -- expect exactly: CANCELADO | f              (anything else: ROLLBACK, stop)
    UPDATE operator_notifications
       SET status = 'FAILED', last_error = 'SUPERSEDED_BOOKING_CANCELLED',
           next_attempt_at = NULL, claim_token = NULL, lease_expires_at = NULL,
           updated_at = now()
     WHERE id = 2 AND status = 'HELD' AND attempts = 0 AND sent_at IS NULL
       AND hist_revision_id = 82
       AND NOT EXISTS (SELECT 1 FROM revisions r WHERE r.id = 82 AND r.agenda_occupying);
      -- expect UPDATE 1                            (anything else: ROLLBACK, stop)
    COMMIT;
    The row is kept as evidence (no DELETE). FAILED is terminal and non-sendable (claim_next
    selects PENDING only), and the new guard would refuse it even if re-queued. It will be
    listed once on /api/ops/booking-attention as OPERATOR_NOTIFICATION_REQUIRED — expected.

================================================================================
8. DUPLICATE-LEAD CONCURRENCY PROOF (D)
================================================================================
  Identity  digits only: regexp_replace(telefono, '[^0-9]', '', 'g'); display form unchanged
            in leads.telefono. National vs E.164 forms remain distinct (owner rule, not regex).
  Database  CREATE UNIQUE INDEX uq_leads_phone_identity ON leads (<identity>)
            WHERE telefono IS NOT NULL AND telefono <> ''
  Create    POST /leads: INSERT … ON CONFLICT (<identity>) WHERE <predicate> DO NOTHING
            RETURNING id; loser reads the winner's row; an existing lead is never modified.
  Lookup    GET /leads?telefono matches the identity ("+549…" finds "549…"); order unchanged.
  CRM UI    ui_lead_create creates nothing when the identity exists (no 500).

  PostgreSQL 16, real threads on separate pooled connections:
    pgd_03  the observed sequence reproduced 6 of 6: both executions run GET (both EMPTY,
            asserted), meet at a barrier, POST ("+549…" and "549…"), link the thread →
            one lead, both callers got its id, thread linked to it, zero orphans.
    pgd_04  8 simultaneous creates (4 "+" / 4 digits) → one lead, exactly one created=True.
    pgd_02  the database itself refuses the '+' variant (SQLSTATE 23505); NULL and '' repeat.
  Lead 169 is not touched: no code path deletes, merges or edits a lead.

================================================================================
9. EMERGENCY-OFF REHEARSAL (E)
================================================================================
  Kit: scripts/emergency_off/{common,watcher_pid,ENABLE,WATCH,KILL}.sh, test_kit.sh,
  rehearse.sh, README.md — all 0644; always invoked `bash /exact/path/X.sh CONFIG`; config
  outside Git. KILL exits 0 only after in-container verification. WATCH runs KILL
  synchronously: 0 → exit 3 (pidfile removed); cannot start / non-zero → *** BLOCKER ***,
  INCIDENT_UNRECOVERED (0600), pidfile KEPT, exit 4. KILL never signals the watcher that
  invoked it. No pkill / pgrep -f / killall anywhere (asserted). Flow dispatch =
  message_type='flow'; operator notification = provider attempt only; HELD-without-attempt is
  logged, never a breach; an unreadable instrument is a breach; rehearsal mode refuses the live
  project and container names.

  test_kit.sh — 28 passed, 0 failed (no containers): breach → KILL with every file 0600; KILL
  missing (127) and failing (1) → BLOCKER, incident, pidfile retained; watcher returns only
  after KILL finished; live-name guard; no pattern kill; exact-PID ownership (stale, garbage,
  self, impostor with the kit path + "pkill"/"killall" in its command line survives; second
  claim refused; KILL_INVOKED_BY_WATCHER honoured).

  rehearse.sh — SEALED: --internal network, disposable PostgreSQL 16 on tmpfs from a
  schema-only dump (no rows), the LIVE image ridecheck-crm-backend:wild1-r3align-b31020ac
  (sha256:19b3cec2…) unmodified, blank provider credentials, synthetic numbers, per-run auth
  key. Final run /opt/ridecheck-builds/emergency-off-rehearsal-20261102T215812Z: 39 passed,
  0 failed.
    RUN 1  ENABLE armed 5 true / 2 false; watcher armed; 3 BOOKING_FLOW texts + 1 HELD notice
           injected → NO breach, HELD noted as withheld; 3 Flow dispatches injected → BREACH
           "Flow dispatches = 3 (max 2)" → watcher invoked KILL itself, waited, exit 3;
           verified from inside the container, independently of KILL's log: seven false,
           original allowlist (count + digest), Resend and SMTP blank, WhatsApp transport
           credentials blank, dispatch disallowed, readiness 200; sentinel whose command line
           carried "WATCH.sh <kit> <config> KILL.sh pkill -f killall" ALIVE, unrelated
           sentinel ALIVE, rehearsal shell ALIVE.
    RUN 2  OFF overlay made unreadable → BREACH → KILL non-zero → watcher exit 4, BLOCKER,
           INCIDENT_UNRECOVERED, pidfile retained; manual KILL then restored and verified OFF.
    Live containers' id, image, restart count and start time identical before/after.
  Two earlier rehearsal directories (…214904Z, …215129Z) are kept: the first stopped because
  the disposable backend lacked AUTH_SECRET_KEY (503 on /login); the second passed 37/39, the
  two failures being the rehearsal script's own exit-status capture (a `wait` in a subshell),
  fixed. Neither is a kit defect.

  The 2026-11-01 kit under /opt/ridecheck-builds was NOT modified and remains defective: any
  future window must instantiate this kit instead.

================================================================================
10. TEST RESULTS
================================================================================
Harness: ridecheck-crm-test:l4.7b2 (Python 3.12.13, SQLAlchemy 2.0.36, psycopg 3.2.3),
--network none (SQLite) / --internal (PostgreSQL), TZ=America/Argentina/Buenos_Aires,
-p no:cacheprovider, trees mounted read-only, memory-capped, one container at a time (2 CPUs
shared with the live stack). Frozen trees:
  pristine   git archive 6f20587              tree digest f420b2eb6e93…3ad1
  candidate  worktree snapshot (final, everything except this closeout)
                                             tree digest bd1cd317b3bd…fa23c

Milestone suites (requirement H → test):
  H1 FAQ → quote → Flow → booking            test_minimum_launch_e2e::test_h1 (CE derives
                                             vehicle, locality, payment FAQ, quote, offer;
                                             DX prepare + confirm; one ack; card; one notice)
  H2 booking information in CRM              b05, h1
  H3 exactly one customer acknowledgement    a02, a04, h1
  H4 exactly one operator notification claim c01, c02, c03, h1
  H5 replayed success: no duplicate          c02
  H6 two customers, one slot                 a01, a02, a13, b04, b05, pga_02 (concurrent, PG)
  H7 loser books a second slot               a04
  H8 notice per commit, never for refusal    c01
  H9 concurrent opening messages → one lead  pgd_03 (6/6), pgd_04
  H10 emergency breach restores OFF          rehearse.sh 39/39, test_kit.sh 28/28
  H11 no identity crossing                   a02, pga_02
  H12 no Meta generic-error recovery         a13 (screen unchanged; recovery out of band)

  tests/test_minimum_launch.py        42 passed (SQLite)
  tests/test_minimum_launch_e2e.py     1 passed (SQLite)
  tests/test_minimum_launch_pg.py     11 passed, 14 subtests (PostgreSQL 16)
  scripts/emergency_off/test_kit.sh   28 passed
  scripts/emergency_off/rehearse.sh   39 passed

10.1 SQLite — full suite, both frozen trees
  PRISTINE   62 failed, 7136 passed, 658 skipped, 9 errors, 7100 subtests (6:37)
  CANDIDATE  62 failed, 7178 passed, 669 skipped, 9 errors, 7127 subtests (5:32)
  (+42 passed / +1 = the new SQLite tests incl. e2e; +11 skipped = the 11 PG-gated new tests)

  failing identities    pristine 71    candidate 71
  NEW (candidate-only)  0
  GONE (pristine-only)  0
  COMMON                71, all 71 with byte-identical (normalised) messages
  No table to print: the identity sets are equal. Identical to R2's pristine baseline.

  Found and fixed on the way (not in the final tree): test_b07 failed only inside the full
  run — another suite's dictConfig/logging.disable silences app loggers; the test now
  re-enables them for itself and restores their state.

10.2 PostgreSQL — every PG-gated suite, both trees, one file per process, fresh clones
  Templates per R2 §5 (schema-only dump of live crm_test + alembic_version, then each side's
  own `alembic upgrade head`; down/up-graded variants; create_all + stamp; empty). Heads:
  pristine 20261027_w1f_conversation_memory, candidate 20261102_lead_phone_identity.

  FILE                                          PRISTINE            CANDIDATE           =
  test_convergence_p2_authority_pg              3 F / 34 P          3 F / 34 P          =
  test_convergence_p3_r1_ack_pg                 15 P                15 P                =
  test_convergence_p3_race_pg                   20 P                20 P (adapted)      =
  test_convergence_p4_pg                        1 F / 15 P          1 F / 15 P          =
  test_convergence_p4_r1_pg                     9 F / 20 P          9 F / 20 P (adapt.) =
  test_convergence_p4_strict_schema_corr._pg    23 skipped          23 skipped          =
  test_convergence_p4_strict_schema_pg          23 F / 98 P         23 F / 98 P         =
  test_l4_7w5_g37r_rv03_tester_reset_pg         1 F / 26 P          1 F / 26 P          =
  test_l4_7w5_g3_7f_clarification_lifecycle     133 P               133 P               =
  test_l4_7w5_g3_7p_offer_authority_pg          215 P               215 P               =
  test_l4_7w5_g3_7r_booking_projection_pg       15 P                15 P                =
  test_l4_7w5_g3_7r_r2_refusal_bounds_pg        12 P                12 P                =
  test_l4_7w5_g3_7r_r3_cycle_reset_bound_pg     21 P                21 P                =
  test_prewild_pw1_bounded_runtime_pg           41 P                41 P                =
  test_sb1_booking_representation_pg            31 P                31 P                =
  test_sb2a_booking_authority_pg                25 P                25 P                =
  test_sb2a_r2_hardening_pg                     18 P                18 P                =
  test_sb2a_r3_typed_plans_pg                   12 P                12 P                =
  test_sb2a_r4_window_scope_pg                  14 P                14 P                =
  test_sb2a_r5_lockwait_pg                      5 P                 5 P                 =
  test_sb2a_r7_temporal_pg                      8 P                 8 P                 =
  test_sb2b_option_b_pg                         48 P                48 P                =
  test_w1f_o1_ce_sla_successor_pg               22 P                22 P                =
  test_w1f_operator_notification_pg             1 F, 4 E            1 F, 4 E            =
  test_w1f_unanswered_alert_pg                  4 P                 4 P                 =
  test_w1f_w01_r2_outstanding_question          346 P               346 P (adapted)     =
  test_minimum_launch_pg (new)                  —                   25 P (11 tests+subt.)
  (counts are junit cases incl. subtests)

  failing identities    pristine 30    candidate 30    NEW 0    GONE 0    COMMON 30
  20 of the 30 common messages differ ONLY in the head name the stale literal is compared
  against (e.g. ['20261027_w1f_conversation_memory'] vs ['20261102_lead_phone_identity'],
  expected ['20261022_operator_notifications']) — the trees are at different heads by
  design. These are R2's inherited "stale pinned head literal" and "harness provisioning"
  identities, failing identically on both sides.

  Measured and corrected during the PG work:
   * correlation id 40 > VARCHAR(36) (§5) — production fix;
   * a Meta RETRY of the refused delivery got "session expired" after the recovery
     superseded the offer, breaking "one delivery, one answer" (p4r1_12). Production fix:
     _confirm_booking recognises, from durable rows only (the delivery's own FINALISE uid +
     the offer's recovery claim + offer SUPERSEDED), a retry of that exact delivery and
     answers the same refusal screen, writing and sending nothing (SQLite a03b; PG p4r1_12);
   * my harness passed CONV_P2_E3_WORKERS='' (collection error); p2 re-run on both sides with
     the corrected harness;
   * contract adaptations, in place and commented (OLD/NEW): p3r_02 / p3r_04 and p4r1_12 —
     a Data Exchange loser's offer is now SUPERSEDED by exactly one fresh offer and one
     explanation is sent; a fresh-IV resubmission of the dead Flow fails closed with no
     second refusal, recovery or message. Every no-booking, once-per-attempt and envelope-
     identity assertion is unchanged.

================================================================================
11. MIGRATION REHEARSAL (PostgreSQL 16)
================================================================================
  revision 20261102_lead_phone_identity (28 characters) ← 20261027_w1f_conversation_memory
  test_minimum_launch_pg::PG_I_* on a disposable database at head:
    upgrade at head                      index present and EXACTLY canonical (pg_get_indexdef)
    repeat upgrade                       no-op, unchanged
    downgrade with protected data        rows present → index dropped, leads digest
                                         byte-identical ("DROPPED (no row changed)")
    re-upgrade                           CREATED, canonical, leads unchanged
    adoption of an existing canonical    stamp parent over an upgraded schema → VERIFIED
    malformed adoption (×2)              non-unique same-expression index; unique index on the
                                         raw column → REFUSED, version and index unchanged
    pre-existing duplicate group         REFUSED, reports "lead_ids=[a, b]" and a 16-hex digest,
                                         never the digits; leads byte-identical (no winner)
    downgrade of a foreign index         REFUSED, nothing dropped
    clean downgrade + re-upgrade         as above
    create_all on PostgreSQL             builds the identical index (pgi_10)
  The harness's template chain also ran this revision's upgrade and downgrade on both
  directions for every template (§10.2).

  "Downgrade refusal with protected data" — stated precisely: the revision holds no data of
  its own; its downgrade can lose none, so it does not refuse on rows. It refuses an index it
  did not create. Rows present during the downgrade are proven untouched.

================================================================================
12. EXACT FINAL PATH SET (32 = 31 + this closeout)
================================================================================
  M backend/app/api/leads.py
  M backend/app/models.py
  M backend/app/services/booking_acknowledgement.py
  M backend/app/services/booking_flow_service.py
  M backend/app/services/conversation_engine.py
  M backend/app/services/operator_notification.py
  M backend/app/services/phone_utils.py
  M backend/app/services/schema_preflight.py
  M backend/app/ui/kanban.py
  M backend/app/ui/kanban_actions.py
  M backend/app/ui/kanban_view.py
  M tests/test_convergence_p3_race_pg.py
  M tests/test_convergence_p4_r1.py
  M tests/test_convergence_p4_r1_pg.py
  M tests/test_w1f_w01_r2_outstanding_question.py
  A backend/app/services/booking_flow_details.py
  A backend/app/services/booking_slot_recovery.py
  A backend/app/services/lead_booking_details.py
  A backend/app/services/lead_identity.py
  A backend/migrations/versions/20261102_lead_phone_identity.py
  A scripts/emergency_off/ENABLE.sh
  A scripts/emergency_off/KILL.sh
  A scripts/emergency_off/README.md
  A scripts/emergency_off/WATCH.sh
  A scripts/emergency_off/common.sh
  A scripts/emergency_off/rehearse.sh
  A scripts/emergency_off/test_kit.sh
  A scripts/emergency_off/watcher_pid.sh
  A tests/test_minimum_launch.py
  A tests/test_minimum_launch_e2e.py
  A tests/test_minimum_launch_pg.py
  A docs/Launch/2026-11-02_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION_CLOSEOUT_001.md

  Test adaptations, each in its file's own idiom and each explained in place:
    test_convergence_p4_r1.py              single-head pin: NEW head 20261102 on 20261027,
                                           chain below asserted unchanged
    test_convergence_p3_race_pg.py         p3r_02 / p3r_04: DX loser's offer SUPERSEDED by
                                           one fresh offer (§10.2)
    test_convergence_p4_r1_pg.py           p4r1_12: one recovery across three deliveries of
                                           one envelope; fresh IV on the dead Flow fails closed
    test_w1f_w01_r2_outstanding_question   j12 head pin (same); K02b/K02c: downgrade to the
                                           revision's parent BY NAME (a relative -1 now lands
                                           on the new revision) and compare versions to the
                                           tree's head; every assertion about 20261027's own
                                           refusal, message and round trip unchanged
  Not touched: /opt/ridecheck-crm-integration, docs/operations/RECOVERY_MANIFEST.md, Meta
  Flow JSON, Compose / env files, allowlists, live crm_test.

  PROPOSED COMMIT SUBJECT
    fix(launch): slot-conflict recovery, Flow details kept, one lead per phone, tested emergency OFF

  EXACT-PATH git add (NOT EXECUTED):
    git -C /opt/ridecheck-crm-minimum-launch add -- \
      backend/app/api/leads.py \
      backend/app/models.py \
      backend/app/services/booking_acknowledgement.py \
      backend/app/services/booking_flow_details.py \
      backend/app/services/booking_flow_service.py \
      backend/app/services/booking_slot_recovery.py \
      backend/app/services/conversation_engine.py \
      backend/app/services/lead_booking_details.py \
      backend/app/services/lead_identity.py \
      backend/app/services/operator_notification.py \
      backend/app/services/phone_utils.py \
      backend/app/services/schema_preflight.py \
      backend/app/ui/kanban.py \
      backend/app/ui/kanban_actions.py \
      backend/app/ui/kanban_view.py \
      backend/migrations/versions/20261102_lead_phone_identity.py \
      scripts/emergency_off/ENABLE.sh \
      scripts/emergency_off/KILL.sh \
      scripts/emergency_off/README.md \
      scripts/emergency_off/WATCH.sh \
      scripts/emergency_off/common.sh \
      scripts/emergency_off/rehearse.sh \
      scripts/emergency_off/test_kit.sh \
      scripts/emergency_off/watcher_pid.sh \
      tests/test_convergence_p3_race_pg.py \
      tests/test_convergence_p4_r1.py \
      tests/test_convergence_p4_r1_pg.py \
      tests/test_minimum_launch.py \
      tests/test_minimum_launch_e2e.py \
      tests/test_minimum_launch_pg.py \
      tests/test_w1f_w01_r2_outstanding_question.py \
      docs/Launch/2026-11-02_RIDECHECK_CRM_PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION_CLOSEOUT_001.md

================================================================================
13. DEPLOYMENT AND ROLLBACK REQUIREMENTS
================================================================================
  PRECONDITIONS (all three, in a separately authorised milestone)
   P1  Duplicate group 169/170 resolved under owner authorisation. The migration REFUSES while
       it exists (measured read-only today: exactly one group, identity digest
       ad98b5c31a4e8ed8…, leads [169, 170]; 169 has 0 threads, 0 revisions, CONSULTA_NUEVA).
       This milestone does not touch 169.
   P2  The §7 HELD-row procedure run before OPERATOR_WHATSAPP_NOTIFY_ENABLED is ever set.
   P3  A certified compatibility image exists (below) before the live migration.
  ORDER
   1  read live state; take a database backup
   2  run `alembic upgrade head` (CREATED or a typed refusal; nothing partial)
   3  only then deploy the image built from this branch (its EXPECTED_HEAD is the new revision,
      so it refuses to start on the old head — the ordering is enforced, not trusted)
   4  verify OFF posture; flags stay OFF; enabling is its own bounded window using
      scripts/emergency_off (instantiated with a 0600 config outside Git)
  ROLLBACK
   * application only, keeping the index: needs a COMPATIBILITY IMAGE — the previous release
     tree with schema_preflight.EXPECTED_HEAD = 20261102_lead_phone_identity and nothing else
     changed, certified by the strict-schema suite. The parent image (b31020ac, EXPECTED_HEAD
     20261027) refuses a database at this revision. With the index present the old POST /leads
     would answer a racing duplicate with commit_or_400's 400 instead of a second lead.
   * full: stop the new image → `alembic downgrade 20261027_w1f_conversation_memory` (drops
     only the index; no row changes; proven) → start the parent image.
  RECOVERY OF A REFUSED SLOT TEXT/FLOW in production: the thread is SCHEDULING + needs_human;
  the evidence rows say which (SLOT_RECOVERY_*). Nothing is retried automatically.

================================================================================
14. REMAINING ACCEPTED LAUNCH DEBT
================================================================================
  * Return-to-origin workday cutoff (NP-02) — deferred by owner decision; late distant slots
    remain offerable.
  * Single-resource availability (R1-F01) — accepted; a second professional must not be
    enabled until availability is professional-aware and NULL-professional rows have an owner.
  * F3a on the handset: the loser still sees Meta's generic error first; the explanation
    arrives in the chat seconds later. The Flow JSON is unchanged.
  * Payment copy authority (NP-05): the canonical constant names débito unprompted, while the
    launch statement says to name an excluded instrument only when asked. Not changed (copy +
    tests); F fixes only the repetition. Accepted: cash, transfer, Mercado Pago; no cheque,
    debit or credit card support is claimed anywhere.
  * NP-06 (outstanding-question bookkeeping) and NP-07 (availability answered with a question).
  * The CE nfm_reply path (`_converged_flow_book`) on SLOT_CONFLICT keeps its existing handoff
    (manual request + advisor copy); the live data_exchange Flow confirms through the Data
    Exchange path, which is the one corrected.
  * The recovery runs inside the Data Exchange request (two provider calls before the
    response); a Meta retry meanwhile finds the claim taken and sends nothing.
  * A recovery whose claimant dies between claim and send is not retried (fail closed); it is
    visible as a SLOT_RECOVERY_CLAIMED row with no outcome row.
  * Losing offers are superseded only when a fresh Flow is issued; after a handoff the losing
    offer stays ISSUED until it expires (as today).
  * The flood gate (3 per 60 s per recipient) can hold a recovery text in a very busy
    conversation; that is a definite non-send → handoff.
  * Repeated FAQ wording beyond payment; Instagram; further Meta Flow redesign; multi-
    professional availability; recurring origins; three clean Wilds.

================================================================================
15. LIVE ISOLATION
================================================================================
  Nothing written to live. Reads only: `git ls-remote`; read-only psql (PGOPTIONS
  default_transaction_read_only=on); one schema-only `pg_dump` (no rows); `docker inspect`;
  `docker exec … python` reading environment variable NAMES/booleans and counts/digests (no
  number printed); /login readiness. Every container created was disposable (mlh-*, mls-*,
  rce-*), on --none or --internal networks, and removed.

================================================================================
16. LIVE STATE, BEFORE AND AFTER
================================================================================
  read 2026-11-02T21:14:39Z (before) and 23:15:32Z (after); identical in every field:
  backend   122d61f95493  sha256:19b3cec2…44f8  restarts 0  started 2026-11-01T23:34:11Z
  postgres  5246fd86d829  sha256:71e27bf6…3ddf  restarts 0
  n8n       6ea80570b67b  sha256:2d70fafe…8d0b  restarts 0
  seven flags            all false
  allowlist              1 entry, sha256[:16] ad98b5c31a4e8ed8;  OPERATOR_WHATSAPP_TO unset
  readiness              200
  schema head            20261027_w1f_conversation_memory   (NOT migrated)
  max message id 6268;   pending outbound 0;   max security event id 735
  leads 55 (max 171), contacts 36, threads 36, thread_revisions 1, revisions 61,
  offers 2, attempt events 7, booking commands 10, thread evidence 20, users 0
  operator_notifications  1 SENT (attempts 1), 2 HELD (attempts 0, NOT_CONFIGURED_DISABLED)
  duplicate phone groups  1 (169/170, untouched)
