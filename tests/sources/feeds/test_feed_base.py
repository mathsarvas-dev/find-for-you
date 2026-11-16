import pytest

from rumo_core.sources.feeds import (
    Attribution,
    feed_attribution,
    feed_names,
    feed_needs_key,
    get_feed,
)
from rumo_core.sources.feeds.base import FeedParams


def test_feed_params_validation():
    with pytest.raises(ValueError):
        FeedParams.from_dict({"keywords": ["um", "dois", "tres", "quatro", "cinco", "seis"]})
    assert FeedParams.from_dict({"keywords": ["  dados "]}).keywords == ("dados",)
    with pytest.raises(ValueError):
        FeedParams.from_dict({"x": 1})
    assert FeedParams.from_dict({"country": "br"}).country == "BR"


def test_feed_params_dedupes_case_insensitively():
    params = FeedParams.from_dict({"keywords": ["Python", "python", " SQL "]})
    assert params.keywords == ("Python", "SQL")


def test_feed_params_rejects_bad_keyword_length():
    with pytest.raises(ValueError):
        FeedParams.from_dict({"keywords": [""]})
    with pytest.raises(ValueError):
        FeedParams.from_dict({"keywords": ["x" * 61]})


def test_feed_params_stable_dict():
    a = FeedParams.from_dict({"keywords": ["dados"], "country": "BR"})
    b = FeedParams.from_dict({"country": "br", "keywords": ["dados"]})
    assert a.to_dict() == b.to_dict() == {"keywords": ["dados"], "country": "BR"}
    assert list(a.to_dict()) == ["keywords", "country"]


def test_registry_lists_and_builds_feeds():
    names = feed_names()
    for name in ("remotive", "remoteok", "arbeitnow", "himalayas"):
        assert name in names
        assert get_feed(name).name == name
        assert get_feed(name) is get_feed(name)  # cached
    assert feed_attribution("remotive") == Attribution("Vagas via Remotive", "https://remotive.com")
    assert feed_needs_key("remotive") is False
    with pytest.raises(KeyError):
        get_feed("nao-existe")
