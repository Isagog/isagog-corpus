"""Row → model parsing, against literal payloads from the production instance."""

import pytest
from corpus.errors import InvalidDocument
from corpus.models import Article, ArticleRef, AssetRef, Edition, EditionRef
from corpus_directus.rows import (
    article_from_row,
    article_ref_from_row,
    edition_from_row,
    edition_ref_from_row,
)
from corpus_directus.schema import MANIFESTO_SCHEMA, DirectusSchema

ARTICLE_UUID = "550e8400-e29b-41d4-a716-446655440000"

ARTICLE_ROW = {
    "id": ARTICLE_UUID,
    "slug": "test-article",
    "status": "published",
    "datePublished": "2024-01-15T10:00:00Z",
    "author": "Mario Rossi",
    "headline": "<h1>Test Headline</h1>",
    "articleKicker": "Breaking",
    "articleBody": "<p>" + "A" * 400 + "</p>",
    "articleSection": {"name": "Politica"},
}

EDITION_ROW = {
    "id": "660e8400-e29b-41d4-a716-446655440000",
    "editionDate": "2024-01-15",
    "status": "published",
    "slug": "edizione-2024-01-15",
    "title": "Edizione del 15 gennaio",
    "editionPdf": {"pdf": "770e8400-e29b-41d4-a716-446655440000"},
    "articles": [ARTICLE_ROW],
}


def _row(**overrides):
    return {**ARTICLE_ROW, **overrides}


@pytest.mark.unit
class TestArticleFromRow:
    def test_maps_every_consumed_field(self):
        article = article_from_row(ARTICLE_ROW, MANIFESTO_SCHEMA)
        assert isinstance(article, Article)
        assert article.id == ARTICLE_UUID
        assert article.slug == "test-article"
        assert article.publish_date == "2024-01-15"
        assert article.author == "Mario Rossi"
        assert article.headline == "Test Headline"
        assert article.kicker == "Breaking"
        assert article.body == "A" * 400
        assert article.section == "Politica"

    @pytest.mark.parametrize("absent", [None, ""])
    def test_null_kicker_and_author_fold_to_empty_strings(self, absent):
        article = article_from_row(_row(articleKicker=absent, author=absent), MANIFESTO_SCHEMA)
        assert article.kicker == ""
        assert article.author == ""

    def test_missing_optional_keys_are_tolerated(self):
        row = {k: v for k, v in ARTICLE_ROW.items() if k not in {"author", "articleKicker"}}
        article = article_from_row(row, MANIFESTO_SCHEMA)
        assert article.author == "" and article.kicker == ""

    def test_missing_section_is_none(self):
        assert article_from_row(_row(articleSection=None), MANIFESTO_SCHEMA).section is None

    @pytest.mark.parametrize("key", ["id", "slug", "datePublished", "headline", "articleBody"])
    def test_absent_required_key_is_a_missing_field(self, key):
        row = {k: v for k, v in ARTICLE_ROW.items() if k != key}
        with pytest.raises(InvalidDocument) as excinfo:
            article_from_row(row, MANIFESTO_SCHEMA)
        assert excinfo.value.kind == "missing_field"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"id": "not-a-uuid"},
            {"slug": "Not A Slug"},
            {"slug": None},
            {"datePublished": "15/01/2024"},
            {"headline": "<p></p>"},
            {"articleBody": None},
        ],
    )
    def test_present_but_unusable_is_a_bad_value(self, overrides):
        with pytest.raises(InvalidDocument) as excinfo:
            article_from_row(_row(**overrides), MANIFESTO_SCHEMA)
        assert excinfo.value.kind == "bad_value"

    def test_follows_a_renamed_field(self):
        schema = DirectusSchema(
            article_fields={**MANIFESTO_SCHEMA.article_fields, "body": "content"}
        )
        row = {**ARTICLE_ROW, "content": "B" * 400}
        assert article_from_row(row, schema).body == "B" * 400

    def test_id_format_is_not_enforced_when_the_schema_says_so(self):
        schema = DirectusSchema(id_is_uuid=False)
        assert article_from_row(_row(id="17"), schema).id == "17"


