PROJECT: RIDECHECK_CRM
TYPE: CLOSEOUT
MILESTONE: M21.4A-ATTRIBUTION-FOUNDATION
DATE: 2026-08-29
AUTHOR: Claude Sonnet 4.6 (AI assistant, supervised)
DB: crm_test ONLY

---

## EXECUTIVE SUMMARY

M21.4A-ATTRIBUTION-FOUNDATION implements the smallest safe canonical acquisition attribution
layer before marketing traffic begins. The `canal` field (UI dropdown) and CE free-text values
are two incompatible vocabularies that cannot serve as a reliable source of truth. This milestone
introduces a parallel, canonical `acq_source` field on `Lead` and `inbound_channel` on both
`WhatsAppThread` and `Lead`, captured from deterministic evidence at first contact and never
overwritten.

The critical irrecoverable risk addressed: Meta only emits `message["referral"]` on the FIRST
inbound message from a Click-to-WhatsApp ad. Prior code did not parse this field. Any ad traffic
arriving before this fix would have lost its acquisition source permanently.

No features removed. No schema destructive. No outbound enabled.

---

## SAFETY CONSTRAINTS — CONFIRMED SATISFIED

| Constraint | Status |
|---|---|
| OUTBOUND remains OFF | ✓ CONFIRMED — OUTBOUND_ENABLED not changed |
| No WhatsApp messages sent | ✓ CONFIRMED — no gate.attempt() calls in attribution code |
| Pricing not modified | ✓ CONFIRMED |
| Scheduling business rules not modified | ✓ CONFIRMED |
| CE authority boundaries not modified | ✓ CONFIRMED — _maybe_set_attribution() is read+annotate only |
| Active-cycle reset semantics not modified | ✓ CONFIRMED — attribution fields not in _execute_cycle_reset() |
| Booking Flow behavior not modified | ✓ CONFIRMED |
| n8n workflows not activated | ✓ CONFIRMED — n8n INACTIVE |
| Meta Flow not published/connected | ✓ CONFIRMED |
| Production DB NOT touched | ✓ CONFIRMED — crm_test only |
| Legacy canal field preserved | ✓ CONFIRMED — canal unchanged in all layers |
| No destructive legacy migration | ✓ CONFIRMED — only ADD COLUMN, no DROP, no backfill of acq_source |

---

## PART 1 — PROBLEM STATEMENT

### 1.1 Canal vocabulary conflict

The `canal` field has two incompatible naming systems:

- **UI dropdown values:** `IG_DM`, `IG_WHATSAPP`, `FB_DM`, `FB_WHATSAPP`, `WEBSITE`, `GOOGLE`, `GMAPS`, `OTROS`
- **CE free-text values:** `"Formulario web"`, `"Instagram"`, `"referido"`, `"WhatsApp"`, etc.

96% of leads (49/51 in crm_test) have `canal = null`. Canal is unreliable for any filtering,
reporting, or marketing attribution.

### 1.2 Invisible attribution fields

`ref_code` (VARCHAR 10) and `rc_code` (VARCHAR 8) were added in migration
`20260824_lead_attribution_fields`. CE stores them correctly (first-write-only), but:
- 0/51 leads have `ref_code` set (no website traffic yet in crm_test)
- Both fields were invisible in `LeadOut` API schema
- Both fields were not rendered in the kanban card

### 1.3 Irrecoverable CTWA risk

Meta only sends `message["referral"]` on the FIRST inbound message from a CTWA ad.
This JSON object contains `source_url`, `source_id`, `source_type`. The webhook handler
did not parse this field. Any ad-driven lead arriving before this fix permanently loses
its acquisition origin — no amount of data backfill can recover it.

---

## PART 2 — CANONICAL MODEL

### 2.1 Field semantics

| Field | Table | Width | Semantics |
|---|---|---|---|
| `inbound_channel` | `whatsapp_threads` | VARCHAR(20) | Technical transport medium. Currently always `WHATSAPP`. Backfilled on all existing threads. Authoritative source. |
| `ctwa_source_url` | `whatsapp_threads` | VARCHAR(500) | URL from Meta CTWA referral object. First-write-only. Never updated. |
| `ctwa_source_id` | `whatsapp_threads` | VARCHAR(100) | source_id from Meta CTWA referral. First-write-only. |
| `ctwa_source_type` | `whatsapp_threads` | VARCHAR(40) | source_type from Meta CTWA referral (e.g., "ad"). First-write-only. |
| `acq_source` | `leads` | VARCHAR(30) | Canonical acquisition source. Populated once from evidence hierarchy. Never overwritten by later messages or cycle resets. |
| `inbound_channel` | `leads` | VARCHAR(20) | Display copy propagated from thread by CE. Avoids kanban needing thread join. |

