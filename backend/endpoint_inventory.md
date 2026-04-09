# Endpoint Inventory

This file is a practical inventory of the current backend/UI endpoints in this repo.

Sources used:
- `backend/swagger_endpoints.md`
- routers under `backend/app/api`, `backend/app/routes`, `backend/app/ui`
- schemas under `backend/app/schemas`
- endpoint-side validation in API/UI handlers

Notes:
- "Valid/invalid" focuses on rules enforced by schemas or endpoint logic.
- Some UI endpoints are form/redirect endpoints rather than JSON APIs.
- Some outputs are summarized when the exact payload depends on runtime data.

## JSON API

### Leads

#### `POST /leads`
- Input body: `LeadCreate`
- Fields:
  - `telefono?: string <= 40`
  - `nombre?: string <= 80`
  - `apellido?: string <= 80`
  - `email?: string <= 120`
  - `canal?: string <= 50`
  - `compro_el_auto?: "SI" | "NO"`
- Valid:
  - `compro_el_auto` omitted
  - `compro_el_auto="SI"`
  - `compro_el_auto="NO"`
- Invalid:
  - `compro_el_auto="MAYBE"`
  - any overlong string field
- Output: `LeadOut`
  - `id: int`
  - `created_at: datetime`
  - `estado: string`
  - `flag?: string`
  - `telefono?: string`
  - `nombre?: string`
  - `apellido?: string`
  - `necesita_humano: bool`
  - `motivo_perdida?: string`

#### `GET /leads`
- Query params:
  - `telefono?`
- Valid:
  - no query params
  - normalizable phone string
- Invalid:
  - malformed phone may fail normalization and return validation error depending on value
- Output: `LeadOut[]`

#### `PATCH /leads/{lead_id}`
- Path params:
  - `lead_id: int`
- Input body: `LeadUpdate`
- Fields:
  - `estado?: "CONSULTA_NUEVA" | "COORDINAR_DISPONIBILIDAD" | "AGENDADO" | "REVISION_COMPLETA" | "ATENCION_HUMANA"`
  - `motivo_perdida?: "PRECIO" | "DISPONIBILIDAD" | "OTRO"`
  - `necesita_humano?: bool`
  - `flag?: "PRESUPUESTANDO" | "PRESUPUESTO_ENVIADO" | "ACEPTADO" | "RECOMPRA" | "PERDIDO"`
  - `telefono?: string <= 40`
  - `nombre?: string <= 80`
  - `apellido?: string <= 80`
  - `email?: string <= 120`
  - `canal?: string <= 50`
  - `compro_el_auto?: "SI" | "NO"`
- Valid:
  - `{"estado":"AGENDADO"}`
  - `{"flag":"PERDIDO","motivo_perdida":"PRECIO"}`
  - `{"necesita_humano":true}`
- Invalid:
  - unknown `estado`
  - unknown `flag`
  - `motivo_perdida` when `flag != "PERDIDO"`
  - invalid `compro_el_auto`
- Output: `LeadOut`

#### `GET /leads/{lead_id}`
- Path params:
  - `lead_id: int`
- Valid:
  - existing lead id
- Invalid:
  - missing lead -> `404`
- Output: `LeadDetailOut`
  - same as `LeadOut`
  - `latest_revision?: RevisionSummaryOut`

#### `GET /leads/{lead_id}/whatsapp`
- Path params:
  - `lead_id: int`
- Valid:
  - existing lead linked to a WhatsApp thread
- Invalid:
  - lead missing -> `404`
  - no WhatsApp thread found for this lead -> `404`
- Output: `WhatsAppThreadOut`
  - `thread_id`
  - `lead_id`
  - `contact_id`
  - `wa_id`
  - `display_name`
  - `unread_count`
  - `last_message_at`
  - `last_message_preview`
  - `last_message_id`

### Lead Revisions

#### `POST /leads/{lead_id}/revisions`
- Path params:
  - `lead_id: int`
