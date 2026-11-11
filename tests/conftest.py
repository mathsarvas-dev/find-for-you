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
