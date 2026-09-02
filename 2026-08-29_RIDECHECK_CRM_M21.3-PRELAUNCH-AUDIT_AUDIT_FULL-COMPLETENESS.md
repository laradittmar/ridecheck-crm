PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: M21.3-PRELAUNCH-AUDIT

DATE: 2026-08-29
AUDITOR: Claude (read-only — no code changes made)

---

AUDIT STATUS: PARTIAL

---

EXECUTIVE SUMMARY:

The RideCheck CRM codebase is architecturally sound and significantly hardened after the WILD-04R
series of fixes. 197/197 regression tests pass. All major lifecycle invariants are documented and
implemented. The outbound safety gate, cycle boundary reset, and business-authority chain are all
correctly implemented in code.

Three technical blockers must be resolved before enabling outbound or proceeding to closed beta:
(1) a database migration has not been applied to the live DB, which will cause the outbound safety
gate to fail on INSERT when OUTBOUND is enabled; (2) the webhook signature gate is in dev-bypass
mode because WHATSAPP_APP_SECRET is empty; (3) n8n has zero active workflows, meaning no inbound
message would reach CE from a live customer. The historical WA token in git history requires
rotation regardless of these.

External blockers (Meta App Secret unavailable, token rotation pending, Flow not published) are
known and treated as operational gates, not code defects.

---

RUNTIME TRUTH:

Container:         ridecheck-crm-backend:wild04r-f6-fd73611 (running 15 hours)
RC HEAD commit:    820f4d6 — test(WILD-04R-F6)
RC branch:         fix/m21.1.1-primary-flow-regression
Container drift:   NONE — all 8 key files match RC exactly
Production repo:   /opt/ridecheck-crm at 33caf3f — OLDER codebase, does NOT match RC
                   This is expected — the production repo is not the active deployment.
Running DB:        crm_test (PostgreSQL, connected via container env DATABASE_URL)
OUTBOUND_ENABLED:  false (confirmed in running container)
n8n:               Container running (up 7 days), port 5678 — but ZERO active workflows
CE direct:         CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED not set → defaults false (correct)
WHATSAPP_APP_SECRET: EMPTY in running container and all .env files

CRITICAL RUNTIME FINDING — UNAPPLIED MIGRATION:
  Applied alembic head in crm_test: 20260827_m21_3_thread_revision_zone_group
  NOT yet applied: 20260828_m2_authorized_path_monitoring
  Effect: whatsapp_messages table is MISSING columns path_id, deployment_id, correlation_id
  Consequence A: GET /security/outbound-ledger → HTTP 500 (column does not exist)
  Consequence B: When OUTBOUND_ENABLED=true, the outbound safety gate will attempt to INSERT
                 WhatsAppMessage with path_id/deployment_id fields → PostgreSQL error → sends blocked
  This migration MUST be applied before enabling outbound.

---

TEST SUITE:

Platform: Python 3.12.13, pytest-9.1.1
Location: /tmp/rctest3 (inside container ridecheck-crm-backend-1)
Run time: 170.64 seconds

RESULTS:
  197 passed
  0 failed
  0 skipped
  0 xfailed
  18 subtests passed
  140 warnings (all datetime deprecation — non-blocking)

COVERAGE BY AREA:
  Outbound safety gate (M19)                  .... 15 passed
  Kill switch / outbound gate (M19-F2)        .... 26 passed
  Blocked dispatch (M20)                      ....  9 passed (+ 18 subtests)
  Authorized paths / path monitoring (M2)     .... 28 passed
  Hardening / webhook sig (M21.3)             .... 25 passed
  Booking flow backend (M21.3-C-D)            .... 47 passed
  Agenda/Calendar UX2                         .... 21 passed
  UX2 runtime                                 .... 10 passed
  Agenda contact + revision linkage (UX3)     .... 16 passed

UNTESTED PATHS (documented gaps):
  - routes/whatsapp.py:429 — n8n vs CE direct dispatch branch: NO automated test
  - Live n8n → CE end-to-end: NO automated test (acknowledged in CLAUDE.md)
  - Production DB migrations: tests use SQLite in-memory, not PostgreSQL schema

---

PART 1 — DEPLOYMENT / RUNTIME TRUTH (Detailed)

RC GIT:
  HEAD:   820f4d6cab685b337704b096dad408bdde0a96d6
  Branch: fix/m21.1.1-primary-flow-regression
  Working tree: many files modified/untracked beyond last commit (M21.3-UX2/UX3/booking work)

CONTAINER IMAGE: ridecheck-crm-backend:wild04r-f6-fd73611

FILE DRIFT CHECK (container vs RC working tree):
  conversation_engine.py:        MATCH
  outbound_safety_gate.py:       MATCH
  routes/whatsapp.py:            MATCH
  schedule.py:                   MATCH
  booking_flow_service.py:       MATCH
  kanban_view.py:                MATCH
  kanban.py:                     MATCH
  lead_lifecycle.py:             MATCH

PRODUCTION REPO (/opt/ridecheck-crm):
  HEAD: 33caf3fc1ebce5001f3fb461e5f1f6558446b2a4
  conversation_engine.py: DIFFERS from RC (older version)
  This repo is NOT what the container serves. The container serves the RC image. The production
  repo is an older branch. No confusion about which code is live.

CONCLUSION: Running code matches RC. No source/runtime drift.

---

PART 2 — INBOUND TRANSPORT PATH

Meta → POST /integrations/whatsapp/webhook (n8n receives raw payload)
  → n8n: persist WhatsAppMessage (direction=in) [NOT YET — n8n inactive]
  → n8n: audio → Whisper transcription
  → n8n: 20-second debounce
  → n8n: POST /api/conversation/handle (CE)

ROUTE VERIFICATION:
  POST /integrations/whatsapp/webhook    EXISTS — routes/whatsapp.py:143
  GET  /integrations/whatsapp/webhook    EXISTS — hub verification: 403 on wrong token (correct)
  Status updates                         HANDLED in same POST endpoint (statuses loop lines 491-596)
  CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED: false (defaults from settings.py:32) — CORRECT
  n8n_webhook_url: http://n8n:5678/webhook/ridecheck-inbound — configured in compose

BLOCKER FINDING — N8N ZERO ACTIVE WORKFLOWS:
  curl http://localhost:5678/api/v1/workflows?active=true → {"data": []}
  n8n container is running but no workflow is active.
  Result: inbound messages from Meta would hit n8n but no workflow would process them.
  CE would never receive events from live customer messages.
  Owner must activate the n8n workflow before live traffic can flow.

Message idempotency: TWO LAYERS
  Layer 1 — webhook: SELECT wa_message_id before INSERT; race-condition IntegrityError caught
  Layer 2 — CE: last_processed_inbound_wa_message_id cursor dedup → skipped_dedup return
Burst handling: n8n 20-second debounce (n8n-side only; CE has no backend debounce)
Audio: n8n Whisper node transcribes before CE call
Image: n8n GPT-4o description before CE call
Context assembly: DB-authoritative burst reconstruction (WILD-04R-F1) — compensates for n8n limit=10
Duplicate processing: content fingerprint dedup on outbound gate prevents double-send on retry

LEGACY N8N AI PATH: Still physically present in n8n workflow but unreachable in practice
  (CE returns handled=true for all real conversations). Path is also blocked at the outbound gate
  (LEGACY_N8N_AI_PIPELINE → BLOCKED_UNAUTHORIZED_PATH + SecurityEvent).

---

PART 3 — BUSINESS-PROCESS AUTHORITY