@pytest.mark.unit
class TestArticleRefFromRow:
    def test_projection_fields_only(self):
        ref = article_ref_from_row(ARTICLE_ROW, MANIFESTO_SCHEMA)
        assert isinstance(ref, ArticleRef)
        assert (ref.id, ref.slug, ref.status, ref.publish_date) == (
            ARTICLE_UUID,
            "test-article",
            "published",
            "2024-01-15",
        )
        assert ref.section == "Politica"

    def test_tolerates_a_thin_row(self):
        ref = article_ref_from_row({"id": ARTICLE_UUID}, MANIFESTO_SCHEMA)
        assert ref.slug is None and ref.status is None and ref.publish_date is None

    def test_still_needs_an_id(self):
        with pytest.raises(InvalidDocument) as excinfo:
            article_ref_from_row({"slug": "x"}, MANIFESTO_SCHEMA)
        assert excinfo.value.kind == "missing_field"


@pytest.mark.unit
class TestEditionFromRow:
    def test_maps_the_edition_and_its_pdf(self):
        edition = edition_from_row(EDITION_ROW, MANIFESTO_SCHEMA)
        assert isinstance(edition, Edition)
        assert edition.date == "2024-01-15"
        assert edition.slug == "edizione-2024-01-15"
        assert edition.title == "Edizione del 15 gennaio"
        assert isinstance(edition.pdf, AssetRef)
        assert edition.pdf.id == "770e8400-e29b-41d4-a716-446655440000"

    def test_keeps_published_articles_only(self):
        row = {
            **EDITION_ROW,
            "articles": [ARTICLE_ROW, {**ARTICLE_ROW, "id": "x", "status": "draft"}],
        }
        edition = edition_from_row(row, MANIFESTO_SCHEMA)
        assert [a.id for a in edition.articles] == [ARTICLE_UUID]

    def test_one_bad_article_does_not_lose_the_edition(self):
        """An edition survives one malformed article — skip and log."""
        row = {**EDITION_ROW, "articles": [ARTICLE_ROW, {"id": "broken", "status": "published"}]}
        edition = edition_from_row(row, MANIFESTO_SCHEMA)
        assert [a.id for a in edition.articles] == [ARTICLE_UUID]

    def test_no_pdf_is_none(self):
        assert edition_from_row({**EDITION_ROW, "editionPdf": None}, MANIFESTO_SCHEMA).pdf is None

    def test_missing_articles_key_is_an_empty_edition(self):
        row = {k: v for k, v in EDITION_ROW.items() if k != "articles"}
        assert edition_from_row(row, MANIFESTO_SCHEMA).articles == ()

    @pytest.mark.parametrize("key", ["id", "editionDate"])
    def test_absent_required_key_is_a_missing_field(self, key):
        row = {k: v for k, v in EDITION_ROW.items() if k != key}
        with pytest.raises(InvalidDocument) as excinfo:
            edition_from_row(row, MANIFESTO_SCHEMA)
        assert excinfo.value.kind == "missing_field"


@pytest.mark.unit
class TestEditionRefFromRow:
    def test_counts_articles_and_carries_the_pdf(self):
        row = {**EDITION_ROW, "articles": ["a", "b", "c"]}
        ref = edition_ref_from_row(row, MANIFESTO_SCHEMA)
        assert isinstance(ref, EditionRef)
        assert ref.article_count == 3
        assert ref.pdf is not None

    def test_malformed_pdf_reference_is_no_pdf(self):
        ref = edition_ref_from_row({**EDITION_ROW, "editionPdf": "not-an-object"}, MANIFESTO_SCHEMA)
        assert ref.pdf is None
