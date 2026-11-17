from datetime import datetime, timedelta, timezone

import pytest

from rumo_core.db import SessionLocal
from rumo_core.llm.quota import (
    QuotaStore,
    parse_duration,
    parse_quota,
    quota_exhausted,
)
from rumo_core.models import LlmModelStatus

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("7.66s", 7.66),
        ("2m59.56s", 179.56),
        ("1h2m3s", 3723.0),
        ("250ms", 0.25),
        ("0s", 0.0),
        ("12", 12.0),
        ("", None),
        ("abc", None),
        (None, None),
    ],
)
def test_parse_duration(raw, expected):
    got = parse_duration(raw)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_parse_quota_reads_groq_headers():
    snap = parse_quota(
        {
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "999",
            "x-ratelimit-reset-requests": "1m26.4s",
            "x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": "7500",
            "x-ratelimit-reset-tokens": "3.75s",
        },
        NOW,
    )
    assert snap.limit_requests == 1000 and snap.remaining_requests == 999
    assert snap.limit_tokens == 8000 and snap.remaining_tokens == 7500
    assert snap.reset_requests_at == NOW + timedelta(seconds=86.4)
    assert snap.reset_tokens_at == NOW + timedelta(seconds=3.75)


def test_parse_quota_none_without_headers_and_ignores_garbage():
    assert parse_quota({}, NOW) is None
    snap = parse_quota({"x-ratelimit-remaining-tokens": "oops", "x-ratelimit-limit-tokens": "10"}, NOW)
    assert snap.remaining_tokens is None and snap.limit_tokens == 10


def test_quota_exhausted_only_until_reset():
    snap = parse_quota(
        {"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "30s"}, NOW
    )
    assert quota_exhausted(snap, NOW) is True
    assert quota_exhausted(snap, NOW + timedelta(seconds=31)) is False
    ok = parse_quota({"x-ratelimit-remaining-tokens": "5", "x-ratelimit-reset-tokens": "30s"}, NOW)
    assert quota_exhausted(ok, NOW) is False


@pytest.fixture
def store():
    name = "test/model-quota"
    with SessionLocal() as s:
        s.query(LlmModelStatus).filter(LlmModelStatus.model == name).delete()
        s.commit()
    yield QuotaStore(SessionLocal), name
    with SessionLocal() as s:
        s.query(LlmModelStatus).filter(LlmModelStatus.model == name).delete()
        s.commit()


def _row(name):
    with SessionLocal() as s:
        return s.get(LlmModelStatus, name)


@pytest.mark.db
def test_store_records_snapshot_and_marks_existing(store):
    st, name = store
    snap = parse_quota({"x-ratelimit-remaining-tokens": "70", "x-ratelimit-limit-tokens": "100"}, NOW)
    st.record(name, snap, now=NOW)
    row = _row(name)
    assert row.exists is True and row.missing_since is None
    assert (row.remaining_tokens, row.limit_tokens, row.observed_at) == (70, 100, NOW)


@pytest.mark.db
def test_store_missing_then_recovers(store):
    st, name = store
    st.mark_missing(name, now=NOW)
    row = _row(name)
    assert row.exists is False and row.missing_since == NOW
    st.mark_missing(name, now=NOW + timedelta(hours=1))
    assert _row(name).missing_since == NOW  # keeps the first sighting
    st.record(name, None, now=NOW + timedelta(hours=2))
    row = _row(name)
    assert row.exists is True and row.missing_since is None


@pytest.mark.db
def test_store_next_try_and_fallback_marks(store):
    st, name = store
    st.set_next_try(name, NOW + timedelta(minutes=5), now=NOW)
    assert _row(name).next_try_at == NOW + timedelta(minutes=5)
    st.record(name, None, now=NOW + timedelta(minutes=6))  # a success clears the wait
    assert _row(name).next_try_at is None
    st.mark_fallback_used(name, now=NOW)
    assert _row(name).fallback_used_at == NOW


def test_store_swallows_database_errors():
    def boom():
        raise RuntimeError("db down")

    st = QuotaStore(boom)
    st.mark_missing("x")  # never breaks the LLM call
    st.record("x", None)
    st.set_next_try("x", NOW)
