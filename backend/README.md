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
## WhatsApp Cloud API (config scaffold)

Set these variables when you are ready to configure WhatsApp integration:

- `WHATSAPP_TOKEN` (default: empty)
- `WHATSAPP_VERIFY_TOKEN` (default: empty)
- `WHATSAPP_PHONE_NUMBER_ID` (default: empty)
- `WHATSAPP_APP_SECRET` (optional, default: empty)

Startup validation behavior:

- If all required WhatsApp vars are empty, integration is treated as disabled.
- If one required var is set but others are missing, the app logs a clear error and continues running.

