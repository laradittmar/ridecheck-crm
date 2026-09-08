PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: L4.7W5-PREP-TESTER-RESET-AGENDA-SEED

STATUS: CONDITIONAL_PASS
DATE: 2026-09-08
SCOPE: crm_test only. No schema change. No code change. OUTBOUND OFF throughout.
CONDITION: 14 appointments seeded, not the ~17 targeted. Reason in §5 — it is a
deliberate deviation, and the owner may want it decided differently.

---

## 1. Part B — tester identity

Resolved from the certified closed-beta allowlist (`CLOSED_BETA_ALLOWED_WA_IDS`), not by
display name. Exactly one contact matched, so there was no ambiguity to stop for:

| | |
|---|---|
| contact_id | 2048 |
| thread_id | 2041 |
| lead_id | 127 |
| other candidates | none — one row matches the allowlisted wa_id |

## 2. Part A — evidence preserved before anything was deleted

```
ARCHIVE : /opt/ridecheck-crm-forensics/L4.7W5-PREP_tester_2048_20260908T182018Z.tar.gz
SHA256  : 24c6cf79edadfd5e54c8c9ebcc90007d0ca637f731669afb5596015ee5dc0a80
SIZE    : 6739 bytes
CREATED : 2026-09-08T18:20:18Z
```

Twelve tables exported as full-fidelity JSON, outside the repo, with a MANIFEST. Row
counts matched the pre-delete inventory exactly and were re-verified inside the archive:

| table | rows |
|---|---|
| whatsapp_contacts / threads / thread_states / candidates | 1 each |
| whatsapp_messages | 15 |
| thread_revisions | 1 |
| leads / revisions | 1 each |
| ai_events | 8 |
| whatsapp_outbound_dedup | 5 |
| whatsapp_recipient_locks | 1 |
| security_events | 0 — no tester-linked security event exists |

Key evidence confirmed present in the archive before deletion: the W4-F2 live Booking Flow
proof (`thread_revision 4`, booked, 2026-09-08 12:30) and the W4-F3 price repair
(`revision 37`, 150000 + 90000 = 240000), plus the outbound ledger including the
BOOKING_FLOW sends and the blocked-attempt attribution rows.

Booking/approval tokens and the tester's phone number are retained **inside** the archive
because they are the evidence; the archive is outside git and none of those values appear
in this document.

## 3. Parts C/D — tester zero state

Deleted in one transaction, following the FK order certified by L4.2-CLEAN-SLATE-TESTER:
ai_events → outbound_dedup → recipient_locks → thread_revisions → messages → candidates →
thread_states → revisions → feedback_post_revision → threads → leads → contacts.

| entity | count |
|---|---|
| contacts, threads, thread_states, candidates | 0 |
| messages, thread_revisions, revisions, leads | 0 |
| ai_events, outbound_dedup, recipient_locks | 0 |

**The contact row was deleted too**, matching L4.2. Nothing about the tester persists by
architecture, so the next inbound from that number creates contact, thread and lead from
scratch — a genuinely new customer, not a returning one.

Everything else is untouched: 32 contacts / 32 leads / 32 threads / 28 revisions before
seeding, unchanged by the reset.

## 4. Parts F/G/H — how the agenda was seeded

Business hours were read from the executable authority (`_WEEKDAY_HOURS`), not from the
prompt, and they agree with it: Tue 09:30-14:00, Wed 09:00-18:00, Thu 09:00-14:00,
Fri 09:00-18:00, Sat 09:00-15:00, Sunday closed. The day→zone table in the prompt is the
**origin** (`ZERO_ZONE_GROUP="Norte"`; Santa Catalina Tue/Thu/Sat, Melo Wed/Fri), which is
what the travel logic measures from — appointments themselves may be in any zone.

**Every slot was chosen by asking `ScheduleService.list_slots()` what was actually
available, then booking exactly that.** No time was invented, and each appointment is one
the scheduler itself considered travel-valid given the ones already seeded. That makes UI
occupancy and scheduler occupancy the same fact by construction rather than two things
reconciled afterwards.

Records seeded are CRM `Revision` rows with `turno_fecha`/`turno_hora` and an occupying
`estado_revision` — the same shape as the existing demo bookings, and one of the two
sources `_load_occupied_slots()` actually reads. Prices were stamped through the same
`PricingService` used everywhere else; no amounts were typed in.

Fixtures are new synthetic leads `DEMO Agenda 01…`, `acq_source='DEMO_FIXTURE'`,
`example.invalid` e-mails, synthetic phones. No real PII.

