PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-INDEPENDENT-REVIEW

Date: 2026-11-03. Read-only independent review of the minimum-launch candidate on branch
integration/prelaunch-minimum-launch (/opt/ridecheck-crm-minimum-launch, parent 6f20587).
Nothing in the candidate was edited, staged, committed, pushed, tagged or stashed. Nothing was
built, deployed or migrated. No live flag, environment, allowlist or Compose file changed. No
provider was contacted. Lead 169 and operator notification 2 were read, not touched. The one
file written in the worktree is this audit (untracked, outside the candidate's 32-path set).

Method: each claim was rebuilt from source, from probes run against the candidate's own code
with controlled provider fakes in sealed containers (`--network none`, or `--internal` for
PostgreSQL), and from read-only live queries
(`PGOPTIONS=-c default_transaction_read_only=on`). The closeout was read in full and treated
as a set of claims to test, not as evidence. Probe scripts and raw outputs are kept in the
reviewer's scratchpad
(/tmp/claude-0/-root/cdf0a374-484f-451c-bb14-ffc9a701532d/scratchpad/review/).

================================================================================
0. VERDICT
================================================================================
CORRECTION REQUIRED

The candidate's core mechanics are correct in the cases it tested:
  * the loser books nothing;
  * recovery runs only for a typed SLOT_CONFLICT;
  * one claim per losing offer;
  * a replay is answered identically and sends nothing;
  * an ambiguous text is never retried;
  * no Flow is sent before the explanation;
  * one operator notice per committed booking;
  * one lead per phone under real concurrency;
  * the OFF kit's breach-to-KILL path works.

The verdict is CORRECTION REQUIRED because three of the prompt's explicit correction triggers
are met, and one pre-existing defect becomes reachable at launch:

  F-01  A claimed-but-unsent recovery is SILENT. After a process death, an unexpected
        exception or a contended handoff lock, the customer is stranded with no human flag,
        and nothing surfaces it (prompt rule D).
  F-02  The recovery depends on an UNBOUNDED, undocumented transport time. It runs inside the
        Data Exchange request, on an `async` route that blocks the single event loop
        (prompt rule C).
  F-03  A prepare_summary persistence failure is SILENT. The booking still proceeds, the
        seller phone has no other canonical copy, and the loser's details are lost with no
        flag (prompt rule E).
  F-04  Pre-existing and launch-exposed: the CRM lead card renders the customer's Flow-typed
        name and email unescaped (stored HTML injection into operator sessions).

The minimum contract scorecard is in §1. The exact corrections are in §15.

================================================================================
1. MINIMUM LAUNCH CONTRACT — SCORECARD
================================================================================
  1  Existing CRM intact                     YES, with F-04 (pre-existing XSS) and F-11
                                             (phone-edit 500)
  2  FAQ + quoting available                 YES (H1 e2e passes; SQLite NEW 0)
  3  Eligible customers reach Smart Booking  YES (unchanged dispatch; e2e)
  4  One booking per successful submission   YES (authority unchanged; replay = same answer)
  5  Submitted info visible in CRM           NOT RELIABLY — F-03 (silent loss)
  6  Loser gets a normal WhatsApp explanation YES on the happy path; NOT GUARANTEED — F-01,
                                             F-02
  7  Loser gets a usable path to a new slot  YES on the happy path; silent strand — F-01
  8  One operator-notification claim         YES (proven; guard added)
  9  Concurrent openings → one lead          YES (PG; migration needs P1, §9)
 10  Emergency OFF restores safe posture     YES (rehearsal re-run, §11); F-07 and F-08 are
                                             configuration and test hygiene issues

================================================================================
2. INDEPENDENT SOURCE AND SCOPE PROOF
================================================================================
  HEAD                   6f20587da9af0d49fbe0b20078cff555521c293b
  branch                 integration/prelaunch-minimum-launch
  git ls-remote origin refs/heads/integration/prelaunch-booking-convergence
                         6f20587da9af0d49fbe0b20078cff555521c293b
  git ls-remote origin refs/heads/integration/prelaunch-minimum-launch   (absent: never pushed)
  staged                 0 paths;  stash list: 0
  dirty path set         32 = 15 M + 17 ??, exactly the closeout §12 manifest
                         (before this audit). EXTRA: none. MISSING: none.
  Meta Flow JSON, Compose, .env, allowlist files: absent from the diff (grep of names and
  content).
  Operational-origin / return-journey behaviour: absent. No match for return
  cutoff/journey/trip, regreso, vuelta or end-of-day in any added line.

  Deferred worktree /opt/ridecheck-crm-integration (6f20587, branch
  integration/prelaunch-booking-convergence):
    git diff --binary sha256        9a5902fb…356a = evidence git_diff_binary.patch (identical)
    git status --porcelain          identical to evidence git_status_porcelain.txt
    every dirty path's sha256       sha256sum -c all_dirty_paths.sha256: OK
    review fingerprint before/after (HEAD + status + diff + untracked hashes) identical (§14)

  Overlap with the deferred candidate:
    EIGHT filenames are common, not the seven the closeout names. Also common:
    tests/test_convergence_p3_race_pg.py.
    Added lines shared with the candidate patch: models.py 2 (`__table_args__ = (` and `)`,
    syntax only); test_convergence_p4_r1.py 1 (the parent-revision literal); every other
    file 0.
    No candidate file or behaviour entered this branch (F-12: documentation only).

  Frozen comparison trees, re-verified byte-for-byte for this review:
    pgh/candidate  == worktree minus the closeout     (diff -rq: no differences)
    pgh/pristine   == git archive 6f20587             (diff -rq: no differences)

================================================================================
3. FINDINGS, BY SEVERITY
================================================================================
HIGH
  F-01  Claimed-but-unsent recovery is silent and permanent (§5, §6). Measured:
        * crash windows 2, 3, 4 and 5;
        * an unexpected Exception after the claim (outcome INTERNAL);
        * a contended K1 lock at handoff (`_handoff` returns False without a log line).
        In each case the result is the SLOT_RECOVERY_CLAIMED row only, with needs_human false
        on both the thread state and the lead. A Meta retry and a customer re-tap both send
        nothing. /api/ops/booking-attention lists committed bookings only and never reads
        these rows. Window 4 is the worst case: the customer was told "Podés elegir otro
        horario" and never received the Flow.
  F-02  Unbounded provider time inside the Data Exchange request (§6).
        * `urlopen(timeout=15)` bounds each blocking socket operation, not the call. DNS
          resolution is not bounded at all.
        * The recovery adds up to three sequential provider calls (text, text retry, Flow).
          Measured elapsed time is their sum (three 3 s calls took 9.1 s).
        * Worst case: at least 45 s, plus DNS.
        * The repository documents no Meta deadline (Meta publishes 10 s for Data Exchange
          endpoints; not re-verified in-session because Meta was not contacted).
        * `booking_flow_data_exchange` is `async def` and calls the synchronous service
          directly, on a single uvicorn worker (`CMD uvicorn app.main:app`, no --workers).
          Every other request, including Meta webhooks for other customers and the CRM,
          waits until the recovery finishes.
  F-03  prepare_summary persistence failure is silent (§7). Measured with a database error and
        with K1 contention: both customers see SUMMARY, the winner books, both cards show no
        Flow details, and nothing is flagged.
        * The winner's seller phone exists nowhere else: ThreadRevision and Revision have no
          seller-phone field, and confirm_booking does not read it.
        * The loser's details are lost entirely.
        * The winner's name, email, address, seller name and URL survive in thread_revisions,
          but the new card block reads only the evidence rows.
  F-04  PRE-EXISTING, launch-exposed: stored HTML injection on the CRM card.
        * `render_lead_card` emits `lead.nombre`/`apellido` (`{n}`) and `tel`/`email` (`_txt`,
          which does not escape) raw.
        * The Booking Flow writes the customer's typed name and email into the lead
          (LeadIdentity).
        * Probe: on the PRISTINE tree, `<img/src=x/onerror=alert(1)>` as nombre appears raw
          4 times in the card HTML.
        * The candidate's new block escapes every value (11/11 escaped). The title fallback
          escapes too, but the lead's own name path is not.

MEDIUM
  F-05  booking_attempt_events is used against its documented contract.
        * The model says the table is "Never authority" and "Read by nobody on a decision
          path: not admission, replay, …".
        * The recovery uses an insert into it as an ADMISSION, which the finaliser's
          refusal counting also does (precedent).
        * `_replayed_slot_refusal` READS it to decide a replay's answer.
        * CHECKs (stage NOTIFICATION, outcome HELD/FAILED), the 64-char reason, the 40-char
          uid and append-only are all respected. The meaning is not: a NOTIFICATION/HELD row
          here means "recovery claimed", whereas everywhere else it means an undelivered
          operator e-mail. That reading is safe only because ops_dashboard filters by
          hist_thread_revision_id, which recovery rows leave NULL.
  F-06  The notification-2 SQL relies on a human reading "expect exactly …"; an unexpected
        state does not roll back by itself. Revision 82 is read without a lock. The command
        id is not pinned (live: booking_command_id 9). A self-enforcing replacement is in §8.
  F-07  The kit's default MAX_FLOW_DISPATCHES=2 is now below the intended behaviour. A
        two-customer slot race sends three Flows (two originals plus one recovery), which is
        exactly the rehearsal's breach shape. The README does not say to raise it for a race
        window. MAX_NEW_ATTEMPTS=6 also has to absorb the recovery claim rows.
  F-08  The closeout's "test_kit.sh 28/28" does not reproduce on the final tree: 27/28. K6
        ("no pattern kill") hits rehearse.sh:182. That hit is the sentinel's fake argv string
        (`exec -a "… pkill -f killall" sleep`), not a kill. rehearse.sh was edited at 18:58,
        after test_kit's run at 18:47. There is no real pattern kill; the test or the
        sentinel string has to change.

LOW
  F-09  The duplicate-group "digest" printed by the migration is unsalted sha256(digits)[:16]
        of a 13-digit phone. It is brute-forceable in seconds, and equals the allowlist digest
        the kit logs (ad98b5c31a4e8ed8). This pseudonymises the number; it does not protect
        it. The same applies to LEAD_CREATED / LEAD_IDENTITY_REUSED log hashes.
  F-10  p4r1_12 no longer exercises "a fresh IV is a NEW attempt, counted once". The fresh IV
        now hits a superseded offer and fails closed. The property should be re-asserted
        where the offer stays ISSUED (e.g. after a text handoff).
  F-11  New unique index, unhandled callers:
        * ui_lead_update changing a phone to one another lead holds → IntegrityError → 500;
        * the CE website-form path `lead.telefono = form_data["phone"]` (only when the lead
          has no phone) can fail the turn;
        * ui_lead_create silently discards the operator's typed data when the phone exists;
        * non-empty, digit-free phones ("-", "s/n") all map to identity '' and collide in the
          index, while `canonical_phone_identity` treats them as "no identity". The second
          one raises IntegrityError.
        Live today: 0 such rows (all 49 phones start 549; 6 NULL/blank).
  F-12  Closeout §2.3 says seven common filenames; there are eight (see §2).
  F-13  Kit hygiene:
        * KILL has no overall timeout (a hung `docker compose` hangs the watcher);
        * the time between ENABLE and starting WATCH is a manual, unwatched gap;
        * rehearsal check "WhatsApp transport credentials blank" proves only the rehearsal's
          own base env. Live OFF keeps WHATSAPP_TOKEN set by design, and KILL does not and
          should not check it.
        Accurate wording is needed, not code.
  F-14  The card shows the thread's live Flow details regardless of cycle, so a later
        conversation shows earlier details until corrected. Informational.

Verified correct (no finding): scope isolation; loser writes no revision, ThreadRevision or
ledger CREATE; recovery entered only for BookingOutcome.SLOT_CONFLICT (a11); one claimant per
losing offer (UNIQUE event_uid; pga_01 rerun); the same delivery after recovery gets the
identical screen and writes nothing (a03b, p4r1_12); a fresh IV on the superseded token fails
closed; ambiguous text never retried; Flow only after a text with a WAMID; a definite Flow
failure withdraws the new offer and hands off; no K1 or row lock held across any provider call
(each K1 session commits before the send, and the gate commits its pending row before the
transport); no new lock-order edge (K1 → offer, K1 → lead, as elsewhere); customer copy fixed
text, no internals; the migration; the operator-notification guard; lead-identity concurrency.

================================================================================
4. SLOT-RECOVERY STATE MACHINE — TRACED
================================================================================
  routes/flow_data_exchange.py:116 booking_flow_data_exchange (async def; sync body)
   → BookingFlowService.handle_confirm_booking → _confirm_booking
   → resolve_context(token)
        BookingTokenError → _replayed_slot_refusal: the offer is SUPERSEDED AND both this
        delivery's FINALISE once-uid AND the offer's recovery claim uid exist → the same
        refusal screen, nothing written. Otherwise the error is re-raised.
   → admission (OfferLifecycle.admit) → BookingAuthority (one transaction)
   → result SLOT_CONFLICT → _refusal_reread / finaliser (FINALISE REFUSED, once per attempt;
     offer stays ISSUED) → ledger re-read missed
   → _recover_slot_conflict: request session rolled back (it holds nothing)
   → recover_slot_conflict
        CLAIM  insert_attempt_event(NOTIFICATION, HELD, SLOT_RECOVERY_CLAIMED, once) in its
               own committed session. Not admitted / DB error → STOP, nothing sent.
        TEXT   _send_ledger_acknowledgement(text=RECOVERY_TEXT, identity R|text|n), gate
               BOOKING_FLOW. WAMID → FLOW. Definite → attempt 2. Ambiguous → no retry →
               HANDOFF.
        FLOW   dispatch allowed? free day? flow id? → K1, the losing offer must still be
               ISSUED and mirrored → clear slot fields → OfferLifecycle.issue (supersedes) →
               SCHEDULING → commit → resolve_context(new) → gate(flow, R|flow|1) → transport.
               SENT → RECOVERED. Definite → withdraw → HANDOFF. Ambiguous → keep the offer +
               HANDOFF.
        HANDOFF K1 → state.last_stage=SCHEDULING, needs_human=true → lead.necesita_humano=true.
               K1 contended → returns False, NO log, NO flag  (F-01).
        any other Exception → outcome INTERNAL, NO handoff        (F-01).
   → the route returns the unchanged refusal APPOINTMENT screen (handset: Meta's generic
     error; F3a, accepted debt).

  Probe results, controlled fakes (SQLite, candidate tree):
    normal                    text sent, Flow sent, offers [SUPERSEDED, ISSUED], needs_human F
    4xx then ok               texts [failed, sent], Flow sent; 2 text calls
    ambiguous text            1 text call, 0 Flow, needs_human T (thread + lead)
    text ok, Flow 4xx         offers [SUPERSEDED, WITHDRAWN], needs_human T
    the loser's revisions     0 in every case

================================================================================
5. CRASH WINDOWS (process death simulated by a BaseException at each boundary)
================================================================================
  window                              durable after death         Meta retry       re-tap (fresh IV)   surfaced?
  1 before claim                      offer ISSUED; no claim      FULL recovery    token error (fails  n/a — self-heals
                                                                  (text + Flow)    closed, correct)    on retry
  2 claim, before text                claim only; 0 sent          nothing sent     nothing sent        NO  ← F-01
  3 Meta accepted, unrecorded         claim; outbound 'pending';  nothing sent     nothing sent        NO (only a stale
                                      customer HAS the text                                            pending row)
  4 text ok, before fresh Flow        claim; text sent; offer     nothing sent     nothing sent        NO  ← worst: told
                                      ISSUED                                                           "elegí otro", no Flow
  5 new offer committed, before Flow  claim; text sent; offers    nothing sent     token error         NO
                                      [SUPERSEDED, ISSUED]
  6 Flow ok, before return            complete recovery           same screen,     token error         n/a (customer has
                                                                  nothing sent                         text + Flow)
  also: Exception after claim → INTERNAL, 0 sent, no flag; K1 contended at handoff → 2 failed
  texts, needs_human false.

  Judgement on the accepted debt ("a claimant dying between claim and send is not retried"):
    detectable          only by an ad-hoc SQL query (a claim with no outcome row); no reader
                        exists in the application
    surfaces for human  NO: needs_human stays false, booking-attention never lists it
    operator can find   only with that SQL (hist_thread_id is on the row)
    customer retry      consistent (no booking, no duplicate), but it does nothing for them
  → NOT acceptable as written (prompt rule D). Correction C-1.

================================================================================
6. LATENCY AND FAILURE BOUNDARY
================================================================================
  case (fakes)                                   elapsed   provider calls   result
  normal                                         0.08 s    text 1, Flow 1   RECOVERED
  definite 4xx, then ok                          0.07 s    text 2, Flow 1   RECOVERED
  ambiguous text                                 0.05 s    text 1           HANDOFF
  text ok, Flow definite                         0.08 s    text 1, Flow 1   HANDOFF
  slow text 3 s                                  3.19 s    text 1, Flow 1   RECOVERED
  slow Flow 3 s                                  3.08 s
  slow text + Flow 3 s each                      6.08 s
  4xx 3 s + ok 3 s + Flow 3 s                    9.10 s    text 2, Flow 1   RECOVERED
  derived worst case (real transport)            ≥45 s + DNS (3 × urlopen 15 s per socket op)
  derived: one ambiguous timeout                 ≈15 s  > the 10 s Meta deadline

  Bounded transport timeout?   PER SOCKET OPERATION ONLY (urlopen timeout=15), never per
                               call or per request; DNS unbounded. No request budget exists.
  Concurrent Meta retry        with one uvicorn worker and a blocking body, the retry is
                               queued behind the first request. It then sees the superseded
                               offer + claim + FINALISE and returns the same screen (verified:
                               a03b/p4r1_12 and probe window 6). With more workers it would
                               see the claim and send nothing: harmless while the first runs,
                               a permanent strand if the first dies (F-01).
  Sessions / K1 during sends   none held. The request session is rolled back before recovery
                               and only reads afterwards (an open snapshot, no row locks).
                               Every K1 session commits before the next provider call. The
                               gate commits its pending row before the transport.
  Deadlock                     none found. Recovery takes K1 → offer and K1 → lead in separate
                               short sessions, the canonical order. The gate's recipient lock
                               is released before transport.
  Whole-process impact         the event loop is blocked for the full recovery (F-02). The
                               pre-existing winner acknowledgement has the same shape (one
                               send); the recovery triples the worst case.
  → CORRECTION REQUIRED (prompt rule C). Correction C-2.

================================================================================
7. DETAIL PERSISTENCE (B)
================================================================================
  Verified:
    * the rows are bound to the thread the token resolved, the admitted offer
      (turn_id "offer:<id>") and the offer-bound candidate;
    * replay writes nothing (claim_id includes the predecessor);
    * a correction supersedes;
    * an empty optional field keeps the previous value (dropped by submitted_values);
    * no booking, revision, offer, lead or message is written, and nothing is sent;
    * nothing is copied into leads;
    * the card joins the most recent thread → current_focus candidate (rejected when it
      belongs to another thread) → the live rows;
    * the block escapes every value;
    * the reads are scoped by thread id, so one customer cannot see another's details;
    * logs carry ids and counts only (b07).

  Failure behaviour (the priority question), measured:
                           prepare screens    confirm              winner card   loser card   flagged
    DB error in persist    SUMMARY, SUMMARY   SUCCESS, SLOT_REFUSAL  no Flow data  empty       no
    K1 contended (1.5 s)   SUMMARY, SUMMARY   SUCCESS, SLOT_REFUSAL  no Flow data  empty       no
  Winner: name, email, address, seller name and URL survive in thread_revisions (buyer_name
  'Ana', address 'x' in the probe). The card does not read them. seller_phone is not stored
  canonically anywhere. Loser: everything lost.
  → CORRECTION REQUIRED (prompt rule E). Correction C-3.

================================================================================
8. OPERATOR NOTIFICATION (C)
================================================================================
  Proven from source:
    * eligibility: enqueue reads only COMMITTED CREATE occupying ledger rows with revision +
      thread_revision, above the floor (live floor = 5), within 24 h;
    * SLOT_CONFLICT writes no ledger row, so no notice;
    * UNIQUE(booking_command_id) and UNIQUE(identity) mean replay creates nothing;
    * the new guard in build_summary holds a booking that is no longer occupying
      (BOOKING_NOT_OCCUPYING → HELD, no provider call);
    * at most 2 attempts (CHECK ≤ 2), the retry only after a definite failure, and the
      gate's per-attempt identity;
    * HELD → PENDING exists nowhere: the only PENDING write is record() on a row this claim
      holds in SENDING;
    * the content is built from revision, lead and contact rows;
    * the destination is env only (OPERATOR_WHATSAPP_TO) and validated.

  Live configuration read (presence only):
    OPERATOR_WHATSAPP_NOTIFY_ENABLED=false
    OPERATOR_WHATSAPP_TO              ABSENT
    OPERATOR_BOOKING_TEMPLATE_NAME    ABSENT
    OPERATOR_BOOKING_TEMPLATE_LANGUAGE ABSENT
    WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID   SET
  Enabling therefore needs all four values plus an approved 10-parameter template
  (TEMPLATE_PARAMETERS order). config_problem() otherwise holds every notice.

  Notification 2, read now:
    id 2 · HELD · attempts 0 · sent_at NULL · hist_revision_id 82 · booking_command_id 9
    last_error NOT_CONFIGURED_DISABLED · revision 82 CANCELADO, agenda_occupying=false
    ledger 9 = COMMITTED CREATE WHATSAPP_FLOW (cancelled by ledger 10, CANCEL API)

  Closeout procedure: the resulting state (FAILED) is terminal and non-sendable (claim_next
  reads PENDING only). The row is preserved, and the UPDATE predicates are sufficient.
  However, the rollback-on-unexpected-state is MANUAL (F-06). Self-enforcing replacement
  (NOT RUN):

    \set ON_ERROR_STOP on
    BEGIN;
    SET LOCAL lock_timeout = '5s';
    DO $$
    DECLARE n int;
    BEGIN
      PERFORM 1 FROM revisions WHERE id = 82 FOR SHARE;
      PERFORM 1 FROM operator_notifications WHERE id = 2 FOR UPDATE;
      IF NOT EXISTS (SELECT 1 FROM operator_notifications
                     WHERE id = 2 AND status = 'HELD' AND attempts = 0 AND sent_at IS NULL
                       AND booking_command_id = 9 AND hist_revision_id = 82
                       AND claim_token IS NULL) THEN
        RAISE EXCEPTION 'notification 2 is not in the expected state — nothing changed';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM revisions
                     WHERE id = 82 AND estado_revision = 'CANCELADO'
                       AND agenda_occupying IS FALSE) THEN
        RAISE EXCEPTION 'revision 82 is not cancelled/non-occupying — nothing changed';
      END IF;
      UPDATE operator_notifications
         SET status = 'FAILED', last_error = 'SUPERSEDED_BOOKING_CANCELLED',
             next_attempt_at = NULL, claim_token = NULL, lease_expires_at = NULL,
             updated_at = now()
       WHERE id = 2 AND status = 'HELD' AND attempts = 0;
      GET DIAGNOSTICS n = ROW_COUNT;
      IF n <> 1 THEN RAISE EXCEPTION 'expected 1 row, got %', n; END IF;
    END $$;
    COMMIT;
  It must run before OPERATOR_WHATSAPP_NOTIFY_ENABLED is ever set. The row will then show
  once on booking-attention as OPERATOR_NOTIFICATION_REQUIRED (expected noise).

