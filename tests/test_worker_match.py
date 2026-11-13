import asyncio
import logging
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, update

from rumo_core.db import SessionLocal
from rumo_core.llm.base import LLMError, LLMRateLimited
from rumo_core.matching.ai_score import MatchDraft
from rumo_core.models import Company, InterviewSession, Job, Match, Profile, User
from rumo_core.profile.schema import Basics, CanonicalProfile, Experience, Skill
from worker import match_jobs as mj
from worker.match_jobs import score_pending_matches

SECRET = "SECRET-POSTING-TEXT-XYZ"


def _profile_data() -> dict:
    return CanonicalProfile(
        basics=Basics(name="Ana Souza", headline="Engenheira de Dados"),
        summary="Engenheira de dados com foco em pipelines.",
        experiences=[Experience(
            company="Acme", title="Engenheira de Dados", start="2020-01",
            description="Pipelines em Python e Airflow.",
        )],
        skills=[Skill(name="Python"), Skill(name="SQL")],
    ).model_dump(mode="json")


def good_draft(score=78) -> MatchDraft:
    return MatchDraft(
        score=score,
        rationale="O perfil tem experiência sólida com Python e SQL, como pede a vaga Data Engineer.",
        strengths=["Pipelines em Python e Airflow na Acme"],
        gaps=["Não há menção a liderança de equipe"],
    )


class FakeLLM:
    def __init__(self, *results, exc=None, hook=None, delay=0.0):
        self.results = list(results)
        self.exc = exc
        self.hook = hook
        self.delay = delay
        self.calls: list[list[dict]] = []

    async def complete_json(self, messages, schema):
        self.calls.append(messages)
        if self.hook:
            self.hook()
        await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        if self.results:
            return self.results.pop(0)
        return good_draft()


@pytest.fixture(autouse=True)
def _state():
    mj._reset_state()
    yield
    mj._reset_state()


@pytest.fixture
def make_user():
    created: list[int] = []

    def new_user():
        with SessionLocal() as s:
            u = User(email=f"match-jobs-{uuid.uuid4().hex}@example.test")
            s.add(u)
            s.commit()
            created.append(u.id)
            s.add(Company(user_id=u.id, source="fake", identifier=f"co{u.id}", name="Globex"))
            s.add(Profile(user_id=u.id, version=1, data=_profile_data(), content_hash="p1"))
            s.commit()
            return u.id

    yield new_user
    with SessionLocal() as s:
        for uid in created:
            s.execute(delete(InterviewSession).where(InterviewSession.user_id == uid))
            s.execute(delete(Match).where(Match.user_id == uid))
            s.execute(delete(Profile).where(Profile.user_id == uid))
            s.execute(delete(Job).where(Job.user_id == uid))
            s.execute(delete(Company).where(Company.user_id == uid))
            s.execute(delete(User).where(User.id == uid))
        s.commit()


@pytest.fixture
def uid(make_user):
    return make_user()


def _pending(uid, i=0, vec=0.5, version=1, **job_kw):
    """Open job + pendente match row; returns the match id."""
    with SessionLocal() as s:
        c = s.execute(select(Company).where(Company.user_id == uid)).scalars().one()
        fields = dict(
            user_id=uid, company_id=c.id, external_id=f"e{i}",
            url=f"https://example.test/{i}", title="Data Engineer",
            description="Buscamos pessoa engenheira de dados com Python e SQL.",
            raw={}, content_hash=f"h{i}", fingerprint=f"f{i}", status="open",
        )
        fields.update(job_kw)
        j = Job(**fields)
        s.add(j)
        s.flush()
        m = Match(user_id=uid, job_id=j.id, profile_version=version, vec_score=vec,
                  content_hash=j.content_hash, state="pendente")
        s.add(m)
        s.commit()
        return m.id


def _get(match_id) -> Match:
    with SessionLocal() as s:
        m = s.get(Match, match_id)
        s.expunge(m)
        return m


async def _run(uid, llm, batch=5, max_attempts=3, **kw):
    return await score_pending_matches(
        SessionLocal, llm, batch=batch, max_attempts=max_attempts, user_id=uid, **kw)


