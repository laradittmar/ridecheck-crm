# Ridecheck CRM

## Login

The CRM now uses cookie-based login for UI routes.

Set these environment variables:

- `ADMIN_EMAIL` (default: `admin@ridecheck.local`)
- `ADMIN_PASSWORD` (default: `admin123`)
- `AUTH_SECRET_KEY` (recommended in production)

UI access is protected for:

- `/kanban`
- `/table`
- `/calendar`
- `/profesionales`
- `/agencias`
- `/ui/*`

Use `/login` to sign in and `Log Out` in the sidebar to close the session.

## Migrations

A new migration file was added for agencias/vendedores and new revision fields:

- `backend/migrations/versions/20260216_add_agencias_and_revision_fields.py`

Run migrations manually in your environment (not auto-run by Codex):

```bash
docker compose exec backend alembic upgrade head
```

## Restore Viaticos Zones

If the database is reset, the `viaticos_zones` table is recreated by migrations but its data is not automatically re-imported.

This project includes a seed script that rebuilds it from a committed CSV snapshot, and it can also read the original workbook when needed:

```powershell
cd backend
.venv\Scripts\python.exe -m app.scripts.seed_viaticos_zones
```

If you correct the Excel workbook, refresh the committed CSV snapshot first:

```powershell
cd backend
.venv\Scripts\python.exe -m app.scripts.sync_viaticos_csv --source "..\Pricing - tabla de viaticos\Zonas - viaticos Actualizado xls.xlsx"
```

Or, to seed directly from the workbook:

```powershell
cd backend
.venv\Scripts\python.exe -m app.scripts.seed_viaticos_zones --source "..\Pricing - tabla de viaticos\Zonas - viaticos Actualizado xls.xlsx"
```

The workbook is expected to contain these columns:

- `Zona_Grupo`
- `Zona_detalle`
- `Precio Viaticos`

## WhatsApp Cloud API (config scaffold)

Set these variables when you are ready to configure WhatsApp integration:

- `WHATSAPP_TOKEN` (default: empty)
- `WHATSAPP_VERIFY_TOKEN` (default: empty)
- `WHATSAPP_PHONE_NUMBER_ID` (default: empty)
- `WHATSAPP_APP_SECRET` (optional, default: empty)

Startup validation behavior:

- If all required WhatsApp vars are empty, integration is treated as disabled.
- If one required var is set but others are missing, the app logs a clear error and continues running.

