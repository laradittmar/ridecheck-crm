# Swagger Endpoints

Source: `backend/openapi_dump.json`

Note: this file reflects what is currently present in the generated Swagger dump. If a route exists in code but is missing from the dump, it will not appear here.

## JSON API

### `GET /leads`
- Summary: `List Leads`
- Path params: none
- Query params: none
- Request body: none
- Response `200`: `LeadOut[]`

### `POST /leads`
- Summary: `Create Lead`
- Path params: none
- Query params: none
- Request body `application/json`: `LeadCreate`
- Request fields: `telefono`, `nombre`, `apellido`
- Response `200`: `LeadOut`
- Response `422`: `HTTPValidationError`

### `PATCH /leads/{lead_id}`
- Summary: `Update Lead`
- Path params: `lead_id: integer`
- Query params: none
- Request body `application/json`: `LeadUpdate`
- Request fields: `estado`, `motivo_perdida`, `necesita_humano`, `flag`
- Response `200`: `LeadOut`
- Response `422`: `HTTPValidationError`

### `POST /leads/{lead_id}/revisions`
- Summary: `Create Revision`
- Path params: `lead_id: integer`
- Query params: none
- Request body `application/json`: `RevisionCreate`
- Request fields: `tipo_vehiculo`, `marca`, `modelo`, `anio`, `link_compra`, `presupuesto_compra`, `vendedor_tipo`, `tipo_vendedor`, `agencia_id`, `compro`, `resultado_link`, `comision`, `cobrado`, `fecha_cobro`, `zone_group`, `zone_detail`, `direccion_texto`, `link_maps`, `direccion_estado`, `precio_base`, `viaticos`, `precio_total`, `pago`, `medio_pago`, `turno_fecha`, `turno_hora`, `cliente_presente`, `turno_notas`, `estado_revision`, `resultado`, `motivo_rechazo`
- Response `200`: `RevisionOut`
- Response `422`: `HTTPValidationError`

### `GET /leads/{lead_id}/revisions`
- Summary: `List Revisions`
- Path params: `lead_id: integer`
- Query params: none
- Request body: none
- Response `200`: `RevisionOut[]`
- Response `422`: `HTTPValidationError`

### `PATCH /leads/{lead_id}/revisions/latest`
- Summary: `Update Latest Revision`
- Path params: `lead_id: integer`
- Query params: none
- Request body `application/json`: `RevisionUpdate`
- Request fields: `tipo_vehiculo`, `marca`, `modelo`, `anio`, `link_compra`, `presupuesto_compra`, `vendedor_tipo`, `tipo_vendedor`, `agencia_id`, `compro`, `resultado_link`, `comision`, `cobrado`, `fecha_cobro`, `zone_group`, `zone_detail`, `direccion_texto`, `link_maps`, `direccion_estado`, `precio_base`, `viaticos`, `precio_total`, `pago`, `medio_pago`, `turno_fecha`, `turno_hora`, `cliente_presente`, `turno_notas`, `estado_revision`, `resultado`, `motivo_rechazo`, `recalcular_presupuesto`
- Response `200`: `RevisionOut`
- Response `422`: `HTTPValidationError`

## HTML Views

### `GET /kanban`
- Summary: `Kanban`
- Query params: `q`, `estado`, `flag`, `canal`, `marca`, `anio`, `zone_group`, `turno_fecha_from`, `turno_fecha_to`, `tipo_vehiculo`, `modelo`, `zone_detail`, `estado_revision`
- Response `200`: `text/html`
- Response `422`: `HTTPValidationError`

### `GET /table`
- Summary: `Table View`
- Query params: `q`, `estado`, `flag`, `profesional_id`, `canal`, `marca`, `anio`, `zone_group`, `turno_fecha_from`, `turno_fecha_to`, `from_date`, `to_date`, `date_field`, `tipo_vehiculo`, `modelo`, `zone_detail`, `estado_revision`, `open_filters`
- Response `200`: `text/html`
- Response `422`: `HTTPValidationError`

### `GET /calendar`
- Summary: `Calendar`
- Query params: `week`, `q`, `estado`, `flag`, `canal`, `marca`, `anio`, `zone_group`, `turno_fecha_from`, `turno_fecha_to`, `tipo_vehiculo`, `modelo`, `zone_detail`, `estado_revision`
- Response `200`: `text/html`
- Response `422`: `HTTPValidationError`

