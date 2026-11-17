-- Runs once, when the db volume is first initialized: a separate database for pytest.
-- Existing volumes: tests/conftest.py creates it on the first run.
CREATE DATABASE rumo_test OWNER rumo;
