import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Integer, MetaData, Table, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from rumo_core.db import Base, SessionLocal, engine
from rumo_core.models import TimestampMixin, User, UserScopedMixin

pytestmark = pytest.mark.db


@pytest.fixture
def session():
    with SessionLocal() as s:
        yield s


def test_default_user_exists(session):
    assert session.get(User, 1) is not None


def test_pgvector_roundtrip(session):
    md = MetaData()
    t = Table(
        "tmp_vec_test", md,
        Column("id", Integer, primary_key=True),
        Column("emb", Vector(1024)),
    )
    md.create_all(engine)
    try:
        vec = [0.1] * 1024
        session.execute(t.insert().values(id=1, emb=vec))
        dist = session.execute(
            select(t.c.emb.l2_distance(vec)).where(t.c.id == 1)
        ).scalar_one()
        assert dist == pytest.approx(0, abs=1e-6)
    finally:
        session.rollback()
        md.drop_all(engine)


def test_user_scoped_mixin_requires_user_id(session):
    class Thing(UserScopedMixin, TimestampMixin, Base):
        __tablename__ = "tmp_thing_test"
        id: Mapped[int] = mapped_column(primary_key=True)

    Base.metadata.create_all(engine, tables=[Thing.__table__])
    try:
        session.add(Thing())
        with pytest.raises(IntegrityError):
            session.flush()
    finally:
        session.rollback()
        Base.metadata.drop_all(engine, tables=[Thing.__table__])
        Base.metadata.remove(Thing.__table__)
