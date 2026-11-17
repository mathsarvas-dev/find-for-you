import os

# Must run before any rumo_core import: db.py builds the engine at import time.
# TEST_DATABASE_URL (optional) points the suite at its own database, so tests never
# share the development database with the worker. Unset = the previous behavior.
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
_DEV_DATABASE_URL = os.environ.get("DATABASE_URL", "")
if _TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
else:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://rumo:rumo@localhost:5432/rumo"
    )

import subprocess
import sys

import pytest


def _ensure_test_database(url: str) -> None:
    """Create the test database if it is missing (via the server's 'postgres' db)."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    name = parsed.database or ""
    if not name or name == "rumo" or url == _DEV_DATABASE_URL:
        raise RuntimeError(
            "TEST_DATABASE_URL must name a database other than the development 'rumo'"
        )
    admin = create_engine(
        parsed.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
            ).scalar()
            if not exists:
                quoted = '"' + name.replace('"', '""') + '"'
                conn.execute(text(f"CREATE DATABASE {quoted}"))
    finally:
        admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _migrated_db(request):
    if not any(i.get_closest_marker("db") for i in request.session.items):
        yield
        return
    if _TEST_DATABASE_URL:
        _ensure_test_database(_TEST_DATABASE_URL)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], check=True
    )
    yield
