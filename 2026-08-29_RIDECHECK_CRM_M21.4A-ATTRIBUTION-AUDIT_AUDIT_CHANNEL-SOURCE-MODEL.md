PROJECT: RIDECHECK_CRM
TYPE: AUDIT
MILESTONE: M21.4A-ATTRIBUTION-AUDIT

DATE: 2026-08-29
AUDITOR: Claude (read-only — no code changes made)

---

AUDIT STATUS: PARTIAL

---

EXECUTIVE SUMMARY:

The current `canal` field is a nullable free-text String(50) on the `Lead` model with no database
enum constraint. It mixes channel and source semantics in eight legacy dropdown values
(IG_DM, IG_WHATSAPP, FB_DM, FB_WHATSAPP, WEBSITE, GOOGLE, GMAPS, OTROS) but is also written
by the CE as arbitrary customer-reported free text ("Formulario web", "Instagram", "referido",
etc.) — a vocabulary incompatible with the dropdown. The field is 96% null in the live test DB.

Two attribution fields already exist but are invisible in the UI: `ref_code` and `rc_code` on
Lead (added via migration 20260824_lead_attribution_fields). These capture website-tracking
tokens (ref: ga/ig/fb/org/dir/otro and cod: RC-XXXX from the wa.me prefill message). Neither
is exposed in any CRM screen or API response schema, and zero rows carry a non-null value.

The n8n AI prompt contains the directive: "Do not ask for canal. Canal is inferred from the
integration metadata, not from the user." This documents the original design intent: channel
should be system-inferred. That intent was never implemented.

The system correctly knows the technical channel (WHATSAPP) for every inbound message —
because every inbound message currently arrives via the WhatsApp webhook. It does not parse
Meta's Click-to-WhatsApp referral metadata, which is the primary irrecoverable attribution risk.

Proposed canonical model: split into `inbound_channel` (system-generated, Thread-level) +
`acq_source` (Lead-level, source-of-discovery). Keep `canal` column unchanged for legacy
continuity. Expose `ref_code` in the UI. Parse Meta CTWA referral before marketing traffic begins.

---

CURRENT CANAL MODEL:

Field:          Lead.canal
Type:           String(50), nullable=True, no DB enum constraint
Default:        NULL
Origin:         Original schema (predates migration system — no migration adds this column)
Validation:     UI only — kanban_actions.py validates against CANAL_OPCIONES list;
                REST API has NO enum enforcement (max_length=50 only)
Enum values:    Defined in kanban_view.py:CANAL_OPCIONES (not in models.py)
Scope:          Lead level — one value per Lead, not per Revision

---

CURRENT DB COLUMN:

Table:   leads
Column:  canal
Type:    VARCHAR(50), nullable
Default: NULL
Index:   none
Enum:    none (not a PostgreSQL ENUM type)

Also present on leads (M21.2-DATA, migration 20260824):
  ref_code    VARCHAR(10), nullable (website tracking ref: ga/ig/fb/org/dir/otro)
  rc_code     VARCHAR(8), nullable, indexed (website session code RC-XXXX)

---

CURRENT VALUES (enum defined in kanban_view.py):

CANONICAL DROPDOWN VALUES (CANAL_OPCIONES):
  IG_DM         — Instagram Direct Message
  IG_WHATSAPP   — Instagram link → WhatsApp
  FB_DM         — Facebook Direct Message
  FB_WHATSAPP   — Facebook link → WhatsApp
  WEBSITE       — Website (ambiguous — could be form or CTA → WhatsApp)
  GOOGLE        — Google search
  GMAPS         — Google Maps
  OTROS         — Other

CE AUTO-POPULATED VALUES (free text, NOT in CANAL_OPCIONES):
  "Formulario web"   — hardcoded in CE for website Flow leads
  "Instagram"        — from Flow como_llego free text
  "WhatsApp"         — from Flow como_llego (seen in smoke tests)
  "Google Ads"       — from Flow como_llego (seen in test fixtures)
  "referido"         — from Flow como_llego (seen in smoke tests)
  Any free text the customer types in the "¿Cómo llegaste?" Flow field

CONFLICT: These two vocabularies are incompatible. The CE writes free-text values that
never match the CANAL_OPCIONES enum. A UI filter for "IG_WHATSAPP" will never match
CE-populated "Instagram". A UI filter for "GOOGLE" will never match CE-populated "Google Ads".

VALUES FOUND IN LIVE DB OUTSIDE CANONICAL LIST:
  "Otro"    — 2 occurrences. NOT in CANAL_OPCIONES (which has "OTROS").
              Must have arrived via REST API (no UI enum enforcement) or direct DB write.

---

CURRENT DATA DISTRIBUTION:

DB: crm_test (51 total leads)

