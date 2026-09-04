"""SEC-PRELAUNCH-SOURCE-HARDENING — one place that builds a PostgreSQL DSN for tests.

The smoke and isolation tests used to carry a hardcoded fallback DSN with the real
database password embedded in it. That is the same defect class as the admin-password
fallback in ``app/auth.py``: a working credential readable in the source tree.

The password now comes from the environment and has **no default**. When it is absent the
DSN still has the right shape, so a test that needs PostgreSQL fails to connect and skips
or errors visibly — it never silently authenticates with a value from the repository.

Nothing here changes the credential itself; it only stops the source from carrying it.
"""
from __future__ import annotations

import os

PASSWORD_ENV = "POSTGRES_PASSWORD"
DEFAULT_USER = "crm"


def pg_password() -> str:
    """The password supplied by the environment. Empty when unset — never a literal."""
    return (os.environ.get(PASSWORD_ENV) or "").strip()


def pg_dsn(database: str, host: str = "postgres", *, user: str = DEFAULT_USER,
           port: int = 5432, driver: str = "postgresql+psycopg") -> str:
    """Build a DSN. Host, port, user and database are structure; only the password is secret."""
    return f"{driver}://{user}:{pg_password()}@{host}:{port}/{database}"
