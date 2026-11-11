import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from api.main import app, current_user_id
from rumo_core.db import get_session


@pytest.mark.db
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


def test_health_db_down_on_execute():
    class BrokenSession:
        def execute(self, *a, **k):
            raise OperationalError("SELECT 1", {}, Exception("outage"))

    app.dependency_overrides[get_session] = lambda: BrokenSession()
    try:
        r = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "down"}


def test_health_unreachable_db_fails_fast():
    import time

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from rumo_core import db

    url = db.engine.url.set(host="localhost", port=5999)
    eng = create_engine(url, connect_args={"connect_timeout": 2})
    factory = sessionmaker(bind=eng)

    def unreachable():
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = unreachable
    try:
        start = time.monotonic()
        r = TestClient(app).get("/health")
        elapsed = time.monotonic() - start
    finally:
        app.dependency_overrides.clear()
        eng.dispose()
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "down"}
    assert elapsed < 15
