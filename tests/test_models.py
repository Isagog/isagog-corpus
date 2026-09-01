"""Domain models — frozen, tuple-based, vendor-neutral (proposal §4.1)."""

import pytest
from corpus.models import (
    PUBLISHED,
    Article,
    ArticlePage,
    ArticleRef,
    AssetRef,
    Edition,
    EditionRef,
)
from pydantic import ValidationError


def _article(**overrides) -> Article:
    fields = {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "slug": "test-article",
        "publish_date": "2024-01-15",
        "author": "Mario Rossi",
        "headline": "Test Headline",
        "kicker": "Breaking",
        "body": "A" * 400,
    }
    return Article(**{**fields, **overrides})


@pytest.mark.unit
class TestArticle:
    def test_is_frozen(self):
        article = _article()
        with pytest.raises(ValidationError):
            article.headline = "mutated"

    def test_optional_fields_default_to_none(self):
        article = _article()
        assert article.section is None
        assert article.language is None

    def test_absent_kicker_and_author_are_empty_strings(self):
        article = _article(kicker="", author="")
        assert article.kicker == ""
        assert article.author == ""

    def test_carries_memaflow_field_names(self):
        """Temporal history compatibility: these names are a frozen contract."""
        assert set(Article.model_fields) == {
            "id",
            "slug",
            "publish_date",
            "author",
            "headline",
            "kicker",
            "body",
            "section",
            "language",
        }


@pytest.mark.unit
class TestRefs:
    def test_article_ref_needs_only_an_id(self):
        ref = ArticleRef(id="abc")
        assert ref.slug is None and ref.status is None and ref.publish_date is None

    def test_article_ref_is_frozen(self):
        with pytest.raises(ValidationError):
            ArticleRef(id="abc").id = "def"

    def test_asset_ref_is_frozen_and_optional_metadata(self):
        asset = AssetRef(id="file-1")
        assert asset.filename is None and asset.mime is None and asset.size is None
        with pytest.raises(ValidationError):
            asset.id = "other"

    def test_edition_ref_carries_counts_and_pdf(self):
        ref = EditionRef(id="e1", date="2024-01-15", article_count=3, pdf=AssetRef(id="f1"))
        assert ref.article_count == 3
        assert ref.pdf is not None and ref.pdf.id == "f1"


@pytest.mark.unit
class TestEdition:
    def test_articles_are_a_tuple(self):
        edition = Edition(id="e1", date="2024-01-15", articles=[_article()])  # type: ignore[arg-type]
        assert isinstance(edition.articles, tuple)

    def test_defaults_to_no_articles_and_no_pdf(self):
        edition = Edition(id="e1", date="2024-01-15")
        assert edition.articles == ()
        assert edition.pdf is None


@pytest.mark.unit
class TestArticlePage:
    def test_items_are_a_tuple_and_cursor_optional(self):
        page = ArticlePage(items=[ArticleRef(id="a")])  # type: ignore[arg-type]
        assert isinstance(page.items, tuple)
        assert page.next_cursor is None


@pytest.mark.unit
def test_published_status_constant():
    assert PUBLISHED == "published"