LLM AUTHORITY REVIEW:
  Pricing/quote:      NO — PricingService is sole authority. AI prompt Rule 8: "NUNCA inventes
                      precios". _PRICE_RE and _QUOTE_INTENT_RE scrub hallucinated values.
  Eligibility:        NO — _check_inspectability_gate() deterministic. Disassembled → decline.
  Scheduling slots:   NO — ScheduleService provides all slots. AI renders, not decides.
  Booking creation:   NO — _process_flow_response() and BookingFlowService are deterministic.
  tipo_vehiculo:      NO (for known vehicles) — WILD-04R-F6: _catalog_tipo_for() overrides AI
                      proposal when catalog has the vehicle. Unknown vehicles: AI accepted as-is.
  Human escalation:   YES (by design) — AI can set needs_human=true. Cannot un-escalate.

LIFECYCLE PIPELINE (functional equivalent):
  UNDERSTAND:   DB-authoritative burst, current_turn_text assembled
  RECONCILE:    _apply_candidate(), zone correction (F3-T2), candidate dedup (F3-T3)
  DECIDE:       Layer A-F gates; stage-based routing; AI call when deterministic path insufficient
  CALCULATE:    _compute_price_quote() — always deterministic, re-runs after any mutation
  COMPOSE:      _build_quote_reply() or AI, _compose_secondary_answers() (FAQ supplement F3)
  VALIDATE:     _scrub_scheduling_confirmation(), price scrubbers, _apply_required_next_question()
  SEND:         _send_text_to_wa() → OutboundSafetyGate → Meta API

AI FALLBACK BYPASS: None found. All replies flow through _send_text_to_wa() → gate.

MINOR FINDING: _handle_general_information_ai() (FAQ path) sends AI reply without price-scrub.
  Relies on prompt rules only. Acceptable since this path only fires when no commercial evidence
  is in the burst. Not a launch blocker.

---

PART 4 — VEHICLE / CANDIDATE LOGIC

Vehicle extraction:        lookup_vehicle() → catalog deterministic resolver on full burst text
Category resolution:       VehicleCatalog authoritative. LLM may propose marca/modelo; tipo derived
Catalog override (F6):     _catalog_tipo_for() in _apply_candidate() create+update paths
LLM cannot override tipo:  YES — for catalog-known vehicles (Peugeot 2008 → SUV_4X4_DEPORTIVO)
SUV/4x4 aliases:           _normalize_tipo_vehiculo() maps all variants → SUV_4X4_DEPORTIVO (F5)
Unknown vehicle:            AI tipo accepted; enters qualification normally
Motorcycle/scooter/quad:   Layer A gate. With WHATSAPP_FLOW_ID: persists MOTO, dispatches Flow.
                           Without Flow: immediate needs_human handoff
Disassembled vehicle:       _detect_disassembled_vehicle() → deterministic decline, no AI
Multiple candidates:        prior demoted to "mentioned"; focus tracked by current_focus_candidate_id
Historical isolation:       watermark filter (created_at >= current_cycle_started_at)
D2 archive:                 _execute_cycle_reset() archives prior current_focus before new watermark

FINDING — BOOKING FLOW CANDIDATE LOADING:
  BookingFlowService._load_focus_candidate() (line 280) uses `.order_by(updated_at.desc()).limit(1)`
  WITHOUT filtering by current_cycle_started_at. For a returning customer with prior-cycle
  candidates, the most recently updated candidate could be from a prior cycle.
  CE's _focus_candidate() is properly watermarked; this service method is not.
  Severity: MEDIUM. The booking Flow is only triggered after CE sets flow_booking_token on the
  correct current-cycle candidate, so in practice the focus candidate in state is already current.
  But the resolution method is not cycle-safe by itself.

---

PART 5 — LOCATION / ZONE / PRICING

Pricing pipeline:
  tipo_vehiculo → PricingRepository.find_base_price(canonical_tipo) [DB]
  zone_group + zone_detail → PricingRepository.find_zone_by_group_and_detail() [DB]
  total = base_price + viaticos

AI cannot invent price:     YES — scrubbers + prompt rules
Quote recompute on change:  YES — F3-T2 zone change in QUOTED → reset flag/stage + recompute
PricingNotFoundError:       CE asks for missing info rather than guessing price
Active candidate authority: _get_active_inspection_location(ctx, state) — candidate-first, F4
Historical location leak:   NONE — state.home_zone_* cleared at reset; candidates watermarked

FINDING — PRICING ALIAS INCONSISTENCY:
  PricingService._canonical_vehicle_type() maps "SUV 4X4" → "SUV/4x4" (pricing.py:87)
  CE normalizes all variants to "SUV_4X4_DEPORTIVO" before pricing.
  If pricing DB has only "SUV_4X4_DEPORTIVO" and not "SUV/4x4" as a key, the pricing.py alias
  could return PricingNotFoundError for some paths. In practice CE always normalizes before calling
  PricingService, so the pricing.py alias is unreachable from the live path. LOW risk.
  Recommend: verify pricing DB seed data has both keys for defense in depth.

CABA/GBA coverage: Deterministic zone resolver. Off-coverage → _handle_out_of_coverage()
                   → deterministic needs_human escalation

---

PART 6 — QUOTE / ACCEPTANCE

Quote generation:         PricingService — deterministic
Price persistence:        WhatsAppThreadCandidate fields + state.home_zone_* after compute
Duplicate quote:          content fingerprint dedup in OutboundSafetyGate prevents re-send
Quote acceptance:         _is_acceptance() — natural language matching; AI path for edge cases
Rejection/uncertainty:    AI handles; deterministic stage guard prevents premature advancement
Correction after quote:   F3-T2 zone correction → stage reset → re-quote; vehicle change → re-quote
Candidate switch:         new candidate → focus switch → re-quote from new vehicle+zone
Revised quote generation: _compute_price_quote() re-runs after every mutation — never cached
Transition to scheduling: lead.flag = ACEPTADO → SCHEDULING stage
Required details:         _apply_required_next_question() finalizer (F5.1) — deterministic append

REPEATED MESSAGE BEHAVIOR: Resolved by content fingerprint dedup in OutboundSafetyGate.
  The same normalized text cannot be sent twice within the 10-minute DEDUP_WINDOW.

---

PART 7 — CUSTOMER DATA / META FLOW PATHS

AUTO path:
  Vehicle detected → zone detected → PricingService → quote → acceptance → scheduling →
  flow_booking_token generated → CE dispatches booking Flow → customer submits → BookingFlowService

MOTO/manual path (with WHATSAPP_FLOW_ID configured):
  Motorcycle keyword detected (Layer A gate) → MOTO candidate created (status='mentioned') →
  Contact Data Flow dispatched → flow_booking_token for moto set in state →
  Flow response received → _process_motorcycle_contact_response():
    - Persist fields (MOTO, make, model preserved)
    - Set needs_human = True
    - Text: "A la brevedad uno de nuestros asesores se estará contactando con vos."
  needs_human NOT set prematurely — CE waits for Flow response

MOTO/manual path (without WHATSAPP_FLOW_ID):
  Immediate deterministic handoff → needs_human = True
  "Perfecto, para avanzar necesito..." — this branch handled differently

MOTO Flow text verification: _MOTORCYCLE_CONTACT_INTRO constant (CE ~line 318) begins
  "Perfecto, para avanzar necesito que completes estos datos." — CONFIRMED

---

PART 8 — SCHEDULING ENGINE

Business hours (verified against contract):
  Monday:    13:00–18:00    ✓ (schedule.py:368)
  Tuesday:   09:30–14:00    ✓ (schedule.py:370)
  Wednesday: 09:00–18:00    ✓ (schedule.py:372)
  Thursday:  09:00–14:00    ✓ (schedule.py:374)
  Friday:    09:00–18:00    ✓ (schedule.py:376)
  Saturday:  09:00–15:00    ✓ (schedule.py:378)
  Sunday:    CLOSED          ✓ (schedule.py:380)

