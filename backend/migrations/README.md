# Alembic migrations

Create a migration (empty):
`docker compose exec backend alembic revision -m "init"`

Create a migration (autogenerate):
`docker compose exec backend alembic revision --autogenerate -m "describe_change"`

Apply migrations:
`docker compose exec backend alembic upgrade head`

After changing code, always run: `docker compose exec backend alembic upgrade head`

Note: `20260207_repair_add_profesional_id_to_revisions.py` is a repair migration to fix schema drift where
`revisions.profesional_id` was missing in prod-like databases.