================================================================================
9. DUPLICATE-LEAD MIGRATION (D)
================================================================================
  Verified:
    * identity = regexp_replace(telefono,'[^0-9]','','g'), predicate
      "telefono IS NOT NULL AND telefono <> ''". These are identical in phone_utils, models,
      the migration and the ON CONFLICT arbiter (d05 pins them). "+549…" and "549…" collide
      (pgd_02: 23505). NULL and '' repeat freely.
    * The lookup uses the same expression AND predicate, so the arbiter index is usable.
    * Concurrent create → one lead. Both callers get its id, both links write the same value,
      no orphan (pgd_03 6/6, pgd_04 8 racers; rerun §12).
    * An existing lead is never modified.
    * Upgrade order: certified server → SHARE ROW EXCLUSIVE lock (lock_timeout 10 s) →
      exact pg_get_indexdef match or REFUSE → duplicate groups or REFUSE (lead ids + 16-hex
      digest; see F-09) → CREATE → read-back.
    * Downgrade drops only the canonical index; it refuses a foreign one.
    * Rows unchanged across upgrade/downgrade/re-upgrade (pgi_*).
    * The revision id is 28 chars. Live alembic_version.version_num is VARCHAR(128); the
      Alembic default of 32 also fits.
  National vs E.164: live data has 49 phones, all starting 549 (35 × 13 digits, 14 × 12), 0
  national forms and 0 last-10-digit collisions. WhatsApp-originated leads carry Meta's wa_id
  (E.164 digits). Classification: ACCEPTABLE LAUNCH DEBT. Only a hand-typed national number
  in the CRM could create a parallel identity, and that is an operator action, visible on the
  board.

