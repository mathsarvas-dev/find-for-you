"""Conditional ATS connectors (Recruitee, Personio, Teamtailor, BambooHR, Pinpoint).

Each one reads the real fixture recorded in Task 2-7 (see tests/fixtures/VERIFICATION.md).
"""

import json
from pathlib import Path

import httpx
import pytest

from rumo_core.sources.base import SourceBadResponse, SourceNotFound
from rumo_core.sources.bamboohr import BambooHRSource
from rumo_core.sources.http import SafeClient
from rumo_core.sources.normalize import normalize_job
from rumo_core.sources.personio import PersonioSource
from rumo_core.sources.pinpoint import PinpointSource
from rumo_core.sources.recruitee import RecruiteeSource
from rumo_core.sources.teamtailor import TeamtailorSource

FIX = Path(__file__).resolve().parents[1] / "fixtures"
PUBLIC = "93.184.216.34"


async def _resolve(host, port):
    return [PUBLIC]


async def _no_sleep(seconds):
    return None


def _client(handler):
    return SafeClient(httpx.MockTransport(handler), resolve=_resolve, sleep=_no_sleep)


def _body(content: bytes, ctype: str, status: int = 200):
    return lambda r: httpx.Response(status, headers={"content-type": ctype}, content=content)


def _json(payload, status=200):
    return _body(json.dumps(payload).encode(), "application/json", status)


# --- Recruitee ---------------------------------------------------------------

REC = json.loads((FIX / "recruitee" / "offers.json").read_text(encoding="utf-8"))["offers"]


def _rec_item(eid, **kw):
    base = {"id": eid, "title": f"Vaga {eid}", "careers_url": f"https://acme.recruitee.com/o/{eid}",
            "location": "Vienna, Austria", "country_code": "AT", "remote": False, "hybrid": True,
            "on_site": False, "description": "<p>Texto</p>", "requirements": "<ul><li>x</li></ul>",
            "published_at": "2026-10-15 13:29:32 UTC"}
    base.update(kw)
    return base


async def test_recruitee_real_fixture():
    jobs = await RecruiteeSource(_client(_json({"offers": REC})), max_bytes=10**6).fetch_jobs("techpartnerships")
    assert [j.external_id for j in jobs] == [str(o["id"]) for o in REC]
    assert all(j.url.startswith("https://") for j in jobs)
    assert jobs[0].country_hint == "AT" and jobs[0].salary_hint is not None
    assert jobs[0].salary_hint.period == "year" and jobs[0].salary_hint.currency == "EUR"
    assert jobs[0].posted_at is not None and jobs[0].posted_at.tzinfo is not None


async def test_recruitee_missing_duplicates_and_shape():
    payload = {"offers": [_rec_item(1), {"id": 2}, "x", _rec_item(1, title="segunda")]}
    jobs = await RecruiteeSource(_client(_json(payload)), max_bytes=10**6).fetch_jobs("acme")
    assert [(j.external_id, j.title) for j in jobs] == [("1", "segunda")]
    with pytest.raises(SourceBadResponse):
        await RecruiteeSource(_client(_json([1])), max_bytes=10**6).fetch_jobs("acme")
    with pytest.raises(SourceNotFound):
        await RecruiteeSource(_client(_json({}, 404)), max_bytes=10**6).fetch_jobs("acme")


async def test_recruitee_hostile_html_is_data():
    payload = {"offers": [_rec_item(1, description="<script>x()</script><img onerror=y()>")]}
    jobs = await RecruiteeSource(_client(_json(payload)), max_bytes=10**6).fetch_jobs("acme")
    text = normalize_job(jobs[0]).description
    assert "<script" not in text and "<img" not in text


def test_recruitee_identifier():
    src = RecruiteeSource(None, max_bytes=1)
    assert src.normalize_identifier("https://Acme.recruitee.com/o/x") == "acme"
    with pytest.raises(ValueError):
        src.normalize_identifier("https://evil.test/acme")


# --- Personio ----------------------------------------------------------------

PERS = (FIX / "personio" / "positions.xml").read_bytes()


async def test_personio_real_fixture():
    jobs = await PersonioSource(_client(_body(PERS, "text/xml")), max_bytes=10**6).fetch_jobs("dci")
    assert len(jobs) >= 1
    assert all(j.url == f"https://dci.jobs.personio.de/job/{j.external_id}" for j in jobs)
    assert jobs[0].description_html


async def test_personio_rejects_doctype_entities():
    bad = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><workzag-jobs/>'
    with pytest.raises(SourceBadResponse):
        await PersonioSource(_client(_body(bad, "text/xml")), max_bytes=10**6).fetch_jobs("dci")