- Input body: `RevisionCreate`
- Fields:
  - Vehicle:
    - `tipo_vehiculo?: string <= 30`
    - `marca?: string <= 50`
    - `modelo?: string <= 50`
    - `anio?: int`
    - `link_compra?: string`
    - `presupuesto_compra?: int`
  - Seller:
    - `vendedor_tipo?: string <= 20`
    - `tipo_vendedor?: string <= 20`
    - `agencia_id?: int`
    - `compro?: "SI" | "NO" | "OFRECIDO"`
    - `resultado_link?: string`
    - `comision?: int`
    - `cobrado?: "SI" | "NO"`
    - `fecha_cobro?: date`
  - Zone/address:
    - `zone_group?: string <= 50`
    - `zone_detail?: string <= 80`
    - `direccion_texto?: string`
    - `link_maps?: string`
    - `direccion_estado?: string <= 20`
  - Pricing/payment:
    - `precio_base?: int`
    - `viaticos?: int`
    - `precio_total?: int`
    - `pago?: bool`
    - `medio_pago?: string <= 20`
  - Turno:
    - `turno_fecha?: date`
    - `turno_hora?: time`
    - `cliente_presente?: bool`
    - `turno_notas?: string`
  - Result:
    - `estado_revision?: string <= 20`
    - `resultado?: string <= 20`
    - `motivo_rechazo?: string <= 80`
- Valid:
  - valid typed values for dates/times/bools/ints
  - `compro` in `SI/NO/OFRECIDO`
  - `cobrado` in `SI/NO`
- Invalid:
  - `compro="MAYBE"`
  - `cobrado="PENDING"`
  - overlong constrained strings
- Output: `RevisionOut`
  - all revision fields plus `id`, `lead_id`, `created_at`

#### `GET /leads/{lead_id}/revisions`
- Output: `RevisionOut[]`

#### `GET /leads/{lead_id}/revisions/latest`
- Output: `RevisionOut`

#### `PATCH /leads/{lead_id}/revisions/latest`
- Input body: `RevisionUpdate`
- Fields: same as `RevisionCreate` plus:
  - `recalcular_presupuesto: bool = true`
- Valid/invalid: same as `RevisionCreate`
- Output: `RevisionOut`

#### `PATCH /revisions/{revision_id}`
- Path params:
  - `revision_id: int`
- Input body: `RevisionUpdate`
- Valid/invalid: same as `RevisionCreate`
- Output: `RevisionOut`

### Thread Revisions

#### `POST /api/revisions`
- Input body: `ThreadRevisionCreateIn`
- Fields:
  - `thread_id: int`
  - `candidate_id: int`
- Valid:
  - existing thread
  - existing candidate
  - candidate belongs to thread
- Invalid:
  - missing thread -> `404`
  - missing candidate -> `404`
  - candidate belongs to another thread -> `400`
- Output: `ThreadRevisionCreateOut`
```json
{
  "revision_id": 123
}
```

#### `PATCH /api/revisions/{revision_id}`
- Path params:
  - `revision_id: int`
- Input body: `ThreadRevisionPatch`
- Fields:
  - `status?: "draft" | "collecting_data" | "booked" | "completed"`
  - `buyer_name?: string <= 120`
  - `buyer_phone?: string <= 40`
  - `buyer_email?: string <= 120`
  - `seller_type?: string <= 30`
  - `seller_name?: string <= 120`
  - `address?: string`
  - `scheduled_date?: date`
  - `scheduled_time?: time`
  - `tipo_vehiculo?: string <= 30`
  - `marca?: string <= 50`
  - `modelo?: string <= 50`
  - `anio?: int`
  - `publication_url?: string`
- Valid:
  - partial patches
  - valid enum for `status`
  - valid date/time types
- Invalid:
  - bad `status`
  - overlong constrained fields
- Output: `ThreadRevisionOut`
  - includes all thread revision fields plus timestamps
- Special behavior:
  - `null` values are ignored and do not overwrite existing values

### Pricing

#### `POST /api/pricing/quote`
- Input body: `PricingQuoteIn`
- Fields:
  - `tipo_vehiculo: string 1..30`
  - `zone_group: string 1..50`
  - `zone_detail: string 1..80`
- Valid:
  - non-empty strings
  - vehicle/zone combination that exists in pricing matrix
- Invalid:
  - empty strings -> `422`
  - unknown vehicle or zone match -> `404`