================================================================================
10. LEAD 169/170 REMEDIATION (read live; nothing executed)
================================================================================
  Live, read-only (no digits printed):
    lead 169  identity sha16 ad98b5c31a4e8ed8, 13 digits, no '+'; CONSULTA_NUEVA, flag NULL,
              nombre NULL, email NULL, necesita_humano false; created 2026-11-01 23:25:23.727
    lead 170  same identity; COORDINAR_DISPONIBILIDAD / ACEPTADO, name + email set,
              necesita_humano true; created 23:25:24.018 (0.29 s later)
    referents  (169 / 170)
      whatsapp_threads 0/1 · revisions 0/1 · booking_commands 0/2 · thread_revisions 0/1
      whatsapp_messages 0/12 · booking_offers 0/1 · thread_evidence 0/12 · candidates 0/1
      hybrid_decision_traces 0/5 · operator_notifications.hist_lead_id 0/1
      feedback_post_revision 0/0
      security_events / ai_events text mentioning lead 169: 0
    FKs to leads: revisions.lead_id, feedback_post_revision.lead_id (both 0 for 169).
    No trigger on leads.
  Lead 170 is the canonical lead of the successful journey.

  Proposed procedure (deployment window, owner-authorised, NOT RUN):
    1  pg_dump -Fc crm_test → a 0600 file in a new 0700 evidence directory; verify it with
       pg_restore -l.
    2  Archive lead 169 (read-only): SELECT row_to_json(l) FROM leads l WHERE id=169 → 0600
       file (it contains the phone; never printed). Record md5(row_to_json(l)::text) of
       169 and of 170 as MD5_169 / MD5_170.
    3  psql -v MD5_169=… -v MD5_170=… :
         \set ON_ERROR_STOP on
         BEGIN;
         SET LOCAL lock_timeout = '5s';
         SELECT set_config('remed.m169', :'MD5_169', true),
                set_config('remed.m170', :'MD5_170', true);
         DO $$
         DECLARE a leads; b leads; n int; fk record;
         BEGIN
           SELECT * INTO a FROM leads WHERE id = 169 FOR UPDATE;
           SELECT * INTO b FROM leads WHERE id = 170 FOR UPDATE;
           IF a.id IS NULL OR b.id IS NULL THEN RAISE EXCEPTION 'lead missing'; END IF;
           IF md5(row_to_json(a)::text) <> current_setting('remed.m169')
              OR md5(row_to_json(b)::text) <> current_setting('remed.m170') THEN
             RAISE EXCEPTION 'a lead changed since it was archived'; END IF;
           IF regexp_replace(a.telefono,'[^0-9]','','g') <> regexp_replace(b.telefono,'[^0-9]','','g')
              OR left(encode(sha256(convert_to(regexp_replace(a.telefono,'[^0-9]','','g'),'UTF8')),'hex'),16)
                 <> 'ad98b5c31a4e8ed8' THEN
             RAISE EXCEPTION 'identity mismatch'; END IF;
           IF a.estado <> 'CONSULTA_NUEVA' OR a.nombre IS NOT NULL OR a.email IS NOT NULL THEN
             RAISE EXCEPTION 'lead 169 is not the empty duplicate'; END IF;
           IF EXISTS (SELECT 1 FROM whatsapp_threads WHERE lead_id = 169)
              OR EXISTS (SELECT 1 FROM revisions WHERE lead_id = 169)
              OR EXISTS (SELECT 1 FROM booking_commands WHERE lead_id = 169)
              OR EXISTS (SELECT 1 FROM feedback_post_revision WHERE lead_id = 169)
              OR EXISTS (SELECT 1 FROM hybrid_decision_traces WHERE lead_id = 169)
              OR EXISTS (SELECT 1 FROM operator_notifications WHERE hist_lead_id = 169) THEN
             RAISE EXCEPTION 'lead 169 has a referent'; END IF;
           FOR fk IN SELECT conrelid::regclass AS t, a2.attname AS c
                     FROM pg_constraint k
                     JOIN pg_attribute a2 ON a2.attrelid = k.conrelid AND a2.attnum = ANY (k.conkey)
                     WHERE k.contype = 'f' AND k.confrelid = 'leads'::regclass LOOP
             EXECUTE format('SELECT count(*) FROM %s WHERE %I = 169', fk.t, fk.c) INTO n;
             IF n <> 0 THEN RAISE EXCEPTION 'FK referent in %.%', fk.t, fk.c; END IF;
           END LOOP;
           IF (SELECT count(*) FROM whatsapp_threads WHERE lead_id = 170) <> 1 THEN
             RAISE EXCEPTION 'lead 170 is not the canonical journey lead'; END IF;
           DELETE FROM leads WHERE id = 169;
           GET DIAGNOSTICS n = ROW_COUNT;
           IF n <> 1 THEN RAISE EXCEPTION 'deleted % rows', n; END IF;
           IF md5((SELECT row_to_json(l) FROM leads l WHERE id = 170)::text)
              <> current_setting('remed.m170') THEN
             RAISE EXCEPTION 'lead 170 changed'; END IF;
           IF EXISTS (SELECT 1 FROM leads WHERE telefono IS NOT NULL AND telefono <> ''
                      GROUP BY regexp_replace(telefono,'[^0-9]','','g') HAVING count(*) > 1) THEN
             RAISE EXCEPTION 'duplicate groups remain'; END IF;
         END $$;
         COMMIT;
    4  Then `alembic upgrade head` → expect `uq_leads_phone_identity=CREATED`.
  Non-destructive alternative, if deletion is refused: UPDATE leads SET telefono = NULL WHERE
  id = 169 (same predicates, archive kept). The index then admits it, and no false identity
  is created. Cost: a nameless, phone-less CONSULTA_NUEVA card stays on the board until an
  operator closes it.

  Owner authorisation required, verbatim:
    "I authorise, in the deployment window and after a verified backup, the deletion of
     exactly lead 169 — the empty CONSULTA_NUEVA duplicate of lead 170 under phone-identity
     digest ad98b5c31a4e8ed8 — using the predicate-checked procedure of audit 001 §10. If any
     predicate fails, nothing is deleted."

