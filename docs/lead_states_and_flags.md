# Lead — Estados, Flags y Endpoints

## Campos de estado y flag

| Campo | Tipo | Default | Valores posibles |
|---|---|---|---|
| `estado` | String(40) | `"CONSULTA_NUEVA"` | `CONSULTA_NUEVA`, `COORDINAR_DISPONIBILIDAD`, `AGENDADO`, `REVISION_COMPLETA`, `ATENCION_HUMANA` |
| `flag` | String(40) | `NULL` | `PRESUPUESTANDO`, `PRESUPUESTO_ENVIADO`, `ACEPTADO`, `RECOMPRA`, `PERDIDO` |
| `motivo_perdida` | String(30) | `NULL` | `PRECIO`, `DISPONIBILIDAD`, `OTRO` *(solo válido si `flag = "PERDIDO"`)*|
| `necesita_humano` | Boolean | `False` | `true` / `false` |

> **Regla de limpieza:** cuando `flag` cambia a cualquier valor distinto de `"PERDIDO"`, `motivo_perdida` se pone automáticamente en `NULL`.

---

## Flujo de `estado`

```
CONSULTA_NUEVA
    ↓
COORDINAR_DISPONIBILIDAD
    ↓
AGENDADO
    ↓
REVISION_COMPLETA   ← al llegar aquí por primera vez se crea un registro FeedbackPostRevision
    ↕
ATENCION_HUMANA     ← puede setearse desde cualquier estado
```

`estado` y `flag` son **independientes entre sí** — se pueden combinar libremente.

---

## Endpoints que modifican `estado`

| Método | Path | Campo modificado | Valor |
|---|---|---|---|
| `POST` | `/leads` | `estado` | `"CONSULTA_NUEVA"` (creación) |
| `PATCH` | `/leads/{lead_id}` | `estado` | cualquier valor válido |
| `POST` | `/ui/move` | `estado` | valor enviado en el form |
| `POST` | `/ui/move_lead` | `estado` | valor en `new_estado` o `estado` del form |
| `POST` | `/ui/lead/{lead_id}/move` | `estado` | valor enviado en el form |
| `POST` | `/ui/lead_update` | `estado` | valor enviado en el form |
| `POST` | `/ui/lead_create` | `estado` | `"CONSULTA_NUEVA"` (creación) |

---

## Endpoints que modifican `flag`

| Método | Path | Campo modificado | Valor |
|---|---|---|---|
| `PATCH` | `/leads/{lead_id}` | `flag` | cualquier valor válido |
| `POST` | `/ui/lead_flag_set` | `flag` | valor enviado en el form |
| `POST` | `/ui/lead_flag_clear` | `flag`, `motivo_perdida` | `NULL`, `NULL` |
| `POST` | `/ui/perdido` | `flag`, `motivo_perdida` | `"PERDIDO"`, valor del form |

---

## Endpoints que modifican `motivo_perdida`

| Método | Path | Campo modificado | Valor |
|---|---|---|---|
| `PATCH` | `/leads/{lead_id}` | `motivo_perdida` | valor válido *(solo si `flag = "PERDIDO"`)*|
| `POST` | `/ui/perdido` | `motivo_perdida` | valor del form |
| `POST` | `/ui/lead_flag_clear` | `motivo_perdida` | `NULL` |
| `POST` | `/ui/lead_flag_set` | `motivo_perdida` | `NULL` *(si el nuevo flag ≠ `"PERDIDO"`)*|

---

## Endpoints que modifican `necesita_humano`

| Método | Path | Campo modificado | Valor |
|---|---|---|---|
| `POST` | `/leads` | `necesita_humano` | `False` (creación) |
| `PATCH` | `/leads/{lead_id}` | `necesita_humano` | `true` / `false` |
| `POST` | `/ui/human` | `necesita_humano` | booleano del form |
| `POST` | `/ui/lead_toggle_humano` | `necesita_humano` | `1` → `true`, cualquier otro → `false` |
| `POST` | `/ui/lead_update` | `necesita_humano` | booleano del form |
| `POST` | `/ui/lead_create` | `necesita_humano` | `False` (creación) |

---

## Campos relacionados (no son estados pero participan en la lógica)

| Campo | Tipo | Valores posibles |
|---|---|---|
| `compro_el_auto` | String(10) | `"SI"`, `"NO"` |
| `canal` | String(50) | `IG_DM`, `IG_WHATSAPP`, `FB_DM`, `FB_WHATSAPP`, `WEBSITE`, `GOOGLE`, `GMAPS`, `OTROS` |
