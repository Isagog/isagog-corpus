"""Consumer-declared data constraints (proposal §4.4)."""

import pytest
from corpus.errors import InvalidDocument
from corpus.models import Article
from corpus.policy import ArticlePolicy

MEMAFLOW_POLICY = ArticlePolicy(min_body_chars=300, max_body_chars=30_000)


def _article(**overrides) -> Article:
    fields = {
        "id": "a1",
        "slug": "s",
        "publish_date": "2024-01-15",
        "author": "",
        "headline": "H",
        "kicker": "",
        "body": "A" * 400,
    }
    return Article(**{**fields, **overrides})


@pytest.mark.unit
class TestArticlePolicy:
    def test_default_policy_only_requires_core_fields(self):
        policy = ArticlePolicy()
        assert policy.min_body_chars is None
        assert policy.max_body_chars is None
        policy.check(_article(body="tiny"))

    def test_missing_required_field_raises(self):
        with pytest.raises(InvalidDocument) as excinfo:
            ArticlePolicy().check(_article(headline=""))
        assert excinfo.value.kind == "missing_field"
        assert "headline" in str(excinfo.value)

    def test_body_too_short(self):
        with pytest.raises(InvalidDocument) as excinfo:
            MEMAFLOW_POLICY.check(_article(body="A" * 299))
        assert excinfo.value.kind == "bad_value"
        assert "299" in str(excinfo.value)

    def test_body_too_long(self):
        with pytest.raises(InvalidDocument) as excinfo:
            MEMAFLOW_POLICY.check(_article(body="A" * 30_001))
        assert excinfo.value.kind == "bad_value"

    @pytest.mark.parametrize("length", [300, 1000, 30_000])
    def test_body_within_bounds_passes(self, length):
        MEMAFLOW_POLICY.check(_article(body="A" * length))

    def test_policy_is_declarative_data(self):
        assert ArticlePolicy(min_body_chars=10) == ArticlePolicy(min_body_chars=10)

    def test_custom_required_fields(self):
        policy = ArticlePolicy(required_fields=frozenset({"author"}))
        policy.check(_article(author="Someone"))
        with pytest.raises(InvalidDocument):
            policy.check(_article(author=""))