================================================================================
11. EMERGENCY OFF (E)
================================================================================
  Verified from source:
    * every script is `bash <path>`, and common.sh sources by BASH_SOURCE, so no exec bit is
      needed;
    * the pidfile is created with noclobber;
    * ownership needs an exact numeric live pid whose cmdline names THIS kit's WATCH.sh and
      THIS config, and that is not the caller or an ancestor (PPid from /proc/status);
    * stop is TERM → bounded wait → KILL of that pid;
    * no pkill/pgrep -f/killall in code (F-08 is a string in a sentinel's argv);
    * WATCH runs KILL synchronously (`KILL_INVOKED_BY_WATCHER=$$ bash KILL.sh`): 0 → exit 3
      (pidfile removed); otherwise BLOCKER + INCIDENT_UNRECOVERED (0600) + pidfile kept +
      exit 4;
    * an unreadable instrument is a breach (need());
    * KILL verifies from inside the recreated container: 7 flags false, dispatch disallowed,
      allowlist count + digest, Resend/SMTP blank, readiness 200, expected image;
    * a Flow dispatch is counted only for message_type='flow';
    * an operator notice counts only as a provider attempt; HELD with 0 attempts is noted,
      never a breach.
  Re-run here:
    test_kit.sh (copy, TMPDIR scratchpad)   27 passed, 1 failed (K6 — F-08)
    rehearse.sh (sealed, live image, synthetic numbers)   39 passed, 0 failed (OUTDIR review/rehearsal-*; live containers
                                            identical before/after inside the run)
  Issues: F-07, F-08, F-13.

================================================================================
12. TEST INTEGRITY
================================================================================
  Adapted historical tests, reviewed line by line:
    p3_race p3r_02/p3r_04   `stays ISSUED` → exactly [SUPERSEDED, ISSUED] for a DX loser; the
                            CE-loser branch is unchanged. Stronger, follows from contract A.
    p4_r1 (head pin)        new head + down_revision asserted; the chain below unchanged.
    w01_r2 j12 / K02        head literal → EXPECTED_HEAD; `downgrade -1` → the explicit
                            parent, so 20261027's own downgrade still runs and its refusal,
                            message, data and round-trip assertions are unchanged.
    p4r1_pg 12              same-delivery invariance kept (one FINALISE, one claim, one
                            message); fresh-IV is now "fails closed". Coverage loss → F-10.
  No skip/xfail/expectedFailure was added to any existing test. The new PG suite is
  skipUnless-gated on its own variables (11 tests).
  Production changed to satisfy tests? One case: _replayed_slot_refusal was added after
  p4r1_12 failed. It restores the pre-existing "one delivery, one answer" contract that the
  recovery broke. That is a legitimate production fix, not test-fitting.

  Fresh runs by this review:
    SQLite full suite, frozen trees (both verified byte-identical, §2):
      pristine   62 failed, 7136 passed, 658 skipped, 9 errors, 7100 subtests
      candidate  62 failed, 7178 passed, 669 skipped, 9 errors, 7127 subtests
      failing identities 71 / 71; NEW 0; GONE 0; common messages differing 0
    PostgreSQL, affected files + new suite, both trees:
      p3_race_pg              20 P / 20 P                NEW 0 GONE 0
      p4_r1_pg                8 fail ids both            NEW 0 GONE 0 (6 differ by head literal only)
      w01_r2 (PG-gated)       346 P / 346 P              NEW 0 GONE 0
      p4_strict_schema_pg     12 fail ids both           NEW 0 GONE 0 (6 head literal; 4 differ
                              only by the extra '20261102_lead_phone_identity: …' line appended
                              to the same failing assertion's output)
      minimum_launch_pg       — / 25 P (11 tests + subtests), incl. pga_01/02, pgd_01–04, pgi_*
  Prior evidence reused only for the remaining PG files (inputs verified identical: same
  frozen trees, same schema dump; live schema head unchanged at 20261027).

================================================================================
13. ACCEPTED DEBT
================================================================================
  The closeout does not claim to close: Meta's terminal-screen defect (F3a), return-to-origin
  cutoff, multi-professional availability, FAQ repetition beyond payment, Instagram, recurring
  professional origins. Each is listed in §14 as debt. Confirmed: no code in the diff
  addresses them.
  1  The customer sees Meta's generic error before the WhatsApp explanation: ACCEPTABLE for a
     minimum launch, PROVIDED the explanation is guaranteed to arrive or a person is flagged
     (needs C-1).
  2  Recovery synchronous inside the Data Exchange request: NOT ACCEPTABLE as built
     (unbounded, event-loop blocking: F-02). Acceptable only after C-2.

================================================================================
14. LIVE ISOLATION
================================================================================
  Read before and after this review (same instrument as the closeout):
  before 2026-11-03T03:24:06Z, after 04:03:11Z — IDENTICAL in every field (diff empty):
    container ridecheck-crm-backend-1 id=122d61f95493 image=sha256:19b3cec208b4d97d0618eec6035c7a9fb3cfd1dc9ebb2000c52e09e425eb44f8 restarts=0 started=2026-11-01T23:34:11.834969498Z
    container ridecheck-crm-postgres-1 id=5246fd86d829 image=sha256:71e27bf60b70bded003791b5573f8b808365613f341df20ffcf0c1ed7bc13ddf restarts=0 started=2026-10-21T23:31:05.793593905Z
    container ridecheck-crm-n8n-1 id=6ea80570b67b image=sha256:2d70fafea7c0c397f40df30daa31243c0bff590e9e87e0430398e7cb4d3d8d0b restarts=0 started=2026-10-21T23:31:12.210634184Z
    flag OUTBOUND_ENABLED=false
    flag SMART_BOOKING_CE_ENABLED=false
    flag OPERATOR_WHATSAPP_NOTIFY_ENABLED=false
    flag CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=false
    flag EVIDENCE_ADMISSION_ENABLED=false
    flag OUTSTANDING_QUESTION_ENABLED=false
    flag LOCALITY_RESOLUTION_SAFETY_ENABLED=false
    allowlist_count=1 allowlist_sha256_16=ad98b5c31a4e8ed8
    operator_to_set=False
    readiness=200
    alembic=20261027_w1f_conversation_memory
    max_message_id=6268
    pending_outbound=0
    max_security_event_id=735
    leads=55/171
    contacts=36
    threads=36
    thread_revisions=1
    revisions=61
    booking_offers=2
    booking_attempt_events=7
    booking_commands=10
    thread_evidence=20
    operator_notifications=1:SENT:1: 2:HELD:0:NOT_CONFIGURED_DISABLED
    dup_phone_groups=1
    users=0
  Worktree fingerprints (HEAD + porcelain + diff --binary + untracked sha256), before = after:
    /opt/ridecheck-crm-integration     68f056bedc0c1000
    /opt/ridecheck-crm-minimum-launch  cf4f9f7434001132   (taken before this audit file was written)
  Disposable objects remaining (rvw-*, rce-*, mlh-*, mls-*): 0.

================================================================================
15. EXACT CORRECTIONS REQUIRED
================================================================================
  C-1  (F-01) Make an incomplete recovery visible and owned:
       * Set the human flag (thread state needs_human + lead.necesita_humano) in the SAME
         transaction as the claim, or as the first step after it. Clear it only when the
         outcome is RECOVERED.
       * Add an ops attention reason (e.g. SLOT_RECOVERY_INCOMPLETE on
         /api/ops/booking-attention): a claim with no terminal recovery row after N seconds,
         with thread_url.
       * On K1 contention in _handoff: retry boundedly and log at ERROR. On any Exception
         after the claim: attempt the handoff.
       * Tests: crash windows 2–5, INTERNAL and contention, each ending flagged + listed.
  C-2  (F-02) Take the provider calls out of the Data Exchange request:
       * Record the claim as a durable recovery job and return the refusal screen at once.
       * Run text → Flow from a background loop (same shape as operator_notification_loop),
         in a thread, with a total per-call deadline.
       * Doing this also gives C-1 a natural retry/lease state.
       * If the owner insists on in-request recovery: a hard wall-clock budget ≤ 8 s total
         (connect + read + DNS), run off the event loop, and the deadline documented and
         tested.
       * Either way: run the DX handler's synchronous body off the event loop (def route, or
         run_in_threadpool).
  C-3  (F-03):
       * Persist the details again at confirm_booking (same function, same thread/offer
         binding), before the authority call, so the loser's and the winner's details do not
         depend on one earlier request.
       * Store seller_phone canonically for a booking.
       * On a persistence failure, mark the thread needs_human with a typed reason.
       * Let the card fall back to thread_revisions for a committed booking.
  C-4  (F-04) html-escape `n`, `tel` and `email` (and audit every `_txt` sink) in
       render_lead_card. Test with a markup payload in nombre, apellido and email.
  C-5  (F-06) Adopt the self-enforcing notification-2 SQL of §8.
  C-6  (F-07, F-08):
       * README/config: MAX_FLOW_DISPATCHES and MAX_NEW_ATTEMPTS for a race window.
       * Make K6 exclude the sentinel's argv string (or build it by concatenation), and
         re-run test_kit to 28/28.
  C-7  (F-05) Either amend the BookingAttemptEvent contract text to name the two new uses, or
       move the claim to the recovery-job table of C-2 (preferred; it removes the
       NOTIFICATION/HELD double meaning).
  Should-fix, not blocking: F-09 (stop printing an unsalted digest or label it as
  reversible), F-10, F-11 (catch IntegrityError in ui_lead_update and the CE form path; treat
  a digit-free phone as NULL identity in the index predicate or the API), F-12, F-13, F-14.

================================================================================
16. NEXT MILESTONE
================================================================================
PRELAUNCH-MINIMUM-VIABLE-SMART-BOOKING-LAUNCH-CORRECTION-R1. Implement C-1 to C-7 on
integration/prelaunch-minimum-launch, still uncommitted. Then:
  * re-run the crash-window and latency probes of §5–6 as committed tests;
  * re-run the frozen SQLite/PostgreSQL comparisons;
  * re-run test_kit (28/28) and the rehearsal.
Followed by a fresh independent review. The deployment milestone (P1 lead 169, P2
notification 2, P3 compatibility image) stays blocked until that review accepts.