### `GET /profesionales`
- Summary: `Profesionales`
- Request body: none
- Response `200`: `text/html`

### `GET /agencias`
- Summary: `Agencias`
- Request body: none
- Response `200`: `text/html`

### `GET /integrations/whatsapp/debug`
- Summary: `Whatsapp Debug`
- Request body: none
- Response `200`: `text/html`

### `GET /ui/agencia_file/{agencia_id}`
- Summary: `Agencia File Download`
- Path params: `agencia_id: integer`
- Request body: none
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `GET /whatsapp/inbox`
- Summary: `Whatsapp Inbox`
- Request body: none
- Response `200`: `text/html`

### `GET /whatsapp/thread/{thread_id}`
- Summary: `Whatsapp Thread`
- Path params: `thread_id: string`
- Request body: none
- Response `200`: `text/html`
- Response `422`: `HTTPValidationError`

## UI Action Endpoints

### `POST /ui/lead_create`
- Summary: `Ui Lead Create`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_lead_create_ui_lead_create_post`
- Request fields: `nombre`, `apellido`, `telefono`, `tel`, `email`, `canal`, `compro_el_auto`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/lead_update`
- Summary: `Ui Lead Update`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_lead_update_ui_lead_update_post`
- Request fields: `lead_id`, `nombre`, `apellido`, `telefono`, `tel`, `email`, `canal`, `compro_el_auto`, `necesita_humano`, `estado`
- Required fields: `lead_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/lead_toggle_humano`
- Summary: `Ui Lead Toggle Humano`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_lead_toggle_humano_ui_lead_toggle_humano_post`
- Request fields: `lead_id`, `value`
- Required fields: `lead_id`, `value`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/lead_delete`
- Summary: `Ui Lead Delete`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_lead_delete_ui_lead_delete_post`
- Request fields: `lead_id`
- Required fields: `lead_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/lead_flag_set`
- Summary: `Ui Lead Flag Set`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_lead_flag_set_ui_lead_flag_set_post`
- Request fields: `lead_id`, `flag`
- Required fields: `lead_id`, `flag`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/lead_flag_clear`
- Summary: `Ui Lead Flag Clear`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_lead_flag_clear_ui_lead_flag_clear_post`
- Request fields: `lead_id`
- Required fields: `lead_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/move`
- Summary: `Ui Move`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_move_ui_move_post`
- Request fields: `lead_id`, `estado`
- Required fields: `lead_id`, `estado`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/move_lead`
- Summary: `Ui Move Lead`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_move_lead_ui_move_lead_post`
- Request fields: `lead_id`, `estado`, `new_estado`, `payload`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/lead/{lead_id}/move`
- Summary: `Ui Lead Move`
- Path params: `lead_id: integer`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_lead_move_ui_lead__lead_id__move_post`
- Request fields: `estado`
- Required fields: `estado`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/human`
- Summary: `Ui Human`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_human_ui_human_post`
- Request fields: `lead_id`, `necesita_humano`
- Required fields: `lead_id`, `necesita_humano`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/perdido`
- Summary: `Ui Perdido`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_perdido_ui_perdido_post`
- Request fields: `lead_id`, `motivo_perdida`
- Required fields: `lead_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/request_delete_lead`
- Summary: `Ui Request Delete Lead`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_request_delete_lead_ui_request_delete_lead_post`
- Request fields: `lead_id`
- Required fields: `lead_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/request_delete_revision`
- Summary: `Ui Request Delete Revision`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_request_delete_revision_ui_request_delete_revision_post`
- Request fields: `lead_id`, `revision_id`
- Required fields: `lead_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/undo_delete`
- Summary: `Ui Undo Delete`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_undo_delete_ui_undo_delete_post`
- Request fields: `token`
- Required fields: `token`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/commit_delete`
- Summary: `Ui Commit Delete`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_commit_delete_ui_commit_delete_post`
- Request fields: `token`
- Required fields: `token`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/revision_create`
- Summary: `Ui Revision Create`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_revision_create_ui_revision_create_post`
- Request fields: `lead_id`
- Required fields: `lead_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/revision_latest_update`
- Summary: `Ui Revision Latest Update`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_revision_latest_update_ui_revision_latest_update_post`
- Request fields: `lead_id`, `tipo_vehiculo`, `vendedor_tipo`, `tipo_vendedor`, `agencia_id`, `agencia_nueva_nombre`, `marca`, `modelo`, `anio`, `link_compra`, `presupuesto_compra`, `compro`, `resultado_link`, `comision`, `cobrado`, `fecha_cobro`, `zone_group`, `zone_detail`, `direccion_texto`, `link_maps`, `direccion_estado`, `precio_base`, `viaticos`, `precio_total`, `recalcular_presupuesto`, `pago`, `medio_pago`, `turno_fecha`, `turno_hora`, `cliente_presente`, `turno_notas`, `estado_revision`, `resultado`, `motivo_rechazo`, `profesional_id`
- Required fields: `lead_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/revision_latest_delete`
- Summary: `Ui Revision Latest Delete`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_revision_latest_delete_ui_revision_latest_delete_post`
- Request fields: `lead_id`
- Required fields: `lead_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/profesional_create`
- Summary: `Ui Profesional Create`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_profesional_create_ui_profesional_create_post`
- Request fields: `nombre`, `apellido`, `email`, `telefono`, `cargo`
- Required fields: `nombre`, `apellido`, `email`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/vendedor_create`
- Summary: `Ui Vendedor Create`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_vendedor_create_ui_vendedor_create_post`
- Request fields: `nombre`
- Required fields: `nombre`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/agencia_create`
- Summary: `Ui Agencia Create`
- Content type: `multipart/form-data`
- Request schema: `Body_ui_agencia_create_ui_agencia_create_post`
- Request fields: `nombre_agencia`, `direccion`, `gmaps`, `mail`, `vendedor_id`, `vendedor_nuevo`, `telefono`, `file`
- Required fields: `nombre_agencia`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/agencia_update`
- Summary: `Ui Agencia Update`
- Content type: `multipart/form-data`
- Request schema: `Body_ui_agencia_update_ui_agencia_update_post`
- Request fields: `agencia_id`, `nombre_agencia`, `direccion`, `gmaps`, `mail`, `vendedor_id`, `vendedor_nuevo`, `telefono`, `file`
- Required fields: `agencia_id`, `nombre_agencia`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /ui/agencia_delete`
- Summary: `Ui Agencia Delete`
- Content type: `application/x-www-form-urlencoded`
- Request schema: `Body_ui_agencia_delete_ui_agencia_delete_post`
- Request fields: `agencia_id`
- Required fields: `agencia_id`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

## WhatsApp

### `POST /whatsapp/thread/{thread_id}/send`
- Summary: `Whatsapp Thread Send`
- Path params: `thread_id: integer`
- Content type: `application/json`
- Request schema: `WhatsAppSendPayload`
- Request fields: `text`, `reply_to_message_id`
- Required fields: `text`
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `GET /integrations/whatsapp/webhook`
- Summary: `Verify Webhook`
- Query params: `hub.mode`, `hub.challenge`, `hub.verify_token`
- Request body: none
- Response `200`: undocumented/unknown schema
- Response `422`: `HTTPValidationError`

### `POST /integrations/whatsapp/webhook`
- Summary: `Inbound Webhook`
- Request body: not documented in Swagger
- Response `200`: undocumented/unknown schema

## Response Schemas

### `LeadOut`
- Fields: `id`, `created_at`, `estado`, `flag`, `telefono`, `nombre`, `apellido`, `necesita_humano`, `motivo_perdida`
- Required: `id`, `created_at`, `estado`, `necesita_humano`

### `RevisionOut`
- Fields: `id`, `lead_id`, `created_at`, `tipo_vehiculo`, `marca`, `modelo`, `anio`, `link_compra`, `presupuesto_compra`, `vendedor_tipo`, `tipo_vendedor`, `agencia_id`, `compro`, `resultado_link`, `comision`, `cobrado`, `fecha_cobro`, `zone_group`, `zone_detail`, `direccion_texto`, `link_maps`, `direccion_estado`, `precio_base`, `viaticos`, `precio_total`, `pago`, `medio_pago`, `turno_fecha`, `turno_hora`, `cliente_presente`, `turno_notas`, `estado_revision`, `resultado`, `motivo_rechazo`
- Required: `id`, `lead_id`, `created_at`, `estado_revision`