@pytest.mark.db
async def test_scores_pending_to_ok(uid):
    mid = _pending(uid)
    llm = FakeLLM(good_draft(81))
    assert await _run(uid, llm) == 1
    m = _get(mid)
    assert m.state == "ok" and m.ai_score == 81
    assert m.rationale.startswith("O perfil")
    assert m.strengths == ["Pipelines em Python e Airflow na Acme"]
    assert m.gaps == ["Não há menção a liderança de equipe"]
    assert m.scored_at is not None and m.content_hash == "h0" and m.error is None
    # prompt built from the job and the profile of the row's version
    prompt = llm.calls[0][1]["content"]
    assert "Data Engineer" in prompt and "Globex" in prompt and "Airflow" in prompt
    assert await _run(uid, llm) == 0  # nothing pending anymore


@pytest.mark.db
async def test_invalid_draft_counts_attempt_then_error_at_max(uid, caplog):
    mid = _pending(uid, description=SECRET + " Python")
    bad = MatchDraft(score=50, rationale="Domina Kubernetes e Terraform.", strengths=[], gaps=[])
    llm = FakeLLM(bad, bad, bad, bad)
    with caplog.at_level(logging.DEBUG):
        await _run(uid, llm, max_attempts=3)
        m = _get(mid)
        assert m.state == "pendente" and m.attempts == 1 and m.ai_score is None
        await _run(uid, llm, max_attempts=3)
        assert _get(mid).attempts == 2
        await _run(uid, llm, max_attempts=3)
        m = _get(mid)
        assert m.state == "erro" and m.attempts == 3 and m.error == "MatchInvalid"
        assert await _run(uid, llm, max_attempts=3) == 0
    assert len(llm.calls) == 3
    assert SECRET not in caplog.text and "Kubernetes" not in caplog.text


@pytest.mark.db
async def test_llm_error_and_unexpected_count_attempt(uid, caplog):
    a = _pending(uid, 0)
    with caplog.at_level(logging.DEBUG):
        await _run(uid, FakeLLM(exc=LLMError(SECRET)), max_attempts=1)
    m = _get(a)
    assert m.state == "erro" and m.error == "LLMError" and m.attempts == 1
    b = _pending(uid, 1)
    with caplog.at_level(logging.DEBUG):
        await _run(uid, FakeLLM(exc=RuntimeError(SECRET)), max_attempts=3)
    m = _get(b)
    assert m.state == "pendente" and m.attempts == 1
    assert SECRET not in caplog.text


@pytest.mark.db
async def test_rate_limit_pauses_without_attempt(uid, caplog):
    a, b = _pending(uid, 0, vec=0.9), _pending(uid, 1, vec=0.8)
    now = [100.0]
    llm = FakeLLM(exc=LLMRateLimited("slow"))
    with caplog.at_level(logging.WARNING, logger="worker.match_jobs"):
        assert await _run(uid, llm, clock=lambda: now[0]) == 0
        assert len(llm.calls) == 1  # batch stopped
        assert await _run(uid, llm, clock=lambda: now[0] + 30) == 0
        assert len(llm.calls) == 1  # still paused
    assert len([r for r in caplog.records if "rate limited" in r.message]) == 1
    assert _get(a).attempts == 0 and _get(b).attempts == 0
    llm.exc = None
    assert await _run(uid, llm, clock=lambda: now[0] + 61) == 2
    assert _get(a).state == "ok" and _get(b).state == "ok"


@pytest.mark.db
async def test_no_llm_does_nothing(uid, caplog):
    mid = _pending(uid)
    with caplog.at_level(logging.WARNING, logger="worker.match_jobs"):
        assert await _run(uid, None) == 0
        assert await _run(uid, None) == 0
    assert len([r for r in caplog.records if "GROQ_API_KEY" in r.message]) == 1
    m = _get(mid)
    assert m.state == "pendente" and m.attempts == 0


@pytest.mark.db
async def test_stale_job_hash_discards_result(uid):
    mid = _pending(uid)
    jid = _get(mid).job_id

    def ingest_rewrites():
        with SessionLocal() as s:
            s.execute(update(Job).where(Job.id == jid).values(content_hash="new"))
            s.commit()

    await _run(uid, FakeLLM(hook=ingest_rewrites))
    m = _get(mid)
    assert m.state == "pendente" and m.ai_score is None and m.attempts == 0
    assert m.content_hash == "h0"


