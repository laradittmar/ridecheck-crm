# Backend Endpoints

Sources:
- Code routers under `backend/app/api`, `backend/app/routes`, and `backend/app/ui`
- Existing generated spec at `backend/openapi_dump.json`

Note: this file is updated from the current codebase, so it includes newer endpoints that may not yet appear in `openapi_dump.json`.

## JSON API

### Leads

#### `POST /leads`
- Output: `LeadOut`
- Input body: `LeadCreate`
- Fields: `telefono`, `nombre`, `apellido`, `email`, `canal`, `compro_el_auto`

#### `GET /leads`
- Output: `LeadOut[]`
- Query params: `telefono` optional
- Input body: none

#### `PATCH /leads/{lead_id}`
- Output: `LeadOut`
- Path params: `lead_id: int`
- Input body: `LeadUpdate`
- Fields: `estado`, `motivo_perdida`, `necesita_humano`, `flag`, `telefono`, `nombre`, `apellido`, `email`, `canal`, `compro_el_auto`

#### `GET /leads/{lead_id}`
- Output: `LeadDetailOut`
- Path params: `lead_id: int`
- Input body: none

#### `GET /leads/{lead_id}/whatsapp`
- Output: `WhatsAppThreadOut`
- Path params: `lead_id: int`
- Input body: none

### Lead Revisions

#### `POST /leads/{lead_id}/revisions`
- Output: `RevisionOut`
- Path params: `lead_id: int`
- Input body: `RevisionCreate`
- Fields: `tipo_vehiculo`, `marca`, `modelo`, `anio`, `link_compra`, `presupuesto_compra`, `vendedor_tipo`, `tipo_vendedor`, `agencia_id`, `compro`, `resultado_link`, `comision`, `cobrado`, `fecha_cobro`, `zone_group`, `zone_detail`, `direccion_texto`, `link_maps`, `direccion_estado`, `precio_base`, `viaticos`, `precio_total`, `pago`, `medio_pago`, `turno_fecha`, `turno_hora`, `cliente_presente`, `turno_notas`, `estado_revision`, `resultado`, `motivo_rechazo`

#### `GET /leads/{lead_id}/revisions`
- Output: `RevisionOut[]`
- Path params: `lead_id: int`
- Input body: none

#### `GET /leads/{lead_id}/revisions/latest`
- Output: `RevisionOut`
- Path params: `lead_id: int`
- Input body: none

#### `PATCH /leads/{lead_id}/revisions/latest`
- Output: `RevisionOut`
- Path params: `lead_id: int`
- Input body: `RevisionUpdate`
- Fields: same as `RevisionCreate` plus `recalcular_presupuesto`

#### `PATCH /revisions/{revision_id}`
- Output: `RevisionOut`
- Path params: `revision_id: int`
- Input body: `RevisionUpdate`
- Fields: same as `RevisionCreate` plus `recalcular_presupuesto`

### Thread Revisions

#### `POST /api/revisions`
- Output: `ThreadRevisionCreateOut`
- Input body: `ThreadRevisionCreateIn`
- Fields: `thread_id`, `candidate_id`
- Example output:
```json
{
  "revision_id": 123
}
```

#### `PATCH /api/revisions/{revision_id}`
- Output: `ThreadRevisionOut`
- Path params: `revision_id: int`
- Input body: `ThreadRevisionPatch`
- Fields: `status`, `buyer_name`, `buyer_phone`, `buyer_email`, `seller_type`, `seller_name`, `address`, `scheduled_date`, `scheduled_time`, `tipo_vehiculo`, `marca`, `modelo`, `anio`, `publication_url`
- Safe patch behavior: `null` values are ignored and do not overwrite existing values

### Pricing

#### `POST /api/pricing/quote`
- Output: `PricingQuoteOut`
- Input body: `PricingQuoteIn`
- Fields: `tipo_vehiculo`, `zone_group`, `zone_detail`
- Example output:
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
- Output: `ScheduleCheckOut`
- Input body: `ScheduleCheckIn`
- Fields: `address`, `preferred_day`, `preferred_time`
- Example output:
```json
{
  "valid": true,
  "suggested_slots": []
}
```

### WhatsApp API

#### `GET /api/whatsapp/threads`
- Output: `WhatsAppThreadOut[]`
- Input body: none

#### `GET /api/whatsapp/thread/{thread_id}`
- Output: `WhatsAppThreadOut`
- Path params: `thread_id: int`
- Input body: none

#### `GET /api/whatsapp/thread/{thread_id}/messages`
- Output: `WhatsAppThreadMessagesOut`
- Path params: `thread_id: int`
- Query params: `limit` default `10`, min `1`, max `20`
- Input body: none