- Output: `PricingQuoteOut`
```json
{
  "tipo_vehiculo": "AUTO",
  "zone_group": "CABA",
  "zone_detail": "Palermo",
  "precio_base": 130000,
  "viaticos": 15000,
  "precio_total": 145000
}
```

### Schedule

#### `POST /api/schedule/check`
- Input body: `ScheduleCheckIn`
- Fields:
  - `address: string, required, min_length=1`
  - `preferred_day: date, required`
  - `preferred_time: time, required`
  - `zone_group?: string <= 50`
  - `zone_detail?: string <= 80`
  - `distance_km?: float >= 0`
  - `is_holiday?: bool = false`
  - `exclude_revision_id?: int`
- Valid:
  - required fields present and correctly typed
  - `distance_km` omitted or `>= 0`
- Invalid:
  - blank `address`
  - invalid date/time format
  - negative `distance_km`
- Output: `ScheduleCheckOut`
  - `valid: bool`
  - `suggested_slots: string[]`
  - `approval_tag: string`
  - `requested_slot: { start, end }`
  - `business_hours: string`
  - `service_minutes: int`
  - `buffer_minutes: int`
  - `travel_minutes: int`
  - `total_slot_minutes: int`
  - `conflicts: ScheduleConflictOut[]`
  - `reasons: string[]`
  - `rules_applied: string[]`

#### `GET /api/schedule/slots`
- Query params:
  - `preferred_day: date, required`
  - `address: string, required, min_length=1`
  - `zone_group?: string`
  - `zone_detail?: string`
  - `distance_km?: float >= 0`
  - `is_holiday?: bool`
  - `exclude_revision_id?: int`
- Valid:
  - same shape rules as schedule check except no explicit requested time
- Invalid:
  - blank address
  - invalid date
  - negative `distance_km`
- Output: `ScheduleSlotsOut`
```json
{
  "preferred_day": "2026-04-08",
  "business_hours": "09:00-18:00",
  "slots": [
    "2026-04-08T10:00",
    "2026-04-08T11:30"
  ],
  "rules_applied": [
    "Duracion fija de revision: 45 minutos"
  ]
}
```

### WhatsApp API

#### `GET /api/whatsapp/threads`
- Input body: none
- Output: `WhatsAppThreadOut[]`

#### `GET /api/whatsapp/thread/{thread_id}`
- Path params:
  - `thread_id: int`
- Valid:
  - existing thread
- Invalid:
  - missing thread -> `404`
- Output: `WhatsAppThreadOut`

#### `GET /api/whatsapp/thread/{thread_id}/messages`
- Path params:
  - `thread_id: int`
- Query params:
  - `limit: int`, default `10`, min `1`, max `20`
- Valid:
  - `limit=1..20`
- Invalid:
  - `limit=0`
  - `limit=21`
- Output: `WhatsAppThreadMessagesOut`
  - `thread_id`
  - `messages: WhatsAppMessageOut[]`

#### `GET /api/whatsapp/thread/{thread_id}/state`
- Path params:
  - `thread_id: int`
- Output: `WhatsAppThreadStateRead`
  - `id?: int`
  - `thread_id: int`
  - `last_intent?: string`
  - `last_stage?: string`
  - `needs_human: bool`
  - `current_focus_candidate_id?: int`
  - `current_revision_id?: int`
  - `last_processed_inbound_wa_message_id?: string`
  - `customer_name?: string`
  - `home_zone_group?: string`
  - `home_zone_detail?: string`
  - `created_at?: datetime`
  - `updated_at?: datetime`

#### `PATCH /api/whatsapp/thread/{thread_id}/state`
- Input body: `WhatsAppThreadStatePatch`
- Fields:
  - `last_intent?: string <= 30`
  - `last_stage?: string <= 30`
  - `needs_human?: bool`
  - `current_focus_candidate_id?: int`
  - `current_revision_id?: int`
  - `last_processed_inbound_wa_message_id?: string <= 191`
  - `customer_name?: string <= 120`
  - `home_zone_group?: string <= 50`
  - `home_zone_detail?: string <= 80`
- Valid:
  - partial patches
- Invalid:
  - overlong constrained strings