Zero zones (verified):
  All days: ZERO_ZONE_GROUP = "Norte" ✓
  Mon (alternating): MONDAY_SANTA_ANCHOR=2026-08-17; Santa Catalina / Melo y Panamericana ✓
  Tue, Thu, Sat: Santa Catalina ✓
  Wed, Fri: Melo y Panamericana ✓

Travel (ZoneTravelProvider, travel.py):
  Same group:               30 min ✓
  CABA ↔ Norte/Oeste/Sur:  60 min ✓
  Cross-GBA groups:         90 min ✓
  Unknown group:             0 min (no constraint — safe fallback)

Service duration: SERVICE_MINUTES = 45 ✓
CANCELADO/REPROGRAMAR excluded: _NON_OCCUPYING_ESTADOS ✓
Operating hours enforced: hard boundary check in check() and _is_travel_valid_slot() ✓
No live Maps dependency: ZoneTravelProvider is fully hardcoded zone-group lookup ✓
SCHED-01 parser fix: date.fromisoformat() with validation used throughout; no broken parser found

---

PART 9 — BOOKING FLOW BACKEND

Route:        POST /integrations/whatsapp/flows/booking/data-exchange
Status:       HTTP 405 on GET (correct — POST-only); route EXISTS
Crypto:       RSA-OAEP (SHA-256) + AES-128-GCM implemented in booking_flow_service.py

ACTIONS VERIFIED:
  INIT:             resolve_context() → _available_dates() → APPOINTMENT screen ✓
  date_selected:    ScheduleService.list_slots() → time slots for date ✓
  prepare_summary:  server-authoritative SUMMARY from DB state, not Flow payload ✓
  confirm_booking:  advisory lock → slot revalidation → atomic commit ✓

Key properties:
  14-day horizon:              BOOKING_HORIZON_DAYS = 14 ✓
  Dynamic slot generation:     ScheduleService called per date, server-computed ✓
  Server-authoritative summary: vehicle/location from DB, not Flow ✓
  Booking token:               make_booking_token() — thread_id + timestamp + secrets.token_hex(8) ✓
  Token stored in:             whatsapp_thread_states.flow_booking_token (NOT separate table) ✓
  Idempotency:                 token consumed (set to None) in same atomic commit ✓
  Advisory lock (Postgres):    pg_try_advisory_xact_lock on date string hash ✓
  Final slot revalidation:     ScheduleService.check() at confirm_booking time ✓
  Conflict behavior:           BookingSlotConflictError raised; token NOT consumed → retry allowed ✓
  Malformed token:             BookingTokenError raised with detail ✓
  Atomic persistence:          ThreadRevision + Revision + Lead + State in one db.commit() ✓
  Token expiry:                TOKEN_MAX_AGE_SECONDS = 7200 (2 hours) ✓

EXTERNAL GATE: Flow is DRAFT, not published. Private key FLOW_BOOKING_PRIVATE_KEY_PATH not
  configured. decrypt_flow_request() would raise ValueError on any real encrypted request.
  Backend code: READY. Meta connection: NOT YET OPERATIONAL.

NOTE — CANDIDATE WATERMARK GAP: _load_focus_candidate() in BookingFlowService does not filter
  by current_cycle_started_at (see Part 4). Acceptable for now since CE controls token issuance.

---

PART 10 — AGENDA / CRM OPERATIONAL LINKAGE

UX considered accepted by owner (visual review completed on desktop + mobile).
Auditing functional correctness only:

Appointment → Revision:     /kanban?open_lead={lead.id}&open_rev={revision.id} (test UX3-09 ✓)
Revision → Appointment:     /calendar?highlight_lead_id=...&week=...&date=...#day (test UX3-10 ✓)
WA action:                   /whatsapp/thread/{thread_id} — resolves from thread_by_lead dict ✓
No WA thread:                WA button not rendered (UX3-03 ✓)
Llamar:                      href="tel:{Lead.telefono}" — canonical customer phone (UX3-05/06 ✓)
Seller phone:                cannot replace customer phone (UX3-08 ✓)
Address/Maps/Waze:           inspection address from Revision (UX3-14 ✓)
CANCELADO cards:             rendered but do not affect travel routing (test_ux2_18 ✓)
Duplicate appointment:       not possible through navigation (read-only links)

---

PART 11 — ACTIVE-CYCLE RESET

cycle_reset_pending column:  EXISTS in crm_test DB (type=boolean, NOT NULL, default=false) ✓
set_lead_estado() wrapper:   ALL 5 CRM endpoints confirmed using it:
  PATCH /leads/{id}           api/leads.py:78 ✓
  POST /ui/lead_update        kanban_actions.py:220 ✓
  POST /ui/move               kanban_actions.py:242 ✓
  POST /ui/move_lead          kanban_actions.py:289 ✓ (via ui_move_lead())
  POST /ui/lead/{id}/move     kanban_actions.py:303 ✓

Brand-new Lead safety:       create_lead() (api/leads.py:42) sets estado directly without calling
  set_lead_estado() → NO spurious cycle_reset_pending=True for new leads ✓

No-op CONSULTA_NUEVA:        old_estado != "CONSULTA_NUEVA" check prevents no-op trigger ✓

CE consumption (_execute_cycle_reset()):
  Archive prior current_focus candidates (D2) ✓
  Capture previous_cursor ✓
  Query DB burst ✓
  Set new watermarks (current_cycle_start_message_db_id, current_cycle_started_at) ✓
  Clear all ACTIVE_REVISION fields (WhatsAppThreadState + Lead) ✓
  cycle_reset_pending = False ✓
  Commit ✓

Post-reset reload (F2):      ctx.candidates + ctx.db_messages re-queried with new watermarks ✓
Stale leak prevention:
  Prior candidates: archived (D2) + watermark filter ✓
  Prior zone: home_zone_* cleared ✓
  Prior quote: flag/stage cleared → re-quote forced ✓
  Prior acceptance: flow_booking_token cleared ✓
  Prior booking: current_revision_id cleared ✓

Preserved across reset:      Contact, Thread, Lead identity, message history, all Revision rows ✓

OPEN GAP (owner decision pending):
  POST /ui/human and POST /ui/lead_toggle_humano write lead.necesita_humano only.
  state.needs_human is NOT written by these endpoints.
  Clearing lead.necesita_humano via CRM without a CONSULTA_NUEVA transition leaves CE permanently
  suppressed (state.needs_human still true). Full cycle reset is the safe workaround.
  This is documented in CONVERSATION_RUNTIME_CONTRACT.md §2 as requiring owner decision.

LIVE DB STATUS: cycle_reset_pending=TRUE on 2 threads — these will reset on next inbound message.

---

PART 12 — HUMAN HANDOFF

Conditions that set needs_human (all confirmed in CE):
  1. Motorcycle (no Flow): immediate → needs_human=True
  2. Phone call escalation: → needs_human=True
  3. Vehicle fallback Flow (unresolvable tipo): → needs_human=True
  4. Location fallback Flow (unresolvable zona): → needs_human=True
  5. Out-of-coverage location: → needs_human=True
  6. AI decision: decision.get("needs_human")=True → accepted
  7. Flow failure: → needs_human=True, estado=ATENCION_HUMANA
  8. Scheduling escalation: → needs_human=True, estado=ATENCION_HUMANA
  9. Booking created: → needs_human=True (human must confirm appointment)
  10. Flood gate: → needs_human=True (in dedicated gate session)
  11. Motorcycle Flow response: → needs_human=True
  12. Inspectability repeated unresolved: → needs_human=True

Reason persistence:    Lead.estado=ATENCION_HUMANA for paths 7+8. All others: log only.
                       No dedicated needs_human_reason column. Not a launch blocker.