#### `GET /api/whatsapp/thread/{thread_id}/state`
- Output: `WhatsAppThreadStateRead`
- Path params: `thread_id: int`
- Input body: none
- Output fields: `id`, `thread_id`, `last_intent`, `last_stage`, `needs_human`, `current_focus_candidate_id`, `last_processed_inbound_wa_message_id`, `customer_name`, `home_zone_group`, `home_zone_detail`, `created_at`, `updated_at`

#### `PATCH /api/whatsapp/thread/{thread_id}/state`
- Output: `WhatsAppThreadStateRead`
- Path params: `thread_id: int`
- Input body: `WhatsAppThreadStatePatch`
- Fields: `last_intent`, `last_stage`, `needs_human`, `current_focus_candidate_id`, `last_processed_inbound_wa_message_id`, `customer_name`, `home_zone_group`, `home_zone_detail`

#### `GET /api/whatsapp/thread/{thread_id}/candidates`
- Output: `WhatsAppThreadCandidateRead[]`
- Path params: `thread_id: int`
- Input body: none

#### `POST /api/whatsapp/thread/{thread_id}/candidates`
- Output: `WhatsAppThreadCandidateRead`
- Path params: `thread_id: int`
- Input body: `WhatsAppThreadCandidateCreate`
- Fields: `label`, `marca`, `modelo`, `version_text`, `anio`, `tipo_vehiculo`, `zone_group`, `zone_detail`, `direccion_texto`, `source_text`, `status`

#### `PATCH /api/whatsapp/thread/{thread_id}/candidates/{candidate_id}`
- Output: `WhatsAppThreadCandidateRead`
- Path params: `thread_id: int`, `candidate_id: int`
- Input body: `WhatsAppThreadCandidatePatch`
- Fields: `label`, `marca`, `modelo`, `version_text`, `anio`, `tipo_vehiculo`, `zone_group`, `zone_detail`, `direccion_texto`, `source_text`, `status`

#### `POST /api/whatsapp/thread/{thread_id}/send-text`
- Output: `WhatsAppSendTextOut`
- Path params: `thread_id: int`
- Input body: `WhatsAppSendTextIn`
- Fields: `text`, `reply_to_message_id`

#### `POST /api/whatsapp/thread/{thread_id}/link`
- Output: `WhatsAppThreadOut`
- Path params: `thread_id: int`
- Input body: `WhatsAppThreadLinkIn`
- Fields: `lead_id`

#### `POST /api/whatsapp/thread/{thread_id}/unlink`
- Output: `WhatsAppThreadOut`
- Path params: `thread_id: int`
- Input body: none

#### `POST /whatsapp/thread/{thread_id}/link-lead`
- Output: `WhatsAppThreadOut`
- Path params: `thread_id: int`
- Input body: `WhatsAppThreadLinkIn`
- Fields: `lead_id`

#### `POST /whatsapp/thread/{thread_id}/unlink-lead`
- Output: `WhatsAppThreadOut`
- Path params: `thread_id: int`
- Input body: none

## WhatsApp Integration

#### `GET /integrations/whatsapp/webhook`
- Output: webhook verification response
- Query params: `hub.mode`, `hub.challenge`, `hub.verify_token`
- Input body: none

#### `POST /integrations/whatsapp/webhook`
- Output: webhook processing response
- Input body: raw Meta/WhatsApp webhook payload

## UI / HTML Endpoints

#### `GET /kanban`
- Output: `text/html`
- Query params: `q`, `estado`, `flag`, `canal`, `marca`, `anio`, `zone_group`, `turno_fecha_from`, `turno_fecha_to`, `tipo_vehiculo`, `modelo`, `zone_detail`, `estado_revision`

#### `GET /table`
- Output: `text/html`
- Query params: `q`, `estado`, `flag`, `profesional_id`, `canal`, `marca`, `anio`, `zone_group`, `turno_fecha_from`, `turno_fecha_to`, `from_date`, `to_date`, `date_field`, `tipo_vehiculo`, `modelo`, `zone_detail`, `estado_revision`, `open_filters`

#### `GET /calendar`
- Output: `text/html`
- Query params: `week`, `q`, `estado`, `flag`, `canal`, `marca`, `anio`, `zone_group`, `turno_fecha_from`, `turno_fecha_to`, `tipo_vehiculo`, `modelo`, `zone_detail`, `estado_revision`

#### `GET /profesionales`
- Output: `text/html`

#### `GET /agencias`
- Output: `text/html`

#### `GET /integrations/whatsapp/debug`
- Output: `text/html`

#### `GET /ui/agencia_file/{agencia_id}`
- Output: file download response
- Path params: `agencia_id: int`

#### `GET /whatsapp/inbox`
- Output: `text/html`

#### `GET /whatsapp/thread/{thread_id}`
- Output: `text/html`
- Path params: `thread_id: string`