- Output: `WhatsAppThreadStateRead`

#### `GET /api/whatsapp/thread/{thread_id}/candidates`
- Output: `WhatsAppThreadCandidateRead[]`

#### `POST /api/whatsapp/thread/{thread_id}/candidates`
- Input body: `WhatsAppThreadCandidateCreate`
- Fields:
  - `label?: string <= 120`
  - `marca?: string <= 50`
  - `modelo?: string <= 50`
  - `version_text?: string <= 120`
  - `anio?: int`
  - `tipo_vehiculo?: string <= 30`
  - `zone_group?: string <= 50`
  - `zone_detail?: string <= 80`
  - `direccion_texto?: string`
  - `source_text?: string`
  - `status?: string <= 30`, default `"mentioned"`
- Valid:
  - partial body
- Invalid:
  - overlong constrained fields
- Output: `WhatsAppThreadCandidateRead`

#### `PATCH /api/whatsapp/thread/{thread_id}/candidates/{candidate_id}`
- Input body: `WhatsAppThreadCandidatePatch`
- Fields: same as candidate create, except `status` default omitted
- Valid/invalid: same as candidate create
- Output: `WhatsAppThreadCandidateRead`

#### `POST /api/whatsapp/thread/{thread_id}/send-text`
- Input body: `WhatsAppSendTextIn`
- Fields:
  - `text: string`
  - `reply_to_message_id?: int`
- Valid:
  - non-empty trimmed text
- Invalid:
  - blank text -> `400`
  - missing thread -> `404`
  - thread with no `wa_id` -> `400`
- Output: `WhatsAppSendTextOut`
  - `ok: bool`
  - `thread_id: int`
  - `wa_message_id: string`
  - `text: string`

#### `POST /api/whatsapp/thread/{thread_id}/link`
- Input body: `WhatsAppThreadLinkIn`
- Fields:
  - `lead_id: int`
- Valid:
  - existing thread and existing lead
- Invalid:
  - missing thread -> `404`
  - missing lead -> `404`
- Output: `WhatsAppThreadOut`

#### `POST /api/whatsapp/thread/{thread_id}/unlink`
- Valid:
  - existing thread
- Invalid:
  - missing thread -> `404`
- Output: `WhatsAppThreadOut`

#### `POST /whatsapp/thread/{thread_id}/link-lead`
- Input/output: same as `/api/whatsapp/thread/{thread_id}/link`

#### `POST /whatsapp/thread/{thread_id}/unlink-lead`
- Input/output: same as `/api/whatsapp/thread/{thread_id}/unlink`

## WhatsApp Integration

#### `GET /integrations/whatsapp/webhook`
- Query params:
  - `hub.mode`
  - `hub.challenge`
  - `hub.verify_token`
- Valid:
  - correct verification token
- Invalid:
  - wrong token
- Output:
  - webhook verification response

#### `POST /integrations/whatsapp/webhook`
- Input:
  - raw Meta/WhatsApp webhook payload
- Output:
  - webhook processing response

## UI / HTML Endpoints

#### `GET /kanban`
- Output: `text/html`
- Query params:
  - `q`
  - `estado`
  - `flag`
  - `canal`
  - `marca`
  - `anio`
  - `zone_group`
  - `turno_fecha_from`
  - `turno_fecha_to`
  - `tipo_vehiculo`
  - `modelo`
  - `zone_detail`
  - `estado_revision`

#### `GET /table`
- Output: `text/html`
- Query params:
  - `q`
  - `estado`
  - `flag`
  - `profesional_id`
  - `canal`
  - `marca`
  - `anio`
  - `zone_group`
  - `turno_fecha_from`
  - `turno_fecha_to`
  - `from_date`
  - `to_date`
  - `date_field`
  - `tipo_vehiculo`
  - `modelo`
  - `zone_detail`
  - `estado_revision`
  - `open_filters`

#### `GET /calendar`
- Output: `text/html`
- Query params:
  - `week`
  - `q`
  - `estado`
  - `flag`
  - `canal`
  - `marca`
  - `anio`
  - `zone_group`
  - `turno_fecha_from`
  - `turno_fecha_to`
  - `tipo_vehiculo`
  - `modelo`
  - `zone_detail`
  - `estado_revision`