@pytest.mark.db
async def test_closed_job_discards_result(uid):
    mid = _pending(uid)
    jid = _get(mid).job_id

    def closes():
        with SessionLocal() as s:
            s.execute(update(Job).where(Job.id == jid).values(status="closed"))
            s.commit()

    await _run(uid, FakeLLM(hook=closes))
    assert _get(mid).state == "pendente" and _get(mid).ai_score is None


@pytest.mark.db
async def test_new_profile_version_discards_result(uid):
    mid = _pending(uid)

    def new_version():
        with SessionLocal() as s:
            s.add(Profile(user_id=uid, version=2, data=_profile_data(), content_hash="p2"))
            s.commit()

    await _run(uid, FakeLLM(hook=new_version))
    m = _get(mid)
    assert m.state == "pendente" and m.ai_score is None and m.profile_version == 1
    # rows of an old profile version are no longer picked
    llm = FakeLLM()
    assert await _run(uid, llm) == 0 and llm.calls == []


@pytest.mark.db
async def test_uses_profile_of_row_version(uid):
    with SessionLocal() as s:
        data = _profile_data()
        data["skills"].append({"name": "Kafka"})
        s.add(Profile(user_id=uid, version=2, data=data, content_hash="p2"))
        s.commit()
    _pending(uid, version=2)
    llm = FakeLLM()
    assert await _run(uid, llm) == 1
    assert "Kafka" in llm.calls[0][1]["content"]


@pytest.mark.db
async def test_batch_limit_respected(uid):
    ids = [_pending(uid, i, vec=0.1 * (i + 1)) for i in range(4)]
    llm = FakeLLM()
    assert await _run(uid, llm, batch=2) == 2
    states = [_get(m).state for m in ids]
    # highest vec_score first
    assert states == ["pendente", "pendente", "ok", "ok"]


@pytest.mark.db
async def test_two_workers_do_not_take_same_row(uid):
    a, b = _pending(uid, 0, vec=0.9), _pending(uid, 1, vec=0.8)
    # a row locked by another transaction (worker mid-selection) is skipped
    with SessionLocal() as other:
        other.execute(select(Match).where(Match.id == a).with_for_update())
        llm = FakeLLM()
        assert await _run(uid, llm, batch=5) == 1
        assert _get(b).state == "ok" and _get(a).state == "pendente"
        other.rollback()
    # two concurrent ticks never send the same row to the LLM
    c, d = _pending(uid, 2, vec=0.7), _pending(uid, 3, vec=0.6)
    llm1, llm2 = FakeLLM(), FakeLLM()
    done = await asyncio.gather(_run(uid, llm1, batch=1), _run(uid, llm2, batch=1))
    assert sorted(done) == [1, 1]
    assert len(llm1.calls) == 1 and len(llm2.calls) == 1
    # a duplicate would leave only one of the three rows ok
    states = {m: _get(m).state for m in (a, c, d)}
    assert list(states.values()).count("ok") == 2


@pytest.mark.db
async def test_other_users_rows_untouched_when_user_id_given(uid, make_user):
    other = make_user()
    mine = _pending(uid, 0)
    theirs = _pending(other, 0)
    assert await _run(uid, FakeLLM()) == 1
    assert _get(mine).state == "ok"
    t = _get(theirs)
    assert t.state == "pendente" and t.attempts == 0


@pytest.mark.db
async def test_no_transaction_open_during_llm_call(uid):
    mid = _pending(uid)
    locked = []

    def probe():
        with SessionLocal() as s:
            row = s.execute(
                select(Match.id).where(Match.id == mid).with_for_update(nowait=True)
            ).first()
            locked.append(row)

    await _run(uid, FakeLLM(hook=probe))
    assert locked == [(mid,)]