Permanent lock:        NONE — _execute_cycle_reset() clears state.needs_human=False explicitly.
Reset clears both:     state.needs_human and lead.necesita_humano both set to False/False at reset.
Unknown location:      deterministic needs_human escalation ✓
Unsupported vehicle:   deterministic handling for motorcycle, disassembled ✓

OPEN GAP: needs_human / lead.necesita_humano sync (same as Part 11 gap).

---

PART 13 — OUTBOUND SAFETY / TRACEABILITY

Gate: OutboundSafetyGate (outbound_safety_gate.py)

Authorized paths (all 7 confirmed in outbound_path_registry.py):
  CE_TEXT, CE_FLOW, CE_INTERACTIVE, CE_LIST, MANUAL_CRM, BOOKING_FLOW, SYSTEM_NOTIFICATION

Legacy path: LEGACY_N8N_AI_PIPELINE → BLOCKED_UNAUTHORIZED_PATH + LEGACY_SENDER_REACHED event ✓
Unknown path (None): OUTBOUND_ATTEMPT_WITH_UNKNOWN_SOURCE + BLOCKER event ✓
Unregistered path: UNREGISTERED_OUTBOUND_SOURCE + BLOCKER event ✓

Gate sequence (per attempt() call):
  Step -1: Authorized path check → SecurityEvent + blocked if not authorized
  Step 0:  Kill switch check (OUTBOUND_ENABLED env var)
  Step 1:  Advisory lock acquisition (SELECT FOR UPDATE on whatsapp_recipient_locks)
  Step 2:  Flood gate (3 messages per wa_id per 60 seconds)
  Step 3:  Content fingerprint dedup (10-minute rolling window)
  Step 4:  INSERT WhatsAppMessage(status="pending") + WhatsAppOutboundDedup BEFORE Meta call
  Post:    mark_sent() updates status + wa_message_id after successful Meta call

Write-before-send: CONFIRMED — gate_db.commit() at step 4 before caller reaches Meta API ✓
WAMID persistence: mark_sent() stores wa_message_id after successful send ✓
Dedicated sessions: gate uses its own sessions, caller session never touched ✓

Unknown WAMID (status webhook):
  WAMID not in DB: SecurityEventType.META_STATUS_FOR_UNKNOWN_WAMID (HIGH) ✓
  + outbound OFF: SecurityEventType.SUCCESSFUL_META_SEND_WHILE_OUTBOUND_OFF (BLOCKER) ✓

DB-only forensic reconstruction: CAPABLE — WhatsAppMessage has path_id, deployment_id,
  content_fingerprint, text, thread_id, timestamp before Meta call ✓
  NOTE: Requires unapplied migration (see Runtime Truth section). Currently non-functional
  in live DB until migration 20260828_m2_authorized_path_monitoring is applied.

ENDPOINTS:
  GET /security/unauthorized-path-events  → HTTP 200 ✓ (working)
  GET /security/outbound-ledger           → HTTP 500 ✗ (migration not applied)

Historical unattributable message gap:
  For FUTURE sends: STRUCTURALLY PREVENTED. Every outbound requires path_id before Meta call.
  History cannot be reconstructed for messages sent before this system existed.

Security events in DB: 0 — consistent with OUTBOUND_ENABLED=false and n8n inactive.

---

PART 14 — WEBHOOK SECURITY

IMPLEMENTATION (code):
  X-Hub-Signature-256 validation:    YES — _verify_signature() routes/whatsapp.py:55 ✓
  HMAC SHA-256:                      YES — hmac.new(secret, raw_body, sha256).hexdigest() ✓
  Constant-time comparison:          YES — hmac.compare_digest() ✓
  Wrong header format rejected:      YES — algo must be "sha256", sep must be "=" ✓
  Missing header rejected:           YES — when secret configured ✓
  Body tampering detected:           YES — HMAC mismatch → 403 ✓
  Tests (T24a-f):                    ALL PASS ✓

BEHAVIOR WHEN WHATSAPP_APP_SECRET IS EMPTY:
  _verify_signature() lines 56-59:
    secret = (app_secret or "").strip()
    if not secret:
        logger.info("Webhook signature skipped (dev mode)")  ← INFO only
        return True  ← ALL TRAFFIC ACCEPTED
  There is no startup enforcement requiring the secret when OUTBOUND is enabled.
  There is no separate dev/prod mode flag — empty string is the implicit dev-mode trigger.

CODE READINESS:    PASS — implementation is correct when secret is configured
CONFIGURATION:     FAIL — WHATSAPP_APP_SECRET is empty, dev-bypass is active
BLOCKER:           Any party that can reach POST /integrations/whatsapp/webhook can inject
                   arbitrary WhatsApp-formatted payloads, which CE will process as real messages.

---

PART 15 — SECRET HYGIENE

FILE                                  VARIABLE                EMPTY/NON-EMPTY   TRACKED   CURRENT/BACKUP
RC docker-compose.yml (HEAD)          WHATSAPP_TOKEN          ${...} ref         TRACKED   CURRENT (env var ref — clean)
RC docker-compose.yml (HEAD)          WHATSAPP_APP_SECRET     EMPTY              TRACKED   CURRENT
RC .gitignore                         .env / .env.*           N/A (ignored)      TRACKED   CURRENT
/opt/ridecheck-crm/.env               WHATSAPP_TOKEN          NON-EMPTY (201)    UNTRACKED CURRENT
/opt/ridecheck-crm/.env               WHATSAPP_APP_SECRET     ABSENT             UNTRACKED CURRENT
/opt/ridecheck-crm/.env.backup-before-resend WHATSAPP_TOKEN   NON-EMPTY (201)    UNTRACKED BACKUP
Running container env                 WHATSAPP_TOKEN          NON-EMPTY (201)    N/A       CURRENT
Running container env                 WHATSAPP_APP_SECRET     EMPTY              N/A       CURRENT
Running container env                 SMTP_PASSWORD           NON-EMPTY (19)     N/A       CURRENT
Flow private key (FLOW_BOOKING_PRIVATE_KEY_PATH) NOT configured — no .pem files found
Git history (RC): plaintext WHATSAPP_TOKEN committed in 5+ historical commits:
  afec998, 2e77c8f, e34ce1b, 6a2eacf, 30654da (confirmed via git show)
  Current RC HEAD (820f4d6): CLEAN — uses ${WHATSAPP_TOKEN} variable reference
Production repo (/opt/ridecheck-crm) HEAD: plaintext WHATSAPP_TOKEN in docker-compose.yml
  (This repo is not what serves traffic — container serves RC image)

SUMMARY:
  Current HEAD docker-compose.yml: CLEAN (env var ref)
  Historical git exposure: YES — old token in commits
  Token rotation: REQUIRED (old token must be revoked after new one deployed)
  App Secret: EMPTY everywhere — configuration task required
  Private key: NOT configured — Flow backend cannot decrypt live Meta requests

---

PART 16 — DATABASE / DATA INTEGRITY

Connected DB: crm_test (PostgreSQL 16 — test/beta DB)
NOTE: Production DB (crm) not inspected (read-only audit — cannot confirm its migration state)

TABLE EXISTENCE:
  Present:   leads, revisions, whatsapp_threads, whatsapp_messages, whatsapp_thread_states,
             whatsapp_thread_candidates, thread_revisions, ai_events, security_events,
             whatsapp_outbound_dedup, whatsapp_recipient_locks, agencias, profesionales,
             vendedores, viaticos_zones, users, system_settings
  NOT PRESENT: appointments, booking_tokens (these are not separate tables in this schema)