### 2.2 Canonical acq_source values

```
INSTAGRAM   — Instagram organic or paid (CTWA or ref_code=ig)
FACEBOOK    — Facebook organic or paid (CTWA or ref_code=fb)
GOOGLE      — Google Search (ref_code=ga)
GOOGLE_MAPS — Google Maps (reserved for future structured source)
WEBSITE     — Website form submit with no specific ref_code (is_website_lead=True)
REFERRAL    — Word-of-mouth referral (reserved for future structured source)
DIRECT      — Direct organic (ref_code=org or ref_code=dir)
OTHER       — Known ad platform, unclassified network
```

### 2.3 Attribution priority chain (CE _maybe_set_attribution)

1. `ref_code` normalization → highest specificity (comes from structured URL parameter)
2. CTWA `source_url`/`source_id` → deterministic social platform detection
3. `is_website_lead=True` with no ref_code → WEBSITE
4. No evidence → `acq_source` stays null (not guessed)

First-write-only at every step. Later messages, new Revisions, and cycle resets do not
change an already-set `acq_source`.

---

## PART 3 — FILES CHANGED

### 3.1 NEW: `backend/migrations/versions/20260829_m21_4a_attribution.py`

Additive migration. Chains from `20260828_m2_authorized_path_monitoring`.

**Adds to `whatsapp_threads`:**
- `inbound_channel VARCHAR(20) NULL`
- `ctwa_source_url VARCHAR(500) NULL`
- `ctwa_source_id VARCHAR(100) NULL`
- `ctwa_source_type VARCHAR(40) NULL`

**Adds to `leads`:**
- `acq_source VARCHAR(30) NULL`
- `inbound_channel VARCHAR(20) NULL`

**Backfill (additive only):**
```sql
UPDATE whatsapp_threads SET inbound_channel = 'WHATSAPP' WHERE inbound_channel IS NULL
```
No backfill of `leads.acq_source` — historical `canal` reliability is insufficient.

**Down:** All columns dropped cleanly.

### 3.2 EDITED: `backend/app/models.py`

`Lead` — added after `rc_code`:
```python
# M21.4A canonical attribution
acq_source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
inbound_channel: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
```

`WhatsAppThread` — added after `created_at`:
```python
# M21.4A canonical attribution
inbound_channel: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
ctwa_source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
ctwa_source_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
ctwa_source_type: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
```

### 3.3 EDITED: `backend/app/routes/whatsapp.py`

In the webhook handler, after thread create/find and before `db.add(WhatsAppMessage(...))`:

```python
# M21.4A: set inbound channel (first-write-only)
if not getattr(thread, "inbound_channel", None):
    thread.inbound_channel = "WHATSAPP"

# M21.4A: capture Meta CTWA referral (first-write-only)
if not getattr(thread, "ctwa_source_id", None) and not getattr(thread, "ctwa_source_url", None):
    referral = message.get("referral") if isinstance(message, dict) else None
    if isinstance(referral, dict):
        raw_url = str(referral.get("source_url") or "").strip()[:500]
        raw_id = str(referral.get("source_id") or "").strip()[:100]
        raw_type = str(referral.get("source_type") or "").strip()[:40]
        if raw_url or raw_id:
            thread.ctwa_source_url = raw_url or None
            thread.ctwa_source_id = raw_id or None
            thread.ctwa_source_type = raw_type or None
            logger.info(
                "WHATSAPP_CTWA_REFERRAL_CAPTURED thread_id=%s source_type=%s",
                thread.id, raw_type or "-",
            )
```

No outbound. No state transitions. No CE calls. Thread mutation only.

### 3.4 EDITED: `backend/app/services/conversation_engine.py`

**Module-level constants (after `_FAQ_PAYMENT_PROBE`):**
```python
_ACQ_INSTAGRAM    = "INSTAGRAM"
_ACQ_FACEBOOK     = "FACEBOOK"
_ACQ_GOOGLE       = "GOOGLE"
_ACQ_GOOGLE_MAPS  = "GOOGLE_MAPS"
_ACQ_WEBSITE      = "WEBSITE"
_ACQ_REFERRAL     = "REFERRAL"
_ACQ_DIRECT       = "DIRECT"
_ACQ_OTHER        = "OTHER"

_REF_CODE_SOURCE_MAP: dict[str, str] = {
    "ga":   _ACQ_GOOGLE,
    "ig":   _ACQ_INSTAGRAM,
    "fb":   _ACQ_FACEBOOK,
    "org":  _ACQ_DIRECT,
    "dir":  _ACQ_DIRECT,
    "otro": _ACQ_OTHER,
}
```

**Module-level function `_ctwa_to_acq_source()`:**
Deterministic URL-based mapping. Instagram URL → INSTAGRAM, Facebook URL → FACEBOOK,
non-empty referral with unrecognized URL → OTHER, empty → None.

