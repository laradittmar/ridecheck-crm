"""pytest configuration for RideCheck CRM test suite.

Responsibilities:
1. JSONB→JSON patch applied at module level, before any test file is imported,
   so that the SQLite in-memory tests (M20, M21.0.1) can create_all without
   raising UnsupportedCompilationError on JSONB columns.
2. Register the `requires_infra` marker.
3. Programmatically mark tests that require live infrastructure or specific
   container file state, without modifying those test files.
4. Exclude test files that fail collection due to missing packages (httpx).
"""
import sys
from pathlib import Path

# ── sys.path: make app importable from repo root ──────────────────────────────
_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# ── JSONB → JSON patch (must run before any app.models import) ────────────────
import sqlalchemy
import sqlalchemy.dialects.postgresql as _pg_dialect
import sqlalchemy.dialects.postgresql.json as _pg_json

# Unconditional: always alias JSONB → JSON so SQLite can compile the schema.
_pg_dialect.JSONB = sqlalchemy.JSON      # type: ignore[attr-defined]
_pg_json.JSONB = sqlalchemy.JSON         # type: ignore[attr-defined]

# ── Exclude files that fail at import time (missing httpx package) ────────────
collect_ignore = [
    "test_ai_regression.py",
    "test_new_api_endpoints.py",
    "test_whatsapp_thread_state_api.py",
]

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_infra: test requires live infrastructure "
        "(PostgreSQL, httpx/TestClient, specific container file layout, etc.)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Programmatically apply requires_infra to tests that need container file state."""
    _infra_modules = {
        # Require /app/backend/app/routes/whatsapp.py in container
        "test_m19_r1_3d_closed_beta",
        # Require /app/tests/reset_closed_beta_scenario.py in container
        "test_m19_r1_3e_reset_rehearsal",
        # Require live PostgreSQL (psycopg3 + real DB URL)
        "test_m19_r1_2_pg_integration",
        "test_m19_r1_2_pg_concurrency",
    }
    marker = pytest.mark.requires_infra
    for item in items:
        module_name = item.module.__name__ if hasattr(item, "module") else ""
        if any(m in module_name for m in _infra_modules):
            item.add_marker(marker)