ROW COUNTS:
  leads:                          51
  revisions:                       5
  whatsapp_threads:               50
  whatsapp_messages:             193
  whatsapp_thread_candidates:     65  (archived: 18, current_focus: 46, mentioned: 1)
  security_events:                 0
  whatsapp_outbound_dedup:        20  (from historical sends)
  thread_revisions:               (not queried but table exists)

INTEGRITY CHECKS:
  Orphan revisions (no lead):      0 ✓
  cycle_reset_pending=TRUE:        2 (expected — awaiting next inbound)
  Leads by estado:                 CONSULTA_NUEVA: 20, PRESUPUESTANDO: 30, AGENDADO: 1
  Duplicate threads (same contact): 0 ✓ (contact_id FK is unique per thread per design)

ANOMALY FOUND — THREAD 1704 (2 current_focus candidates):
  id=106: Peugeot 2008 / SUV_4X4_DEPORTIVO (created 2026-08-26)
  id=107: Ford Focus / AUTO (created 2026-08-27)
  state: current_focus_candidate_id=107, last_stage=QUALIFYING, cycle_reset_pending=False
  The state correctly points to id=107 (Ford Focus). CE _focus_candidate() prioritizes
  state.current_focus_candidate_id, so CE would use Ford Focus correctly.
  However, id=106 retains status='current_focus' which violates the invariant of one
  current_focus per thread at any time.
  Likely cause: a vehicle-switch test scenario in crm_test where the demotion of the
  prior candidate to "mentioned" did not complete (possible timing issue or test abort).
  Risk for PRODUCTION: LOW (crm is a different DB; this appears to be a crm_test artifact).
  Recommend: verify _apply_candidate() demotion path in testing; verify crm DB is clean.

UNAPPLIED MIGRATION:
  Applied:     20260827_m21_3_thread_revision_zone_group
  MISSING:     20260828_m2_authorized_path_monitoring
  Missing columns on whatsapp_messages: path_id, deployment_id, correlation_id
  Impact: outbound-ledger endpoint returns 500; outbound gate INSERT will fail when enabled

---

PART 17 — API / ERROR HANDLING

Auth:           CRM UI routes require session (redirect to login on unauthenticated)
Webhook:        Signature verification before processing; returns 403 on failure
Booking Flow:   ValueError on bad crypto → 421 (correct Meta protocol response)
BookingTokenError: returned as appropriate error to Flow (review flow_data_exchange.py)
Pricing failure: PricingNotFoundError → CE asks for missing info (not a 500)
DB failure:     unhandled exception in CE → error/internal_error → cursor not advanced (retry safe)
Inbound dedup:  IntegrityError on duplicate wa_message_id caught gracefully