| CANAL VALUE    | COUNT | IN_OPCIONES | NOTE                                |
|----------------|-------|-------------|-------------------------------------|
| NULL           | 49    | N/A         | 96% of leads have no attribution    |
| "Otro"         | 2     | NO          | Not in CANAL_OPCIONES — legacy/API  |

| FIELD      | NON-NULL ROWS | TOTAL | NOTE                                    |
|------------|---------------|-------|-----------------------------------------|
| canal      | 2             | 51    | 96% null                                |
| ref_code   | 0             | 51    | Never populated in this DB              |
| rc_code    | 0             | 51    | Never populated in this DB              |

ASSESSMENT:
  - `canal` is consistently unpopulated in crm_test
  - The 2 "Otro" values are outside the canonical enum and likely pre-system manual entries
  - No website tracking (ref_code, rc_code) has fired — website flow never reached booking
  - Canal data in crm_test has zero operational value; it is an artifact of manual data entry
  - Production DB (crm) not queried — crm_test data is illustrative only

---

WRITE PATHS:

| # | SOURCE                          | WHEN                         | VALUE SET                       | OVERWRITE BEHAVIOR        | PATH                                         |
|---|---------------------------------|------------------------------|---------------------------------|---------------------------|----------------------------------------------|
| 1 | Manual CRM — ui_lead_create()   | Lead creation via UI         | Selected from CANAL_OPCIONES    | Initial write             | POST /ui/lead_create → kanban_actions.py:130 |
| 2 | Manual CRM — ui_lead_update()   | Manual edit via lead menu    | Selected from CANAL_OPCIONES    | ALWAYS OVERWRITE          | POST /ui/lead_update → kanban_actions.py:174 |
| 3 | REST API — create_lead()        | Lead creation via API        | Any free text (max 50 chars)    | Initial write             | POST /api/leads → api/leads.py:42-49         |
| 4 | REST API — update_lead()        | Lead update via API          | Any free text (if non-None)     | ALWAYS OVERWRITE if sent  | PATCH /leads/{id} → api/leads.py:108-109     |
| 5 | CE — _process_flow_response()   | Website Flow booking         | "Formulario web" (hardcoded)    | FIRST-WRITE-ONLY          | CE line 1639, guard line 1724                |
| 6 | CE — _process_flow_response()   | Generic Flow booking         | como_llego free text            | FIRST-WRITE-ONLY          | CE line 1644, guard line 1724                |
| 7 | CE — motorcycle contact Flow    | Motorcycle Flow response     | como_llego free text            | FIRST-WRITE-ONLY          | CE line 3625, guard line 3637                |

PATH 2 CRITICAL FINDING:
  ui_lead_update() sends canal="" (empty string) when the user leaves it unset.
  _clean_str("") returns None. Then:
    c = _clean_str(canal)      # → None
    if c and c not in CANAL_OPCIONES:  # → False (c is None)
    lead.canal = c             # → sets to None
  This means EVERY UI edit that doesn't explicitly set canal will NULL the field.
  A human editor can silently erase a CE-captured como_llego value on any lead edit.

PATH 5/6/7 GUARD PATTERN:
  CE uses `if canal and not lead.canal:` — correctly first-write-only.
  CE will NOT overwrite a manually-entered or previously-captured canal value.
  BUT: the inverse is not true — UI update (path 2) WILL overwrite a CE-set value.

N8N: canal is NOT referenced in any n8n workflow node. n8n AI prompt says:
  "Do not ask for canal. Canal is inferred from the integration metadata, not from the user."
  This confirms the original design intent but the inference was never implemented.

---

TECHNICAL CHANNEL AUTHORITY:

| INTEGRATION          | CHANNEL DETERMINABLE? | EVIDENCE                                         | WRITES `canal`? | WHERE AVAILABLE                     |
|----------------------|-----------------------|--------------------------------------------------|-----------------|-------------------------------------|
| WhatsApp webhook     | YES — WHATSAPP        | POST /integrations/whatsapp/webhook receives all | NO              | routes/whatsapp.py — every inbound  |
| Instagram DM         | NO INTEGRATION        | No Instagram DM webhook handler exists           | N/A             | Not integrated                      |
| Facebook DM          | NO INTEGRATION        | No Facebook DM webhook handler exists            | N/A             | Not integrated                      |
| Website form         | YES — inferred WA     | state.is_website_lead (20260618_website_lead_flag)| YES (CE path 5) | whatsapp_thread_states.is_website_lead |
| n8n transport        | PASSES THROUGH        | n8n passes thread_id + message to CE             | NO              | n8n never writes canal              |

CONCLUSION:
  Every inbound message currently enters via the WhatsApp webhook. The system definitively
  knows `inbound_channel = WHATSAPP` for ALL current leads. It does not record this anywhere
  as a structured attribute. The legacy IG_DM / FB_DM values in CANAL_OPCIONES imply Instagram/
  Facebook DM integrations that do not currently exist — those values can only be set manually.