**CE method `_maybe_set_attribution(ctx, state)`:**
Called in `_handle()` after lead confirmed, after cycle reset, before human check.
- Sets `lead.inbound_channel` from `thread.inbound_channel` (first-write-only)
- Sets `lead.acq_source` from priority chain (first-write-only)
- No-ops if `lead` is None
- Idempotent: second call with same data produces no change

**Call site in `_handle()` (before human check):**
```python
# M21.4A: populate attribution fields (first-write-only, no overwrite)
self._maybe_set_attribution(ctx, state)
```

### 3.5 EDITED: `backend/app/schemas/leads.py`

Added to `LeadOut`:
```python
# M21.4A canonical attribution (read-only)
inbound_channel: str | None = None
acq_source: str | None = None
ref_code: str | None = None
rc_code: str | None = None
```

`ref_code` and `rc_code` were already stored correctly but were invisible to the API.
This makes them accessible. Both are read-only (no corresponding `LeadUpdate` fields).

### 3.6 EDITED: `backend/app/ui/kanban_view.py`

Attribution block added to `render_lead_card()`. Reads: `inbound_channel`, `acq_source`,
`ref_code`, `rc_code`. Renders a `<div class="muted leadAttribution">` only when at
least one field is non-null. Combined `ref_code · rc_code` display when both present.
Injected directly after `<div class="muted leadContact">` in the card template.

### 3.7 NEW: `tests/test_m21_4a_attribution.py`

54 test cases covering ATTR-01 through ATTR-25.

---

## PART 4 — RUNTIME / DB PROOF

### 4.1 Migration chain applied to crm_test

```
Before:  alembic_version = 20260827_m21_3_thread_revision_zone_group
Stamped: 20260828_m2_authorized_path_monitoring  (schema objects already existed)
Applied: 20260829_m21_4a_attribution              (new M21.4A columns)
After:   alembic_version = 20260829_m21_4a_attribution
```

### 4.2 Schema verification (crm_test, live DB)

**`whatsapp_threads` new columns:**
```
ctwa_source_id   VARCHAR(100)   ✓
ctwa_source_type VARCHAR(40)    ✓
ctwa_source_url  VARCHAR(500)   ✓
inbound_channel  VARCHAR(20)    ✓
```

**`leads` new columns:**
```
acq_source       VARCHAR(30)    ✓
inbound_channel  VARCHAR(20)    ✓
ref_code         VARCHAR(10)    (pre-existing, now exposed in API/UI)
rc_code          VARCHAR(8)     (pre-existing, now exposed in API/UI)
```

### 4.3 Data state (crm_test)

| Metric | Value | Expected |
|---|---|---|
| Total threads | 50 | — |
| Threads with `inbound_channel = WHATSAPP` | 50 | All (backfilled by migration) |
| Threads with `ctwa_source_id` set | 0 | Expected — no ad traffic in crm_test |
| Total leads | 51 | — |
| Leads with `acq_source` set | 0 | Expected — historical canal unreliable, no backfill |
| Leads with `inbound_channel` set | 0 | Expected — CE not re-run on historical messages |
| Leads with `ref_code` set | 0 | Expected — no website traffic in crm_test yet |

Backfill of `leads.acq_source` deliberately omitted. Historical canal values have 96% null
rate and two incompatible vocabularies. First-write from live traffic will produce clean data.

---

## PART 5 — TEST RESULTS

### PART 14 — ATTR test suite

**ATTR-01 through ATTR-25: 54/54 PASS**

```
collected 54 items
54 passed, 3 warnings, 6 subtests passed in 1.82s
```

All 54 subtests pass. No failures. No skips.

Coverage:
- ATTR-01: WhatsApp inbound sets thread.inbound_channel = WHATSAPP ✓
- ATTR-02: Repeated messages do not overwrite existing channel ✓
- ATTR-03: WhatsApp channel alone does not imply acquisition source ✓
- ATTR-04: No evidence → acq_source remains null ✓
- ATTR-05: Meta CTWA referral parsed (3 subtests) ✓
- ATTR-06: Instagram CTWA URL → INSTAGRAM (3 subtests) ✓
- ATTR-07: Facebook CTWA URL → FACEBOOK (3 subtests) ✓
- ATTR-08: Ambiguous CTWA → OTHER, not guessed (4 subtests) ✓
- ATTR-09: Webhook retry idempotent — first-write-only for CTWA ✓
- ATTR-10: Existing acq_source not overwritten by later CTWA (2 subtests) ✓
- ATTR-11: ref_code first-write-only (2 subtests) ✓
- ATTR-12: rc_code first-write-only ✓
- ATTR-13: ref_code → acq_source mapping deterministic (6 subtests) ✓
- ATTR-14: Cycle reset source inspection — inbound_channel and acq_source absent (2 subtests) ✓
- ATTR-15: _maybe_set_attribution preserves existing source after cycle reset ✓
- ATTR-16: New Revision preserves original acquisition source ✓
- ATTR-17: Legacy canal backward compatible (3 subtests) ✓
- ATTR-18: Canonical attribution does not read canal (2 subtests) ✓
- ATTR-19: CRM renders technical channel in lead card (2 subtests) ✓
- ATTR-20: CRM renders acquisition source in lead card (2 subtests) ✓
- ATTR-21: CRM conditionally renders ref_code/rc_code (3 subtests) ✓
- ATTR-22: No raw Meta payload exposed (2 subtests) ✓
- ATTR-23: No outbound produced by attribution (2 subtests) ✓
- ATTR-24: Existing webhook behavior unaffected (3 subtests) ✓
- ATTR-25: _maybe_set_attribution is idempotent (2 subtests) ✓