#### `GET /profesionales`
- Output: `text/html`

#### `GET /agencias`
- Output: `text/html`

#### `GET /integrations/whatsapp/debug`
- Output: `text/html`

#### `GET /ui/agencia_file/{agencia_id}`
- Path params:
  - `agencia_id: int`
- Output:
  - file download response

#### `GET /whatsapp/inbox`
- Output: `text/html`

#### `GET /whatsapp/thread/{thread_id}`
- Path params:
  - `thread_id: string`
- Output: `text/html`

#### `GET /whatsapp/thread/{thread_id}/latest`
- Path params:
  - `thread_id: int`
- Output:
  - latest thread payload

## UI Action Endpoints

These endpoints are form/UI helpers. Most return redirects or small JSON payloads.

#### `POST /ui/lead_create`
- Content type: `application/x-www-form-urlencoded`
- Fields:
  - `nombre`
  - `apellido`
  - `telefono`
  - `tel`
  - `email`
  - `canal`
  - `compro_el_auto`
- Valid:
  - `compro_el_auto` omitted, `SI`, `NO`
- Invalid:
  - unsupported `compro_el_auto` is silently normalized away in UI flow
- Output:
  - redirect to `/kanban`

#### `POST /ui/lead_update`
- Content type: `application/x-www-form-urlencoded`
- Fields:
  - `lead_id`
  - `nombre`
  - `apellido`
  - `telefono`
  - `tel`
  - `email`
  - `canal`
  - `compro_el_auto`
  - `necesita_humano`
  - `estado`
- Valid:
  - `estado` in lead state set
- Invalid:
  - invalid `estado` is ignored in UI flow rather than hard-failing
- Output:
  - redirect to `/kanban#lead-{lead_id}`

#### `POST /ui/lead_toggle_humano`
- Fields:
  - `lead_id`
  - `value`
- Output:
  - redirect to `/kanban`

#### `POST /ui/lead_delete`
- Fields:
  - `lead_id`
- Output:
  - redirect to `/kanban`

#### `POST /ui/lead_flag_set`
- Fields:
  - `lead_id`
  - `flag`
- Valid:
  - `flag in {"PRESUPUESTANDO","PRESUPUESTO_ENVIADO","ACEPTADO","RECOMPRA","PERDIDO"}`
- Invalid:
  - bad `flag` -> `400`
- Output:
  - redirect to `/kanban`

#### `POST /ui/lead_flag_clear`
- Fields:
  - `lead_id`
- Output:
  - redirect to `/kanban`

#### `POST /ui/move`
- Fields:
  - `lead_id`
  - `estado`
- Valid:
  - valid lead state
- Invalid:
  - bad `estado` -> `400`
- Output:
  - redirect to `/kanban`

#### `POST /ui/move_lead`
- Fields:
  - `lead_id`
  - `estado`
  - `new_estado`
  - `payload`
- Valid:
  - target state present and valid
- Invalid:
  - missing `lead_id` or `estado` -> `400`
- Output:
```json
{
  "ok": true,
  "estado": "AGENDADO"
}
```

#### `POST /ui/lead/{lead_id}/move`
- Fields:
  - `estado`
- Valid:
  - valid lead state
- Invalid:
  - bad `estado` -> `400`
- Output:
```json
{
  "ok": true
}
```

#### `POST /ui/human`
- Fields:
  - `lead_id`
  - `necesita_humano`
- Output:
  - redirect to `/kanban`

#### `POST /ui/perdido`
- Fields:
  - `lead_id`
  - `motivo_perdida`
- Output:
  - redirect to `/kanban`

#### `POST /ui/request_delete_lead`
- Fields:
  - `lead_id`
- Output:
```json
{
  "ok": true,
  "token": "string",
  "lead_id": 123,
  "deadline_ts": 1234567890,
  "countdown_seconds": 7
}
```

#### `POST /ui/request_delete_revision`
- Fields:
  - `lead_id`
  - `revision_id`
- Output:
```json
{
  "ok": true,
  "token": "string",
  "lead_id": 123,
  "revision_id": 456,
  "deadline_ts": 1234567890,
  "countdown_seconds": 7
}
```