---

SOURCE EVIDENCE AVAILABLE:

| EVIDENCE TYPE                   | AVAILABLE NOW | PERSISTED            | LOCATION                          | RELIABLE? | NOTES                                          |
|---------------------------------|---------------|----------------------|-----------------------------------|-----------|------------------------------------------------|
| Website tracking ref_code       | YES           | YES (if website flow) | Lead.ref_code (String 10)        | MEDIUM    | ga/ig/fb/org/dir/otro from tracking.js         |
| Website session code rc_code    | YES           | YES (if website flow) | Lead.rc_code (String 8, indexed) | MEDIUM    | RC-XXXX session token                          |
| Flow como_llego field           | YES           | YES (if Flow used)    | Lead.canal (free text)           | LOW       | Self-reported by customer — not validated      |
| Meta CTWA referral metadata     | NO            | NO                   | message.referral (not parsed)    | HIGH      | IRRECOVERABLE if not captured at first message |
| Meta ad_id / adset_id           | NO            | NO                   | Not in webhook parser            | HIGH      | Available from Meta CTWA payload               |
| Instagram referral              | NO            | NO                   | No IG DM integration             | N/A       | No integration exists                          |
| Facebook referral               | NO            | NO                   | No FB DM integration             | N/A       | No integration exists                          |
| UTM parameters                  | NO            | NO                   | Not in any model or route        | N/A       | WhatsApp doesn't carry UTM params              |
| gclid / gbraid / wbraid         | NO            | NO                   | Explicitly excluded in migration | N/A       | "are NOT in the WhatsApp message" (migration comment) |
| wa.me link parameters           | PARTIAL       | PARTIAL              | ref_code via message text parse  | MEDIUM    | Only works for website wa.me prefill           |
| QR code attribution             | NO            | NO                   | Not in any model                 | N/A       | Not implemented                                |
| Manual CRM selection            | YES           | YES                  | Lead.canal (dropdown)            | LOW       | Only as accurate as human entry                |

META CTWA REFERRAL DETAIL:
  Meta sends a `referral` object on the first message when a customer comes from a
  Click-to-WhatsApp ad. Structure: {source_url, source_id, headline, body, source_type,
  media: {type, url}}. The current webhook parser (routes/whatsapp.py) reads:
  message.get("text"), message.get("audio"), message.get("image"), message.get("interactive")
  — but DOES NOT read message.get("referral").
  This data is available in the first inbound message payload and ONLY there. Once the
  message is processed without capturing it, the referral is permanently lost.

---

CAMPAIGN EVIDENCE:

ref_code EXISTS:     YES — Lead.ref_code String(10), nullable
rc_code EXISTS:      YES — Lead.rc_code String(8), nullable, indexed

HOW THEY ARRIVE:
  Website tracking.js appends to the wa.me prefill:
    "ref: ga · cod: RC-ABCD"
  CE's _parse_website_form() extracts both tokens (CE lines 1243-1253).
  _send_booking_notification() does NOT pass ref_code or rc_code to email.
  Neither field is in LeadCreate / LeadUpdate schema (not exposed via REST API).
  Neither field is in any CRM UI template.

USED:                NO — stored but not consumed for any routing, filtering, or display
PERSISTED:           YES — Lead table (when website flow fires)
EXPOSED IN UI:       NO
EXPOSED IN API:      NO (not in schema files)
POPULATED IN LIVE DB: 0/51 leads (zero, crm_test)

OTHER CAMPAIGN FIELDS:
  utm_campaign, utm_source, utm_medium: DO NOT EXIST
  ad_id, adset_id, creative_id:         DO NOT EXIST
  referral_code, qr_code:               DO NOT EXIST

FINDING: ref_code represents a partial, invisible campaign-attribution implementation.
  It captures website-side attribution correctly but is never used downstream.

---

DOMAIN OWNERSHIP RECOMMENDATION:

A. INBOUND CHANNEL

  CANONICAL OWNER:    WhatsAppThread
  WHY:                The communication medium is determined when the thread is created and
                      never changes. A WhatsApp thread is always WHATSAPP. An Instagram DM
                      thread (if ever integrated) is always INSTAGRAM_DM. Not Lead-level
                      because a Lead could theoretically have multiple threads (though the
                      current domain model has one Lead ↔ one Thread).
                      In practice, since Lead ↔ Thread is 1:1, either works. Thread is more
                      semantically precise.
  LIFETIME:           Permanent — set at thread creation, never changed
  OVERWRITE POLICY:   NEVER overwrite — system sets once on first inbound message

  PRACTICAL RECOMMENDATION:
    Add `inbound_channel` column to `whatsapp_threads` (not leads).
    Populate automatically by the webhook handler.
    The CRM can JOIN through thread for display.
    Alternatively: add to leads for simplicity since 1:1 — but semantically belongs to Thread.

