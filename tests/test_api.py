from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from api.main import app, current_user_id
from rumo_core.db import get_session


def test_health_ok():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "up"}


def test_health_db_down():
    def broken():
        raise OperationalError("SELECT 1", {}, Exception("db down"))
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = broken
    try:
        r = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "down"}


def test_current_user_id_default():
    assert current_user_id() == 1