#### `POST /ui/undo_delete`
- Fields:
  - `token`
- Output:
```json
{
  "ok": true,
  "undone": true
}
```

#### `POST /ui/commit_delete`
- Fields:
  - `token`
- Output:
```json
{
  "ok": true,
  "committed": true
}
```
or an idempotent falsey commit state

#### `POST /ui/revision_create`
- Fields:
  - `lead_id`
- Output:
  - redirect to `/kanban`

#### `POST /ui/revision_latest_update`
- Content type: `application/x-www-form-urlencoded`
- Fields:
  - `lead_id`
  - `tipo_vehiculo`
  - `vendedor_tipo`
  - `tipo_vendedor`
  - `agencia_id`
  - `agencia_nueva_nombre`
  - `marca`
  - `modelo`
  - `anio`
  - `link_compra`
  - `presupuesto_compra`
  - `compro`
  - `resultado_link`
  - `comision`
  - `cobrado`
  - `fecha_cobro`
  - `zone_group`
  - `zone_detail`
  - `direccion_texto`
  - `link_maps`
  - `direccion_estado`
  - `precio_base`
  - `viaticos`
  - `precio_total`
  - `recalcular_presupuesto`
  - `pago`
  - `medio_pago`
  - `turno_fecha`
  - `turno_hora`
  - `cliente_presente`
  - `turno_notas`
  - `estado_revision`
  - `resultado`
  - `motivo_rechazo`
  - `profesional_id`
- Valid:
  - `compro in {"SI","NO","OFRECIDO"}`
  - `cobrado in {"SI","NO"}`
  - parseable date/time/int/bool fields
  - `estado_revision` only if in current UI options
- Invalid:
  - invalid parsed ints/dates may resolve to `None`
  - invalid `estado_revision` is ignored in UI flow
  - overlapping/invalid turno is blocked by current frontend schedule validation before submit
- Output:
  - redirect to `/kanban`

#### `POST /ui/revision_latest_delete`
- Fields:
  - `lead_id`
- Output:
  - redirect to `/kanban`

#### `POST /ui/profesional_create`
- Fields:
  - `nombre`
  - `apellido`
  - `email`
  - `telefono`
  - `cargo`
- Output:
  - redirect to `/profesionales`

#### `POST /ui/vendedor_create`
- Fields:
  - `nombre`
- Output:
  - redirect to `/agencias`

#### `POST /ui/agencia_create`
- Content type: `multipart/form-data`
- Fields:
  - `nombre_agencia`
  - `direccion`
  - `gmaps`
  - `mail`
  - `vendedor_id`
  - `vendedor_nuevo`
  - `telefono`
  - `file`
- Output:
  - redirect to `/agencias`

#### `POST /ui/agencia_update`
- Content type: `multipart/form-data`
- Fields:
  - `agencia_id`
  - `nombre_agencia`
  - `direccion`
  - `gmaps`
  - `mail`
  - `vendedor_id`
  - `vendedor_nuevo`
  - `telefono`
  - `file`
- Output:
  - redirect to `/agencias`

#### `POST /ui/agencia_delete`
- Fields:
  - `agencia_id`
- Output:
  - redirect to `/agencias`

## WhatsApp UI Endpoints

#### `POST /whatsapp/thread/{thread_id}/send`
- Input body: `WhatsAppSendPayload`
- Fields:
  - `text: string`
  - `reply_to_message_id?: int`
- Valid:
  - non-empty text
- Invalid:
  - blank text
- Output:
  - UI send response / redirect payload

#### `PATCH /whatsapp/thread/{thread_id}/display-name`
- Input body:
  - UI display-name payload
- Output:
  - `WhatsAppThreadOut`

#### `DELETE /whatsapp/thread/{thread_id}`
- Output:
  - delete result JSON

#### `POST /whatsapp/thread/{thread_id}/send-media`
- Input:
  - multipart media form
- Output:
  - media send result

#### `POST /whatsapp/thread/{thread_id}/messages/{message_id}/react`
- Input:
  - reaction payload
- Output:
  - reaction result

#### `GET /whatsapp/thread/{thread_id}/latest`
- Output:
  - latest thread payload