**Part J isolation:** demo leads have **no WhatsApp thread**. `quote_followup` and
`unanswered_alert` both iterate `whatsapp_thread_states`, so these fixtures are
structurally invisible to every outbound job — not merely unlikely to be picked up.

## 5. Part E/I — what was seeded, and the deviation

| day | | appointments | times |
|---|---|---|---|
| Tue 2026-09-08 | 09:30-14:00 | 2 | 10:00, 11:30 |
| Wed 2026-09-09 | 09:00-18:00 | 4 | 09:30, 11:00, 13:00, 14:30 |
| Thu 2026-09-10 | 09:00-14:00 | 2 | 09:30, 11:00 |
| Fri 2026-09-11 | 09:00-18:00 | 4 | 09:30, 11:30, 13:00, 15:00 |
| Sat 2026-09-12 | 09:00-15:00 | 2 | 09:30, 11:00 |
| **total** | | **14** | |

Status mix: **12 CONFIRMADO, 2 PENDIENTE**. Both estados occupy under
`_NON_OCCUPYING_ESTADOS = {CANCELADO, REPROGRAMAR}`, so the pending pair adds realism
without distorting scheduler truth. Sunday 2026-09-13 and Monday 2026-09-07 were not
seeded.

**The deviation, stated plainly.** The target was ~17 (3/4/3/4/3). I first seeded exactly
that, then measured availability and found Tuesday, Thursday and Saturday had **zero free
slots in every zone tested**. Three inspections at 45 minutes plus inter-zone travel simply
consumes a 4.5–6 hour day; the short days' real capacity is three, with nothing left over.

Part E asks for approximately 3, and licenses adjustment only for pre-existing bookings —
which did not apply, the week was empty. Part L, by contrast, states a hard requirement:
*"other legitimate free slots remain available"* and *"we deliberately want the next Wild to
schedule against a partially occupied calendar."* Those two cannot both hold on the short
days. I treated the explicit Part L gate as authoritative over an approximate count and
removed the latest appointment from each short day.

If you would rather have 17 with Tue/Thu/Sat fully booked — which is also realistic, and
would exercise the bot's "no availability, here are alternatives" path — say so and I will
restore the three. It is one command either way.

## 6. Parts K/L — verification

**Scheduler occupancy (read-only, per day, four zones):** no seeded time is offered as
free anywhere, and every seeded day retains free capacity in at least one zone.

```
Tue  seeded 10:00,11:30              free: Norte 13:00
Wed  seeded 09:30,11:00,13:00,14:30  free: Norte/Oeste/Sur 16:30,17:00 · CABA 16:00,16:30,17:00
Thu  seeded 09:30,11:00              free: Norte 12:30,13:00 · CABA 13:00
Fri  seeded 09:30,11:30,13:00,15:00  free: CABA 17:00 · Oeste 16:30,17:00
Sat  seeded 09:30,11:00              free: Norte 12:30,13:00,13:30,14:00 · CABA 13:00,13:30,14:00
```

Friday and Tuesday are full for Norte but open for CABA/Oeste — correct travel behaviour,
not an error: a customer's zone determines what is reachable after the day's existing route.

**Calendar UI:** the week view for 2026-09-07 → 2026-09-13 renders all seven days and all
**14 of 14** demo leads (193,002 bytes; every `DEMO Agenda NN` name present, none missing).

One honest limitation: I verified the calendar by rendering `render_calendar_page()`
server-side against the live database rather than by fetching `/calendar` as a logged-in
user, because doing the latter required reading `ADMIN_PASSWORD` out of `.env` and I would
not extract the admin credential to satisfy a check. The route's auth boundary was
confirmed separately — unauthenticated `/calendar` returns **303** to login. So: page
content proven, `200`-while-authenticated not proven.

## 7. Parts M/N — safety

| | |
|---|---|
| Outbound | OFF (`OUTBOUND_ENABLED=false`), never armed |
| WhatsApp messages created | 0 in the last 60 minutes |
| Outbound dedup rows created | 0 |
| Tester rows remaining | 0 — no appointment seeded for the tester |
| Demo leads with `necesita_humano` | 0 |
| Demo leads with a thread | 0 |
| Alembic head | `20260831_wild01_dedup_causal_inbound`, unchanged; parked drift untouched |
| Production | not written. One read-only `count(*)` was run against `crm` solely to show it is a separate database (4 revisions, unchanged). Every write in this milestone targeted `crm_test`. |