async def test_personio_missing_and_bad_shape():
    xml = (b'<?xml version="1.0"?><workzag-jobs><position><id>1</id><name>A</name></position>'
           b'<position><id>2</id></position></workzag-jobs>')
    jobs = await PersonioSource(_client(_body(xml, "text/xml")), max_bytes=10**6).fetch_jobs("acme")
    assert [j.external_id for j in jobs] == ["1"]
    with pytest.raises(SourceBadResponse):
        await PersonioSource(_client(_body(b"<other/>", "text/xml")), max_bytes=10**6).fetch_jobs("acme")


# --- Teamtailor --------------------------------------------------------------

RSS = (FIX / "teamtailor" / "jobs.rss").read_bytes()


async def test_teamtailor_real_fixture():
    jobs = await TeamtailorSource(_client(_body(RSS, "application/rss+xml")), max_bytes=10**6).fetch_jobs("kompan")
    assert len(jobs) >= 1
    assert all(j.url.startswith("https://kompan.teamtailor.com/jobs/") for j in jobs)
    assert jobs[0].posted_at is not None and jobs[0].location_text


async def test_teamtailor_rejects_doctype_and_bad_shape():
    bad = b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY e "x">]><rss/>'
    with pytest.raises(SourceBadResponse):
        await TeamtailorSource(_client(_body(bad, "application/rss+xml")), max_bytes=10**6).fetch_jobs("acme")
    with pytest.raises(SourceBadResponse):
        await TeamtailorSource(_client(_body(b"<html/>", "application/rss+xml")), max_bytes=10**6).fetch_jobs("acme")


async def test_teamtailor_missing_fields_and_hostile_html():
    xml = (b'<?xml version="1.0"?><rss><channel>'
           b"<item><title>T</title><link>https://acme.teamtailor.com/jobs/1</link>"
           b"<description>&lt;script&gt;x()&lt;/script&gt;</description><guid>g1</guid></item>"
           b"<item><title>sem link</title></item></channel></rss>")
    jobs = await TeamtailorSource(_client(_body(xml, "application/rss+xml")), max_bytes=10**6).fetch_jobs("acme")
    assert [j.external_id for j in jobs] == ["g1"]
    assert "<script" not in normalize_job(jobs[0]).description


def test_teamtailor_identifier():
    assert TeamtailorSource(None, max_bytes=1).normalize_identifier("https://kompan.teamtailor.com/jobs") == "kompan"


# --- BambooHR ----------------------------------------------------------------

BAMBOO = json.loads((FIX / "bamboohr" / "list.json").read_text(encoding="utf-8"))


async def test_bamboohr_real_fixture():
    jobs = await BambooHRSource(_client(_json(BAMBOO)), max_bytes=10**6).fetch_jobs("semble")
    assert len(jobs) == len(BAMBOO["result"])
    assert all(j.url == f"https://semble.bamboohr.com/careers/{j.external_id}" for j in jobs)
    assert all(j.description_html == "" for j in jobs)


async def test_bamboohr_missing_and_shape():
    payload = {"meta": {}, "result": [{"id": "1", "jobOpeningName": "A"}, {"id": "2"}, 3]}
    jobs = await BambooHRSource(_client(_json(payload)), max_bytes=10**6).fetch_jobs("acme")
    assert [j.external_id for j in jobs] == ["1"]
    with pytest.raises(SourceBadResponse):
        await BambooHRSource(_client(_json([])), max_bytes=10**6).fetch_jobs("acme")


# --- Pinpoint ----------------------------------------------------------------

PIN = json.loads((FIX / "pinpoint" / "postings.json").read_text(encoding="utf-8"))


async def test_pinpoint_real_fixture():
    jobs = await PinpointSource(_client(_json(PIN)), max_bytes=10**6).fetch_jobs("carto")
    assert [j.external_id for j in jobs] == [str(p["id"]) for p in PIN["data"]]
    assert jobs[0].url.startswith("https://carto.pinpointhq.com/")
    assert jobs[0].work_model_hint == "remote"
    assert jobs[0].description_html


async def test_pinpoint_missing_shape_and_hostile():
    payload = {"data": [{"id": 1, "title": "A", "url": "https://x.pinpointhq.com/1",
                         "description": "<script>x()</script>"}, {"id": 2}]}
    jobs = await PinpointSource(_client(_json(payload)), max_bytes=10**6).fetch_jobs("acme")
    assert [j.external_id for j in jobs] == ["1"]
    assert "<script" not in normalize_job(jobs[0]).description
    with pytest.raises(SourceBadResponse):
        await PinpointSource(_client(_json({"x": 1})), max_bytes=10**6).fetch_jobs("acme")
    with pytest.raises(SourceNotFound):
        await PinpointSource(_client(_json({}, 404)), max_bytes=10**6).fetch_jobs("acme")
