import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rumo_core.config import Settings

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", ["api.profile", "api.main", "api.deps"])
def test_module_imports_in_fresh_interpreter(module):
    # api.profile used to import api.main (circular); each must import on its own.
    r = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


def test_current_user_id_is_reexported_from_main():
    from api import deps, main

    assert main.current_user_id is deps.current_user_id


def test_cors_origins_default():
    s = Settings(database_url="x", _env_file=None)
    assert s.cors_origins == "http://localhost:3000"


def test_preflight_allows_web_origin():
    from api.main import app

    r = TestClient(app).options(
        "/profile/imports",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "POST" in r.headers["access-control-allow-methods"]


def test_preflight_rejects_other_origin():
    from api.main import app

    r = TestClient(app).options(
        "/profile/imports",
        headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in r.headers