B. ACQUISITION SOURCE

  CANONICAL OWNER:    Lead (original acquisition event)
  WHY:                Source describes how the customer found RideCheck — this is a property
                      of the customer relationship, not of individual Revision cycles.
                      A customer who first came via Instagram and returns 6 months later is
                      still "originally acquired from Instagram."
  LIFETIME:           Lead lifetime — original_acq_source is permanent
  OVERWRITE POLICY:   NEVER overwrite original. Optionally add `latest_acq_source` for
                      re-acquisition tracking, but this is not required for initial launch.

  REPEAT REVISION SCENARIO:
    A customer first books via Instagram → original_acq_source = INSTAGRAM.
    Six months later they return via Google → for that new Revision, latest_acq_source = GOOGLE.
    Both values preserved if two columns used.
    For launch: only original_acq_source required. latest_acq_source is post-launch.

  SMALLEST VIABLE:
    One column: `acq_source` on Lead, first-write-only (mirrors CE's existing guard pattern).
    Post-launch: add `latest_acq_source` or acquisition_events table if needed.

C. CAMPAIGN ATTRIBUTION

  CANONICAL OWNER:    Lead (original acquisition event — existing ref_code / rc_code columns)
  WHY:                Campaign codes arrive at lead creation time only. They describe the
                      specific marketing activity that brought the customer. This is Lead-level,
                      not Revision-level (unless specific re-engagement campaigns are tracked).
  LIFETIME:           Lead lifetime — first-write-only
  OVERWRITE POLICY:   NEVER overwrite (CE already enforces this)

  EXISTING IMPLEMENTATION:
    ref_code (Lead.ref_code) and rc_code (Lead.rc_code) already implement campaign attribution
    for website-origin leads. These are correct. The gap is:
    1. Neither is exposed in the UI or API response schema
    2. Meta CTWA referral (for WhatsApp ad campaigns) is not captured at all

OWNERSHIP SUMMARY:

| CONCEPT           | CANONICAL OWNER | COLUMN               | LIFETIME       | OVERWRITE POLICY |
|-------------------|-----------------|----------------------|----------------|------------------|
| Inbound channel   | WhatsAppThread  | inbound_channel      | Thread origin  | Never            |
| Acq source        | Lead            | acq_source (new)     | Lead creation  | Never (first-write) |
| Website ref code  | Lead            | ref_code (exists)    | Lead creation  | Never (CE enforces) |
| Website session   | Lead            | rc_code (exists)     | Lead creation  | Never (CE enforces) |
| CTWA campaign     | Lead            | (not yet captured)   | Lead creation  | Never            |
| Legacy attribution| Lead            | canal (keep as-is)   | Lead lifetime  | Human-controlled |

---

PART 2 — VALUE SEMANTICS:

| LEGACY VALUE  | SEMANTIC TYPE           | IMPLIED CHANNEL    | IMPLIED SOURCE   | CONFIDENCE | RATIONALE                                       |
|---------------|-------------------------|--------------------|------------------|------------|-------------------------------------------------|
| IG_DM         | CHANNEL+SOURCE COMBINED | INSTAGRAM_DM       | INSTAGRAM        | HIGH       | Name unambiguously encodes both dimensions      |
| IG_WHATSAPP   | CHANNEL+SOURCE COMBINED | WHATSAPP           | INSTAGRAM        | HIGH       | IG link → WA; channel=WA is explicit in name   |
| FB_DM         | CHANNEL+SOURCE COMBINED | FACEBOOK_DM        | FACEBOOK         | HIGH       | Name unambiguously encodes both dimensions      |
| FB_WHATSAPP   | CHANNEL+SOURCE COMBINED | WHATSAPP           | FACEBOOK         | HIGH       | FB link → WA; channel=WA is explicit in name   |
| WEBSITE       | SOURCE (ambiguous)      | WHATSAPP or WEB    | WEBSITE          | MEDIUM     | Ambiguous: could be website CTA → WA or web form |
| GOOGLE        | SOURCE ONLY             | WHATSAPP (assumed) | GOOGLE           | MEDIUM     | No channel info; assumed Google search → WA    |
| GMAPS         | SOURCE ONLY             | WHATSAPP (assumed) | GOOGLE_MAPS      | MEDIUM     | Google Maps listing → WA                        |
| OTROS         | UNKNOWN/LEGACY          | UNKNOWN            | OTHER            | LOW        | Catch-all; no information                       |
| "Formulario web" | SOURCE (CE free text) | WHATSAPP          | WEBSITE          | HIGH       | CE hardcodes this for is_website_lead=True      |
| "Instagram"   | SOURCE (CE free text)   | WHATSAPP           | INSTAGRAM        | MEDIUM     | Self-reported by customer in Flow               |
| "WhatsApp"    | CHANNEL (misuse)        | WHATSAPP           | UNKNOWN          | LOW        | Customer reports channel not source             |
| "referido"    | SOURCE (CE free text)   | WHATSAPP           | REFERRAL         | MEDIUM     | Self-reported; not in any canonical list        |
| "Google Ads"  | SOURCE (CE free text)   | WHATSAPP           | GOOGLE           | MEDIUM     | Self-reported; more specific than GOOGLE        |
| "Otro"        | UNKNOWN/LEGACY          | UNKNOWN            | OTHER            | LOW        | In DB but not in CANAL_OPCIONES; API or DB-direct |

FINDING: The free-text CE values ("Formulario web", "Instagram", etc.) and the dropdown values
  (IG_WHATSAPP, GOOGLE, etc.) represent completely separate vocabularies with zero overlap.
  Any filter query must handle both vocabularies simultaneously for accurate results.
  Currently, the CRM filter only matches exact values — filtering by GOOGLE misses "Google Ads".

---

LEGACY NORMALIZATION MAPPING:

| LEGACY VALUE     | NEW CHANNEL       | NEW ACQ_SOURCE | CONFIDENCE | RATIONALE                                                   |
|------------------|-------------------|----------------|------------|-------------------------------------------------------------|
| IG_DM            | INSTAGRAM_DM      | INSTAGRAM      | HIGH       | Name encodes both; DM = Instagram DM channel                |
| IG_WHATSAPP      | WHATSAPP          | INSTAGRAM      | HIGH       | Name encodes both; WA is explicit                           |
| FB_DM            | FACEBOOK_DM       | FACEBOOK       | HIGH       | Name encodes both; DM = Facebook DM channel                 |
| FB_WHATSAPP      | WHATSAPP          | FACEBOOK       | HIGH       | Name encodes both; WA is explicit                           |
| WEBSITE          | WHATSAPP          | WEBSITE        | MEDIUM     | Most likely: website CTA → WA. If website form: confirmed.  |
| GOOGLE           | WHATSAPP          | GOOGLE         | MEDIUM     | Assumed Google search → WhatsApp contact                    |
| GMAPS            | WHATSAPP          | GOOGLE_MAPS    | MEDIUM     | Google Maps → WhatsApp contact                              |
| OTROS            | UNKNOWN           | OTHER          | LOW        | No information. Cannot normalize with confidence.           |
| "Formulario web" | WHATSAPP          | WEBSITE        | HIGH       | CE hardcodes; is_website_lead=True means WA channel         |
| "Instagram"      | WHATSAPP          | INSTAGRAM      | MEDIUM     | Self-reported; channel=WA since message came via WA         |
| "WhatsApp"       | WHATSAPP          | UNKNOWN        | LOW        | Customer reports channel not source; source unknown         |
| "referido"       | WHATSAPP          | REFERRAL       | MEDIUM     | Referral source; channel=WA                                 |
| "Google Ads"     | WHATSAPP          | GOOGLE         | MEDIUM     | More specific than GOOGLE; maps to same source              |
| "Otro"           | UNKNOWN           | OTHER          | LOW        | Same as OTROS                                               |

Note: Do NOT apply this mapping to the DB. This is a design reference for migration planning.

---

BACKWARD COMPATIBILITY RISKS:

1. KANBAN_VIEW.PY: CANAL_OPCIONES constant used in:
   - New lead creation form (line 1207)
   - Lead table view form (line 1684)
   - Lead card edit form (line 4967-4969, 5053-5055)
   - Any change to enum values breaks the dropdown UI

2. KANBAN_VIEW.PY: _canal_label() (lines 2930-2938, 6092-6100):
   - Maps enum → human-readable labels in chip filters
   - Adding new values requires adding entries here

3. KANBAN.PY (and KANBAN_VIEW.PY): canal filter WHERE clause:
   - `Lead.canal == canal` — exact string comparison
   - Changing values (e.g., IG_WHATSAPP → WHATSAPP/INSTAGRAM) breaks existing filter URLs

4. KANBAN_ACTIONS.PY: validation `c not in CANAL_OPCIONES → None`:
   - New canonical values not yet in CANAL_OPCIONES would be rejected by UI
   - REST API has no validation — inconsistency risk

5. CONVERSATION_ENGINE.PY:
   - Hardcoded `canal = "Formulario web"` (line 1639) — must match UI expectations
   - `canal = flow_data.get("como_llego")` — free text — no enum enforcement

6. TEST SUITE (test_m18_business_logic.py):
   - 4 tests assert specific canal values: "Formulario web", "Instagram", "Google Ads"
   - Any change to canal write logic breaks these tests

7. REST API SCHEMAS (schemas/leads.py):
   - `LeadCreate.canal` / `LeadUpdate.canal` — free text, max_length=50
   - No enum enforcement — any API client can write any value
   - External API clients may depend on reading canal values

8. RESEND EMAIL:
   - `send_booking_notification(source="whatsapp")` — hardcoded "whatsapp" string
   - NOT from lead.canal — not affected by canal changes
   - BUT: "Cómo llegó:" in email always shows "whatsapp" regardless of canal — misleading

9. N8N WORKFLOW JSON:
   - References canal as a field name in AI prompt context ("Do not ask for canal")
   - No functional code dependency — does not read or write canal
   - Not a breaking dependency

10. CSS / JavaScript:
    - No CSS class names derived from canal values found
    - No JavaScript data attributes keyed on canal values found

---

RECOMMENDED M21.4A MODEL:

Guiding principle: Add new fields; do NOT rename or remove `canal`. Preserve backward compat.

NEW FIELDS:

Field 1: `inbound_channel` on `whatsapp_threads` (OR `leads` for simplicity given 1:1 relationship)
  Type:     VARCHAR(20), nullable
  Values:   WHATSAPP, INSTAGRAM_DM, FACEBOOK_DM, WEB, OTHER, UNKNOWN
  Set by:   routes/whatsapp.py at thread creation (system-generated)
  Guard:    First-write-only
  UI:       Read-only display, not editable

Field 2: `acq_source` on `leads`
  Type:     VARCHAR(30), nullable
  Values:   INSTAGRAM, FACEBOOK, GOOGLE, GOOGLE_MAPS, WEBSITE, QR, REFERRAL, DIRECT, OTHER, UNKNOWN
  Set by:   CE from ref_code normalization → como_llego normalization → CTWA referral
  Priority: ref_code (highest) → CTWA referral → normalized como_llego → manual canal normalization
  Guard:    First-write-only (same pattern as existing ref_code guard)
  UI:       Primarily read-only; human override allowed

Field 3: `acq_channel_raw` on `leads` (Meta CTWA referral capture)
  Type:     VARCHAR(200), nullable
  Values:   source_url from Meta referral object (full URL, or ad ID)
  Set by:   routes/whatsapp.py on first inbound message if referral object present
  Guard:    First-write-only
  UI:       Read-only (debug/admin only)

EXISTING FIELDS — NO CHANGES:
  `canal`:    Keep as-is. Used for legacy manual attribution and CE como_llego capture.
              Gradually replaced by `acq_source` for new leads. Old leads: `canal` is authoritative.
  `ref_code`: Keep as-is. Already implemented correctly. Expose in UI (currently invisible).
  `rc_code`:  Keep as-is. Indexed. Expose in UI if campaign tracking is activated.

`canal` RENAME POLICY:
  Do NOT rename the column. DO rename the UI label from "Canal" to "Origen (legado)" or
  simply suppress it from the edit form over time as `acq_source` becomes the primary field.
  Enum validation (`CANAL_OPCIONES`) can remain for legacy writes.

NORMALIZATION SERVICE (needed in M21.4A.3):
  A function `_normalize_como_llego(raw: str) -> str | None` that maps:
    "instagram" / "ig" / "instagram dm" → INSTAGRAM
    "facebook" / "fb"                  → FACEBOOK
    "google" / "google ads" / "ga"     → GOOGLE
    "google maps" / "gmaps"            → GOOGLE_MAPS
    "website" / "web" / "formulario web" / "pagina web" → WEBSITE
    "referido" / "referral" / "ref"   → REFERRAL
    "directo" / "directo" / "direct"  → DIRECT
    "otro" / "otros" / "other"        → OTHER
    anything else / empty              → None (do not write acq_source)
  This prevents garbage from customer free-text contaminating `acq_source`.

EXPLICIT EXCLUSIONS from M21.4A:
  - Urgency scoring
  - Budget scoring
  - AI lead quality
  - Dashboards / analytics
  - Follow-up automation
  - Marketing automation
  - latest_acq_source (separate from original_acq_source)
  - Acquisition event history table
  - UTM parameter capture (WhatsApp doesn't carry UTM)

---

RECOMMENDED UI BEHAVIOR:

INBOUND CHANNEL:
  Behavior:  System-generated. Read-only in UI. Not editable by humans.
  Rationale: The technical channel is known with certainty by the system. Human override
             introduces error and is not needed.
  Display:   Small badge on lead card (e.g., "📱 WhatsApp").

ACQ_SOURCE:
  Behavior:  System-generated when evidence exists. Human-editable as override.
  Rationale: System evidence (ref_code, CTWA referral) is reliable. como_llego is less so.
             Humans should be able to correct "WhatsApp" (channel misuse) to the real source.
  Display:   Editable dropdown in lead card, separate from canal. Values from canonical enum.
  Guard:     Backend accepts human override but CE does not overwrite a non-null value.

CAMPAIGN / ref_code:
  Behavior:  Read-only. Shown only if non-null.
  Rationale: Campaign codes are system evidence — not human-entered. Not editable.
  Display:   Small label on lead card if ref_code is set (e.g., "ref: ig / RC-ABCD").

CANAL (legacy):
  Behavior:  Keep editable for backward compatibility and manual legacy leads.
  Future:    Gradually de-emphasize. Consider relabeling as "Canal (legado)" in the UI label.
  No immediate UI changes required.

---

RECOMMENDED MIGRATION STRATEGY:

Step 0 (PRE-REQUISITE):
  Apply unapplied migration 20260828_m2_authorized_path_monitoring first (from M21.3-PRELAUNCH-AUDIT BLOCK-01).
  M21.4A migrations chain after this.

Step 1 (M21.4A.1 — Additive migration):
  ALTER TABLE whatsapp_threads ADD COLUMN inbound_channel VARCHAR(20);
  ALTER TABLE leads ADD COLUMN acq_source VARCHAR(30);
  ALTER TABLE leads ADD COLUMN acq_channel_raw VARCHAR(200);
  Optional: CREATE INDEX on leads.acq_source

Step 2 (M21.4A.2 — Backfill):
  Backfill inbound_channel = 'WHATSAPP' for all existing WhatsAppThread rows.
  (All current threads are WhatsApp — no ambiguity.)
  UPDATE whatsapp_threads SET inbound_channel = 'WHATSAPP' WHERE inbound_channel IS NULL;

Step 3 (M21.4A.3 — Backfill acq_source from canal):
  For existing leads with canal values, attempt normalization:
  IG_DM / IG_WHATSAPP → INSTAGRAM
  FB_DM / FB_WHATSAPP → FACEBOOK
  WEBSITE / "Formulario web" → WEBSITE
  GOOGLE / "Google Ads" → GOOGLE
  GMAPS → GOOGLE_MAPS
  "Instagram" → INSTAGRAM
  "referido" → REFERRAL
  everything else → leave null (OTHERS can be reviewed manually)
  IMPORTANT: Only backfill acq_source where it is NULL. Do NOT touch ref_code or rc_code.

---

PART 5 — INBOUND TECHNICAL CHANNEL AUTHORITY (SUMMARY):

| INTEGRATION       | CAN DETERMINE CHANNEL? | EVIDENCE                                  | CURRENTLY WRITES CANAL? |
|-------------------|------------------------|-------------------------------------------|-------------------------|
| WhatsApp webhook  | YES — WHATSAPP         | Every inbound message                     | NO                      |
| Instagram DM      | NO INTEGRATION         | Endpoint does not exist                   | NO                      |
| Facebook DM       | NO INTEGRATION         | Endpoint does not exist                   | NO                      |
| Website form      | PARTIAL — WA           | state.is_website_lead flag                | YES — "Formulario web"  |
| n8n               | NO (transport only)    | Passes through; no channel enrichment     | NO                      |

---

PART 6 — SOURCE ATTRIBUTION EVIDENCE (SUMMARY):

| EVIDENCE TYPE             | AVAILABLE | PERSISTED | RELIABLE | CURRENT STATUS                                   |
|---------------------------|-----------|-----------|----------|--------------------------------------------------|
| Meta CTWA referral        | YES       | NO        | HIGH     | NOT PARSED — irrecoverable if missed at first msg |
| Website ref_code (tracking.js) | YES  | YES       | MEDIUM   | Stored but invisible in UI/API                   |
| Website rc_code           | YES       | YES       | MEDIUM   | Stored but not used downstream                   |
| Flow como_llego           | YES       | YES       | LOW      | Customer-reported free text → lead.canal          |
| UTM parameters            | NO        | NO        | N/A      | WhatsApp doesn't carry UTM                        |
| gclid/gbraid/wbraid       | NO        | NO        | N/A      | Not in WhatsApp message (confirmed in migration)  |

---

REQUIRED BEFORE CLOSED BETA:
PARTIAL

Attribution fields (canal, acq_source, inbound_channel) are advisory only and not part of
any business logic. Not required for closed beta to function.

However: parsing Meta CTWA referral metadata IS required before any paid Meta ad campaign
launches. Closed beta with 1-2 manually whitelisted contacts (no ads) does not require this.

MINIMUM FOR CLOSED BETA:
  1. Expose ref_code in UI (trivial — already stored, just not shown). LOW effort.
  2. Nothing else in M21.4A blocks closed beta.

---

REQUIRED BEFORE PUBLIC LAUNCH:
PARTIAL

Full M21.4A implementation recommended before:
  - Running Meta Click-to-WhatsApp ad campaigns
  - Running Instagram / Facebook ad campaigns that link to WhatsApp
  - Operating with marketing traffic at any scale

Without it: customer acquisition data is irrecoverable for any ad-driven traffic.
With it: every lead has accurate channel + source + campaign data from day one.

---

IRRECOVERABLE ATTRIBUTION RISK IF DEFERRED:

CRITICAL: Meta Click-to-WhatsApp referral data.

When a customer clicks a Meta ad and opens WhatsApp, Meta sends a `referral` object in the
FIRST inbound message. It contains: source_url (the ad or post URL), source_id (ad set or
page ID), headline, body, source_type (ad, post, etc.), and media info.

This data is present ONLY in the FIRST message. If the webhook processes that message without
capturing `message["referral"]`, the attribution evidence is permanently lost. There is no
API to retroactively retrieve which ad a customer clicked before messaging.

If RideCheck runs Meta ads before this is implemented:
  - All customers who come from those ads will appear as unattributed (canal=null, acq_source=null)
  - Cannot reconstruct which customers came from which ad after the fact
  - ROAS (Return on Ad Spend) calculation will be impossible

RECOMMENDATION: Parse and store `message.get("referral")` in routes/whatsapp.py before
any Meta ad campaign launches. Store as `lead.acq_channel_raw` (the source_url) and
normalize source_type to `lead.acq_source`. This is a SMALL code change in one file.

NON-IRRECOVERABLE RISKS (CAN BE FIXED LATER):
  - Splitting canal into channel + source: existing data can be backfilled from canal values
  - Exposing ref_code in UI: stored already, just needs display code
  - Normalizing como_llego: stored as canal, can be normalized post-hoc
  - Adding acq_source column: additive, no data loss

---

RECOMMENDED IMPLEMENTATION PHASES:

M21.4A.1 — Canonical model definition + migration
  1. Define INBOUND_CHANNEL_VALUES and ACQ_SOURCE_VALUES enums/constants in models or constants file
  2. Write Alembic migration: add inbound_channel (VARCHAR 20) to whatsapp_threads,
     add acq_source (VARCHAR 30) to leads, add acq_channel_raw (VARCHAR 200) to leads
  3. Include backfill of inbound_channel = 'WHATSAPP' for existing threads
  4. Include best-effort backfill of acq_source from existing canal values (normalization map)
  5. ESTIMATED SCOPE: SMALL (1 migration + constants)

M21.4A.2 — Inbound channel auto-population
  1. In routes/whatsapp.py: when thread is first created (line 366), set
     thread.inbound_channel = "WHATSAPP" (for WhatsApp webhook)
  2. First-write-only guard
  3. ESTIMATED SCOPE: TINY (3-5 lines in whatsapp.py)

M21.4A.3 — Meta CTWA referral capture [HIGHEST PRIORITY — irrecoverable]
  1. In routes/whatsapp.py, in message processing loop:
     referral = message.get("referral") or {}
     if referral and not lead.acq_channel_raw:
         lead.acq_channel_raw = referral.get("source_url") or referral.get("source_id") or ""
     Normalize source_type → acq_source (ad → META_AD, etc.)
  2. ESTIMATED SCOPE: SMALL (10-15 lines in whatsapp.py)

M21.4A.4 — Source attribution from existing evidence
  1. Implement _normalize_como_llego() function
  2. In CE _process_flow_response() and motorcycle handler:
     acq_source = _normalize_como_llego(raw_como_llego)
     if acq_source and not lead.acq_source:
         lead.acq_source = acq_source
  3. Separately: normalize ref_code → acq_source:
     if lead.ref_code and not lead.acq_source:
         lead.acq_source = _normalize_ref_code(lead.ref_code)
     Mapping: ga → GOOGLE, ig → INSTAGRAM, fb → FACEBOOK, org → DIRECT, dir → DIRECT, otro → OTHER
  4. ESTIMATED SCOPE: SMALL (1 utility function + integration points in CE)

M21.4A.5 — UI exposure
  1. Show acq_source as read-only badge on lead card (new field, separate from canal)
  2. Show ref_code as read-only label if non-null (already stored — just display it)
  3. Show inbound_channel as read-only badge
  4. Keep canal dropdown editable for legacy compatibility
  5. ESTIMATED SCOPE: SMALL (HTML/render changes in kanban_view.py)

M21.4A.6 (OPTIONAL, POST-LAUNCH) — Full API exposure
  1. Add acq_source, inbound_channel, ref_code to LeadOut / LeadDetailOut schemas
  2. ESTIMATED SCOPE: TINY

---

NO CHANGES MADE:
YES

OUTBOUND:
OFF

PRODUCTION DB TOUCHED:
NO

STOP.