@pytest.mark.db
async def test_skips_while_interview_turn_pending(uid, make_user):
    mid = _pending(uid)
    other = make_user()  # an interview of ANY user blocks the tick
    with SessionLocal() as s:
        iv = InterviewSession(user_id=other, profile_version=1, state="generating")
        s.add(iv)
        s.commit()
        iv_id = iv.id
    llm = FakeLLM()
    for state in ("generating", "working"):
        with SessionLocal() as s:
            s.execute(update(InterviewSession).where(InterviewSession.id == iv_id)
                      .values(state=state))
            s.commit()
        assert await _run(uid, llm) == 0
    assert llm.calls == [] and _get(mid).state == "pendente"
    with SessionLocal() as s:  # waiting for the user's answer: no LLM needed
        s.execute(update(InterviewSession).where(InterviewSession.id == iv_id)
                  .values(state="awaiting_answer"))
        s.commit()
    assert await _run(uid, llm) == 1
    assert _get(mid).state == "ok"


@pytest.mark.db
async def test_call_timeout_pauses_without_attempt(uid, caplog):
    mid = _pending(uid)
    now = [100.0]
    llm = FakeLLM(delay=5.0)
    with caplog.at_level(logging.WARNING, logger="worker.match_jobs"):
        assert await _run(uid, llm, call_timeout=0.05, clock=lambda: now[0]) == 0
    m = _get(mid)
    assert m.state == "pendente" and m.attempts == 0
    assert any("timed out" in r.message for r in caplog.records)
    llm.delay = 0.0
    assert await _run(uid, llm, clock=lambda: now[0] + 30) == 0  # paused
    assert len(llm.calls) == 1
    assert await _run(uid, llm, clock=lambda: now[0] + 61) == 1
    assert _get(mid).state == "ok"


def _scored_last_24h() -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count(Match.id)).where(
            Match.scored_at > func.now() - timedelta(hours=24)))


@pytest.mark.db
async def test_daily_limit_caps_scoring_and_logs_hourly(uid, caplog):
    a, b = _pending(uid, 0, vec=0.9), _pending(uid, 1, vec=0.8)
    limit = _scored_last_24h() + 1
    now = [1000.0]
    llm = FakeLLM()
    with caplog.at_level(logging.INFO, logger="worker.match_jobs"):
        # one slot left: the batch is cut to it
        assert await _run(uid, llm, daily_limit=limit, clock=lambda: now[0]) == 1
        assert _get(a).state == "ok" and _get(b).state == "pendente"
        assert await _run(uid, llm, daily_limit=limit, clock=lambda: now[0] + 1) == 0
        assert await _run(uid, llm, daily_limit=limit, clock=lambda: now[0] + 60) == 0
        assert len([r for r in caplog.records if "daily limit" in r.message]) == 1
        assert await _run(uid, llm, daily_limit=limit, clock=lambda: now[0] + 3700) == 0
        assert len([r for r in caplog.records if "daily limit" in r.message]) == 2
    assert len(llm.calls) == 1 and _get(b).attempts == 0
    # rows scored over 24 h ago do not count
    with SessionLocal() as s:
        s.execute(update(Match).where(Match.id == a).values(
            scored_at=func.now() - timedelta(hours=25)))
        s.commit()
    assert await _run(uid, llm, daily_limit=limit, clock=lambda: now[0] + 7400) == 1
    assert _get(b).state == "ok"


def test_match_settings_defaults():
    from pydantic import ValidationError

    from rumo_core.config import Settings

    s = Settings(_env_file=None, database_url="x")
    assert s.match_daily_limit == 20 and s.match_call_timeout == 25
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="x", match_daily_limit=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="x", match_call_timeout=0)


def test_job_registered_after_fill():
    from rumo_core.config import Settings
    from worker.main import build_registry, build_score_matches_job

    s = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost/x",
                 match_batch=4, match_max_attempts=5, match_daily_limit=7,
                 match_call_timeout=12)
    sf, llm, emb = object(), object(), object()
    names = list(build_registry(s, sf, llm, emb)._jobs)
    assert names.index("score_matches") == names.index("fill_missing_job_fields") + 1
    job = build_score_matches_job(s, sf, llm)
    assert job.func is score_pending_matches and job.args == (sf, llm)
    assert job.keywords == {"batch": 4, "max_attempts": 5, "daily_limit": 7,
                            "call_timeout": 12}
    reg = build_registry(s, sf, llm, emb)
    assert reg._jobs["score_matches"][1] == 60
