"""Vendor-neutral query model (proposal §4.2)."""

from datetime import date

import pytest
from corpus.models import PUBLISHED
from corpus.query import ArticleOrder, ArticleQuery, EditionQuery
from pydantic import ValidationError


@pytest.mark.unit
class TestArticleQuery:
    def test_defaults_match_the_production_read_pattern(self):
        query = ArticleQuery()
        assert query.ids == ()
        assert query.slugs == ()
        assert query.sections == ()
        assert query.edition_id is None
        assert query.published_from is None and query.published_to is None
        assert query.status == PUBLISHED
        assert query.require_publish_date is True
        assert query.order is ArticleOrder.PUBLISH_DATE_DESC
        assert query.page_size == 100

    def test_is_frozen(self):
        query = ArticleQuery()
        with pytest.raises(ValidationError):
            query.page_size = 5

    def test_sequences_are_coerced_to_tuples(self):
        query = ArticleQuery(ids=["a", "b"], slugs=["s"], sections=["Politica"])  # type: ignore[arg-type]
        assert query.ids == ("a", "b")
        assert query.slugs == ("s",)
        assert query.sections == ("Politica",)

    def test_status_can_be_cleared(self):
        assert ArticleQuery(status=None).status is None

    @pytest.mark.parametrize("bad", [0, -1])
    def test_page_size_must_be_positive(self, bad):
        with pytest.raises(ValidationError):
            ArticleQuery(page_size=bad)

    def test_date_axes_accept_dates(self):
        query = ArticleQuery(published_from=date(2024, 1, 1), published_to=date(2024, 1, 31))
        assert query.published_from == date(2024, 1, 1)


@pytest.mark.unit
class TestEditionQuery:
    def test_defaults(self):
        query = EditionQuery()
        assert query.date_from is None
        assert query.date_to is None
        assert query.date_exact is None
        assert query.require_pdf is False

    def test_is_frozen(self):
        with pytest.raises(ValidationError):
            EditionQuery().require_pdf = True


@pytest.mark.unit
def test_article_order_values():
    assert ArticleOrder.PUBLISH_DATE_DESC.value == "publish_date_desc"
    assert ArticleOrder.PUBLISH_DATE_ASC.value == "publish_date_asc"