CUSTOMER DEAD ENDS:
  No inbound → CE because n8n is inactive: customer gets no response, no alert (n8n won't fire)
  needs_human stuck (sync gap): CE stays suppressed even if human thinks they cleared it
  Both are operational configuration issues, not code defects

---

PART 18 — TEST SUITE HEALTH

TOTAL: 197 passed / 0 failed / 0 skipped / 18 subtests passed
Time: 170.64 seconds
All warnings: datetime deprecation (non-blocking, Python 3.12 known issue)

GROUPING:
  Outbound safety (M19):         41 tests — PASS
  Blocked dispatch (M20):         9 tests + 18 subtests — PASS
  Authorized paths (M2):         28 tests — PASS
  Hardening T4-T25 (M21.3):     25 tests — PASS
  Booking flow (M21.3-C-D):      47 tests — PASS
  UX2 + UX2 runtime (M21.3):    31 tests — PASS
  UX3 contact/linkage (M21.3):  16 tests — PASS

GAPS:
  No test for n8n → CE dispatch path
  Tests use SQLite in-memory; PostgreSQL migration state not tested
  BookingFlowService._load_focus_candidate() cycle watermark not tested

---

PART 19 — HISTORICAL INCIDENT REGRESSION

A. Repeated vehicle question
   STATUS: FIXED
   Evidence: F3-T3 candidate dedup (action=create redirected to update for same marca+modelo)
   Tests: test_wild04r_f3_exact_cases.py, test_messy_turn_reconciliation.py

B. Duplicate quote
   STATUS: FIXED
   Evidence: content_fingerprint dedup in OutboundSafetyGate (10-min window)
   Tests: test_m19_r1_outbound_safety_gate.py (BLOCKED_DUPLICATE tests)

C. Schedule confirmation then bot asks vehicle again
   STATUS: FIXED
   Evidence: WILD-04R cycle_reset_pending + watermark filters; post-reset reload (F2)
   Tests: test_wild04r_cycle_boundary.py, test_wild04r_f2_reset_faq.py

D. Unknown vehicle failed to trigger correct fallback
   STATUS: FIXED
   Evidence: _check_inspectability_gate() deterministic gates; F5 D1 AI prompt Rule 20;
   F5.1 _apply_required_next_question() deterministic finalizer
   Tests: test_wild04r_f5_1_required_location_gate.py (28 tests)

E. Stale San Miguel location surviving explicit Palermo
   STATUS: FIXED
   Evidence: F4 — _get_active_inspection_location() candidate-first; F3-T2 zone correction guard
   Tests: test_wild04r_f4_location_authority.py (Cases A-E)

F. Scheduling parser using 18 instead of corrected 9
   STATUS: FIXED
   Evidence: schedule.py _business_hours() is fully deterministic; Monday=13:00-18:00 confirmed
   Tests: test_m21_3_scheduler.py

G. Historical unattributable outbound message
   STATUS: FIXED FOR FUTURE SENDS (historical sender cannot be retroactively identified)
   Evidence: write-before-send with path_id, deployment_id, content_fingerprint
   NOTE: outbound-ledger endpoint currently returns 500 due to unapplied migration
   Tests: test_m2_authorized_paths.py (T16-T25)

H. UX source/runtime mismatch
   STATUS: FIXED
   Evidence: Container fully matches RC (0 drift on all 8 key files)
   Tests: test_m21_3_ux2_runtime.py (10 runtime tests)

I. Same Lead / new Revision reset leakage
   STATUS: FIXED
   Evidence: cycle_reset_pending explicit signal + watermark isolation (WILD-04R Phase 1+2)
   Tests: test_wild04r_cycle_boundary.py, test_wild04r_f1_evidence.py,
          test_wild04r_f2_reset_faq.py, test_wild04r_f5_cycle_safe_authority.py

---

PART 20 — EXTERNAL VENDOR / META READINESS LIST

META ACCOUNT ACCESS
  Owner currently cannot authenticate to Meta Business Manager to retrieve App Secret.
  Prerequisite for all Meta configuration items below.

META APP SECRET (WHATSAPP_APP_SECRET)
  Status: UNAVAILABLE — requires Meta account authentication
  Config action: retrieve from Meta App dashboard → set in .env (untracked)
  Code: implementation correct when set

WHATSAPP TOKEN
  Status: Existing token (len=201) in .env is the old token exposed in git history
  Action: Generate new token via Meta Business Manager
  Action: Revoke old token immediately after new token is deployed
  Action: New token → .env ONLY (no git commit)
  Code: ${WHATSAPP_TOKEN} pattern in docker-compose.yml is ready

BOOKING FLOW (Flow ID: 28104222025943520 — "RideCheck Booking")
  Status: DRAFT — not published, not connected to Meta app
  Actions required:
    1. Generate RSA key pair for Flow encryption
    2. Upload public key to Meta Flow editor
    3. Set FLOW_BOOKING_PRIVATE_KEY_PATH env var pointing to private key file
    4. Configure Flow endpoint in Meta (POST /integrations/whatsapp/flows/booking/data-exchange)
    5. Test Flow in preview mode (Meta-side)
    6. Publish Flow
  Code: Backend READY — INIT/date_selected/prepare_summary/confirm_booking all implemented

META WEBHOOK
  Status: webhook verification token configured (GET challenge: 403 on wrong token — correct)
  Action after App Secret: verify HMAC signature on live traffic
  Action: configure webhook URL in Meta App (POST /integrations/whatsapp/webhook)

N8N TRANSPORT
  Status: Container running (7 days uptime), but ZERO active workflows
  Action: Owner must activate the inbound processing workflow in n8n UI
  Note: n8n is the required transport tier (CONVERSATION_ENGINE_DIRECT_WEBHOOK_ENABLED=false)

CLOSED BETA
  Action: Add owner's WhatsApp number to CLOSED_BETA_ALLOWED_WA_IDS (or enable outbound first)
  Action: Run one controlled booking end-to-end (text → quote → accept → Flow → confirm)
  Action: Verify WAMID appears in ledger, status webhook updates it
  Action: Verify ThreadRevision + Revision created correctly

GOOGLE MAPS
  Status: No API key required — Maps links are static URLs (address in query param)
  Waze: Same — static URL
  GPS: No server-side API dependency
  Classification: POST-LAUNCH (nothing to configure before launch)

DATABASE MIGRATION
  Action: Apply 20260828_m2_authorized_path_monitoring to crm_test (and crm before enabling outbound)
  This adds path_id, deployment_id, correlation_id to whatsapp_messages
  Without this: outbound gate INSERT fails → no sends possible even if OUTBOUND=true

---

LAUNCH READINESS MATRIX

| AREA                          | STATUS        | SEVERITY | EVIDENCE                                                  | OWNER ACTION? | CLAUDE ACTION? | EXTERNAL? | NOTES                                         |
|-------------------------------|---------------|----------|------------------------------------------------------------|---------------|----------------|-----------|-----------------------------------------------|
| Inbound receiving             | BLOCKED_EXT   | BLOCKER  | n8n 0 active workflows; Meta webhook not configured        | YES           | NO             | YES       | n8n activation + Meta webhook setup required  |
| Conversation understanding    | PASS          | —        | 197/197 tests; CE business authority audited               | NO            | NO             | NO        | All WILD-04R fixes implemented                |
| Vehicle handling              | PASS          | —        | F5/F6 catalog authority; normalization; all gates          | NO            | NO             | NO        | Unknown vehicle: AI tipo (acceptable)         |
| Location handling             | PASS          | —        | F4 candidate-first; F3-T2 zone correction re-quote         | NO            | NO             | NO        |                                               |
| Pricing                       | PASS          | —        | PricingService authoritative; scrubbers; no AI pricing     | NO            | NO             | NO        | Alias minor gap in pricing.py (LOW risk)      |
| Quote acceptance              | PASS          | —        | _is_acceptance(); stage gates; dedup fingerprint           | NO            | NO             | NO        |                                               |
| Customer data collection      | PASS          | —        | MOTO flow path confirmed; _MOTORCYCLE_CONTACT_INTRO ✓     | NO            | NO             | NO        |                                               |
| Scheduling                    | PASS          | —        | All hours correct; travel times correct; no Maps dep       | NO            | NO             | NO        |                                               |
| Booking Flow backend          | PASS          | —        | All 4 actions implemented; advisory lock; atomic           | NO            | NO             | NO        | Private key not configured (external gate)    |
| Agenda/Revision linkage       | PASS          | —        | UX3 tests 197/197; owner visually approved                 | NO            | NO             | NO        |                                               |
| Active-cycle reset            | PASS          | —        | set_lead_estado() all 5 endpoints; F2 reload; F5 D2        | NO            | NO             | NO        |                                               |
| Human handoff                 | PARTIAL       | MEDIUM   | needs_human/lead.necesita_humano sync gap open             | YES (decision)| NO             | NO        | Owner decision pending per contract           |
| Outbound safety               | PARTIAL       | BLOCKER  | Gate code: correct. Migration not applied: outbound fails  | YES (migrate) | YES (migrate)  | NO        | Must apply 20260828 migration before outbound |
| Forensic traceability         | PARTIAL       | HIGH     | Code: correct. Ledger endpoint: 500 (migration missing)    | YES (migrate) | YES (migrate)  | NO        | Same migration restores endpoint              |
| Webhook security code         | PASS          | —        | HMAC SHA-256; constant-time; T24a-f all pass               | NO            | NO             | NO        |                                               |
| Webhook security config       | FAIL          | BLOCKER  | WHATSAPP_APP_SECRET empty → dev-bypass active              | YES           | NO             | YES       | Requires Meta App Secret retrieval            |
| Secret hygiene                | PARTIAL       | BLOCKER  | Token in git history; App Secret empty; key not configured | YES           | NO             | YES       | Rotation required; Meta account access needed |
| Meta Flow publication         | BLOCKED_EXT   | BLOCKER  | Flow DRAFT; no key; no endpoint; not connected             | YES           | NO             | YES       | All Meta-side setup required                  |
| WhatsApp credential rotation  | FAIL          | BLOCKER  | Old token in git history; must rotate before prod traffic  | YES           | NO             | YES       | Requires new token from Meta                  |
| n8n transport                 | FAIL          | BLOCKER  | 0 active workflows — no messages reach CE                  | YES           | NO             | NO        | Owner activates workflow in n8n UI            |
| CRM operational UI            | PASS          | —        | UX2/UX3 complete; owner visual approval; 197/197 pass      | NO            | NO             | NO        |                                               |
| Database integrity            | PARTIAL       | HIGH     | Migration missing; thread 1704 anomaly in crm_test         | YES (migrate) | YES (migrate)  | NO        | crm DB not inspected (production)             |
| Regression suite              | PASS          | —        | 197/197, 0 failed, 0 skipped                               | NO            | NO             | NO        |                                               |

---

BLOCKERS — MUST RESOLVE BEFORE CLOSED BETA / WILD:

BLOCK-01
  ID: BLOCK-01
  Title: Database migration 20260828_m2_authorized_path_monitoring not applied
  Why: When OUTBOUND_ENABLED=true, outbound safety gate INSERT will fail (missing columns
       path_id, deployment_id, correlation_id on whatsapp_messages). No outbound send possible.
       Also: GET /security/outbound-ledger returns 500 (forensic endpoint broken).
  Owner: Owner (apply migration to crm_test AND crm before enabling outbound)
  Claude: Can run the migration command if directed; migration file exists in repo
  Scope: TINY (one alembic upgrade command)

BLOCK-02
  ID: BLOCK-02
  Title: WHATSAPP_APP_SECRET empty — webhook in dev-bypass mode
  Why: _verify_signature() returns True for ALL traffic when secret is empty.
       Any party reaching POST /integrations/whatsapp/webhook can inject arbitrary
       CE-processed messages. This is a critical security gate.
  Owner: Meta account access → retrieve App Secret → set in .env
  Claude: NO (external credential)
  Scope: TINY (retrieve + configure) — depends on Meta account access

BLOCK-03
  ID: BLOCK-03
  Title: n8n has zero active workflows — inbound messages do not reach CE
  Why: curl /api/v1/workflows?active=true returns []. Customer WhatsApp messages
       arrive at Meta, hit the webhook, persist to DB, but n8n fires no workflow.
       CE never processes real customer messages.
  Owner: Activate the inbound processing workflow in n8n UI
  Claude: NO (n8n operational action, not code)
  Scope: TINY (toggle workflow active in n8n)

BLOCK-04
  ID: BLOCK-04
  Title: WhatsApp access token exposed in git history — rotation required
  Why: The plaintext WHATSAPP_TOKEN appears in RC git commits afec998, 2e77c8f, e34ce1b,
       6a2eacf, 30654da (historical). Any repo clone can read it. Before enabling production
       outbound, the old token must be revoked. If it is not revoked, an attacker with repo
       access could send WhatsApp messages via Meta as the RideCheck account.
  Owner: Generate new token via Meta Business Manager → deploy to .env → revoke old token
  Claude: NO (Meta credential rotation)
  Scope: SMALL (generate + deploy + revoke)

BLOCK-05
  ID: BLOCK-05
  Title: Booking Flow not published — Meta Flow interaction not possible
  Why: Flow is DRAFT. Private key not configured. Endpoint not connected in Meta.
       Customers cannot submit the booking Form. The booking creation path
       (ThreadRevision + Revision + lead state) is not reachable from real customers.
  Owner: Full Meta Flow setup (key, endpoint, connection, publication)
  Claude: NO (Meta operational setup)
  Scope: MEDIUM (multiple Meta-side configuration steps + key generation)

---

HIGH — SHOULD RESOLVE BEFORE PUBLIC LAUNCH:

HIGH-01
  ID: HIGH-01
  Title: GET /security/outbound-ledger returns HTTP 500 (forensic endpoint broken)
  Why: Missing columns (migration BLOCK-01). After BLOCK-01 is resolved, this is auto-fixed.
  Note: This is a consequence of BLOCK-01. Once migration is applied, HIGH-01 is resolved.
  Owner: Resolved by applying migration (BLOCK-01)
  Scope: TINY (subsumed by BLOCK-01)

HIGH-02
  ID: HIGH-02
  Title: needs_human / lead.necesita_humano sync gap in CRM human-toggle endpoints
  Why: POST /ui/human and POST /ui/lead_toggle_humano write lead.necesita_humano but NOT
       state.needs_human. A human operator who clears the human flag via CRM without performing
       a full cycle reset (CONSULTA_NUEVA) leaves CE permanently suppressed. The customer's
       messages will be skipped indefinitely with no CE response and no alert.
  Owner: Owner decision (do these endpoints also write state.needs_human?) — architectural scope
  Claude: SMALL code change once owner decides
  Scope: SMALL

HIGH-03
  ID: HIGH-03
  Title: BookingFlowService._load_focus_candidate() not cycle-watermarked
  Why: For a returning customer with prior-cycle candidates, this method could return a
       prior-cycle candidate when resolving the booking context. CE controls token issuance
       so in practice the active candidate is always current — but the method is not safe
       by itself. If the candidate resolution logic is called for other purposes, it could
       yield wrong results.
  Owner: Claude can fix
  Claude: YES — add current_cycle_started_at filter to the query
  Scope: TINY

HIGH-04
  ID: HIGH-04
  Title: Production DB (crm) migration state unknown
  Why: The crm DB was not inspected (audit constraint). If crm does not have migration
       20260828_m2_authorized_path_monitoring applied, enabling outbound on the production
       container would immediately fail. Must verify before switching OUTBOUND_ENABLED=true.
  Owner: Apply migration to crm (read production DB state, run alembic upgrade)
  Claude: Can assist with migration command
  Scope: TINY (verify + apply)

HIGH-05
  ID: HIGH-05
  Title: Thread 1704 has 2 current_focus candidates (crm_test integrity anomaly)
  Why: Violates the invariant "at most one current_focus candidate per thread."
       CE prioritizes state.current_focus_candidate_id so it behaves correctly,
       but the anomaly could confuse future queries or context loading.
       Likely a crm_test artifact; production DB not inspected.
  Owner: Verify crm DB has no such anomalies; investigate demotion path in _apply_candidate()
  Claude: YES (read-only verification; targeted fix if demotion bug found)
  Scope: SMALL

---

MEDIUM — SAFE TO LAUNCH WITH:

MED-01
  ID: MED-01
  Title: No automated test for n8n → CE dispatch routing branch
  Why: routes/whatsapp.py:429 (CE direct vs n8n) has no test. Documented in CLAUDE.md.
       Both paths are structurally correct; the gap is in automated coverage.
  Owner: Claude
  Claude: YES — add integration test stub
  Scope: SMALL

MED-02
  ID: MED-02
  Title: FAQ path (_handle_general_information_ai) replies not price-scrubbed
  Why: AI-generated FAQ responses bypass _PRICE_RE and _QUOTE_INTENT_RE scrubbers.
       Price hallucination prevented only by prompt rules, not deterministic backstop.
       Low risk since FAQ path fires only when no commercial evidence in burst.
  Owner: Claude
  Claude: YES — add scrub call before _send_text_to_wa() in that path
  Scope: TINY

MED-03
  ID: MED-03
  Title: DB-authoritative burst guard does not fire for first-ever message on thread
  Why: _fetch_burst_messages() returns [] when previous_cursor is None (no prior processed
       message). First burst relies solely on n8n payload completeness.
       Almost never an issue (first message is usually a greeting, not a multi-message burst).
  Owner: Claude
  Claude: YES — small fix to handle None previous_cursor
  Scope: TINY

MED-04
  ID: MED-04
  Title: n8n message-fetch endpoint limit (10, not 50)
  Why: DB-authoritative burst guard compensates for incomplete n8n payloads.
       Increasing n8n limit to 50 would reduce dependency on the compensation mechanism
       for large bursts (>10 messages in one debounce window — rare in practice).
  Owner: Owner (n8n node configuration)
  Claude: NO (n8n operational change)
  Scope: TINY

---

POST-LAUNCH / INTENTIONALLY DEFERRED:

POST-01
  ID: POST-01
  Title: Full per-call-site answer_source tagging (40+ _out() sites)
  Why: Deferred second pass per CONVERSATION_RUNTIME_CONTRACT.md. Inference rules cover
       common paths. Does not affect correctness, only telemetry completeness.
  Scope: MEDIUM

POST-02
  ID: POST-02
  Title: Google Maps / Waze static URL links (no API key)
  Why: Current implementation uses static URL templates with address in query param.
       This is sufficient for launch. An API key would enable geocoding validation.
  Scope: MEDIUM (future enhancement)

POST-03
  ID: POST-03
  Title: outbound-ledger fingerprint filter uses Python .startswith() not SQL LIKE
  Why: Minor performance issue on large datasets. Not a correctness defect.
  Scope: TINY (post-launch optimization)

POST-04
  ID: POST-04
  Title: Answer performance telemetry per-turn alerting threshold gap (>120s)
  Why: Unanswered alert threshold correctly set to 120s. Per-turn ai_events observability
       implemented. Full SLA classification per CONVERSATION_RUNTIME_CONTRACT.md §5.
       Alert mechanism works; no remaining gap beyond the deferred tagging (POST-01).
  Scope: Subsumed by POST-01

---

EXTERNAL META / VENDOR ITEMS:

EXT-01  Meta account access — owner cannot currently authenticate (password unknown)
          Blocks: BLOCK-02 (App Secret), BLOCK-04 (token rotation), BLOCK-05 (Flow setup)

EXT-02  Meta App Secret retrieval and configuration
          When: After EXT-01 resolved
          Action: CLAUDE.md pattern — set WHATSAPP_APP_SECRET in .env (untracked)

EXT-03  WhatsApp token rotation
          When: After EXT-01 resolved
          Action: Generate new token → deploy to .env → revoke old token
          Old token: in RC git history (commits afec998, 2e77c8f, e34ce1b, 6a2eacf, 30654da)

EXT-04  Booking Flow setup
          When: After EXT-01 resolved; backend READY
          Steps: RSA key pair → public key to Meta Flow → FLOW_BOOKING_PRIVATE_KEY_PATH set →
                 endpoint connected in Meta → preview test → publish

EXT-05  Meta webhook live signature proof
          When: After EXT-02 (App Secret configured)
          Action: POST a signed test payload to webhook; verify 200 response

EXT-06  Closed beta — controlled booking session
          When: After BLOCK-01..05 resolved + EXT-02..05 complete
          Action: One allowlisted customer number; real conversation to booking; verify:
                  WAMID in ledger, status webhook updates, ThreadRevision + Revision created

EXT-07  n8n workflow activation
          When: Before any live traffic
          Action: Owner activates workflow in n8n UI (http://localhost:5678)
          Note: This is NOT a Meta external dependency — it is a local operational step

---

HISTORICAL INCIDENT REGRESSION STATUS:

| ID | INCIDENT                              | STATUS        | EVIDENCE                                          |
|----|---------------------------------------|---------------|---------------------------------------------------|
| A  | Repeated vehicle question             | FIXED         | F3-T3 dedup; test_messy_turn_reconciliation.py    |
| B  | Duplicate quote                       | FIXED         | content_fingerprint dedup; test_m19_r1 ✓          |
| C  | Booking → vehicle question again      | FIXED         | cycle_reset + post-reset reload; F2 tests ✓       |
| D  | Unknown vehicle wrong fallback        | FIXED         | F5 D1 + F5.1 deterministic gate; 28 tests ✓       |
| E  | Stale location after correction       | FIXED         | F4 candidate-first + F3-T2 re-quote; tests ✓      |
| F  | Scheduling parser 18→9               | FIXED         | schedule.py deterministic _business_hours() ✓     |
| G  | Historical unattributable outbound    | FIXED (future)| write-before-send + path_id; ledger endpoint 500 until migration |
| H  | UX source/runtime mismatch           | FIXED         | 0 drift on all 8 key files; runtime tests ✓       |
| I  | Same Lead / new cycle leakage         | FIXED         | WILD-04R Phase 1+2; F5; cycle boundary tests ✓    |

---

DATA INTEGRITY:

Connected DB: crm_test (PostgreSQL 16 — beta/test DB)
Production DB (crm): NOT inspected per audit constraints

crm_test findings:
  Orphan revisions: 0 ✓
  Duplicate threads per contact: 0 ✓
  cycle_reset_pending=TRUE: 2 (expected; will clear on next inbound)
  Thread 1704: 2 current_focus candidates (anomaly — see HIGH-05)
  Applied migrations: only 20260827_m21_3_thread_revision_zone_group (missing 20260828)
  Security events: 0 (consistent with outbound=false, n8n inactive)
  Outbound dedup entries: 20 (from historical beta sends)

---

SECURITY / TRACEABILITY:

OUTBOUND GATE: CODE CORRECT — all 7 authorized paths; LEGACY blocked; path_id=None blocked;
  write-before-send; WAMID persistence; dedicated sessions; security event emission.
  OPERATIONAL GAP: migration not applied → INSERT fails when outbound enabled.

WEBHOOK SECURITY: CODE CORRECT — HMAC SHA-256, constant-time comparison, body integrity.
  OPERATIONAL GAP: App Secret empty → dev-bypass active.

FORENSIC ENDPOINTS:
  /security/unauthorized-path-events: HTTP 200 — WORKING
  /security/outbound-ledger:          HTTP 500 — BROKEN (migration missing)

FUTURE SENDS: Structurally prevented from being unattributable — path_id + deployment_id +
  content_fingerprint + write-before-send all implemented.

HISTORICAL EXPOSURE: WA token in git history. Must revoke after rotation.

---

RECOMMENDED EXECUTION ORDER:

1. APPLY DATABASE MIGRATION
   Run: alembic upgrade 20260828_m2_authorized_path_monitoring
   Target: crm_test first (verify), then crm (production DB before enabling outbound)
   Resolves: BLOCK-01, HIGH-01, HIGH-04

2. FIX BookingFlowService._load_focus_candidate() CYCLE WATERMARK
   One targeted code fix (HIGH-03) — TINY scope
   Apply and redeploy before enabling outbound

3. RESOLVE META ACCOUNT ACCESS (EXT-01)
   Owner recovers Meta account authentication (password reset, 2FA recovery, etc.)
   This is the prerequisite for all Meta credential steps

4. RETRIEVE AND CONFIGURE WHATSAPP_APP_SECRET
   Meta App Settings → retrieve App Secret → set WHATSAPP_APP_SECRET in .env (untracked)
   Restart container to pick up new .env
   Resolves: BLOCK-02

5. GENERATE AND ROTATE WHATSAPP TOKEN
   Generate new long-lived token in Meta Business Manager
   Deploy new token to .env only (not docker-compose.yml)
   Revoke old token via Meta API/dashboard
   Resolves: BLOCK-04

6. ACTIVATE N8N WORKFLOW
   Open n8n UI (http://localhost:5678 or crm.ridecheck.ar:5678)
   Activate the inbound processing workflow
   Resolves: BLOCK-03

7. NO-SEND SECURITY SMOKE TEST
   With OUTBOUND_ENABLED=false and App Secret configured:
   Send a test signed webhook payload
   Verify: 200 response, WhatsAppMessage persisted, CE processes, no outbound (kill switch)
   Verify: GET /security/outbound-ledger returns 200 with correct data

8. BOOKING FLOW SETUP (EXT-04)
   Generate RSA key pair; upload public key to Meta Flow editor
   Set FLOW_BOOKING_PRIVATE_KEY_PATH; configure endpoint in Meta
   Test Flow in preview; publish Flow
   Resolves: BLOCK-05

9. CLOSED BETA ACTIVATION
   Set OUTBOUND_ENABLED=true in .env (or docker-compose.yml → deploy)
   Add owner number to CLOSED_BETA_ALLOWED_WA_IDS
   Run ONE controlled conversation end-to-end:
     Text → vehicle → zone → quote → accept → Flow → booking
   Verify: WAMID in outbound-ledger, status webhook updates it, ThreadRevision + Revision created
   Verify: cycle_reset_pending works on second conversation

10. ACTIVE-CYCLE RESET PROOF
    Owner operator: move test lead to CONSULTA_NUEVA in CRM
    Send new inspection query as the test customer
    Verify: new cycle starts, no prior vehicle/location in CE context

11. WILD TEST SESSION
    Allow 1-2 real customer conversations (with close monitoring)
    Monitor: /security/unauthorized-path-events, /security/outbound-ledger
    Monitor: ai_events for latency + reply_produced

12. RESOLVE HIGH-02 (needs_human sync gap) if not already resolved
    Owner decides approach; Claude implements

13. PUBLIC LAUNCH DECISION

---

READY FOR META OPERATIONAL SETUP:
YES — code is ready; App Secret retrieval is the only code-external dependency

READY FOR CLOSED BETA AFTER META SETUP:
CONDITIONAL — requires BLOCK-01 (migration), BLOCK-03 (n8n), BLOCK-04 (token rotation),
BLOCK-05 (Flow published) in addition to BLOCK-02 (App Secret)

READY FOR WILD TEST:
CONDITIONAL — after all BLOCKERs resolved + one verified closed beta session

READY FOR PUBLIC LAUNCH:
CONDITIONAL — after Wild test + HIGH-02 decision + HIGH-03 fix

EXACT IDS BLOCKING NEXT STEP (Meta operational setup):
  BLOCK-01 — migration not applied (outbound gate broken)
  BLOCK-02 — App Secret empty (webhook bypass; external: Meta account)
  BLOCK-03 — n8n 0 active workflows (no inbound processing)
  BLOCK-04 — old WA token must be rotated (external: Meta account)
  BLOCK-05 — Booking Flow not published (external: Meta account + Flow setup)

EXACT IDS BLOCKING CLOSED BETA (beyond above):
  HIGH-03 — BookingFlowService candidate watermark gap (TINY code fix)
  HIGH-04 — production DB migration status unconfirmed

---

NO CHANGES MADE: YES

OUTBOUND: OFF

PRODUCTION DB TOUCHED: NO

STOP.