#### `POST /whatsapp/thread/{thread_id}/send`
- Output: UI send response / redirect payload
- Path params: `thread_id: int`
- Input body: `WhatsAppSendPayload`
- Fields: `text`, `reply_to_message_id`

#### `PATCH /whatsapp/thread/{thread_id}/display-name`
- Output: `WhatsAppThreadOut`
- Path params: `thread_id: int`
- Input body: UI display-name payload

#### `DELETE /whatsapp/thread/{thread_id}`
- Output: delete result JSON
- Path params: `thread_id: int`

#### `POST /whatsapp/thread/{thread_id}/send-media`
- Output: media send result
- Path params: `thread_id: int`
- Input body: multipart media form

#### `POST /whatsapp/thread/{thread_id}/messages/{message_id}/react`
- Output: reaction result
- Path params: `thread_id: int`, `message_id: int`
- Input body: reaction payload

#### `GET /whatsapp/thread/{thread_id}/latest`
- Output: latest thread payload
- Path params: `thread_id: int`

## UI Action Endpoints

#### `POST /ui/lead_create`
- Input: `application/x-www-form-urlencoded`
- Fields: `nombre`, `apellido`, `telefono`, `tel`, `email`, `canal`, `compro_el_auto`
- Output: UI JSON response

#### `POST /ui/lead_update`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`, `nombre`, `apellido`, `telefono`, `tel`, `email`, `canal`, `compro_el_auto`, `necesita_humano`, `estado`
- Output: UI JSON response

#### `POST /ui/lead_toggle_humano`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`, `value`
- Output: UI JSON response

#### `POST /ui/lead_delete`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`
- Output: UI JSON response

#### `POST /ui/lead_flag_set`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`, `flag`
- Output: UI JSON response

#### `POST /ui/lead_flag_clear`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`
- Output: UI JSON response

#### `POST /ui/move`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`, `estado`
- Output: UI JSON response

#### `POST /ui/move_lead`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`, `estado`, `new_estado`, `payload`
- Output: UI JSON response

#### `POST /ui/lead/{lead_id}/move`
- Path params: `lead_id: int`
- Input: `application/x-www-form-urlencoded`
- Fields: `estado`
- Output: UI JSON response

#### `POST /ui/human`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`, `necesita_humano`
- Output: UI JSON response

#### `POST /ui/perdido`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`, `motivo_perdida`
- Output: UI JSON response

#### `POST /ui/request_delete_lead`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`
- Output: UI JSON response

#### `POST /ui/request_delete_revision`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`, `revision_id`
- Output: UI JSON response

#### `POST /ui/undo_delete`
- Input: `application/x-www-form-urlencoded`
- Fields: `token`
- Output: UI JSON response

#### `POST /ui/commit_delete`
- Input: `application/x-www-form-urlencoded`
- Fields: `token`
- Output: UI JSON response

#### `POST /ui/revision_create`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`
- Output: UI JSON response

#### `POST /ui/revision_latest_update`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`, `tipo_vehiculo`, `vendedor_tipo`, `tipo_vendedor`, `agencia_id`, `agencia_nueva_nombre`, `marca`, `modelo`, `anio`, `link_compra`, `presupuesto_compra`, `compro`, `resultado_link`, `comision`, `cobrado`, `fecha_cobro`, `zone_group`, `zone_detail`, `direccion_texto`, `link_maps`, `direccion_estado`, `precio_base`, `viaticos`, `precio_total`, `recalcular_presupuesto`, `pago`, `medio_pago`, `turno_fecha`, `turno_hora`, `cliente_presente`, `turno_notas`, `estado_revision`, `resultado`, `motivo_rechazo`, `profesional_id`
- Output: UI JSON response

#### `POST /ui/revision_latest_delete`
- Input: `application/x-www-form-urlencoded`
- Fields: `lead_id`
- Output: UI JSON response

#### `POST /ui/profesional_create`
- Input: `application/x-www-form-urlencoded`
- Fields: `nombre`, `apellido`, `email`, `telefono`, `cargo`
- Output: UI JSON response

#### `POST /ui/vendedor_create`
- Input: `application/x-www-form-urlencoded`
- Fields: `nombre`
- Output: UI JSON response

#### `POST /ui/agencia_create`
- Input: `multipart/form-data`
- Fields: `nombre_agencia`, `direccion`, `gmaps`, `mail`, `vendedor_id`, `vendedor_nuevo`, `telefono`, `file`
- Output: UI JSON response

#### `POST /ui/agencia_update`
- Input: `multipart/form-data`
- Fields: `agencia_id`, `nombre_agencia`, `direccion`, `gmaps`, `mail`, `vendedor_id`, `vendedor_nuevo`, `telefono`, `file`
- Output: UI JSON response

#### `POST /ui/agencia_delete`
- Input: `application/x-www-form-urlencoded`
- Fields: `agencia_id`
- Output: UI JSON response
