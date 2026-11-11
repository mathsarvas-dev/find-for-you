import os

# Must run before any rumo_core import: db.py builds the engine at import time.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://rumo:rumo@localhost:5432/rumo"
)

import subprocess
import sys

import pytest


@pytest.fixture(scope="session", autouse=True)
def _migrated_db(request):
    if not any(i.get_closest_marker("db") for i in request.session.items):
        yield
        return
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], check=True
    )
    yield