### PART 15 — Full regression suite

```
Before M21.4A:  14 failed / 639 passed / 12 subtests passed
After  M21.4A:  14 failed / 693 passed / 18 subtests passed
Net change:      0 new failures / +54 new passing tests / +6 new subtests
```

**No regressions introduced.** All 14 pre-existing failures are from `test_m19_f2_2_outbound_kill_switch.py`
(static source-audit tests that reference an obsolete `/app/backend/...` path convention) and
`test_m2_authorized_paths.py` / `test_m18_business_logic.py` — all pre-existing from prior milestones,
none caused by M21.4A.

---

## PART 6 — DESIGN DECISIONS RECORDED

### D1: Lead.inbound_channel is display copy, not authoritative

The authoritative record is `WhatsAppThread.inbound_channel`. The kanban route loads leads
without a thread join, so CE propagates the value to `Lead.inbound_channel` as display copy.
This avoids adding a JOIN to the kanban query. The migration backfills threads but NOT leads
(CE must propagate on next contact).

### D2: No backfill of leads.acq_source from canal

canal has:
- 96% null rate (49/51 leads in crm_test)
- Two incompatible vocabularies (UI dropdown vs CE free-text)
- No reliable mapping to canonical values without guessing

Backfilling from unreliable data would pollute the canonical field. First live message will
populate cleanly.

### D3: CTWA fields stored on Thread, not Lead

The referral arrives in a WhatsApp message, which belongs to a Thread. Lead may be linked
after the fact (or may be a repeat customer on a new Revision). Storing on Thread is the
correct domain boundary. CE derives `acq_source` from Thread's CTWA fields at Lead-link time.

### D4: _ctwa_to_acq_source is deterministic-only

No probabilistic guessing. If the URL contains `instagram.com` → INSTAGRAM. If it contains
`facebook.com` or `fb.com` → FACEBOOK. Any other non-empty referral → OTHER. This preserves
the integrity of the canonical field — it never contains a guess.

### D5: First-write-only everywhere

Every attribution field follows the existing `if not getattr(lead, "field", None):` CE guard
pattern. This prevents any later message (retransmission, second visit, new campaign) from
silently overwriting the original acquisition source.

---

## PART 7 — WHAT IS NOT YET DONE (scope boundary)

The following are intentionally OUT OF SCOPE for M21.4A-FOUNDATION:

- UI for filtering/grouping leads by `acq_source` (reporting dashboard — next milestone)
- Migration of `canal` UI labels to canonical values (user-facing vocabulary migration)
- `GOOGLE_MAPS` structured detection (no deterministic CTWA URL pattern for GMaps yet)
- `REFERRAL` structured detection (requires referral code scheme design)
- UTM parameter capture from web form (website form doesn't currently pass UTMs to API)
- Admin/analyst API for attribution aggregation queries
- Deprecation of `canal` field (canal remains live; no timeline set)

---

## SUMMARY

| Check | Value |
|---|---|
| Files created | 2 (migration, test file) |
| Files edited | 5 (models, whatsapp route, CE, schemas, kanban) |
| New DB columns | 6 (4 on whatsapp_threads, 2 on leads) |
| Migration applied | crm_test ONLY — `20260829_m21_4a_attribution` |
| ATTR tests | 54/54 PASS |
| Full regression | 693 passed / 14 failed (0 new failures) |
| Outbound sent | 0 |
| Production DB | UNTOUCHED |
| Canal field | PRESERVED, unchanged |
| CTWA irrecoverable risk | MITIGATED — webhook now captures referral on first message |

NO CHANGES MADE TO: pricing / scheduling / CE authority / cycle-reset semantics / Booking Flow / n8n / Meta Flow / production DB / canal field.

OUTBOUND: OFF

PRODUCTION DB TOUCHED: NO

STOP.
